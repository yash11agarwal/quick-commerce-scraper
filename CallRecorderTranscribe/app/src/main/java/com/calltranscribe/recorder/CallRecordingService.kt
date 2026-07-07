package com.calltranscribe.recorder

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.io.File
import java.util.Date

/**
 * Foreground service that records the active call and, when configured, transcribes
 * the finished recording.
 *
 * Audio source strategy: try [MediaRecorder.AudioSource.VOICE_CALL] first (true
 * both-sides capture, available only on rooted / OEM / system builds), then
 * VOICE_COMMUNICATION, then plain MIC. On a normal phone the call falls back to MIC,
 * so the far end is only captured when the call is on speakerphone.
 */
class CallRecordingService : Service() {

    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null
    private var recording = false

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Every startForegroundService() call must be matched by a startForeground()
        // within a few seconds, including a STOP that lands on a fresh service instance.
        if (intent?.action == ACTION_START || intent?.action == ACTION_STOP) {
            startForegroundCompat(buildRecordingNotification())
        }

        when (intent?.action) {
            ACTION_START -> {
                val number = intent.getStringExtra(EXTRA_NUMBER) ?: "unknown"
                val direction = intent.getStringExtra(EXTRA_DIRECTION) ?: "call"
                startRecording(number, direction)
            }

            ACTION_STOP -> stopRecordingAndMaybeTranscribe()
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun startRecording(number: String, direction: String) {
        if (recording) return

        val startedAt = Date()
        val file = RecordingStore.newAudioFile(
            this,
            Recording.buildFileName(startedAt, direction, number)
        )
        outputFile = file

        val source = tryStartRecorder(file)
        if (source == null) {
            Log.e(TAG, "Could not start recorder with any audio source")
            outputFile = null
            stopForegroundCompat()
            stopSelf()
            return
        }
        Log.i(TAG, "Recording started with source=$source -> ${file.name}")
        recording = true
    }

    /** Attempts each audio source in order; returns the one that worked, or null. */
    private fun tryStartRecorder(file: File): Int? {
        val sources = listOf(
            MediaRecorder.AudioSource.VOICE_CALL,
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            MediaRecorder.AudioSource.MIC
        )
        for (source in sources) {
            val rec = newRecorder()
            try {
                rec.setAudioSource(source)
                rec.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                rec.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                rec.setAudioEncodingBitRate(96_000)
                rec.setAudioSamplingRate(44_100)
                rec.setOutputFile(file.absolutePath)
                rec.prepare()
                rec.start()
                recorder = rec
                return source
            } catch (e: Exception) {
                Log.w(TAG, "Audio source $source failed: ${e.message}")
                try {
                    rec.reset()
                    rec.release()
                } catch (_: Exception) {
                }
                // Remove a possibly zero-byte file before the next attempt.
                if (file.exists() && file.length() == 0L) file.delete()
            }
        }
        return null
    }

    @Suppress("DEPRECATION")
    private fun newRecorder(): MediaRecorder =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this)
        else MediaRecorder()

    private fun stopRecordingAndMaybeTranscribe() {
        val file = outputFile
        if (recording) {
            try {
                recorder?.stop()
            } catch (e: Exception) {
                Log.w(TAG, "recorder.stop failed: ${e.message}")
            }
            try {
                recorder?.release()
            } catch (_: Exception) {
            }
            recorder = null
            recording = false
        }

        if (file == null || !file.exists() || file.length() == 0L) {
            file?.delete()
            stopForegroundCompat()
            stopSelf()
            return
        }

        val prefs = Prefs(this)
        if (prefs.autoTranscribe && prefs.hasApiKey()) {
            updateNotification(buildTranscribingNotification())
            transcribe(file, prefs)
        } else {
            stopForegroundCompat()
            stopSelf()
        }
    }

    private fun transcribe(file: File, prefs: Prefs) {
        scope.launch {
            val result = TranscriptionClient.transcribe(prefs, file)
            val recording = Recording.fromFile(file)
            result.onSuccess { text ->
                recording?.writeTranscript(text)
                Notifier.showTranscriptReady(this@CallRecordingService, file.name)
            }.onFailure { err ->
                Log.e(TAG, "Transcription failed", err)
                Notifier.showTranscriptFailed(this@CallRecordingService, err.message ?: "error")
            }
            stopForegroundCompat()
            stopSelf()
        }
    }

    private fun buildRecordingNotification(): Notification =
        baseNotification(getString(R.string.notif_recording_title))

    private fun buildTranscribingNotification(): Notification =
        baseNotification(getString(R.string.notif_transcribing_title))

    private fun baseNotification(title: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, RecorderApp.CHANNEL_RECORDING)
            .setContentTitle(title)
            .setContentText(getString(R.string.notif_recording_text))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(open)
            .setOngoing(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIF_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun updateNotification(notification: Notification) {
        Notifier.manager(this).notify(NOTIF_ID, notification)
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    override fun onDestroy() {
        try {
            recorder?.release()
        } catch (_: Exception) {
        }
        recorder = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "CallRecordingService"
        private const val NOTIF_ID = 1001

        const val ACTION_START = "com.calltranscribe.recorder.action.START"
        const val ACTION_STOP = "com.calltranscribe.recorder.action.STOP"
        const val EXTRA_NUMBER = "number"
        const val EXTRA_DIRECTION = "direction"
    }
}

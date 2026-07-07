package com.calltranscribe.recorder

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

object Notifier {

    fun manager(context: Context): NotificationManager =
        context.getSystemService(NotificationManager::class.java)

    fun showTranscriptReady(context: Context, fileName: String) {
        val open = PendingIntent.getActivity(
            context, 1,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n = NotificationCompat.Builder(context, RecorderApp.CHANNEL_TRANSCRIPTION)
            .setContentTitle(context.getString(R.string.notif_transcribed_title))
            .setContentText(fileName)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        manager(context).notify(fileName.hashCode(), n)
    }

    fun showTranscriptFailed(context: Context, message: String) {
        val n = NotificationCompat.Builder(context, RecorderApp.CHANNEL_TRANSCRIPTION)
            .setContentTitle("Transcription failed")
            .setContentText(message)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setAutoCancel(true)
            .build()
        manager(context).notify(("fail" + message).hashCode(), n)
    }
}

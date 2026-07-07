package com.calltranscribe.recorder

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat

/**
 * Listens to telephony broadcasts and turns them into start/stop commands for
 * [CallRecordingService]. Because PHONE_STATE fires several times per call we
 * track the previous state to detect real transitions and to tell incoming from
 * outgoing calls.
 */
class CallStateReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_NEW_OUTGOING_CALL -> {
                // Fired just before an outgoing call is placed; carries the dialled number.
                intent.getStringExtra(Intent.EXTRA_PHONE_NUMBER)?.let {
                    lastNumber = it
                    lastDirection = DIRECTION_OUTGOING
                }
            }

            TelephonyManager.ACTION_PHONE_STATE_CHANGED -> handlePhoneState(context, intent)
        }
    }

    private fun handlePhoneState(context: Context, intent: Intent) {
        val stateStr = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
        val incomingNumber = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)

        val state = when (stateStr) {
            TelephonyManager.EXTRA_STATE_RINGING -> STATE_RINGING
            TelephonyManager.EXTRA_STATE_OFFHOOK -> STATE_OFFHOOK
            TelephonyManager.EXTRA_STATE_IDLE -> STATE_IDLE
            else -> return
        }

        if (state == lastState) return

        when (state) {
            STATE_RINGING -> {
                lastDirection = DIRECTION_INCOMING
                if (!incomingNumber.isNullOrBlank()) lastNumber = incomingNumber
            }

            STATE_OFFHOOK -> {
                // Call is now connected (either an answered incoming or a placed outgoing).
                if (lastState != STATE_RINGING && lastDirection != DIRECTION_INCOMING) {
                    lastDirection = DIRECTION_OUTGOING
                }
                if (!incomingNumber.isNullOrBlank()) lastNumber = incomingNumber
                startRecording(context)
            }

            STATE_IDLE -> {
                // Call finished (or a ring was never answered).
                stopRecording(context)
                lastNumber = "unknown"
                lastDirection = DIRECTION_INCOMING
            }
        }

        lastState = state
    }

    private fun startRecording(context: Context) {
        if (!Prefs(context).autoRecord) return
        val intent = Intent(context, CallRecordingService::class.java).apply {
            action = CallRecordingService.ACTION_START
            putExtra(CallRecordingService.EXTRA_NUMBER, lastNumber)
            putExtra(CallRecordingService.EXTRA_DIRECTION, lastDirection)
        }
        ContextCompat.startForegroundService(context, intent)
    }

    private fun stopRecording(context: Context) {
        val intent = Intent(context, CallRecordingService::class.java).apply {
            action = CallRecordingService.ACTION_STOP
        }
        // Service may already be gone; startForegroundService is safe either way and the
        // service stops itself once it has handled STOP.
        try {
            ContextCompat.startForegroundService(context, intent)
        } catch (_: Exception) {
        }
    }

    companion object {
        private const val STATE_IDLE = 0
        private const val STATE_RINGING = 1
        private const val STATE_OFFHOOK = 2

        const val DIRECTION_INCOMING = "incoming"
        const val DIRECTION_OUTGOING = "outgoing"

        @Volatile private var lastState = STATE_IDLE
        @Volatile private var lastNumber = "unknown"
        @Volatile private var lastDirection = DIRECTION_INCOMING
    }
}

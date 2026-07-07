package com.calltranscribe.recorder

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * The manifest-registered [CallStateReceiver] is active as soon as the app is
 * installed, so no work is strictly required at boot. This receiver exists to keep
 * the process warmed after a reboot and as a hook for future scheduling.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // No-op: presence of the receiver is enough to be woken; state is read lazily.
    }
}

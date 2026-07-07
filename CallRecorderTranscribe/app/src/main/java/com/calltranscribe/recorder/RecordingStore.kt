package com.calltranscribe.recorder

import android.content.Context
import java.io.File

/** Locates recordings on disk. Recordings live in the app-specific external files dir. */
object RecordingStore {

    fun dir(context: Context): File {
        val dir = File(context.getExternalFilesDir(null), "recordings")
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    fun newAudioFile(context: Context, fileName: String): File =
        File(dir(context), fileName)

    fun list(context: Context): List<Recording> =
        dir(context).listFiles { f -> f.extension.equals("m4a", ignoreCase = true) }
            ?.mapNotNull { Recording.fromFile(it) }
            ?.sortedByDescending { it.startedAt.time }
            ?: emptyList()

    fun delete(recording: Recording) {
        recording.audioFile.delete()
        if (recording.transcriptFile.exists()) recording.transcriptFile.delete()
    }
}

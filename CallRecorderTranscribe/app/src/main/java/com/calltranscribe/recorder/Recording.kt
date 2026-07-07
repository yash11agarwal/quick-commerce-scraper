package com.calltranscribe.recorder

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * One recorded call, backed by an audio file on disk plus an optional sidecar
 * transcript file (same base name, ".txt" extension).
 *
 * File name format: call_<yyyyMMdd_HHmmss>_<direction>_<number>.m4a
 * e.g. call_20260707_142530_incoming_15551234567.m4a
 */
data class Recording(
    val audioFile: File,
    val startedAt: Date,
    val direction: String,
    val number: String
) {
    val transcriptFile: File
        get() = File(audioFile.parentFile, audioFile.nameWithoutExtension + ".txt")

    val hasTranscript: Boolean
        get() = transcriptFile.exists() && transcriptFile.length() > 0

    fun readTranscript(): String =
        if (transcriptFile.exists()) transcriptFile.readText() else ""

    fun writeTranscript(text: String) {
        transcriptFile.writeText(text)
    }

    val displayNumber: String
        get() = if (number.isBlank() || number == "unknown") "Unknown number" else number

    val displayTime: String
        get() = DISPLAY_FORMAT.format(startedAt)

    companion object {
        private val FILE_FORMAT = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
        private val DISPLAY_FORMAT = SimpleDateFormat("MMM d, yyyy · h:mm a", Locale.getDefault())

        fun buildFileName(startedAt: Date, direction: String, number: String): String {
            val safeNumber = number.replace(Regex("[^0-9+]"), "").ifBlank { "unknown" }
            return "call_${FILE_FORMAT.format(startedAt)}_${direction}_${safeNumber}.m4a"
        }

        /** Parse a Recording from an audio file, or null if the name is not ours. */
        fun fromFile(file: File): Recording? {
            val name = file.nameWithoutExtension
            if (!name.startsWith("call_")) return null
            val parts = name.removePrefix("call_").split("_")
            // parts: [yyyyMMdd, HHmmss, direction, number]
            if (parts.size < 3) return null
            val date = try {
                FILE_FORMAT.parse("${parts[0]}_${parts[1]}")
            } catch (e: Exception) {
                Date(file.lastModified())
            } ?: Date(file.lastModified())
            val direction = parts.getOrElse(2) { "call" }
            val number = parts.drop(3).joinToString("_").ifBlank { "unknown" }
            return Recording(file, date, direction, number)
        }
    }
}

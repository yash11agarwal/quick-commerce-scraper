package com.calltranscribe.recorder

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Uploads an audio file to a Whisper-compatible transcription endpoint and returns
 * the transcript text.
 *
 * Works with the OpenAI audio transcriptions API and any server that mirrors it
 * (self-hosted whisper.cpp servers, Groq's Whisper endpoint, etc.). Configure the
 * endpoint, model and key in Settings.
 */
object TranscriptionClient {

    private val client = OkHttpClient.Builder()
        .callTimeout(5, TimeUnit.MINUTES)
        .readTimeout(5, TimeUnit.MINUTES)
        .writeTimeout(5, TimeUnit.MINUTES)
        .build()

    fun transcribe(prefs: Prefs, audio: File): Result<String> {
        if (!prefs.hasApiKey()) {
            return Result.failure(IllegalStateException("No API key set (Settings → API key)."))
        }
        if (!audio.exists() || audio.length() == 0L) {
            return Result.failure(IllegalStateException("Audio file missing or empty."))
        }

        return try {
            val bodyBuilder = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "file",
                    audio.name,
                    audio.asRequestBody("audio/m4a".toMediaType())
                )
                .addFormDataPart("model", prefs.model)
                .addFormDataPart("response_format", "text")

            if (prefs.language.isNotBlank()) {
                bodyBuilder.addFormDataPart("language", prefs.language)
            }

            val request = Request.Builder()
                .url(prefs.apiBaseUrl)
                .addHeader("Authorization", "Bearer ${prefs.apiKey}")
                .post(bodyBuilder.build())
                .build()

            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (response.isSuccessful) {
                    Result.success(text.trim())
                } else {
                    Result.failure(
                        RuntimeException("HTTP ${response.code}: ${text.take(300)}")
                    )
                }
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}

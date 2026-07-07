package com.calltranscribe.recorder

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * App settings. The API key is held in an EncryptedSharedPreferences file so it is
 * stored encrypted at rest; the remaining flags live in a normal preferences file.
 */
class Prefs(context: Context) {

    private val plain: SharedPreferences =
        context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    private val secure: SharedPreferences = run {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "secure_settings",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    var autoRecord: Boolean
        get() = plain.getBoolean(KEY_AUTO_RECORD, true)
        set(v) = plain.edit().putBoolean(KEY_AUTO_RECORD, v).apply()

    var autoTranscribe: Boolean
        get() = plain.getBoolean(KEY_AUTO_TRANSCRIBE, true)
        set(v) = plain.edit().putBoolean(KEY_AUTO_TRANSCRIBE, v).apply()

    var apiBaseUrl: String
        get() = plain.getString(KEY_BASE_URL, DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(v) = plain.edit().putString(KEY_BASE_URL, v.trim()).apply()

    var model: String
        get() = plain.getString(KEY_MODEL, DEFAULT_MODEL) ?: DEFAULT_MODEL
        set(v) = plain.edit().putString(KEY_MODEL, v.trim()).apply()

    /** Optional ISO language hint (e.g. "en"). Empty means auto-detect. */
    var language: String
        get() = plain.getString(KEY_LANGUAGE, "") ?: ""
        set(v) = plain.edit().putString(KEY_LANGUAGE, v.trim()).apply()

    var apiKey: String
        get() = secure.getString(KEY_API_KEY, "") ?: ""
        set(v) = secure.edit().putString(KEY_API_KEY, v.trim()).apply()

    fun hasApiKey(): Boolean = apiKey.isNotBlank()

    companion object {
        const val DEFAULT_BASE_URL = "https://api.openai.com/v1/audio/transcriptions"
        const val DEFAULT_MODEL = "whisper-1"

        private const val KEY_AUTO_RECORD = "auto_record"
        private const val KEY_AUTO_TRANSCRIBE = "auto_transcribe"
        private const val KEY_BASE_URL = "api_base_url"
        private const val KEY_MODEL = "api_model"
        private const val KEY_LANGUAGE = "api_language"
        private const val KEY_API_KEY = "api_key"
    }
}

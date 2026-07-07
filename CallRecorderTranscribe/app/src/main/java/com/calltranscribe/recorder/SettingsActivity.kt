package com.calltranscribe.recorder

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.calltranscribe.recorder.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var prefs: Prefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        supportActionBar?.title = getString(R.string.settings)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        prefs = Prefs(this)

        binding.switchAutoRecord.isChecked = prefs.autoRecord
        binding.switchAutoTranscribe.isChecked = prefs.autoTranscribe
        binding.editApiKey.setText(prefs.apiKey)
        binding.editBaseUrl.setText(prefs.apiBaseUrl)
        binding.editModel.setText(prefs.model)
        binding.editLanguage.setText(prefs.language)

        binding.btnSave.setOnClickListener { save() }
    }

    private fun save() {
        prefs.autoRecord = binding.switchAutoRecord.isChecked
        prefs.autoTranscribe = binding.switchAutoTranscribe.isChecked
        prefs.apiKey = binding.editApiKey.text?.toString().orEmpty()
        prefs.apiBaseUrl = binding.editBaseUrl.text?.toString()
            ?.ifBlank { Prefs.DEFAULT_BASE_URL } ?: Prefs.DEFAULT_BASE_URL
        prefs.model = binding.editModel.text?.toString()
            ?.ifBlank { Prefs.DEFAULT_MODEL } ?: Prefs.DEFAULT_MODEL
        prefs.language = binding.editLanguage.text?.toString().orEmpty()
        Toast.makeText(this, R.string.saved, Toast.LENGTH_SHORT).show()
        finish()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}

package com.calltranscribe.recorder

import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.calltranscribe.recorder.databinding.ActivityRecordingDetailBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class RecordingDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRecordingDetailBinding
    private lateinit var recording: Recording
    private var player: MediaPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityRecordingDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        val path = intent.getStringExtra(EXTRA_PATH)
        val file = path?.let { File(it) }
        val parsed = file?.let { Recording.fromFile(it) }
        if (parsed == null || !file.exists()) {
            Toast.makeText(this, "Recording not found", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        recording = parsed

        binding.number.text = recording.displayNumber
        val direction = recording.direction.replaceFirstChar { it.uppercase() }
        binding.subtitle.text = "$direction · ${recording.displayTime}"
        showTranscript()

        binding.btnPlay.setOnClickListener { togglePlayback() }
        binding.btnTranscribe.setOnClickListener { transcribeNow() }
        binding.btnShare.setOnClickListener { share() }
        binding.btnDelete.setOnClickListener { confirmDelete() }
    }

    private fun showTranscript() {
        binding.transcript.text =
            if (recording.hasTranscript) recording.readTranscript()
            else getString(R.string.no_transcript)
    }

    private fun togglePlayback() {
        val p = player
        if (p != null && p.isPlaying) {
            p.stop()
            p.release()
            player = null
            binding.btnPlay.text = getString(R.string.play)
            return
        }
        try {
            player = MediaPlayer().apply {
                setDataSource(recording.audioFile.absolutePath)
                setOnCompletionListener {
                    it.release()
                    player = null
                    binding.btnPlay.text = getString(R.string.play)
                }
                prepare()
                start()
            }
            binding.btnPlay.text = getString(R.string.stop)
        } catch (e: Exception) {
            Toast.makeText(this, "Cannot play: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun transcribeNow() {
        val prefs = Prefs(this)
        if (!prefs.hasApiKey()) {
            Toast.makeText(this, "Set your API key in Settings first.", Toast.LENGTH_LONG).show()
            return
        }
        binding.btnTranscribe.isEnabled = false
        binding.transcript.text = getString(R.string.transcribing)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                TranscriptionClient.transcribe(prefs, recording.audioFile)
            }
            result.onSuccess { text ->
                recording.writeTranscript(text)
                showTranscript()
            }.onFailure { err ->
                binding.transcript.text = "Transcription failed:\n${err.message}"
            }
            binding.btnTranscribe.isEnabled = true
        }
    }

    private fun share() {
        val uri: Uri = FileProvider.getUriForFile(
            this, "$packageName.fileprovider", recording.audioFile
        )
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "audio/mp4"
            putExtra(Intent.EXTRA_STREAM, uri)
            if (recording.hasTranscript) {
                putExtra(Intent.EXTRA_TEXT, recording.readTranscript())
            }
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(intent, getString(R.string.share)))
    }

    private fun confirmDelete() {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete)
            .setMessage("Delete this recording and its transcript?")
            .setPositiveButton(R.string.delete) { _, _ ->
                RecordingStore.delete(recording)
                finish()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onStop() {
        super.onStop()
        player?.release()
        player = null
    }

    companion object {
        const val EXTRA_PATH = "path"
    }
}

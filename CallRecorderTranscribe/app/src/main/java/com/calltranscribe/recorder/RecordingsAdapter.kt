package com.calltranscribe.recorder

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.calltranscribe.recorder.databinding.ItemRecordingBinding

class RecordingsAdapter(
    private val onClick: (Recording) -> Unit
) : RecyclerView.Adapter<RecordingsAdapter.VH>() {

    private val items = mutableListOf<Recording>()

    fun submit(list: List<Recording>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

    inner class VH(val binding: ItemRecordingBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemRecordingBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val r = items[position]
        holder.binding.number.text = r.displayNumber
        val direction = r.direction.replaceFirstChar { it.uppercase() }
        holder.binding.subtitle.text = "$direction · ${r.displayTime}"
        if (r.hasTranscript) {
            holder.binding.transcriptStatus.text = "📄 Transcript ready"
            holder.binding.transcriptStatus.setTextColor(0xFF2E7D32.toInt())
        } else {
            holder.binding.transcriptStatus.text = "No transcript"
            holder.binding.transcriptStatus.setTextColor(0xFF9E9E9E.toInt())
        }
        holder.binding.root.setOnClickListener { onClick(r) }
    }

    override fun getItemCount(): Int = items.size
}

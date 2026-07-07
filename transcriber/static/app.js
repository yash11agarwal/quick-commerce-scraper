"use strict";

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const statusBox = document.getElementById("status");
const statusFile = document.getElementById("status-file");
const statusLabel = document.getElementById("status-label");
const statusDetail = document.getElementById("status-detail");
const barFill = document.getElementById("bar-fill");
const downloads = document.getElementById("downloads");
const dlTxt = document.getElementById("dl-txt");
const dlSrt = document.getElementById("dl-srt");
const errorBox = document.getElementById("error");

let pollTimer = null;

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) upload(fileInput.files[0]);
});

["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});

function resetUI(filename) {
  clearInterval(pollTimer);
  statusBox.classList.remove("hidden");
  downloads.classList.add("hidden");
  errorBox.classList.add("hidden");
  statusLabel.className = "badge";
  statusFile.textContent = filename;
  barFill.style.width = "0%";
}

async function upload(file) {
  resetUI(file.name);
  statusLabel.textContent = "uploading";
  statusDetail.textContent = "Sending file to the server…";

  const form = new FormData();
  form.append("file", file);
  form.append("model", document.getElementById("model").value);
  form.append("language", document.getElementById("language").value);
  form.append("timestamps_in_txt", document.getElementById("ts-txt").checked);

  let job;
  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `upload failed (HTTP ${res.status})`);
    }
    job = await res.json();
  } catch (err) {
    return fail(err.message);
  }

  poll(job.id);
}

function poll(jobId) {
  pollTimer = setInterval(async () => {
    let job;
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error(`status check failed (HTTP ${res.status})`);
      job = await res.json();
    } catch (err) {
      clearInterval(pollTimer);
      return fail(err.message);
    }

    statusLabel.textContent = job.status;
    barFill.style.width = `${Math.round(job.progress * 100)}%`;

    if (job.status === "queued" || job.status === "processing") {
      const pct = Math.round(job.progress * 100);
      statusDetail.textContent =
        job.stage + (job.stage === "transcribing" ? ` — ${pct}%` : "…");
    } else if (job.status === "completed") {
      clearInterval(pollTimer);
      statusLabel.classList.add("done");
      barFill.style.width = "100%";
      const mins = job.duration ? (job.duration / 60).toFixed(1) : "?";
      statusDetail.textContent =
        `Done — ${mins} min of audio, language: ${job.language || "unknown"}.`;
      dlTxt.href = `/api/jobs/${job.id}/download/txt`;
      dlSrt.href = `/api/jobs/${job.id}/download/srt`;
      downloads.classList.remove("hidden");
    } else if (job.status === "failed") {
      clearInterval(pollTimer);
      fail(job.error || "transcription failed");
    }
  }, 1500);
}

function fail(message) {
  statusLabel.textContent = "failed";
  statusLabel.classList.add("failed");
  statusDetail.textContent = "";
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

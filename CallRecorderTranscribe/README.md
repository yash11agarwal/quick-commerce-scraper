# Call Recorder + Transcribe (Android)

An Android app that automatically records phone calls and transcribes them, so
that after a call (e.g. a meeting you took on the phone) you always have both the
**audio recording** and a **text transcript** to come back to.

- Auto-starts recording when a call connects and stops when it ends.
- Saves each recording as an `.m4a` file with call metadata (number, direction, time).
- Transcribes each recording via a Whisper-compatible cloud API and saves the text
  next to the audio.
- Simple UI: list of calls → tap to play the audio, read/share the transcript, or
  re-transcribe.

> This project ships as a **complete, buildable Android Studio project**. It was not
> compiled into an APK in the environment where it was generated, because that
> environment's network policy blocks Google's SDK/artifact hosts (`dl.google.com`).
> On your own machine Android Studio downloads the SDK normally — see **Build** below.

---

## ⚠️ Read this first: the "record the other person" limitation

You asked for both **your microphone** and the **receiver** (the other person) to be
recorded. There is an Android platform reality you need to know:

- **Since Android 10 (2019), Google blocks third-party apps from tapping the raw
  call audio stream.** The privileged `VOICE_CALL` audio source — the only source
  that captures both sides directly — is restricted to the phone's built-in dialer
  and to system/carrier apps.
- This app **tries `VOICE_CALL` first** and automatically falls back to
  `VOICE_COMMUNICATION` and then the plain microphone (`MIC`) if it's not permitted.

What that means in practice:

| Your device | What gets recorded |
|-------------|--------------------|
| **Rooted phone**, or an OEM/system build that grants `VOICE_CALL` (some Xiaomi/older Samsung ROMs), or Android ≤ 9 | **Both sides** directly, even without speakerphone. |
| **Normal, unrooted Android 10+** | **Your mic only** — unless you put the call on **speakerphone**, in which case the mic also picks up the other person. |

Because you chose the **rooted / OEM `VOICE_CALL`** path, the app is built to use it
when available. On a rooted device, grant the app the `CAPTURE_AUDIO_OUTPUT`/
`VOICE_CALL` capability (e.g. via a Magisk/privileged-permissions setup) and it will
capture both sides. If `VOICE_CALL` is denied it will not crash — it silently falls
back to the mic, and the in-app banner reminds you to use speakerphone.

### Legal note
Recording calls is regulated and in many places requires the consent of the other
party (one-party vs. all-party consent varies by country/state). You are
responsible for complying with the laws that apply to you — tell people when a call
is being recorded where required.

---

## Build

**Requirements:** Android Studio (Koala/Ladybug or newer) with JDK 17+, or a
command-line Android SDK. Min Android 7.0 (API 24), targets Android 14 (API 34).

### Option A — Android Studio (easiest)
1. `File → Open` and select this `CallRecorderTranscribe` folder.
2. Let it sync (it downloads the Android Gradle Plugin, SDK platform 34 and
   build-tools automatically).
3. `Build → Build App Bundle(s) / APK(s) → Build APK(s)`.
4. The debug APK lands in `app/build/outputs/apk/debug/app-debug.apk`.
5. Copy it to your phone and install (enable "Install unknown apps" for your file
   manager), or use `Run` with the phone connected.

### Option B — Command line
```bash
# Point the build at your SDK (Android Studio usually writes this for you):
cp local.properties.sample local.properties
#   then edit local.properties -> sdk.dir=/path/to/Android/Sdk

./gradlew assembleDebug        # -> app/build/outputs/apk/debug/app-debug.apk
# release (unsigned):
./gradlew assembleRelease
```
The Gradle wrapper (`gradlew`) is included and pinned to Gradle 8.14.3.

---

## Setup on the phone

1. Install and open the app. Grant the permissions it asks for:
   **Microphone, Phone, Call log, Notifications.**
2. Open **Settings** (top-right menu) and:
   - Turn on **Automatically record calls** (on by default).
   - Turn on **Automatically transcribe after each call** (on by default).
   - Paste your **API key** and confirm the endpoint/model (defaults below).
3. Make or receive a call. Recording starts automatically; when the call ends the
   audio is saved and (if configured) transcribed.
4. Open a call in the list to **play**, **read/share the transcript**, **re-transcribe**,
   or **delete**.

### Transcription provider
The transcription call is a standard multipart upload to a Whisper-compatible
endpoint. Defaults (editable in Settings):

- **Base URL:** `https://api.openai.com/v1/audio/transcriptions`
- **Model:** `whisper-1`
- **API key:** your key (stored encrypted on-device via `EncryptedSharedPreferences`)

It also works with any API that mirrors the OpenAI audio-transcriptions contract —
for example a self-hosted `whisper.cpp` server or Groq's Whisper endpoint — just
change the Base URL/model. Audio is uploaded to whichever endpoint you configure, so
choose one you trust.

---

## How it works

| File | Role |
|------|------|
| `CallStateReceiver` | Listens to `PHONE_STATE` / `NEW_OUTGOING_CALL`, detects call start/end and incoming vs outgoing, and starts/stops the service. |
| `CallRecordingService` | Foreground service (`microphone` type). Records with `MediaRecorder`, trying `VOICE_CALL → VOICE_COMMUNICATION → MIC`. On call end, kicks off transcription. |
| `TranscriptionClient` | Uploads the `.m4a` to the configured Whisper endpoint (OkHttp), returns the transcript text. |
| `RecordingStore` / `Recording` | Recordings + sidecar `.txt` transcripts in the app's external files dir; metadata parsed from the filename. |
| `MainActivity` / `RecordingDetailActivity` / `SettingsActivity` | UI: list, playback + transcript + share/delete, settings. |
| `Prefs` | Settings; API key held in `EncryptedSharedPreferences`. |

Recordings are stored in `Android/data/com.calltranscribe.recorder/files/recordings/`.

---

## Known limitations

- **Far-end audio** on stock Android 10+ requires speakerphone (see above).
- **Android 14+ background start:** starting a microphone foreground service from a
  broadcast receiver can be restricted on some Android 14 builds/OEMs. If auto-record
  doesn't trigger on your device, this is the usual cause; a more invasive
  `InCallService`/`ConnectionService` integration would be the next step.
- Some OEMs (MIUI, ColorOS, etc.) require you to enable **Autostart** and disable
  battery optimization for the app so the receiver fires reliably.
- No diarization (it won't label "who said what"); it's a single continuous transcript.

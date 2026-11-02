# Local Voice Services

Setup and configuration for the optional text-to-speech and speech-to-text services. XTTS v2, StyleTTS 2, and Whisper each run as a separate local FastAPI server on the machine with the GPU; ElevenLabs is a cloud alternative configured on the backend with an API key.

## How the pieces connect

**The browser calls the local voice servers directly** (issue #399). The backend never talks to them, so the backend can run anywhere (a cloud VM with no GPU) while voice stays on the machine that has one. Voice works in a browser on that machine; recordings and voice samples never leave it.

- **ElevenLabs** goes through the backend (`/api/tts/*`), since its API key is server-side.
- **Local voice** is a per-browser setting: in the browser on the voice machine, open Settings and turn on **Use voice servers on this machine**. The addresses default to `http://localhost:8021` (StyleTTS 2), `http://localhost:8020` (XTTS) and `http://localhost:8030` (Whisper). Other browsers (a phone, another computer) leave it off and never try to reach localhost.
- **Which TTS speaks:** the first local server that answers, StyleTTS 2 before XTTS; if neither answers (or local voice is off), ElevenLabs if the backend has a key. The settings panel shows what each server answered.
- **Dictation**, chosen in the same panel: *Whisper, else the browser's own* (default), *Whisper only*, or *The browser's own only* (the Web Speech API, no punctuation). With local voice off, the browser's own is used.
- **Voices live with their server.** Each TTS server keeps its cloned voices in its voices directory (the samples plus `voices.json`), manages them (`/voices`), and synthesizes by voice id (`/speak`). The format is the one the backend used to keep, and the default directories (`backend/xtts_voices`, `backend/styletts2_voices`) are the same, so existing voices carry over when the servers run from `backend/`.

The old backend settings `XTTS_ENABLED`, `STYLETTS2_ENABLED`, `WHISPER_ENABLED`, `DICTATION_MODE` and the `*_API_URL` settings no longer do anything; the backend logs a warning at startup if any is still set.

## Who can reach the voice servers

The voice servers have no authentication, so they are fenced two ways:

- **They listen on `127.0.0.1` only** (`--host` defaults to it). Other machines can't reach them. Don't pass `--host 0.0.0.0`.
- **Only the app's own pages may call them from a browser.** A request carrying an `Origin` header must name an allowed origin or it is refused with a 403 before any route runs. CORS alone wouldn't be enough, because a file-upload POST from any website reaches the server without a preflight. The default allowed origins are the app on this machine (`http://localhost:8000`, `http://127.0.0.1:8000`). When the app is served from somewhere else, add its origin on each server:

  ```bash
  python run_xtts.py --allow-origin https://<host>.<tailnet>.ts.net
  ```

  `--allow-origin` is repeatable. The list can also be set with `VOICE_ALLOWED_ORIGINS` (comma-separated), which replaces the defaults. Requests with no `Origin` (curl, scripts on this machine) pass.

The servers take speaker audio as an upload or from their own voice library. No endpoint accepts a file path.

**A page served over HTTPS from another machine** (the cloud VM behind `tailscale serve`) should still be able to call `http://localhost`, since browsers treat localhost as a secure origin, but Chrome's Local Network Access may ask for permission first; allow it for the app's site. *Not yet measured:* the flow was verified with the app at `http://localhost`, not yet from a `ts.net` origin (issue #399). The page must be HTTPS for the microphone to work at all.

## Voice server environment

The servers read these from their own process environment (they don't read the backend's `.env`):

| Variable | Server | Description | Default |
|----------|--------|-------------|---------|
| `VOICE_ALLOWED_ORIGINS` | all | Origins allowed to call the server (replaces the defaults) | the local app |
| `XTTS_VOICES_DIR` | XTTS | Cloned voices: samples and `voices.json` | `./xtts_voices` |
| `XTTS_LANGUAGE` | XTTS | Synthesis language | `en` |
| `XTTS_DEFAULT_SPEAKER` | XTTS | Sample used when no voice is chosen | none |
| `XTTS_PRELOAD_SPEAKERS` | XTTS | Comma-separated samples to warm at startup | none |
| `STYLETTS2_VOICES_DIR` | StyleTTS 2 | Cloned voices: samples and `voices.json` | `./styletts2_voices` |
| `STYLETTS2_DEFAULT_SPEAKER` | StyleTTS 2 | Sample used when no voice is chosen (else the built-in LJSpeech voice) | none |
| `STYLETTS2_PHONEMIZER` | StyleTTS 2 | `gruut` or `espeak` | `gruut` |
| `STYLETTS2_PRELOAD_SPEAKERS` | StyleTTS 2 | Comma-separated samples to warm at startup | none |
| `STYLETTS2_PRONUNCIATION_FIXES` | StyleTTS 2 | JSON map of words to phonetic spellings; `{}` disables | built-in fixes |

Whisper's model is chosen with `--model` (default `large-v3`).

**Backend (ElevenLabs only):**

| Variable | Description | Required |
|----------|-------------|----------|
| `ELEVENLABS_API_KEY` | ElevenLabs API key for cloud TTS | No |
| `ELEVENLABS_VOICE_ID` | Default ElevenLabs voice ID | No (default: Rachel) |
| `ELEVENLABS_VOICES` | JSON array for multiple ElevenLabs voices | No |

## Voice server endpoints

What the browser calls (all behind the origin guard):

| Server | Endpoint | Purpose |
|--------|----------|---------|
| XTTS, StyleTTS 2 | `GET /health` | Answering? |
| XTTS, StyleTTS 2 | `GET /voices` | `{provider, voices}`; no sample paths |
| XTTS, StyleTTS 2 | `POST /voices` | Clone: multipart `audio_file`, `label`, `description`, settings |
| XTTS, StyleTTS 2 | `PUT /voices/{id}`, `DELETE /voices/{id}` | Edit or delete a voice |
| XTTS, StyleTTS 2 | `POST /speak` | JSON `{text, voice_id?, ...}` → `audio/wav` (≤ 5000 characters; StyleTTS 2 takes `alpha`, `beta`, `diffusion_steps`, `embedding_scale`, `speed` overrides) |
| Whisper | `GET /health` | Answering, and `model_loaded` |
| Whisper | `POST /transcribe` | Multipart `audio_file` → `{text, language, ...}` |

XTTS stores its per-voice settings (temperature, penalties, speed) for the UI, but synthesis uses the model's own defaults, as it always has.

## Local XTTS v2 Setup

XTTS v2 provides local, GPU-accelerated text-to-speech with voice cloning. It runs as a separate server.

**Prerequisites:**
- NVIDIA GPU with CUDA (recommended) or CPU (slower)
- Python 3.9-3.11
- ~2GB disk space for model

**Installation:**
```bash
cd backend

# Install PyTorch (GPU version)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# Or for CPU only:
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install XTTS dependencies
pip install -r requirements-xtts.txt
```

**Running the XTTS Server:**
```bash
cd backend
./start-xtts.sh      # Linux/macOS (recommended, auto-activates venv)
# Or manually:
python run_xtts.py
```

The server downloads the XTTS model (~2GB) on first run and starts on port 8020.

**Voice Cloning:** with local voice on, Settings → Clone New Voice uploads a 6-30 second sample to the server. XTTS supports 17 languages including English, Spanish, French, German, Japanese, Chinese, and more.

## Local StyleTTS 2 Setup

StyleTTS 2 provides local, GPU-accelerated text-to-speech with voice cloning and style transfer. When it answers, the browser prefers it over XTTS and ElevenLabs.

**Prerequisites:**
- NVIDIA GPU with CUDA (recommended) or CPU (slower)
- Python 3.9-3.11

**Installation:**
```bash
cd backend

# Install PyTorch (GPU version)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# Or for CPU only:
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install StyleTTS 2 dependencies
pip install -r requirements-styletts2.txt
```

The default phonemizer is gruut (MIT licensed, no system dependencies). For espeak phonemizer, install `espeak-ng` and set `STYLETTS2_PHONEMIZER=espeak`.

**Running the StyleTTS 2 Server:**
```bash
cd backend
./start-styletts2.sh     # Linux/macOS (recommended, auto-activates venv)
# Or manually:
python run_styletts2.py
```

Models are auto-downloaded from HuggingFace on first run (~1GB). Server starts on port 8021.

## Local Whisper STT Setup

Whisper provides local, GPU-accelerated speech-to-text with proper punctuation—a significant improvement over browser-native dictation which lacks punctuation entirely.

**Prerequisites:**
- NVIDIA GPU with CUDA (recommended) or CPU (slower)
- Python 3.9-3.11
- ~3GB disk space for model

**Installation:**
```bash
cd backend

# Install PyTorch (GPU version)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# Or for CPU only:
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install Whisper dependencies
pip install -r requirements-whisper.txt
```

**Running the Whisper Server:**
```bash
cd backend
./start-whisper.sh    # Linux/macOS (recommended, auto-activates venv)
# Or manually:
python run_whisper.py
```

The server downloads the Whisper large-v3 model (~3GB) on first run and starts on port 8030. Dictation uses it once its model has loaded.

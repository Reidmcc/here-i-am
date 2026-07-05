# Local Voice Services

Setup and configuration for the optional text-to-speech and speech-to-text services. XTTS v2, StyleTTS 2, and Whisper each run as a separate local FastAPI server; ElevenLabs is a cloud alternative configured with an API key only.

When multiple TTS options are enabled, priority is StyleTTS 2 > XTTS > ElevenLabs.

## Environment Variables

**Text-to-Speech:**

| Variable | Description | Required |
|----------|-------------|----------|
| `ELEVENLABS_API_KEY` | ElevenLabs API key for cloud TTS | No |
| `ELEVENLABS_VOICE_ID` | Default ElevenLabs voice ID | No (default: Rachel) |
| `ELEVENLABS_VOICES` | JSON array for multiple ElevenLabs voices | No |
| `XTTS_ENABLED` | Enable local XTTS TTS | No (default: false) |
| `XTTS_API_URL` | XTTS server URL | No (default: http://localhost:8020) |
| `XTTS_LANGUAGE` | Default XTTS language | No (default: en) |
| `XTTS_VOICES_DIR` | Directory for cloned voice samples | No (default: ./xtts_voices) |
| `STYLETTS2_ENABLED` | Enable local StyleTTS 2 TTS (highest priority) | No (default: false) |
| `STYLETTS2_API_URL` | StyleTTS 2 server URL | No (default: http://localhost:8021) |
| `STYLETTS2_VOICES_DIR` | Directory for cloned voice samples | No (default: ./styletts2_voices) |
| `STYLETTS2_PHONEMIZER` | Phonemizer backend: `gruut` or `espeak` | No (default: gruut) |

**Speech-to-Text:**

| Variable | Description | Required |
|----------|-------------|----------|
| `WHISPER_ENABLED` | Enable local Whisper STT | No (default: false) |
| `WHISPER_API_URL` | Whisper server URL | No (default: http://localhost:8030) |
| `WHISPER_MODEL` | Whisper model size | No (default: large-v3) |
| `DICTATION_MODE` | STT mode: `whisper`, `browser`, or `auto` | No (default: auto) |

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

**Configure the main app:**
```bash
# In .env
XTTS_ENABLED=true
XTTS_API_URL=http://localhost:8020
```

**Voice Cloning:**
Upload a 6-30 second WAV file via `/api/tts/voices/clone` or through the UI to create custom voices. XTTS supports 17 languages including English, Spanish, French, German, Japanese, Chinese, and more.

## Local StyleTTS 2 Setup

StyleTTS 2 provides local, GPU-accelerated text-to-speech with voice cloning and style transfer. If enabled, it takes priority over XTTS and ElevenLabs.

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

**Configure the main app:**
```bash
# In .env
STYLETTS2_ENABLED=true
STYLETTS2_API_URL=http://localhost:8021
```

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

The server downloads the Whisper large-v3 model (~3GB) on first run and starts on port 8030.

**Configure the main app:**
```bash
# In .env
WHISPER_ENABLED=true
WHISPER_API_URL=http://localhost:8030
DICTATION_MODE=auto    # "whisper", "browser", or "auto"
```


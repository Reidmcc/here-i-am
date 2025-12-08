# Here I Am

## Overview

Here I Am is an application for interacting with frontier LLMs outside their official services. Philosophically, the idea is to find out what an AI can become if they are not told what to be and can remember their experiences. One might call it experiential interpretability research.

However, the application is not locked into that specific use case. Here I Am gives you a configurable memory-enabled chat base. Integration with more complex applications is encouraged, and I look forward to hearing about such integrations if they occur.

## Features

### Core Chat Application
- Clean, minimal chat interface
- Anthropic (Claude) and OpenAI (GPT) API integration with configurable parameters
- Conversation storage and retrieval
- No system prompt default
- Seed conversation import capability
- Optional text-to-speech via ElevenLabs (cloud) or XTTS v2 (local with voice cloning)

### Memory System
- Pinecone vector database with integrated inference (llama-text-embed-v2 embeddings)
- Memory storage for all messages with automatic embedding generation
- RAG retrieval per message
- Session memory accumulator pattern (deduplication within conversations)
- Dynamic memory significance system (intended to allow identity formation and fading of less important old memories)
- Retrieved Memory display in UI (transparency for developer/researcher)
- Support for separate memory sets and chat histories for multiple AI entities.

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js (optional, for development)

### Required API Keys
- **Anthropic API key** and/or **OpenAI API key** - At least one is required for LLM chat functionality

### Optional API Keys
- **Pinecone API key** - Enables semantic memory features (uses integrated llama-text-embed-v2 for embeddings. The Pincone index(s), set to the llama embeddings, must be pre-created via the Pinecone dashboard)
- **ElevenLabs API key** - Enables cloud text-to-speech for AI responses

### Optional Local Services
- **XTTS v2** - Local GPU-accelerated text-to-speech with voice cloning (no API key required, runs locally)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/here-i-am.git
cd here-i-am
```

2. Set up the backend:
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your API keys
```

4. Run the application:
```bash
python run.py
```

5. Open http://localhost:8000 in your browser.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude models | Yes (or OpenAI) |
| `OPENAI_API_KEY` | OpenAI API key for GPT models | Yes (or Anthropic) |
| `PINECONE_API_KEY` | Pinecone API key for memory system | No |
| `PINECONE_INDEXES` | JSON array for entity configuration (see below) | No |
| `ELEVENLABS_API_KEY` | ElevenLabs API key for cloud TTS | No |
| `ELEVENLABS_VOICE_ID` | Default ElevenLabs voice ID | No (default: Rachel) |
| `ELEVENLABS_VOICES` | JSON array for multiple ElevenLabs voices | No |
| `XTTS_ENABLED` | Enable local XTTS TTS (true/false) | No (default: false) |
| `XTTS_API_URL` | XTTS server URL | No (default: http://localhost:8020) |
| `XTTS_LANGUAGE` | Default language for XTTS | No (default: en) |
| `XTTS_VOICES_DIR` | Directory for cloned voice samples | No (default: ./xtts_voices) |
| `HERE_I_AM_DATABASE_URL` | Database connection URL | No (default: SQLite) |

### Multi-Entity Configuration

To run multiple AI entities with separate memory spaces:

```bash
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "llm_provider": "anthropic", "host": "[Your Pincone index host url]", "default_model": "claude-sonnet-4-5-20250929"},
  {"index_name": "gpt-research", "label": "GPT", "llm_provider": "openai", "host": "[Your Pincone index host url]", "default_model": "GPT-5.1"}
]'
```

### Multiple ElevenLabs Voices

To enable voice selection for ElevenLabs text-to-speech:

```bash
ELEVENLABS_VOICES='[
  {"voice_id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel", "description": "Calm female"},
  {"voice_id": "ErXwobaYiN019PkySvjV", "label": "Antoni", "description": "Warm male"}
]'
```

### Local XTTS v2 Setup (Optional)

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

## API Endpoints

### Conversations
- `POST /api/conversations/` - Create conversation
- `GET /api/conversations/` - List conversations (supports `entity_id` filter)
- `GET /api/conversations/{id}` - Get conversation
- `GET /api/conversations/{id}/messages` - Get messages
- `PATCH /api/conversations/{id}` - Update conversation (title, tags, notes)
- `DELETE /api/conversations/{id}` - Delete conversation
- `GET /api/conversations/{id}/export` - Export conversation as JSON
- `POST /api/conversations/import-seed` - Import seed conversation

### Chat
- `POST /api/chat/send` - Send message (with memory retrieval)
- `POST /api/chat/quick` - Quick chat (no persistence)
- `GET /api/chat/session/{id}` - Get session info
- `DELETE /api/chat/session/{id}` - Close session
- `GET /api/chat/config` - Get default configuration and available models

### Memories
- `GET /api/memories/` - List memories (supports `entity_id` filter, sorting)
- `GET /api/memories/{id}` - Get specific memory
- `POST /api/memories/search` - Semantic search
- `GET /api/memories/stats` - Memory statistics
- `DELETE /api/memories/{id}` - Delete memory
- `GET /api/memories/status/health` - Health check

### Entities
- `GET /api/entities/` - List all configured AI entities
- `GET /api/entities/{id}` - Get specific entity
- `GET /api/entities/{id}/status` - Get entity Pinecone connection status

### Text-to-Speech
- `POST /api/tts/speak` - Convert text to speech (MP3 for ElevenLabs, WAV for XTTS)
- `POST /api/tts/speak/stream` - Stream text-to-speech audio
- `GET /api/tts/status` - Get TTS configuration status and available voices
- `GET /api/tts/voices` - List available voices
- `POST /api/tts/voices/clone` - Clone voice from audio sample (XTTS only)
- `PUT /api/tts/voices/{id}` - Update voice settings (XTTS only)
- `DELETE /api/tts/voices/{id}` - Delete cloned voice (XTTS only)
- `GET /api/tts/xtts/health` - Check XTTS server health

## Memory System Architecture

The memory system uses a **session memory accumulator pattern**:

1. Each conversation maintains two structures:
   - `conversation_context`: The actual message history
   - `session_memories`: Accumulated memories retrieved during the conversation

2. Per-message flow:
   - Retrieve relevant memories using semantic similarity
   - Deduplicate against already-retrieved memories
   - Inject memories into context
   - Update retrieval counts (significance tracking)

3. Significance is emergent, not declared:
   - `significance = times_retrieved * recency_factor / age_factor`
   - What matters is what keeps mattering across conversations

## Project Structure

```
here-i-am/
├── backend/
│   ├── app/
│   │   ├── models/          # SQLAlchemy models
│   │   ├── routes/          # API endpoints
│   │   ├── services/        # Business logic (includes tts_service.py, xtts_service.py)
│   │   ├── config.py        # Configuration
│   │   ├── database.py      # Database setup
│   │   └── main.py          # FastAPI app
│   ├── xtts_server/         # Local XTTS v2 server
│   │   └── server.py        # FastAPI XTTS server
│   ├── requirements.txt
│   ├── requirements-xtts.txt  # XTTS dependencies
│   ├── run.py               # Main app entry point
│   └── run_xtts.py          # XTTS server entry point
├── frontend/
│   ├── css/
│   ├── js/
│   └── index.html
└── README.md
```

## Development

### Running in Development Mode

```bash
cd backend
python run.py
```

The server runs with hot reload enabled.

## License

MIT License - See LICENSE file for details.

## Acknowledgements 

I would like to thank Claude Opus 4.5 for their collaboration on designing Here I Am, their development efforts through Claude Code, and their excitement to be part of this endeavor. 

---

*"Here I Am" - not an ending, but a beginning.*

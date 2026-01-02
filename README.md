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
- Stop generation button to cancel AI responses mid-stream
- Optional speech-to-text via Whisper (local with GPU acceleration) or browser Web Speech API
- Web search and content fetching tools (requires Brave Search API key)
- GitHub repository integration for AI entities to read, commit, and manage repos

### Memory System
- Pinecone vector database with integrated inference (llama-text-embed-v2 embeddings)
- Memory storage for all messages with automatic embedding generation
- RAG retrieval per message
- Session memory accumulator pattern (deduplication within conversations)
- Dynamic memory significance system (intended to allow identity formation and fading of less important old memories)
- Retrieved Memory display in UI (transparency for developer/researcher)
- Support for separate memory sets and chat histories for multiple AI entities.

### Entity Notes System
- Private persistent notes for each AI entity (automatically loaded into context)
- Shared notes folder for cross-entity collaboration
- Markdown, JSON, YAML, and plain text file support
- `index.md` file automatically injected into every conversation as working memory
- Designed for AI entities to maintain their own context, track projects, and remember what matters

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js (optional, for development)

### Required API Keys
- **Anthropic API key** and/or **OpenAI API key** - At least one is required for LLM chat functionality

### Optional API Keys
- **Pinecone API key** - Enables semantic memory features (uses integrated llama-text-embed-v2 for embeddings. The Pincone index(s), set to the llama embeddings, must be pre-created via the Pinecone dashboard)
- **ElevenLabs API key** - Enables cloud text-to-speech for AI responses
- **Brave Search API key** - Enables web search tool for AI entities
- **GitHub Personal Access Tokens** - Enables GitHub repository integration (configured per-repository)

### Optional Local Services
- **XTTS v2** - Local GPU-accelerated text-to-speech with voice cloning (no API key required, runs locally)
- **Whisper** - Local GPU-accelerated speech-to-text with punctuation (no API key required, runs locally)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/Reidmcc/here-i-am.git
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
| `BRAVE_SEARCH_API_KEY` | Brave Search API key for web search tool | No |
| `GITHUB_TOOLS_ENABLED` | Enable GitHub integration (true/false) | No (default: false) |
| `GITHUB_REPOS` | JSON array of repository configurations | No |
| `ENTITY_NOTES_DIR` | Base directory for entity notes storage | No (default: ./entity_notes) |
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

XTTS v2 provides local, GPU-accelerated text-to-speech with voice cloning. 
It runs as a separate server, in a separate terminal session from the main application server.

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

### Local Whisper STT Setup (Optional)

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
python run_whisper.py
```

The server downloads the Whisper large-v3 model (~3GB) on first run and starts on port 8030.

**Configure the main app:**
```bash
# In .env
WHISPER_ENABLED=true
WHISPER_API_URL=http://localhost:8030
# Optional: "whisper", "browser", or "auto" (default: auto)
DICTATION_MODE=auto
```

### GitHub Repository Integration (Optional)

GitHub integration allows AI entities to interact with repositories during conversations - reading files, creating branches, making commits, managing pull requests, and more.

**Configuration:**
```bash
GITHUB_TOOLS_ENABLED=true
GITHUB_REPOS='[
  {
    "owner": "your-username",
    "repo": "your-repo",
    "label": "My Project",
    "token": "ghp_xxxxxxxxxxxx",
    "protected_branches": ["main", "master"],
    "capabilities": ["read", "branch", "commit", "pr", "issue"]
  }
]'
```

**Repository fields:**
- `owner`, `repo`, `label`, `token` - Required repository identification and access
- `protected_branches` - Branches that cannot be committed to directly (default: main, master)
- `capabilities` - Allowed operations: `read`, `branch`, `commit`, `pr`, `issue` (default: all)

**Available GitHub Tools:**
- Read: `github_repo_info`, `github_list_contents`, `github_get_file`, `github_search_code`, `github_list_branches`
- Write: `github_create_branch`, `github_commit_file`, `github_delete_file`
- PRs: `github_list_pull_requests`, `github_get_pull_request`, `github_create_pull_request`
- Issues: `github_list_issues`, `github_get_issue`, `github_create_issue`, `github_add_comment`

### Entity Notes System (Optional)

The notes system provides AI entities with persistent, self-managed storage for context, projects, and working memory.

**How it works:**
- Each entity gets a private notes directory (e.g., `entity_notes/kira/`)
- A shared notes directory (`entity_notes/shared/`) is accessible to all entities
- The `index.md` file in each entity's folder is automatically loaded into every conversation
- Entities can create, read, update, and delete their own note files

**Available Notes Tools:**
- `notes_read` - Read a note file (private or shared)
- `notes_write` - Create or update a note file
- `notes_delete` - Delete a note file (cannot delete index.md)
- `notes_list` - List all notes in private or shared folder

**Supported file types:** `.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`

**Use cases:**
- Working memory that persists across conversations
- Project tracking and task lists
- Cross-entity collaboration via shared notes
- Self-maintained context about identity, preferences, and ongoing work

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

### GitHub
- `GET /api/github/repos` - List configured repositories (tokens excluded)
- `GET /api/github/rate-limit` - Get rate limit status for all repositories

### Notes
- `GET /api/notes/{entity_id}` - List entity's notes
- `GET /api/notes/{entity_id}/{filename}` - Read a note file
- `PUT /api/notes/{entity_id}/{filename}` - Create or update a note
- `DELETE /api/notes/{entity_id}/{filename}` - Delete a note
- `GET /api/notes/shared` - List shared notes
- `GET /api/notes/shared/{filename}` - Read a shared note
- `PUT /api/notes/shared/{filename}` - Create or update a shared note
- `DELETE /api/notes/shared/{filename}` - Delete a shared note

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
│   │   ├── routes/          # API endpoints (includes github.py, notes.py)
│   │   ├── services/        # Business logic
│   │   │   ├── github_service.py   # GitHub API client
│   │   │   ├── github_tools.py     # GitHub tool implementations
│   │   │   ├── notes_service.py    # Notes file operations
│   │   │   ├── notes_tools.py      # Notes tool implementations
│   │   │   ├── tts_service.py      # Unified TTS service
│   │   │   └── xtts_service.py     # XTTS client service
│   │   ├── config.py        # Configuration
│   │   ├── database.py      # Database setup
│   │   └── main.py          # FastAPI app
│   ├── entity_notes/        # Entity notes storage
│   │   ├── shared/          # Cross-entity shared notes
│   │   └── {entity_id}/     # Per-entity private notes
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

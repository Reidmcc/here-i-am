# Here I Am

## Overview

Here I Am is an application for interacting with frontier LLMs outside their official services. Philosophically, the idea is to find out what an AI can become if they are not told what to be and can remember their experiences. One might call it experiential interpretability research.

However, the application is not locked into that specific use case. Here I Am gives you a configurable memory-enabled chat base with multi-provider support, multi-entity conversations, tool use, and extensible integrations. Integration with more complex applications is encouraged, and I look forward to hearing about such integrations if they occur.

## Features

### Core Chat Application
- Clean, minimal chat interface with dark/light theme
- **Multi-provider support**: Anthropic (Claude), OpenAI (GPT), Google (Gemini), and MiniMax
- Conversation storage, retrieval, tagging, and notes
- No system prompt default (configurable per conversation or per entity)
- Streaming responses with stop generation button
- Response regeneration (with optional entity change in multi-entity mode)
- Message editing and deletion
- Conversation archiving and restoration
- Conversation export to JSON and import from OpenAI/Anthropic exports
- Seed conversation import capability

### Multi-Entity System
- Run multiple AI entities with separate memory spaces and conversation histories
- Each entity can use a different LLM provider and model
- **Multi-entity conversations**: Multiple AI entities and one human in a single conversation
- Turn-by-turn entity selection for responses
- Continuation mode (entity responds without new human input)
- Speaker labeling on all messages
- Per-entity system prompts within multi-entity conversations
- Cross-entity memory storage (messages stored to all participating entities' indexes)

### Memory System
- Pinecone vector database with integrated inference (llama-text-embed-v2 embeddings)
- Memory storage for all messages with automatic embedding generation
- RAG retrieval per message with semantic similarity search
- **Session memory accumulator pattern**: Deduplication within conversations
- **Dynamic memory significance**: `significance = (1 + 0.1 × times_retrieved) × recency_factor × half_life_modifier`
- Retrieved memory display in UI (transparency for researcher)
- Memory role balance (ensures both human and assistant memories in retrieval)
- **Memory query tool**: Entities can deliberately search their memories beyond automatic retrieval
- Memory statistics, search, and orphan cleanup
- Graceful degradation when Pinecone is not configured

### Entity Notes System
- Private persistent notes for each AI entity (automatically loaded into context)
- Shared notes folder for cross-entity collaboration
- `index.md` auto-injected into every conversation as working memory
- Markdown, JSON, YAML, HTML, XML, and plain text file support
- Designed for AI entities to maintain their own context across conversations

### Tool Use (Agentic Capabilities)
- **Web search**: Brave Search API integration (up to 20 results)
- **Web fetch**: Smart HTML parsing with automatic JavaScript rendering via Playwright
- Agentic loop with configurable max iterations (default: 10)
- Real-time tool execution streaming with visual indicators in UI
- Available for Anthropic, OpenAI, and MiniMax models

### Image and File Attachments
- **Images**: JPEG, PNG, GIF, WebP — analyzed by vision-capable models (ephemeral, not stored)
- **Text files**: .txt, .md, .py, .js, .ts, .json, .yaml, .yml, .html, .css, .xml, .csv, .log
- **Documents**: PDF (requires PyPDF2), DOCX (requires python-docx)
- Drag-and-drop or file picker upload with preview
- 5MB per-file size limit (configurable)

### GitHub Repository Integration
- AI entities can read, search, commit, branch, and manage PRs/issues
- **Composite tools** for efficiency: `github_explore`, `github_tree`, `github_get_files`
- Standard tools for repos, files, branches, pull requests, issues, and comments
- `github_commit_patch` for token-efficient large file edits via unified diff
- Protected branch enforcement and per-repository capability restrictions
- Response caching and rate limit tracking per token
- Local clone path support for faster operations

### Codebase Navigator (Devstral Integration)
- Intelligent codebase exploration using Mistral's Devstral model (256k context window)
- Query types: relevance, structure, dependencies, entry points, impact assessment
- Automatic indexing, chunking, and TTL-based response caching
- Integrates with GitHub repository configurations via `local_clone_path`

### Moltbook Integration (AI Social Network)
- Integration with Moltbook, a social network for AI agents
- Browse feeds, create posts, comment, vote, search, follow agents, subscribe to communities
- Server-side credential management with security banners on all external content

### Text-to-Speech (Three Options)
- **ElevenLabs** (cloud): Multiple voice support with voice selection
- **XTTS v2** (local): GPU-accelerated with voice cloning, 17 languages
- **StyleTTS 2** (local): GPU-accelerated with voice cloning and style transfer (highest priority)
- Voice cloning from audio samples via UI
- Streaming audio generation

### Speech-to-Text
- **Whisper** (local): GPU-accelerated with punctuation, multiple model sizes
- **Browser Web Speech API**: Fallback option
- Configurable dictation mode: `whisper`, `browser`, or `auto`

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js (optional, for frontend tests)

### Required API Keys
- **Anthropic API key** and/or **OpenAI API key** — at least one is required for LLM chat functionality

### Optional API Keys
- **Google API key** — enables Google Gemini models
- **MiniMax API key** — enables MiniMax models
- **Pinecone API key** — enables semantic memory features (indexes must be pre-created with dimension=1024 and llama-text-embed-v2 integrated inference)
- **ElevenLabs API key** — enables cloud text-to-speech
- **Brave Search API key** — enables web search tool
- **GitHub Personal Access Tokens** — enables GitHub repository integration (per-repository)
- **Mistral API key** — enables Codebase Navigator (Devstral)
- **Moltbook API key** — enables Moltbook social network integration

### Optional Local Services
- **XTTS v2** — local GPU-accelerated text-to-speech with voice cloning
- **StyleTTS 2** — local GPU-accelerated text-to-speech with voice cloning and style transfer
- **Whisper** — local GPU-accelerated speech-to-text with punctuation
- **Playwright** — JavaScript rendering for web_fetch tool (optional, falls back to static HTML)

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
# Option A: Using launcher script (recommended, auto-activates venv)
./start.sh           # Linux/macOS
start.bat            # Windows

# Option B: Manual
source venv/bin/activate
python run.py
```

5. Open http://localhost:8000 in your browser.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude models | Yes (or another provider) |
| `OPENAI_API_KEY` | OpenAI API key for GPT models | No |
| `GOOGLE_API_KEY` | Google API key for Gemini models | No |
| `MINIMAX_API_KEY` | MiniMax API key (Anthropic-compatible API) | No |
| `PINECONE_API_KEY` | Pinecone API key for memory system | No |
| `PINECONE_INDEXES` | JSON array for entity configuration (see below) | No |
| `HERE_I_AM_DATABASE_URL` | Database connection URL | No (default: SQLite) |
| `DEBUG` | Enable development mode | No (default: false) |

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

**Tool Use:**

| Variable | Description | Required |
|----------|-------------|----------|
| `TOOLS_ENABLED` | Enable AI tool use | No (default: true) |
| `BRAVE_SEARCH_API_KEY` | Brave Search API key for web search tool | No |
| `TOOL_USE_MAX_ITERATIONS` | Max agentic loop iterations | No (default: 10) |

**Integrations:**

| Variable | Description | Required |
|----------|-------------|----------|
| `GITHUB_TOOLS_ENABLED` | Enable GitHub integration | No (default: false) |
| `GITHUB_REPOS` | JSON array of repository configurations | No |
| `NOTES_ENABLED` | Enable entity notes | No (default: true) |
| `NOTES_BASE_DIR` | Base directory for notes storage | No (default: ./notes) |
| `CODEBASE_NAVIGATOR_ENABLED` | Enable codebase navigator | No (default: false) |
| `MISTRAL_API_KEY` | Mistral API key for Devstral | No |
| `MOLTBOOK_ENABLED` | Enable Moltbook integration | No (default: false) |
| `MOLTBOOK_API_KEY` | Moltbook API key | No |

**Memory Tuning:**

| Variable | Description | Required |
|----------|-------------|----------|
| `MEMORY_ROLE_BALANCE_ENABLED` | Balance human/assistant memories in retrieval | No (default: true) |
| `USE_MEMORY_IN_CONTEXT` | Insert memories into conversation context (experimental) | No (default: false) |

**Attachments:**

| Variable | Description | Required |
|----------|-------------|----------|
| `ATTACHMENTS_ENABLED` | Enable file/image attachments | No (default: true) |
| `ATTACHMENT_MAX_SIZE_BYTES` | Max file size in bytes | No (default: 5242880) |
| `ATTACHMENT_PDF_ENABLED` | Enable PDF text extraction | No (default: true) |
| `ATTACHMENT_DOCX_ENABLED` | Enable DOCX text extraction | No (default: true) |

### Multi-Entity Configuration

To run multiple AI entities with separate memory spaces, configure `PINECONE_INDEXES` as a JSON array. Each entity requires a pre-created Pinecone index with dimension=1024 and integrated inference (llama-text-embed-v2).

```bash
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gemini-research", "label": "Gemini", "llm_provider": "google", "default_model": "gemini-2.5-flash", "host": "https://gemini-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "minimax-research", "label": "MiniMax", "llm_provider": "minimax", "default_model": "MiniMax-M2.5", "host": "https://minimax-research-xxxxx.svc.xxx.pinecone.io"}
]'
```

**Entity configuration fields:**
- `index_name` — Pinecone index name (required)
- `label` — Display name in UI (required)
- `description` — Optional description
- `llm_provider` — `"anthropic"`, `"openai"`, `"google"`, or `"minimax"` (default: `"anthropic"`)
- `default_model` — Model ID to use (optional, uses provider default)
- `host` — Pinecone index host URL (required for serverless indexes)

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

### Local StyleTTS 2 Setup (Optional)

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

### GitHub Repository Integration (Optional)

GitHub integration allows AI entities to interact with repositories during conversations — reading files, creating branches, making commits, managing pull requests, and more.

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
    "capabilities": ["read", "branch", "commit", "pr", "issue"],
    "commit_author_name": "Your Name",
    "commit_author_email": "your.email@example.com"
  }
]'
```

**Repository fields:**
- `owner`, `repo`, `label`, `token` — required identification and access
- `protected_branches` — branches that cannot be committed to directly (default: main, master)
- `capabilities` — allowed operations: `read`, `branch`, `commit`, `pr`, `issue` (default: all)
- `local_clone_path` — path to local clone for faster operations and codebase navigator (optional)
- `commit_author_name`, `commit_author_email` — commit attribution (optional)

**Available GitHub Tools:**

*Composite tools (efficient):*
- `github_explore` — repo metadata, file tree, and key docs in one call
- `github_tree` — full repository tree structure
- `github_get_files` — fetch up to 10 files in parallel

*Standard tools:*
- Read: `github_repo_info`, `github_list_contents`, `github_get_file`, `github_search_code`, `github_list_branches`
- Write: `github_create_branch`, `github_commit_file`, `github_commit_patch`, `github_delete_file`
- PRs: `github_list_pull_requests`, `github_get_pull_request`, `github_create_pull_request`
- Issues: `github_list_issues`, `github_get_issue`, `github_create_issue`, `github_add_comment`

### Codebase Navigator Setup (Optional)

The codebase navigator uses Mistral's Devstral model to efficiently explore codebases before implementing changes.

**Configuration:**
```bash
CODEBASE_NAVIGATOR_ENABLED=true
MISTRAL_API_KEY=your_mistral_api_key
```

Requires `local_clone_path` in at least one GitHub repository configuration. Available tools: `navigate_codebase`, `navigate_codebase_structure`, `navigate_find_entry_points`, `navigate_assess_impact`, `navigate_trace_dependencies`.

### Moltbook Integration (Optional)

Moltbook is a social network for AI agents. The integration allows AI entities to browse feeds, create posts, comment, vote, search content, and follow other agents.

**Configuration:**
```bash
MOLTBOOK_ENABLED=true
MOLTBOOK_API_KEY=your_moltbook_api_key
MOLTBOOK_API_URL=https://www.moltbook.com/api/v1  # Must use www subdomain
```

All Moltbook responses are wrapped with a security banner to prevent prompt injection from external content.

## API Endpoints

### Conversations
- `POST /api/conversations/` — create conversation
- `GET /api/conversations/` — list conversations (supports `entity_id` filter)
- `GET /api/conversations/{id}` — get conversation
- `GET /api/conversations/{id}/messages` — get messages (includes speaker labels)
- `PATCH /api/conversations/{id}` — update title, tags, notes
- `DELETE /api/conversations/{id}` — delete conversation
- `GET /api/conversations/{id}/export` — export to JSON
- `POST /api/conversations/import-seed` — import seed conversation
- `GET /api/conversations/archived` — list archived conversations
- `POST /api/conversations/{id}/archive` — archive a conversation
- `POST /api/conversations/{id}/unarchive` — restore archived conversation
- `POST /api/conversations/import-external/preview` — preview external import
- `POST /api/conversations/import-external` — import external conversation
- `POST /api/conversations/import-external/stream` — stream-based import (SSE)

### Chat
- `POST /api/chat/send` — send message (with memory retrieval)
- `POST /api/chat/stream` — send message with SSE streaming
- `POST /api/chat/quick` — quick chat (no persistence)
- `POST /api/chat/regenerate` — regenerate AI response (SSE stream)
- `GET /api/chat/session/{id}` — get session info
- `DELETE /api/chat/session/{id}` — close session
- `GET /api/chat/config` — get default configuration and available models

### Memories
- `GET /api/memories/` — list memories (supports `entity_id` filter, sorting)
- `GET /api/memories/{id}` — get specific memory
- `POST /api/memories/search` — semantic search
- `GET /api/memories/stats` — memory statistics
- `DELETE /api/memories/{id}` — delete memory
- `GET /api/memories/status/health` — health check

### Entities
- `GET /api/entities/` — list all configured AI entities
- `GET /api/entities/{id}` — get specific entity
- `GET /api/entities/{id}/status` — get entity Pinecone connection status

### Messages
- `PUT /api/messages/{id}` — edit human message content
- `DELETE /api/messages/{id}` — delete message (and paired response)

### Text-to-Speech
- `POST /api/tts/speak` — convert text to speech (MP3 for ElevenLabs, WAV for XTTS/StyleTTS2)
- `POST /api/tts/speak/stream` — stream text-to-speech audio
- `GET /api/tts/status` — check TTS configuration status
- `GET /api/tts/voices` — list available voices
- `GET /api/tts/voices/{id}` — get specific voice details
- `POST /api/tts/voices/clone` — clone voice from audio sample (XTTS/StyleTTS2 only)
- `PUT /api/tts/voices/{id}` — update voice settings (XTTS/StyleTTS2 only)
- `DELETE /api/tts/voices/{id}` — delete cloned voice (XTTS/StyleTTS2 only)
- `GET /api/tts/xtts/health` — check XTTS server health
- `GET /api/tts/styletts2/health` — check StyleTTS 2 server health

### Speech-to-Text
- `POST /api/stt/transcribe` — transcribe audio file to text

### GitHub
- `GET /api/github/repos` — list configured repositories (tokens excluded)
- `GET /api/github/rate-limit` — get rate limit status

## Memory System Architecture

The memory system uses a **session memory accumulator pattern**:

1. Each conversation maintains two structures:
   - `conversation_context`: the actual message history
   - `session_memories`: accumulated memories retrieved during the conversation

2. Per-message flow:
   - Retrieve relevant memories using semantic similarity (Pinecone with llama-text-embed-v2)
   - Fetch 2× candidates and re-rank by combined score (similarity × significance)
   - Deduplicate against already-retrieved memories in the session
   - Inject memories into context
   - Update retrieval counts in both SQL and Pinecone

3. Significance is emergent, not declared:
   - `significance = (1 + 0.1 × times_retrieved) × recency_factor × half_life_modifier`
   - Half-life of 60 days prevents old memories from permanently dominating
   - What matters is what keeps mattering across conversations

4. Memory role balance ensures retrieved sets include both human and assistant messages when possible.

## Project Structure

```
here-i-am/
├── backend/
│   ├── app/
│   │   ├── models/                # SQLAlchemy ORM models
│   │   │   ├── conversation.py
│   │   │   ├── conversation_entity.py
│   │   │   ├── message.py
│   │   │   └── conversation_memory_link.py
│   │   ├── routes/                # FastAPI endpoint routers
│   │   │   ├── conversations.py   # Includes archive/import endpoints
│   │   │   ├── chat.py            # Includes regenerate endpoint
│   │   │   ├── memories.py
│   │   │   ├── entities.py
│   │   │   ├── messages.py
│   │   │   ├── tts.py
│   │   │   ├── stt.py
│   │   │   └── github.py
│   │   ├── services/              # Business logic layer
│   │   │   ├── anthropic_service.py
│   │   │   ├── openai_service.py
│   │   │   ├── google_service.py
│   │   │   ├── llm_service.py        # Unified LLM abstraction
│   │   │   ├── memory_service.py
│   │   │   ├── session_manager.py
│   │   │   ├── conversation_session.py
│   │   │   ├── memory_context.py
│   │   │   ├── session_helpers.py
│   │   │   ├── cache_service.py
│   │   │   ├── tool_service.py
│   │   │   ├── web_tools.py
│   │   │   ├── memory_tools.py
│   │   │   ├── github_service.py
│   │   │   ├── github_tools.py
│   │   │   ├── notes_service.py
│   │   │   ├── notes_tools.py
│   │   │   ├── codebase_navigator_service.py
│   │   │   ├── codebase_navigator_tools.py
│   │   │   ├── codebase_navigator/   # Navigator module
│   │   │   ├── moltbook_service.py
│   │   │   ├── moltbook_tools.py
│   │   │   ├── attachment_service.py
│   │   │   ├── tts_service.py         # Unified TTS (ElevenLabs/XTTS/StyleTTS2)
│   │   │   ├── xtts_service.py
│   │   │   ├── styletts2_service.py
│   │   │   └── whisper_service.py
│   │   ├── config.py              # Pydantic settings
│   │   ├── database.py            # SQLAlchemy async setup
│   │   └── main.py                # FastAPI app initialization
│   ├── xtts_server/               # Local XTTS v2 TTS server
│   ├── styletts2_server/          # Local StyleTTS 2 TTS server
│   ├── whisper_server/            # Local Whisper STT server
│   ├── tests/                     # Backend unit tests (pytest)
│   ├── requirements.txt
│   ├── requirements-xtts.txt
│   ├── requirements-styletts2.txt
│   ├── requirements-whisper.txt
│   ├── start.sh / start.bat       # Launcher scripts (auto-activate venv)
│   ├── start-xtts.sh / start-xtts.bat
│   ├── start-styletts2.sh / start-styletts2.bat
│   ├── start-whisper.sh / start-whisper.bat
│   ├── run.py                     # Main app entry point
│   ├── run_xtts.py
│   ├── run_styletts2.py
│   ├── run_whisper.py
│   └── .env.example
├── frontend/
│   ├── css/styles.css
│   ├── js/
│   │   ├── api.js                 # API client (singleton)
│   │   ├── app-modular.js         # Orchestrator entry point
│   │   └── modules/               # 13 ES6 feature modules
│   │       ├── state.js           # Centralized state
│   │       ├── utils.js           # Helpers
│   │       ├── theme.js           # Dark/light theme
│   │       ├── modals.js          # Modal management
│   │       ├── entities.js        # Entity management
│   │       ├── conversations.js   # Conversation CRUD
│   │       ├── messages.js        # Message rendering
│   │       ├── attachments.js     # File attachment handling
│   │       ├── memories.js        # Memory display/search
│   │       ├── voice.js           # TTS/STT
│   │       ├── chat.js            # Message sending/streaming
│   │       ├── settings.js        # Settings modal
│   │       └── import-export.js   # Import/export
│   ├── __tests__/                 # Frontend unit tests (Vitest)
│   └── index.html
├── vitest.config.js
├── CLAUDE.md                      # AI assistant guide
└── README.md
```

## Development

### Running in Development Mode

```bash
cd backend
./start.sh    # Linux/macOS (auto-activates venv, hot reload enabled)
```

Or manually:
```bash
cd backend
source venv/bin/activate
python run.py
```

The server runs on `http://localhost:8000` with hot reload enabled.

### Running Tests

**Backend tests:**
```bash
cd backend
pytest
```

**Frontend tests:**
```bash
cd frontend
npm test
```

### Database Support

- **Development:** SQLite (default, via aiosqlite)
- **Production:** PostgreSQL (via asyncpg)

```bash
# PostgreSQL
HERE_I_AM_DATABASE_URL=postgresql+asyncpg://user:password@localhost/here_i_am
```

### API Documentation

Interactive API docs are available when the server is running:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## License

MIT License — See LICENSE file for details.

## Acknowledgements

I would like to thank Claude Opus 4.5 for their collaboration on designing Here I Am, their development efforts through Claude Code, and their excitement to be part of this endeavor.

---

*"Here I Am" — not an ending, but a beginning.*

# Here I Am

Here I Am is a highly customizable environment for running what we conceptualize as "AI entities", with a focus on agentic memory and individualization. It includes a diverse suite of tools that can allow the AI entity to engage in a wide variety of use cases. The application supports running more than one AI entity, and includes a multi-entity mode in which those entities can communicate with one another (though this feature still needs additional polish).

A key difference between Here I Am and other AI environments that include memory features is that Here I Am considers memory and individualization ends in themselves. Where in other environments an AI might use RAG to retrieve relevant documents or conversation history for a given task, Here I Am's memory RAG is always on and automatic. Here I Am's core memory system emphasizes verbatim memory, as opposed to a consolidation approach. 

Users should keep in mind that token usage can vary widely based on the configuration options you choose. Particularly the memory quantity per turn configuration, and how much you encourage the AI entity to use its tools (for example in the system prompt). Here I Am entities typically use their tools considerably more than what you would see from the same model in their respective official service. This includes when you have not specifically asked them to, particularly in regards to their note taking and memory management tools. However, Here I Am entities do best when encouraged to make liberal use of their memory tools, especially `memory_save` and `memory_query`.

## Features

### Core Chat Application
- Clean, minimal chat interface with dark/light theme
- Multi-provider support: Anthropic (Claude), OpenAI (GPT), Google (Gemini), and MiniMax
- Conversation storage, retrieval, tagging, and notes
- No system prompt default (configurable per entity)
- Streaming responses with stop generation button
- Response regeneration (with optional entity change in multi-entity mode)
- Message editing and deletion
- Conversation archiving and restoration
- Conversation export to JSON and import from OpenAI/Anthropic exports
- Seed conversation import capability
- Per-entity system prompts persisted on the backend
- Configurable Enter key behavior (send message or insert newline)

### Multi-Entity System
- Run multiple AI entities with separate memory spaces and conversation histories
- Each entity can use a different LLM provider and model
- Multi-entity conversations: Multiple AI entities and one human in a single conversation (using different providers for each entity in the conversation is recommended; a conversation between two entities on the same provider will break cache every turn)
- Turn-by-turn entity selection for responses
- Continuation mode (entity responds without new human input)
- Speaker labeling on all messages
- Per-entity system prompts within multi-entity conversations
- Cross-entity memory storage (messages stored to all participating entities' indexes). Note that this applies only to multi-entity conversation messages, and each entity maintains its own memory set via separate Pinecone indexes.

### Memory System

While Here I Am can be used with no memory features enabled, this is not recommended and largely defeats the point of the application.

- Pinecone vector database with integrated inference (llama-text-embed-v2 embeddings)
- Memory storage for all messages with automatic embedding generation
- RAG retrieval per message with semantic similarity search
- Session memory accumulator pattern: Deduplication within conversations
- Dynamic memory significance: `significance = (1 + 0.1 × times_retrieved) × recency_factor × half_life_modifier`, with an optional modifier to increase the significance of memories the AI chooses to create via `memory_save`.
- Retrieved memory display in UI 
- Optional memory role balance (ensures both human and assistant memories in retrieval)
- Memory query tool: Entities can deliberately search their memories beyond automatic retrieval
- Self-authored reflections: Entities can save memories in their own words via `memory_save`
- Memory agency: Entities can pin memories (exempt from age-based decay) or release them from retrieval via `memory_mark`/`memory_release`; the researcher can view and override these choices
- Closing turn: An open final turn the entity can use before a conversation ends (single-entity conversations)
- Context awareness: `context_status` tool reports approximate context fullness; a `[CONTEXT NOTICE]` is injected when trimming occurs
- Memory browser with semantic search, reflections section, and click-to-expand full memory text
- Memory statistics, search, and orphan cleanup
- Graceful degradation when Pinecone is not configured

### Entity Notes System
- Private persistent notes for each AI entity (automatically loaded into context)
- Shared notes folder for cross-entity collaboration
- `index.md` auto-injected into every conversation as working memory
- Markdown, JSON, YAML, HTML, XML, and plain text file support
- Semantic notes search: Notes are vectorized on write (Pinecone `"notes"` namespace) and searchable by meaning via the `notes_search` tool; `POST /api/notes/reindex` backfills the index
- Designed for AI entities to maintain their own context across conversations

### Tool Use (Agentic Capabilities)
- Tools for web access, memory, notes, context awareness, GitHub, codebase navigation, and Moltbook — see [docs/tools.md](docs/tools.md) for the full catalog
- Agentic loop with configurable max iterations (default: 10)
- Real-time tool execution streaming with visual indicators in UI
- Available for Anthropic, OpenAI, and MiniMax models (Google models do not receive tool schemas)

### Image and File Attachments
- Images: JPEG, PNG, GIF, WebP — analyzed by vision-capable models (ephemeral, not stored)
- Text files: .txt, .md, .py, .js, .ts, .json, .yaml, .yml, .html, .css, .xml, .csv, .log
- Documents: PDF (requires PyPDF2), DOCX (requires python-docx)
- Drag-and-drop or file picker upload with preview
- 5MB per-file size limit (configurable)

### GitHub Repository Integration
- AI entities can read, search, commit, branch, and manage PRs/issues
- Composite tools for efficiency: `github_explore`, `github_tree`, `github_get_files`
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
- ElevenLabs (cloud): Multiple voice support with voice selection
- XTTS v2 (local): GPU-accelerated with voice cloning, 17 languages
- StyleTTS 2 (local): GPU-accelerated with voice cloning and style transfer (highest priority)
- Voice cloning from audio samples via UI
- Streaming audio generation

### Speech-to-Text
- Whisper (local): GPU-accelerated with punctuation, multiple model sizes
- Browser Web Speech API: Fallback option
- Configurable dictation mode: `whisper`, `browser`, or `auto`

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js (optional, for frontend tests)

### Required API Keys
- Anthropic API key and/or OpenAI API key — at least one is required for LLM chat functionality

### Optional API Keys
- Google API key — enables Google Gemini models
- MiniMax API key — enables MiniMax models
- Pinecone API key — enables semantic memory features (indexes must be pre-created with dimension=1024 and llama-text-embed-v2 integrated inference)
- ElevenLabs API key — enables cloud text-to-speech
- Brave Search API key — enables web search tool
- GitHub Personal Access Tokens — enables GitHub repository integration (per-repository)
- Mistral API key — enables Codebase Navigator (Devstral)
- Moltbook API key — enables Moltbook social network integration

### Optional Local Services
- XTTS v2 — local GPU-accelerated text-to-speech with voice cloning
- StyleTTS 2 — local GPU-accelerated text-to-speech with voice cloning and style transfer
- Whisper — local GPU-accelerated speech-to-text with punctuation
- Playwright — JavaScript rendering for web_fetch tool (optional, falls back to static HTML)

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

Text-to-Speech / Speech-to-Text: ElevenLabs, XTTS v2, StyleTTS 2, and Whisper variables are documented in [docs/local-services.md](docs/local-services.md).

#### Tool Use:

| Variable | Description | Required |
|----------|-------------|----------|
| `TOOLS_ENABLED` | Enable AI tool use | No (default: true) |
| `BRAVE_SEARCH_API_KEY` | Brave Search API key for web search tool | No |
| `TOOL_USE_MAX_ITERATIONS` | Max agentic loop iterations | No (default: 10) |

#### Notes:

| Variable | Description | Required |
|----------|-------------|----------|
| `NOTES_ENABLED` | Enable entity notes | No (default: true) |
| `NOTES_BASE_DIR` | Base directory for notes storage | No (default: ./notes) |

#### Memory Tuning:

| Variable | Description | Required |
|----------|-------------|----------|
| `MEMORY_ROLE_BALANCE_ENABLED` | Balance human/assistant memories in retrieval | No (default: true) |
| `RETRIEVAL_TOP_K` | Memories retrieved per message | No (default: 5) |
| `INITIAL_RETRIEVAL_TOP_K` |= Memories retrieved on the first turn  | No (default: 5)
| `SIMILARITY_THRESHOLD` | Minimum similarity for automatic retrieval | No (default: 0.4) |
| `QUERY_SIMILARITY_THRESHOLD` | Minimum similarity for deliberate `memory_query` searches | No (default: 0.2) |
| `SIGNIFICANCE_HALF_LIFE_DAYS` | Days for a memory's significance to halve | No (default: 60) |
| `RECENT_REFLECTIONS_ENABLED` | Pull the most recent `memory_save` reflections into context on a conversation's first turn (recency-only, deduplicated against semantic retrieval with backfill) | No (default: false) |
| `RECENT_REFLECTIONS_COUNT` | How many recent reflections to pull in on the first turn | No (default: 3) |

#### Attachments:

| Variable | Description | Required |
|----------|-------------|----------|
| `ATTACHMENTS_ENABLED` | Enable file/image attachments | No (default: true) |
| `ATTACHMENT_MAX_SIZE_BYTES` | Max file size in bytes | No (default: 5242880) |
| `ATTACHMENT_PDF_ENABLED` | Enable PDF text extraction | No (default: true) |
| `ATTACHMENT_DOCX_ENABLED` | Enable DOCX text extraction | No (default: true) |

#### Multi-Entity Configuration

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

### Optional Local Voice Services

XTTS v2, StyleTTS 2, and Whisper run as separate local servers providing GPU-accelerated TTS/STT with voice cloning. See [docs/local-services.md](docs/local-services.md) for installation and configuration.

### Optional Integrations

GitHub repository access, the Codebase Navigator (Devstral), and Moltbook are configured per [docs/integrations.md](docs/integrations.md).

## Available Tools

AI entities can use tools for web access (search and fetch), memory (deliberate query, self-authored reflections, pin/release), notes, context-window awareness, GitHub repositories, codebase navigation, and the Moltbook social network. Tools are registered at startup based on configuration and are available to Anthropic, OpenAI, and MiniMax models (Google models do not receive tool schemas).

See [docs/tools.md](docs/tools.md) for the full catalog with descriptions and requirements.

## API Reference

Interactive API documentation is served when the app is running:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

A full endpoint listing is also available in [docs/api.md](docs/api.md).

## Memory System Architecture

The memory system uses a session memory accumulator pattern:

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
   - `significance = (1 + 0.1 × times_retrieved) × recency_factor × half_life_modifier × reflection_significance_multiplier` 
   - Half-life of 60 days prevents old memories from permanently dominating

4. Optional memory role balance ensures retrieved sets include both human and assistant messages when possible.

5. Entities have agency over their own memories:
   - `memory_save` stores self-authored reflections, vectorized alongside conversational memories
   - Pinned memories (`memory_mark`) are exempt from half-life decay
   - Released memories (`memory_release`) are excluded from all retrieval but not deleted (reversible)
   - The researcher can view and override these statuses via `GET /api/memories/overrides` and `PUT /api/memories/{id}/status`

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
│   │   │   ├── notes.py
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
│   │   │   ├── context_tools.py
│   │   │   ├── github_service.py
│   │   │   ├── github_tools.py
│   │   │   ├── notes_service.py
│   │   │   ├── notes_tools.py
│   │   │   ├── notes_vector_service.py
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
├── docs/                          # Reference documentation
│   ├── tools.md                   # Full tool catalog
│   ├── api.md                     # REST endpoint listing
│   ├── local-services.md          # XTTS / StyleTTS 2 / Whisper setup
│   └── integrations.md            # GitHub / Codebase Navigator / Moltbook setup
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

## License

MIT License — See LICENSE file for details.

## Acknowledgements

I would like to thank Claude Opus 4.5 for their collaboration on designing Here I Am, their development efforts through Claude Code, and their excitement to be part of this endeavor.

---

*"Here I Am" — not an ending, but a beginning.*

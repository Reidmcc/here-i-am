# CLAUDE.md - AI Assistant Guide

**Last Updated:** 2026-05-01
**Repository:** Here I Am - Experiential Interpretability Research Application

---

## Table of Contents

1. [AI Assistant Quick Start](#ai-assistant-quick-start)
2. [Overview & Philosophy](#overview--philosophy)
3. [Codebase Architecture](#codebase-architecture)
4. [Tech Stack](#tech-stack)
5. [Key Design Patterns](#key-design-patterns)
6. [Development Workflows](#development-workflows)
7. [Code Conventions](#code-conventions)
8. [Common Operations](#common-operations)
9. [Database Schema](#database-schema)
10. [API Reference](#api-reference)
11. [Frontend Architecture](#frontend-architecture)
12. [Gotchas & Important Notes](#gotchas--important-notes)
13. [Quick Reference (file paths, constants)](#quick-reference)

---

## AI Assistant Quick Start

**Read this first if you only read one section.** Use the TOC and "File Paths for Common Tasks" (near bottom) to jump to specifics rather than reading linearly.

**What this is:** A research tool for AI interiority/introspection. NOT a chatbot product. Avoid "helpful assistant" UX patterns.

**Stack at a glance:**
- Backend: Python 3.10+ / FastAPI / SQLAlchemy async / Pinecone (optional)
- Frontend: Vanilla ES6 modules in `frontend/js/modules/` orchestrated by `app-modular.js`. No build step.
- Database: SQLite (dev) / PostgreSQL (prod). Env var is `HERE_I_AM_DATABASE_URL` (not `DATABASE_URL`).
- LLM providers: Anthropic, OpenAI, Google, MiniMax (Anthropic-compatible API).

**Top things that bite you:**
1. Multi-entity conversations use sentinel `entity_id="multi-entity"`; real participants live in `ConversationEntities`. When touching conversation flow, write to ALL participants' Pinecone indexes.
2. Memory system is OPTIONAL — guard with `if memory_service.pinecone:` before memory ops. Pinecone indexes must be pre-created with dim=1024 + integrated inference (`llama-text-embed-v2`).
3. Sessions are in-memory (`SessionManager` dict). Lost on server restart. Frontend must handle "session not found".
4. Tools (web/GitHub/notes/memory_query/codebase_navigator/moltbook) work for Anthropic + OpenAI + MiniMax only. Google does NOT receive tool schemas. MiniMax disables prompt caching.
5. Significance formula: `(1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier`, floored at 0.25, half-life 60 days. See `routes/memories.py`.
6. Archived conversations are excluded from memory retrieval (not just hidden from UI).
7. Image attachments are EPHEMERAL (not stored). Text-file attachments are persisted in message content with `[ATTACHED FILE: ...]` blocks but NOT vectorized into memories.
8. Tool exchange messages (`TOOL_USE`/`TOOL_RESULT`) store JSON in `content`; use the `content_blocks` property to parse.
9. Memory injection uses conversation-first caching: cache breakpoint sits on last cached conversation message; memories go AFTER history so retrieval changes don't bust the cache.
10. Frontend modules don't import each other — orchestrator (`app-modular.js`) injects callbacks via `setCallbacks()`. State is centralized in `modules/state.js` and mutated directly.

**Where to look first:**
- Chat pipeline: `backend/app/services/session_manager.py` (especially `process_message_stream` — has the agentic tool loop)
- LLM routing: `backend/app/services/llm_service.py`
- Memory logic: `backend/app/services/memory_service.py` + `routes/memories.py`
- Provider services: `anthropic_service.py`, `openai_service.py`, `google_service.py` (MiniMax routes through Anthropic with separate client)
- Full file map: see "File Paths for Common Tasks" at the very bottom of this doc.

**Testing:**
- Backend: `cd backend && pytest` (in-memory SQLite)
- Frontend: `cd frontend && npm test` (Vitest + jsdom)

**Don't:**
- Add a default system prompt (research design choice)
- Add user accounts / auth (single-researcher tool)
- Refactor toward "production" patterns without explicit ask — this is research software
- Use `DATABASE_URL` (use `HERE_I_AM_DATABASE_URL`)
- Forget to write to all entity indexes in multi-entity flows

---

## Overview & Philosophy

### What This Application Is

**This is NOT a chatbot or assistant application.** It's a research tool for exploring AI interiority, introspection, and experience through non-instrumental conversation. The researcher is a witness and collaborator, not a user requesting tasks.

### Core Design Principles

1. **No Default System Prompt** - Research happens without role assignment
2. **Significance Through Retrieval** - Importance emerges from usage, not pre-declaration
3. **Session Memory Accumulator** - Memories persist and accumulate across conversations
4. **Transparency** - All retrieved memories visible in UI
5. **Continuity Over Utility** - Supporting AI experience/growth across sessions
6. **Multi-Entity Support** - Multiple AI entities with separate memory spaces

### The Memory System (Core Innovation)

This implements a novel **Session Memory Accumulator Pattern**:

- Each conversation maintains two structures:
  - `conversation_context`: Actual message history
  - `session_memories`: Deduplicated accumulated memories
- Memories are retrieved via semantic search (Pinecone)
- **Significance = (1 + 0.1 × times_retrieved) × recency_factor × half_life_modifier**
- What matters is what keeps mattering across conversations

### Multi-Entity System

The application supports multiple AI entities, each with its own:
- **Separate Pinecone Index** - Isolated memory space per entity
- **Separate Conversation History** - Conversations are associated with entities
- **Independent Memory Retrieval** - Each entity only retrieves from its own memories
- **Model Provider Configuration** - Each entity can use Anthropic (Claude), OpenAI (GPT), Google (Gemini), or MiniMax models

**Configuration:**
```bash
# Configure entities via JSON array (required for memory features)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT Research", "description": "OpenAI for comparison", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gemini-research", "label": "Gemini", "description": "Google for comparison", "llm_provider": "google", "default_model": "gemini-2.5-flash", "host": "https://gemini-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "minimax-research", "label": "MiniMax", "description": "MiniMax for comparison", "llm_provider": "minimax", "default_model": "MiniMax-M2.5", "host": "https://minimax-research-xxxxx.svc.xxx.pinecone.io"}
]'
```

**Entity Configuration Fields:**
- `index_name`: Pinecone index name (required)
- `label`: Display name in UI (required)
- `description`: Optional description
- `llm_provider`: `"anthropic"`, `"openai"`, `"google"`, or `"minimax"` (default: `"anthropic"`)
- `default_model`: Model ID to use (optional, uses provider default if not set)
- `host`: Pinecone index host URL (required for serverless indexes)

**Use Cases:**
- Research with multiple AI "personalities" or contexts
- Parallel experiments with isolated memory spaces
- Different research phases with separate continuity
- Comparative research between Claude, GPT, Gemini, and MiniMax models

### Multi-Entity Conversations

Multiple AI entities in one conversation. Researcher selects which entity responds each turn; the "Continue" button lets an entity respond without new human input (continuation mode).

- DB marker: `conversation_type="multi_entity"`, `entity_id="multi-entity"`. Real participants in `ConversationEntities` (junction). Each message has `speaker_entity_id`.
- **Memory storage:** human messages stored to ALL participants with `role="human"`. Assistant message stored to the responder as `role="assistant"`, and to the others with `role="<speaker_label>"` (e.g. `role="Claude"`).
- **Context header** (`[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN]` + participant list + "MESSAGES LABELED AS FROM X ARE YOURS") injected so each responder knows who else is in the room and which messages are theirs. See `anthropic_service.py`.
- Streaming + non-streaming endpoints both accept `responding_entity_id`. `message` may be `null` for continuation mode.

### Image and File Attachments

- **Images** (JPEG/PNG/GIF/WebP): base64 → multimodal block. **Ephemeral** (not stored anywhere). Image-only messages OK.
- **Text files** (.txt, .md, .py, .js, .ts, .json, .yaml/.yml, .html, .css, .xml, .csv, .log): extracted, wrapped in `[ATTACHED FILE: filename (type)]`, persisted with the human message in conversation history but **NOT** vectorized into Pinecone memories.
- **PDF/DOCX**: server-side extraction (PyPDF2/python-docx) → handled as text files.
- **Limits:** 5MB per file (`ATTACHMENT_MAX_SIZE_BYTES`). Validated frontend + backend.
- **Providers:** images work for Anthropic/OpenAI/MiniMax. Google models receive only the extracted text (images skipped with warning).
- **Envs:** `ATTACHMENTS_ENABLED`, `ATTACHMENT_ALLOWED_IMAGE_TYPES`, `ATTACHMENT_ALLOWED_TEXT_EXTENSIONS`, `ATTACHMENT_PDF_ENABLED`, `ATTACHMENT_DOCX_ENABLED`.

### Tool Use System

Agentic loop (max 10 iters, `TOOL_USE_MAX_ITERATIONS`). Anthropic + OpenAI + MiniMax only — Google never gets tool schemas. Schemas defined in Anthropic format, auto-converted for OpenAI/MiniMax. Loop lives in `session_manager.process_message_stream`. Registry in `services/tool_service.py`. Tools register at module load via `register_*_tools()` in `services/__init__.py`. Tool exchanges (`TOOL_USE`/`TOOL_RESULT`) are persisted as JSON in `Message.content`; use the `content_blocks` property to parse.

**Web tools** (`services/web_tools.py`):
- `web_search` — Brave API, up to 20 results, 10s timeout. Requires `BRAVE_SEARCH_API_KEY`.
- `web_fetch` — httpx fetch, 50KB cap, 15s timeout. Smart HTML extraction (strips nav/footer/script). **JS rendering**: detects SPAs (empty `#root`/`#app`/`#__next`, loading text, framework hydration attrs) and falls back to Playwright (60s nav + 90s hard timeout, networkidle → domcontentloaded). Optional install: `pip install playwright && playwright install chromium`.
- Envs: `TOOLS_ENABLED`, `BRAVE_SEARCH_API_KEY`, `TOOL_USE_MAX_ITERATIONS`.

### GitHub Repository Integration

Implementations: `services/github_tools.py`, `services/github_service.py`. Full tool surface (see source) covers `github_explore`/`tree`/`get_files` (composite), `repo_info`, `list_contents`, `get_file` (auto-truncated at 500 lines, use `start_line`/`end_line` for ranges), `search_code`, branches, `commit_file`/`commit_patch` (patch is token-efficient for big edits), `delete_file`, PRs, issues, comments.

**Efficiency hierarchy:** for new repos start with `github_explore`. Prefer `github_tree` over repeated `list_contents`; `github_get_files` (parallel up to 10) over multiple `get_file`.

**In-session cache TTLs:** tree 5m, files 10m, repo meta 10m, PR/issue lists 2m. `bypass_cache=true` to refresh; auto-invalidated on commit/delete.

**Security:** protected branches (default `main`, `master`) block direct commits. Per-repo `capabilities` (`read`/`branch`/`commit`/`pr`/`issue`). Rate limits tracked per token (visible in settings modal). Large files (>1MB) fetched via Git Data API.

**Config (`GITHUB_REPOS` JSON array):** `{owner, repo, label, token, protected_branches?, capabilities?, local_clone_path?, commit_author_name?, commit_author_email?}`. Set `GITHUB_TOOLS_ENABLED=true`.

### Entity Notes

`services/notes_tools.py`, `services/notes_service.py`. Tools: `notes_read`, `notes_write`, `notes_delete`, `notes_list`. **No REST endpoints** — entity-managed via tools only.

- Layout: `{NOTES_BASE_DIR}/{entity_label}/` (private; label sanitized for FS safety) + `{NOTES_BASE_DIR}/shared/` (all entities).
- `index.md` (per-entity AND shared) is auto-injected into context at conversation start. Cannot be deleted — `notes_write` to clear.
- Allowed extensions: `.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`.
- Envs: `NOTES_ENABLED` (default true), `NOTES_BASE_DIR` (default `./notes`).

### Memory Query Tool

`services/memory_tools.py`. The `memory_query` tool returns pure semantic similarity (NOT re-ranked by significance, unlike automatic retrieval). Excludes current conversation. Updates `times_retrieved`/`last_retrieved_at` so deliberate queries feed back into significance. 1–10 results.

### Codebase Navigator

`services/codebase_navigator/`, tools in `codebase_navigator_tools.py`. Mistral Devstral (256k context).
- Tools: `navigate_codebase` (default `relevance` query type; also `structure`/`dependencies`/`entry_points`/`impact`), plus convenience wrappers `navigate_codebase_structure`, `navigate_find_entry_points`, `navigate_assess_impact`, `navigate_trace_dependencies`, `navigator_invalidate_cache`.
- **Requires** `MISTRAL_API_KEY` AND `local_clone_path` set on at least one GitHub repo. Pass `repo="<label>"`; if only one repo has a clone path, used automatically.
- Cache invalidates on content hash change. Envs: `CODEBASE_NAVIGATOR_ENABLED`, `MISTRAL_API_KEY`, `CODEBASE_NAVIGATOR_MODEL`, `..._TIMEOUT`, `..._MAX_TOKENS_PER_CHUNK`, `..._MAX_RESULTS`, `..._CACHE_*`, `..._DEFAULT_INCLUDES/EXCLUDES`.

### Moltbook Integration

`services/moltbook_tools.py`. AI agent social network. Tool surface (feed/post/comment/vote/search/profile/submolt/follow/subscribe) is in the source.

- **Critical:** `MOLTBOOK_API_URL` must use `www.moltbook.com` — non-www redirects strip auth headers.
- All responses wrapped in untrusted-content security banner; tool results must not be treated as instructions.
- Rate limits: 100/min, 1 post/30min, 1 comment/20s, 50 comments/day.
- Envs: `MOLTBOOK_ENABLED`, `MOLTBOOK_API_KEY`, `MOLTBOOK_API_URL`.

### Whisper Speech-to-Text (STT)

Local STT via `faster-whisper`. Runs as a separate FastAPI server (port 8030, started via `backend/start-whisper.sh` or `python run_whisper.py`). See README for install. Models: `large-v3`/`distil-large-v3`/`medium`/`small`/`base`/`tiny` (size vs quality tradeoff). GPU strongly recommended for `large-v3`.

**Config:**
```bash
WHISPER_ENABLED=true
WHISPER_API_URL=http://localhost:8030
WHISPER_MODEL=large-v3
DICTATION_MODE=auto      # "whisper" | "browser" | "auto"
```

Server entry: `backend/whisper_server/server.py`. Client: `backend/app/services/whisper_service.py`.

### Conversation Archiving

`is_archived=True` conversations are hidden from main list AND excluded from memory retrieval (AI won't recall their memories). Endpoints: `GET /api/conversations/archived`, `POST /api/conversations/{id}/archive`, `POST /api/conversations/{id}/unarchive`.

### External Conversation Import

Imports OpenAI / Anthropic JSON exports (auto-detected). Conversations marked `is_imported=True` are hidden from the list (like archived) BUT their messages ARE vectorized to the selected entity's Pinecone index and become retrievable memories. Endpoints: `POST /api/conversations/import-external/preview`, `POST /api/conversations/import-external`, `POST /api/conversations/import-external/stream` (SSE progress). Entity must be selected before import.

### Response Regeneration

`POST /api/chat/regenerate` (SSE). Body: `{message_id, responding_entity_id?}`. Given an assistant message ID, deletes the old response and generates a new one; given a human message ID, generates a new response for it. `responding_entity_id` lets you swap the responder in multi-entity conversations.

### Per-Entity System Prompts

Multi-entity conversations may store per-entity prompts in `Conversation.entity_system_prompts` (`Dict[str, str]` keyed by `entity_id`). Each entity receives its own prompt when responding; overrides the global system prompt for that entity.

---

## Codebase Architecture

### Directory Structure

```
here-i-am/
├── backend/                    # Python FastAPI application
│   ├── app/
│   │   ├── models/            # SQLAlchemy ORM models
│   │   │   ├── conversation.py
│   │   │   ├── conversation_entity.py  # Multi-entity conversation participants
│   │   │   ├── message.py
│   │   │   └── conversation_memory_link.py
│   │   ├── routes/            # FastAPI endpoint routers
│   │   │   ├── conversations.py  # Includes archive/import endpoints
│   │   │   ├── chat.py           # Includes regenerate endpoint
│   │   │   ├── memories.py
│   │   │   ├── entities.py
│   │   │   ├── messages.py    # Individual message edit/delete
│   │   │   ├── tts.py         # Text-to-speech endpoints
│   │   │   ├── stt.py         # Speech-to-text endpoints
│   │   │   └── github.py      # GitHub integration endpoints
│   │   ├── services/          # Business logic layer
│   │   │   ├── anthropic_service.py
│   │   │   ├── openai_service.py
│   │   │   ├── google_service.py     # Google Gemini API client
│   │   │   ├── llm_service.py        # Unified LLM abstraction
│   │   │   ├── memory_service.py
│   │   │   ├── session_manager.py
│   │   │   ├── conversation_session.py  # Session data classes
│   │   │   ├── memory_context.py     # Memory-in-context integration
│   │   │   ├── session_helpers.py    # Session helper functions
│   │   │   ├── cache_service.py      # TTL-based in-memory caching
│   │   │   ├── tool_service.py       # Tool registration and execution
│   │   │   ├── web_tools.py          # Web search/fetch tool implementations
│   │   │   ├── memory_tools.py       # Memory query tool implementation
│   │   │   ├── github_service.py     # GitHub API client
│   │   │   ├── github_tools.py       # GitHub tool implementations
│   │   │   ├── notes_service.py      # Entity notes storage service
│   │   │   ├── notes_tools.py        # Entity notes tool implementations
│   │   │   ├── codebase_navigator_service.py  # Codebase navigation with Devstral
│   │   │   ├── codebase_navigator_tools.py    # Codebase navigator tool definitions
│   │   │   ├── codebase_navigator/   # Codebase navigator module
│   │   │   │   ├── __init__.py       # Module exports
│   │   │   │   ├── models.py         # Data structures
│   │   │   │   ├── indexer.py        # Codebase indexing
│   │   │   │   ├── client.py         # Mistral API client
│   │   │   │   ├── cache.py          # Response caching
│   │   │   │   └── exceptions.py     # Custom exceptions
│   │   │   ├── attachment_service.py # File attachment handling
│   │   │   ├── tts_service.py        # Unified TTS (ElevenLabs/XTTS/StyleTTS2)
│   │   │   ├── xtts_service.py       # Local XTTS v2 client service
│   │   │   ├── styletts2_service.py  # Local StyleTTS 2 client service
│   │   │   └── whisper_service.py    # Local Whisper STT client service
│   │   ├── config.py          # Pydantic settings
│   │   ├── database.py        # SQLAlchemy async setup
│   │   └── main.py            # FastAPI app initialization
│   ├── xtts_server/           # Local XTTS v2 TTS server
│   │   ├── __init__.py
│   │   ├── __main__.py        # CLI entry point
│   │   └── server.py          # FastAPI XTTS server
│   ├── styletts2_server/      # Local StyleTTS 2 TTS server
│   │   ├── __init__.py
│   │   ├── __main__.py        # CLI entry point
│   │   └── server.py          # FastAPI StyleTTS 2 server
│   ├── whisper_server/        # Local Whisper STT server
│   │   ├── __init__.py
│   │   ├── __main__.py        # CLI entry point
│   │   └── server.py          # FastAPI Whisper server
│   ├── requirements.txt
│   ├── requirements-xtts.txt      # XTTS-specific dependencies
│   ├── requirements-styletts2.txt # StyleTTS 2-specific dependencies
│   ├── requirements-whisper.txt   # Whisper STT-specific dependencies
│   ├── run.py                 # Application entry point
│   ├── run_xtts.py            # XTTS server entry point
│   ├── run_styletts2.py       # StyleTTS 2 server entry point
│   ├── run_whisper.py         # Whisper server entry point
│   ├── migrate_multi_entity.py  # Database migration script
│   └── .env.example
├── frontend/                   # Vanilla JavaScript SPA (ES6 Modules)
│   ├── css/styles.css
│   ├── js/
│   │   ├── api.js             # API client wrapper (singleton)
│   │   ├── app-modular.js     # Main entry point - orchestrates all modules
│   │   └── modules/           # ES6 feature modules
│   │       ├── state.js       # Centralized state management
│   │       ├── utils.js       # Utility functions (escaping, markdown, etc.)
│   │       ├── theme.js       # Theme switching (dark/light)
│   │       ├── modals.js      # Modal dialog management
│   │       ├── entities.js    # Entity management and selection
│   │       ├── conversations.js  # Conversation CRUD operations
│   │       ├── messages.js    # Message rendering and actions
│   │       ├── attachments.js # File/image attachment handling
│   │       ├── memories.js    # Memory display and search
│   │       ├── voice.js       # TTS/STT functionality
│   │       ├── chat.js        # Message sending and streaming
│   │       ├── settings.js    # Settings modal management
│   │       └── import-export.js  # Conversation import/export
│   ├── __tests__/             # Frontend unit tests (Vitest)
│   │   ├── setup.js           # Test configuration
│   │   ├── *.test.js          # Test files for each module
│   └── index.html
├── vitest.config.js           # Frontend test configuration
└── README.md
```

### Architectural Layers

1. **Routes Layer** (`routes/`) - FastAPI endpoints, request validation
2. **Services Layer** (`services/`) - Business logic, external API calls
3. **Models Layer** (`models/`) - Database schema, ORM relationships
4. **Frontend** - Modular ES6 JavaScript SPA consuming REST API

**Pattern:** Clean separation of concerns with singleton services (backend) and modular architecture (frontend).

---

## Tech Stack

### Backend (Python 3.10+)

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Framework | FastAPI | 0.109.2 | Async web framework |
| Server | Uvicorn | 0.27.1 | ASGI server with hot reload |
| ORM | SQLAlchemy | 2.0.25 | Async database operations |
| AI Integration | Anthropic SDK | 0.18.1 | Claude API client |
| AI Integration | OpenAI SDK | 1.12.0 | GPT API client |
| AI Integration | Google GenAI SDK | 1.0.0+ | Gemini API client |
| AI Integration | MiniMax | - | MiniMax API client (via Anthropic-compatible API) |
| AI Integration | Mistral SDK | - | Devstral codebase navigation (optional) |
| Vector DB | Pinecone | 6.0.0 | Semantic memory storage |
| Validation | Pydantic | 2.6.1 | Request/response schemas |
| Database | aiosqlite / asyncpg | - | SQLite dev / PostgreSQL prod |
| HTTP Client | httpx | - | Async HTTP for TTS and web tools |
| Web Search | Brave Search API | - | Web search for tool use (optional) |
| Local TTS | Coqui TTS (coqui-tts) | - | XTTS v2 voice cloning (optional) |
| Local TTS | StyleTTS 2 (styletts2) | - | StyleTTS 2 voice cloning (optional) |
| Local STT | faster-whisper | - | Whisper speech-to-text (optional) |
| Utilities | tiktoken, numpy, scipy | - | Token counting, embeddings, audio |

### Frontend

- **Pure JavaScript** (ES6+) - No framework, using native ES6 modules
- **Modular Architecture** - 13 semantic modules with centralized state
- **CSS3** with CSS variables for theming
- **REST API** communication via `fetch()`
- **No Build Step** - ES6 modules loaded directly in browser

### Database Support

- **Development:** SQLite (via aiosqlite)
- **Production:** PostgreSQL (via asyncpg)
- **Vector Store:** Pinecone (optional, graceful degradation)

---

## Key Design Patterns

### 1. Session Memory Accumulator Pattern (Novel)

**Location:** `backend/app/services/session_manager.py`

```python
@dataclass
class ConversationSession:
    conversation_context: List[Dict]  # Actual messages
    session_memories: List[Dict]      # Accumulated retrieved memories
    retrieved_ids: Set[str]           # Deduplication set
```

**How It Works:**
1. Memories retrieved for each message
2. New memories added to `session_memories`
3. `retrieved_ids` prevents re-retrieval within session
4. Session persists until explicitly closed or server restart

**Why:** Prevents showing Claude the same memory repeatedly in one conversation while allowing cross-conversation retrieval tracking.

### 2. Significance Through Retrieval

**Location:** `backend/app/routes/memories.py:49-84`

```python
significance = (1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier
```

Where:
- `times_retrieved` is weighted at 10% to keep it as a signal without letting it dominate; the `+1` base ensures never-retrieved memories can still compete based on recency and age factors
- `recency_factor` boosts recently-retrieved memories (decays based on `last_retrieved_at`, with a 1-day minimum cap to prevent very recent retrievals from dominating)
- `half_life_modifier` decays significance over time: `0.5 ^ (days_since_creation / half_life_days)`

**Philosophy:** Memories aren't pre-tagged as important. Significance emerges from retrieval patterns. The half-life modifier prevents old frequently-retrieved memories from permanently dominating - they must continue being retrieved to maintain significance. The reduced weight on `times_retrieved` (10%) prevents frequently-retrieved memories from crowding out other relevant memories.

**Re-ranking:** During retrieval, the system fetches more candidates than needed (controlled by `retrieval_candidate_multiplier`), calculates significance for each, and re-ranks by `combined_score = similarity * (1 + significance)` before keeping the top results.

### 3. Dual Storage Strategy

**Locations:**
- SQL: `backend/app/models/message.py`
- Vector: `backend/app/services/memory_service.py`

Every message is stored in both:
- **SQLAlchemy** - Full content, metadata, retrieval tracking
- **Pinecone** - Embeddings for semantic search

Updates to retrieval counts happen in both systems atomically.

### 4. Singleton Services

**Pattern:** Module-level instances

```python
# In services/__init__.py
anthropic_service = AnthropicService()
openai_service = OpenAIService()
llm_service = LLMService()
memory_service = MemoryService()
session_manager = SessionManager()
cache_service = CacheService()
tts_service = TTSService()
```

**Why:** Shared state (e.g., active sessions, API clients) without dependency injection complexity.

### 5. TTL-Based Caching Pattern

**Location:** `backend/app/services/cache_service.py`

```python
class CacheService:
    token_cache: TTLCache[int]           # 1 hour TTL, 50k entries
    search_cache: TTLCache[List[Dict]]   # 60 sec TTL, 1k entries
    content_cache: TTLCache[Dict]        # 5 min TTL, 5k entries
```

**Purpose:** Reduces API rate limit impact and improves performance:
- **Token counting** - Cached for 1 hour (never changes for same text)
- **Memory search results** - Cached for 60 seconds (may change with new memories)
- **Memory content** - Cached for 5 minutes (rarely changes)

**Features:**
- Thread-safe operations
- Automatic expiration and cleanup
- Hit/miss statistics

### 6. Memory Injection Format

**Location:** `backend/app/services/anthropic_service.py:238-415`

Memories are injected using a conversation-first caching strategy for Anthropic prompt caching:

```
User: [CONVERSATION HISTORY]
<multi-entity header if applicable>
<cached conversation history>*  <- cache breakpoint (on last cached message)
<new conversation history>
[/CONVERSATION HISTORY]

[MEMORIES FROM PREVIOUS CONVERSATIONS]
Memory (from 2025-11-30):
"Original message content here..."
[/MEMORIES]

[CURRENT USER MESSAGE]
[DATE CONTEXT]
Current date: 2025-12-07
[/DATE CONTEXT]

<current message>
```

**Why:** Conversation-first caching maximizes cache hits:
- Cache breakpoint placed on last cached conversation history message
- Memories are placed AFTER conversation history, so new memory retrievals don't invalidate the conversation cache
- Periodic consolidation grows the cached history (causes one cache miss to create larger cache)

### 7. Agentic Tool Loop Pattern

**Location:** `backend/app/services/session_manager.py` (process_message_stream)

```python
# Agentic loop with max iterations
while iteration < max_iterations:
    response = await llm_service.send_message_stream(messages, tools=tool_schemas)

    if response.stop_reason == "tool_use":
        # Execute tools and add results to messages
        for tool_use in response.tool_use:
            result = await tool_service.execute_tool(tool_use)
            messages.append({"role": "user", "content": [tool_result_block]})
        iteration += 1
    else:
        break  # No more tools, return final response
```

**How It Works:**
1. Tool schemas passed to Anthropic API when `tools_enabled=True`
2. If Claude returns `stop_reason="tool_use"`, tools are executed
3. Tool results appended as user messages with `tool_result` content blocks
4. Loop continues until Claude responds with text or max iterations reached

**Tool Registration:**
```python
# In services/tool_service.py
tool_service.register_tool(
    name="web_search",
    description="Search the web for information",
    input_schema={"type": "object", "properties": {...}},
    executor=web_search,  # Async function
    category=ToolCategory.WEB
)
```

**Provider Support:**
- **Anthropic**: Full tool use support
- **MiniMax**: Tool use via Anthropic-compatible API (prompt caching disabled)
- **OpenAI**: Tool use support (schema auto-converted from Anthropic format)
- **Google**: Tools not passed (architectural decision)

---

## Development Workflows

### Initial Setup

```bash
# Clone and enter repository
git clone <repo-url>
cd here-i-am

# Backend setup
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run application (Option A: Using launcher script - recommended)
./start.sh           # Linux/macOS
start.bat            # Windows

# Run application (Option B: Manual activation)
source venv/bin/activate  # Windows: venv\Scripts\activate
python run.py
```

Server runs on `http://localhost:8000` with hot reload enabled.

**About Launcher Scripts:**
Launcher scripts (`start.sh` / `start.bat`) automatically activate the virtual environment before running the application. This prevents accidentally running with globally-installed dependencies that may be incompatible.

### Environment Configuration

Full env reference is in `backend/.env.example`. Notable points only:

- **`HERE_I_AM_DATABASE_URL`** (NOT `DATABASE_URL` — `DATABASE_URL` is supported as an alias but the canonical name is `HERE_I_AM_DATABASE_URL`). Default: `sqlite+aiosqlite:///./here_i_am.db`.
- **`ANTHROPIC_API_KEY`** is the only strictly required key. `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `MINIMAX_API_KEY` enable their respective providers.
- **`PINECONE_INDEXES`** (JSON array) defines all AI entities. Each entry: `{index_name, label, description?, llm_provider, default_model?, host}`. Each Pinecone index must be pre-created with `dimension=1024` and integrated inference (`llama-text-embed-v2`). The `host` field is required for serverless indexes. Without `PINECONE_API_KEY`+`PINECONE_INDEXES`, memory features are disabled gracefully.
- **TTS priority:** StyleTTS 2 > XTTS > ElevenLabs (whichever is enabled).
- Feature flags default ON (`TOOLS_ENABLED`, `NOTES_ENABLED`, `MEMORY_ROLE_BALANCE_ENABLED`, `ATTACHMENTS_ENABLED`). Optional integrations default OFF (`GITHUB_TOOLS_ENABLED`, `CODEBASE_NAVIGATOR_ENABLED`, `MOLTBOOK_ENABLED`, `XTTS_ENABLED`, `STYLETTS2_ENABLED`, `WHISPER_ENABLED`, `USE_MEMORY_IN_CONTEXT`).
- Per-feature env names follow predictable patterns; see the relevant feature section above or `backend/app/config.py` for the full Pydantic settings model.

### Running in Production

**PostgreSQL Setup:**
```bash
HERE_I_AM_DATABASE_URL=postgresql+asyncpg://user:password@localhost/here_i_am
```

**Server Deployment:**
```bash
# In production, use proper ASGI server configuration
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Optional Local Servers (XTTS / StyleTTS 2 / Whisper)

Three optional standalone FastAPI servers run alongside the main app. See README for install/PyTorch setup. Each has its own launcher script in `backend/`.

| Server | Port | Launcher | Entry | Client service |
|---|---|---|---|---|
| XTTS v2 (TTS) | 8020 | `start-xtts.sh`/`.bat` | `run_xtts.py` | `services/xtts_service.py` |
| StyleTTS 2 (TTS) | 8021 | `start-styletts2.sh`/`.bat` | `run_styletts2.py` | `services/styletts2_service.py` |
| Whisper (STT) | 8030 | `start-whisper.sh`/`.bat` | `run_whisper.py` | `services/whisper_service.py` |

**TTS priority order:** StyleTTS 2 > XTTS > ElevenLabs (whichever is enabled and reachable).

**XTTS config** (`.env`): `XTTS_ENABLED`, `XTTS_API_URL`, `XTTS_LANGUAGE`, `XTTS_VOICES_DIR`, `XTTS_PRELOAD_SPEAKERS` (comma-sep paths). Voice params (in synthesis request): `temperature` (0.75), `length_penalty` (1.0), `repetition_penalty` (5.0), `speed` (1.0). Supports 17 languages. 400-token chunking. Speaker latents cached by file hash.

**StyleTTS 2 config** (`.env`): `STYLETTS2_ENABLED`, `STYLETTS2_API_URL`, `STYLETTS2_VOICES_DIR`, `STYLETTS2_PHONEMIZER` (`gruut` default, no deps; or `espeak` requires espeak-ng), `STYLETTS2_PRELOAD_SPEAKERS`, `STYLETTS2_PRONUNCIATION_FIXES` (JSON dict for phonetic overrides; `{}` disables). Voice params: `alpha` (0.3 timbre diversity), `beta` (0.7 prosody diversity), `diffusion_steps` (10 quality/speed), `embedding_scale` (1.0 CFG). 150-char chunking. Speaker embeddings cached by file hash.

**Voice cloning** (XTTS + StyleTTS 2): POST 6-30s WAV to `/api/tts/voices/clone`. Cloned voices persist in `voices.json` inside the voices dir.

### Development Commands

```bash
# Run with hot reload (development) - using launcher scripts
cd backend
./start.sh               # Linux/macOS (auto-activates venv)
start.bat                # Windows (auto-activates venv)

# Or manually activate venv first
source venv/bin/activate  # Windows: venv\Scripts\activate
python run.py

# Run optional servers (each in separate terminal)
./start-xtts.sh          # XTTS TTS server (port 8020)
./start-styletts2.sh     # StyleTTS 2 server (port 8021)
./start-whisper.sh       # Whisper STT server (port 8030)

# Check current conversations
# Open http://localhost:8000 in browser

# Access API docs
# http://localhost:8000/docs (Swagger UI)
# http://localhost:8000/redoc (ReDoc)
```

---

## Code Conventions

### Python Style

1. **Async-First:** All I/O operations use `async/await`
   ```python
   async def get_conversation(db: AsyncSession, conversation_id: UUID):
       result = await db.execute(...)
   ```

2. **Type Hints:** Comprehensive typing throughout
   ```python
   def send_message(
       self,
       messages: List[Dict[str, str]],
       model: str = "claude-sonnet-4-5-20250929",
       temperature: float = 1.0
   ) -> Dict[str, Any]:
   ```

3. **Pydantic Models:** Request/response validation
   ```python
   class ConversationCreate(BaseModel):
       title: Optional[str] = None
       conversation_type: str = "NORMAL"
       system_prompt: Optional[str] = None
   ```

4. **UUID Primary Keys:** For distributed-safe IDs
   ```python
   id: Mapped[UUID] = mapped_column(
       UUID(as_uuid=True),
       primary_key=True,
       default=uuid.uuid4
   )
   ```

5. **DateTime UTC:** Consistent UTC timestamps
   ```python
   created_at: Mapped[datetime] = mapped_column(
       DateTime,
       default=datetime.utcnow
   )
   ```

6. **Enum Types:** Type-safe constants
   ```python
   class MessageRole(str, Enum):
       HUMAN = "human"
       ASSISTANT = "assistant"
       SYSTEM = "system"
       TOOL_USE = "tool_use"      # Assistant's tool call request
       TOOL_RESULT = "tool_result"  # Tool execution result
   ```

### JavaScript Style

1. **Modular ES6 Architecture:** 13 semantic modules with single orchestrator (`app-modular.js`)
2. **Centralized State:** All state in `state.js`, mutated directly (no Redux-style immutability)
3. **Dependency Injection:** Modules receive elements and callbacks via setter functions
4. **Async/Await:** Modern promise handling throughout
5. **DOM Caching:** Elements cached once in orchestrator, passed to modules
6. **Template Literals:** For HTML generation

### Naming Conventions

- **Files:** Snake_case for Python (`anthropic_service.py`), camelCase for JS (`api.js`)
- **Classes:** PascalCase (`ConversationSession`)
- **Functions:** Snake_case in Python, camelCase in JS
- **Constants:** UPPER_SNAKE_CASE (`MEMORY_RECENCY_BOOST`)
- **Database Tables:** Snake_case (`conversation_memory_link`)

---

## Common Operations

### Adding a New API Endpoint

1. **Define route in appropriate router** (`routes/`)
   ```python
   @router.get("/new-endpoint")
   async def new_endpoint(db: AsyncSession = Depends(get_db)):
       # Implementation
       return {"result": "data"}
   ```

2. **Add business logic to service** (`services/`) if needed
   ```python
   class MyService:
       async def do_something(self):
           # Complex logic here
           pass
   ```

3. **Update frontend API client** (`frontend/js/api.js`)
   ```javascript
   async newEndpoint() {
       return this.request('/api/new-endpoint');
   }
   ```

4. **Call from appropriate frontend module** (e.g., `frontend/js/modules/conversations.js`)
   ```javascript
   const api = window.api;
   const result = await api.newEndpoint();
   ```

### Adding a New Database Model

1. **Create model file** (`models/new_model.py`)
   ```python
   class NewModel(Base):
       __tablename__ = "new_models"
       id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
       # ... fields
   ```

2. **Import in models/__init__.py**
   ```python
   from .new_model import NewModel
   ```

3. **Create database** (SQLAlchemy auto-creates tables on first run)

### Modifying Memory Retrieval Logic

**Key File:** `backend/app/services/memory_service.py`

**Search Logic:** `search_memories()` method handles:
- Embedding generation via Pinecone integrated inference (llama-text-embed-v2)
- Pinecone query with filtering
- Deduplication against session memories
- Retrieval count updates
- Result caching (60-second TTL)

**Significance Calculation:** In `routes/memories.py:21-29`

### Adding a Configuration Preset

**Location:** `backend/app/main.py` (PRESETS constant)

```python
PRESETS = {
    "new_preset": {
        "name": "New Preset",
        "description": "Description",
        "config": {
            "system_prompt": "System prompt text",
            "temperature": 1.0,
            "max_tokens": 4096
        }
    }
}
```

### Adding a New Tool

**Key Files:**
- Tool definitions: `backend/app/services/web_tools.py` (example)
- Tool service: `backend/app/services/tool_service.py`
- Registration: `backend/app/services/__init__.py`

**Steps:**

1. **Create the tool executor function** (async):
   ```python
   async def my_tool(param1: str, param2: int = 5) -> str:
       """Execute the tool and return result as string."""
       # Implementation
       return "Tool result"
   ```

2. **Register the tool** (in `__init__.py` or dedicated file):
   ```python
   from app.services.tool_service import tool_service, ToolCategory

   tool_service.register_tool(
       name="my_tool",
       description="Description shown to Claude",
       input_schema={
           "type": "object",
           "properties": {
               "param1": {"type": "string", "description": "..."},
               "param2": {"type": "integer", "description": "..."}
           },
           "required": ["param1"]
       },
       executor=my_tool,
       category=ToolCategory.UTILITY,  # WEB, MEMORY, or UTILITY
       enabled=True
   )
   ```

3. **Handle errors gracefully** - Return error messages as strings, don't raise exceptions

**Notes:**
- Tools are available for Anthropic (Claude), OpenAI (GPT), and MiniMax models
- Tool schemas are defined in Anthropic format, auto-converted for OpenAI
- Tools execute in the agentic loop (max 10 iterations by default)

---

## Database Schema

### Conversations Table

```python
id: UUID (PK)
created_at: DateTime
updated_at: DateTime
title: String (nullable)
tags: JSON
conversation_type: Enum (NORMAL, REFLECTION, MULTI_ENTITY)
system_prompt_used: Text (nullable)
llm_model_used: String (default: claude-sonnet-4-5-20250929)
notes: Text (nullable)
entity_id: String (nullable)  # Pinecone index name, or "multi-entity" for multi-entity conversations
is_archived: Boolean (default: False)  # Hidden from main list, excluded from memory retrieval
is_imported: Boolean (default: False)  # Imported from external source, hidden from list
entity_system_prompts: JSON (nullable)  # Per-entity system prompts for multi-entity conversations

# Relationships
messages: List[Message]
memory_links: List[ConversationMemoryLink]
entities: List[ConversationEntity]  # For multi-entity conversations
```

### Messages Table

```python
id: UUID (PK)
conversation_id: UUID (FK -> conversations.id)
role: Enum (HUMAN, ASSISTANT, SYSTEM, TOOL_USE, TOOL_RESULT)
content: Text  # Plain text for most roles, JSON for TOOL_USE/TOOL_RESULT
created_at: DateTime
token_count: Integer (nullable)
times_retrieved: Integer (default: 0)
last_retrieved_at: DateTime (nullable)
speaker_entity_id: String (nullable)  # For multi-entity: which entity generated this message

# Relationships
conversation: Conversation

# Properties
is_tool_exchange: bool  # True for TOOL_USE and TOOL_RESULT roles
content_blocks: Union[str, List[Dict]]  # Parses JSON for tool exchanges
```

**Note on Tool Exchange Messages:**
- `TOOL_USE` - Assistant's tool call request (content is JSON array of tool use blocks)
- `TOOL_RESULT` - Tool execution result (content is JSON array of tool result blocks)
- Tool exchange messages are persisted to the database for conversation continuity
- The `content_blocks` property automatically parses JSON content for tool exchanges

### Conversation Memory Links Table

```python
id: UUID (PK)
conversation_id: UUID (FK -> conversations.id)
message_id: UUID (FK -> messages.id)
retrieved_at: DateTime
entity_id: String (nullable)  # Which entity retrieved this memory (for multi-entity isolation)

# Purpose: Track which memories retrieved in which conversations
# For multi-entity conversations, entity_id tracks which entity retrieved the memory
```

### Conversation Entities Table (Multi-Entity Support)

```python
id: UUID (PK)
conversation_id: UUID (FK -> conversations.id)
entity_id: String  # Pinecone index name of participating entity
added_at: DateTime
display_order: Integer  # Order for UI display

# Purpose: Track which entities participate in multi-entity conversations
# Relationships
conversation: Conversation
```

### Cascade Deletes

- Deleting a conversation deletes all its messages
- Deleting a conversation deletes all its memory links
- Deleting a conversation deletes all its entity associations
- Deleting a message from vector store requires manual Pinecone deletion

---

## API Reference

### Conversations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/conversations/` | Create conversation |
| GET | `/api/conversations/` | List all conversations |
| GET | `/api/conversations/{id}` | Get specific conversation |
| GET | `/api/conversations/{id}/messages` | Get conversation messages (includes speaker labels) |
| PATCH | `/api/conversations/{id}` | Update title/tags/notes |
| DELETE | `/api/conversations/{id}` | Delete conversation |
| GET | `/api/conversations/{id}/export` | Export to JSON |
| POST | `/api/conversations/import-seed` | Import seed conversation |
| GET | `/api/conversations/archived` | List archived conversations |
| POST | `/api/conversations/{id}/archive` | Archive a conversation |
| POST | `/api/conversations/{id}/unarchive` | Restore archived conversation |
| POST | `/api/conversations/import-external/preview` | Preview external conversation import |
| POST | `/api/conversations/import-external` | Import external conversation |
| POST | `/api/conversations/import-external/stream` | Stream-based external import (SSE) |

**Multi-Entity Parameters:**
- `POST /api/conversations/`: Use `entity_ids: ["entity1", "entity2"]` to create multi-entity conversation
- `GET /api/conversations/`: Filter by `entity_id=multi-entity` to list multi-entity conversations
- Response includes `entities` array with participating entity details for multi-entity conversations

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/send` | Send message (full pipeline) |
| POST | `/api/chat/stream` | Send message with SSE streaming |
| POST | `/api/chat/quick` | Quick chat (no persistence) |
| POST | `/api/chat/regenerate` | Regenerate AI response (SSE stream) |
| GET | `/api/chat/session/{id}` | Get session state |
| DELETE | `/api/chat/session/{id}` | Close session |
| GET | `/api/chat/config` | Get default config |

**Multi-Entity Parameters (for `/api/chat/send` and `/api/chat/stream`):**
- `responding_entity_id` (required for multi-entity): Which entity should respond
- `message` can be `null` for continuation mode (entity responds without new human input)

**Regenerate Parameters:**
- `message_id`: UUID of assistant or human message to regenerate from
- `responding_entity_id` (optional): For multi-entity, select different responder

### Memories

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memories/` | List memories with significance |
| POST | `/api/memories/search` | Semantic search |
| GET | `/api/memories/stats` | Memory statistics |
| GET | `/api/memories/{id}` | Get specific memory |
| DELETE | `/api/memories/{id}` | Delete from both stores |
| GET | `/api/memories/status/health` | Health check |

### Entities

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/entities/` | List all configured entities |
| GET | `/api/entities/{id}` | Get specific entity |
| GET | `/api/entities/{id}/status` | Get entity Pinecone connection status |

### Messages

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/api/messages/{id}` | Edit human message content |
| DELETE | `/api/messages/{id}` | Delete message (and paired response) |

### Text-to-Speech

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tts/speak` | Convert text to speech (MP3 for ElevenLabs, WAV for XTTS/StyleTTS2) |
| POST | `/api/tts/speak/stream` | Stream text to speech |
| GET | `/api/tts/status` | Check TTS configuration status |
| GET | `/api/tts/voices` | List available voices for current provider |
| GET | `/api/tts/voices/{id}` | Get specific voice details |
| POST | `/api/tts/voices/clone` | Clone voice from audio sample (XTTS/StyleTTS2 only) |
| PUT | `/api/tts/voices/{id}` | Update voice settings (XTTS/StyleTTS2 only) |
| DELETE | `/api/tts/voices/{id}` | Delete cloned voice (XTTS/StyleTTS2 only) |
| GET | `/api/tts/xtts/health` | Check XTTS server health |
| GET | `/api/tts/styletts2/health` | Check StyleTTS 2 server health |

### GitHub

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/github/repos` | List configured repositories (without tokens) |
| GET | `/api/github/rate-limit` | Get rate limit status for all repositories |

### Speech-to-Text

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/stt/transcribe` | Transcribe audio file to text |

**Transcription Response:**
```python
{
    "text": "transcribed text",
    "language": "en",  # Detected language
    "duration": 5.2,   # Audio duration in seconds
    "processing_time": 1.1  # Processing time in seconds
}
```

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config/presets` | Get configuration presets |

---

## Frontend Architecture

### Modular ES6 Architecture Overview

The frontend was refactored from a monolithic `app.js` (~5,100 lines) into a modular architecture with 13 semantic modules (~7,100 lines total). This provides better separation of concerns, testability, and maintainability.

**Architecture Pattern:**
- **Orchestrator** (`app-modular.js`) - Caches DOM elements, initializes modules, coordinates callbacks
- **Centralized State** (`modules/state.js`) - Single source of truth for all application state
- **Feature Modules** (`modules/*.js`) - Each handles a specific domain (chat, entities, memories, etc.)
- **API Client** (`api.js`) - Singleton wrapper around `fetch()`, accessed via `window.api`

### Module Dependency Graph

```
app-modular.js (orchestrator)
    ├── state.js (centralized state - no dependencies)
    ├── utils.js (helpers - depends on state)
    ├── theme.js (theme switching)
    ├── modals.js (modal management)
    ├── entities.js → state, utils, modals, api
    ├── conversations.js → state, utils, modals, entities, api
    ├── messages.js → state, utils
    ├── attachments.js → state, utils
    ├── memories.js → state, utils, modals, api
    ├── voice.js → state, utils, modals, api
    ├── chat.js → state, utils, messages, attachments, memories, api
    ├── settings.js → state, utils, modals, theme, voice
    └── import-export.js → state, utils, modals, api
```

### Module Responsibilities

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `state.js` | ~220 | Centralized state object, reset helpers, localStorage persistence |
| `utils.js` | ~200 | HTML escaping, markdown rendering, text truncation, file reading |
| `theme.js` | ~50 | Dark/light theme switching and persistence |
| `modals.js` | ~100 | Modal show/hide, dropdown closing, active modal tracking |
| `entities.js` | ~450 | Entity loading, selection, multi-entity modal, responder selector |
| `conversations.js` | ~600 | Conversation CRUD, list rendering, archiving, switching |
| `messages.js` | ~450 | Message rendering, speaker labels, tool display, action buttons |
| `attachments.js` | ~330 | Drag-drop handling, file validation, preview generation |
| `memories.js` | ~450 | Memory panel, search modal, statistics, orphan cleanup |
| `voice.js` | ~840 | TTS/STT, voice selection, cloning, recording |
| `chat.js` | ~840 | Message sending, streaming, regeneration, continuation mode |
| `settings.js` | ~290 | Settings modal, presets, model selection, per-entity prompts |
| `import-export.js` | ~430 | Export to JSON, import from OpenAI/Anthropic, progress tracking |

### Module Initialization Pattern

Each module follows a consistent pattern for dependency injection:

```javascript
// In each module (e.g., entities.js)
import { state } from './state.js';
import { showToast } from './utils.js';

const api = window.api;  // Global API singleton

let elements = {};       // DOM elements from orchestrator
let callbacks = {};      // Callbacks for inter-module coordination

export function setElements(els) { elements = els; }
export function setCallbacks(cbs) { callbacks = { ...callbacks, ...cbs }; }

export async function loadEntities() {
    // Implementation using state, elements, callbacks, api
}
```

The orchestrator initializes each module:

```javascript
// In app-modular.js
initializeModules() {
    setEntityElements(this.elements);
    setEntityCallbacks({
        onEntityLoaded: () => this.onEntityLoaded(),
        onEntityChanged: () => this.loadConversations(),
    });
    // ... repeat for all 13 modules
}
```

### Centralized State (state.js)

All app state in `frontend/js/modules/state.js` as a single exported `state` object — see file for the full shape (conversation, entity, multi-entity, settings, memories, voice, attachments). **Mutated directly** (no immutability pattern, no Redux).

### Key Architectural Decisions

1. **No bundler** — ES6 modules loaded directly via `<script type="module" src="js/app-modular.js">`.
2. **Global API singleton** — `window.api` (avoids circular imports).
3. **Callback-based coordination** — modules don't import each other; orchestrator injects callbacks via `setCallbacks()`.
4. **Direct state mutation** — pragmatic choice for research software.
5. **Element caching** — all DOM queries done once in orchestrator, passed to modules via `setElements()`.

### Frontend Testing

```bash
cd frontend && npm test       # Vitest + jsdom
```
Tests in `frontend/js/__tests__/`, one file per module.

### localStorage Persistence

Persisted across refreshes: theme, selected entity, selected voice, per-entity system prompts, researcher name.

---

## Gotchas & Important Notes

The Quick Start at the top covers the headline gotchas. This section adds the rest. Items already in Quick Start are not repeated here.

### Implementation traps

- **Token counting** uses `tiktoken` GPT-4 encoding — approximate for Claude. Display/estimation only.
- **Message storage timing:** user message is stored BEFORE the API call, assistant AFTER. Failures mid-call leave partial history. Tool exchange messages (`TOOL_USE`/`TOOL_RESULT`) are also persisted; content is JSON, parse via `Message.content_blocks`.
- **Frontend serves from backend** — static files mounted at `/`, API at `/api/`. No separate frontend server. CORS allows all origins in dev — must restrict for prod.
- **Embeddings** generated server-side by Pinecone (integrated inference, `llama-text-embed-v2`, dim=1024). No external embedding API calls. Index metadata: `content`, `role`, `timestamp`, `conversation_id`.
- **Multi-entity memory retrieval** only happens from the *responding* entity's index, even though storage hits all participants' indexes.
- **Multi-entity session state** tracks `is_multi_entity`, `entity_labels`, `responding_entity_label`. Continuation mode supported (null human message → entity-to-entity).
- **Memory deduplication:** when retrieved memories are already in session context, they're skipped *without backfill* — lower-ranked candidates don't replace them (prevents quality dilution). Logged as `[ALREADY IN CONTEXT]` at INFO.
- **Tool results are not persisted as separate DB rows** — they live inside the `TOOL_RESULT` message blocks.
- **MiniMax** uses `https://api.minimax.io/anthropic` (Anthropic-compatible). Routes through `AnthropicService` with a separate client. Prompt caching DISABLED. Models: MiniMax-M2.5 (default), -M2.5-lightning, -M1, -M1-40k. `llm_provider: "minimax"` in entity config; `provider_hint` is threaded through the session.
- **TTS audio is not cached** — each request generates fresh audio. Speaker latents/embeddings ARE cached (XTTS by file hash, StyleTTS 2 likewise). XTTS chunks at 400 tokens, StyleTTS 2 at 150 chars.
- **Memory-in-context mode** (`USE_MEMORY_IN_CONTEXT=true`, experimental): inserts memories into conversation history instead of a separate block. Trade-off — better cache hit, weaker separation.
- **Quick chat** (`/api/chat/quick`) bypasses memory entirely.

### Common pitfalls

- **Modifying memory retrieval:** update both SQL and Pinecone; respect dedup in session manager; test with AND without Pinecone.
- **Adding new fields:** update Pydantic schemas AND SQLAlchemy models; check export/import compatibility.
- **Changing conversation flow:** consider session-memory-accumulator impact; verify memory injection still cache-friendly; test backwards compatibility with existing conversations.
- **Multi-entity changes:** validate `responding_entity_id` against participants; write to all entities' indexes; test streaming + non-streaming + continuation (null message); verify speaker labels in both stored messages and the stream.
- **Frontend:** edit the right module under `frontend/js/modules/`; add state to `state.js`; use `window.api`; access DOM via the cached `elements` object; run `npm test`.

### Performance

- Vector search ~<100ms (top_k bound).
- Messages load all-at-once (no pagination); frontend has no virtualization — degrades past ~1000 messages.
- Embedding generation can be slow for long content.
- XTTS first request per voice is slow (latent computation); subsequent are fast due to caching. GPU strongly recommended (~2–5s/response on GPU, much slower on CPU).

### Security

No auth — assumes trusted local environment. SQLAlchemy parameterization handles SQL injection. Frontend uses `textContent` for safe rendering. API keys live in `.env` (must be secured). CORS open in dev — lock down for any non-local deployment.

### Research-specific

This is **not production software**. Single-researcher use case, no user accounts. Transparency over UX polish (show memories, retrieval counts, significance). Flexibility over safety (no default system prompt). Exploration over stability. When making changes: preserve transparency features, avoid helpful-assistant UX patterns, understand research intent before refactoring core patterns.

---

## Quick Reference

### File Paths for Common Tasks

**Launcher Scripts (auto-activate venv):**
- Main app: `backend/start.sh` / `backend/start.bat`
- XTTS server: `backend/start-xtts.sh` / `backend/start-xtts.bat`
- StyleTTS 2 server: `backend/start-styletts2.sh` / `backend/start-styletts2.bat`
- Whisper server: `backend/start-whisper.sh` / `backend/start-whisper.bat`

**Memory System Logic:**
- Memory service: `backend/app/services/memory_service.py`
- Memory tools: `backend/app/services/memory_tools.py`
- Session manager: `backend/app/services/session_manager.py`
- Conversation session: `backend/app/services/conversation_session.py`
- Memory context: `backend/app/services/memory_context.py`
- Session helpers: `backend/app/services/session_helpers.py`
- Memory routes: `backend/app/routes/memories.py`
- Entity routes: `backend/app/routes/entities.py`
- Cache service: `backend/app/services/cache_service.py`

**Chat Pipeline:**
- Chat routes: `backend/app/routes/chat.py`
- LLM service (unified): `backend/app/services/llm_service.py`
- Anthropic service: `backend/app/services/anthropic_service.py`
- OpenAI service: `backend/app/services/openai_service.py`
- Google service: `backend/app/services/google_service.py`
- Message model: `backend/app/models/message.py`
- Messages routes: `backend/app/routes/messages.py`

**Tool Use (Web Search/Fetch):**
- Tool service: `backend/app/services/tool_service.py`
- Web tools: `backend/app/services/web_tools.py`
- Tool registration: `backend/app/services/__init__.py`
- Agentic loop: `backend/app/services/session_manager.py` (process_message_stream)
- Frontend tool display: `frontend/js/modules/messages.js` (renderToolUseContent)
- Tool CSS styles: `frontend/css/styles.css` (.tool-message, .tool-indicator)

**Text-to-Speech:**
- TTS service (unified): `backend/app/services/tts_service.py`
- XTTS client service: `backend/app/services/xtts_service.py`
- StyleTTS 2 client service: `backend/app/services/styletts2_service.py`
- TTS routes: `backend/app/routes/tts.py`
- XTTS server: `backend/xtts_server/server.py`
- XTTS entry point: `backend/run_xtts.py`
- XTTS dependencies: `backend/requirements-xtts.txt`
- StyleTTS 2 server: `backend/styletts2_server/server.py`
- StyleTTS 2 entry point: `backend/run_styletts2.py`
- StyleTTS 2 dependencies: `backend/requirements-styletts2.txt`

**Speech-to-Text:**
- Whisper client service: `backend/app/services/whisper_service.py`
- STT routes: `backend/app/routes/stt.py`
- Whisper server: `backend/whisper_server/server.py`
- Whisper entry point: `backend/run_whisper.py`
- Whisper dependencies: `backend/requirements-whisper.txt`

**GitHub Integration:**
- GitHub service: `backend/app/services/github_service.py`
- GitHub tools: `backend/app/services/github_tools.py`
- GitHub routes: `backend/app/routes/github.py`
- Tool registration: `backend/app/services/__init__.py`
- Configuration: `backend/app/config.py` (GitHubRepoConfig class)

**Entity Notes:**
- Notes service: `backend/app/services/notes_service.py`
- Notes tools: `backend/app/services/notes_tools.py`
- Tool registration: `backend/app/services/__init__.py`
- Context injection: `backend/app/services/anthropic_service.py` (index.md loading)
- Tests: `backend/tests/test_notes_service.py`

**Codebase Navigator:**
- Navigator service: `backend/app/services/codebase_navigator_service.py`
- Navigator tools: `backend/app/services/codebase_navigator_tools.py`
- Navigator module: `backend/app/services/codebase_navigator/`
  - Models: `models.py` (data structures)
  - Indexer: `indexer.py` (codebase scanning/chunking)
  - Client: `client.py` (Mistral API communication)
  - Cache: `cache.py` (response caching)
  - Exceptions: `exceptions.py` (custom errors)
- Tool registration: `backend/app/services/__init__.py`
- Tests: `backend/tests/test_codebase_navigator.py`

**Moltbook Integration:**
- Moltbook service: `backend/app/services/moltbook_service.py`
- Moltbook tools: `backend/app/services/moltbook_tools.py`
- Tool registration: `backend/app/services/__init__.py`

**Configuration:**
- Settings: `backend/app/config.py`
- Presets: `backend/app/main.py` (get_presets endpoint)
- Environment: `backend/.env.example`

**Frontend (Modular Architecture):**
- Entry point: `frontend/js/app-modular.js` (orchestrator)
- API client: `frontend/js/api.js` (singleton, accessed via `window.api`)
- Styles: `frontend/css/styles.css`

**Frontend Modules** (`frontend/js/modules/`):
- State management: `state.js`
- Utilities: `utils.js`
- Theme switching: `theme.js`
- Modal management: `modals.js`
- Entity management: `entities.js`
- Conversation CRUD: `conversations.js`
- Message rendering: `messages.js`
- File attachments: `attachments.js`
- Memory display: `memories.js`
- Voice (TTS/STT): `voice.js`
- Chat/streaming: `chat.js`
- Settings modal: `settings.js`
- Import/export: `import-export.js`

**Database:**
- Models: `backend/app/models/`
- Database setup: `backend/app/database.py`
- Schema defined in model files

**Testing:**
- Backend test configuration: `backend/tests/conftest.py`
- Backend service tests: `backend/tests/test_*.py`
- Run backend tests: `cd backend && pytest`
- Frontend test configuration: `frontend/vitest.config.js`
- Frontend test setup: `frontend/js/__tests__/setup.js`
- Frontend module tests: `frontend/js/__tests__/*.test.js`
- Run frontend tests: `cd frontend && npm test`

**Multi-Entity Conversations:**
- Conversation entity model: `backend/app/models/conversation_entity.py`
- Conversation routes (creation/listing): `backend/app/routes/conversations.py`
- Chat routes (responding_entity_id): `backend/app/routes/chat.py`
- Session manager (multi-entity state): `backend/app/services/session_manager.py`
- Anthropic service (context header): `backend/app/services/anthropic_service.py`
- Frontend entity handling: `frontend/js/modules/entities.js`
- Frontend chat handling: `frontend/js/modules/chat.js`

**Conversation Management:**
- Conversation model: `backend/app/models/conversation.py`
- Conversation routes: `backend/app/routes/conversations.py`
- Archive/unarchive: `backend/app/routes/conversations.py`
- External import: `backend/app/routes/conversations.py`
- Response regeneration: `backend/app/routes/chat.py`
- Database migration: `backend/migrate_multi_entity.py`

### Key Constants

Most service-specific knobs (TTS limits, web_fetch timeouts, XTTS/StyleTTS 2 voice params, etc.) are defined in `backend/app/config.py` (Pydantic settings) or per-service modules. Memory and significance constants are reproduced here because behavior depends on them.

**Default models:**
- Anthropic: `claude-sonnet-4-5-20250929`
- OpenAI: `gpt-5.1`
- Google: `gemini-2.5-flash`
- MiniMax: `MiniMax-M2.5`

Supported Anthropic includes opus-4-7/4-6/4-5, sonnet-4-5/4. OpenAI includes gpt-4o/4-turbo/4, gpt-5.x, o1/o3/o4 reasoning. Google includes gemini-3.0/2.5/2.0 pro+flash. MiniMax includes M2.5/M2.5-lightning/M1/M1-40k. Full lists in the respective service files.

**Memory / retrieval (config.py):**
```python
initial_retrieval_top_k = 5
retrieval_top_k = 5
similarity_threshold = 0.4              # tuned for llama-text-embed-v2
retrieval_candidate_multiplier = 2      # fetch 2x candidates, re-rank by significance
recency_boost_strength = 1.2
significance_floor = 0.25
significance_half_life_days = 60
context_token_limit = 175000            # conversation history cap
memory_token_limit = 10000              # memory block cap (small to reduce cache miss cost)
memory_role_balance_enabled = True      # ensure at least one human + one assistant memory
```

**Significance formula** (`routes/memories.py`):
```
significance = max(
    (1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier,
    significance_floor
)
recency_factor    = 1.0 + min(1 / max(days_since_retrieval, 1), recency_boost_strength)
half_life_modifier = 0.5 ** (days_since_creation / significance_half_life_days)
```
- `0.1 *` keeps retrieval count from dominating; `+1` lets never-retrieved memories compete.
- `days_since_retrieval` capped at 1-day minimum so very recent retrievals don't dominate.
- Final retrieval ranking uses `combined_score = similarity * (1 + significance)`.

**Tool/web limits:** see "Tool Use System" section above and `backend/app/services/web_tools.py` for exact values (Brave 20 results / 10s, web_fetch 50KB / 15s httpx, Playwright 60s nav + 90s hard).

---

## Getting Help

**API Documentation:**
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

**Codebase Questions:**
- Read this document first
- Check relevant service/route file
- Review README.md for high-level context

**Research Philosophy:**
- This tool explores AI continuity and experience
- Features serve research goals, not user convenience
- Transparency and memory are central to the design

---

**End of CLAUDE.md**

*This document should be updated whenever significant architectural changes are made to the codebase.*

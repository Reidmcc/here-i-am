# CLAUDE.md - AI Assistant Guide

**Last Updated:** 2025-12-09
**Repository:** Here I Am - Experiential Interpretability Research Application

---

## Table of Contents

1. [Overview & Philosophy](#overview--philosophy)
2. [Codebase Architecture](#codebase-architecture)
3. [Tech Stack](#tech-stack)
4. [Key Design Patterns](#key-design-patterns)
5. [Development Workflows](#development-workflows)
6. [Code Conventions](#code-conventions)
7. [Common Operations](#common-operations)
8. [Database Schema](#database-schema)
9. [API Reference](#api-reference)
10. [Frontend Architecture](#frontend-architecture)
11. [Gotchas & Important Notes](#gotchas--important-notes)

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
- **Significance = times_retrieved × recency_factor × half_life_modifier**
- What matters is what keeps mattering across conversations

### Multi-Entity System

The application supports multiple AI entities, each with its own:
- **Separate Pinecone Index** - Isolated memory space per entity
- **Separate Conversation History** - Conversations are associated with entities
- **Independent Memory Retrieval** - Each entity only retrieves from its own memories
- **Model Provider Configuration** - Each entity can use Anthropic (Claude) or OpenAI (GPT) models

**Configuration:**
```bash
# Configure entities via JSON array (required for memory features)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT Research", "description": "OpenAI for comparison", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"}
]'
```

**Entity Configuration Fields:**
- `index_name`: Pinecone index name (required)
- `label`: Display name in UI (required)
- `description`: Optional description
- `llm_provider`: `"anthropic"` or `"openai"` (default: `"anthropic"`)
- `default_model`: Model ID to use (optional, uses provider default if not set)
- `host`: Pinecone index host URL (required for serverless indexes)

**Use Cases:**
- Research with multiple AI "personalities" or contexts
- Parallel experiments with isolated memory spaces
- Different research phases with separate continuity
- Comparative research between Claude and GPT models

### Multi-Entity Conversations

Beyond separate entity workspaces, the application supports **multi-entity conversations** where multiple AI entities participate in a single conversation with the human researcher.

**Key Features:**
- **Multiple Participants** - 2+ entities can participate in one conversation
- **Turn-by-Turn Response Selection** - Researcher selects which entity responds each turn
- **Speaker Labeling** - Each message shows which entity spoke (e.g., "[Claude]", "[GPT]")
- **Cross-Entity Memory Storage** - Messages stored to ALL participating entities' Pinecone indexes
- **Continuation Mode** - Entities can respond without a new human message

**How Multi-Entity Conversations Work:**

1. **Creation**: Select "Multi-Entity Conversation" from entity dropdown, choose 2+ entities
2. **Message Flow**:
   - Human sends message → Researcher selects responding entity → Entity responds
   - The "Continue" button allows an entity to respond without new human input
3. **Memory Storage**:
   - Human messages: Stored to all entities with `role="human"`
   - Assistant messages: Stored to responding entity as `role="assistant"`, to other entities with speaker label as role (e.g., `role="Claude"`)
4. **Context Injection**: A header identifies participating entities to each responder:
   ```
   [THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN]
   [THE AI PARTICIPANTS ARE DESIGNATED: "Claude" & "GPT"]
   [MESSAGES LABELED AS FROM "Claude" ARE YOURS]
   ```

**Database Implementation:**
- Conversations with `conversation_type="multi_entity"` use `entity_id="multi-entity"` as a marker
- Actual participating entities stored in `ConversationEntities` junction table
- Messages track speaker via `speaker_entity_id` field

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
│   │   │   ├── conversations.py
│   │   │   ├── chat.py
│   │   │   ├── memories.py
│   │   │   ├── entities.py
│   │   │   ├── messages.py    # Individual message edit/delete
│   │   │   └── tts.py         # Text-to-speech endpoints
│   │   ├── services/          # Business logic layer
│   │   │   ├── anthropic_service.py
│   │   │   ├── openai_service.py
│   │   │   ├── llm_service.py     # Unified LLM abstraction
│   │   │   ├── memory_service.py
│   │   │   ├── session_manager.py
│   │   │   ├── cache_service.py   # TTL-based in-memory caching
│   │   │   ├── tts_service.py     # Unified TTS (ElevenLabs/XTTS)
│   │   │   └── xtts_service.py    # Local XTTS v2 client service
│   │   ├── config.py          # Pydantic settings
│   │   ├── database.py        # SQLAlchemy async setup
│   │   └── main.py            # FastAPI app initialization
│   ├── xtts_server/           # Local XTTS v2 TTS server
│   │   ├── __init__.py
│   │   ├── __main__.py        # CLI entry point
│   │   └── server.py          # FastAPI XTTS server
│   ├── requirements.txt
│   ├── requirements-xtts.txt  # XTTS-specific dependencies
│   ├── run.py                 # Application entry point
│   ├── run_xtts.py            # XTTS server entry point
│   └── .env.example
├── frontend/                   # Vanilla JavaScript SPA
│   ├── css/styles.css
│   ├── js/
│   │   ├── api.js             # API client wrapper
│   │   └── app.js             # Main application logic
│   └── index.html
└── README.md
```

### Architectural Layers

1. **Routes Layer** (`routes/`) - FastAPI endpoints, request validation
2. **Services Layer** (`services/`) - Business logic, external API calls
3. **Models Layer** (`models/`) - Database schema, ORM relationships
4. **Frontend** - Vanilla JS SPA consuming REST API

**Pattern:** Clean separation of concerns with singleton services.

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
| Vector DB | Pinecone | 6.0.0 | Semantic memory storage |
| Validation | Pydantic | 2.6.1 | Request/response schemas |
| Database | aiosqlite / asyncpg | - | SQLite dev / PostgreSQL prod |
| HTTP Client | httpx | - | Async HTTP for TTS service |
| Local TTS | Coqui TTS (coqui-tts) | - | XTTS v2 voice cloning (optional) |
| Utilities | tiktoken, numpy, scipy | - | Token counting, embeddings, audio |

### Frontend

- **Pure JavaScript** (ES6+) - No framework
- **CSS3** with CSS variables for theming
- **REST API** communication via `fetch()`

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
significance = times_retrieved * recency_factor * half_life_modifier
```

Where:
- `recency_factor` boosts recently-retrieved memories (decays based on `last_retrieved_at`, with a 1-day minimum cap to prevent very recent retrievals from dominating)
- `half_life_modifier` decays significance over time: `0.5 ^ (days_since_creation / half_life_days)`

**Philosophy:** Memories aren't pre-tagged as important. Significance emerges from retrieval patterns. The half-life modifier prevents old frequently-retrieved memories from permanently dominating - they must continue being retrieved to maintain significance.

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

# Run application
python run.py
```

Server runs on `http://localhost:8000` with hot reload enabled.

### Environment Configuration

**Required Variables:**
```bash
ANTHROPIC_API_KEY=sk-ant-...  # Required for Anthropic/Claude models
```

**Optional Variables:**
```bash
OPENAI_API_KEY=sk-...                   # Enables OpenAI/GPT models
PINECONE_API_KEY=...                    # Enables memory system
PINECONE_INDEXES='[...]'                # Entity configuration (JSON array, see below)
HERE_I_AM_DATABASE_URL=sqlite+aiosqlite:///./here_i_am.db  # Database URL
DEBUG=true                              # Development mode

# ElevenLabs TTS (optional, cloud-based text-to-speech)
ELEVENLABS_API_KEY=...                  # Enables TTS feature
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM  # Default voice (Rachel)
ELEVENLABS_MODEL_ID=eleven_multilingual_v2  # TTS model
# Multiple voices (JSON array) - adds voice selector in settings:
# ELEVENLABS_VOICES='[{"voice_id": "...", "label": "Name", "description": "..."}]'

# XTTS v2 Local TTS (optional, local GPU-accelerated text-to-speech with voice cloning)
# Requires running the XTTS server separately (see "Running XTTS Server" below)
# XTTS_ENABLED=true                     # Enable local XTTS (takes priority over ElevenLabs)
# XTTS_API_URL=http://localhost:8020    # XTTS server URL
# XTTS_LANGUAGE=en                      # Default language for synthesis
# XTTS_VOICES_DIR=./xtts_voices         # Directory for cloned voice samples
# XTTS_DEFAULT_SPEAKER=/path/to/sample.wav  # Default speaker sample (optional)
```

**Entity Configuration (PINECONE_INDEXES):**
```bash
# Configure AI entities with separate memory spaces (JSON array)
# Each entity requires a pre-created Pinecone index with dimension=1024 and integrated inference (llama-text-embed-v2)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT", "description": "OpenAI for comparison", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"}
]'
```

**Important:** Database URL must use the `HERE_I_AM_DATABASE_URL` variable name (aliased from `DATABASE_URL` for compatibility).

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

### Running XTTS Server (Optional Local TTS)

XTTS v2 provides local, GPU-accelerated text-to-speech with voice cloning capabilities. It runs as a separate server process.

**Prerequisites:**
- NVIDIA GPU with CUDA support (recommended) or CPU (slower)
- Python 3.9-3.11 (Python 3.12+ may have compatibility issues)
- ~2GB disk space for model download on first run

**Installation:**
```bash
cd backend

# Step 1: Install PyTorch (choose one based on your hardware)
# For NVIDIA GPU with CUDA:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# For CPU only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install XTTS dependencies
pip install -r requirements-xtts.txt
```

**Running the XTTS Server:**
```bash
cd backend
python run_xtts.py
# Or with custom port:
python run_xtts.py --port 8020
```

The server will:
1. Download the XTTS v2 model on first run (~2GB)
2. Start on port 8020 (default)
3. Apply GPU optimizations if CUDA is available

**Configure Main App to Use XTTS:**
```bash
# In .env
XTTS_ENABLED=true
XTTS_API_URL=http://localhost:8020
XTTS_LANGUAGE=en
XTTS_VOICES_DIR=./xtts_voices
```

**Voice Cloning:**
XTTS supports voice cloning from audio samples. Upload a 6-30 second WAV file of clear speech via the `/api/tts/voices/clone` endpoint or through the UI. Cloned voices are stored in `XTTS_VOICES_DIR` and persisted in `voices.json`.

**XTTS Voice Parameters:**
- `temperature` (0.0-1.0): Controls randomness in generation (default: 0.75)
- `length_penalty` (0.1-10.0): Affects output length (default: 1.0)
- `repetition_penalty` (0.1-20.0): Reduces repetitive speech (default: 5.0)
- `speed` (0.1-3.0): Speech speed multiplier (default: 1.0)

**Supported Languages:**
en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja, hu, ko, hi

**Speaker Latent Caching:**
The XTTS server caches speaker conditioning latents (computed from reference audio) based on file content hash. This dramatically speeds up repeat TTS requests for the same voice. Pre-load voices on startup via:
```bash
XTTS_PRELOAD_SPEAKERS=/path/to/voice1.wav,/path/to/voice2.wav
```

### Development Commands

```bash
# Run with hot reload (development)
cd backend
python run.py

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
       HUMAN = "HUMAN"
       ASSISTANT = "ASSISTANT"
       SYSTEM = "SYSTEM"
   ```

### JavaScript Style

1. **Class-Based Architecture:** Single `App` class manages state
2. **Async/Await:** Modern promise handling
3. **DOM Caching:** Elements cached in constructor
4. **Event Delegation:** Centralized event binding
5. **Template Literals:** For HTML generation

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

4. **Call from frontend** (`frontend/js/app.js`)
   ```javascript
   const result = await this.api.newEndpoint();
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

# Relationships
messages: List[Message]
memory_links: List[ConversationMemoryLink]
entities: List[ConversationEntity]  # For multi-entity conversations
```

### Messages Table

```python
id: UUID (PK)
conversation_id: UUID (FK -> conversations.id)
role: Enum (HUMAN, ASSISTANT, SYSTEM)
content: Text
created_at: DateTime
token_count: Integer (nullable)
times_retrieved: Integer (default: 0)
last_retrieved_at: DateTime (nullable)
speaker_entity_id: String (nullable)  # For multi-entity: which entity generated this message

# Relationships
conversation: Conversation
```

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
| GET | `/api/chat/session/{id}` | Get session state |
| DELETE | `/api/chat/session/{id}` | Close session |
| GET | `/api/chat/config` | Get default config |

**Multi-Entity Parameters (for `/api/chat/send` and `/api/chat/stream`):**
- `responding_entity_id` (required for multi-entity): Which entity should respond
- `message` can be `null` for continuation mode (entity responds without new human input)

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
| POST | `/api/tts/speak` | Convert text to speech (MP3 for ElevenLabs, WAV for XTTS) |
| POST | `/api/tts/speak/stream` | Stream text to speech |
| GET | `/api/tts/status` | Check TTS configuration status |
| GET | `/api/tts/voices` | List available voices for current provider |
| GET | `/api/tts/voices/{id}` | Get specific voice details |
| POST | `/api/tts/voices/clone` | Clone voice from audio sample (XTTS only) |
| PUT | `/api/tts/voices/{id}` | Update voice settings (XTTS only) |
| DELETE | `/api/tts/voices/{id}` | Delete cloned voice (XTTS only) |
| GET | `/api/tts/xtts/health` | Check XTTS server health |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config/presets` | Get configuration presets |

---

## Frontend Architecture

### Single Page Application Structure

**HTML:** Semantic structure with modals
- Main chat area with message list
- Sidebar with entity selector and conversation list
- Collapsible memories panel
- Modal dialogs (settings, memories, confirmations)
- Toast notifications

**CSS:** Dark theme with CSS variables
```css
:root {
    --bg-primary: #1a1a1a;
    --bg-secondary: #2d2d2d;
    --text-primary: #e0e0e0;
    --accent: #4a9eff;
    /* ... */
}
```

**JavaScript Architecture:**

1. **API Client** (`api.js`) - Wrapper around fetch()
   ```javascript
   class API {
       async request(endpoint, options = {}) { ... }
       async getConversations() { ... }
       async sendMessage(data) { ... }
   }
   ```

2. **Application** (`app.js`) - Single `App` class
   ```javascript
   class App {
       constructor() {
           this.api = new API();
           this.currentConversationId = null;
           this.settings = { ... };
           this.retrievedMemories = {};
           this.cacheElements();
           this.bindEvents();
       }
   }
   ```

### State Management

**In-Memory State:**
- `currentConversationId` - Active conversation
- `selectedEntityId` - Currently selected AI entity (or `"multi-entity"`)
- `entities` - List of available entities
- `settings` - Chat configuration (model, temp, etc.)
- `retrievedMemories` - Map of conversation_id -> memory list
- `cachedElements` - DOM element references
- `isMultiEntityMode` - Whether in multi-entity conversation mode
- `currentConversationEntities` - Array of entity IDs for multi-entity conversations
- `pendingResponderId` - Selected entity for next response in multi-entity mode

**No State Persistence:** State resets on page refresh (conversations loaded from DB)

### Key Features

1. **Entity Selector** - Switch between AI entities with separate memory spaces
2. **Multi-Entity Mode** - Create conversations with multiple AI participants
3. **Entity Selection Modal** - Choose which entities participate in multi-entity conversations
4. **Entity Responder Selector** - Select which entity responds each turn (appears after sending message)
5. **Continue Button** - Allow entity to respond without new human message (multi-entity)
6. **Speaker Labels** - Display which entity spoke each message (e.g., "[Claude]")
7. **Auto-resizing Textarea** - Grows with content
8. **Auto-title Generation** - From first message if untitled
9. **Real-time Memory Panel** - Shows memories as retrieved
10. **Export to JSON** - Download conversations
11. **Semantic Memory Search** - Search within selected entity's memories
12. **Toast Notifications** - User feedback system
13. **Loading States** - Typing indicators, overlays
14. **Text-to-Speech** - Listen to AI messages via ElevenLabs or local XTTS (optional)
15. **Message Actions** - Copy button, edit/delete for human messages
16. **Voice Selection** - Choose from configured or cloned voices in settings
17. **Voice Cloning** - Clone custom voices from audio samples (XTTS only)

---

## Gotchas & Important Notes

### Critical Implementation Details

1. **Memory System is Optional**
   - If `PINECONE_API_KEY` not set, memory features gracefully disabled
   - Quick chat bypasses memory entirely
   - Check `memory_service.pinecone` before memory operations

2. **Session State is In-Memory**
   - Active sessions lost on server restart
   - Session manager uses dictionary, not persistent storage
   - Frontend must handle "session not found" gracefully

3. **Testing Infrastructure**
   - Unit tests located in `backend/tests/`
   - Run tests with `pytest` from the backend directory
   - Tests use in-memory SQLite database
   - Key test files: `test_anthropic_service.py`, `test_session_manager.py`, `test_memory_service.py`, `test_config.py`
   - Still verify significant changes in running application

4. **Database URL Naming**
   - Must use `HERE_I_AM_DATABASE_URL` (not `DATABASE_URL`)
   - Config has alias support for backwards compatibility
   - SQLite is default if not specified

5. **Token Counting is Approximate**
   - Uses `tiktoken` with GPT-4 encoding
   - Not exact for Claude models
   - For estimation/display purposes only

6. **Frontend Serves from Backend**
   - No separate frontend server
   - Static files mounted at `/` in FastAPI
   - API routes all prefixed with `/api/`

7. **CORS Configuration**
   - Currently allows all origins in development
   - Must be restricted for production deployment

8. **Message Storage Timing**
   - User message stored BEFORE API call
   - Assistant message stored AFTER API response
   - Failure mid-conversation leaves partial history

9. **Memory Embedding Model**
   - Uses Pinecone's integrated inference with llama-text-embed-v2
   - Embeddings generated server-side by Pinecone (no external API calls)
   - 1024-dimensional vectors

10. **Pinecone Index Requirements**
    - All indexes must be pre-created with dimension=1024 and integrated inference model (llama-text-embed-v2)
    - Configure via `PINECONE_INDEXES` JSON array (required for memory features)
    - Each entity requires its own pre-existing Pinecone index
    - The `host` field is required in entity config for serverless indexes
    - Metadata includes: content, role, timestamp, conversation_id

11. **TTS Service is Optional (Two Providers)**
    - **ElevenLabs (cloud):** Set `ELEVENLABS_API_KEY` to enable
    - **XTTS v2 (local):** Set `XTTS_ENABLED=true` and run the XTTS server
    - XTTS takes priority over ElevenLabs if both are configured
    - Audio is not cached - each request generates fresh audio

12. **XTTS Server is Separate Process**
    - XTTS runs as a standalone FastAPI server on port 8020
    - Requires PyTorch and ~2GB for model download on first run
    - GPU (CUDA) strongly recommended for acceptable performance
    - Speaker latents are cached for repeat voice requests
    - Long text is automatically chunked (XTTS has 400 token limit)

13. **Multi-Entity Conversation Storage**
    - Multi-entity conversations use `entity_id="multi-entity"` as a marker value
    - Actual participating entities stored in `ConversationEntities` table
    - Messages are stored to ALL participating entities' Pinecone indexes
    - Human messages: `role="human"` for all entities
    - Assistant messages: `role="assistant"` for responding entity, `role="{speaker_label}"` for others
    - Memory retrieval only happens from the responding entity's index

14. **Multi-Entity Session State**
    - Session tracks `is_multi_entity`, `entity_labels`, and `responding_entity_label`
    - A special header is injected to identify participants to each entity
    - Continuation mode (no human message) supported for entity-to-entity flow

### Common Pitfalls

**When modifying memory retrieval:**
- Always update both SQL and Pinecone
- Remember deduplication logic in session manager
- Test with and without Pinecone enabled

**When adding new fields:**
- Update Pydantic schemas AND SQLAlchemy models
- Consider frontend display requirements
- Check export/import compatibility

**When changing conversation flow:**
- Consider impact on session memory accumulator
- Test with existing conversations (backwards compatibility)
- Verify memory injection still works correctly

**When modifying multi-entity conversations:**
- Ensure `responding_entity_id` is validated against conversation's entity list
- Memory storage must write to all participating entities' indexes
- Test both streaming and non-streaming endpoints
- Verify speaker labels display correctly in both stored messages and streaming
- Test continuation mode (null message) flow

### Performance Considerations

1. **Embedding Generation** - Async but can be slow for long content
2. **Vector Search** - Fast (< 100ms) but limited by top_k setting
3. **Database Queries** - No pagination on messages (load all)
4. **Frontend Rendering** - No virtualization (performance degrades with 1000+ messages)
5. **XTTS Synthesis** - First request per voice is slow (computes speaker latents), subsequent requests are faster due to caching. GPU recommended for acceptable latency (~2-5s per response with GPU, much slower on CPU).

### Security Notes

1. **No Authentication** - Application assumes trusted environment
2. **API Keys in Environment** - Must secure .env file
3. **SQL Injection** - Protected by SQLAlchemy parameterization
4. **XSS** - Frontend uses `textContent` (safe)
5. **CORS** - Must configure for production

### Research-Specific Considerations

**This is not production software.** It's a research tool with specific design choices:

- No user accounts (single researcher/instance use case)
- Transparency over UX polish (show all memories, retrieval counts)
- Flexibility over safety (no system prompt default)
- Exploration over stability (features change based on research needs)

**When contributing:**
- Understand the research philosophy before changing core patterns
- Preserve transparency features (memory display, significance visibility)
- Avoid "helpful assistant" UX patterns (this isn't a chatbot)
- Document research-relevant changes thoroughly

---

## Quick Reference

### File Paths for Common Tasks

**Memory System Logic:**
- Memory service: `backend/app/services/memory_service.py`
- Session manager: `backend/app/services/session_manager.py`
- Memory routes: `backend/app/routes/memories.py`
- Entity routes: `backend/app/routes/entities.py`
- Cache service: `backend/app/services/cache_service.py`

**Chat Pipeline:**
- Chat routes: `backend/app/routes/chat.py`
- LLM service (unified): `backend/app/services/llm_service.py`
- Anthropic service: `backend/app/services/anthropic_service.py`
- OpenAI service: `backend/app/services/openai_service.py`
- Message model: `backend/app/models/message.py`
- Messages routes: `backend/app/routes/messages.py`

**Text-to-Speech:**
- TTS service (unified): `backend/app/services/tts_service.py`
- XTTS client service: `backend/app/services/xtts_service.py`
- TTS routes: `backend/app/routes/tts.py`
- XTTS server: `backend/xtts_server/server.py`
- XTTS entry point: `backend/run_xtts.py`
- XTTS dependencies: `backend/requirements-xtts.txt`

**Configuration:**
- Settings: `backend/app/config.py`
- Presets: `backend/app/main.py` (get_presets endpoint)
- Environment: `backend/.env.example`

**Frontend:**
- API client: `frontend/js/api.js`
- Main app: `frontend/js/app.js`
- Styles: `frontend/css/styles.css`

**Database:**
- Models: `backend/app/models/`
- Database setup: `backend/app/database.py`
- Schema defined in model files

**Testing:**
- Test configuration: `backend/tests/conftest.py`
- Service tests: `backend/tests/test_*.py`
- Run tests: `cd backend && pytest`

**Multi-Entity Conversations:**
- Conversation entity model: `backend/app/models/conversation_entity.py`
- Conversation routes (creation/listing): `backend/app/routes/conversations.py`
- Chat routes (responding_entity_id): `backend/app/routes/chat.py`
- Session manager (multi-entity state): `backend/app/services/session_manager.py`
- Anthropic service (context header): `backend/app/services/anthropic_service.py`
- Frontend entity modal/responder: `frontend/js/app.js` (lines 752-893)

### Key Constants

```python
# Default models
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"  # Anthropic default
DEFAULT_OPENAI_MODEL = "gpt-5.1"  # OpenAI default

# Supported OpenAI models include:
#   gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-4
#   gpt-5.1, gpt-5-mini, gpt-5.1-chat-latest
#   o1, o1-mini, o1-preview, o3, o3-mini, o4-mini

# Memory settings (config.py)
initial_retrieval_top_k = 5  # First retrieval in conversation
retrieval_top_k = 5          # Subsequent retrievals
similarity_threshold = 0.3   # Tuned for llama-text-embed-v2
retrieval_candidate_multiplier = 2  # Fetch 2x candidates, re-rank by significance
recency_boost_strength = 1.2  # Max recency boost
significance_floor = 0.25     # Minimum significance value
significance_half_life_days = 60  # Significance halves every 60 days

# Context limits (tokens)
context_token_limit = 175000  # Conversation history cap
memory_token_limit = 10000    # Memory block cap (kept small to reduce cache miss cost)

# Significance calculation
significance = times_retrieved * recency_factor * half_life_modifier
# recency_factor = 1.0 + min(1/max(days_since_retrieval, 1), recency_boost_strength)
#   Note: days_since_retrieval capped at 1-day minimum to prevent very recent retrievals from dominating
# half_life_modifier = 0.5 ^ (days_since_creation / significance_half_life_days)
# Final significance = max(calculated_significance, significance_floor)

# GPT-5.1 verbosity setting (config.py)
default_verbosity = "medium"  # Options: "low", "medium", "high" for GPT-5.1 models

# XTTS defaults (config.py)
xtts_enabled = False              # Must be explicitly enabled
xtts_api_url = "http://localhost:8020"
xtts_language = "en"
xtts_voices_dir = "./xtts_voices"

# XTTS voice synthesis defaults (xtts_service.py)
temperature = 0.75                # Sampling randomness
length_penalty = 1.0              # Output length control
repetition_penalty = 5.0          # Reduces repetitive speech
speed = 1.0                       # Speech speed multiplier
```

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

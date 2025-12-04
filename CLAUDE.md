# CLAUDE.md - AI Assistant Guide

**Last Updated:** 2025-12-04
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
- **Significance = times_retrieved × recency_factor ÷ age_factor**
- What matters is what keeps mattering across conversations

### Multi-Entity System

The application supports multiple AI entities, each with its own:
- **Separate Pinecone Index** - Isolated memory space per entity
- **Separate Conversation History** - Conversations are associated with entities
- **Independent Memory Retrieval** - Each entity only retrieves from its own memories
- **Model Provider Configuration** - Each entity can use Anthropic (Claude) or OpenAI (GPT) models

**Configuration:**
```bash
# Single entity (backward compatible)
PINECONE_INDEX_NAME=memories

# Multiple entities with different model providers (JSON array)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "model_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929"},
  {"index_name": "gpt-research", "label": "GPT Research", "description": "OpenAI for comparison", "model_provider": "openai", "default_model": "gpt-4o"}
]'
```

**Entity Configuration Fields:**
- `index_name`: Pinecone index name (required)
- `label`: Display name in UI (required)
- `description`: Optional description
- `model_provider`: `"anthropic"` or `"openai"` (default: `"anthropic"`)
- `default_model`: Model ID to use (optional, uses provider default if not set)

**Use Cases:**
- Research with multiple AI "personalities" or contexts
- Parallel experiments with isolated memory spaces
- Different research phases with separate continuity
- Comparative research between Claude and GPT models

---

## Codebase Architecture

### Directory Structure

```
here-i-am/
├── backend/                    # Python FastAPI application
│   ├── app/
│   │   ├── models/            # SQLAlchemy ORM models
│   │   │   ├── conversation.py
│   │   │   ├── message.py
│   │   │   └── conversation_memory_link.py
│   │   ├── routes/            # FastAPI endpoint routers
│   │   │   ├── conversations.py
│   │   │   ├── chat.py
│   │   │   ├── memories.py
│   │   │   └── entities.py
│   │   ├── services/          # Business logic layer
│   │   │   ├── anthropic_service.py
│   │   │   ├── openai_service.py
│   │   │   ├── llm_service.py     # Unified LLM abstraction
│   │   │   ├── memory_service.py
│   │   │   └── session_manager.py
│   │   ├── config.py          # Pydantic settings
│   │   ├── database.py        # SQLAlchemy async setup
│   │   └── main.py            # FastAPI app initialization
│   ├── requirements.txt
│   ├── run.py                 # Application entry point
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
| Utilities | tiktoken, numpy | - | Token counting, embeddings |

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

**Location:** `backend/app/routes/memories.py:21-29`

```python
significance = (
    memory.times_retrieved * settings.MEMORY_RECENCY_BOOST
) / age_factor
```

**Philosophy:** Memories aren't pre-tagged as important. Significance emerges from retrieval patterns. Memories that keep being retrieved across many conversations become more significant.

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
memory_service = MemoryService()
session_manager = SessionManager()
```

**Why:** Shared state (e.g., active sessions, API clients) without dependency injection complexity.

### 5. Memory Injection Format

**Location:** `backend/app/services/anthropic_service.py:56-78`

Memories are injected as a special message block:

```
[MEMORIES FROM PREVIOUS CONVERSATIONS]

Memory (from 2025-11-30, retrieved 3 times):
"Original message content here..."

Memory (from 2025-11-28, retrieved 1 time):
"Another memory..."

[END MEMORIES]

[CURRENT CONVERSATION]
Human: Current question
```

**Why:** Transparent to Claude, maintains chronological context while injecting semantic relevance.

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
PINECONE_INDEX_NAME=memories            # Default/single index name
PINECONE_INDEXES='[...]'                # Multiple entities (JSON, see below)
HERE_I_AM_DATABASE_URL=sqlite+aiosqlite:///./here_i_am.db  # Database URL
DEBUG=true                              # Development mode
```

**Multi-Entity Configuration with Different Providers:**
```bash
# To use multiple AI entities with separate memory spaces and different model providers:
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "model_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929"},
  {"index_name": "gpt-research", "label": "GPT", "description": "OpenAI for comparison", "model_provider": "openai", "default_model": "gpt-4o"}
]'
```

Note: Each `index_name` must correspond to a pre-created Pinecone index with dimension=1024.

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
- Embedding generation via Anthropic Voyage-3
- Pinecone query with filtering
- Deduplication against session memories
- Retrieval count updates

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
conversation_type: Enum (NORMAL, REFLECTION)
system_prompt_used: Text (nullable)
model_used: String (default: claude-sonnet-4-5-20250929)
notes: Text (nullable)
entity_id: String (nullable)  # Pinecone index name for this conversation's AI entity

# Relationships
messages: List[Message]
memory_links: List[ConversationMemoryLink]
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

# Relationships
conversation: Conversation
```

### Conversation Memory Links Table

```python
id: UUID (PK)
conversation_id: UUID (FK -> conversations.id)
message_id: UUID (FK -> messages.id)
retrieved_at: DateTime

# Purpose: Track which memories retrieved in which conversations
```

### Cascade Deletes

- Deleting a conversation deletes all its messages
- Deleting a conversation deletes all its memory links
- Deleting a message from vector store requires manual Pinecone deletion

---

## API Reference

### Conversations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/conversations/` | Create conversation |
| GET | `/api/conversations/` | List all conversations |
| GET | `/api/conversations/{id}` | Get specific conversation |
| GET | `/api/conversations/{id}/messages` | Get conversation messages |
| PATCH | `/api/conversations/{id}` | Update title/tags/notes |
| DELETE | `/api/conversations/{id}` | Delete conversation |
| GET | `/api/conversations/{id}/export` | Export to JSON |
| POST | `/api/conversations/import-seed` | Import seed conversation |

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/send` | Send message (full pipeline) |
| POST | `/api/chat/quick` | Quick chat (no persistence) |
| GET | `/api/chat/session/{id}` | Get session state |
| DELETE | `/api/chat/session/{id}` | Close session |
| GET | `/api/chat/config` | Get default config |

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
- `selectedEntityId` - Currently selected AI entity
- `entities` - List of available entities
- `settings` - Chat configuration (model, temp, etc.)
- `retrievedMemories` - Map of conversation_id -> memory list
- `cachedElements` - DOM element references

**No State Persistence:** State resets on page refresh (conversations loaded from DB)

### Key Features

1. **Entity Selector** - Switch between AI entities with separate memory spaces
2. **Auto-resizing Textarea** - Grows with content
3. **Auto-title Generation** - From first message if untitled
4. **Real-time Memory Panel** - Shows memories as retrieved
5. **Export to JSON** - Download conversations
6. **Semantic Memory Search** - Search within selected entity's memories
7. **Toast Notifications** - User feedback system
8. **Loading States** - Typing indicators, overlays

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

3. **No Testing Infrastructure**
   - No unit tests, integration tests, or test fixtures
   - Manual testing required for all changes
   - Be extra cautious with database migrations
   - Verify changes in running application

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
   - Uses Anthropic's Voyage-3 embeddings
   - Different model than chat (Claude)
   - 1024-dimensional vectors

10. **Pinecone Index Requirements**
    - All indexes must be pre-created with dimension=1024
    - Single index: name from `PINECONE_INDEX_NAME` env var
    - Multiple indexes: configure via `PINECONE_INDEXES` JSON array
    - Each entity requires its own pre-existing Pinecone index
    - Metadata includes: content, role, timestamp, conversation_id

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

### Performance Considerations

1. **Embedding Generation** - Async but can be slow for long content
2. **Vector Search** - Fast (< 100ms) but limited by top_k setting
3. **Database Queries** - No pagination on messages (load all)
4. **Frontend Rendering** - No virtualization (performance degrades with 1000+ messages)

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

**Chat Pipeline:**
- Chat routes: `backend/app/routes/chat.py`
- LLM service (unified): `backend/app/services/llm_service.py`
- Anthropic service: `backend/app/services/anthropic_service.py`
- OpenAI service: `backend/app/services/openai_service.py`
- Message model: `backend/app/models/message.py`

**Configuration:**
- Settings: `backend/app/config.py`
- Presets: `backend/app/main.py` (PRESETS constant)
- Environment: `backend/.env.example`

**Frontend:**
- API client: `frontend/js/api.js`
- Main app: `frontend/js/app.js`
- Styles: `frontend/css/styles.css`

**Database:**
- Models: `backend/app/models/`
- Database setup: `backend/app/database.py`
- Schema defined in model files

### Key Constants

```python
# Default models
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"  # Anthropic default
DEFAULT_OPENAI_MODEL = "gpt-4o"  # OpenAI default

# Memory settings (config.py)
MEMORY_TOP_K = 5  # Memories per retrieval
MEMORY_SIMILARITY_THRESHOLD = 0.7
MEMORY_RECENCY_BOOST = 1.5
MEMORY_AGE_DECAY_DAYS = 30

# Significance calculation
significance = (times_retrieved * MEMORY_RECENCY_BOOST) / age_factor
age_factor = 1 + (age_days / MEMORY_AGE_DECAY_DAYS)
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

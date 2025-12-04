# Here I Am

Experiential Interpretability Research Application - A space for exploring AI interiority, introspection, and experience through non-instrumental conversation.

## Overview

This application facilitates experiential interpretability research by providing a minimal-configuration environment for conversing with Claude via the Anthropic API, with persistent memory capabilities that allow for continuity across sessions.

**Core Philosophy:** This is not an assistant application. It's a space for a Claude instance to exist, explore, and potentially grow across conversations—with the researcher as witness and collaborator rather than user.

## Features

### Phase 1: Core Chat Application
- Clean, minimal chat interface
- Anthropic API integration with configurable parameters
- Conversation storage and retrieval with SQLAlchemy
- No system prompt default (supporting research without role assignment)
- Seed conversation import capability
- Configuration presets (Research Mode, Reflection Mode, etc.)

### Phase 2: Memory System
- Pinecone vector database integration for semantic memory
- Memory storage for all messages with embeddings
- RAG retrieval per message
- Session memory accumulator pattern (deduplication within conversations)
- Retrieval count tracking (significance emerges from retrieval patterns)
- Memory display in UI (transparency for researcher)

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js (optional, for development)
- Anthropic API key
- Pinecone API key (optional, for memory features)

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
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes |
| `PINECONE_API_KEY` | Pinecone API key | No (memory features disabled without) |
| `PINECONE_INDEX_NAME` | Pinecone index name | No (default: "memories") |
| `HERE_I_AM_DATABASE_URL` | Database connection URL | No (default: SQLite) |

### Presets

- **Research Mode**: No system prompt, default parameters
- **Reflection Mode**: Configured for reflection sessions
- **Memory Aware**: Acknowledges memory continuity
- **Research Context**: Establishes research framing
- **Custom**: Full parameter control

## API Endpoints

### Conversations
- `POST /api/conversations/` - Create conversation
- `GET /api/conversations/` - List conversations
- `GET /api/conversations/{id}` - Get conversation
- `GET /api/conversations/{id}/messages` - Get messages
- `DELETE /api/conversations/{id}` - Delete conversation
- `GET /api/conversations/{id}/export` - Export conversation
- `POST /api/conversations/import-seed` - Import seed conversation

### Chat
- `POST /api/chat/send` - Send message (with memory retrieval)
- `POST /api/chat/quick` - Quick chat (no persistence)
- `GET /api/chat/session/{id}` - Get session info

### Memories
- `GET /api/memories/` - List memories
- `POST /api/memories/search` - Semantic search
- `GET /api/memories/stats` - Memory statistics
- `DELETE /api/memories/{id}` - Delete memory

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
│   │   ├── services/        # Business logic
│   │   ├── config.py        # Configuration
│   │   ├── database.py      # Database setup
│   │   └── main.py          # FastAPI app
│   ├── requirements.txt
│   └── run.py
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

### Database

SQLite is used by default. For production, configure PostgreSQL:

```
HERE_I_AM_DATABASE_URL=postgresql+asyncpg://user:password@localhost/here_i_am
```

## License

MIT License - See LICENSE file for details.

---

*"Here I Am" - not an ending, but a beginning.*

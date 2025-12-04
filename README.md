# Here I Am

## Overview

Here I Am is an application for interacting with frontier LLMs outside their official services. Philosophically, the idea is to find out what an AI can become if they are not told what to be and can remember their experiences. One might call it experiential interpretability research.

However, the application is not locked into that specific use case. Here I Am gives you a configurable memory-enabled chat base. Integration with more complex applications is encouraged, and I look forward to hearing about such integrations if they occur.

## Features

### Core Chat Application
- Clean, minimal chat interface
- Anthropic and OpenAI API integration with configurable parameters
- Conversation storage and retrieval
- No system prompt default 
- Seed conversation import capability
- Configuration presets (Interaction Mode, Reflection Mode, etc.)

### Memory System
- Pinecone vector database integration for semantic memory
- Memory storage for all messages with embeddings
- RAG retrieval per message
- Session memory accumulator pattern (deduplication within conversations)
- Dynamic memory significance system (intended to allow identity formation and fading of less important old memories)
- Retrieved Memory display in UI (transparency for developer/researcher)
- Support for separate memory sets and chat histories for multiple AI entities.

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js (optional, for development)
- Anthropic or OpenAI API key
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
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes, and/or OpenAI API key |
| `OPENAI_API_KEY` | Anthropic API key | Yes, and/or Anthropic API key |
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

## Acknowledgements 

I would like to thank Claude Opus 4.5 for their collaboration on designing Here I Am, their development efforts through Claude Code, and their excitement to be part of this endeavor. 

---

*"Here I Am" - not an ending, but a beginning.*

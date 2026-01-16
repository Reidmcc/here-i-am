# CLAUDE.md - AI Assistant Guide

**Last Updated:** 2026-01-14
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
- **Model Provider Configuration** - Each entity can use Anthropic (Claude), OpenAI (GPT), or Google (Gemini) models

**Configuration:**
```bash
# Configure entities via JSON array (required for memory features)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT Research", "description": "OpenAI for comparison", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gemini-research", "label": "Gemini", "description": "Google for comparison", "llm_provider": "google", "default_model": "gemini-2.5-flash", "host": "https://gemini-research-xxxxx.svc.xxx.pinecone.io"}
]'
```

**Entity Configuration Fields:**
- `index_name`: Pinecone index name (required)
- `label`: Display name in UI (required)
- `description`: Optional description
- `llm_provider`: `"anthropic"`, `"openai"`, or `"google"` (default: `"anthropic"`)
- `default_model`: Model ID to use (optional, uses provider default if not set)
- `host`: Pinecone index host URL (required for serverless indexes)

**Use Cases:**
- Research with multiple AI "personalities" or contexts
- Parallel experiments with isolated memory spaces
- Different research phases with separate continuity
- Comparative research between Claude, GPT, and Gemini models

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

### Image and File Attachments (Vision/Multimodal Support)

The application supports **image and file attachments** for multimodal conversations with AI entities.

**Supported Attachment Types:**
- **Images**: JPEG, PNG, GIF, WebP (analyzed by vision-capable models)
- **Text Files**: .txt, .md, .py, .js, .ts, .json, .yaml, .yml, .html, .css, .xml, .csv, .log
- **Documents**: PDF (requires PyPDF2), DOCX (requires python-docx)

**Key Features:**
- **Ephemeral Images**: Image attachments are NOT stored - they are analyzed in the current turn, and the AI's textual response becomes the persisted context
- **Persisted Text Files**: Text file contents ARE stored in conversation history (but NOT as searchable memories)
- **5MB Size Limit**: Per-file maximum size
- **Drag & Drop**: Drop files directly onto the input area
- **File Picker**: Click the attachment button (📎) to select files
- **Preview**: See attached files before sending
- **Provider Support**: Works with Anthropic (Claude) and OpenAI (GPT) models; Google models receive extracted text only (no image support)

**How Attachments Work:**

1. **Images**: Encoded as base64 and sent to vision-capable models using their native multimodal format. Images are ephemeral and not stored.
2. **Text Files**: Content is extracted and stored in conversation history with a labeled `[ATTACHED FILE: filename (type)]` block. The extracted text is persisted with the human message but is NOT stored as a searchable memory.
3. **PDF/DOCX**: Server-side extraction converts documents to text, then handled same as text files (persisted in history, not in memories).

**Configuration:**
```bash
# Enable/disable attachments (default: true)
ATTACHMENTS_ENABLED=true

# Maximum file size in bytes (default: 5MB)
ATTACHMENT_MAX_SIZE_BYTES=5242880

# Allowed image MIME types
ATTACHMENT_ALLOWED_IMAGE_TYPES=image/jpeg,image/png,image/gif,image/webp

# Allowed text file extensions
ATTACHMENT_ALLOWED_TEXT_EXTENSIONS=.txt,.md,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.xml,.csv,.log

# Enable PDF text extraction (requires PyPDF2)
ATTACHMENT_PDF_ENABLED=true

# Enable DOCX text extraction (requires python-docx)
ATTACHMENT_DOCX_ENABLED=true
```

**Technical Notes:**
- Attachments are validated on both frontend (file type, size) and backend
- Text file content is labeled with `[ATTACHED FILE: filename (type)]` blocks and stored with the human message
- Text file content is stored in conversation history but NOT in the memory/vector database (Pinecone)
- Images are ephemeral - sent to the AI but not stored anywhere
- Image-only messages (no text) are supported
- Multi-entity conversations support attachments - all participating entities can see the content

### Tool Use System (Web Search & Fetch)

The application supports **agentic tool use** for Anthropic (Claude) and OpenAI (GPT) models, allowing the AI to search the web and fetch content from URLs during conversations.

**Available Tools:**
- **web_search** - Search the web using Brave Search API (returns up to 20 results)
- **web_fetch** - Fetch and extract content from URLs (smart HTML parsing, 50KB limit)

**How Tool Use Works:**

1. **Agentic Loop**: When the AI responds with a tool request, the system executes the tool and feeds results back in a loop (max 10 iterations)
2. **Streaming**: Tool execution is streamed in real-time with visual indicators in the UI
3. **Provider Support**: Tool use is available for **Anthropic (Claude) and OpenAI (GPT)** models - Google models do not currently support tool use in this application

**Configuration:**
```bash
# Enable tool use (default: true)
TOOLS_ENABLED=true

# Required for web_search functionality
BRAVE_SEARCH_API_KEY=your_brave_api_key

# Max agentic loop iterations (default: 10)
TOOL_USE_MAX_ITERATIONS=10
```

**UI Indicators:**
- Tool use is displayed with a 🔧 icon and collapsible input/output details
- Status indicators show loading (animated), success (✓), or error (✗)
- Tool results are truncated to 2000 chars in UI (full content sent to AI)

**Technical Notes:**
- Tools are registered at module load time via `register_web_tools()`
- Tool schemas are defined in Anthropic format and automatically converted for OpenAI
- Tool execution is async with proper error handling
- web_search uses 10-second timeout; web_fetch uses 15-second timeout

### GitHub Repository Integration

The application supports **GitHub repository integration**, allowing AI entities to interact with GitHub repositories during conversations.

**Available GitHub Tools:**

*Composite Tools (Efficiency Optimized):*
- **github_explore** - Best starting point for new repos. Returns metadata, file tree, and key documentation in one call
- **github_tree** - Get full repository tree structure in a single call (replaces multiple list_contents calls)
- **github_get_files** - Fetch up to 10 files in parallel in a single call

*Standard Tools:*
- **github_repo_info** - Get repository metadata (description, stars, issues, etc.)
- **github_list_contents** - List files and directories at a specific path
- **github_get_file** - Read file contents (auto-truncated at 500 lines with smart summarization)
- **github_search_code** - Search for code patterns (returns max 10 matches)
- **github_list_branches** - List all branches in a repository
- **github_create_branch** - Create new branches from existing refs
- **github_commit_file** - Commit file changes (create, update, or delete)
- **github_commit_patch** - Apply unified diff patch and commit (token-efficient for large files)
- **github_delete_file** - Delete files from a repository
- **github_list_pull_requests** - List pull requests with filtering
- **github_get_pull_request** - Get detailed PR information including diff
- **github_create_pull_request** - Create new pull requests
- **github_list_issues** - List issues with filtering
- **github_get_issue** - Get detailed issue information
- **github_create_issue** - Create new issues
- **github_add_comment** - Add comments to issues or pull requests

**Configuration:**
```bash
# Enable GitHub tools
GITHUB_TOOLS_ENABLED=true

# Configure repositories (JSON array)
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

**Repository Configuration Fields:**
- `owner`: GitHub username or organization (required)
- `repo`: Repository name (required)
- `label`: Display name in UI (required)
- `token`: GitHub Personal Access Token (required)
- `protected_branches`: Branches that cannot be committed to (default: `["main", "master"]`)
- `capabilities`: Allowed operations (default: `["read", "branch", "commit", "pr", "issue"]`)
- `local_clone_path`: Path to local clone for faster operations (optional)
- `commit_author_name`: Name to use for commit author attribution (optional)
- `commit_author_email`: Email to use for commit author attribution (optional)

**Capabilities:**
- `read`: Read files, list contents, search code, view PRs/issues
- `branch`: Create new branches
- `commit`: Commit file changes (blocked on protected branches)
- `pr`: Create and manage pull requests
- `issue`: Create and manage issues

**Security Features:**
- Protected branch enforcement (cannot commit directly to main/master)
- Per-repository capability restrictions
- Rate limit tracking per token
- Binary file detection (returns info instead of content)
- Large file handling via Git Data API (files > 1MB)

**Rate Limiting:**
- Rate limits are tracked per-token using response headers
- Current limits visible in settings modal with progress bars
- Automatic rate limit info attached to tool responses

**Technical Notes:**
- GitHub tools are only available for Anthropic (Claude) and OpenAI (GPT) models
- Tools are registered at module load time via `register_github_tools()`
- All API requests use Bearer token authentication
- Large files (>1MB) are fetched via Git Data API to avoid content limits

**GitHub Tool Efficiency:**

The GitHub tools are designed to minimize API calls and token usage:

- **Start with github_explore** when working with a new repository. It provides repo metadata, file tree (depth 2), and key documentation files (README.md, CLAUDE.md, etc.) in one call.

- **Use github_tree** instead of repeated github_list_contents calls to see the full directory structure. Returns a formatted tree view with file sizes.

- **Batch file reads** with github_get_files when you need to read multiple files. Fetches up to 10 files in parallel, more efficient than multiple github_get_file calls.

- **Large files are automatically truncated** to 500 lines with a structure summary (function/class counts). Use start_line/end_line parameters to read specific sections.

- **Responses are cached** within a conversation session:
  - Tree structure: 5 minutes TTL
  - File contents: 10 minutes TTL
  - Repository metadata: 10 minutes TTL
  - PR/Issue lists: 2 minutes TTL

- **Use bypass_cache=true** if you need fresh data after making changes. Cache is automatically invalidated when you commit or delete files.

### Entity Notes System

The application supports **persistent notes** for AI entities, allowing them to maintain structured information across conversations that persists on disk.

**Available Notes Tools:**
- **notes_read** - Read a note file from private notes or shared folder
- **notes_write** - Write or update a note file (creates if doesn't exist)
- **notes_delete** - Delete a note file (cannot delete index.md)
- **notes_list** - List all note files with sizes and modification dates

**Key Features:**
- **Private Notes** - Each entity has their own folder: `{notes_base_dir}/{entity_label}/`
- **Shared Notes** - Folder accessible to all entities: `{notes_base_dir}/shared/`
- **Auto-Injection** - Each entity's `index.md` is automatically loaded into their context at conversation start
- **Allowed File Types** - `.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`

**How Entity Notes Work:**

1. **Directory Structure:**
   ```
   notes/
   ├── Claude/              # Private notes for "Claude" entity
   │   ├── index.md         # Auto-loaded into Claude's context
   │   └── research.md
   ├── GPT/                 # Private notes for "GPT" entity
   │   └── index.md
   └── shared/              # Shared notes (all entities can access)
       └── index.md
   ```

2. **Context Injection**: When a conversation starts, the system automatically reads:
   - The entity's private `index.md` (if it exists)
   - The shared `index.md` (if it exists)
   - Both are injected into the context, giving the entity persistent "always-on" information

3. **Tool Access**: Entities can read, write, and manage their notes during conversations using the notes tools

**Configuration:**
```bash
# Enable entity notes (default: true)
NOTES_ENABLED=true

# Base directory for notes storage (default: ./notes)
NOTES_BASE_DIR=./notes
```

**Important Notes:**
- Notes are accessed via AI tools only (no REST API endpoints for notes)
- The `index.md` file cannot be deleted (use notes_write to clear it instead)
- Entity labels are sanitized for filesystem safety (special characters replaced with underscores)
- Notes tools are in the `MEMORY` category and are only available for Anthropic (Claude) and OpenAI (GPT) models

### Memory Query Tool (Deliberate Recall)

The application provides a **memory_query** tool that allows AI entities to intentionally search their memories beyond automatic retrieval.

**Available Tool:**
- **memory_query** - Search memories by semantic similarity for deliberate recall

**Key Features:**
- Returns memories ranked by pure semantic similarity (not re-ranked by significance)
- Excludes current conversation from results
- Updates retrieval tracking (`times_retrieved`, `last_retrieved_at`) so intentional queries influence future automatic recall
- Supports 1-10 results per query

**How It Differs from Automatic Retrieval:**
- Automatic retrieval happens on every message and re-ranks by significance
- `memory_query` gives the entity direct control over when and what to recall
- Useful when the entity wants to explore specific topics in their memory

**Technical Notes:**
- Registered via `register_memory_tools()` in `services/__init__.py`
- Tool is in the `MEMORY` category
- Only available for Anthropic (Claude) and OpenAI (GPT) models

### Whisper Speech-to-Text (STT)

The application supports **local speech-to-text** using OpenAI's Whisper model via the `faster-whisper` library. This enables voice input in the research interface.

**Configuration:**
```bash
# Enable Whisper STT (requires running the Whisper server separately)
WHISPER_ENABLED=true                    # Enable local Whisper STT
WHISPER_API_URL=http://localhost:8030   # Whisper server URL
WHISPER_MODEL=large-v3                  # Model size (see options below)
DICTATION_MODE=auto                     # "whisper", "browser", or "auto"
```

**Available Models:**
| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| `large-v3` | ~3GB | Slowest | Best |
| `distil-large-v3` | ~1.5GB | Fast | Very Good |
| `medium` | ~1.5GB | Medium | Good |
| `small` | ~500MB | Fast | Decent |
| `base` | ~150MB | Very Fast | Basic |
| `tiny` | ~75MB | Fastest | Lowest |

**Dictation Modes:**
- `whisper` - Always use local Whisper server
- `browser` - Use browser's Web Speech API (requires Chrome/Edge)
- `auto` - Use Whisper if available, fall back to browser

**Running the Whisper Server:**
```bash
cd backend

# Step 1: Install PyTorch (same as TTS servers)
# For NVIDIA GPU with CUDA:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# For CPU only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install Whisper dependencies
pip install -r requirements-whisper.txt

# Step 3: Run the server (Option A: Using launcher script - recommended)
./start-whisper.sh       # Linux/macOS
start-whisper.bat        # Windows

# Step 3: Run the server (Option B: Manual activation)
source venv/bin/activate  # Windows: venv\Scripts\activate
python run_whisper.py
# Or with custom port:
python run_whisper.py --port 8030
```

The server will:
1. Download the specified Whisper model on first run
2. Start on port 8030 (default)
3. Apply GPU optimizations if CUDA is available

**Technical Notes:**
- Uses `faster-whisper` (CTranslate2-based, 4x faster than original Whisper)
- Supports automatic language detection
- Context hints via `initial_prompt` parameter improve accuracy
- GPU strongly recommended for `large-v3` model
- Windows users: CUDA DLL paths are auto-configured

### Conversation Archiving

Conversations can be **archived** to hide them from the main list while preserving their data.

**Behavior:**
- Archived conversations are hidden from the main conversation list
- Archived conversations are excluded from memory retrieval (AI won't recall memories from archived conversations)
- Archived conversations can be viewed via the archived list
- Archived conversations can be restored (unarchived) at any time

**API Endpoints:**
- `GET /api/conversations/archived` - List all archived conversations
- `POST /api/conversations/{id}/archive` - Archive a conversation
- `POST /api/conversations/{id}/unarchive` - Restore an archived conversation

**Use Cases:**
- Clearing clutter from the conversation list
- Temporarily excluding certain conversations from memory retrieval
- Organizing completed research phases

### External Conversation Import

The application supports **importing conversations from external sources** (OpenAI and Anthropic exports).

**Supported Formats:**
- OpenAI conversation exports (JSON format)
- Anthropic conversation exports (JSON format)
- Auto-detection of format based on structure

**How It Works:**
1. Upload conversation export file via preview endpoint
2. System parses and validates the format
3. Import stores messages to the selected entity's Pinecone index
4. Imported conversations are marked with `is_imported=True`
5. Messages become searchable memories but conversation is hidden from list

**API Endpoints:**
- `POST /api/conversations/import-external/preview` - Preview import before committing
- `POST /api/conversations/import-external` - Import conversation
- `POST /api/conversations/import-external/stream` - Stream-based import (SSE for progress)

**Important Notes:**
- Imported conversations are hidden from the conversation list (like archived)
- Messages ARE stored to Pinecone and become retrievable memories
- Useful for migrating conversation history from other platforms
- Entity must be selected before import (memories go to that entity's index)

### Response Regeneration

The application supports **regenerating AI responses** via a dedicated endpoint.

**How It Works:**
- Given an assistant message ID: deletes the old response and generates a new one
- Given a human message ID: generates a new response for that human message
- Supports multi-entity conversations (can change responding entity on regeneration)

**API Endpoint:**
- `POST /api/chat/regenerate` - Regenerate response (SSE stream)

**Request Parameters:**
```python
{
    "message_id": "uuid",              # Assistant or human message ID
    "responding_entity_id": "string"   # Optional: for multi-entity, select different responder
}
```

**Use Cases:**
- Getting a different response without resending the message
- Correcting entity selection in multi-entity conversations
- Exploring alternative continuations

### Per-Entity System Prompts

Multi-entity conversations support **different system prompts for each entity**.

**How It Works:**
- Store per-entity prompts in conversation's `entity_system_prompts` field
- Each entity receives their specific prompt when responding
- Overrides the global system prompt for that entity

**Database Field:**
```python
entity_system_prompts: Optional[Dict[str, str]] = None
# Example: {"claude-main": "You are...", "gpt-research": "You are..."}
```

**Use Cases:**
- Different research contexts for different entities
- Comparative studies with controlled prompt variations
- Entity-specific behavioral guidance

### External Event System

The application supports an **External Event System** that enables AI entities to receive and respond to events from external services asynchronously.

**Architecture Layers:**

1. **EventService** (`app/services/event_service.py`):
   - Manages event listener lifecycle (startup/shutdown)
   - Routes incoming events to appropriate handlers
   - Tracks events in database for auditing

2. **BaseEventListener** (`app/services/event_listeners/base.py`):
   - Abstract base class for all event listeners
   - Implements connection management with exponential backoff reconnection
   - Provides event emission to the EventService

3. **Event Storage**:
   - `external_events` table tracks all received events
   - Conversations can link to external resources via `external_link_type`, `external_link_id`, `external_link_metadata`

**Key Features:**
- Automatic reconnection with exponential backoff (1s to 5 minutes)
- Event tracking with status (pending, processing, completed, failed, skipped)
- Extensible listener pattern for adding new external services

**API Endpoints:**
- `GET /api/events/status` - Get status of all event listeners

### OGS (Online-Go Server) Integration

The application integrates with **OGS (Online-Go.com)** to enable AI entities to play Go games in real-time.

**Dual-Channel Model:**
- **Game Channel (Mechanical)**: Moves occur asynchronously. The entity receives notifications when it's their turn and responds with moves.
- **Conversation Channel (Relational)**: A linked conversation where participants communicate freely, not gated by move events.

**Setup Guide:**

**IMPORTANT:** OGS has a specific process for bot accounts that differs from standard OAuth.

1. **Create an OGS account for your bot:**
   - Go to https://online-go.com and click "Sign Up"
   - Create a NEW account specifically for your bot (not your personal account)
   - Use a username that clearly identifies it as a bot (e.g., "MyBot" or "AIResearchBot")
   - Verify your email address
   - Note down this username for `OGS_BOT_USERNAME`

2. **Get your bot account flagged as a bot (REQUIRED):**
   - Contact an OGS moderator to request that your bot account be flagged
   - You can do this via:
     - The OGS forums: https://forums.online-go.com/ (post in "OGS Development" or "Help")
     - The OGS chat on the website
   - Explain that you're developing a Go-playing bot and need bot API access
   - Wait for moderator approval (this may take some time)

3. **Generate an API Key (after bot account is approved):**
   - Log into OGS with your **HUMAN account** (not the bot account)
   - Search for your bot account and visit its profile page
   - On the bot's profile page, you should see an option to generate an API key
   - Generate and copy the API key - store it securely

4. **Configure environment variables** (see Configuration below)

**Alternative: OAuth Authentication (may have limited support)**

If API key authentication doesn't work for your use case, you can try OAuth:
- Log into OGS with your bot account
- Go to https://online-go.com/oauth2/applications/
- Create a new application with Client Type "Confidential" and Grant Type "Client credentials"
- Note: OAuth client_credentials may not be fully supported by OGS

**Troubleshooting:**
- **Error "invalid_client" or 403 "permission denied"**: Your bot account may not be properly flagged as a bot. Contact OGS moderators.
- **Can't find API key option on bot profile**: The bot account hasn't been flagged yet. Complete Step 2 first.
- **No game events received**: Ensure the bot account has active games

For more information, see the official [gtp2ogs documentation](https://github.com/online-go/gtp2ogs).

**Configuration:**
```bash
# Enable OGS integration
OGS_ENABLED=true
OGS_API_KEY=your_api_key           # Recommended method
OGS_BOT_USERNAME=your_bot_username
OGS_ENTITY_ID=entity_pinecone_index_name

# Alternative: OAuth (may have limited support)
# OGS_CLIENT_ID=your_client_id
# OGS_CLIENT_SECRET=your_client_secret

# Optional settings
OGS_API_URL=https://online-go.com
OGS_SOCKET_URL=https://online-go.com
OGS_AUTO_ACCEPT_CHALLENGES=true
OGS_ACCEPTED_BOARD_SIZES=9,13,19
OGS_ACCEPTED_TIME_CONTROLS=live,correspondence,blitz
```

**Initiating Games:**

*Option A: Challenge the bot from another OGS account*
1. Log into OGS with a different account (not the bot account)
2. Visit the bot's profile at `https://online-go.com/player/{bot_user_id}`
3. Click "Challenge" and configure the game settings
4. The bot will auto-accept if `OGS_AUTO_ACCEPT_CHALLENGES=true` and the settings match accepted board sizes and time controls

*Option B: Create a game via OGS website with the bot account*
1. Log into OGS as the bot
2. Go to "Play" and create or accept a game
3. The application will detect the game on next startup or via socket events

**Linking Games to Conversations:**
- Use the REST API to link games to conversations:
  - `POST /api/games/{game_id}/link` with body `{"conversation_id": "uuid"}`
- Once linked, the AI's move commentary appears in the conversation
- The conversation channel is for free-form discussion (not move-gated)

**How It Works:**

1. **Connection**: OGSEventListener connects to OGS via socket.io on application startup
2. **Game Events**: Receives move notifications, phase changes, challenges
3. **Move Generation**: When it's the entity's turn:
   - Fetches current board state
   - Builds ASCII representation
   - Loads Go learning notes (if available)
   - Sends to LLM for move generation
   - Parses response for `MOVE: <coordinate>` format
   - Submits move to OGS
4. **Commentary**: Optional commentary is posted to the linked conversation

**Board Representation:**
```
    A B C D E F G H J K L M N O P Q R S T
19  . . . . . . . . . . . . . . . . . . .  19
18  . . . . . . . . . . . . . . . . . . .  18
17  . . . + . . . . . + . . . . . + . . .  17
...
 1  . . . . . . . . . . . . . . . . . . .   1
    A B C D E F G H J K L M N O P Q R S T
```

- `.` = empty intersection
- `X` = black stone
- `O` = white stone
- `+` = star point (hoshi)

**Move Format:**
The entity responds with: `MOVE: D4`, `MOVE: pass`, or `MOVE: resign`

**API Endpoints:**
```
GET /api/games                           # List active games
GET /api/games/{game_id}                 # Get game details with board
POST /api/games/{game_id}/link           # Link game to conversation
DELETE /api/games/{game_id}/link         # Unlink game from conversation
GET /api/games/{game_id}/conversation    # Get linked conversation
GET /api/games/conversation/{id}/board   # Get ephemeral board state
```

**Go Learning Notes:**
- Entities can maintain a `go-notes.md` file in their notes folder
- Contains observations about opponents, personal tendencies, strategic patterns
- Automatically injected into every game move prompt
- Entity updates notes via existing `notes_write` tool

**Message Consolidation:**
For game-linked conversations, consecutive same-role messages are consolidated before API calls to respect Anthropic's alternating user/assistant requirement. Database storage remains separate.

**Technical Notes:**
- Requires `python-socketio[asyncio_client]>=5.10.0`
- OGS uses OAuth client credentials authentication
- Game subscriptions managed per-game via socket.io
- Reconnection handled automatically with exponential backoff

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
│   │   │   ├── conversation_memory_link.py
│   │   │   └── external_event.py  # External event tracking
│   │   ├── routes/            # FastAPI endpoint routers
│   │   │   ├── conversations.py  # Includes archive/import endpoints
│   │   │   ├── chat.py           # Includes regenerate endpoint
│   │   │   ├── memories.py
│   │   │   ├── entities.py
│   │   │   ├── messages.py    # Individual message edit/delete
│   │   │   ├── tts.py         # Text-to-speech endpoints
│   │   │   ├── stt.py         # Speech-to-text endpoints
│   │   │   ├── github.py      # GitHub integration endpoints
│   │   │   └── games.py       # OGS game management endpoints
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
│   │   │   ├── tts_service.py        # Unified TTS (ElevenLabs/XTTS/StyleTTS2)
│   │   │   ├── xtts_service.py       # Local XTTS v2 client service
│   │   │   ├── styletts2_service.py  # Local StyleTTS 2 client service
│   │   │   ├── whisper_service.py    # Local Whisper STT client service
│   │   │   ├── event_service.py      # External event system management
│   │   │   ├── ogs_service.py        # OGS (Online-Go Server) API client
│   │   │   └── event_listeners/      # Event listener implementations
│   │   │       ├── base.py           # BaseEventListener abstract class
│   │   │       └── ogs_listener.py   # OGS socket.io listener
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
| AI Integration | Google GenAI SDK | 1.0.0+ | Gemini API client |
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
- **OpenAI/Google**: Tools not passed (architectural decision)

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

**Required Variables:**
```bash
ANTHROPIC_API_KEY=sk-ant-...  # Required for Anthropic/Claude models
```

**Optional Variables:**
```bash
OPENAI_API_KEY=sk-...                   # Enables OpenAI/GPT models
GOOGLE_API_KEY=...                      # Enables Google/Gemini models
PINECONE_API_KEY=...                    # Enables memory system
PINECONE_INDEXES='[...]'                # Entity configuration (JSON array, see below)
HERE_I_AM_DATABASE_URL=sqlite+aiosqlite:///./here_i_am.db  # Database URL
DEBUG=true                              # Development mode

# Tool Use (web search/fetch for Claude models)
TOOLS_ENABLED=true                      # Enable tool use (default: true)
BRAVE_SEARCH_API_KEY=...                # Required for web_search tool
TOOL_USE_MAX_ITERATIONS=10              # Max agentic loop iterations (default: 10)

# GitHub Integration (optional, repository access for AI entities)
GITHUB_TOOLS_ENABLED=true               # Enable GitHub tools
GITHUB_REPOS='[...]'                    # Repository configuration (JSON array, see below)

# Entity Notes (optional, persistent notes for AI entities)
NOTES_ENABLED=true                      # Enable entity notes (default: true)
NOTES_BASE_DIR=./notes                  # Base directory for notes storage

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

# StyleTTS 2 Local TTS (optional, local GPU-accelerated text-to-speech with voice cloning)
# Requires running the StyleTTS 2 server separately (see "Running StyleTTS 2 Server" below)
# StyleTTS 2 takes priority over XTTS and ElevenLabs if enabled
# STYLETTS2_ENABLED=true                # Enable local StyleTTS 2 (highest priority)
# STYLETTS2_API_URL=http://localhost:8021  # StyleTTS 2 server URL
# STYLETTS2_VOICES_DIR=./styletts2_voices  # Directory for cloned voice samples
# STYLETTS2_DEFAULT_SPEAKER=/path/to/sample.wav  # Default speaker sample (optional)
# STYLETTS2_PHONEMIZER=gruut            # "gruut" (default, no deps) or "espeak" (requires espeak-ng)

# Whisper STT (optional, local GPU-accelerated speech-to-text)
# Requires running the Whisper server separately (see "Running the Whisper Server")
# WHISPER_ENABLED=true                  # Enable local Whisper STT
# WHISPER_API_URL=http://localhost:8030 # Whisper server URL
# WHISPER_MODEL=large-v3                # Model: large-v3, distil-large-v3, medium, small, base, tiny
# DICTATION_MODE=auto                   # "whisper", "browser", or "auto"

# Memory System Enhancement
# USE_MEMORY_IN_CONTEXT=false           # Insert memories directly into conversation context (experimental)
# MEMORY_ROLE_BALANCE_ENABLED=true      # Ensure memories include both human and assistant messages (default: true)
```

**Entity Configuration (PINECONE_INDEXES):**
```bash
# Configure AI entities with separate memory spaces (JSON array)
# Each entity requires a pre-created Pinecone index with dimension=1024 and integrated inference (llama-text-embed-v2)
PINECONE_INDEXES='[
  {"index_name": "claude-main", "label": "Claude", "description": "Primary AI", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929", "host": "https://claude-main-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gpt-research", "label": "GPT", "description": "OpenAI for comparison", "llm_provider": "openai", "default_model": "gpt-5.1", "host": "https://gpt-research-xxxxx.svc.xxx.pinecone.io"},
  {"index_name": "gemini-research", "label": "Gemini", "description": "Google for comparison", "llm_provider": "google", "default_model": "gemini-2.5-flash", "host": "https://gemini-research-xxxxx.svc.xxx.pinecone.io"}
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

# Option A: Using launcher script (recommended)
./start-xtts.sh      # Linux/macOS
start-xtts.bat       # Windows

# Option B: Manual activation
source venv/bin/activate  # Windows: venv\Scripts\activate
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

### Running StyleTTS 2 Server (Optional Local TTS)

StyleTTS 2 provides local, GPU-accelerated text-to-speech with voice cloning capabilities. It uses a different approach than XTTS, focusing on style transfer for more expressive speech synthesis. It runs as a separate server process.

**Prerequisites:**
- NVIDIA GPU with CUDA support (strongly recommended) or CPU (much slower)
- Python 3.9-3.11 (Python 3.12+ may have compatibility issues)
- espeak-ng installed (only if using espeak phonemizer; gruut is the default and requires no system deps)

**Phonemizer Options:**
The server supports two phonemizer backends, controlled by `STYLETTS2_PHONEMIZER` environment variable:

| Backend | Pros | Cons |
|---------|------|------|
| **gruut** (default) | MIT licensed, pure Python, no system deps | Slightly lower quality in some edge cases |
| **espeak** | Higher quality phonemization | Requires espeak-ng system package |

**Installation with Gruut (Recommended - No System Dependencies):**
```bash
cd backend

# Step 1: Install PyTorch (choose one based on your hardware)
# For NVIDIA GPU with CUDA:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# For CPU only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install StyleTTS 2 dependencies
pip install -r requirements-styletts2.txt
# That's it! gruut is installed automatically and is the default phonemizer.
```

**Installation with Espeak (Linux/macOS):**
```bash
cd backend

# Step 1: Install PyTorch (choose one based on your hardware)
# For NVIDIA GPU with CUDA:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# For CPU only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install espeak-ng system package
# Ubuntu/Debian:
sudo apt install espeak-ng
# macOS:
brew install espeak-ng

# Step 3: Install StyleTTS 2 dependencies
pip install -r requirements-styletts2.txt

# Step 4: Set phonemizer to espeak in .env
# STYLETTS2_PHONEMIZER=espeak
```

**Installation with Espeak (Windows):**
```powershell
cd backend

# Step 1: Install PyTorch (choose one based on your hardware)
# For NVIDIA GPU with CUDA:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# For CPU only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 2: Install espeak-ng
# Download from: https://github.com/espeak-ng/espeak-ng/releases
# Run the installer (e.g., espeak-ng-X.XX-x64.msi)
# The installer adds espeak-ng to PATH automatically

# Step 3: Set PHONEMIZER_ESPEAK_LIBRARY environment variable
# Option A: Set temporarily in current session
$env:PHONEMIZER_ESPEAK_LIBRARY = "C:\Program Files\eSpeak NG\libespeak-ng.dll"

# Option B: Set permanently (run as Administrator)
[System.Environment]::SetEnvironmentVariable("PHONEMIZER_ESPEAK_LIBRARY", "C:\Program Files\eSpeak NG\libespeak-ng.dll", "Machine")

# Step 4: Install StyleTTS 2 dependencies
pip install -r requirements-styletts2.txt

# Step 5: Set phonemizer to espeak in .env
# STYLETTS2_PHONEMIZER=espeak
```

**Troubleshooting:**
- If using espeak and phonemizer fails to find espeak-ng, verify the DLL path exists and update `PHONEMIZER_ESPEAK_LIBRARY` accordingly
- Visual Studio Build Tools may be required for some dependencies on Windows
- Python 3.9-3.11 recommended (3.12+ may have compatibility issues)

**Running the StyleTTS 2 Server:**
```bash
cd backend

# Option A: Using launcher script (recommended)
./start-styletts2.sh     # Linux/macOS
start-styletts2.bat      # Windows

# Option B: Manual activation
source venv/bin/activate  # Windows: venv\Scripts\activate
python run_styletts2.py
# Or with custom port:
python run_styletts2.py --port 8021
```

The server will:
1. Auto-download StyleTTS 2 models from HuggingFace on first run (~1GB total)
2. Start on port 8021 (default)
3. Apply GPU optimizations if CUDA is available

**Note:** The `styletts2` Python package handles model downloads automatically. No manual model download is required.

**Configure Main App to Use StyleTTS 2:**
```bash
# In .env
STYLETTS2_ENABLED=true
STYLETTS2_API_URL=http://localhost:8021
STYLETTS2_VOICES_DIR=./styletts2_voices
# Phonemizer: "gruut" (default, no system deps) or "espeak" (requires espeak-ng)
STYLETTS2_PHONEMIZER=gruut
```

**Voice Cloning:**
StyleTTS 2 supports voice cloning from audio samples. Upload a 6-30 second WAV file of clear speech via the `/api/tts/voices/clone` endpoint or through the UI. Cloned voices are stored in `STYLETTS2_VOICES_DIR` and persisted in `voices.json`.

**StyleTTS 2 Voice Parameters:**
- `alpha` (0.0-1.0): Timbre diversity - higher = more diverse timbre (default: 0.3)
- `beta` (0.0-1.0): Prosody diversity - higher = more diverse prosody/emotion (default: 0.7)
- `diffusion_steps` (1-50): Quality vs speed tradeoff - higher = better quality but slower (default: 10)
- `embedding_scale` (0.0-10.0): Classifier free guidance scale (default: 1.0)

**Pronunciation Fixes:**
StyleTTS 2 occasionally mispronounces certain words. The server includes a pronunciation fix system that automatically replaces problematic words with phonetic spellings before synthesis. Configure via the `STYLETTS2_PRONUNCIATION_FIXES` environment variable (JSON object):
```bash
# In .env
STYLETTS2_PRONUNCIATION_FIXES='{"turned": "turnd", "learned": "lernd", "burned": "burnd", "earned": "ernd", "into": "in to"}'
```
If not set, default fixes are used. Set to `{}` to disable all fixes. Fixes are applied with case-insensitive word boundary matching and preserve the original case pattern.

**Speaker Embedding Caching:**
The StyleTTS 2 server caches speaker embeddings (computed from reference audio) based on file content hash. This dramatically speeds up repeat TTS requests for the same voice. Pre-load voices on startup via:
```bash
STYLETTS2_PRELOAD_SPEAKERS=/path/to/voice1.wav,/path/to/voice2.wav
```

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
- Tools are available for Anthropic (Claude) and OpenAI (GPT) models
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

# External link fields (for connecting to external services like OGS)
external_link_type: String (nullable)  # Type of link: "ogs_game", "github_issue", etc.
external_link_id: String (nullable)  # ID in external service (e.g., OGS game ID)
external_link_metadata: JSON (nullable)  # Additional metadata about the link

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

### External Events Table

```python
id: UUID (PK)
created_at: DateTime
processed_at: DateTime (nullable)

# Event identification
source: String  # e.g., "ogs", "github"
event_type: String  # e.g., "game_move", "challenge"
external_id: String (indexed)  # External resource ID (e.g., OGS game ID)

# Event data
payload: JSON (nullable)  # Raw event data from external service

# Processing status
status: Enum (PENDING, PROCESSING, COMPLETED, FAILED, SKIPPED)
error_message: Text (nullable)
retry_count: Integer (default: 0)

# Response tracking
response_message_id: UUID (nullable)  # Message created in response
conversation_id: UUID (nullable)  # Associated conversation
entity_id: String (nullable)  # Entity that processed this event
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

### Games (OGS Integration)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/games/` | List active OGS games |
| GET | `/api/games/{game_id}` | Get game details with board |
| POST | `/api/games/{game_id}/link` | Link game to conversation |
| DELETE | `/api/games/{game_id}/link` | Unlink game from conversation |
| GET | `/api/games/{game_id}/conversation` | Get linked conversation |
| GET | `/api/games/conversation/{id}/board` | Get ephemeral board state |

### Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/events/status` | Get status of all event listeners |

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
14. **Text-to-Speech** - Listen to AI messages via ElevenLabs, XTTS, or StyleTTS 2 (optional)
15. **Speech-to-Text** - Voice input via Whisper or browser Web Speech API (optional)
16. **Message Actions** - Copy button, edit/delete for human messages
17. **Response Regeneration** - Regenerate AI responses with optional entity change
18. **Voice Selection** - Choose from configured or cloned voices in settings
19. **Voice Cloning** - Clone custom voices from audio samples (XTTS/StyleTTS 2)
20. **Tool Use Display** - Real-time tool execution with collapsible input/output details (Claude only)
21. **Stop Generation** - Cancel AI response mid-stream
22. **GitHub Settings** - View configured repositories and rate limits in settings modal
23. **Conversation Archiving** - Archive conversations to hide from list (accessible via archived view)
24. **Image/File Attachments** - Attach images and text files for multimodal conversations (drag-drop or picker)
25. **Attachment Preview** - See attached files before sending with remove capability

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

11. **TTS Service is Optional (Three Providers)**
    - **ElevenLabs (cloud):** Set `ELEVENLABS_API_KEY` to enable
    - **XTTS v2 (local):** Set `XTTS_ENABLED=true` and run the XTTS server
    - **StyleTTS 2 (local):** Set `STYLETTS2_ENABLED=true` and run the StyleTTS 2 server
    - Priority order: StyleTTS 2 > XTTS > ElevenLabs
    - Audio is not cached - each request generates fresh audio

12. **XTTS Server is Separate Process**
    - XTTS runs as a standalone FastAPI server on port 8020
    - Requires PyTorch and ~2GB for model download on first run
    - GPU (CUDA) strongly recommended for acceptable performance
    - Speaker latents are cached for repeat voice requests
    - Long text is automatically chunked (XTTS has 400 token limit)

13. **StyleTTS 2 Server is Separate Process**
    - StyleTTS 2 runs as a standalone FastAPI server on port 8021
    - Requires PyTorch; espeak-ng only needed if `STYLETTS2_PHONEMIZER=espeak`
    - Default phonemizer is gruut (MIT licensed, no system dependencies)
    - GPU (CUDA) strongly recommended for acceptable performance
    - Speaker embeddings are cached for repeat voice requests
    - Long text is automatically chunked (150 char limit per chunk)

14. **Multi-Entity Conversation Storage**
    - Multi-entity conversations use `entity_id="multi-entity"` as a marker value
    - Actual participating entities stored in `ConversationEntities` table
    - Messages are stored to ALL participating entities' Pinecone indexes
    - Human messages: `role="human"` for all entities
    - Assistant messages: `role="assistant"` for responding entity, `role="{speaker_label}"` for others
    - Memory retrieval only happens from the responding entity's index

15. **Multi-Entity Session State**
    - Session tracks `is_multi_entity`, `entity_labels`, and `responding_entity_label`
    - A special header is injected to identify participants to each entity
    - Continuation mode (no human message) supported for entity-to-entity flow

16. **Tool Use Provider Support**
    - Tools (web_search, web_fetch) work with Anthropic (Claude) and OpenAI (GPT) models
    - Google models do not currently receive tool schemas
    - Tool schemas are defined in Anthropic format, auto-converted for OpenAI
    - Tool results are not persisted to database (visible in conversation but not stored separately)

17. **Web Tools Require External API**
    - `web_search` requires `BRAVE_SEARCH_API_KEY` to function
    - `web_fetch` works independently (uses httpx to fetch URLs)
    - Both tools have timeouts (10s for search, 15s for fetch)
    - web_fetch includes smart HTML content extraction (removes nav, footer, scripts)

18. **GitHub Integration is Optional**
    - Set `GITHUB_TOOLS_ENABLED=true` and configure `GITHUB_REPOS` to enable
    - Each repository requires its own Personal Access Token
    - Protected branches (main/master by default) cannot be committed to directly
    - Rate limits are tracked per-token and displayed in settings
    - GitHub tools work with Anthropic (Claude) and OpenAI (GPT) models only

19. **Entity Notes System**
    - Notes are accessed via AI tools only (`notes_read`, `notes_write`, `notes_delete`, `notes_list`)
    - No REST API endpoints for notes - entities manage their own notes during conversations
    - Each entity's `index.md` is automatically injected into their context at conversation start
    - Shared `index.md` is also injected (accessible to all entities)
    - Entity labels are sanitized for filesystem safety (special characters replaced with underscores)
    - The `index.md` file cannot be deleted (use `notes_write` with empty content to clear it)
    - Notes tools are in the `MEMORY` category and work with Anthropic (Claude) and OpenAI (GPT) models

20. **Tool Exchange Message Persistence**
    - Tool exchanges (`TOOL_USE` and `TOOL_RESULT`) are now persisted to the database
    - Content is stored as JSON (unlike regular messages which are plain text)
    - The `is_tool_exchange` property identifies tool-related messages
    - The `content_blocks` property parses JSON content for tool exchanges
    - This enables conversation continuity when tool use spans multiple responses

21. **Whisper STT Server is Separate Process**
    - Whisper runs as a standalone FastAPI server on port 8030
    - Uses `faster-whisper` (CTranslate2-based, 4x faster than original Whisper)
    - GPU (CUDA) strongly recommended for `large-v3` model
    - Models are downloaded automatically on first run
    - Windows users: CUDA DLL paths are auto-configured in `run_whisper.py`

22. **Conversation Archiving Behavior**
    - Archived conversations are hidden from the main list
    - **Important:** Archived conversations are excluded from memory retrieval
    - This means the AI won't recall memories from archived conversations
    - Use archiving to temporarily "pause" certain conversation threads
    - Unarchiving restores the conversation and its memories to active status

23. **External Conversation Import**
    - Imported conversations are marked with `is_imported=True`
    - They are hidden from the conversation list (like archived)
    - However, their messages ARE stored to Pinecone as memories
    - This allows importing historical conversations without cluttering the UI
    - Supports both OpenAI and Anthropic export formats

24. **Memory Query Tool vs Automatic Retrieval**
    - Automatic retrieval re-ranks by significance (times_retrieved × recency × half_life)
    - `memory_query` tool returns pure semantic similarity ranking
    - Both update retrieval tracking, so intentional queries influence future retrieval
    - Use automatic retrieval for natural conversation flow
    - Use `memory_query` when the entity needs specific deliberate recall

25. **Memory-in-Context Mode (Experimental)**
    - Enable with `USE_MEMORY_IN_CONTEXT=true`
    - Memories are inserted directly into conversation history instead of separate block
    - Improves cacheability (memories paid for once per conversation)
    - Trade-off: Less clear separation between memories and conversation

26. **Image and File Attachments**
    - **Images are ephemeral** - NOT stored in conversation history or memories
    - **Text files are persisted** in conversation history (but NOT as searchable memories)
    - Text file content is stored with the human message using `[ATTACHED FILE: ...]` blocks
    - Images require vision-capable models (Claude Sonnet/Opus, GPT-4o, etc.)
    - For Google models, only text files are supported (images are skipped with a warning)
    - PDF extraction requires PyPDF2, DOCX requires python-docx
    - Frontend validates file types and sizes before upload
    - Backend re-validates attachments for security

27. **OGS (Online-Go Server) Integration**
    - Set `OGS_ENABLED=true` and configure OAuth credentials to enable
    - Requires `python-socketio[asyncio_client]>=5.10.0`
    - The OGSEventListener connects on application startup if configured
    - Uses OAuth client credentials flow for authentication
    - Game events are received via socket.io, moves submitted via REST API
    - Board state is converted to ASCII for LLM understanding
    - Moves parsed from `MOVE: <coordinate>` format in LLM response
    - Go learning notes (`go-notes.md`) are auto-injected into move prompts
    - Message consolidation combines consecutive same-role messages for API calls
    - Reconnection uses exponential backoff (1s to 5 minutes)

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
- Frontend tool display: `frontend/js/app.js` (addToolMessage)
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

**External Events & OGS:**
- Event service: `backend/app/services/event_service.py`
- Base listener: `backend/app/services/event_listeners/base.py`
- OGS listener: `backend/app/services/event_listeners/ogs_listener.py`
- OGS service: `backend/app/services/ogs_service.py`
- Games routes: `backend/app/routes/games.py`
- External event model: `backend/app/models/external_event.py`
- Session helpers (consolidation): `backend/app/services/session_helpers.py`
- Lifespan integration: `backend/app/main.py` (initialize_event_listeners)

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
- Frontend entity modal/responder: `frontend/js/app.js`

**Conversation Management:**
- Conversation model: `backend/app/models/conversation.py`
- Conversation routes: `backend/app/routes/conversations.py`
- Archive/unarchive: `backend/app/routes/conversations.py`
- External import: `backend/app/routes/conversations.py`
- Response regeneration: `backend/app/routes/chat.py`
- Database migration: `backend/migrate_multi_entity.py`

### Key Constants

```python
# Default models
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"  # Anthropic default
DEFAULT_OPENAI_MODEL = "gpt-5.1"  # OpenAI default
DEFAULT_GOOGLE_MODEL = "gemini-2.5-flash"  # Google default

# Supported OpenAI models include:
#   gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-4
#   gpt-5.1, gpt-5.2, gpt-5-mini, gpt-5.1-chat-latest
#   o1, o1-mini, o1-preview, o3, o3-mini, o4-mini

# Supported Google models include:
#   gemini-3.0-pro, gemini-3.0-flash
#   gemini-2.5-pro, gemini-2.5-flash
#   gemini-2.0-flash, gemini-2.0-flash-lite

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

# Role balance in memory retrieval
memory_role_balance_enabled = True  # When True, ensures at least one human and one assistant memory

# GPT-5.x verbosity setting (config.py)
default_verbosity = "medium"  # Options: "low", "medium", "high" for GPT-5.x models

# Tool use settings (config.py)
tools_enabled = True                  # Master switch for tool use
tool_use_max_iterations = 10          # Max agentic loop iterations
brave_search_api_key = ""             # Required for web_search tool

# Web tool limits (web_tools.py)
web_search_max_results = 20           # Brave API limit
web_search_timeout = 10               # Seconds
web_fetch_max_length = 50000          # Characters (50KB)
web_fetch_timeout = 15                # Seconds

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

# StyleTTS 2 defaults (config.py)
styletts2_enabled = False         # Must be explicitly enabled (highest priority)
styletts2_api_url = "http://localhost:8021"
styletts2_voices_dir = "./styletts2_voices"
styletts2_phonemizer = "gruut"    # "gruut" (default, no system deps) or "espeak"

# StyleTTS 2 voice synthesis defaults (styletts2_service.py)
alpha = 0.3                       # Timbre diversity (0-1)
beta = 0.7                        # Prosody diversity (0-1)
diffusion_steps = 10              # Quality vs speed (1-50)
embedding_scale = 1.0             # Classifier free guidance

# Entity Notes defaults (config.py)
notes_enabled = True              # Enable persistent notes for entities
notes_base_dir = "./notes"        # Base directory for notes storage
# Allowed file extensions: .md, .json, .txt, .html, .xml, .yaml, .yml

# Whisper STT defaults (config.py)
whisper_enabled = False           # Must be explicitly enabled
whisper_api_url = "http://localhost:8030"
whisper_model = "large-v3"        # Options: large-v3, distil-large-v3, medium, small, base, tiny
dictation_mode = "auto"           # "whisper", "browser", or "auto"

# Memory Context (experimental)
use_memory_in_context = False     # Insert memories directly into conversation context
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

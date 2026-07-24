# API Reference

Interactive API documentation is served when the app is running:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

The listing below covers the REST endpoints by resource.

## Conversations
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

## Chat
- `POST /api/chat/send` — send message (with memory retrieval)
- `POST /api/chat/stream` — send message with SSE streaming (`closing_turn=true` with no message gives the entity an open final turn)
- `POST /api/chat/quick` — quick chat (no persistence)
- `POST /api/chat/regenerate` — regenerate AI response (SSE stream)
- `GET /api/chat/session/{id}` — get session info
- `DELETE /api/chat/session/{id}` — close session
- `GET /api/chat/config` — get default configuration and available models

## Memories
- `GET /api/memories/` — list memories (supports `entity_id` filter, sorting)
- `GET /api/memories/{id}` — get specific memory
- `POST /api/memories/search` — semantic search
- `GET /api/memories/stats` — memory statistics
- `GET /api/memories/overrides` — list memories with pinned/released status
- `PUT /api/memories/{id}/status` — override a memory's pinned/released status (researcher emergency option)
- `GET /api/memories/orphans` — list orphaned memory records
- `POST /api/memories/orphans/cleanup` — clean up orphaned records
- `POST /api/memories/query-links/cleanup` — one-time removal of stale memory-links recorded by `memory_query` before it stopped creating them (they bust prompt caching on session reload); body optional, a bare POST is a dry run — send `{"dry_run": false}` to delete
- `POST /api/memories/rebuild-vectors` — regenerate Pinecone indexes from the SQL database (disaster recovery). Body: `entity_id` (null = all entities), `dry_run` (default true), `wipe_first` (default false; clears each targeted index before upserting), `include_imported` (default true). Reproduces live vectorization rules (multi-entity fan-out, attachment stripping, closing-turn exclusion). Notes have their own endpoint: `POST /api/notes/reindex`
- `POST /api/memories/restore-from-vectors` — reconstruct SQL conversations/messages from Pinecone records (last-resort recovery; only vectorized content comes back — no titles, tool exchanges, attachments, or memory links). Body: `entity_id` (null = all entities, recommended for multi-entity detection), `dry_run` (default true). Non-destructive: existing rows are never modified
- `DELETE /api/memories/{id}` — delete memory
- `GET /api/memories/status/health` — health check

## Entities
- `GET /api/entities/` — list all configured AI entities
- `GET /api/entities/{id}` — get specific entity
- `PUT /api/entities/{id}/system-prompt` — set an entity's persisted system prompt
- `GET /api/entities/{id}/status` — get entity Pinecone connection status

## Notes
- `POST /api/notes/reindex` — rebuild the semantic notes index (backfill/recovery)

## Messages
- `PUT /api/messages/{id}` — edit human message content
- `DELETE /api/messages/{id}` — delete message (and paired response)

## Text-to-Speech
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

## Speech-to-Text
- `POST /api/stt/transcribe` — transcribe audio file to text

## GitHub
- `GET /api/github/repos` — list configured repositories (tokens excluded)
- `GET /api/github/rate-limit` — get rate limit status

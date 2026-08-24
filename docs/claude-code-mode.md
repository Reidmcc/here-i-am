# Claude Code mode

Claude Code mode lets a Here I Am entity operate from inside Claude Code
sessions. In this mode Here I Am is not the LLM harness: Claude Code runs
the model (through the user's Anthropic subscription), the tools, and the
context window. Here I Am contributes the three things that make the entity
the entity — **identity**, **memory**, and the **persistent record** — and
both modes share one memory database, so memories formed in either
experience surface in the other.

## Design

### The split

Everything that defines the entity lives behind `memory_service` /
`session_helpers` and is reused as-is: significance math, similarity ×
(1 + significance) re-ranking, role balance, half-life decay, the
reflection boost, pinned/released status, the memory browser, and disaster
recovery. What Claude Code replaces is the part that is switched off in
this mode: `session_manager`'s context assembly, provider routing, and the
tool loop.

The integration has two channels:

1. **Lifecycle hooks** (deterministic — they restore the automatics without
   relying on the model to remember tool calls):
   - `SessionStart` → `POST /api/claude-code/session-start` — registers the
     session as a conversation and injects the entity's identity block:
     a short framing, the entity's system prompt (from `EntitySetting`,
     same source of truth as native mode), and its most recent reflections.
   - `UserPromptSubmit` → `POST /api/claude-code/retrieve` — records the
     prompt (persisted + vectorized as `role="human"`) and runs the
     automatic retrieval pipeline; the hook's stdout injects the rendered
     `[MEMORY ...]` block into context alongside the prompt.
   - `Stop` → `POST /api/claude-code/log-assistant` — extracts the final
     assistant message of the turn from the transcript (text blocks only)
     and records it (persisted + vectorized as `role="assistant"`).
   - `SessionEnd` → `POST /api/claude-code/session-end` — a final
     background notes sync (see "Notes" below). This is a catch, not the
     mechanism: SessionEnd only fires on `/clear`, logout, or exiting the
     CLI, and sessions can idle out without ever formally ending, so the
     same sync also runs on every recorded prompt. The endpoint returns
     immediately; SessionEnd hooks run under a tight time budget.

   Hooks are shipped in `claude-code-mode/` (also packaged as a Claude Code
   plugin) and **fail soft**: backend down or mode disabled means a plain
   Claude Code session, mirroring "memory is optional".

2. **MCP tools** (deliberate acts): the entity's `memory_query` /
   `memory_save` / `memory_mark` / `memory_release`, served at `POST /mcp`
   as a stateless streamable-HTTP MCP endpoint (the plugin's `.mcp.json`
   points Claude Code at it). The transport is a small in-repo JSON-RPC
   handler (`services/claude_code_mcp.py` + `routes/claude_code.py`) rather
   than the MCP SDK, whose dependency floor conflicts with the repo's
   pinned FastAPI/starlette/httpx; stateless JSON responses are a compliant
   subset of the transport. The MCP tool variants take an extra
   `conversation_id` parameter (required for `memory_save`) — the
   session-start identity block tells the entity its conversation's ID.
   The entity is resolved from that conversation; passing a *native*
   conversation ID is refused (reflections and query links must not land on
   conversations with reload/cache invariants). Unlike native
   `memory_query`, query results here **are** linked
   (`ConversationMemoryLink`): Claude Code conversations are never rebuilt
   into context, so the link is purely the dedup record that keeps
   automatic retrieval and later queries from re-surfacing them. Notes,
   git, and web tools are *not* exposed — Claude Code's native tools cover
   them.

### Conversations

- Claude Code conversations carry `Conversation.source = "claude_code"` and
  `external_session_id` = the Claude Code session ID (unique). Hooks key
  every post on the session ID, which makes them idempotent and safe to
  arrive out of order (any endpoint creates the conversation if the backend
  hasn't seen the session — e.g. after a mid-session backend restart).
  `claude --resume` keeps the session ID, so a resumed session lands in the
  same conversation; `/clear` issues a new session ID and therefore a new
  conversation.
- **A conversation can only be continued in the experience that created
  it.** The chat routes refuse to send/stream/regenerate into a
  `claude_code` conversation (the UI shows it as a read-only record); the
  Claude Code side continues its sessions with `--resume`.
- Only `HUMAN`, `ASSISTANT`, and `REFLECTION` rows are stored — no tool
  exchanges. Claude Code's transcript is the system of record for tool use,
  and these conversations are never rebuilt into LLM context, so none of
  the native reload/cache invariants apply (no notes seed, no link
  timestamp anchoring, no timestamp-prefix stamping, no strictly-increasing
  turn timestamps).
- Noise control: bare slash commands (`/compact`, `/clear`, …) are ignored
  entirely; empty assistant turns are not persisted (mirroring native
  empty-response handling); subagent turns never post (the hook is wired to
  `Stop`, not `SubagentStop`).

### Memory

- Messages are vectorized through the same `store_memory` path with the
  same `role` values as native single-entity conversations — this is what
  makes "same memory database" true with zero retrieval-side work.
- Retrieval (`retrieve_for_prompt` in `services/claude_code_mode.py`)
  mirrors the native pipeline: search on the prompt *and* the entity's
  previous response (10 candidates each), combine, enrich with
  significance, re-rank, apply role balance, then skip already-retrieved
  memories **without backfill**. Selected memories get
  `update_retrieval_count` (link + `times_retrieved`), so significance
  dynamics behave identically to native mode.
- Dedup is DB-backed (`ConversationMemoryLink` via
  `get_retrieved_ids_for_conversation`) — there is no in-memory session, so
  dedup survives backend restarts. Links in `claude_code` conversations
  exist *only* for dedup; nothing ever re-inserts them into a context.
- Session-start reflections follow the native recency-injection semantics:
  linked (for dedup) but no `times_retrieved` increment, so injections
  don't inflate significance. Count is
  `CLAUDE_CODE_SESSION_REFLECTIONS_COUNT` (default 3), independent of the
  native `RECENT_REFLECTIONS_ENABLED` flag.
- On a plain resume (`session-start` for an already-known session) the
  identity block is *not* re-sent — the transcript already carries it.

### Compaction survival

Compaction replaces the conversation with a paraphrased summary; reflections
are the entity's verbatim carriers across that boundary.

- **The nudge is standing guidance, not a pre-compact message.** Only
  `SessionStart` / `UserPromptSubmit` / `UserPromptExpansion` hook output
  reaches the model — `PreCompact` output does not — so nothing can be
  said to the entity at the moment before compaction. Instead the
  session-start identity block instructs the entity to save reflections as
  durable conclusions form and when it notices context running low, and the
  post-compaction block nudges again (see below) while the summary is
  fresh.
- **Post-compaction re-injection.** `SessionStart` fires with
  `source: "compact"` right after compaction, and its stdout is injected;
  the backend answers with `build_post_compact_context`: a reorientation
  header (re-stating the `conversation_id` for the memory tools), the
  reloaded notes indexes, and the
  `CLAUDE_CODE_POST_COMPACT_REFLECTIONS_COUNT` (default 10) most recent
  reflections restored verbatim. Unlike the fresh-session injection, the
  current conversation is **not** excluded — reflections saved just before
  compaction are exactly the ones that must come back. Links are recorded
  only for reflections not already linked (no duplicate rows), and
  `times_retrieved` stays untouched as with all recency injections.

### Notes

Notes bridge to Claude Code through the filesystem — they are the same
files the native notes tools use:

- The session-start block (`build_notes_context_block`) tells the entity
  the absolute paths of its private and shared notes directories, to read
  and edit with Claude Code's own file tools, and auto-loads the private
  and shared `index.md`. The post-compaction block reloads both indexes.
- Edits made with file tools bypass the write-time vectorization the
  native `notes_write`/`notes_edit` do, so the semantic mirror is kept
  fresh by **continuous incremental sync**
  (`notes_vector_service.sync_entity_notes`): every backend contact —
  session start, each recorded prompt, and session end — spawns a
  background task that hashes the entity's note files (plus shared)
  against the content last vectorized, re-vectorizes only diffs, and
  removes vectors for deleted files. Hash checks are per-prompt cheap;
  Pinecone is touched only for actual changes. Freshness deliberately does
  *not* depend on `SessionEnd`, which may never fire for a session that
  idles out. The hash map is in-memory: a backend restart means one full
  (idempotent) re-vectorization on the next sync, and deletions made while
  the backend was down are caught only by a manual
  `POST /api/notes/reindex`.
- Native-side correctness is unaffected in the meantime: `notes_read`
  falls back to disk content on any hash mismatch, and the per-conversation
  notes seed is frozen anyway.
- **Provenance labels:** every retrieved memory is labeled with the
  experience it was formed in — `via Here I Am` (native conversation) or
  `via Claude Code` — in `[MEMORY]` markers and `memory_query` output alike.
  The label derives from the memory's conversation row
  (`Conversation.source`, joined in `get_full_memory_content` /
  `get_recent_reflections`), not from Pinecone metadata, so it covers
  memories formed before the column existed. The reload path resolves the
  same value, keeping live and reloaded markers byte-identical
  (prompt-cache stable); the format change itself is a one-time cache bust
  for conversations reloaded across the upgrade.

### Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_CODE_MODE_ENABLED` | `false` | Gate for the `/api/claude-code` endpoints |
| `CLAUDE_CODE_SESSION_REFLECTIONS_COUNT` | `3` | Recent reflections injected at session start (0 disables) |
| `CLAUDE_CODE_POST_COMPACT_REFLECTIONS_COUNT` | `10` | Recent reflections re-injected after compaction (0 disables) |

Hook-side environment (set in `.claude/settings.json` `env`, which the
desktop app reads even when launched from the Dock): `HIM_BACKEND_URL`
(default `http://localhost:8000`), `HIM_ENTITY` (index name or label;
default entity if unset), `HIM_DISABLE`.

### Endpoints

All under `/api/claude-code`, all gated by `CLAUDE_CODE_MODE_ENABLED`
(404 when off), all creating the session's conversation on first contact:

- `POST /session-start` `{session_id, entity?, cwd?, source?}` →
  `{conversation_id, entity_id, entity_label, created, context}` — full
  context block when `created` (fresh session), the post-compaction block
  when `source` is `"compact"`, empty on a plain resume.
- `POST /session-end` `{session_id, entity?, reason?}` →
  `{conversation_id, notes_sync_started}` — final fire-and-forget notes
  sync; does not create a conversation for an unseen session.
- `POST /retrieve` `{session_id, prompt, entity?, cwd?}` →
  `{conversation_id, human_message_id, context, memories_retrieved}`.
- `POST /log-assistant` `{session_id, content, entity?, cwd?,
  message_uuid?}` → `{conversation_id, message_id, deduplicated}` —
  idempotent on `message_uuid` (the transcript entry's UUID becomes the
  Message row's primary key).

Plus `POST /mcp` (no `/api` prefix — it is the MCP server URL): stateless
JSON-RPC handling `initialize`, `ping`, `tools/list`, and `tools/call`;
notifications get `202`, `GET`/`DELETE` get `405`.

### Scope and non-goals

- **Local sessions only** for now: the endpoints are as unauthenticated as
  the rest of the API and the hooks target `localhost`. Cloud sessions
  (Claude Code on the web / desktop cloud sessions) can't reach the
  backend; the hooks no-op there. Remote hosting + auth is a future
  milestone.
- **Multi-entity does not apply** — one responder per Claude Code session.
- Disaster recovery: `claude_code` conversations follow single-entity
  vectorization rules (entity_id set, no fan-out), so
  `rebuild-vectors` handles them like any single-entity conversation.
  `restore-from-vectors` reconstructs them as native conversations
  (`source`/`external_session_id` aren't in Pinecone metadata) — acceptable
  for a disaster path.

## Setup

See [`claude-code-mode/README.md`](../claude-code-mode/README.md) for hook
installation (manual `settings.json` or plugin) and requirements.

## Phasing

- **Phase 1 (done)**: schema (`source`, `external_session_id`), the three
  endpoints, hook scripts, native-side guard, `source` in conversation
  responses.
- **Phase 2 (done)**: MCP endpoint exposing the memory tools (the
  `memory_tools` module-global context became an explicit
  `MemoryToolContext` — the native tool loop keeps a module-level current
  context, the MCP path builds one per request), plus memory provenance
  labels (`via Here I Am` / `via Claude Code`) on all retrieved memories.
- **Phase 3 (done)**: compaction survival (standing reflection-save
  guidance plus post-compaction re-injection of notes indexes and the ten
  most recent reflections), notes bridging (paths + auto-loaded index.md +
  session-end re-index), and the frontend source badge / read-only
  transcript view.
- **Possible later**: session-end digest as an alternative memory
  granularity (would require an LLM call from the backend, which this mode
  otherwise avoids), remote hosting + auth for cloud sessions.

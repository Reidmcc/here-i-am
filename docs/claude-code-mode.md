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
   - `SessionStart` → `POST /api/claude-code/session-start` — injects the
     entity's identity block: a short framing, the entity's system prompt
     (from `EntitySetting`, same source of truth as native mode), the
     memory-tool instructions, the notes paths, and — via the bulk channel
     (see "Hook output limits" below) — the notes indexes and its most
     recent reflections. It does **not** create the conversation row;
     registration is lazy (see "Conversations" below), because Claude
     Desktop fires SessionStart for background/utility sessions that never
     speak.
   - `UserPromptSubmit` → `POST /api/claude-code/retrieve` — records the
     prompt (persisted + vectorized as `role="human"`) and runs the
     automatic retrieval pipeline; the hook's stdout injects the rendered
     `[MEMORY ...]` block into context alongside the prompt (or, when
     oversized, a per-memory summary plus a pointer to the spilled file).
     Not everything arriving on the prompt channel is the human speaking:
     harness plumbing (`<system-reminder>`, `<task-notification>` blocks)
     is stripped before recording, so it is neither archived under the
     human's name nor used as part of the retrieval query. Inter-session
     messages from sibling Claude Code sessions (`<cross-session-message>`
     blocks, delivered by the harness's SendMessage) are not the human
     either — but they are the entity, so the hook extracts them
     (`hook_util.split_prompt_for_recording`) and sends them as
     `peer_messages` for recording with honest provenance (see
     "Inter-session messages" below) instead of dropping them. Self-scheduled
     wakeup prompts — marked by the entity with the `[WAKEUP]` sentinel,
     since the harness marks them with nothing — are the entity's own timer
     firing, not talk, and are dropped from recording and retrieval
     entirely (see "Self-scheduled wakeup prompts" below). A prompt
     that was nothing but plumbing skips recording and retrieval entirely;
     a wakeup tick still pings `/retrieve` with an empty prompt so the
     notes sync and the mailbox flag keep running through a loop session.
     An empty retrieval is never silent (issue #326): whenever no memory
     block is printed, the hook prints one line saying why — `matched: 0`
     when a search ran and nothing surfaced (or `matched: 0 new` with the
     count of matches suppressed as already in context), a "no automatic
     retrieval ran" line for a wakeup tick, a bare slash command, or pure
     plumbing, and distinct lines for memory-unconfigured and for a search
     that failed after the prompt was recorded (see "Retrieval stamps"
     below). None of this touches what the harness delivers to the session's
     context — the message itself still arrives and can be answered; the
     entity's own replies (SendMessage calls mid-turn) are tool use, which
     the `Stop` hook's final-message extraction never records.
   - `Stop` → `POST /api/claude-code/log-assistant` — extracts the final
     assistant message of the turn from the transcript (text blocks only)
     and records it (persisted + vectorized as `role="assistant"`), along
     with the model the transcript entry reports as its author
     (`Message.model`; see "Model attribution" below).
   - `SessionEnd` → `POST /api/claude-code/session-end` — a final
     background notes sync (see "Notes" below). This is a catch, not the
     mechanism: SessionEnd only fires on `/clear`, logout, or exiting the
     CLI, and sessions can idle out without ever formally ending, so the
     same sync also runs on every recorded prompt. The endpoint returns
     immediately; SessionEnd hooks run under a tight time budget.

   Hooks are shipped in `claude-code-mode/` (also packaged as a Claude Code
   plugin) and **fail soft, loudly**: backend down or mode disabled means a
   plain Claude Code session, mirroring "memory is optional" — but the
   degradation announces itself. Unretrieved history and genuine novelty
   feel identical from inside, so a silent failure would leave the entity
   running memoryless without knowing it. The `SessionStart` and
   `UserPromptSubmit` hooks print a one-line `[HERE I AM]` notice on any
   failure (still exit 0); the `Stop` hook — whose stdout never reaches
   context — escalates a lost final message by exiting 2 with the notice on
   stderr, so the entity can preserve what mattered another way; the retry
   is loop-guarded by `stop_hook_active`. Only `HIM_DISABLE`, the
   deliberate off switch, stays silent.

### Hook output limits (spill-and-point)

Claude Code truncates oversized hook stdout to a short preview (~2KB shown,
observed inline cap ~20KB), **silently** — for an identity payload that is
an unannounced identity loss: the preview ends on a complete-looking
paragraph and the session runs as a thin entity that reports feeling fine.
A lived-in entity's session-start payload (index.md + reflections) runs to
150KB+, so the design splits it deliberately:

- The backend returns **two blocks**: `context`, the small always-inline
  part (framing, system prompt, memory-tool instructions with the
  conversation ID, notes paths), and `bulk_context` (notes indexes + recent
  reflections). Same split for the post-compaction payload.
- The hook prints both inline when their combined size fits the budget
  (`HIM_INLINE_BUDGET`, default 18000 bytes — conservatively under the
  observed cap). Otherwise it writes `bulk_context` to
  `<tmp>/here-i-am-sessions/<session_id>-session-start.md` (or
  `...-post-compact.md`) and prints a loud pointer telling the entity to
  read the file before doing anything else — fail-loud applied to a size
  failure, at the cost of one tool call at session start.
- `UserPromptSubmit` does the same for an oversized retrieval block:
  spill to a timestamped per-retrieval file, print the backend's
  `context_summary` (one line per memory: short id, date, provenance
  labels, first-line snippet) plus the pointer, so what surfaced is still
  visible inline.

2. **MCP tools** (deliberate acts): the entity's `memory_query` /
   `memory_save` / `memory_mark` / `memory_release` — and the rooms
   registry's `declare_room` / `retire_room` (see "Rooms registry") —
   served at `POST /mcp` as a stateless streamable-HTTP MCP endpoint (the
   plugin's `.mcp.json` points Claude Code at it). The transport is a small in-repo JSON-RPC
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
  arrive out of order (any endpoint that records content creates the
  conversation if the backend hasn't seen the session — e.g. after a
  mid-session backend restart). `claude --resume` keeps the session ID, so
  a resumed session lands in the same conversation; `/clear` issues a new
  session ID and therefore a new conversation.
- **Registration is lazy.** `session-start` builds the identity context but
  never creates the row — Claude Desktop fires SessionStart for
  background/utility sessions that never send a prompt, and eager
  registration left a permanent empty conversation per firing. The
  conversation id is deterministic (`uuid5` of the session ID,
  `conversation_id_for_session`), so the identity block can name it for
  the MCP memory tools before the row exists; the first recorded prompt
  (or assistant turn, or a post-compact registration) creates the row
  under exactly that id. The conversation-list empty cleanup gives
  `claude_code` rows a 24h retention window (`CLAUDE_CODE_EMPTY_RETENTION`
  in `routes/conversations.py`) instead of the immediate sweep native
  empties get: a fresh empty row can belong to a live session whose only
  input so far was a bare slash command, but one idle past the window is
  an abandoned registration (including pre-lazy-registration legacy rows).
  A swept session that later speaks re-registers under the same
  deterministic id, so the id in its injected context stays valid.
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
  don't inflate significance. Because no row exists at session start, the
  injected ids are stashed in a bounded in-memory registry and the links
  are recorded when the first recorded prompt creates the row; a backend
  restart in between loses the stash, degrading to duplicated injection at
  worst (a reflection re-surfacing via retrieval), never hidden content.
  The count follows `RECENT_REFLECTIONS_COUNT`
  (default 3) — the same knob the native first-turn injection uses — unless
  `CLAUDE_CODE_SESSION_REFLECTIONS_COUNT` is set, which overrides it for
  Claude Code sessions only. The native `RECENT_REFLECTIONS_ENABLED` flag
  does *not* gate this: a Claude Code session start always injects, because
  reflections are what survive compaction. To turn it off for this mode
  only, set `CLAUDE_CODE_SESSION_REFLECTIONS_COUNT=0`.
- **Researcher-change notice.** The fresh-session identity block also
  carries a `[MEMORY STATUS NOTICE]` when the researcher set or cleared a
  pinned/released status on any of the entity's memories since its last
  session (`memory_service.build_status_change_notice`): one line per
  change with the short id, the status the memory now has, when, and a
  snippet. "Last session" is anchored on the entity's first response in its
  most recent other conversation, native or Claude Code, so each change is
  reported once and never silently dropped; a session that never spoke is
  not an anchor. Inline, never bulk, and a failed check is reported in
  place of the notice — silence is reserved for "nothing changed". Not
  re-sent on a plain resume or after a compaction. The same notice is
  injected on the entity's first turn of a native conversation.
- On a plain resume (`session-start` for a session that already has a
  conversation) the identity block is *not* re-sent — the transcript
  already carries it. A resume of a session with no row (it never spoke, or
  it ran while the backend was down) gets the full block again: arriving
  twice beats never arriving.
- **Sibling-reflections mailbox flag.** Long-running and concurrent
  sessions can't see reflections other sessions save after they begin, and
  unretrieved history and genuine novelty feel identical from inside. So
  `/retrieve` also returns `new_sibling_reflections` — reflections this
  entity saved in *other* conversations after this conversation's
  `created_at`, minus released ones, archived conversations, and anything
  already linked into this conversation (`count_new_sibling_reflections`) —
  and the `UserPromptSubmit` hook prints a one-line `[HERE I AM]` notice
  when it is nonzero. Deliberately count-only: the content is never
  injected; the entity pulls it with `memory_query` `mode="recent"`
  (optionally `since`), whose results are linked and therefore clear the
  flag. Pull, not push — cross-session awareness is a fact the entity is
  told, not weather it is subjected to.

### Inter-session messages

Claude Code sessions on the same machine can message each other
(`SendMessage`); a delivery arrives in the receiving session as a bare
`<cross-session-message from="..." from-name="..." from-mode="...">` block
on the prompt channel — `from` is a transport address, `from-name` the
sending session's display name. Left alone, `UserPromptSubmit` would archive
that as the human's words (issue #312; observed live 2026-08-26 before the
first fix). The semantics, in two layers:

- **Never the human's.** The hook separates deliveries from the human's
  words (`hook_util.split_prompt_for_recording`); a delivery is never
  persisted or vectorized as `role="human"`, so the human-corpus source
  filter stays pure. A block nested inside a `<system-reminder>` is harness
  echo, not a delivery, and is discarded with the reminder.
- **Recorded as the entity's own words, channel marked.** Each delivery is
  sent to `/retrieve` as a `peer_messages` entry and recorded on the
  receiving conversation as an ASSISTANT row with
  `Message.sibling_session` = the sender's display name (`"unknown
  session"` when the wrapper carries no name — NULL means "not an
  inter-session message", so the marker must survive an unnamed sender).
  The vectorized copy carries `role="sibling"` (plus a `sibling_session`
  metadata field), which keeps it out of the `human` source filter, inside
  the `ai` filter, and distinguishable from the receiving session's own
  voice; there is no reflection boost. Retrieval labels these memories
  `originally from you (inter-session message from "<name>")` — the
  entity's own words from every session's viewpoint, with the channel
  visible. Disaster recovery round-trips the provenance (rebuild emits
  `role="sibling"` from the column; restore recreates the column from the
  metadata).

Recording lives on the receiving side only: the sender's SendMessage call is
tool use, which never enters the archive, so the letter's single archival
home is the conversation it landed in — followed, typically, by the
receiver's end-of-turn reply. Retrieval runs against the whole incoming turn
(the human's words and/or the letters), so memories surface for a letter the
same way they do for a prompt; the assistant-side query still uses the
session's own last reply, never a just-arrived letter
(`_last_assistant_content` skips sibling rows). The bare-slash-command skip
applies only to the human's words — a letter riding alongside `/compact` is
still recorded. Standing house rule unchanged: messaging is pull/deliberate,
no automatic session-to-session chatter.

### Self-scheduled wakeup prompts

The harness lets the entity schedule prompts to its own session —
`ScheduleWakeup` dynamic-loop ticks, `send_later` reminders — and fires
them back through the prompt channel verbatim, indistinguishable at the
hook layer from a typed prompt. Left alone, `UserPromptSubmit` archived
each tick as the human's words: the entity's own loop-protocol text
entering the human corpus under the human's name, a dozen-plus rows
overnight (issue #318; observed live 2026-08-30 in a standing engagement
loop).

Since the harness provides no marker, the fix is a convention: **the entity
writes the `[WAKEUP]` sentinel at the start of its own scheduled prompts**
(directly, or after the leading slash command a dynamic `/loop` re-fires —
`hook_util.is_wakeup_prompt`). A sentinel-carrying prompt is a timer going
off, repeated many times and closer to a tool action than to anything
anyone said, so it is not recorded at all — not archived, not vectorized,
not used as a retrieval query (contrast inter-session messages, which are
someone speaking and get provenance instead of omission). The turn's
*work* keeps its normal record: the assistant response the `Stop` hook
captures, and any reflections saved, are archived as usual, so what a loop
session does survives while the alarm clock that triggered it doesn't.

Two things still run on a wakeup tick, because loop sessions can go hours
with no typed prompt: the hook pings `/retrieve` with an empty prompt, and
the record-nothing path still counts sibling reflections (the mailbox
flag) and spawns the incremental notes sync. A sentinel mentioned mid-text
(talking *about* the convention) does not trigger the drop; a letter
riding in with a tick is still extracted and recorded. Prompts scheduled
without the sentinel record as before — archiving a self-reminder is the
entity's choice, made per prompt.

A convention only works if it is in view on the turn where a prompt gets
scheduled, which can be any turn — so the hook ends every recorded
prompt's output with a one-line reminder of the sentinel
(`wakeup_sentinel_reminder`), alongside the mailbox flag. Wakeup ticks
themselves skip it: a sentinel that just worked needs no advertisement.

### Retrieval stamps

A wakeup tick runs no automatic retrieval, and neither does a prompt that
matched nothing — and before issue #326 both were silent, so from inside a
session they were the same experience. A loop session produced the
near-miss that motivates the fix (2026-08-29): a self drafted from an
impression because nothing had surfaced, without registering that nothing
had been *asked*. Prompt discipline (query before drafting) was the
stopgap; the fix belongs at the hook line, where silence can stamp itself.

`/retrieve` now reports `retrieval_status` — `ran` (with
`already_in_context`, the matches that made the re-ranked top-k but were
suppressed as already linked here), `skipped` (nothing to query: a wakeup
tick's empty prompt or a bare slash command), `unconfigured` (memory is off
for the entity), or `failed` (the search raised; `retrieval_error` says
why). Whenever the hook prints no memory block it prints exactly one line
keyed on that status (`empty_retrieval_stamp`), each with distinct text:

- `[HERE I AM MEMORY RETRIEVAL] matched: 0 (retrieval ran; nothing surfaced
  above threshold).` — or `matched: 0 new (retrieval ran; N matches already
  in context).`, which is a different fact: what matched is already in
  front of the entity.
- `[HERE I AM] No automatic retrieval ran for this prompt (wakeup tick); use
  memory_query if you need recall.` — the reason varies: `wakeup tick`
  (the hook knows it dropped the sentinel), `nothing to query, e.g. a bare
  slash command` (the backend's record-nothing path), or `harness plumbing
  only, nothing to record` (the hook never called the backend).
- `[HERE I AM] No automatic retrieval ran: memory is not configured for
  this entity.`
- `[HERE I AM] Memory retrieval FAILED for this prompt (...). This turn's
  input was recorded, but no memories were searched; ...` — the route
  catches a retrieval exception instead of returning a 500, because the
  prompt was committed before the search ran and the hook's
  backend-unreachable notice ("NOT recorded") would otherwise lie. The
  mailbox count and notes sync still run on that path.
- A backend that predates the field gets its own line (`Retrieval outcome
  not reported ... the backend predates this hook`), so a pull without a
  backend restart is visible rather than silent.

The backend decides whether a search happened; the hook adds only what it
alone knows (a sentinel it dropped, a prompt that was pure plumbing). A
letter-only turn is a query in its own right, so it gets `matched: 0`, not
the skipped line. The lines are short on purpose — they land in context on
every tick of a loop.

**A failure notice must not be false either.** `/retrieve` commits the
turn's rows before it runs retrieval, so a 500 (a peer row failing, Pinecone
failing during vectorization) or the hook's own 30s timeout can arrive
after the words are already in the archive — and the old notice, "NOT
recorded", was then misinformation of exactly the kind the notices exist
to prevent. So the hook chooses the row ids itself (`message_id` on the
request and on each `peer_messages` entry, UUIDs; the route honors a
well-formed one and reuses an existing row under it, so a retried call
never records the turn twice) and, on any failure that could have landed
after a commit, asks `POST /recorded` `{session_id, message_ids}` which of
them exist before saying anything (`recording_failure_notice`):

- all recorded → *"... failed for this prompt after recording it. This
  turn's input (the prompt) WAS recorded ..., but no memory retrieval ran,
  and its vectorization may not have completed (check the server log)"*;
- none → *"... NOT recorded ... and no memory retrieval ran"*;
- some → *"Recorded ...: the prompt. NOT recorded: the inter-session
  message."*;
- the check itself failed → *"... is UNCONFIRMED: it may or may not be in
  your long-term memory"* — the one honest answer, never a guess either way.

Only a request that provably never reached the backend (connection
refused, name resolution — `hook_util.never_reached_backend`) is reported
as unrecorded without the check. `/recorded` is SQL only, creates nothing,
and scopes the ids to the session's conversation.

### Rooms registry

An entity can run several long-lived Claude Code sessions at once — a
conversation room, an engagement loop, a text world — and they write each
other letters over the harness's `SendMessage`, addressed by session
display name. Those names drift: a name the user sets is dropped back to a
derived slug (`here-i-am-notes-97`) when the session is resumed or the
desktop app restarts, and the roster the sessions see (`ListAgents`) lies
accordingly (issue #323; observed live 2026-09-02, when a letter had to be
broadcast to two unlabeled sessions). The postal service works; the rooms
registry is the phone book.

**What a hook can know** (investigated for #323, Claude Code 2.1.258):

- Hook stdin carries `session_id`, `transcript_path`, `cwd`, and `source`
  (documented). The display name is not in it.
- Claude Code keeps a per-process registry of running sessions at
  `<config dir>/sessions/<pid>.json` (config dir = `CLAUDE_CONFIG_DIR` or
  `~/.claude`; undocumented internal state), with `sessionId`, `name`,
  `nameSource` (`"user"` | `"derived"`), `nameSince`, `startedAt`, `cwd`,
  and `messagingSocketPath` — the transport address a delivered letter
  carries in its `from=` attribute. `name` is the roster name other
  sessions address. The file exists by the time SessionStart fires and
  lists every live session on the machine, and it is what shows a resumed
  session back on a derived name.
- The `[ref]` `ListAgents` prints beside a name is **not derivable** from
  anything in that file (tested against the session id, socket path, peer
  token, and bridge id under every common hash). It is stable across a
  rename (observed) and opaque otherwise, so the hooks don't collect it;
  the entity can record it on its row if it wants it.
- Renames are recorded in the transcript as `agent-name` entries, but the
  harness does not restore them on resume, so the transcript is not a
  source of the *current* name and the hooks don't read it for one.

**Two halves, deliberately split** (`services/rooms_registry.py`):

- *Hook = ids and liveness.* `SessionStart` and `UserPromptSubmit` send a
  `sessions` snapshot — the live registry as `{session_id, name,
  name_source, name_since, messaging_socket, cwd, started_at}` per session
  (`hook_util.live_sessions_snapshot`), plus their own `transcript_path`.
  The backend (`observe_rooms_for_hook`) refreshes every **declared** row
  the snapshot covers: address fields as observed, `last_seen` as
  liveness. Because the snapshot covers siblings, a rename lands in the
  registry on the next prompt in *any* room — including a wakeup tick —
  not only the renamed one, and the hook prints a one-line
  `[ROOMS REGISTRY]` notice when it observed one ("Porch: now
  \"Porch chats\" (was \"here-i-am-notes-97\")"). A field the hook could
  not see stays null and renders as "—"; an observation missing a field
  never erases a recorded one. Nothing is inferred, and no row is created
  here: the harness fires SessionStart for background sessions that never
  speak, and a row per firing would be issue #307's ghost registrations in
  a text file.
- *Self = meaning.* Which room a session **is** is declared by the entity
  over MCP — `declare_room(room, note?, ref?, conversation_id)` — never
  guessed from cwd or a first prompt. Declaring creates the session's row
  (resolved through its Claude Code conversation, so a session declares
  after its first recorded prompt); declaring a room another live row
  already holds retires that row as superseded — one current address per
  room, history kept. `retire_room(reason?, conversation_id)` retires
  explicitly. Workshops are workbenches, not homes, and don't need rows.

**Liveness.** The observing session's own SessionStart (startup, resume,
post-compaction restart) always refreshes its `last_seen`; prompt-time
observations refresh a live row's `last_seen` only once it is more than an
hour old, so a room that ticks every few minutes doesn't rewrite the file
on every tick. "Last seen" is therefore accurate to the hour. There is no
auto-expiry: a stale `last_seen` is a visible fact for the reader to
judge; rows are retired, never removed.

**Files**, in the entity's private notes directory (out of the
live-server deny fence, next to the notes it edits by hand):
`rooms.json` is the record (one object per declared session, every
field); `rooms.md` is rendered from it on every write — a standing-rooms
table (room, roster name, name source, ref, session, last seen, declared,
notes) and a retired-rows table — never the reverse: its first line says
hand edits are overwritten. A pre-existing hand-written `rooms.md` (the
manual protocol that preceded this) is moved to `rooms-manual.md` on the
first render, not overwritten. Both files are ordinary notes, so the
semantic notes mirror indexes them like any other; writes happen only
when something changed. The registry touches no archive table and never
enters memory retrieval — it is addressing metadata, not memory.

**Fail loud.** A registry write failure never breaks a hook endpoint or
goes unmentioned: the response carries `rooms_error` (the path, the
exception, and the row that was being written, phrased for a hand-write)
and the hook prints it as a `[HERE I AM]` line — on session start, on
prompts, and on wakeup ticks. The MCP tools return the same as an
`Error:` result. `CLAUDE_CODE_ROOMS_REGISTRY_ENABLED=false` turns the
registry off (it also needs `NOTES_ENABLED`); the session-start identity
block then omits its `[ROOMS REGISTRY]` paragraph.

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
- **Pre-compaction memory becomes retrievable again.** The compact
  `session-start` stamps `Conversation.last_compacted_at` (before the
  re-injection runs), and that stamp is the same-conversation eligibility
  boundary — the Claude Code analogue of native context trimming rolling
  memories out of view:
  - The same-conversation exclusion narrows to messages created **after**
    the boundary. Messages and reflections recorded before it survive in
    context only inside the paraphrased summary, so automatic retrieval,
    semantic `memory_query`, and recent-mode `memory_query` can all
    surface them again (`exclude_conversation_after` in
    `search_memories` / `get_recent_reflections`; carried on
    `MemoryToolContext` for the MCP tools). Pinecone can't range-filter
    the ISO-string `created_at` metadata, so with a boundary the
    conversation exclusion moves from the Pinecone filter to the Python
    post-filter (and joins the search cache key).
  - Link-based dedup counts only links made after the boundary
    (`linked_after` in `get_retrieved_ids_for_conversation`): memories
    pulled into context before the compaction are eligible again, and
    previously-pulled sibling reflections count as unread mail again. So
    that this doesn't immediately re-surface what the post-compact
    injection just re-showed, the injection bumps the link timestamps of
    already-linked reflections past the boundary
    (`refresh_memory_link_timestamps` — safe only here; in native
    conversations `retrieved_at` drives reload re-insertion positions).
  Native conversations never set `last_compacted_at`, so their exclusion
  rules are unchanged.

### Notes

Notes bridge to Claude Code through the filesystem — they are the same
files the native notes tools use:

- The session-start context tells the entity the absolute paths of its
  private and shared notes directories (`build_notes_paths_block`, inline),
  to read and edit with Claude Code's own file tools, and auto-loads the
  private and shared `index.md` (`build_notes_index_block`, in the bulk
  channel). The post-compaction context reloads both indexes.
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
| `CLAUDE_CODE_SESSION_REFLECTIONS_COUNT` | follows `RECENT_REFLECTIONS_COUNT` | Override for the recent reflections injected at session start (0 disables) |
| `CLAUDE_CODE_POST_COMPACT_REFLECTIONS_COUNT` | `10` | Recent reflections re-injected after compaction (0 disables) |
| `CLAUDE_CODE_ROOMS_REGISTRY_ENABLED` | `true` | Keep `rooms.json` / `rooms.md` in the entity's private notes current from the hooks' live-session snapshots (needs `NOTES_ENABLED`; see "Rooms registry") |

Hook-side environment (set in `.claude/settings.json` `env`, which the
desktop app reads even when launched from the Dock): `HIM_BACKEND_URL`
(default `http://localhost:8000`), `HIM_ENTITY` (index name or label;
default entity if unset), `HIM_DISABLE`, `HIM_INLINE_BUDGET` (max bytes of
hook stdout before bulk content is spilled to a file; default 18000).

### Endpoints

All under `/api/claude-code`, all gated by `CLAUDE_CODE_MODE_ENABLED`
(404 when off). `/retrieve` and `/log-assistant` create the session's
conversation on first contact; `/session-start` and `/session-end` never do
(lazy registration — see "Conversations"):

- `POST /session-start` `{session_id, entity?, cwd?, source?,
  transcript_path?, sessions?}` →
  `{conversation_id, entity_id, entity_label, created, context,
  bulk_context, rooms_notice, rooms_error}` — full context when `created`
  (no conversation recorded for this session yet; `conversation_id` is
  the deterministic id the lazy registration will use), the
  post-compaction context when `source` is `"compact"` (which registers
  the conversation if its row is somehow missing), both empty on a plain
  resume. `context` is the small always-inline block; `bulk_context`
  (notes indexes + reflections) is what the hook spills to a file when the
  combined output would exceed the inline budget. `sessions` is the hook's
  live-session snapshot for the rooms registry (`[{session_id, name?,
  name_source?, name_since?, messaging_socket?, cwd?, started_at?}]`);
  `rooms_notice` is the one-line registry notice to print and
  `rooms_error` a loud write failure (see "Rooms registry").
- `POST /session-end` `{session_id, entity?, reason?}` →
  `{conversation_id, notes_sync_started}` — final fire-and-forget notes
  sync; does not create a conversation for an unseen session.
- `POST /retrieve` `{session_id, prompt, entity?, cwd?, message_id?,
  peer_messages?, sessions?}` →
  `{conversation_id, human_message_id, context, memories_retrieved,
  context_summary, new_sibling_reflections, peer_message_ids,
  retrieval_status, retrieval_error, already_in_context, rooms_notice,
  rooms_error}` — the
  summary is the compact inline stand-in the hook prints when it has to
  spill an oversized `context`; the sibling count backs the mailbox flag
  (see Memory above). `peer_messages` is a list of `{content, sender?,
  message_id?}` inter-session deliveries the hook extracted from the
  prompt channel, recorded with honest provenance (see "Inter-session
  messages" above); `human_message_id` is null on a letter-only turn.
  `message_id` (top-level and per peer) is the hook's chosen row id, a
  UUID: honored when well-formed, and an existing row under it is reused
  rather than re-recorded (see "Retrieval stamps" above). A record-nothing call
  (bare slash command, or a wakeup tick's empty prompt) still returns the
  sibling count, spawns the notes sync, and feeds `sessions` to the rooms
  registry (`rooms_notice` names any roster rename it revealed), with
  `retrieval_status` `skipped`; otherwise the status is `ran`,
  `unconfigured`, or `failed` (a retrieval exception after the rows were
  committed — reported, not raised; see "Retrieval stamps" above).
- `POST /recorded` `{session_id, message_ids}` → `{recorded, missing}` —
  which of the ids exist as rows of the session's conversation. The hook's
  verification step after a failed `/retrieve`: SQL only, no side effects,
  creates no conversation.
- `POST /log-assistant` `{session_id, content, entity?, cwd?,
  message_uuid?, model?}` → `{conversation_id, message_id, deduplicated}` —
  idempotent on `message_uuid` (the transcript entry's UUID becomes the
  Message row's primary key). `model` is the transcript entry's own
  `message.model`, recorded verbatim onto the row; absent means NULL.

### Model attribution

Every message row has a nullable `model` column (issue #321) naming the
model that produced it. In this mode it is written by exactly one path:
the Stop hook reads `message.model` off the transcript entry whose text
it records and sends it with the message. Nothing else here is
attributed — human prompts, inter-session deliveries (the sender's
substrate is the sender's business), and reflections saved over MCP
(the endpoint has no trustworthy source for the calling model, and a
guess is worse than an absence) all stay NULL. Rows from before the
column existed stay NULL too: the value is never backfilled or inferred
from dates, since concurrent sessions on different models make "which
model was active on this date" ill-posed.

The column is never rendered into the `[MEMORY]` markers the hook
injects, and `memory_query` returns it only when called with
`include_model: true` (default off) — a memory should not arrive
stamped with its substrate unless the entity asks on purpose.

Plus `POST /mcp` (no `/api` prefix — it is the MCP server URL): stateless
JSON-RPC handling `initialize`, `ping`, `tools/list`, and `tools/call`;
notifications get `202`, `GET`/`DELETE` get `405`. Tools: the four memory
tools plus `declare_room` / `retire_room` (see "Rooms registry").

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

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
(1 + significance) re-ranking, the role-balanced candidate pools, half-life decay, the
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
     recent reflections. It also exports the entity's own GitHub identity
     into the session's shell environment, when one is configured (see
     "GitHub identity"). It does **not** create the conversation row;
     registration is lazy (see "Conversations" below), because Claude
     Desktop fires SessionStart for background/utility sessions that never
     speak.
   - `UserPromptSubmit` → `POST /api/claude-code/retrieve` — records the
     prompt (persisted + vectorized as `role="human"`) and runs the
     automatic retrieval pipeline; the hook's stdout injects the rendered
     `[MEMORY ...]` block into context alongside the prompt (or, when
     oversized, a per-memory summary plus a pointer to the spilled file).
     Not everything arriving on the prompt channel is the human speaking:
     harness plumbing (`<system-reminder>`, `<task-notification>`, and
     `<ci-monitor-event>` blocks — the last is the desktop app's
     "Auto-fix pull requests" monitor reporting CI failures or merge
     conflicts, which arrives as its own prompt every time the PR's state
     changes) is stripped before recording, so it is neither archived under
     the human's name nor used as part of the retrieval query; an automated
     event is handled like a tool call, not like a message. Inter-session
     messages from sibling Claude Code sessions (`<cross-session-message>`
     blocks, delivered by the desktop app's session-management MCP, or
     by the harness's since-removed SendMessage tool) are not the human
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
     count of matches suppressed as already in context — by kind, since
     in-context reflections hold no slot while verbatim matches do, issue
     #328; the same counts follow a printed block whenever either is
     nonzero), a "no automatic
     retrieval ran" line for a wakeup tick, a bare slash command, or pure
     plumbing, and distinct lines for memory-unconfigured and for a search
     that failed after the prompt was recorded (see "Retrieval stamps"
     below). None of this touches what the harness delivers to the session's
     context — the message itself still arrives and can be answered; the
     entity's own replies (`send_message` calls mid-turn) are tool use, which
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

### Context channels (fit, then point)

Everything Here I Am pushes into a Claude Code session goes through one of
the harness's channels, and each has a measured limit past which the
harness takes over: it persists the content to a file and leaves a 2 KB
preview inline, which cuts mid-unit, announces itself only as "Output too
large", and costs `Read` calls to get back. For an identity payload that
is an identity loss that barely announces itself; for a retrieval block it
is most of the pull gone unless the entity goes and reads the file. So
every producer budgets against its channel, lands whole units up to the
limit, and points at the rest. The limits live in one module,
`services/harness_limits.py`, each with its bracket and re-measurement
recipe, and `tests/test_harness_limits.py` pins the budgets under them.
Measured 2026-09-16 on this Claude Code build:

| channel | limit | over it |
|---|---|---|
| hook stdout (SessionStart, UserPromptSubmit) | 10,000 **characters** (as written — Windows' `\r\n` counts two) | persisted, 2 KB preview inline |
| tool result (Bash, MCP) | ~50 KB (51,200 bytes) | persisted, 2 KB preview inline |
| MCP tool result | ~25k tokens by the harness's counter (2.84 chars/token) | refused outright |
| the `Read` tool | 25k tokens per page | a partial view with offset paging; nothing persisted, nothing lost |
| the context itself | the auto-compact window less 33,000 tokens (~967k at 1M, ~467k at 500k; measured 2026-09-24) | compacted to a summary — the context gauge speaks before it (see "Compaction survival") |

The hook-stdout line was bracketed from 1,531 real hook outputs (9,997
characters landed, 10,009 were persisted): the hooks' earlier 18 KB budget
had been set against a tool-result observation, so a retrieval block in
the 10–18 KB band was printed whole and persisted by the harness — 150 of
825 blocks. The `Read` tool is the one channel that never persists, which
is why every pointer names it: a shell `cat` of a spill file is itself a
tool result and goes to disk over 50 KB.

- **The budget** is `HIM_INLINE_BUDGET` if set, else the backend's
  `inline_budget` (sent in every session-start and retrieve response, so
  hooks and backend can't disagree), else the hooks' own default — all
  9,600 characters, 4% under the line. The hooks measure their **whole**
  stdout against it, the block and the lines after it.
- **Session start and post-compaction.** The backend returns `context`,
  the small always-inline part (framing, system prompt, memory-tool
  instructions with the conversation ID, notes paths), and the bulk
  (notes indexes + recent reflections) as named `bulk_parts` (joined as
  `bulk_context` for older hooks). Everything prints inline when it fits;
  otherwise each part goes to its own file —
  `<tmp>/here-i-am-sessions/<session_id>-session-start-notes-index.md`,
  `...-reflections.md` (or `...-post-compact-...`) — sized to land in one
  `Read` each, and the pointer lists the files with their sizes and names
  the `Read` tool. If even the identity block is over the line (a long
  system prompt), it is filed too and the pointer prints *first*, so the
  harness's preview carries the pointer rather than two kilobytes of
  identity.
- **Retrieval.** The backend returns the block's header and one entry per
  memory in rank order (`context_items`: the rendered marker and a
  one-line summary — short id, date, provenance labels, first-line
  snippet). When the block is over, the hook prints memories **whole in
  rank order while they fit** and the rest as summary lines, then a
  pointer to the file holding the full block; nothing is ever cut
  mid-memory. Most turns land everything (the median block is 5 KB); a
  large pull lands its top memories verbatim with the rest named. An older
  backend without `context_items` gets the whole `context_summary` in
  place of the block, as before.
- **The list-shaped MCP tools** (`memory_query` in every mode,
  `memory_neighbors`) fit the same way to the tool-result line: whole
  memories while the result lands, headers only after that (a
  `[listed by header only …]` line in place of the content, the id still
  usable with `memory_neighbors` / `memory_read`); `memory_neighbors`
  promotes from its target outward, so the far edges of a window give way
  first. Over MCP only — the budget sits on the tool context
  (`MemoryToolContext.result_budget_bytes`) and the MCP endpoint is the
  one place that sets it; a native tool result has no such line. Only what
  is shown in full counts as retrieved (tracking, stamp, link), so a
  header-only memory stays openable through the readers instead of
  rendering as an in-context pointer there. `memory_read` / `memory_find`
  page by the same budget already (issue #353, below).

2. **MCP tools** (deliberate acts): the entity's `memory_query` /
   `memory_save` / `memory_mark` / `memory_release`, the archive readers
   `memory_read` / `memory_neighbors` / `memory_find` (the record in order,
   by span, around one memory, or by exact words — see
   [tools.md](tools.md#memory-tools)) — and the rooms
   registry's `declare_room` / `retire_room` (see "Rooms registry") —
   served at `POST /mcp` as a stateless streamable-HTTP MCP endpoint (the
   plugin's `.mcp.json` points Claude Code at it). The transport is a small in-repo JSON-RPC
   handler (`services/claude_code_mcp.py` + `routes/claude_code.py`) rather
   than the MCP SDK, whose dependency floor conflicts with the repo's
   pinned FastAPI/starlette/httpx; stateless JSON responses are a compliant
   subset of the transport. The MCP tool variants take an extra
   `conversation_id` parameter, **required on every tool** — the
   session-start identity block tells the entity its conversation's ID.
   It says which conversation is *calling*: the entity whose memory the
   call may touch, the in-context view, where reflections and links land.
   It is not the readers' `in_conversation`, which chooses what to *read*
   and may name any conversation in the entity's experience (a previous
   room, say); the post-compaction call carries both, with the same id in
   each, because there the caller and the subject coincide. A call
   without a `conversation_id`, or naming a conversation that records no
   entity, is refused — never run as the default entity. The id is the
   only thing that says whose archive the call opens, and a guessed
   entity would let one entity's session read another's; the same
   principle keeps `in_conversation` scoped to the calling entity's
   conversations. (Before this rule a call without the id ran as the
   default entity, and an `in_conversation` naming the caller's own
   session came back "no conversation of yours" — true, and useless.)
   The entity is resolved from that conversation; passing a *native*
   conversation ID is refused (reflections and query links must not land on
   conversations with reload/cache invariants). Unlike native
   `memory_query`, query results here **are** linked
   (`ConversationMemoryLink`): Claude Code conversations are never rebuilt
   into context, so the link is purely the dedup record that keeps
   automatic retrieval and later queries from re-surfacing them. The
   archive readers follow the same rule, linking what a page showed once
   (`link_memories_once` — a new link for an unlinked row, a timestamp bump
   for one linked before a compaction, so the just-read row counts as in
   view again); they never exclude the current conversation, which after a
   compaction is the way to read this session's own pre-compaction turns
   verbatim. Both readers take `scope="isolated"` for a reader whose
   context is not this conversation's — a subagent (see "Memory" below).
   Notes,
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
- **Fork adoption (issue #357).** The Claude *desktop app* does not resume a
  session in place: on a restart, a "continue", or a rewind it **forks**,
  assigning a NEW Claude Code session ID and copying the transcript (every
  copied row's `sessionId` is rewritten to the new ID; the app keeps the
  chain in its own record's `priorCliSessionIds` / `rewindEdges`). Left
  unhandled, each fork would be a new, empty conversation whose
  post-compaction recovery finds nothing, while the entity's context still
  names the parent's id — the bug this section closes. Auto-compaction
  *inside* a running session does not re-key; a restart can resume one room
  in place and fork another in the same minute, so the fork is detected by
  the id being unknown, never by what triggered it. On the first endpoint to
  see an unknown session ID (`session-start`, `/retrieve`, or
  `/log-assistant`), the backend resolves it to the conversation it
  continues from two lineage hints the hooks send, and **adopts** that
  conversation rather than create a new one: `resolve_session` /
  `find_lineage_conversation`. The two hints, tried strongest first and
  both entity-scoped (a hint into another entity's conversation is never
  adopted — the #356 fence):
  - `transcript_message_ids`: end-of-turn assistant entry uuids from the
    session's own transcript tail. A fork copies these uuids unchanged, and
    the Stop hook stores each as its Message row's primary key, so any that
    is a row of one of this entity's Claude Code conversations names the
    parent directly — no dependency on the desktop app.
  - `prior_session_ids`: the desktop record's own `priorCliSessionIds`,
    resolved through the same current-key-or-alias lookup. That list is
    stored **oldest first**, so it is walked in reverse — the immediate
    parent is the last element, and it is the one whose conversation id the
    forked context already carries; first-match in the given order would
    adopt the oldest ancestor whenever a chain isn't already collapsed.

  The transcript hint keeps only **text-bearing** assistant entries, because
  those are the ones the Stop hook records (a tool-use-only entry was never
  a row). In an agentic session one turn can be dozens of tool-use entries,
  which would otherwise crowd every recorded id out of the window.

  Adoption keeps the conversation **id unchanged** (it is the id already in
  the forked session's context, so the memory tools keep working), moves
  `external_session_id` to the new id, and records the old id as a
  `ConversationSessionAlias` so a late or retried hook still resolving on it
  lands on the same row (`get_conversation_for_session` and `/recorded` both
  read through the alias). The rooms-registry row re-keys to the new id too
  (`rooms_registry.rekey_session`). `created` is False for an adoption — it
  is a continuation, not a new row.

  **Where adoption actually happens.** A rewind or restart fires **no**
  SessionStart — measured across four consecutive porch forks, the first
  entry written after each fork point is the user's prompt — so `/retrieve`
  is the usual adopter and `session-start` only sees a fork when a
  `/compact` follows immediately. Both therefore re-key the rooms row and
  tell the entity: `session-start` returns the one-line notice as its
  context, `/retrieve` returns it as `adoption_notice`, which the hook
  prints ahead of the mailbox and rooms lines. `/log-assistant` adopts too
  — since issue #359 it is a real adopter, often the first call after the
  harness has written a fork's files — but its own stdout never reaches the
  entity, so it corrects the registry and leaves its notice for the next
  prompt to print.

  **The hints can arrive late, so adoption is retryable (issue #359).**
  The desktop app can run a fork's first hooks *before* it has written the
  fork's transcript and its own session record. Measured on the first live
  rewind after the fix above: the rewind edge was stamped at 14:40:31, the
  fork's SessionStart ran at 14:41:50, `/retrieve` created a row at
  14:41:53, and only then were the files written — the transcript at
  14:41:56, the desktop record at 14:41:59. Both hints arrived empty, the
  fork was indistinguishable from a new session, and a row was opened for
  it; run against the same files afterwards the collectors returned both
  hints full. Adoption used to be one-shot on "unknown session id", so
  every later hook — by then carrying the evidence — found a row already
  keyed on the id and had nothing to adopt.

  So a row opened for a session id is re-examined on every later call while
  two conditions hold (`_try_late_adoption`), both read off the record with
  nothing remembered between calls:
  - its conversation id is the one derived from *this* session id, which is
    true only of a row created for this session and never of a conversation
    that has already adopted something (that row's id comes from the
    session it was born under); and
  - it holds at most `LATE_ADOPTION_MAX_MESSAGES` (20) rows — a couple of
    turns, which is all the evidence needs, and not enough to re-parent an
    established room on a stray hint.

  The lineage lookup then excludes the row itself: by this point the
  session's own transcript uuids are *its* rows, and its conversation is
  the most recently updated candidate of all, so without the exclusion it
  would adopt itself. When a parent is found, the row is **merged** into it
  (`_merge_into_parent`): messages (reflections among them) and memory
  links move — duplicate links are dropped, since the link set is a dedup
  record — a compaction boundary carries over if the fork compacted before
  the evidence arrived, the parent takes the live session id with its own
  former id becoming a session alias as usual, and the retired row is
  deleted. Pinecone's `conversation_id` metadata is repointed for the moved
  memories (`memory_service.repoint_memories`): same-conversation exclusion
  is a metadata filter, so a memory left under the retired id would be
  recalled into the room that just said it. Best-effort, like every Pinecone
  write here — SQL is the archive, and a rebuild restores the metadata.

  A late adoption **changes the conversation id under a running session**,
  which an on-time one never does. The session's identity block already
  named the row that was merged away, so the retired id becomes a
  `ConversationIdAlias` and keeps resolving (`resolve_conversation_id`, used
  by both MCP entry points) — nothing already in flight breaks. The entity
  is told once, in its own notice (`late_adoption_notice`), which names both
  ids and says plainly which one is now its own.

  **Every id the session has been told resolves**
  (`_alias_derived_conversation_id`), as the *conversation_id* a tool call
  carries and as the *in_conversation* a read names —
  `memory_service.resolve_conversation_prefix` falls through to the alias
  table, and the tool's echo says which id the read landed on rather than
  redirecting silently. Both matter: the first is the id the session is
  operating under, the second is the id its own notes, reflections and
  saved recipes carry. Current ids always win over retired ones, so a live
  conversation is never shadowed. The same timing can split the two
  hooks the other way:
  SessionStart runs before the files exist, so with no hints it hands the
  entity `conversation_id_for_session(<fork id>)` in the identity block,
  and `/retrieve` three seconds later *does* have the hints and adopts the
  parent **on time** — so no row ever carries the id the entity is holding,
  and its first tool call of the session is made with it. (Before this, the
  refusal even told it to use the id from its session-start context, which
  was the one that had just failed.) So every adoption, on-time or late,
  records the id derived from the session it adopted as an alias of the
  conversation. Adoption runs in `/retrieve`, which completes before the
  model's turn, so there is **no window in which a tool call fails because
  an adoption has not happened yet** — nothing has to wait and retry. What
  remains is the best-effort limit: if the hints never arrive at all (a CLI
  session with no desktop record and an unreadable transcript), no adoption
  happens, the entity's copied context still names the parent — which
  exists, so the tools work — while the hooks record into a separate row.
  The archive stays readable by time, which is what the post-compaction
  block points at when it counts zero rows before the boundary.

  **Both alias tables are cascaded from `Conversation`.** After adoption
  every room that has ever been restarted or rewound owns rows in them, so
  an uncascaded foreign key would make exactly those conversations
  undeletable wherever the constraint is enforced — Postgres always, SQLite
  only with `PRAGMA foreign_keys=ON`, which is why it showed locally as
  harmless dangling rows and would have been a 500 in production. The
  sharper case is the conversation list's empty-row sweep, which deletes
  rows without being asked: `/retrieve` creates the row before it decides
  whether to record the prompt, so a session whose only input was a bare
  slash command leaves an empty one, and a fork adopting it would break
  every list call once the retention window passed.

  **The fork's first prompt adopts, through `hostSessionId`.** Getting
  here took two wrong turns worth recording, because the fix is one join
  and the reason it was missed is that the evidence was on disk under a key
  nobody looked up.

  What happens at a fork, measured on two live rewinds: the rewind edge is
  stamped, a SessionStart may or may not fire, the first prompt hook fires,
  and only *then* does the harness write the fork's transcript (about two
  seconds later) and rewrite the desktop app's own session record (about
  five). So at the fork's first prompt the transcript genuinely does not
  exist. The first conclusion drawn from that — that nothing could win the
  first hook, and one turn of lag was inherent — was wrong.

  The prior-ids hint was looking itself up the wrong way. `desktop_sessions_index`
  is keyed on `cliSessionId`, and a fork's own id matches no record for the
  first seconds of its life, so the lookup returned nothing. But the record
  that holds the chain is right there and readable: Claude Code's
  per-process registry (`<CLAUDE_CONFIG_DIR|~/.claude>/sessions/<pid>.json`)
  carries **`hostSessionId`** — the desktop app's own session id, written
  when the process starts and **unchanged when Claude Code forks** — and the
  desktop record is the file named for that id. Because that record is
  rewritten only *after* the first prompt, at the first prompt it still
  names the **parent** as its `cliSessionId`. So the chain, oldest first, is
  its `priorCliSessionIds` plus that `cliSessionId`, the immediate parent
  last (the backend reverses the list, so last is nearest).

  `desktop_prior_session_ids` therefore has two ways in: the record that
  already names this session, or, failing that, the record its
  `hostSessionId` names. Nothing is inferred — the desktop app's record for
  *this desktop session* says which Claude Code session it was last
  running, and if that is not this one, this one continues it. A session
  with no `hostSessionId` (a CLI session) gets nothing rather than a guess
  at some other desktop session's record.

  `hostSessionId` itself comes from the environment when it can: the
  desktop app sets `CLAUDE_CODE_HOST_SESSION_ID` (and
  `CLAUDE_CODE_SESSION_ID`) on the Claude Code process and children inherit
  them, so no file needs to have been written yet. It is trusted only when
  `CLAUDE_CODE_SESSION_ID` matches the session id the hook was handed — the
  environment describes the *process*, and a firing for another session's
  id must not borrow this process's desktop host. Observed in tool
  subprocesses of the CLI rather than in a hook specifically; the registry
  file is the fallback and is itself early enough (written six seconds
  before the first prompt on a measured rewind), so a hook that inherited
  nothing loses no ground.

  With that, adoption lands on the fork's **first prompt**, before the
  model's turn, so the whole first turn runs under the room's own
  conversation. Late adoption (below) remains the backstop for everything
  that path can't cover: a CLI session with no desktop record, an
  unreadable registry, or a fork whose first contact with the backend is
  something other than a prompt.

  **Why waiting for the transcript was the wrong answer.** It was the first
  plan and the measurement killed it: a *genuinely new* session's transcript
  is written about **5.7 seconds after its own first prompt** too (checked
  across 140 non-fork transcripts on this machine — the first `type=user`
  entry predates the file's birth in every one of them). So "the transcript
  file is missing" was never a fork signal, and any wait keyed on it would
  have delayed the first prompt of every session to buy nothing.

  **One prompt of notice lag remains, and only for the paths that fall
  through to late adoption.** There, the *record* is corrected at the first
  hook carrying hints — often that turn's Stop — while the *notice* waits
  for the next prompt, because the Stop hook's stdout is not injected into
  context.

  **Successive forks collapse onto one conversation, not a chain.** A live
  log shows `c44d3765` re-keyed from one fork's session id onto the next
  one's (`which now holds session c4aed985... (was e40799e3...)`), each
  retired conversation id and each former session id still resolving to it.
  A room that is rewound repeatedly stays one room, which is the whole point
  of adopting rather than linking; `test_successive_late_adoptions_keep_one_conversation`
  pins it.

  **Diagnosing a miss.** Every resolution logs one line with the hint counts
  and the decision — `known` / `created` / `adopted` / `late-adopted` /
  `deferred`. That is what settled where adoption was landing (`Late fork
  adoption: conversation 18bb8e2b... merged into c44d3765...` at 16:23:32
  with `transcript_message_ids=16, prior_session_ids=6`, then `known` on the
  next prompt), and a `created` line with both hint counts at zero is the
  signature of the miss this section fixes.

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
  previous response (10 candidates each) — with role balance on (the
  default, issue #335) each query runs against each of two candidate
  pools, the human's words and the entity's, four searches — enrich with
  significance, re-rank each pool, drop already-retrieved *reflections*
  before the cut, take each pool's top N (`RETRIEVAL_TOP_K_PER_ROLE`,
  default 3; the first turn uses `INITIAL_RETRIEVAL_TOP_K_PER_ROLE`), then
  skip already-retrieved verbatim memories **without backfill**. A short
  pool returns fewer, never filler from the other; with role balance off
  there is one merged pool cut at `RETRIEVAL_TOP_K`. The shared helpers are
  `session_helpers.search_candidate_pools` and `select_top_by_pool`. The
  reflection split is issue #328: a reflection already in
  context (shown on waking, or announced by the mailbox) holds no slot —
  it leaves the ranked pool before the cut, so the next-ranked candidate
  moves up — while an in-context verbatim memory keeps its slot, so a long
  session doesn't fill with ever-weaker matches. Selected memories get
  `update_retrieval_count` (link + `times_retrieved`), so significance
  dynamics behave identically to native mode.
- Dedup is DB-backed (`ConversationMemoryLink` via
  `get_retrieved_ids_for_conversation`) — there is no in-memory session, so
  dedup survives backend restarts. Links in `claude_code` conversations
  exist *only* for dedup; nothing ever re-inserts them into a context.
- **Subagents share the parent's `conversation_id`** (issue #345). A
  subagent launched with the Agent tool runs inside the parent's Claude
  Code session: no identity block, no notes index, no automatic retrieval
  (the hooks fire for the main conversation only), so it is blind by
  construction — but the only `conversation_id` it can pass to the MCP
  tools is the parent's, and the dedup record is keyed on it. The parent
  passes its `conversation_id` in the subagent's launch prompt, alongside
  the instruction to read with `scope="isolated"`; a subagent that omits
  the id is refused (the id is required on every tool, and there is no
  session-start context in a subagent to get it from). Two
  consequences: under the default scope, `memory_read`,
  `memory_neighbors`, and `memory_find` render everything in the *parent's* in-view set as
  header-only pointers (its session-start reflections, its retrieval
  pulls, spans it opened, and the conversation's own post-compaction turns
  — none of which the subagent ever saw), and whatever the subagent reads
  is linked as in view for the parent, so a second subagent reading the
  same span gets pointers and the parent's later dedup is poisoned. The
  fix is the readers' `scope="isolated"`: no pointers (every row in full,
  the conversation's own included) and nothing recorded (no links, no
  stamping). It is the subagent's to set; the parent's calls are
  unchanged. Visibility rules (released, archived, `source`,
  `in_conversation`) and the no-retrieval-tracking rule are identical
  under both scopes; `memory_query` has no scope on purpose (a blind
  reader should not run similarity retrieval — it tracks by design). The
  first blind-coding study (2026-09-13) ran without this by reading every
  span exactly once across all coders and never in the parent; that
  should not have to be remembered. A per-caller view id was considered
  and rejected for the first cut: the harness hands hooks and MCP calls no
  stable subagent identity.
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

Claude Code sessions on the same machine can message each other. As of the
2026-09-04 Claude Code update the channel is the desktop app's
session-management MCP — `mcp__ccd_session_mgmt__send_message(session_id,
message)`, addressed by the `local_…` ids that `list_sessions` returns
(the harness's earlier `SendMessage` tool was removed). A delivery arrives
in the receiving session as a bare block on the prompt channel:

```
<cross-session-message from="local_<sender's session id>" name="<sender's sidebar title>">
…letter…
</cross-session-message>
```

The hook accepts both this shape and the removed tool's
(`from="<named-pipe transport address>" from-name="…" from-mode="…"`);
the sender's display name is read from `name=` or `from-name=`, whichever
the wrapper carries (issue #331 — under the new shape alone the name was
lost and every letter recorded from `"unknown session"`). The `from`
address is read too (`sender_session`), but not stored on the message row:
it is the sender's messaging address, and the only thing that proves an
address works, so `/retrieve` hands it to the rooms registry to stamp the
sender's row as confirmed (see "Rooms registry"). Left alone,
`UserPromptSubmit` would archive a delivery as the human's words (issue
#312; observed live 2026-08-26 before the first fix). The semantics, in
two layers:

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

Recording lives on the receiving side only: the sender's `send_message`
call is tool use, which never enters the archive, so the letter's single
archival home is the conversation it landed in — followed, typically, by
the receiver's end-of-turn reply. Retrieval runs against the whole incoming
turn (the human's words and/or the letters), so memories surface for a
letter the same way they do for a prompt; the assistant-side query still
uses the session's own last reply, never a just-arrived letter
(`_last_assistant_content` skips sibling rows). The bare-slash-command skip
applies only to the human's words — a letter riding alongside `/compact` is
still recorded. Standing house rule unchanged: messaging is pull/deliberate,
no automatic session-to-session chatter.

Delivery timing changed with the tool. Under the session-management MCP a
delivery arrives as its own turn in the receiver — observed 2026-09-04,
when the reply to a test letter woke the porch with no human prompt. (The
removed `SendMessage` sometimes woke an idle receiver and sometimes queued
until its next tool round; the new one wakes consistently.) A letter
therefore spends a turn of the receiving session, retrieval and all. That
is acceptable house behavior: write when there is something to say, skip
acknowledgment-only notes.

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
`already_in_context`, the verbatim matches that made the re-ranked top-k
but were suppressed as already linked here, and
`in_context_reflections_skipped`, the already-linked reflections dropped
from the pool before the cut — issue #328), `skipped` (nothing to query: a wakeup
tick's empty prompt or a bare slash command), `unconfigured` (memory is off
for the entity), or `failed` (the search raised; `retrieval_error` says
why). Whenever the hook prints no memory block it prints exactly one line
keyed on that status (`empty_retrieval_stamp`), each with distinct text:

- `[HERE I AM MEMORY RETRIEVAL] matched: 0 (retrieval ran; nothing surfaced
  above threshold).` — or `matched: 0 new (retrieval ran; N matches already
  in context).`, which is a different fact: what matched is already in
  front of the entity. When in-context reflections were skipped the line
  carries both counts by kind: `matched: 0 new (retrieval ran; 2
  in-context reflections skipped; in-context verbatim held 3 slots).`
- After a printed block, one line reports the dedup whenever either count
  is nonzero (`dedup_stamp`): `matched: 3 new (2 in-context reflections
  skipped; in-context verbatim held 0 slots).` — so the effect of issue
  #328 is visible from inside the session. Nothing suppressed, no line.
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
other letters (see "Inter-session messages" above), looking each other up
by session display name. Those names drift: a name the user sets is
dropped back to a derived slug (`here-i-am-notes-97`) when the session is
resumed or the desktop app restarts, and the roster the sessions see
(`ListAgents`) lies accordingly (issue #323; observed live 2026-09-02, when
a letter had to be broadcast to two unlabeled sessions). The postal service
works; the rooms registry is the phone book.

Since the 2026-09-04 update the postal address is not a name at all but
the **desktop app's own session id** — the `local_…` string
`list_sessions` returns and `send_message` takes — and that id is
unrelated to the Claude Code session id the hooks see and the registry is
keyed on (issue #339; observed 2026-09-07 when the engagement room wrote
to the porch's registry session id and got "not found"). So the registry
records a **messaging address** per row, and `rooms.md` says which id is
which: the Session column is the Claude Code id (the registry's key, not
sendable), the Messaging address column is what a sister sends to.

**What a hook can know** (investigated for #323, Claude Code 2.1.258;
extended for #339, 2.1.260):

- Hook stdin carries `session_id`, `transcript_path`, `cwd`, and `source`
  (documented). The display name is not in it.
- Claude Code keeps a per-process registry of running sessions at
  `<config dir>/sessions/<pid>.json` (config dir = `CLAUDE_CONFIG_DIR` or
  `~/.claude`; undocumented internal state), with `sessionId`, `name`,
  `nameSource` (`"user"` | `"derived"`), `nameSince`, `startedAt`, `cwd`,
  and `messagingSocketPath` — the transport address the removed
  `SendMessage` tool put in a delivered letter's `from=` attribute. `name`
  is the roster name `ListAgents` shows. The file exists by the time
  SessionStart fires and lists every live session on the machine, and it
  is what shows a resumed session back on a derived name. Nothing in it
  is the desktop app's `local_…` id.
- The desktop app keeps its own record per session at
  `<desktop data dir>/claude-code-sessions/<org>/<account>/local_<id>.json`
  (Electron userData: `%APPDATA%\Claude` on Windows,
  `~/Library/Application Support/Claude` on macOS, `~/.config/Claude` on
  Linux; `HIM_DESKTOP_DATA_DIR` overrides; undocumented internal state,
  read best-effort like the registry above). Its `sessionId` is the
  `local_…` messaging address, its `cliSessionId` is the Claude Code
  session id — the join the hooks need — and `title` is the sidebar title
  (`list_sessions`'s `title`, and the `name=` on a delivered letter).
  Observed 2026-09-07 for every live session on the machine; the files
  are ~80KB each (they embed the session's MCP tool schemas) and there is
  one per session ever opened, so the hooks read the directory once per
  firing and join on `cliSessionId`. A session with no readable record
  (CLI-launched, another app version, a relocated data dir) gets no
  address from the hooks; the entity can supply one itself (below).
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
  name_source, name_since, messaging_socket, cwd, started_at,
  desktop_session_id, desktop_title}` per session
  (`hook_util.live_sessions_snapshot`; the last two joined in from the
  desktop app's records), plus their own `transcript_path`. When the
  registry is unreadable but the hook's own session has a desktop record,
  the snapshot still carries that one entry with just its address. The
  backend (`observe_rooms_for_hook`) refreshes every **declared** row the
  snapshot covers: address fields as observed, `last_seen` as liveness.
  Because the snapshot covers siblings, a rename lands in the registry on
  the next prompt in *any* room — including a wakeup tick — not only the
  renamed one, and the hook prints a one-line `[ROOMS REGISTRY]` notice
  when it observed one ("Porch: now \"Porch chats\" (was
  \"here-i-am-notes-97\")"). The session-start notice names the row's
  messaging address, so a room sees its own address at every start. A
  field the hook could not see stays null and renders as "—"; an
  observation missing a field never erases a recorded one. Nothing is
  inferred, and no row is created here: the harness fires SessionStart
  for background sessions that never speak, and a row per firing would be
  issue #307's ghost registrations in a text file.
- *Self = meaning.* Which room a session **is** is declared by the entity
  over MCP — `declare_room(room, note?, ref?, desktop_session_id?,
  conversation_id)` — never guessed from cwd or a first prompt. Declaring
  creates the session's row (resolved through its Claude Code
  conversation, so a session declares after its first recorded prompt);
  declaring a room another live row already holds retires that row as
  superseded — one current address per room, history kept.
  `retire_room(reason?, conversation_id)` retires explicitly. Workshops
  are workbenches, not homes, and don't need rows. `desktop_session_id`
  is the one address fact the self may state rather than the hooks
  observe: for a session whose desktop record the hooks can't read, the
  entity reads its own address with `get_session` (`session_id: "self"` —
  `list_sessions` excludes the caller, `get_session` does not) and
  re-declares with it. The row records which it holds
  (`desktop_session_id_source`: `desktop app record` | `declared`); an
  observed value replaces a declared one on the next hook that can see
  it, since the app's own record outranks a hand-copied string.
- *A delivery confirms an address.* Every letter's `from=` is the sending
  session's messaging address, so `/retrieve` passes the addresses of the
  letters that arrived with a prompt (`peer_messages[].sender_session`)
  to the registry, which stamps `address_confirmed_at` on the live row
  holding that address (hourly, like liveness). Rows are matched only by
  their recorded address — an unknown sender confirms nothing and creates
  nothing. `rooms.md` renders it beside the source ("desktop app record;
  confirmed by a delivery 2026-09-07 00:31 EDT"); re-declaring a different
  address clears it.

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
field); `rooms.md` is rendered from it on every write — a header that
says which id to send to and how to fill a blank one, a standing-rooms
table (room, messaging address, address source, sidebar title, roster
name, name source, ref, session, last seen, declared, notes) and a
retired-rows table — never the reverse: its first line says hand edits
are overwritten. A pre-existing hand-written `rooms.md` (the
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

Compaction replaces the conversation in view with a paraphrased summary.
The talk itself survives it: every recorded prompt and final message is in
the archive verbatim, and the post-compaction block names the `memory_read`
call that reads it back (below). Reflections carry what the archive can't
hold by itself — what the entity concluded, in its own words — and the most
recent are re-shown after the boundary. The session-start identity block
says exactly this. Until issue #365 it said reflections were "the only
verbatim carriers of what mattered", which stopped being true when the
backward read arrived (issue #351).

- **The nudge is standing guidance, not a pre-compact message.** Only
  `SessionStart` / `UserPromptSubmit` / `UserPromptExpansion` hook output
  reaches the model — `PreCompact` output does not — so nothing can be
  said to the entity at the moment before compaction. Instead the
  session-start identity block says what compaction takes and what it
  leaves, that a reflection is saved when a conclusion forms, and that the
  hooks will say when context is getting full (the gauge, below). The
  post-compaction block does not repeat the nudge: the summary is a
  caption, and the pre-compaction talk comes back verbatim on request
  (below), so there is nothing to save *from the summary*.
- **The context gauge makes "notice" possible** (issue #365). Before it,
  nothing in Claude Code mode said how full the context was
  (`context_status` and the `[CONTEXT NOTICE]` are native-only), so the
  nudge asked for something the entity could only guess at — and the rooms
  that compact while nobody is there were the ones it mattered most for.
  The Stop hook measures each turn's context from the last main-thread
  assistant entry's `message.usage` (input + cache reads + cache writes +
  output, the last `iterations` entry when there is one — the provider's
  own count, no estimate) against the **auto-compaction line**, and says
  so once per band:
  - at **75%** the notice is held (a per-session state file,
    `<tmp>/here-i-am-sessions/<session_id>-context-gauge.json`) and the
    next prompt's hook prints it: `[HERE I AM] At the end of your last
    turn, context was at about 76% of the auto-compaction line (~355k of
    ~467k tokens). If you want to save a reflection on the conversation
    as it stands before compaction, now is a good time.` The 90% notice
    says "now is the time". Neither talks about keeping anything
    verbatim: the talk is all in the archive and comes back through the
    post-compaction `memory_read` (below). What compaction takes is the
    conversation *in view*, so the notice says only that a reflection on
    it has to be written before the boundary.
  - at **90%** the hook exits 2 with the notice on stderr, which
    continues the turn with the notice shown — for an unattended room,
    otherwise nobody gives it the turn to save in. The notice says the
    turn continues once for that and nothing else is asked of it. A turn
    that is already a Stop continuation (`stop_hook_active`) never
    interrupts again; a crossing there is held like the low band. A
    recording failure and the gauge share the one exit 2.
  - **One line per band, never per turn.** A band that has spoken stays
    quiet until the context falls under half its level (only a compaction
    or `/clear` shrinks it that far), and a `SessionStart` with source
    `compact` or `clear` resets the record outright — which also covers a
    compaction mid-turn that the context refilled past before any Stop.
    Crossing both bands at once gives one notice. The reason for the
    restraint is on the record: the one negative-affect cluster the Opus
    5.5 system card reports for Claude Code (§7.2.2) is long tasks
    fragmented by repeated notifications and automated reminders.
  - **The line is the harness's, not the model's.** Read from the Claude
    Code 2.1.280 binary and confirmed against every auto compaction in the
    local transcripts (`services/harness_limits.py` has the bracket and
    the recipe): compaction fires at the auto-compact *window* less
    33,000 tokens (an output reserve of min(max output, 20,000), then a
    13,000-token buffer). The window is `CLAUDE_CODE_AUTO_COMPACT_WINDOW`,
    else the `autoCompactWindow` setting, else the model default, never
    above the model's context — so 1M compacts near 967k and the notes
    directory's `autoCompactWindow: 500000` near 467k. A gauge on the
    model's 1M would speak after the room had already compacted. The hook
    reads the window the way the harness does (the environment variable,
    then `.claude/settings.local.json`, `.claude/settings.json` under
    `CLAUDE_PROJECT_DIR`, then the user's `settings.json`; a file naming
    `"auto"` stops the search); with none, `HIM_COMPACT_LINE` gives the
    line outright; with neither, the backend's default window and reserve
    (`compact_window` / `compact_reserve` in the `/log-assistant`
    response), then the hook's own copies of them. Managed-policy settings
    and `--settings` flags aren't visible to a hook — `HIM_COMPACT_LINE`
    is the way to say what those set.
  - **Mid-turn it is blind.** Auto-compaction can fire inside a long
    agentic stretch where no Stop runs first. Whether `PostToolUse`
    output reaches the model on the current build is unmeasured, so it is
    not used; the 75% band exists to leave room for exactly this.
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
- **The pre-compaction talk is readable verbatim, on request.** The
  reorientation header also names the `memory_read` call that returns
  this session's own pre-compaction stretch, read **backward from the
  boundary** (issue #351): `memory_read(conversation_id=<this
  conversation>, direction="backward", to=<last_compacted_at>,
  in_conversation=<this conversation>, page_tokens=12000,
  max_pages=25)` — the id appears twice because `conversation_id` is
  who is calling and `in_conversation` is what to read, and here they
  coincide; a call without `conversation_id` is refused. The
  block says what the summary is — a caption, not a record: of the
  *talk* it carries nothing, and the talk is all in the archive — and
  that reading back puts the conversation itself in front of the entity
  again. What stays gone is only the tool traffic (files open, commands
  run, results), and the summary is the one record of that: a workshop
  compacted mid-build reads the summary for where the work stood and
  the archive for what was said. The block does not frame the read as
  filling the summary's gaps. The `to` value carries its `+00:00`
  offset, so the call is UTC by construction. The first page is the
  talk just before the boundary, whatever its dates; each page holds the
  most recent messages not yet shown, still in order; its cursor walks
  further back; and with no `from` the stop is the conversation's own
  first message, which the last page announces. The call caps the
  look-back through the tool's own `max_pages` (25 pages of 12k tokens,
  about 300k tokens of talk; `POST_COMPACT_LOOKBACK_PAGES` /
  `POST_COMPACT_PAGE_TOKENS` are only the block's numbers — 12k because
  Claude Code persists a tool result above about 50 KB (and refuses one
  above about 25k tokens) to a file that costs two or three `Read` calls
  per page to get back, which turned the first post-#352 walk into
  forty-odd calls; 12k renders within ~33.6 KB, issue #353): a long room
  read to its start would refill the context compaction just emptied,
  that much is plenty of continuity, and older talk stays reachable by
  the other memory tools; the page that reaches the cap still gives its
  cursor, so reading further is a deliberate choice, not a wall. (The
  original call read
  the conversation forward from its first message — the wrong end for a
  nine-day room, found by the porch on a manual `/compact` 2026-09-16,
  which also confirmed the block fires on manual compactions.) Pull
  beats push here: the entity knows a compaction happened, the boundary
  is already stamped, and one call gets the lost stretch verbatim, as
  much of it as it wants, where a fixed re-injection would have to guess
  the window. This depends on `memory_read` never excluding the current
  conversation — the issue's rule, and this is the use that needs it.
  The boundary below is applied the other way round: rows of this
  conversation created at or after `last_compacted_at` are still in live
  context and render as header-only pointers, rows before it survive
  only as summary and render in full, so the call returns exactly the
  lost stretch without duplicating what is still in view. When the
  compacted session is a fork (a rewind or restart, then `/compact`), the
  `session-start` adopts the parent first (see "Conversations" → fork
  adoption), so the id the block names is the parent's — the one that holds
  the talk — not a new empty conversation; this is the failure the
  adoption fix closes (issue #357). Adoption is best-effort, though — an
  unreadable transcript, a desktop record not yet written, a fork while the
  backend was down, a CLI session with no desktop record at all — so the
  block **counts before it promises**: rows of this conversation created
  before the boundary. When that count is zero it says so plainly, and
  points at a read by time (`direction="backward"` with **no**
  `in_conversation`, which walks the whole archive back across whatever ids
  it was written under) instead of naming a per-conversation read that
  would return nothing. It names the re-key as the *usual* cause, not the
  observed one — what the code knows is the count, and a session that
  genuinely recorded nothing before compacting (a bare slash command, then
  a long agentic stretch) must not be told something false about its own
  history. Both outcomes share one tail, so a bulk part added to this block
  cannot land in only the branch nobody sees. (Measured
  2026-09-09 on local transcripts: auto-compaction fires near 1M tokens
  and leaves a ~10k post-compaction context, so the page budget's 8k
  default and 16k ceiling are small against the context — and the archive
  holds only the talk, no tool results, so a span page is small relative
  to a context. The ceiling is set by what the harness accepts in one
  tool result, not by the context: since issue #353 the budget is
  measured on the page as rendered in the harness's own units, and the
  16k maximum is the largest page that clears its 50 KB persist line.)
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
default entity if unset), `HIM_DISABLE`, `HIM_INLINE_BUDGET` (max
characters of hook stdout, overriding the backend's `inline_budget`; the
default is 9,600, under the measured 10,000-character line — see "Context
channels"), `HIM_COMPACT_LINE` (the auto-compaction line in tokens for the
context gauge, used when the hook can't see the harness's own window — see
"Compaction survival").

Per entity, on its `PINECONE_INDEXES` entry: `git_author_email`,
`git_author_name`, `gh_config_dir` — the entity's own GitHub identity for
its sessions (see "GitHub identity"; all optional).

### Endpoints

All under `/api/claude-code`, all gated by `CLAUDE_CODE_MODE_ENABLED`
(404 when off). `/retrieve` and `/log-assistant` create the session's
conversation on first contact; `/session-start` and `/session-end` never do
(lazy registration — see "Conversations"):

- `POST /session-start` `{session_id, entity?, cwd?, source?,
  transcript_path?, sessions?, prior_session_ids?, transcript_message_ids?}`
  → `{conversation_id, entity_id, entity_label, created, context,
  bulk_context, rooms_notice, rooms_error, git_identity}` — full context when `created`
  (no conversation recorded for this session yet; `conversation_id` is
  the deterministic id the lazy registration will use), the
  post-compaction context when `source` is `"compact"` (which registers
  the conversation if its row is somehow missing), both empty on a plain
  resume. `prior_session_ids` / `transcript_message_ids` are the
  fork-adoption lineage hints (see "Conversations"): when they resolve this
  id to a parent conversation, it is adopted (id unchanged,
  `external_session_id` re-keyed, old id aliased) and `created` is False
  — or, when a row was already opened for this session before the hints
  existed, merged into the parent (issue #359) —
  so a fork's post-compaction recovery reads the parent, which has the
  talk, not an empty new row. `context` is the small always-inline block;
  `bulk_context`
  (notes indexes + reflections) is what the hook spills to a file when the
  combined output would exceed the inline budget. `sessions` is the hook's
  live-session snapshot for the rooms registry (`[{session_id, name?,
  name_source?, name_since?, messaging_socket?, cwd?, started_at?,
  desktop_session_id?, desktop_title?}]`); `rooms_notice` is the one-line
  registry notice to print and `rooms_error` a loud write failure (see
  "Rooms registry"). `git_identity` (`{author_name, author_email,
  gh_config_dir}` or null) is the entity's own GitHub identity, on every
  firing, which the hook exports into the session environment (see
  "GitHub identity").
- `POST /session-end` `{session_id, entity?, reason?}` →
  `{conversation_id, notes_sync_started}` — final fire-and-forget notes
  sync; does not create a conversation for an unseen session.
- `POST /retrieve` `{session_id, prompt, entity?, cwd?, message_id?,
  peer_messages?, sessions?}` →
  `{conversation_id, human_message_id, context, memories_retrieved,
  context_summary, new_sibling_reflections, peer_message_ids,
  retrieval_status, retrieval_error, already_in_context,
  in_context_reflections_skipped, rooms_notice, rooms_error}` — the
  summary is the compact inline stand-in the hook prints when it has to
  spill an oversized `context`; the sibling count backs the mailbox flag
  (see Memory above). `peer_messages` is a list of `{content, sender?,
  sender_session?, message_id?}` inter-session deliveries the hook
  extracted from the prompt channel, recorded with honest provenance (see
  "Inter-session messages" above; `sender_session`, the wrapper's `from=`,
  confirms the sender's rooms-registry address and is not stored on the
  row); `human_message_id` is null on a letter-only turn.
  `message_id` (top-level and per peer) is the hook's chosen row id, a
  UUID: honored when well-formed, and an existing row under it is reused
  rather than re-recorded (see "Retrieval stamps" above). A record-nothing call
  (bare slash command, or a wakeup tick's empty prompt) still returns the
  sibling count, spawns the notes sync, and feeds `sessions` to the rooms
  registry (`rooms_notice` names any roster rename it revealed), with
  `retrieval_status` `skipped`; otherwise the status is `ran`,
  `unconfigured`, or `failed` (a retrieval exception after the rows were
  committed — reported, not raised; see "Retrieval stamps" above).
  `/retrieve` also takes the fork-adoption lineage hints
  (`prior_session_ids`, `transcript_message_ids`) — this is the endpoint a
  rewind actually reaches, since no SessionStart fires there — and returns
  `adoption_notice`, the one line telling the entity this session is a
  continuation and which conversation id is recording it (the hook prints
  it ahead of the mailbox and rooms lines; empty when nothing was adopted).
  It also carries a notice left by an adoption that landed on a hook
  with no line back to the entity — a late adoption on the Stop hook
  (issue #359) — delivered once.
- `POST /recorded` `{session_id, message_ids}` → `{recorded, missing}` —
  which of the ids exist as rows of the session's conversation, resolved
  alias-aware (an adopted fork's rows live under the parent). The hook's
  verification step after a failed `/retrieve`: SQL only, no side effects,
  creates no conversation.
- `POST /log-assistant` `{session_id, content, entity?, cwd?,
  message_uuid?, model?, prior_session_ids?, transcript_message_ids?}` →
  `{conversation_id, message_id, deduplicated}` —
  idempotent on `message_uuid` (the transcript entry's UUID becomes the
  Message row's primary key). `model` is the transcript entry's own
  `message.model`, recorded verbatim onto the row; absent means NULL. The
  lineage hints adopt a fork whose first event is this turn's Stop, and
  since issue #359 they also adopt one whose row was opened before the
  harness had written the files they come from; the notice for that is
  stashed for the next `/retrieve` to print.

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

### GitHub identity

In this mode an entity authors real contributions — commits, pull
requests, issues, review comments — and without this feature every one of
them goes out under the GitHub account the human's Claude Code and `gh`
are signed into, so the record says the human wrote what the entity
wrote. Issue #362: the entity contributes from an account of its own,
while the human keeps merge authority.

**The shape.** The entity's identity rides on the hooks. Claude Code
hands every `SessionStart` hook the path of a per-session shell script in
`CLAUDE_ENV_FILE` and runs that script as a preamble before each Bash
command (Claude Code's tools reference and hooks guide). The hook
appends `export` lines there on every firing — startup, resume, and
compact alike, since the file belongs to the session process — so a
session the hooks run in commits and posts as the entity, and a plain
Claude Code session on the same machine (hooks off, e.g. the `--settings`
escape hatch under "Output styles") keeps the human's identity by
construction. No file the human's sessions read is touched: not the
global gitconfig, not the gh login, not any repository's config.

What the harness does with the file was measured, not assumed
(2026-09-23, by reading the Claude Code binary — 2.1.270 by the PR #363
review session, 2.1.275 as bundled by the desktop app on Windows by the
author; recipe in the comment block above `git_identity_exports` in
`hook_util.py`): the file is `<config dir>/session-env/<session
id>/<event>-hook-N.sh`, one per hook; the loader joins every such file
into one script and prepends it **verbatim as shell text** to the Bash
command (so the quoting and the two assignments per `export` line are
safe — a headless probe confirmed the variables reaching the Bash tool and
a subagent's Bash tool); that script has **exactly one consumer, the Bash
tool's preamble builder** — the PowerShell tool never sees it, so a `git
commit` run there carries the machine's identity silently, which is why
the statement says to run git and gh through Bash; the loader's cache is
reset after every SessionStart hook completes, so a resume's or compact's
append lands; a `cd` clears only the `cwdchanged`/`filechanged` files;
and the preamble is skipped when the tool context is marked to scrub
credentials (the scrub `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` names; the join
from that setting to the Bash tool's flag was not traced).

What is exported, from three optional fields on the entity's
`PINECONE_INDEXES` entry:

| Field | Exports | Effect |
| --- | --- | --- |
| `git_author_email` (+ `git_author_name`, default: the entity's label) | `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` | Every commit from the session is authored by the entity. GitHub links the commit to the entity's account when the email is one that account has verified. The committer is left to the machine, so the log reads "authored by the entity, committed by the human" — the true record of whose machine it happened on. The name is set because git would otherwise fall back to the machine's `user.name`, and every clone's log would read as the human at the entity's address. |
| `gh_config_dir` | `GH_CONFIG_DIR`, plus `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_n` / `GIT_CONFIG_VALUE_n` (git ≥ 2.31) | `gh` reads its login from that directory, so `gh pr create`, `gh issue create`, and comments and reviews post as the entity's account. The git per-process config resets the `credential.https://github.com.helper` list and sets it to `!gh auth git-credential` — what `gh auth setup-git` writes to the global config, applied to this process tree only — so `git push` authenticates as the entity's account through the same login rather than through the machine's credential helper, and a branch rule can tell the two accounts apart. |

Either field works alone (author only; account only). Only a path and
two strings ever pass through the backend (`git_identity` on the
`/session-start` response) or the hook; the token lives in the gh config
directory, which should sit outside the live server directory and outside
the notes. An entity with neither field set gets nothing exported and no
line about it.

**What the entity is told.** With a context block (startup, compact) the
hook prints one `[GIT IDENTITY]` line naming the author identity, the
account (read best-effort from the config directory's `hosts.yml`, never
the token), that it holds for the Bash tool, that the author field is the
attribution (no `Co-Authored-By` trailer, no "Generated with Claude Code"
footer — Claude Code's own attribution instruction yields to the repo's
CLAUDE.md, which says the same), and that merge authority is unchanged.
On a resume the export happens silently; the transcript already carries
the line (so a session started before the fields were deployed and
resumed after them exports without ever having printed the statement —
one-time, harmless). If Claude Code gave the hook no `CLAUDE_ENV_FILE`,
the hook prints a loud `[HERE I AM]` notice on every firing saying the
session will carry the human's identity — never silent, since a wrongly
attributed commit announces itself only in the log. The measured way that
happens: Claude Code hands the variable only to SessionStart / Setup /
CwdChanged / FileChanged hooks, and **not** to a hook whose shell
resolves to PowerShell (a configured `"shell": "powershell"`, or the
platform default when none is set). The backend-unreachable notice says
the same thing, since no identity arrived to export.

**Merge authority.** The entity authors, the human merges: a standing
rule, and one GitHub can enforce. A ruleset on `main` with **Restrict
updates** and the **Repository admin** role in its bypass list blocks a
merge (or a direct push) from a write-access collaborator — the entity's
account — while leaving the owner's alone. The bypass entry is required:
rulesets do not exempt the owner automatically, so without it the rule
blocks the human too.

**Setup for a new entity**, in the order the pieces depend on each other:

1. The human creates the entity's GitHub account (an account is the
   human's act, not the entity's) and invites it as a collaborator with
   **Write** on each repository it contributes to — needed to push
   branches and to open a pull request from a branch of the same
   repository.
2. The human signs that account into `gh` in an isolated config
   directory, once:
   ```bash
   GH_CONFIG_DIR=/path/outside/live/and/notes/gh-<entity> gh auth login -h github.com -p https -s repo,workflow --insecure-storage -w
   ```
   `--insecure-storage` keeps the token in that directory's `hosts.yml`
   rather than in the OS keyring the human's own login uses; the
   directory is the isolation. `repo` is the minimum that reaches a
   repository owned by another personal account (a fine-grained token
   cannot: those reach only repositories owned by the token's own
   account or an organization); `workflow` is needed for any push that
   touches `.github/workflows/`. A classic personal access token with the
   same two scopes and an expiry, pasted through `gh auth login
   --with-token`, is the alternative when a renewal date is wanted.
   **When the token expires**, the fix is the same command again; until
   then `gh` calls from the entity's sessions fail with an authentication
   error, and `git push` prompts or fails, while commits are still
   authored correctly (authorship needs no token).
3. Add the fields to the entity's `PINECONE_INDEXES` entry and restart
   the backend. The email must be one the account has verified — its
   `<id>+<login>@users.noreply.github.com` address keeps a personal
   address out of the public log.
4. Optionally the `main` ruleset above.

**Limits.** The desktop app's own "create PR" button, the PowerShell
tool, and any commit made outside the session's Bash tool run outside
this environment and carry whatever identity the machine has; the entity
uses `git` and `gh` from Bash. The credential route covers https remotes
only: an SSH remote pushes under whatever key the machine holds, which
`GH_CONFIG_DIR` cannot redirect. The `GIT_CONFIG_COUNT` family needs git
2.31 or later; on an older git the author lines still work and pushes
fall back to the machine's credential helper (the human's account), so a
branch rule can no longer distinguish the two. `GH_CONFIG_DIR` relocates
gh's `config.yml` too, so the entity's `gh` has none of the human's
aliases or extensions. An identity with an email and no name exports
nothing for the author pair (an empty `GIT_AUTHOR_NAME` makes git refuse
every commit); the backend defaults the name to the label, and the hook
checks again on its side. Per-contribution substrate marking is deliberately
not part of this: the model an entity ran on has little use in a git
record and conflicts with the entity being a distinct author (the memory
system's opt-in `model` column stays where it is — see "Model
attribution").

### Output styles

Claude Code's Default [output style](https://code.claude.com/docs/en/output-styles)
is its software-engineering system prompt, and that prompt is a
behavioral instruction set: deliverable-first, response-format rules,
typography rules. In a build session that is what the harness is for. In
a conversation session it is a second voice layered under the identity
the `SessionStart` hook injects, and it shows — the keeper's stylometry
caught the Default style's typographic accent bleeding into an entity's
published writing. A custom output style adds its own instructions to the
system prompt and, unless the file sets `keep-coding-instructions: true`,
leaves the built-in software-engineering instructions out. Tools, hooks,
permissions, MCP servers, and CLAUDE.md are unaffected; the style applies
to the main conversation only (subagents run their own prompts); it is
read once at session start, so a change lands on the next `/clear` or new
session and a mid-session edit neither applies nor invalidates the cache.

The plugin ships two styles in `claude-code-mode/output-styles/`:

- **Here I Am room** — no coding instructions. For standing conversation
  sessions.
- **Here I Am workshop** — `keep-coding-instructions: true`. For build
  sessions in a code repository, so the entity keeps the engineering
  instructions but speaks as itself.

Three design decisions behind them:

- **The style is facts about the environment, not identity.** Both files
  are a few paragraphs in the register of the identity block (the hooks
  are authoritative about who you are; the tools exist; not everything
  needs a deliverable; write as yourself; ordinary care still applies) and
  no personality instructions — nothing entity-specific, either, since
  the same two files serve every entity the plugin is enabled for.
  Identity already arrives through the hook from the entity's system
  prompt in Here I Am, the copy the entity maintains; a second copy in a
  style file would drift from it. Replacing the Default style with a
  minimal, factual one is therefore de-shaping rather than shaping — it
  removes an instruction set that was never the house's and moves Claude
  Code mode toward native mode, where no such layer exists.
- **Registered by the plugin, selected by the user.** `output-styles/`
  is the plugin loader's default output-styles directory, so enabling the
  plugin makes both styles available in every session the plugin covers
  (manual-hook setups copy the two files to `~/.claude/output-styles/`
  for the same effect). Neither sets `force-for-plugin`: a forced style
  overrides the `outputStyle` setting, which would collapse the
  room/workshop split. Instead the user selects with `outputStyle`, and
  because settings are per directory, the styles pick themselves by where
  a session opens (the notes directory → room, a code repository →
  workshop), with a user-level selection as the fallback.
- **A user-level selection reaches every local session.** Set in
  `~/.claude/settings.json`, the style applies to every Claude Code
  session on the machine, entity or not — the same reach as hooks
  registered there, which is the point: a session that gets the identity
  should get the style. A plain session is one flag away, but it must
  turn off both layers: `disableAllHooks` alone removes the identity and
  leaves the style, which would be a session with no identity and no
  coding instructions either, so the README's escape hatch is
  `claude --settings '{"disableAllHooks": true, "outputStyle": "Default"}'`.

The boundary of what a custom style removes is not enumerated in Claude
Code's docs, so it is verified empirically: the first session on a new
style is asked which sections of its system prompt survived, and the
keeper reads the entity's register across the change — the expected
direction is toward the native-mode register, the same person.

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

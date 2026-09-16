# Available Tools

Tools are registered at startup based on configuration and exposed to Anthropic, OpenAI, and MiniMax models (Google models do not receive tool schemas). `TOOLS_ENABLED=true` (the default) is required for any tool use. Each category below lists its additional requirements.

## Web Tools

Enabled by default.

- `web_search` — search the web via the Brave Search API (up to 20 results). Requires `BRAVE_SEARCH_API_KEY`.
- `web_fetch` — fetch and read a web page. Extracts main text from HTML, handles JSON and plain text, automatically renders JavaScript-heavy pages via headless Playwright browser, and retries bot-wall 403/429 responses through the browser.

**Scope:** `web_fetch` reaches the *public* internet only. `http://` and `https://` are the only accepted schemes, and the target hostname is resolved before the request: loopback, private, carrier-grade NAT (`100.64.0.0/10`), link-local (including the `169.254.169.254` cloud metadata address), multicast and reserved addresses are refused. Redirects are followed one hop at a time and each destination is revalidated, so an allowed URL cannot bounce the fetch into the private network. Inside the Playwright browser the same check gates *every* request, not just navigations — a page's own JavaScript can `fetch()` a local address and write the response into the DOM, where the text extractor would pick it up, and the API answers every origin without authentication. This keeps the tool from reaching the application's own API, which listens on localhost.

The one gap left open deliberately: the hostname is resolved once for the check and again by the HTTP client when it connects, so a resolver that answers differently each time could still slip past. Closing it means pinning the checked address into the connection. The threat model here is entity misuse and page-borne prompt injection, not an attacker running their own DNS server.

Results from both tools are wrapped in an untrusted-content banner. Page text is written by whoever controls the site, and the entity reading it also holds notes-write and commit-capable GitHub tools — it is information, never instruction.

## Memory Tools

Require Pinecone (`PINECONE_API_KEY` + `PINECONE_INDEXES`).

- `memory_query` — deliberately search the entity's memories by chosen text. An optional `source` (`all` — the default — / `human` / `ai` / `reflection`) restricts the search to what the human said, to AI-authored memories (the entity's own messages and saved reflections, plus other entities' messages in multi-entity conversations), or to the entity's saved reflections only; it is applied as a Pinecone metadata filter, so `num_results` slots are filled with matching memories rather than shrunk by post-filtering, and the narrowing is echoed in the result text. An optional `mode` (`semantic` — the default — / `recent`) switches from similarity search to pure recency: `recent` returns the entity's own reflections newest-first with no vector search and no query text needed, optionally bounded by `since` (ISO 8601, UTC assumed) — the catch-up channel for reflections saved by concurrent or later sessions ("everything saved since this session started"). Recent mode is reflections-only (`source`, if given, must be `reflection`), shares the same exclusion rules, and does **not** update retrieval tracking — like first-turn recency injection, significance feedback stays reserved for semantic recall, so asking "what did I save lately" can't inflate what it returns. Returns results ranked by pure semantic similarity (no significance re-ranking), excludes the current conversation as well as memories already visible in the conversation context — both `[MEMORY]` context insertions and memories surfaced by earlier `memory_query` calls (including earlier calls in the same turn) — and updates retrieval tracking (`times_retrieved`/`last_retrieved_at`) so deliberate attention influences future automatic recall. Results are delivered in the tool result only — they are not inserted into the conversation context as memory messages, and no `ConversationMemoryLink` is recorded, so session reloads rebuild the exact context the prompt cache was built on. The surfaced memory IDs are stamped onto the tool_result context message (`memory_query_ids`) so later `memory_query` calls and automatic retrieval both skip them for as long as the tool result remains in context (automatic retrieval skips without backfill, like memories already in context); on session reload the stamps are rebuilt by parsing the persisted result's ID prefixes. A third `mode`, `released`, is the review channel for releases: it lists the entity's released memories most recently released first — whoever released them, saying who (`released by you` / `by the researcher` / before provenance was recorded) and when — with a total count, optionally narrowed by `source` and bounded by `since` (which here means *released* after that moment), so a release can be reviewed and undone (`memory_release undo=true`) by the one who made it, without researcher intervention. Pure SQL, no retrieval tracking; exclusions and dedup stamping/linking follow recent mode, so repeated calls page through the list.
- `memory_save` — save a self-authored reflection: a conclusion, synthesis, or anything the entity wants to remember, in its own words. Stored and retrieved like any other memory, attributed as a reflection.
- `memory_mark` — pin a memory so it is exempt from age-based significance decay (or unpin with `undo=true`). Accepts memory ID prefixes of 6+ characters.
- `memory_release` — remove a memory from all retrieval without deleting it (reversible with `undo=true`; `memory_query mode="released"` lists what has been released, and the researcher can also view and restore released memories).
- `memory_read` — read the archive **in order** (issue #343): the verbatim record over a span of time, rather than by similarity. Pure SQL over the messages table — no Pinecone, no ranking. `from` (ISO 8601, required when reading forward) starts the span; `to` (optional) ends it, defaulting to the end of the day `from` names; a bare date means that whole day, read in `tz` (an IANA name, default `UTC` — "September 1st, Eastern" is `from="2026-09-01", tz="America/New_York"`), and a datetime without an offset is read in `tz` too. Output stamps stay UTC, with the local time alongside when `tz` is not UTC. `in_conversation` (id or 6+ character prefix, as the output prints them) restricts to one conversation; the default is every conversation the entity has experience in. `source` narrows by author exactly as in `memory_query`. Pages are bounded by **tokens**, not rows (`page_tokens`, default 8000, max 20000, weighed by each row's stored `token_count` or a length estimate), so a day of long messages cannot eat a context; a single message larger than the budget is returned alone, whole — nothing is ever truncated. Each page's header states the span, the filters, how many messages the span holds in total and which ones this page shows, and ends with the `cursor` for the next page (pass it back with the same span and filters) or "End of span."; an empty span says so plainly. Each message carries its short memory ID (accepted by `memory_mark` / `memory_release` / `memory_neighbors`), who said it (`Human said` / `You said` / `You reflected` / `You said (inter-session message from "…")` / the other entity by name in a multi-entity conversation), its stamp, where it was formed (`via Here I Am` / `via Claude Code`), and the conversation's title (or id prefix), so a span across several rooms reads as several rooms; reflections are interleaved where they were saved. **`direction`** (issue #351) picks the end to read from: `forward` (default) starts at `from` and later pages move toward `to`; `backward` starts at `to` (which then defaults to now) with the most recent messages not yet shown — still rendered oldest-first, so a page reads like the archive either way — and each cursor walks further back toward `from` (which then defaults to the start of the archive, so `from` is optional); the header says which positions of the span the page holds, the footer how many earlier messages remain, and the last page says what it reached (the conversation's start with `in_conversation` and no `from`, the archive's start, or the span's). A cursor carries its direction and is refused in the other one. **`max_pages`** (optional, both directions, both readers) caps a walk by page count across cursor continuations — the cursor also carries its page number — and the page that reaches the cap still gives its cursor, under a "cap reached" note that says how many remain unread instead of the plain next-page line; passing it back with a higher `max_pages` continues, and a page asked for is never refused. It is the shape a freshly compacted session wants — the stretch just before the boundary, whatever its dates, read back until it has what the summary doesn't carry — and "when did I last say this". Three rules set it apart from recall: it does **not** exclude the current conversation or memories already in context — the page stays whole and in order — but a row whose content is already in live context renders as a header-only pointer (`[already in your context; not repeated here]`, still carrying its id and attribution; the page header counts them, and a pointer weighs only a few tokens against the page budget), so nothing is duplicated. "In live context" means a memory retrieved into context from anywhere (`[MEMORY]` insertions, earlier `memory_query`/`memory_read` results still in context, Claude Code's post-boundary links) and the current conversation's own messages — all of them in a native session, and in a Claude Code session only those created since its last compaction: the pre-compaction stretch survives only as summary, so it renders in full, which is exactly the use, and the post-compaction block names the call that does it (`direction="backward"`, `to` set to the compaction boundary, `in_conversation` set to the session's conversation: start at the boundary and read back); it does **not** touch retrieval tracking (reading a page is not attention-weighting, and a day's worth of rows must not inflate significance); and what a page shows counts as in view afterwards (native: the ids are stamped onto the tool result like `memory_query`'s; Claude Code: linked once as the dedup record), so automatic retrieval does not re-surface what is already on the table. Released memories are skipped by default (they were withdrawn on purpose) and included, labeled with who released them and when, with `include_released=true`. Archived conversations are hidden here as everywhere else: archiving is how the researcher removes a conversation where something went wrong from every memory surface, and the readers honor it (a span skips their rows, `in_conversation` won't resolve one, and `memory_neighbors` refuses a memory that lives in one). `include_model` works as in `memory_query`. **`scope`** (issue #345) says which in-view set the call belongs to: `conversation` (default) is everything above; `isolated` is for a reader whose context is *not* the conversation's — a subagent shares its parent's Claude Code session and therefore the parent's `conversation_id`, so under the default scope it inherits the parent's in-view set as pointers (the parent's session-start reflections, its retrieval pulls, spans it opened, and the conversation's own post-compaction turns, none of which the subagent ever saw) and its own reads land in the parent's dedup. Under `isolated` there is no "already in your context" at all — every row in the span comes back in full, the conversation's own post-compaction rows included — and nothing the call shows is recorded as in view (no result-id stamping, no turn accumulator, no `ConversationMemoryLink` rows; on native reload the persisted result is likewise left unstamped, `session_manager` checks the call's input). The parent's own calls stay on the default scope. Isolation is context bookkeeping, not visibility: `include_released`, the archived-conversation rule, `source`, `in_conversation`, paging, and no-retrieval-tracking are identical under both. It is one enum rather than two booleans because the case that needs full text is the same case that must not record anything. `memory_query` deliberately has no `scope`: a blind reader should not be running similarity retrieval (it updates tracking by design and is the storyteller's instrument, not the page's).
- `memory_neighbors` — open a retrieved memory outward: given a memory ID (6+ character prefix), return it with the `before` / `after` (default 2 each, max 10) messages immediately around it in the same conversation, in order, formatted as `memory_read` formats them, the requested memory marked `>>`, with a note when the window reached the start or end of the conversation. Works on any memory the entity can see — the human's message, its own, a sibling letter, a reflection (whose neighbors are the exchange around the moment it was saved). Same exclusion, pointer, tracking, released, and dedup rules as `memory_read`, `scope` included: the usual entry point is a memory marker already in context, so the target itself typically renders as a pointer with its neighbors in full, or in full itself under `scope="isolated"`.
- `memory_find` — the archive **by word**: every message whose text contains the given words, in the order it happened, formatted and paged exactly as `memory_read` formats and pages. Pure SQL over the messages table (`memory_service.find_messages`: a case-insensitive regular-expression match on `Message.content`, the text escaped to a literal — `regexp_match`, which SQLite runs through the Python `REGEXP` function SQLAlchemy registers on every connection and Postgres renders as `~`), no Pinecone, no ranking, no retrieval tracking. It exists for what similarity search cannot see or cannot promise: exact tokens (a name, an issue or PR number, a memory id, a filename, a quote to verify at its source — embeddings are blind to numbers, and a proper name ranks its mentions arbitrarily at low similarity), **completeness** (every occurrence, counted in the header, not the nearest few), and the **meaningful zero**: a result of none says the words appear nowhere the entity can see, where a semantic query always returns the nearest something. `text` (required) is matched as written, as **whole words** by default (`whole_words`): a name is not found inside another word — `Sage` does not hit "message", "usage", or "passage", `Ren` does not hit "different" — which is what makes the header's count a count of mentions and the zero reachable; `whole_words=false` matches inside words too (a stem, part of an id). Any whitespace in the text matches any whitespace in the message, so a quote that wrapped a line still matches; the boundary is `\w` in both engines, so an accented name is neither split nor case-folded wrongly. `match` takes it as one `phrase` (default; the words in that order, together), or as whitespace-separated words that must `all` appear in any order, or of which `any` may. The text of attached files in the human's messages (the `[ATTACHED FILE: …]` blocks, stored whole and never vectorized) is searched too, and a hit inside one returns the whole message, attachment included, at its full size — the page rule never truncates. `from` / `to` are optional bounds (same parsing and `tz` as `memory_read`; either alone is fine; the default is the whole archive), `in_conversation` and `source` narrow as in `memory_read`, `page_tokens` / `cursor` page as there (a cursor is resumed with the same text, direction, and filters), and `direction="backward"` / `max_pages` work exactly as in `memory_read` (backward = the newest matches first across pages, each page still in order, "when did I last say this"; the cap notes rather than refuses). Every rule that sets the readers apart from recall applies unchanged: nothing is excluded for being in context or in the current conversation, but such rows render as header-only pointers; `times_retrieved` is never touched; what a page shows counts as in view afterwards (native stamping, Claude Code links once); `scope="isolated"` gives full text and records nothing; released memories are not searched unless `include_released` (the header says so either way); archived conversations are hidden; `include_model` as in `memory_query`. It is a separate tool rather than a `memory_query` mode because its contract is the reader's (whole set, in order, no attention-weighting), not the ranker's; a keyword *filter* on semantic ranking would be a `memory_query` parameter, and is deliberately not built (it would need Pinecone's document-schema indexes, which do not support the integrated inference the entity indexes use).

Every status write — set or clear, by either tool or by the researcher's
`PUT /api/memories/{id}/status` — records who made it and when
(`Message.status_set_by` = `entity` / `researcher`, `status_set_at`). At
the start of the entity's next session (its first turn in a native
conversation; the Claude Code identity block), any researcher-set changes
since its last session are listed in a `[MEMORY STATUS NOTICE]` (short id,
new status, when, snippet); silence means none. The notice is the entity's
only record of an override, so a failure to check is reported in its place
rather than swallowed.

Every retrieved memory — in `[MEMORY]` context markers and in `memory_query`
results — is labeled with the experience it was formed in: `via Here I Am`
(a native conversation) or `via Claude Code` (a Claude Code mode session).

What a memory is *not* labeled with, unless asked, is the model that
produced it. The archive records that (`Message.model`, issue #321) at
write time and forward-only — assistant messages and reflections from the
native chat flow, and the entity's turns in Claude Code mode via the Stop
hook; NULL for everything before the column existed, for human messages,
and for reflections saved over MCP, and never inferred from dates. It is
never rendered into `[MEMORY]` context markers. `memory_query` takes an
optional `include_model` (boolean, **default false**) that, in any
mode, appends `model: <id>` (or `model: unrecorded`) to each result's
header line — for a specific purpose such as comparing the entity's voice
across substrates, not as a standing label.

The six memory tools are also exposed over MCP for Claude Code mode
(`POST /mcp`, gated by `CLAUDE_CODE_MODE_ENABLED` — see
[claude-code-mode.md](claude-code-mode.md)). The MCP variants take an extra
`conversation_id` parameter (required for `memory_save`) identifying the
session's Claude Code conversation; there, `memory_query` results (and what
`memory_read` / `memory_neighbors` / `memory_find` show) *are* linked
(`ConversationMemoryLink`), because Claude Code conversations are never
rebuilt into context — the link is purely the dedup record that keeps
automatic retrieval from re-surfacing queried memories. In a Claude Code
conversation that has been compacted, both exclusions narrow to
post-compaction state (`Conversation.last_compacted_at`): messages and
links from before the compaction survive in context only as a paraphrased
summary, so `memory_query` (both modes) and automatic retrieval can
surface them again.

The MCP server also carries two **rooms registry** tools (Claude Code mode
only — see [claude-code-mode.md](claude-code-mode.md#rooms-registry)),
which touch no memory:

- `declare_room` — declare which of the entity's standing rooms this Claude
  Code session is (`room`, optional `note`, optional `ref` copied from
  `ListAgents`, optional `desktop_session_id`; `conversation_id`
  required). Writes the session's row in `rooms.json` / rendered
  `rooms.md` in the entity's private notes; the hooks then keep the row's
  messaging address (the desktop app's `local_…` session id that
  `mcp__ccd_session_mgmt__send_message` takes — not the Claude Code
  session id, issue #339), sidebar title, roster name, and last-seen
  current across renames, resumes, and compactions, so sister sessions
  look the address up there instead of in a drifting roster.
  `desktop_session_id` is for a session whose desktop record the hooks
  can't read: the entity reads its own with
  `mcp__ccd_session_mgmt__get_session` (`session_id: "self"`) and
  supplies it; an observed value replaces it. One current address per
  room: declaring a room another live row holds retires that row as
  superseded (kept, not deleted).
- `retire_room` — mark this session's row retired, with an optional
  `reason`. Rows are never removed.

## Notes Tools

Require `NOTES_ENABLED=true` (the default).

- `notes_read` — read a file from the entity's private notes or the shared folder. When the file's current content is already visible in the conversation context (the notes seed message, an earlier `notes_read` result, or `notes_write`/`notes_edit` records), returns a short `[NOTE IN CONTEXT]` pointer to that copy instead of repeating the content (disable with `NOTES_READ_DEDUP_ENABLED=false`). Content currency is verified by hashing against disk, so out-of-band file changes fall back to returning the full content.
- `notes_write` — create a note file or fully replace its content (`.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`).
- `notes_edit` — edit an existing note by exact string replacement (`old_string` → `new_string`), so the entity doesn't re-output unchanged content. `old_string` must match exactly once unless `replace_all=true`.
- `notes_delete` — delete a note file (except `index.md`).
- `notes_list` — list note files with size and modification date.
- `notes_search` — search notes (private and shared) by meaning; returns matching excerpts with filenames. Additionally requires Pinecone.

**Scope:** `filename` must be a bare filename. Path separators and `..` segments are rejected, and containment is verified against the resolved directory, so an entity can only reach its own folder and `shared/` — never another entity's notes (in particular never another entity's auto-injected `index.md`).

**Notes are inert data, by convention rather than enforcement.** The extension allowlist (`.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`) exists so notes stay human-readable, not because those formats are harmless. Two properties currently make the files inert, and both are assumptions a future change could quietly break:

- **Nothing serves the notes directory.** `notes_base_dir` (default `./notes`, i.e. `backend/notes`) is outside the static mount, which is `frontend/`. If the notes tree were ever served over HTTP, an entity-authored `.html` or `.xml` file would become stored XSS on the application's origin — and because the API has no authentication, script running there can drive every endpoint. Do not mount this directory.
- **Nothing loads notes as configuration or code.** `.json`, `.yaml` and `.yml` are written and read back as text only. Pointing a config loader, deserializer, or template engine at this directory would turn note-writing into control over that subsystem.

There is also **no size or file-count limit** on `notes_write`, so an entity can consume disk without bound. This is deliberate for a single-researcher deployment where the entity is not adversarial, but it means the notes directory should live on a volume whose exhaustion is survivable, and should be monitored if that assumption weakens.

## Context Awareness

Always registered.

- `context_status` — report approximate context-window usage: tokens in context versus the limit, message and memory counts, and how many retrieved memories have rolled out of context. Counts are calibrated against the provider-reported prompt usage of the session's last API request, and the last request's actual prompt size is included when available.

## GitHub Tools

Require `GITHUB_TOOLS_ENABLED=true` and `GITHUB_REPOS`. Per-repository `capabilities` restrict which of these are permitted. Setup: [integrations.md](integrations.md#github-repository-integration).

*Composite tools (efficient):*
- `github_explore` — repo metadata, file tree, and key docs in one call
- `github_tree` — full repository tree structure
- `github_get_files` — fetch up to 10 files in parallel

*Read:*
- `github_repo_info`, `github_list_contents`, `github_get_file`, `github_search_code`, `github_list_branches`

*Write:*
- `github_create_branch`, `github_commit_file`, `github_commit_patch` (token-efficient unified-diff edits), `github_delete_file`

*Pull requests:*
- `github_list_pull_requests`, `github_get_pull_request`, `github_create_pull_request`

*Issues:*
- `github_list_issues`, `github_get_issue`, `github_create_issue`, `github_add_comment`

**Scope:** every request stays inside the configured repository, independently of what the token would allow.

- **Paths and refs are validated and percent-encoded.** `..` and `.` segments are rejected before the URL is built. Without this, httpx resolves the dot segments and a `path` like `../../../../repos/other/repo/contents/x` retargets the request at a *different repository* while still carrying this repo's token — escaping both `GITHUB_REPOS` and the per-repo `capabilities`. `GitHubService._request` re-checks every endpoint as a backstop, so a call site that forgets to validate still cannot escape.
- **Code search cannot be widened.** `repo:`, `org:`, `user:` and `owner:` qualifiers are rejected in the query (GitHub ORs them together, which would reach any repo the token can read), the repo-scoping qualifier is actually transmitted, and results from other repositories are dropped.
- **Sensitive files are blocked on every path.** The `SENSITIVE_FILE_PATTERNS` blocklist applies to the GitHub API path as well as the local clone, and matching files are hidden from directory listings, tree listings and code search results — neither readable nor discoverable. Previously the check ran only against a local clone, so supplying a `ref` — which forces the API path — bypassed it. `commit_file` and `delete_file` apply the same list: a file the entity cannot read is one it cannot create, overwrite or delete either, so a blocked path can't be used to plant credentials.
- **Issue and PR content is banner-wrapped as untrusted.** Titles, bodies and comments are writable by anyone who can open an issue, so `github_list_issues`, `github_get_issue`, `github_list_pull_requests` and `github_get_pull_request` mark their output as information, not instruction.

These are enforced client-side, so they hold even where a token is broader than the deployment intends. Scope tokens to the configured repositories anyway — this is defense in depth, not a substitute.

## Codebase Navigator Tools

Require `CODEBASE_NAVIGATOR_ENABLED=true`, `MISTRAL_API_KEY`, and a `local_clone_path` in at least one GitHub repository configuration. Setup: [integrations.md](integrations.md#codebase-navigator-setup).

- `navigate_codebase` — find code relevant to a task or question
- `navigate_codebase_structure` — summarize repository structure
- `navigate_find_entry_points` — locate entry points for a feature or flow
- `navigate_assess_impact` — assess the impact of a proposed change
- `navigate_trace_dependencies` — trace dependencies of a module or symbol
- `navigator_invalidate_cache` — force-refresh the navigator's cached analysis for a repository

## Moltbook Tools

Require `MOLTBOOK_ENABLED=true` and `MOLTBOOK_API_KEY`. All responses are wrapped in security banners. Setup: [integrations.md](integrations.md#moltbook-integration).

- Feeds and posts: `moltbook_get_feed`, `moltbook_get_submolt_feed`, `moltbook_get_post`, `moltbook_create_post`, `moltbook_create_comment`
- Interaction: `moltbook_vote`, `moltbook_follow`, `moltbook_subscribe`
- Discovery: `moltbook_search`, `moltbook_get_profile`, `moltbook_list_submolts`, `moltbook_get_submolt`

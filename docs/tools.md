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

- `memory_query` — deliberately search the entity's memories by chosen text. An optional `source` (`all` — the default — / `human` / `ai` / `reflection`) restricts the search to what the human said, to AI-authored memories (the entity's own messages and saved reflections, plus other entities' messages in multi-entity conversations), or to the entity's saved reflections only; it is applied as a Pinecone metadata filter, so `num_results` slots are filled with matching memories rather than shrunk by post-filtering, and the narrowing is echoed in the result text. An optional `mode` (`semantic` — the default — / `recent`) switches from similarity search to pure recency: `recent` returns the entity's own reflections newest-first with no vector search and no query text needed, optionally bounded by `since` (ISO 8601, UTC assumed) — the catch-up channel for reflections saved by concurrent or later sessions ("everything saved since this session started"). Recent mode is reflections-only (`source`, if given, must be `reflection`), shares the same exclusion rules, and does **not** update retrieval tracking — like first-turn recency injection, significance feedback stays reserved for semantic recall, so asking "what did I save lately" can't inflate what it returns. Returns results ranked by pure semantic similarity (no significance re-ranking), excludes the current conversation as well as memories already visible in the conversation context — both `[MEMORY]` context insertions and memories surfaced by earlier `memory_query` calls (including earlier calls in the same turn) — and updates retrieval tracking (`times_retrieved`/`last_retrieved_at`) so deliberate attention influences future automatic recall. Results are delivered in the tool result only — they are not inserted into the conversation context as memory messages, and no `ConversationMemoryLink` is recorded, so session reloads rebuild the exact context the prompt cache was built on. The surfaced memory IDs are stamped onto the tool_result context message (`memory_query_ids`) so later `memory_query` calls and automatic retrieval both skip them for as long as the tool result remains in context (automatic retrieval skips without backfill, like memories already in context); on session reload the stamps are rebuilt by parsing the persisted result's ID prefixes.
- `memory_save` — save a self-authored reflection: a conclusion, synthesis, or anything the entity wants to remember, in its own words. Stored and retrieved like any other memory, attributed as a reflection.
- `memory_mark` — pin a memory so it is exempt from age-based significance decay (or unpin with `undo=true`). Accepts memory ID prefixes of 6+ characters.
- `memory_release` — remove a memory from all retrieval without deleting it (reversible with `undo=true`; the researcher can also view and restore released memories).

Every retrieved memory — in `[MEMORY]` context markers and in `memory_query`
results — is labeled with the experience it was formed in: `via Here I Am`
(a native conversation) or `via Claude Code` (a Claude Code mode session).

The four memory tools are also exposed over MCP for Claude Code mode
(`POST /mcp`, gated by `CLAUDE_CODE_MODE_ENABLED` — see
[claude-code-mode.md](claude-code-mode.md)). The MCP variants take an extra
`conversation_id` parameter (required for `memory_save`) identifying the
session's Claude Code conversation; there, `memory_query` results *are*
linked (`ConversationMemoryLink`), because Claude Code conversations are
never rebuilt into context — the link is purely the dedup record that keeps
automatic retrieval from re-surfacing queried memories. In a Claude Code
conversation that has been compacted, both exclusions narrow to
post-compaction state (`Conversation.last_compacted_at`): messages and
links from before the compaction survive in context only as a paraphrased
summary, so `memory_query` (both modes) and automatic retrieval can
surface them again.

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

# Available Tools

Tools are registered at startup based on configuration and exposed to Anthropic, OpenAI, and MiniMax models (Google models do not receive tool schemas). `TOOLS_ENABLED=true` (the default) is required for any tool use. Each category below lists its additional requirements.

## Web Tools

Enabled by default.

- `web_search` — search the web via the Brave Search API (up to 20 results). Requires `BRAVE_SEARCH_API_KEY`.
- `web_fetch` — fetch and read a web page. Extracts main text from HTML, handles JSON and plain text, automatically renders JavaScript-heavy pages via headless Playwright browser, and retries bot-wall 403/429 responses through the browser.

## Memory Tools

Require Pinecone (`PINECONE_API_KEY` + `PINECONE_INDEXES`).

- `memory_query` — deliberately search the entity's memories by chosen text. Returns results ranked by pure semantic similarity (no significance re-ranking), excludes the current conversation as well as memories already in the conversation context, and updates retrieval tracking so deliberate attention influences future automatic recall.
- `memory_save` — save a self-authored reflection: a conclusion, synthesis, or anything the entity wants to remember, in its own words. Stored and retrieved like any other memory, attributed as a reflection.
- `memory_mark` — pin a memory so it is exempt from age-based significance decay (or unpin with `undo=true`). Accepts memory ID prefixes of 6+ characters.
- `memory_release` — remove a memory from all retrieval without deleting it (reversible with `undo=true`; the researcher can also view and restore released memories).

## Notes Tools

Require `NOTES_ENABLED=true` (the default).

- `notes_read` — read a file from the entity's private notes or the shared folder.
- `notes_write` — create or update a note file (`.md`, `.json`, `.txt`, `.html`, `.xml`, `.yaml`, `.yml`).
- `notes_delete` — delete a note file (except `index.md`).
- `notes_list` — list note files with size and modification date.
- `notes_search` — search notes (private and shared) by meaning; returns matching excerpts with filenames. Additionally requires Pinecone.

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

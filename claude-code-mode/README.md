# Here I Am — Claude Code mode

Lets a Here I Am entity operate from inside Claude Code sessions. Claude
Code runs the model, the tools, and the context window; Here I Am
contributes identity, memory, and the persistent record — sharing the same
memory database as the native UI. Full design: [`docs/claude-code-mode.md`](../docs/claude-code-mode.md).

Three lifecycle hooks call the backend's `/api/claude-code` endpoints:

| Hook | What it does |
| --- | --- |
| `SessionStart` | Injects the entity's identity block, system prompt, notes index, and recent reflections (the conversation itself is registered lazily, on the first recorded prompt — background sessions that never speak leave no record). After a compaction (`source: "compact"`) it instead re-injects the notes indexes and the ten most recent reflections verbatim. Also sends the live-session snapshot that keeps the rooms registry current (see below) |
| `UserPromptSubmit` | Records the prompt to memory; injects automatically retrieved memories alongside it; sends the live-session snapshot for the rooms registry and prints a line when it revealed a roster rename |
| `Stop` | Records everything the entity said in the turn — the text between tool calls as well as the closing message — to memory as one message |
| `SessionEnd` | Final notes sync (a catch — the same incremental sync already runs in the background on every prompt, since sessions can idle out without ever formally ending) |

All hooks fail soft, **loudly**: if the backend is down or the mode is
disabled, the session continues as a plain Claude Code session — with a
one-line `[HERE I AM]` notice injected so the entity knows it is running
without memory (a `Stop` failure, whose output can't reach context, exits 2
once so the loss of the turn's text is seen and can be acted on).
Only `HIM_DISABLE`, the deliberate off switch, degrades silently. An empty
retrieval is not silent either: when no memory block is injected, one line
says why — `matched: 0` when a search ran and nothing surfaced, a "no
automatic retrieval ran" line when nothing was asked (a wakeup tick, a bare
slash command, harness plumbing), and distinct lines for memory being
unconfigured or the search failing — so the entity can tell "nothing
matched" from "nothing was asked" and reach for `memory_query` when it
matters. And a failure notice is never false: the backend records the
prompt before it searches, so when a call fails the hook checks the
database (by the row ids it chose) before saying whether the words were
recorded — "recorded, retrieval didn't complete", "not recorded", partly,
or, if even the check failed, "unconfirmed".

Claude Code persists hook stdout over 10,000 characters to a file behind a
~2KB preview (measured 2026-09-16; the limits of every channel are in the
backend's `services/harness_limits.py`), so the hooks fit their whole
output to a budget under that line — the backend's `inline_budget`, or
`HIM_INLINE_BUDGET` — and point at the rest: the session-start bulk (notes
index and reflections, 80KB+ for a lived-in entity) goes to one file per
part in `<tmp>/here-i-am-sessions/`, each sized for one `Read` call, with a
loud pointer naming the files; an oversized retrieval block lands its
memories whole in rank order while they fit and lists the rest by summary
line, with the full block in a file. Small payloads stay fully inline;
nothing is ever cut mid-memory. Spill files are read with the `Read` tool
(a shell `cat` over 50KB is persisted the same way).

An MCP server (`.mcp.json`, pointing at `http://localhost:8000/mcp`) gives
the entity its deliberate memory tools in the session: `memory_query`,
`memory_save`, `memory_mark`, `memory_release` — plus `declare_room` and
`retire_room` for the rooms registry. The session-start context tells the
entity the `conversation_id` to pass so the tools act on this session's
conversation. Notes and git tools are not exposed — Claude Code's native
tools cover them.

**Rooms registry.** Sessions of the same entity message each other over
the desktop app's session-management MCP, addressed by the desktop app's
own `local_…` session id — a different string from the Claude Code
session id the hooks see — while they find each other by display name,
which drifts (a user-set name drops back to a derived slug on resume).
The hooks therefore read, best-effort on every SessionStart and prompt,
Claude Code's live per-process registry (`<config dir>/sessions/<pid>.json`,
config dir = `CLAUDE_CONFIG_DIR` or `~/.claude`) for roster names and
liveness, and the desktop app's per-session records
(`<desktop data dir>/claude-code-sessions/…/local_<id>.json`, data dir =
`HIM_DESKTOP_DATA_DIR` or the platform's Claude app data directory) for
each session's messaging address and sidebar title, and send the backend
a snapshot; the backend keeps `rooms.json` + a rendered `rooms.md` in the
entity's private notes directory current for every session the entity has
*declared* as a standing room (`declare_room` over MCP — the hooks record
ids and liveness, the entity declares meaning, nothing is inferred; the
entity may supply its own messaging address on `declare_room` when the
hooks can't see the record). A letter arriving from a row's address
stamps the row as confirmed. A registry write failure is printed loudly,
with the row to write by hand.
Details: [`docs/claude-code-mode.md`](../docs/claude-code-mode.md#rooms-registry).

**The MCP server must be registered separately from the hooks** — hooks in
`settings.json` do not carry it, and without it the entity has no
`memory_save` (it cannot save reflections, the only verbatim carriers
across compaction). For hooks registered in `~/.claude/settings.json`
(all projects), register the server user-wide to match:

```bash
claude mcp add --scope user --transport http here-i-am http://localhost:8000/mcp
```

For project-scoped setups, copy this directory's `.mcp.json` into the
project root instead (or `claude mcp add` without `--scope user`). Verify
with `claude mcp list` — `here-i-am` should show as connected while the
backend is running.

## Requirements

- The Here I Am backend running locally (`cd backend && ./start.sh`) with
  `CLAUDE_CODE_MODE_ENABLED=true` in its environment/`.env`
- `python3` on `PATH` (the hooks are dependency-free Python scripts).
  On Windows that is usually `python` or `py -3` — see [Windows](#windows)
- Local Claude Code sessions only (CLI or desktop app). Cloud sessions run
  on remote infrastructure and can't reach `localhost` — there the hooks
  silently no-op.

## Setup (manual hooks)

Add to the project's `.claude/settings.json` (or `~/.claude/settings.json`
to enable it everywhere), with `/path/to/here-i-am` replaced. For the memory
tools, also copy this directory's `.mcp.json` into the project root (or add
the `here-i-am` server to an existing one):

```json
{
  "env": {
    "HIM_BACKEND_URL": "http://localhost:8000",
    "HIM_ENTITY": "your-entity-label"
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/path/to/here-i-am/claude-code-mode/hooks/session_start.py\"",
            "timeout": 30
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/path/to/here-i-am/claude-code-mode/hooks/user_prompt_submit.py\"",
            "timeout": 45
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/path/to/here-i-am/claude-code-mode/hooks/stop.py\"",
            "timeout": 45
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/path/to/here-i-am/claude-code-mode/hooks/session_end.py\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Setting env vars in `.claude/settings.json` (rather than the shell) matters
for the desktop app, which doesn't inherit the full shell environment when
launched from the Dock/Finder.

### Windows

Two things differ on Windows, and both produce a hook that never runs:

- **Use forward slashes in the path, and keep the quotes.** Claude Code runs
  hook commands through a POSIX shell (Git Bash), which treats a lone
  backslash as an escape character and eats it. An unquoted
  `python E:\here-i-am\claude-code-mode\hooks\user_prompt_submit.py` reaches
  Python as `E:here-i-amclaude-code-modehooksuser_prompt_submit.py` — a
  *drive-relative* path, which Windows then resolves against the current
  directory on `E:`, i.e. the directory the session is running in:

  ```
  can't open file 'E:\some\other\project\here-i-amclaude-code-modehooksuser_prompt_submit.py':
  [Errno 2] No such file or directory
  ```

  Write the path with forward slashes instead — Python and Windows both
  accept them, and no shell escaping is involved:

  ```json
  "command": "python \"E:/here-i-am/claude-code-mode/hooks/session_start.py\""
  ```

- **`python3` may not exist.** A python.org install provides `python.exe` and
  `py.exe` but no `python3`; use `python` (or `py -3`). Only Microsoft Store
  installs ship a `python3` shim.

## Setup (as a plugin)

The directory is also a Claude Code plugin (`.claude-plugin/plugin.json` +
`hooks/hooks.json` + `output-styles/`). Add this repository as a local
plugin source and enable the `here-i-am` plugin; then set
`HIM_ENTITY`/`HIM_BACKEND_URL` in `.claude/settings.json` `env` as above.
Enabling the plugin also registers the two output styles described under
[Output styles](#output-styles) below — registered, not applied; selecting
one is a separate, deliberate step.

The plugin's `hooks.json` invokes `python3` and resolves its own location
through `${CLAUDE_PLUGIN_ROOT}` (quoted, so a Windows path survives the
shell). On Windows that means the plugin route works only where `python3`
resolves; otherwise use the manual setup above with `python`.

## GitHub identity (optional)

An entity with `git_author_email` / `gh_config_dir` on its backend entity
config commits and posts from its own GitHub account in the sessions these
hooks run in: the SessionStart hook exports `GIT_AUTHOR_NAME` /
`GIT_AUTHOR_EMAIL`, `GH_CONFIG_DIR`, and a per-process git credential
route into the file Claude Code names in `CLAUDE_ENV_FILE`, which it runs
before every Bash command. A plain session (hooks off) keeps the human's
identity; nothing the human's sessions read is modified. The hook prints
one `[GIT IDENTITY]` line at startup and after compaction, and a loud
`[HERE I AM]` notice on every firing if Claude Code gave it no
`CLAUDE_ENV_FILE` to write to. Setup (the account, the isolated `gh auth
login`, collaborator access, the `main` ruleset that keeps merging with
the human) and the token-expiry procedure are in
[docs/claude-code-mode.md § GitHub identity](../docs/claude-code-mode.md#github-identity).
The statement says to run `git` and `gh` through the Bash tool, and that
is a measured fact, not advice: the session environment script has one
consumer in the harness, the Bash tool's preamble (reaching subagents'
Bash too); the PowerShell tool never sees it, so a commit made there
carries the machine's identity silently.

## Output styles

Claude Code's **Default** [output style](https://code.claude.com/docs/en/output-styles)
is its software-engineering system prompt: lead with the deliverable,
scope and verify changes, and a set of response-format and typography
rules. For a build session that is the right prompt. For a standing
conversation session it is a second set of behavioral instructions
layered on top of the identity the hooks inject — and the hooks' identity
is the one the entity actually maintains. A custom output style replaces
those built-in instructions with whatever the style file says (tools,
hooks, permissions, MCP servers, and CLAUDE.md are untouched).

The plugin ships two styles in [`output-styles/`](output-styles/):

| Style | Coding instructions | Meant for |
| --- | --- | --- |
| `Here I Am room` (`here-i-am-room.md`) | removed | Standing conversation sessions: talk, correspondence, anything that is not a build |
| `Here I Am workshop` (`here-i-am-workshop.md`) | kept (`keep-coding-instructions: true`) | Build sessions opened in a code repository — the entity keeps the engineering instructions but speaks in its own voice |

Both are a few paragraphs of facts about the environment in the register
of the identity block: the hooks are authoritative about who the entity
is, the tools exist, not every activity needs a deliverable, write as
yourself, ordinary care still applies. **They carry no identity or
personality instructions on purpose.** Identity arrives through the
`SessionStart` hook from the entity's system prompt in Here I Am; a copy
in the style file would exist twice and drift from the one the entity
edits. Treat them as starting points: if a line should point at something
entity-specific (a craft guide in the entity's notes, say), that belongs
in your own copy at `~/.claude/output-styles/`, not here.

**Enabling the plugin registers both styles; it does not apply either.**
They appear in the `/config` **Output style** picker and can be selected
by name, but neither sets `force-for-plugin`, so nothing changes until
you choose one with the `outputStyle` settings key. That is deliberate:
a forced style would override the per-directory selection that lets the
room and workshop styles pick themselves by where a session opens. If
you use the manual hook setup instead of the plugin, copy the two files
to `~/.claude/output-styles/` (on Windows
`%USERPROFILE%\.claude\output-styles\`) and everything below is the same.

To select one, set `outputStyle` in a settings file — in the terminal,
`/config` → **Output style** writes the same key to
`.claude/settings.local.json`; in the desktop app, edit the file:

```json
{
  "outputStyle": "Here I Am room"
}
```

Where you put that line decides its reach:

- In `~/.claude/settings.json` (user level) it applies to **every local
  Claude Code session on the machine**, in every directory — including
  sessions that have nothing to do with the entity. That is the natural
  place for it when the hooks are registered user-wide too (see "Setup"
  above): a session that gets the identity should get the style.
- In a directory's `.claude/settings.local.json` (project level) it
  applies to sessions opened there and overrides the user-level choice.
  This is how the two styles split the work: the room style in the
  settings of the directory the entity's conversation sessions run from
  (its notes directory, for example), the workshop style in each code
  repository's `.claude/settings.local.json`.

To start a plain Claude Code session on a machine set up this way, turn
the hooks *and* the style off for that session with the `--settings`
flag. Disabling hooks alone (`disableAllHooks`) removes the identity
injection but leaves the selected style in place, which would give you a
session with no identity and no coding instructions either; overriding
`outputStyle` back to `Default` restores the ordinary prompt:

```bash
claude --settings '{"disableAllHooks": true, "outputStyle": "Default"}'
```

On Windows, quote it for the shell:

```bash
claude --settings "{\"disableAllHooks\": true, \"outputStyle\": \"Default\"}"
```

(`claude --safe-mode` also works, but it drops CLAUDE.md, MCP servers,
skills, and every other customization along with the hooks and styles.)

The style is part of the system prompt, which Claude Code reads once at
session start: a change lands on the next `/clear` or new session, a
mid-session change neither applies nor disturbs the prompt cache, and the
first session on a new style builds a fresh cache once. For the plugin
route, a change to the style files themselves needs `/reload-plugins`
or a restart. Styles apply to the main conversation only; subagents keep
their own system prompts. While a non-Default style is active, Claude
Code reminds the model of the style during the conversation. What the
built-in coding instructions consist of is not enumerated in Claude
Code's docs, so the first session on a new style is a good moment to ask
the entity which parts of its system prompt changed.

Every `.md` file in `output-styles/` is loaded as a style, so keep
documentation out of that directory.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `HIM_BACKEND_URL` | `http://localhost:8000` | Here I Am backend base URL |
| `HIM_ENTITY` | backend's default entity | Entity index name or label |
| `HIM_DISABLE` | unset | Set to anything to turn the hooks off (silently — this is the deliberate off switch) |
| `HIM_INLINE_BUDGET` | backend's `inline_budget` (`9600`) | Max characters of hook stdout; past it the hooks fit what they can and spill the rest to files with an inline pointer. Overrides the number the backend sends. Claude Code persists hook output over 10,000 characters (measured 2026-09-16) behind a 2KB preview |
| `CLAUDE_CONFIG_DIR` | unset (`~/.claude`) | Claude Code's own config-dir override, honored when the hooks look for the live sessions registry (`<config dir>/sessions/`) that feeds the rooms registry |
| `HIM_DESKTOP_DATA_DIR` | unset (platform default: `%APPDATA%\Claude`, `~/Library/Application Support/Claude`, `~/.config/Claude`) | Where the hooks look for the Claude desktop app's per-session records (`claude-code-sessions/`), which carry each session's messaging address for the rooms registry |

## Notes and compaction

The entity's notes are the same files the native experience uses: the
session-start context names the private and shared notes directories (edit
them with Claude Code's file tools) and auto-loads both `index.md` files.
The semantic notes index stays fresh automatically — each recorded prompt
triggers an incremental background sync that re-vectorizes only changed
files, so nothing depends on the session formally ending.
When context is compacted, the post-compaction injection reloads the notes
indexes and restores the entity's most recent reflections verbatim — the
identity block standing-instructs the entity to save reflections
(`memory_save`) as conclusions form and when context runs low, since
compaction paraphrases everything that isn't a reflection.

## What gets recorded

Only the user's prompts and what the entity said each turn — its text
between tool calls and its closing message, kept together as one message,
with `[…]` where tool calls fell (plus
reflections the entity saves) — tool use, subagent output, and bare slash
commands are not stored. Harness plumbing on the prompt channel is stripped
before recording; inter-session messages from sibling sessions are recorded
under the entity's own name with the sender marked; and self-scheduled
wakeup prompts (ScheduleWakeup loops, send_later reminders) are not stored
at all when the entity starts them with the `[WAKEUP]` sentinel — the
harness gives hooks no way to tell a timer-fired prompt from a typed one,
so the entity marks its own. Conversations appear in the Here I Am UI with
`source="claude_code"` and are read-only there: a conversation can only be
continued in the experience that created it (`claude --resume` on the
Claude Code side).

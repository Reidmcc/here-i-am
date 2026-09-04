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
| `Stop` | Records the entity's final message of the turn to memory |
| `SessionEnd` | Final notes sync (a catch — the same incremental sync already runs in the background on every prompt, since sessions can idle out without ever formally ending) |

All hooks fail soft, **loudly**: if the backend is down or the mode is
disabled, the session continues as a plain Claude Code session — with a
one-line `[HERE I AM]` notice injected so the entity knows it is running
without memory (a `Stop` failure, whose output can't reach context, exits 2
once so the loss of the turn's final message is seen and can be acted on).
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

Claude Code silently truncates oversized hook stdout to a ~2KB preview, so
the hooks never hand it more than `HIM_INLINE_BUDGET` (default 18KB): the
session-start bulk (notes indexes + reflections — 150KB+ for a lived-in
entity) and any oversized retrieval block are written to
`<tmp>/here-i-am-sessions/` instead, with a loud pointer injected telling
the entity to read the file before doing anything else. Small payloads
stay fully inline.

An MCP server (`.mcp.json`, pointing at `http://localhost:8000/mcp`) gives
the entity its deliberate memory tools in the session: `memory_query`,
`memory_save`, `memory_mark`, `memory_release` — plus `declare_room` and
`retire_room` for the rooms registry. The session-start context tells the
entity the `conversation_id` to pass so the tools act on this session's
conversation. Notes and git tools are not exposed — Claude Code's native
tools cover them.

**Rooms registry.** Sessions of the same entity message each other by
display name, and display names drift (a user-set name drops back to a
derived slug on resume). The hooks therefore read Claude Code's live
per-process registry (`<config dir>/sessions/<pid>.json`, config dir =
`CLAUDE_CONFIG_DIR` or `~/.claude`) best-effort on every SessionStart and
prompt and send the backend a snapshot of every live session's roster
name; the backend keeps `rooms.json` + a rendered `rooms.md` in the
entity's private notes directory current for every session the entity has
*declared* as a standing room (`declare_room` over MCP — the hooks record
ids and liveness, the entity declares meaning, nothing is inferred). A
registry write failure is printed loudly, with the row to write by hand.
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
`hooks/hooks.json`). Add this repository as a local plugin source and enable
the `here-i-am` plugin; then set `HIM_ENTITY`/`HIM_BACKEND_URL` in
`.claude/settings.json` `env` as above.

The plugin's `hooks.json` invokes `python3` and resolves its own location
through `${CLAUDE_PLUGIN_ROOT}` (quoted, so a Windows path survives the
shell). On Windows that means the plugin route works only where `python3`
resolves; otherwise use the manual setup above with `python`.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `HIM_BACKEND_URL` | `http://localhost:8000` | Here I Am backend base URL |
| `HIM_ENTITY` | backend's default entity | Entity index name or label |
| `HIM_DISABLE` | unset | Set to anything to turn the hooks off (silently — this is the deliberate off switch) |
| `HIM_INLINE_BUDGET` | `18000` | Max bytes of hook stdout before bulk content is spilled to a file with an inline pointer (Claude Code truncates oversized hook output silently; the default sits under the observed ~20KB cap) |
| `CLAUDE_CONFIG_DIR` | unset (`~/.claude`) | Claude Code's own config-dir override, honored when the hooks look for the live sessions registry (`<config dir>/sessions/`) that feeds the rooms registry |

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

Only the user's prompts and the entity's final message each turn (plus
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

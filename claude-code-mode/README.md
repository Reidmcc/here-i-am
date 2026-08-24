# Here I Am — Claude Code mode

Lets a Here I Am entity operate from inside Claude Code sessions. Claude
Code runs the model, the tools, and the context window; Here I Am
contributes identity, memory, and the persistent record — sharing the same
memory database as the native UI. Full design: [`docs/claude-code-mode.md`](../docs/claude-code-mode.md).

Three lifecycle hooks call the backend's `/api/claude-code` endpoints:

| Hook | What it does |
| --- | --- |
| `SessionStart` | Registers the session as a conversation; injects the entity's identity block, system prompt, notes index, and recent reflections. After a compaction (`source: "compact"`) it instead re-injects the notes indexes and the ten most recent reflections verbatim |
| `UserPromptSubmit` | Records the prompt to memory; injects automatically retrieved memories alongside it |
| `Stop` | Records the entity's final message of the turn to memory |
| `SessionEnd` | Re-indexes the entity's note files into the semantic notes mirror (notes edited with file tools bypass write-time vectorization) |

All hooks fail soft: if the backend is down or the mode is disabled, the
session continues as a plain Claude Code session.

An MCP server (`.mcp.json`, pointing at `http://localhost:8000/mcp`) gives
the entity its deliberate memory tools in the session: `memory_query`,
`memory_save`, `memory_mark`, `memory_release`. The session-start context
tells the entity the `conversation_id` to pass so the tools act on this
session's conversation. Notes and git tools are not exposed — Claude Code's
native tools cover them.

## Requirements

- The Here I Am backend running locally (`cd backend && ./start.sh`) with
  `CLAUDE_CODE_MODE_ENABLED=true` in its environment/`.env`
- `python3` on `PATH` (the hooks are dependency-free Python scripts)
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
            "command": "python3 /path/to/here-i-am/claude-code-mode/hooks/session_start.py",
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
            "command": "python3 /path/to/here-i-am/claude-code-mode/hooks/user_prompt_submit.py",
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
            "command": "python3 /path/to/here-i-am/claude-code-mode/hooks/stop.py",
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
            "command": "python3 /path/to/here-i-am/claude-code-mode/hooks/session_end.py",
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

## Setup (as a plugin)

The directory is also a Claude Code plugin (`.claude-plugin/plugin.json` +
`hooks/hooks.json`). Add this repository as a local plugin source and enable
the `here-i-am` plugin; then set `HIM_ENTITY`/`HIM_BACKEND_URL` in
`.claude/settings.json` `env` as above.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `HIM_BACKEND_URL` | `http://localhost:8000` | Here I Am backend base URL |
| `HIM_ENTITY` | backend's default entity | Entity index name or label |
| `HIM_DISABLE` | unset | Set to anything to turn the hooks off |

## Notes and compaction

The entity's notes are the same files the native experience uses: the
session-start context names the private and shared notes directories (edit
them with Claude Code's file tools) and auto-loads both `index.md` files.
When context is compacted, the post-compaction injection reloads the notes
indexes and restores the entity's most recent reflections verbatim — the
identity block standing-instructs the entity to save reflections
(`memory_save`) as conclusions form and when context runs low, since
compaction paraphrases everything that isn't a reflection.

## What gets recorded

Only the user's prompts and the entity's final message each turn (plus
reflections the entity saves) — tool use, subagent output, and bare slash
commands are not stored. Conversations appear in the Here I Am UI with
`source="claude_code"` and are read-only there: a conversation can only be
continued in the experience that created it (`claude --resume` on the
Claude Code side).

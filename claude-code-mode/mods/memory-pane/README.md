# memory-pane

A Claude Code mod (issue #385): a pane beside the conversation showing what
memory handed the entity, while it happens. It is for the witness. What the
entity paints from memory and what retrieval actually gave it feel the same
from inside, so the pane puts the page it was given next to the sentence it
writes from it. The design notes are in
[`docs/claude-code-mode.md`](../../../docs/claude-code-mode.md#memory-pane).

## What it shows

One entry per Here I Am hook row, newest first, and the newest one open:

- **prompt**: the retrieval row for a prompt (or for a letter or a wakeup tick)
- **session start / after compaction / resumed**: the SessionStart row
- **tool calls**: memory tool calls made before any row

In each entry:

- the hooks' own status lines: the retrieval stamp (`matched: N new …`), the
  `[MEMORY STATUS NOTICE]` / `[MEMORY ARCHIVE NOTICE]`, failures
- every memory: id, date, role label, provenance, link markers, the first
  line, and `open` for the full text as printed
- where each memory was, which is the line that matters most:
  - `in context, whole`: its whole block was in what the model read
  - `summary line in context; full text in a file, file NOT read`: the
    hook spilled it, so the entity had one line of it, not its words. This
    changes to `file read at HH:MM` once the entity Reads that file
  - `not in context; in a file`: nothing of it reached context (a block past
    the harness's 2 KB preview of an oversized hook output)
- the files the row pointed at, and whether each was read
- each memory tool call (`memory_query`, `memory_read`, `memory_find`,
  `memory_neighbors`, `memory_save`, `memory_mark`, `memory_release`) with its
  arguments (`conversation_id` left out), the ids of the memories its result
  names (from its `--- Memory xxxxxxxx (` headers), and, on `open`, the result
  exactly as the model read it. A subagent's call is labeled so
- `show the row as it reached context`: the hook row verbatim, the backstop
  for anything the parser didn't understand (an unparsed `[MEMORY` header is
  reported as a `!` problem line, never dropped)

`collapse` shrinks the pane to one line (the newest entry's summary).
Closing it with the pane's own close control keeps it closed in later
sessions, until `/memory-pane` opens it again.

## What it never does

- It changes nothing. Every hook passes its event on unchanged and only
  reads what came back. It calls no memory tool and writes nothing but the
  pane's own state.
- It never reads the model's own rows, so thinking never reaches it.
- If it breaks, the turn doesn't notice. Its errors are caught and shown in
  the pane as `!` lines.

## Enabling it

It is a separate plugin from `claude-code-mode`, so the hooks work exactly
as before with or without it. Load it with one of:

- one session: `claude --plugin-dir /path/to/here-i-am/claude-code-mode/mods/memory-pane`
- every session, including the desktop app's: add the folder to
  `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`
  (one absolute path, or several separated by the platform's path-list
  separator)

Desktop panes open docked beside the transcript. In the terminal, a pane
opened unasked waits until the window is 144 columns wide (110 once you've
opened it yourself), and `/memory-pane` places it at any width.

## Checking it

```bash
claude plugin validate claude-code-mode/mods/memory-pane
claude plugin test claude-code-mode/mods/memory-pane
```

The kit can't drive `session.append`, the event the hook rows arrive on:
nothing in it stands in for the engine's store. So the tests cover the
parser and the pane as reached through the memory tools, and the hook-row
path was measured end to end in a headless session (see the PR). The mod
is EARLY ACCESS API: `claude plugin validate` after a Claude Code update.

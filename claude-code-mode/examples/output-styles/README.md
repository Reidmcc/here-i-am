# Example output styles

Two Claude Code [output styles](https://code.claude.com/docs/en/output-styles)
for an entity running in Claude Code mode:

| File | Style name | Keeps Claude Code's coding instructions? | For |
| --- | --- | --- | --- |
| `here-i-am-room.md` | `Here I Am room` | No | Standing conversation sessions — talk, correspondence, anything that is not a build |
| `here-i-am-workshop.md` | `Here I Am workshop` | Yes (`keep-coding-instructions: true`) | Build sessions opened in a code repository |

These are **examples, not part of the install**. Nothing copies them
anywhere, and they live deliberately outside the plugin's `output-styles/`
directory, which Claude Code would otherwise register for everyone who
enables the plugin. Whether to use an output style at all, and what it
should say, is each user's call. Copy one to `~/.claude/output-styles/` or
a project's `.claude/output-styles/`, edit it, and select it with the
`outputStyle` settings key — the full walkthrough, and what a style does
and does not change, is in
[the Claude Code mode README](../../README.md#output-styles-optional).

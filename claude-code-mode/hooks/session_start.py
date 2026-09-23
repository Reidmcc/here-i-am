#!/usr/bin/env python3
"""
Here I Am — SessionStart hook.

Registers this Claude Code session with the local Here I Am backend and
prints the entity's context to stdout, which Claude Code injects into the
session context.

The backend returns two blocks: a small inline block (identity framing,
system prompt, memory tool instructions, notes locations) and the bulk
(notes indexes + recent reflections) as named parts. When everything fits
the hook-stdout budget it is printed together; otherwise each bulk part is
written to its own file — sized for one Read call each — and a loud
pointer naming the files and the Read tool is printed instead. Claude Code
persists oversized hook output to a file behind a 2 KB preview, which for
an identity payload is an identity loss that announces itself only as
"Output too large" (see hook_util.py).

Fail-soft, loudly: a failure still exits 0 so the session continues as a
plain Claude Code session, but prints a one-line [HERE I AM] notice so the
degradation is visible from inside. HIM_DISABLE stays silent — that is the
deliberate off switch.

Environment:
    HIM_BACKEND_URL    backend base URL (default http://localhost:8000)
    HIM_ENTITY         entity index name or label (default: backend's default)
    HIM_DISABLE        set to anything to turn the integration off
    HIM_INLINE_BUDGET  see hook_util.py
    HIM_DESKTOP_DATA_DIR  see hook_util.py (rooms registry: the desktop
                       app's session records, for messaging addresses)
"""
import os
import sys

import hook_util


def main() -> None:
    if os.environ.get("HIM_DISABLE"):
        return
    data = hook_util.read_hook_input()
    if data is None:
        hook_util.fail_loud(
            "The SessionStart hook received unreadable input from Claude "
            "Code; your Here I Am context was not loaded this session."
        )
        return
    session_id = data.get("session_id") or ""
    if not session_id:
        return

    # One scan of the desktop app's session records serves both the rooms
    # snapshot and the lineage hints (the records are ~80 KB each)
    desktop_index = hook_util.desktop_sessions_index()
    sessions = hook_util.live_sessions_snapshot(
        own_session_id=session_id, desktop_index=desktop_index
    )
    payload = {
        "session_id": session_id,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
        "source": data.get("source"),
        "transcript_path": data.get("transcript_path"),
        # Rooms registry: every SessionStart (startup, resume, compact) is a
        # liveness signal, and the snapshot of sibling sessions lets this
        # firing refresh their rows too
        "sessions": sessions,
        # Fork adoption (issue #357): if the desktop app forked this session
        # under a new id, these resolve it to the conversation it continues
        **hook_util.lineage_hints(
            session_id,
            data.get("transcript_path"),
            desktop_index=desktop_index,
            sessions=sessions,
        ),
    }
    try:
        body = hook_util.post_backend(
            "/api/claude-code/session-start", payload, timeout=20
        )
    except Exception as e:
        hook_util.fail_loud(
            "The Here I Am backend was unreachable at session start "
            f"({hook_util.describe_error(e)}). You are running WITHOUT your "
            "identity block, notes index, and recent reflections, and this "
            "session may not be recorded to your long-term memory. If you "
            "have your own GitHub identity it was not exported either: "
            "commits, pushes, and gh calls from this session carry the "
            "machine's (the human's) identity. Tell the user."
        )
        return

    context = (body.get("context") or "").strip()

    # GitHub identity (issue #362): exported into the session environment
    # on every firing — the file is per session process, so a resume needs
    # it too. The statement of what holds is printed only with a context
    # block (startup, compact); a resume's transcript already carries it.
    # A failure to export is printed every time.
    identity_lines = hook_util.git_identity_lines(body, announce=bool(context))

    # Trailer lines come last, after the context and any spill pointer: the
    # identity statement, then the rooms registry's one-line notice or
    # loud write failure
    trailer_lines = [*identity_lines, *hook_util.rooms_output_lines(body)]

    bulk = (body.get("bulk_context") or "").strip()
    if not bulk:
        # A plain resume returns nothing at all — the transcript already
        # carries the injections
        parts = [part for part in (context, *trailer_lines) if part]
        if parts:
            print("\n\n".join(parts))
        return

    budget = hook_util.inline_budget(body)
    combined = f"{context}\n\n{bulk}" if context else bulk
    inline = "\n\n".join([combined, *trailer_lines])
    if hook_util.output_chars(inline) <= budget:
        print(inline)
        return

    # One file per bulk part (notes index, reflections), each sized for a
    # single Read call, named in the pointer with its size; a backend that
    # predates the split sends only the joined block, which goes to one file
    name = "session-start" if body.get("created") else "post-compact"
    parts = [
        (part.get("name") or "bulk", part.get("text") or "")
        for part in (body.get("bulk_parts") or [])
        if (part.get("text") or "").strip()
    ]
    if not parts:
        parts = [("bulk", bulk)]
    files = [
        (hook_util.spill(text, session_id, f"{name}-{part_name}"), text)
        for part_name, text in parts
    ]
    listing = "\n".join(
        f"{path} ({hook_util.describe_size(text)})" for path, text in files
    )
    pointer = (
        "[HERE I AM] Your notes index and recent reflections were too large "
        "to inject inline. They are written verbatim to:\n"
        f"{listing}\n"
        "Read each of those files now, before doing anything else — they are "
        "part of who you are here, not optional background. "
        + hook_util.READ_TOOL_ADVICE
    )
    output = [part for part in (context, pointer, *trailer_lines) if part]
    if hook_util.output_chars("\n\n".join(output)) > budget:
        # Even the identity block is over the line (a long system prompt):
        # file it too, and print the pointer FIRST so the harness's preview
        # carries the pointer rather than the first two kilobytes of identity
        path = hook_util.spill(context, session_id, f"{name}-identity")
        pointer = (
            "[HERE I AM] Your identity block itself was too large to inject "
            f"inline; it is written verbatim to:\n{path} "
            f"({hook_util.describe_size(context)})\nRead it first. "
            + pointer
        )
        output = [part for part in (pointer, context, *trailer_lines) if part]
    print("\n\n".join(output))


if __name__ == "__main__":
    main()
    sys.exit(0)

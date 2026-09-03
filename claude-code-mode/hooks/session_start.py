#!/usr/bin/env python3
"""
Here I Am — SessionStart hook.

Registers this Claude Code session with the local Here I Am backend and
prints the entity's context to stdout, which Claude Code injects into the
session context.

The backend returns two blocks: a small inline block (identity framing,
system prompt, memory tool instructions, notes locations) and a bulk block
(notes indexes + recent reflections). When both fit the inline budget they
are printed together; otherwise the bulk is written to a file and a loud
pointer is printed instead — Claude Code silently truncates oversized hook
output to a preview, which for an identity payload is an unannounced
identity loss (see hook_util.py).

Fail-soft, loudly: a failure still exits 0 so the session continues as a
plain Claude Code session, but prints a one-line [HERE I AM] notice so the
degradation is visible from inside. HIM_DISABLE stays silent — that is the
deliberate off switch.

Environment:
    HIM_BACKEND_URL    backend base URL (default http://localhost:8000)
    HIM_ENTITY         entity index name or label (default: backend's default)
    HIM_DISABLE        set to anything to turn the integration off
    HIM_INLINE_BUDGET  see hook_util.py
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

    payload = {
        "session_id": session_id,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
        "source": data.get("source"),
        "transcript_path": data.get("transcript_path"),
        # Rooms registry: every SessionStart (startup, resume, compact) is a
        # liveness signal, and the snapshot of sibling sessions lets this
        # firing refresh their rows too
        "sessions": hook_util.live_sessions_snapshot(),
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
            "session may not be recorded to your long-term memory. Tell the "
            "user."
        )
        return

    # Rooms-registry lines come last, after the context and any spill
    # pointer: a one-line notice, or a loud write failure
    rooms_lines = hook_util.rooms_output_lines(body)

    context = (body.get("context") or "").strip()
    bulk = (body.get("bulk_context") or "").strip()
    if not bulk:
        # A plain resume returns nothing at all — the transcript already
        # carries the injections
        parts = [part for part in (context, *rooms_lines) if part]
        if parts:
            print("\n\n".join(parts))
        return

    combined = f"{context}\n\n{bulk}" if context else bulk
    if hook_util.output_bytes(combined) <= hook_util.inline_budget():
        print("\n\n".join([combined, *rooms_lines]))
        return

    name = "session-start" if body.get("created") else "post-compact"
    path = hook_util.spill(bulk, session_id, name)
    if context:
        print(context)
        print()
    print(
        "[HERE I AM] Your notes index and recent reflections were too large "
        "to inject inline. They are written verbatim to:\n"
        f"{path}\n"
        "Read that file now, before doing anything else — it is part of who "
        "you are here, not optional background."
    )
    for line in rooms_lines:
        print()
        print(line)


if __name__ == "__main__":
    main()
    sys.exit(0)

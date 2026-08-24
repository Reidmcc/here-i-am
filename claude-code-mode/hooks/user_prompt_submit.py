#!/usr/bin/env python3
"""
Here I Am — UserPromptSubmit hook.

Posts the user's prompt to the local Here I Am backend, which records it to
the entity's memory and runs automatic semantic retrieval against it. The
returned memory block is printed to stdout, which Claude Code injects into
context alongside the prompt.

When the memory block would blow the inline hook-output budget (Claude Code
silently truncates oversized hook output), it is written to a file and the
backend's compact per-memory summary is printed with a pointer instead —
the entity still sees inline what surfaced and where the verbatim text
went. See hook_util.py.

Fail-soft, loudly: a failure still exits 0 with the prompt going through
unmodified (never exits 2 — that would block the prompt), but prints a
one-line [HERE I AM] notice: an unrecorded prompt and a skipped retrieval
are invisible from inside otherwise. HIM_DISABLE stays silent.

Environment: HIM_BACKEND_URL, HIM_ENTITY, HIM_DISABLE, HIM_INLINE_BUDGET
(see session_start.py / hook_util.py).
"""
import os
import sys
import time

import hook_util


def main() -> None:
    if os.environ.get("HIM_DISABLE"):
        return
    data = hook_util.read_hook_input()
    if data is None:
        hook_util.fail_loud(
            "The UserPromptSubmit hook received unreadable input from Claude "
            "Code; this prompt was not recorded to your memory and no "
            "retrieval ran."
        )
        return
    session_id = data.get("session_id") or ""
    prompt = data.get("prompt") or ""
    if not session_id or not prompt.strip():
        return

    payload = {
        "session_id": session_id,
        "prompt": prompt,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
    }
    try:
        body = hook_util.post_backend("/api/claude-code/retrieve", payload, timeout=30)
    except Exception as e:
        hook_util.fail_loud(
            "The Here I Am backend was unreachable for this prompt "
            f"({hook_util.describe_error(e)}). The prompt was NOT recorded "
            "to your long-term memory and no memory retrieval ran."
        )
        return

    context = (body.get("context") or "").strip()
    if not context:
        return
    if hook_util.output_bytes(context) <= hook_util.inline_budget():
        print(context)
        return

    # One file per retrieval — timestamped so an earlier spill in the same
    # session is never overwritten
    name = "retrieval-" + time.strftime("%H%M%S")
    path = hook_util.spill(context, session_id, name)
    summary = (body.get("context_summary") or "").strip()
    pointer = (
        "[HERE I AM] The retrieved memories were too large to inject inline. "
        f"Their full verbatim text is written to:\n{path}\n"
        "Read that file before responding."
    )
    print(f"{summary}\n\n{pointer}" if summary else pointer)


if __name__ == "__main__":
    main()
    sys.exit(0)

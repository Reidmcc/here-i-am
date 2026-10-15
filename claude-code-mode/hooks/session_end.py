#!/usr/bin/env python3
"""
Here I Am — SessionEnd hook.

Tells the backend the session ended so it can re-index the entity's note
files into the semantic notes mirror (notes edited with Claude Code's file
tools bypass the write-time vectorization the native notes tools do).

Fail-soft: any problem exits 0 silently. Nothing is printed — the session
is over, there is no context to add to.

Environment: HIM_BACKEND_URL, HIM_ENTITY, HIM_DISABLE (see session_start.py).
"""
import json
import os
import sys

import hook_util


def main() -> None:
    if os.environ.get("HIM_DISABLE"):
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    session_id = data.get("session_id") or ""
    if not session_id:
        return

    payload = {
        "session_id": session_id,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "reason": data.get("reason"),
    }
    # SessionEnd hooks run under a tight time budget; the endpoint returns
    # immediately (the reindex continues server-side), so one short try
    # (hook_util.SESSION_END_TIMEOUTS) is all it gets
    try:
        hook_util.post_backend(
            "/api/claude-code/session-end", payload, timeout=hook_util.SESSION_END_TIMEOUTS
        )
    except Exception:
        return


if __name__ == "__main__":
    main()
    sys.exit(0)

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
import urllib.request


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
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + "/api/claude-code/session-end",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    # SessionEnd hooks run under a tight time budget; the endpoint returns
    # immediately (the reindex continues server-side), so a short timeout
    # is enough for the localhost round-trip.
    try:
        urllib.request.urlopen(request, timeout=3).close()
    except Exception:
        return


if __name__ == "__main__":
    main()
    sys.exit(0)

#!/usr/bin/env python3
"""
Here I Am — SessionStart hook.

Registers this Claude Code session with the local Here I Am backend and
prints the entity's context block (identity, system prompt, recent
reflections) to stdout, which Claude Code injects into the session context.

Fail-soft: any problem (backend down, mode disabled, bad input) exits 0
with no output, so the session continues as a plain Claude Code session.

Environment:
    HIM_BACKEND_URL  backend base URL (default http://localhost:8000)
    HIM_ENTITY       entity index name or label (default: backend's default)
    HIM_DISABLE      set to anything to turn the integration off
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
        "cwd": data.get("cwd"),
        "source": data.get("source"),
    }
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + "/api/claude-code/session-start",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
    except Exception:
        return

    context = (body.get("context") or "").strip()
    if context:
        print(context)


if __name__ == "__main__":
    main()
    sys.exit(0)

#!/usr/bin/env python3
"""
Here I Am — UserPromptSubmit hook.

Posts the user's prompt to the local Here I Am backend, which records it to
the entity's memory and runs automatic semantic retrieval against it. The
returned memory block is printed to stdout, which Claude Code injects into
context alongside the prompt.

Fail-soft: any problem exits 0 with no output — the prompt goes through
unmodified. Never exits 2 (that would block the prompt).

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
    prompt = data.get("prompt") or ""
    if not session_id or not prompt.strip():
        return

    payload = {
        "session_id": session_id,
        "prompt": prompt,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
    }
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + "/api/claude-code/retrieve",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except Exception:
        return

    context = (body.get("context") or "").strip()
    if context:
        print(context)


if __name__ == "__main__":
    main()
    sys.exit(0)

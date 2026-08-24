#!/usr/bin/env python3
"""
Here I Am — Stop hook.

Fires when the assistant finishes a turn. Extracts the final assistant
message of the turn from the session transcript (text blocks only — tool
use stays Claude Code's business) and posts it to the local Here I Am
backend, which records and vectorizes it as the entity's response.

The transcript entry's UUID rides along so the backend can deduplicate a
re-fired hook. Only the main conversation loop is logged — this script is
wired to Stop, not SubagentStop, so subagent turns never write the
entity's memory.

Fail-soft, loudly: when the backend can't be reached the final message of
the turn — the sole memory-bearing artifact of everything that happened in
it — is lost from the archive. A Stop hook's stdout never reaches context,
so the failure is escalated the one way the entity can see it: exit 2 with
the notice on stderr, which continues the turn with the message shown. The
entity can then preserve what mattered another way and tell the user. The
escalation is guarded by stop_hook_active so a persistently down backend
gets exactly one loud retry per turn, never a loop; the retry's Stop fires
with stop_hook_active set and any failure there exits 0 silently.

Environment: HIM_BACKEND_URL, HIM_ENTITY, HIM_DISABLE (see session_start.py).
"""
import json
import os
import sys
import urllib.request


def last_assistant_text(transcript_path: str):
    """
    The final assistant message of the turn: the last transcript entry of
    type "assistant" whose message carries at least one non-empty text
    block. Returns (text, entry_uuid) or (None, None).
    """
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None, None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
        else:
            continue
        text = "\n\n".join(t for t in texts if t and t.strip()).strip()
        if text:
            return text, entry.get("uuid")
    return None, None


def main() -> None:
    if os.environ.get("HIM_DISABLE"):
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return

    text, entry_uuid = last_assistant_text(transcript_path)
    if not text:
        return

    payload = {
        "session_id": session_id,
        "content": text,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
        "message_uuid": entry_uuid,
    }
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + "/api/claude-code/log-assistant",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except Exception as e:
        if data.get("stop_hook_active"):
            return
        print(
            "[HERE I AM] The Here I Am backend was unreachable at the end of "
            f"this turn ({e.__class__.__name__}: {e}). Your final message was "
            "NOT recorded to your long-term memory. Preserve anything "
            "important another way (memory_save via MCP if available, or your "
            "notes files), and tell the user the backend is down.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
    sys.exit(0)

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

The context gauge (issue #365) rides the same channel. After every turn
the hook measures the context against the auto-compaction line and, the
first time it crosses a band, says so: the top band by exit 2 (the turn
continues so the entity can save a reflection on the conversation while
it is still in view), the lower
band held for the next prompt. Once per band, never per turn, and never an
interrupt on a turn that is itself a continuation (see hook_util).

Environment: HIM_BACKEND_URL, HIM_ENTITY, HIM_DISABLE (see session_start.py),
HIM_COMPACT_LINE (see hook_util.py).
"""
import json
import os
import sys
import urllib.request

import hook_util


def entry_model(entry: dict):
    """
    The model that produced a transcript entry, as the entry reports it
    (message.model on assistant entries), or None. The value is recorded
    verbatim into the archive's model column (issue #321) — read, never
    inferred, so a missing field stays a NULL rather than a guess.
    """
    message = entry.get("message") or {}
    model = message.get("model") if isinstance(message, dict) else None
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def last_assistant_text(transcript_path: str):
    """
    The final assistant message of the turn: the last transcript entry of
    type "assistant" whose message carries at least one non-empty text
    block. Returns (text, entry_uuid, model) or (None, None, None) — model
    is the entry's own attribution (see entry_model), None when absent.
    """
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None, None, None

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
            return text, entry.get("uuid"), entry_model(entry)
    return None, None, None


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

    # Everything that has to reach the entity from here goes out as one
    # exit 2: a recording failure, the context gauge's top band, or both
    notices = []
    body = {}
    text, entry_uuid, model = last_assistant_text(transcript_path)
    if text:
        body, failure = record_final_message(data, text, entry_uuid, model)
        if failure and not data.get("stop_hook_active"):
            notices.append(failure)

    # The context gauge (issue #365): measured after every turn, spoken
    # once per band — see hook_util. A continuation turn never interrupts
    # again; its notice is held for the next prompt
    gauge = hook_util.check_context_gauge(
        session_id,
        hook_util.last_context_tokens(transcript_path),
        hook_util.compact_line(
            body, os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd")
        ),
        may_interrupt=not data.get("stop_hook_active"),
    )
    if gauge:
        notices.append(gauge)

    if notices:
        print("\n\n".join(notices), file=sys.stderr)
        sys.exit(2)


def record_final_message(data: dict, text: str, entry_uuid, model):
    """
    Post the turn's final message to /log-assistant. Returns (response
    body, failure notice): the body is {} when it isn't JSON — the message
    was still recorded — and the notice is None on success.
    """
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    payload = {
        "session_id": session_id,
        "content": text,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
        "message_uuid": entry_uuid,
        # Which model wrote the message — from the transcript entry itself,
        # the one place it is knowable at write time (issue #321)
        "model": model,
        # Fork adoption (issue #357): the first recorded event of a forked
        # session may be this turn's Stop, so carry the lineage hints here too
        **hook_util.lineage_hints(session_id, transcript_path),
    }
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + "/api/claude-code/log-assistant",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except Exception as e:
        return {}, (
            "[HERE I AM] The Here I Am backend was unreachable at the end of "
            f"this turn ({e.__class__.__name__}: {e}). Your final message was "
            "NOT recorded to your long-term memory. Preserve anything "
            "important another way (memory_save via MCP if available, or your "
            "notes files), and tell the user the backend is down."
        )
    try:
        body = json.loads(raw)
    except Exception:
        body = None
    return (body if isinstance(body, dict) else {}), None


if __name__ == "__main__":
    main()
    sys.exit(0)

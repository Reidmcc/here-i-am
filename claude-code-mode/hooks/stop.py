#!/usr/bin/env python3
"""
Here I Am — Stop hook.

Fires when the assistant finishes a turn. Collects everything the entity
said in the turn from the session transcript — every text chunk since the
turn began, the text between tool calls as well as the closing message,
joined in order into ONE message with "[…]" where tool calls fell (issue
#364) — and posts it to the local Here I Am backend, which records and
vectorizes it as the entity's response. The archive is the talk: tool use
is deeds and stays Claude Code's business, and thinking blocks are never
read.

The turn's last text entry's UUID rides along as the row id so the backend
can deduplicate a re-fired hook. Only the main conversation loop is logged —
this script is wired to Stop, not SubagentStop, and sidechain entries are
skipped, so subagent turns never write the entity's memory.

Fail-soft, loudly: when the backend can't be reached the turn's text — the
sole memory-bearing artifact of everything that happened in it — is lost
from the archive. A Stop hook's stdout never reaches context,
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

import hook_util  # also reconfigures stdio to UTF-8 on import


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


# Written on its own line between two chunks of a turn's text wherever
# the entity called a tool in between, so a later reading shows that work
# happened between the sentences without describing it (issue #364).
TOOL_CALL_MARKER = "[…]"
# Written where a prompt queued mid-turn reached the entity, once per
# kind: its row is recorded when it arrives, before this turn's row, so
# without the line the chunks said before it would read as a reply to it.
# Wording chosen by Pseudo (issue #364 review, PR #372).
HUMAN_ARRIVED_MARKER = "[… the human's message arrived here]"
LETTER_ARRIVED_MARKER = "[… a letter arrived here]"


def turn_assistant_text(transcript_path: str):
    """
    Everything the entity said in the turn that just ended: every text
    block of its own since the turn's boundary (hook_util.is_turn_boundary),
    in transcript order, joined by blank lines, with TOOL_CALL_MARKER
    between two chunks that had a tool call between them, and an arrival
    marker where a prompt queued mid-turn reached the entity after it had
    already spoken (hook_util.queued_arrival). Nothing is filtered — a
    short "checking now" is talk too.

    Returns (text, entry_uuid, model) or (None, None, None). entry_uuid is
    the turn's LAST text entry's uuid, which becomes the row id: a re-fired
    hook dedups on it, and it is the id the fork-adoption hint sends for
    this turn (hook_util.transcript_assistant_uuids). model is that
    entry's own attribution (see entry_model), None when absent.
    """
    pieces, entry_uuid, model = [], None, None
    tool_since_text = False
    try:
        for entry in hook_util.iter_turn_entries(transcript_path):
            if hook_util.is_turn_boundary(entry):
                pieces, entry_uuid, model = [], None, None
                tool_since_text = False
                continue
            arrival = hook_util.queued_arrival(entry)
            if arrival is not None:
                # Before any text the whole row already follows the
                # arrival, so there is nothing to mark
                if pieces:
                    if tool_since_text:
                        pieces.append(TOOL_CALL_MARKER)
                        tool_since_text = False
                    human_spoke, letters = arrival
                    if human_spoke:
                        pieces.append(HUMAN_ARRIVED_MARKER)
                    if letters:
                        pieces.append(LETTER_ARRIVED_MARKER)
                continue
            has_text = False
            for kind, text in hook_util.entry_text_blocks(entry):
                if kind == "tool":
                    tool_since_text = True
                    continue
                if pieces and tool_since_text:
                    pieces.append(TOOL_CALL_MARKER)
                pieces.append(text)
                tool_since_text = False
                has_text = True
            if has_text:
                entry_uuid = entry.get("uuid") or entry_uuid
                model = entry_model(entry)
    except Exception:
        return None, None, None
    if not pieces:
        return None, None, None
    return "\n\n".join(pieces), entry_uuid, model


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
    text, entry_uuid, model = turn_assistant_text(transcript_path)
    if text:
        body, failure = record_final_message(data, text, entry_uuid, model)
        if failure and not data.get("stop_hook_active"):
            notices.append(failure)

    # The context gauge (issue #365): measured after every turn, spoken
    # once per band — see hook_util. A continuation turn never interrupts
    # again; its notice is held for the next prompt
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd")
    if hook_util.auto_compact_enabled(project_dir):
        tokens, context_model = hook_util.last_context_usage(transcript_path)
        gauge = hook_util.check_context_gauge(
            session_id,
            tokens,
            hook_util.compact_line(body, project_dir, context_model),
            may_interrupt=not data.get("stop_hook_active"),
            # A fork carries its parent's context, so it carries its bands
            parents=lambda: hook_util.desktop_prior_session_ids(session_id),
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
            f"this turn ({e.__class__.__name__}: {e}). What you said this turn "
            "was NOT recorded to your long-term memory. Preserve anything "
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

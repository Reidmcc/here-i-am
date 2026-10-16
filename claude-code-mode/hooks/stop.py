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

The notice is never false (issue #400, the prompt hook's rule from #326).
A failure can arrive after the backend committed the row (a read timeout
or a dropped connection, ordinary across a network), so only a request
that provably never reached the backend is reported as NOT recorded
outright. Any other failure is checked against /recorded by the row id
this hook chose: recorded means nothing was lost and nothing is said;
missing is NOT recorded; a check that fails too is UNCONFIRMED.

Environment: HIM_BACKEND_URL, HIM_ENTITY, HIM_DISABLE (see session_start.py).
"""
import json
import os
import sys
import uuid

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
    hook_util.start_hook_clock("Stop")
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return

    failure = None
    text, entry_uuid, model = turn_assistant_text(transcript_path)
    if text:
        failure = record_final_message(data, text, entry_uuid, model)

    if failure and not data.get("stop_hook_active"):
        print(failure, file=sys.stderr)
        sys.exit(2)


def record_final_message(data: dict, text: str, entry_uuid, model):
    """
    Post the turn's final message to /log-assistant. Returns the failure
    notice, or None on success.
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
    try:
        hook_util.post_backend(
            "/api/claude-code/log-assistant",
            payload,
            timeout=hook_util.LOG_ASSISTANT_TIMEOUTS,
        )
    except Exception as e:
        return recording_failure_notice(e, session_id, entry_uuid)
    return None


PRESERVE = (
    "Preserve anything important another way (memory_save via MCP if "
    "available, or your notes files), and tell the user"
)


def _canonical_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def recording_failure_notice(error, session_id: str, entry_uuid):
    """
    The notice for a failed /log-assistant call, accurate about whether
    the turn was recorded; None when it was (the answer was lost after
    the commit), since then nothing was lost.

    Recorded-but-unanswered may leave the row's vectorization unfinished,
    as the prompt hook says of its own rows; that is not said here, since
    a Stop notice costs the entity a continued turn and the row itself is
    whole. The server log shows it.

    A row found missing is NOT recorded only when the server's handler is
    over (it answered, with an error); after a read deadline or a dropped
    connection it may still be running and land the row later, so missing
    is then UNCONFIRMED (hook_util.request_may_still_be_running).
    """
    err = hook_util.describe_error(error)
    if hook_util.never_reached_backend(error):
        return (
            "[HERE I AM] The Here I Am backend could not be reached at the end "
            f"of this turn ({err}). What you said this turn was NOT recorded to "
            f"your long-term memory. {PRESERVE} the backend could not be reached."
        )
    row_id = _canonical_id(entry_uuid)
    check_error = None
    if row_id is None:
        check_error = "this turn had no row id to ask about"
    else:
        try:
            if row_id in hook_util.recorded_message_ids(session_id, [row_id]):
                return None
        except Exception as e:
            check_error = hook_util.describe_error(e)
    if check_error is not None:
        return (
            "[HERE I AM] The Here I Am backend failed at the end of this turn "
            f"({err}), and the follow-up check of whether this turn was "
            f"recorded failed too ({check_error}). What you said this turn is "
            "UNCONFIRMED: it may or may not be in your long-term memory. "
            f"{PRESERVE} the backend is failing."
        )
    if hook_util.request_may_still_be_running(error):
        return (
            "[HERE I AM] The Here I Am backend did not finish answering at the "
            f"end of this turn ({err}). What you said this turn was not in your "
            "long-term memory as of a check just after, and the request may "
            "still land, so it is UNCONFIRMED: it may or may not be in your "
            f"long-term memory. {PRESERVE} the backend is slow or failing."
        )
    return (
        "[HERE I AM] The Here I Am backend failed at the end of this turn "
        f"({err}). What you said this turn was NOT recorded to your long-term "
        f"memory. {PRESERVE} the backend is failing."
    )


if __name__ == "__main__":
    main()
    sys.exit(0)

#!/usr/bin/env python3
"""
Here I Am — UserPromptSubmit hook.

Posts the user's prompt to the local Here I Am backend, which records it to
the entity's memory and runs automatic semantic retrieval against it. The
returned memory block is printed to stdout, which Claude Code injects into
context alongside the prompt.

Not everything on the prompt channel is the human: harness plumbing
(system reminders, task notifications) is stripped and dropped, while
inter-session messages from sibling Claude Code sessions are extracted and
sent separately (peer_messages), so the backend can record them under the
entity's own name with the sending session marked instead of archiving
them as the human's words (issue #312). Self-scheduled wakeup prompts —
marked by the entity with the [WAKEUP] sentinel, since the harness marks
them with nothing (issue #318) — are dropped from recording too, though
the backend is still pinged so notes sync and the mailbox flag survive a
wakeup-driven loop session. Every recorded prompt's output ends with a
one-line reminder of the sentinel convention, so it is in view on any
turn where the entity might schedule a prompt to itself.

When the memory block would blow the inline hook-output budget (Claude Code
silently truncates oversized hook output), it is written to a file and the
backend's compact per-memory summary is printed with a pointer instead —
the entity still sees inline what surfaced and where the verbatim text
went. See hook_util.py.

An empty retrieval is never silent (issue #326): from inside a session,
"retrieval ran and nothing matched" and "no retrieval ran" feel identical,
and a self that drafts from an impression because nothing surfaced needs
to know whether anything was asked. So whenever no memory block is
printed, one line says why — matched: 0 (with the count of matches
suppressed as already in context), no retrieval ran (wakeup tick, harness
plumbing, nothing to query), memory unconfigured, or retrieval failed —
each with distinct text, and the last two distinct from the
backend-unreachable notice below. The backend reports which
(retrieval_status); the hook only adds what it alone knows (a wakeup
sentinel it dropped, a prompt that was pure plumbing and never sent).

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
    # Harness blocks (system reminders, task notifications) are not the
    # human speaking — stripped so they are neither archived under the
    # human's name nor used as a retrieval query. Inter-session messages
    # from sibling sessions aren't the human either, but they are the
    # entity: extracted and sent alongside the prompt for recording with
    # honest provenance. A prompt that was pure harness plumbing leaves
    # nothing to send.
    prompt, peer_messages = hook_util.split_prompt_for_recording(
        data.get("prompt") or ""
    )
    # A self-scheduled wakeup prompt (the [WAKEUP] sentinel convention,
    # issue #318) is the entity's own timer firing, not anyone speaking:
    # dropped from recording and retrieval entirely. The backend is still
    # pinged so the notes sync and the sibling-reflections mailbox keep
    # running through a long wakeup-driven loop session.
    wakeup = hook_util.is_wakeup_prompt(prompt)
    if wakeup:
        prompt = ""
    if not session_id:
        return
    if not prompt and not peer_messages and not wakeup:
        # Pure harness plumbing: nothing to record, and the backend is not
        # called — which is exactly the silence that must stamp itself
        print(plumbing_only_stamp())
        return

    payload = {
        "session_id": session_id,
        "prompt": prompt,
        "peer_messages": peer_messages,
        "entity": os.environ.get("HIM_ENTITY") or None,
        "cwd": data.get("cwd"),
    }
    try:
        body = hook_util.post_backend("/api/claude-code/retrieve", payload, timeout=30)
    except Exception as e:
        if not prompt and not peer_messages:
            # Wakeup tick: nothing was going to be recorded, so the loss is
            # only the mailbox check and the background notes sync
            hook_util.fail_loud(
                "The Here I Am backend was unreachable for this wakeup tick "
                f"({hook_util.describe_error(e)}). Nothing needed recording, "
                "but no notes sync ran and new sibling reflections were not "
                "checked."
            )
            return
        hook_util.fail_loud(
            "The Here I Am backend was unreachable for this prompt "
            f"({hook_util.describe_error(e)}). This turn's input (the prompt "
            "and any inter-session message it carried) was NOT recorded to "
            "your long-term memory and no memory retrieval ran."
        )
        return

    # Mailbox flag: reflections saved by other sessions since this
    # conversation began. Content is deliberately not injected — the entity
    # decides whether to pull it (memory_query mode "recent") — but the
    # *fact* must be, because unretrieved history and genuine novelty feel
    # identical from inside.
    mailbox = sibling_reflections_notice(body)
    # Standing reminder of the sentinel convention, with every recorded
    # message: the entity can schedule a prompt to itself on any turn, and
    # a tick scheduled without the sentinel pollutes the archive once per
    # firing. Skipped on wakeup ticks themselves — a sentinel that just
    # worked needs no advertisement.
    reminder = "" if wakeup else wakeup_sentinel_reminder()
    tail = [part for part in (mailbox, reminder) if part]

    context = (body.get("context") or "").strip()
    if not context:
        # No memory block to print: say why, first (issue #326)
        print("\n\n".join([empty_retrieval_stamp(body, wakeup), *tail]))
        return
    if hook_util.output_bytes(context) <= hook_util.inline_budget():
        print("\n\n".join([context, *tail]))
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
    parts = [part for part in (summary, pointer, *tail) if part]
    print("\n\n".join(parts))


MEMORY_QUERY_HINT = "use memory_query if you need recall."


def plumbing_only_stamp() -> str:
    """The line for a prompt that was harness plumbing only (never sent)."""
    return (
        "[HERE I AM] No automatic retrieval ran for this prompt (harness "
        f"plumbing only, nothing to record); {MEMORY_QUERY_HINT}"
    )


def empty_retrieval_stamp(body: dict, wakeup: bool) -> str:
    """
    One line explaining why no memory block was printed (issue #326).

    Keyed on the backend's retrieval_status, so the hook never guesses
    whether a search happened; the hook adds only what it alone knows —
    that the prompt it sent empty was a wakeup tick it dropped.
    """
    status = body.get("retrieval_status")
    if status is None:
        # An older backend (not yet restarted after a pull) reports nothing
        return (
            "[HERE I AM] Retrieval outcome not reported for this prompt (the "
            f"backend predates this hook); {MEMORY_QUERY_HINT}"
        )
    if status == "ran":
        try:
            already = int(body.get("already_in_context") or 0)
        except (TypeError, ValueError):
            already = 0
        if already > 0:
            plural = "match" if already == 1 else "matches"
            return (
                "[HERE I AM MEMORY RETRIEVAL] matched: 0 new (retrieval ran; "
                f"{already} {plural} already in context)."
            )
        return (
            "[HERE I AM MEMORY RETRIEVAL] matched: 0 (retrieval ran; nothing "
            "surfaced above threshold)."
        )
    if status == "skipped":
        reason = "wakeup tick" if wakeup else "nothing to query, e.g. a bare slash command"
        return (
            "[HERE I AM] No automatic retrieval ran for this prompt "
            f"({reason}); {MEMORY_QUERY_HINT}"
        )
    if status == "unconfigured":
        return (
            "[HERE I AM] No automatic retrieval ran: memory is not configured "
            "for this entity."
        )
    if status == "failed":
        error = (body.get("retrieval_error") or "").strip()
        detail = f" ({error})" if error else ""
        return (
            f"[HERE I AM] Memory retrieval FAILED for this prompt{detail}. "
            "This turn's input was recorded, but no memories were searched; "
            f"{MEMORY_QUERY_HINT}"
        )
    return (
        "[HERE I AM] No memories surfaced for this prompt (retrieval status: "
        f"{status}); {MEMORY_QUERY_HINT}"
    )


def wakeup_sentinel_reminder() -> str:
    """One-line standing reminder of the [WAKEUP] convention (issue #318)."""
    return (
        "[HERE I AM] Scheduling a prompt to your own session (a wakeup, "
        f"loop tick, or reminder)? Start it with {hook_util.WAKEUP_SENTINEL} "
        "so the fired prompt is not archived as the human's words."
    )


def sibling_reflections_notice(body: dict) -> str:
    """One-line mailbox flag, empty when there is no new mail."""
    try:
        count = int(body.get("new_sibling_reflections") or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return ""
    plural = "reflection" if count == 1 else "reflections"
    return (
        f"[HERE I AM] {count} {plural} saved in other sessions since this "
        "conversation began, not shown here. Use memory_query with "
        'mode="recent" to read them if you want them.'
    )


if __name__ == "__main__":
    main()
    sys.exit(0)

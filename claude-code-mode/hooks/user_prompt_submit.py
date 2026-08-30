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
wakeup-driven loop session.

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
    if not session_id or (not prompt and not peer_messages and not wakeup):
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

    context = (body.get("context") or "").strip()
    if not context:
        if mailbox:
            print(mailbox)
        return
    if hook_util.output_bytes(context) <= hook_util.inline_budget():
        print(f"{context}\n\n{mailbox}" if mailbox else context)
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
    parts = [part for part in (summary, pointer, mailbox) if part]
    print("\n\n".join(parts))


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

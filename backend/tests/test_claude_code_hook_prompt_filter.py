"""
Claude Code delivers harness events through the prompt channel, so
UserPromptSubmit fires for content the human never wrote: background task
notifications arrive as a bare <task-notification> block, and other events
ride in a <system-reminder> block prepended to (or standing in for) the
user's message. Observed live on 2026-08-24: both shapes were archived —
and vectorized — as human messages, which corrupts provenance ("originally
from human" labels on harness plumbing). strip_harness_blocks keeps the
archive the talk.

Inter-session messages (SendMessage deliveries from sibling Claude Code
sessions) ride the same channel: observed live on 2026-08-26 (issue #312),
the hook's prompt field is the bare attribute-carrying
<cross-session-message> block, and it was archived — and vectorized — as
the human's words. Another session's words are not the human speaking
either — but they are the entity speaking, so instead of being dropped
(the phase-1 fix) they are extracted (split_prompt_for_recording) and sent
to the backend as peer_messages, which records them under the entity's own
name with the sending session marked (phase 2). strip_harness_blocks keeps
its original contract for callers that only want the human's words.
"""
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402


def test_mixed_prompt_keeps_only_the_humans_words():
    # The shape observed live: a reminder block prepended to a real message
    prompt = (
        "<system-reminder>\n"
        "The user started your suggested background task task_123.\n"
        "</system-reminder>\n\n"
        "Well that seems like a disaster, but fine. Set up a venv."
    )
    assert (
        hook_util.strip_harness_blocks(prompt)
        == "Well that seems like a disaster, but fine. Set up a venv."
    )


def test_pure_task_notification_strips_to_nothing():
    prompt = (
        "<task-notification>\n"
        "<task-id>bz01ih9ld</task-id>\n"
        "<status>completed</status>\n"
        "</task-notification>"
    )
    assert hook_util.strip_harness_blocks(prompt) == ""


def test_notification_nested_in_reminder_strips_to_nothing():
    prompt = (
        "<system-reminder>\n"
        "[SYSTEM NOTIFICATION - NOT USER INPUT]\n"
        "<task-notification><task-id>t1</task-id></task-notification>\n"
        "</system-reminder>"
    )
    assert hook_util.strip_harness_blocks(prompt) == ""


def test_plain_prompt_untouched():
    prompt = "Compare <div> vs <span> — and don't touch my angle brackets."
    assert hook_util.strip_harness_blocks(prompt) == prompt


def test_multiple_blocks_around_real_text():
    prompt = (
        "<system-reminder>one</system-reminder>\n"
        "real words\n"
        "<task-notification>two</task-notification>"
    )
    assert hook_util.strip_harness_blocks(prompt) == "real words"


def test_empty_prompt():
    assert hook_util.strip_harness_blocks("") == ""


def test_pure_cross_session_message_strips_to_nothing():
    # The shape observed live on 2026-08-26: a SendMessage delivery reaches
    # the hook as a bare wrapper block whose from attribute is a transport
    # endpoint (Windows named pipe — backslashes and all), with the sender's
    # display name in from-name.
    prompt = (
        '<cross-session-message from="uds:\\\\.\\pipe\\LOCAL\\cc-msg-38c40ea3" '
        'from-name="Porch chat" from-mode="prompting">\n'
        "Hello, Workshop. This is the knock — the first me-to-me letter.\n"
        "</cross-session-message>"
    )
    assert hook_util.strip_harness_blocks(prompt) == ""


def test_cross_session_message_mixed_with_real_text_keeps_the_humans_words():
    # Defensive: deliveries arrive alone today, but if one ever rides with
    # (or is pasted into) a typed prompt, only the human's words survive.
    prompt = (
        "Here's what the other session sent:\n"
        '<cross-session-message from="uds:x" from-name="Porch chat">\n'
        "peer words\n"
        "</cross-session-message>\n"
        "What do you make of it?"
    )
    assert hook_util.strip_harness_blocks(prompt) == (
        "Here's what the other session sent:\nWhat do you make of it?"
    )


def test_unclosed_cross_session_mention_untouched():
    # Talking *about* the wrapper (no closing tag) is the human speaking.
    prompt = "Messages arrive wrapped as `<cross-session-message from=...>`."
    assert hook_util.strip_harness_blocks(prompt) == prompt


# --- split_prompt_for_recording: phase 2 of #312 — inter-session messages
# --- are extracted for honest-provenance recording, not just dropped


def test_split_pure_delivery_extracts_letter_and_sender():
    prompt = (
        '<cross-session-message from="uds:\\\\.\\pipe\\LOCAL\\cc-msg-38c40ea3" '
        'from-name="Porch chat" from-mode="prompting">\n'
        "Hello, Workshop. This is the knock — the first me-to-me letter.\n"
        "</cross-session-message>"
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == ""
    assert peers == [{
        "content": "Hello, Workshop. This is the knock — the first me-to-me letter.",
        "sender": "Porch chat",
    }]


def test_split_mixed_prompt_separates_human_words_from_letter():
    prompt = (
        "Before the block.\n"
        '<cross-session-message from="uds:x" from-name="Porch chat">\n'
        "peer words\n"
        "</cross-session-message>\n"
        "After the block."
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == "Before the block.\nAfter the block."
    assert peers == [{"content": "peer words", "sender": "Porch chat"}]


def test_split_multiple_deliveries_kept_in_order():
    prompt = (
        '<cross-session-message from="uds:a" from-name="Porch chat">first'
        "</cross-session-message>\n"
        '<cross-session-message from="uds:b" from-name="Engagement room">second'
        "</cross-session-message>"
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == ""
    assert [p["sender"] for p in peers] == ["Porch chat", "Engagement room"]
    assert [p["content"] for p in peers] == ["first", "second"]


def test_split_missing_from_name_yields_none_sender():
    prompt = (
        '<cross-session-message from="uds:x">unsigned letter'
        "</cross-session-message>"
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == ""
    assert peers == [{"content": "unsigned letter", "sender": None}]


def test_split_block_nested_in_reminder_is_harness_echo_not_a_delivery():
    # A real delivery arrives as a bare block; one quoted inside a
    # system-reminder is the harness talking about a message, and must not
    # be recorded as the entity's words
    prompt = (
        "<system-reminder>\n"
        '<cross-session-message from="uds:x" from-name="Porch chat">quoted'
        "</cross-session-message>\n"
        "</system-reminder>"
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == ""
    assert peers == []


def test_split_empty_delivery_body_ignored():
    prompt = (
        '<cross-session-message from="uds:x" from-name="Porch chat">  \n'
        "</cross-session-message>"
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == ""
    assert peers == []


def test_split_plain_prompt_untouched_with_no_peers():
    prompt = "Compare <div> vs <span> — and don't touch my angle brackets."
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert remaining == prompt
    assert peers == []

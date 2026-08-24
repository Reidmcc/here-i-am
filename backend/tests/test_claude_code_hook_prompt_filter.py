"""
Claude Code delivers harness events through the prompt channel, so
UserPromptSubmit fires for content the human never wrote: background task
notifications arrive as a bare <task-notification> block, and other events
ride in a <system-reminder> block prepended to (or standing in for) the
user's message. Observed live on 2026-08-24: both shapes were archived —
and vectorized — as human messages, which corrupts provenance ("originally
from human" labels on harness plumbing). strip_harness_blocks keeps the
archive the talk.
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

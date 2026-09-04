"""
Self-scheduled wakeup prompts (issue #318): ScheduleWakeup dynamic-loop
ticks and send_later reminders fire back through the prompt channel with no
harness marker separating a timer-fired prompt from a typed one, and were
archived — and vectorized — as the human's words, once per tick. The fix is
a convention: the entity writes the [WAKEUP] sentinel at the start of its
own scheduled prompts, and the UserPromptSubmit hook drops a
sentinel-carrying prompt from recording and retrieval entirely (a repeated
self-addressed timer is closer to a tool action than to talk). The backend
is still pinged on a wakeup tick, so the incremental notes sync and the
sibling-reflections mailbox keep running through a loop session that may
see no typed prompt for hours.

Hook-level tests run the hook as a subprocess with hook_util.post_backend
stubbed (the stub records the payload it was handed), mirroring
test_claude_code_hook_mailbox.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

# --- is_wakeup_prompt: the sentinel convention itself


def test_sentinel_at_start_is_wakeup():
    assert hook_util.is_wakeup_prompt("[WAKEUP] Continue the standing loop.")


def test_bare_sentinel_is_wakeup():
    assert hook_util.is_wakeup_prompt("[WAKEUP]")


def test_leading_whitespace_tolerated():
    assert hook_util.is_wakeup_prompt("  \n[WAKEUP] tick")


def test_sentinel_after_slash_command_is_wakeup():
    # A dynamic /loop re-fires its whole input, slash command included
    assert hook_util.is_wakeup_prompt("/loop [WAKEUP] check the deploy")


def test_sentinel_later_in_text_is_not_wakeup():
    # Talking about the convention is the human speaking
    assert not hook_util.is_wakeup_prompt("use the [WAKEUP] sentinel")


def test_sentinel_not_directly_after_slash_command_is_not_wakeup():
    assert not hook_util.is_wakeup_prompt("/loop check [WAKEUP] later")


def test_empty_prompt_is_not_wakeup():
    assert not hook_util.is_wakeup_prompt("")


def test_sentinel_behind_harness_plumbing_recognized_after_split():
    # A tick can arrive with a reminder block prepended; the hook checks
    # the user-authored remainder, not the raw prompt
    prompt = (
        "<system-reminder>notifications pending</system-reminder>\n"
        "[WAKEUP] Continue the loop."
    )
    remaining, peers = hook_util.split_prompt_for_recording(prompt)
    assert peers == []
    assert hook_util.is_wakeup_prompt(remaining)


# --- Hook behavior: what a wakeup tick sends (and doesn't send)


def run_hook(
    prompt: str,
    tmp_path,
    body: dict = None,
    unreachable: bool = False,
    extra_env: dict = None,
):
    """Run user_prompt_submit.main() with post_backend stubbed.

    Returns (stdout, payload) — payload is what the hook POSTed, or None
    when it never called the backend."""
    payload_file = tmp_path / "payload.json"
    stdin_payload = json.dumps({"session_id": "wakeup-test", "prompt": prompt})
    stub_tail = (
        "    raise OSError('backend down')\n"
        if unreachable
        else "    return body\n"
    )
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = {(body if body is not None else {'context': ''})!r}\n"
        "def stub(path, payload, timeout=30):\n"
        f"    with open({str(payload_file)!r}, 'w', encoding='utf-8') as f:\n"
        "        json.dump(payload, f)\n"
        + stub_tail
        + "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(stdin_payload)})\n"
        "import user_prompt_submit\n"
        "user_prompt_submit.main()\n"
    )
    env = {**os.environ, **(extra_env or {})}
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = (
        json.loads(payload_file.read_text(encoding="utf-8"))
        if payload_file.exists()
        else None
    )
    return result.stdout.decode("utf-8"), payload


def test_wakeup_tick_records_nothing_but_pings_backend(tmp_path):
    out, payload = run_hook(
        "[WAKEUP] Continue the standing Substack engagement loop.",
        tmp_path,
        body={"context": "", "retrieval_status": "skipped"},
    )
    assert payload is not None, "backend must still be pinged on a tick"
    assert payload["prompt"] == ""
    assert payload["peer_messages"] == []
    # A tick's only output is the no-retrieval stamp (issue #326): the
    # silence that caused the 08-29 near-miss now says what it is
    assert out.strip() == (
        "[HERE I AM] No automatic retrieval ran for this prompt (wakeup "
        "tick); use memory_query if you need recall."
    )


def test_wakeup_tick_still_prints_mailbox_flag(tmp_path):
    out, _ = run_hook(
        "[WAKEUP] tick",
        tmp_path,
        body={"context": "", "new_sibling_reflections": 2},
    )
    assert "2 reflections saved in other sessions" in out


def test_wakeup_with_peer_message_still_records_the_letter(tmp_path):
    prompt = (
        '<cross-session-message from="uds:x" from-name="Porch chat">\n'
        "letter words\n"
        "</cross-session-message>\n"
        "[WAKEUP] tick"
    )
    _, payload = run_hook(prompt, tmp_path)
    assert payload["prompt"] == ""
    assert len(payload["peer_messages"]) == 1
    peer = payload["peer_messages"][0]
    assert (peer["content"], peer["sender"]) == ("letter words", "Porch chat")
    # The hook names the row it is asking for (a UUID), so it can verify
    # the recording after a failed call
    import uuid

    assert uuid.UUID(peer["message_id"])


def test_typed_prompt_still_sent_verbatim(tmp_path):
    _, payload = run_hook("What did we discuss about gardens?", tmp_path)
    assert payload["prompt"] == "What did we discuss about gardens?"


def test_pure_plumbing_still_skips_backend_entirely(tmp_path):
    # No sentinel, nothing user-authored: unchanged pre-#318 behavior
    _, payload = run_hook(
        "<system-reminder>notifications pending</system-reminder>", tmp_path
    )
    assert payload is None


def test_unreachable_backend_notice_is_accurate_for_a_tick(tmp_path):
    # The regular failure notice says the turn's input was NOT recorded —
    # wrong for a tick, where nothing was going to be recorded anyway
    out, _ = run_hook("[WAKEUP] tick", tmp_path, unreachable=True)
    assert "[HERE I AM]" in out
    assert "wakeup tick" in out
    assert "NOT recorded" not in out


# --- The standing sentinel reminder: the convention only works if the
# --- entity remembers it on the turn where it schedules a prompt


def test_reminder_printed_with_every_recorded_prompt(tmp_path):
    out, _ = run_hook("hello", tmp_path)
    assert "Start it with [WAKEUP]" in out


def test_reminder_rides_alongside_context_and_mailbox(tmp_path):
    out, _ = run_hook(
        "hello",
        tmp_path,
        body={"context": "[MEMORY] something", "new_sibling_reflections": 2},
    )
    assert "[MEMORY] something" in out
    assert "2 reflections saved in other sessions" in out
    assert "Start it with [WAKEUP]" in out


def test_reminder_survives_spill_branch(tmp_path):
    out, _ = run_hook(
        "hello",
        tmp_path,
        body={
            "context": "[MEMORY] " + ("x" * 30000),
            "context_summary": "- abc12345: snippet",
        },
        extra_env={
            "HIM_INLINE_BUDGET": "1000",
            "TMPDIR": str(tmp_path),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
        },
    )
    assert "too large to inject inline" in out
    assert "Start it with [WAKEUP]" in out


def test_reminder_not_printed_on_wakeup_ticks(tmp_path):
    # A sentinel that just worked needs no advertisement — ticks stay
    # output-silent (test_wakeup_tick_records_nothing_but_pings_backend
    # asserts the fully-empty case; this one adds a mailbox flag)
    out, _ = run_hook(
        "[WAKEUP] tick",
        tmp_path,
        body={"context": "", "new_sibling_reflections": 1},
    )
    assert "1 reflection saved in other sessions" in out
    assert "Start it with [WAKEUP]" not in out

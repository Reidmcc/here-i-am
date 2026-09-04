"""
The UserPromptSubmit hook stamps an empty retrieval (issue #326).

From inside a session, "retrieval ran and nothing matched" and "no
retrieval ran at all" are indistinguishable when both are silent — the
engagement room's wakeup-driven loop produced a near-miss on 2026-08-29
where a self drafted from an impression because nothing surfaced, without
realizing nothing had been asked. So whenever the hook prints no memory
block it prints one line saying why, keyed on the backend's
retrieval_status, with distinct text for each state:

- retrieval ran, nothing matched            -> "matched: 0"
- retrieval ran, matches already in context -> "matched: 0 new (...)"
- no retrieval ran (wakeup tick / bare slash command / pure plumbing)
- memory unconfigured for the entity
- retrieval failed after the prompt was recorded
- backend too old to report (absent field)

and none of them shares text with the backend-unreachable notice.

The hook runs as a subprocess with hook_util.post_backend stubbed,
mirroring test_claude_code_hook_wakeup.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"

MATCHED_ZERO = (
    "[HERE I AM MEMORY RETRIEVAL] matched: 0 (retrieval ran; nothing "
    "surfaced above threshold)."
)
NO_RETRIEVAL = "[HERE I AM] No automatic retrieval ran for this prompt"
HINT = "use memory_query if you need recall."


def run_hook(prompt: str, body=None, unreachable: bool = False, extra_env=None):
    """Run user_prompt_submit.main() with post_backend stubbed.

    Returns (stdout, called) — called is whether the backend was posted to.
    """
    stdin_payload = json.dumps({"session_id": "stamp-test", "prompt": prompt})
    marker = "BACKEND-CALLED"
    # "unreachable" means the connection was refused — the one failure the
    # hook may report as unrecorded without checking /recorded
    stub_tail = (
        "    import urllib.error\n"
        "    raise urllib.error.URLError(ConnectionRefusedError('refused'))\n"
        if unreachable
        else "    return body\n"
    )
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = json.loads({json.dumps(json.dumps(body if body is not None else {'context': ''}))})\n"
        "def stub(path, payload, timeout=30):\n"
        f"    sys.stderr.write({marker!r})\n"
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
    stderr = result.stderr.decode("utf-8", "replace")
    assert result.returncode == 0, stderr
    return result.stdout.decode("utf-8"), marker in stderr


def lines(out: str):
    return [ln for ln in out.splitlines() if ln.strip()]


# --- retrieval ran


def test_ran_with_nothing_found_prints_matched_zero():
    out, called = run_hook("hello", body={"context": "", "retrieval_status": "ran"})
    assert called
    assert lines(out)[0] == MATCHED_ZERO


def test_ran_with_matches_already_in_context_says_zero_new():
    out, _ = run_hook(
        "hello",
        body={"context": "", "retrieval_status": "ran", "already_in_context": 3},
    )
    assert lines(out)[0] == (
        "[HERE I AM MEMORY RETRIEVAL] matched: 0 new (retrieval ran; "
        "3 matches already in context)."
    )
    assert MATCHED_ZERO not in out


def test_single_already_in_context_match_is_singular():
    out, _ = run_hook(
        "hello",
        body={"context": "", "retrieval_status": "ran", "already_in_context": 1},
    )
    assert "1 match already in context" in out
    assert "1 matches" not in out


def test_no_stamp_when_memories_surfaced():
    out, _ = run_hook(
        "hello",
        body={"context": "[MEMORY] something", "retrieval_status": "ran"},
    )
    assert "[MEMORY] something" in out
    assert "matched: 0" not in out
    assert NO_RETRIEVAL not in out


def test_no_stamp_on_spill_branch(tmp_path):
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] " + ("x" * 30000),
            "context_summary": "[HERE I AM MEMORY RETRIEVAL] 1 memory ...\n- abc12345: snippet",
            "retrieval_status": "ran",
        },
        extra_env={
            "HIM_INLINE_BUDGET": "1000",
            "TMPDIR": str(tmp_path),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
        },
    )
    assert "too large to inject inline" in out
    assert "matched: 0" not in out


# --- no retrieval ran


def test_wakeup_tick_prints_skipped_line_not_matched_zero():
    out, called = run_hook(
        "[WAKEUP] tick", body={"context": "", "retrieval_status": "skipped"}
    )
    assert called
    assert lines(out) == [
        f"{NO_RETRIEVAL} (wakeup tick); {HINT}",
    ]
    assert "matched: 0" not in out


def test_bare_slash_command_prints_skipped_line_with_its_own_reason():
    # The hook sends "/compact" as a prompt; the backend's record-nothing
    # path reports skipped — the hook must not call it a wakeup tick
    out, _ = run_hook("/compact", body={"context": "", "retrieval_status": "skipped"})
    first = lines(out)[0]
    assert first.startswith(NO_RETRIEVAL)
    assert "bare slash command" in first
    assert "wakeup" not in first
    assert first.endswith(HINT)


def test_pure_plumbing_prints_skipped_line_without_calling_backend():
    out, called = run_hook(
        "<system-reminder>notifications pending</system-reminder>"
    )
    assert not called
    assert lines(out) == [
        f"{NO_RETRIEVAL} (harness plumbing only, nothing to record); {HINT}",
    ]


def test_missing_session_id_stays_silent():
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        "hook_util.post_backend = lambda *a, **k: {'context': ''}\n"
        "sys.stdin = io.StringIO(json.dumps({'prompt': 'hello'}))\n"
        "import user_prompt_submit\n"
        "user_prompt_submit.main()\n"
    )
    env = {**os.environ}
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env
    )
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").strip() == ""


# --- other empty outcomes, each with its own text


def test_unconfigured_memory_line():
    out, _ = run_hook("hello", body={"context": "", "retrieval_status": "unconfigured"})
    assert lines(out)[0] == (
        "[HERE I AM] No automatic retrieval ran: memory is not configured "
        "for this entity."
    )


def test_failed_retrieval_line_says_prompt_was_recorded():
    out, _ = run_hook(
        "hello",
        body={
            "context": "",
            "retrieval_status": "failed",
            "retrieval_error": "RuntimeError: pinecone down",
        },
    )
    first = lines(out)[0]
    assert first.startswith("[HERE I AM] Memory retrieval FAILED for this prompt")
    assert "(RuntimeError: pinecone down)" in first
    assert "was recorded" in first
    assert first.endswith(HINT)


def test_failed_line_is_distinct_from_unreachable_notice():
    failed, _ = run_hook(
        "hello", body={"context": "", "retrieval_status": "failed"}
    )
    unreachable, _ = run_hook("hello", unreachable=True)
    assert "NOT recorded" in unreachable
    assert "NOT recorded" not in failed
    assert "FAILED" not in unreachable
    assert lines(failed)[0] != lines(unreachable)[0]


def test_absent_status_from_older_backend_is_still_not_silent():
    out, _ = run_hook("hello", body={"context": ""})
    first = lines(out)[0]
    assert "Retrieval outcome not reported" in first
    assert "matched: 0" not in out
    assert first.endswith(HINT)


def test_unknown_status_falls_through_to_a_generic_line():
    out, _ = run_hook("hello", body={"context": "", "retrieval_status": "novel"})
    assert "retrieval status: novel" in lines(out)[0]


# --- placement: the stamp comes first, then the mailbox flag and reminder


def test_stamp_precedes_mailbox_and_reminder():
    out, _ = run_hook(
        "hello",
        body={"context": "", "retrieval_status": "ran", "new_sibling_reflections": 2},
    )
    got = lines(out)
    assert got[0] == MATCHED_ZERO
    assert "2 reflections saved in other sessions" in got[1]
    assert "Start it with [WAKEUP]" in got[2]


def test_wakeup_stamp_precedes_mailbox_and_skips_reminder():
    out, _ = run_hook(
        "[WAKEUP] tick",
        body={"context": "", "retrieval_status": "skipped", "new_sibling_reflections": 1},
    )
    got = lines(out)
    assert got[0].startswith(NO_RETRIEVAL)
    assert "1 reflection saved in other sessions" in got[1]
    assert len(got) == 2


# --- dedup by kind (issue #328): reflections skip free, verbatim hold slots


def test_zero_new_with_reflections_skipped_reports_both_counts():
    out, _ = run_hook(
        "hello",
        body={
            "context": "",
            "retrieval_status": "ran",
            "already_in_context": 3,
            "in_context_reflections_skipped": 2,
        },
    )
    assert lines(out)[0] == (
        "[HERE I AM MEMORY RETRIEVAL] matched: 0 new (retrieval ran; "
        "2 in-context reflections skipped; in-context verbatim held 3 slots)."
    )


def test_zero_new_with_only_reflections_skipped():
    out, _ = run_hook(
        "hello",
        body={"context": "", "retrieval_status": "ran", "in_context_reflections_skipped": 1},
    )
    assert lines(out)[0] == (
        "[HERE I AM MEMORY RETRIEVAL] matched: 0 new (retrieval ran; "
        "1 in-context reflection skipped; in-context verbatim held 0 slots)."
    )
    assert MATCHED_ZERO not in out


def test_surfaced_block_is_followed_by_dedup_line_when_reflections_skipped():
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] something",
            "memories_retrieved": 3,
            "retrieval_status": "ran",
            "already_in_context": 0,
            "in_context_reflections_skipped": 2,
        },
    )
    got = lines(out)
    assert got[0] == "[MEMORY] something"
    assert got[1] == (
        "[HERE I AM MEMORY RETRIEVAL] matched: 3 new (2 in-context reflections "
        "skipped; in-context verbatim held 0 slots)."
    )
    assert "Start it with [WAKEUP]" in got[2]


def test_surfaced_block_is_followed_by_dedup_line_when_verbatim_held_a_slot():
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] something",
            "memories_retrieved": 4,
            "retrieval_status": "ran",
            "already_in_context": 1,
        },
    )
    got = lines(out)
    assert got[1] == (
        "[HERE I AM MEMORY RETRIEVAL] matched: 4 new (0 in-context reflections "
        "skipped; in-context verbatim held 1 slot)."
    )


def test_surfaced_block_without_dedup_gets_no_extra_line():
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] something",
            "memories_retrieved": 1,
            "retrieval_status": "ran",
            "already_in_context": 0,
            "in_context_reflections_skipped": 0,
        },
    )
    got = lines(out)
    assert got[0] == "[MEMORY] something"
    assert "matched:" not in out
    assert "Start it with [WAKEUP]" in got[1]


def test_dedup_line_precedes_mailbox_flag():
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] something",
            "memories_retrieved": 2,
            "retrieval_status": "ran",
            "in_context_reflections_skipped": 1,
            "new_sibling_reflections": 2,
        },
    )
    got = lines(out)
    assert got[1].startswith("[HERE I AM MEMORY RETRIEVAL] matched: 2 new")
    assert "2 reflections saved in other sessions" in got[2]


def test_dedup_line_survives_the_spill_branch(tmp_path):
    out, _ = run_hook(
        "hello",
        body={
            "context": "[MEMORY] " + ("x" * 30000),
            "context_summary": "[HERE I AM MEMORY RETRIEVAL] 1 memory ...\n- abc12345: snippet",
            "memories_retrieved": 1,
            "retrieval_status": "ran",
            "in_context_reflections_skipped": 3,
        },
        extra_env={
            "HIM_INLINE_BUDGET": "1000",
            "TMPDIR": str(tmp_path),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
        },
    )
    assert "too large to inject inline" in out
    assert (
        "matched: 1 new (3 in-context reflections skipped; in-context verbatim "
        "held 0 slots)."
    ) in out

"""
What the Stop hook says, and when.

A Stop hook's stdout never reaches context, so its one way to speak is
exit 2, which continues the turn with stderr shown. That belongs to the
recording failure alone: the turn's text didn't reach the archive, once
per turn, never a loop (stop_hook_active).

There used to be a second voice. The context gauge (issue #365) measured
each turn against the auto-compaction line and held a notice for the next
prompt at 90%. Issue #394 removed it: the compaction mod (issue #383)
gives the entity a turn at the compaction itself and wakes it holding the
talk, so a warning ahead of the boundary only fed the prior that reads
compaction as loss. These tests pin that a turn near the line is
recorded and nothing else.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"


def _transcript(tmp_path, input_tokens):
    entries = [
        {"type": "user", "uuid": "p", "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant",
            "uuid": "a",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "usage": {"input_tokens": input_tokens, "output_tokens": 500},
            },
        },
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def _run(script, stdin_payload, tmp_path, body=None, backend_down=False):
    """Run a hook script as a subprocess with the backend stubbed; returns
    (exit code, stdout, stderr)."""
    body = body if body is not None else {}
    stub_body = "raise OSError('backend down')" if backend_down else f"return FakeResponse({json.dumps(body)!r})"
    code = (
        "import io, json, sys, urllib.request\n"
        "import hook_util\n"
        "class FakeResponse:\n"
        "    def __init__(self, text): self.text = text\n"
        "    def read(self): return self.text.encode('utf-8')\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): return False\n"
        "def urlopen(request, timeout=30):\n"
        f"    {stub_body}\n"
        "urllib.request.urlopen = urlopen\n"
        "def post_backend(path, payload, timeout=30):\n"
        f"    return {body!r}\n"
        "hook_util.post_backend = post_backend\n"
        "hook_util.desktop_sessions_index = lambda *a, **k: {}\n"
        "hook_util.live_sessions_snapshot = lambda *a, **k: []\n"
        "hook_util.desktop_prior_session_ids = lambda *a, **k: []\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        f"import {script}\n"
        f"{script}.main()\n"
    )
    env = {
        **os.environ,
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
    }
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=30
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
    )


def _stop(tmp_path, input_tokens=950_000, stop_hook_active=False, **kwargs):
    payload = {
        "session_id": "stop-session",
        "transcript_path": _transcript(tmp_path, input_tokens),
        "cwd": str(tmp_path),
        "stop_hook_active": stop_hook_active,
    }
    return _run("stop", payload, tmp_path, **kwargs)


def test_a_recording_failure_exits_2_with_the_notice(tmp_path):
    code, out, err = _stop(tmp_path, backend_down=True)
    assert code == 2
    assert out == ""
    assert "was NOT recorded" in err


def test_a_recording_failure_on_the_continuation_is_silent(tmp_path):
    # One loud retry per turn, never a loop
    assert _stop(tmp_path, backend_down=True, stop_hook_active=True) == (0, "", "")


def _files(directory):
    return sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    "prompt",
    ["hello", "<system-reminder>\ntick\n</system-reminder>"],
    ids=["recorded-prompt", "plumbing-only-prompt"],
)
def test_a_turn_near_the_compaction_line_changes_nothing_the_entity_is_told(tmp_path, prompt):
    # The invariant, whatever words a future notice would use: the next
    # prompt's output is byte-equal with and without a Stop at ~95% of the
    # 1M default's line (where the gauge would have spoken) before it
    quiet, near = tmp_path / "quiet", tmp_path / "near"
    quiet.mkdir()
    near.mkdir()
    before = _files(near)
    assert _stop(near, input_tokens=920_000) == (0, "", "")
    # The Stop leaves nothing behind for a later hook to read, under any name
    assert _files(near) == sorted([*before, "transcript.jsonl"])

    payload = {"session_id": "stop-session", "prompt": prompt}
    body = {"context": "", "retrieval_status": "ran"}
    told_quiet = _run("user_prompt_submit", payload, quiet, body=body)
    told_near = _run("user_prompt_submit", payload, near, body=body)
    assert told_quiet[0] == 0
    assert told_quiet[1]  # the hook does say something; the two must match
    assert told_near == told_quiet

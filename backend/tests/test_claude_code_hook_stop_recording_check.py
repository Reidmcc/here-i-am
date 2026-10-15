"""
The Stop hook's failure notice must not be false (issue #400).

The prompt hook has asked /recorded before saying "NOT recorded" since
issue #326; Stop said "NOT recorded" on any exception. Across a network a
read timeout or a dropped connection after the backend committed the row
is ordinary, so Stop now does the same, by the row id it already chooses
(the turn's last text entry's uuid):

- never reached the backend      -> exit 2, "NOT recorded", no check
- failed, the row is there       -> exit 0, silent (nothing was lost)
- failed, the row is missing     -> exit 2, "NOT recorded"
- failed, and the check failed   -> exit 2, "UNCONFIRMED"
- on the continuation (stop_hook_active) -> silent whatever happened

The hook runs as a subprocess with hook_util.post_backend stubbed per path.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"

LOG = "/api/claude-code/log-assistant"
RECORDED = "/api/claude-code/recorded"
ENTRY = str(uuid.uuid4())


def _transcript(tmp_path, entry_uuid=ENTRY):
    entries = [
        {"type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant",
            "uuid": entry_uuid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
        },
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def run_stop(tmp_path, responses, stop_hook_active=False, entry_uuid=ENTRY):
    """responses maps a path to {"raise": kind} (unreachable / timeout /
    http500 / reset) or {"recorded": "all" | "none"}. Returns (exit code,
    stdout, stderr, calls)."""
    calls_file = tmp_path / "calls.json"
    stdin_payload = {
        "session_id": "stop-check",
        "transcript_path": _transcript(tmp_path, entry_uuid),
        "cwd": str(tmp_path),
        "stop_hook_active": stop_hook_active,
    }
    code = (
        "import io, json, sys, urllib.error\n"
        "import hook_util\n"
        f"responses = json.loads({json.dumps(json.dumps(responses))})\n"
        "calls = []\n"
        "def make_error(kind):\n"
        "    if kind == 'unreachable':\n"
        "        return hook_util.BackendUnreachable('no connection to vm:8000 within 4 s')\n"
        "    if kind == 'timeout':\n"
        "        return TimeoutError('the backend did not answer within 30 s')\n"
        "    if kind == 'http500':\n"
        "        return urllib.error.HTTPError('http://x', 500, 'Internal Server Error', {}, None)\n"
        "    return ConnectionResetError('reset')\n"
        "def stub(path, payload, timeout=30):\n"
        "    calls.append([path, payload])\n"
        f"    with open({str(calls_file)!r}, 'w', encoding='utf-8') as f:\n"
        "        json.dump(calls, f)\n"
        "    spec = responses.get(path) or {}\n"
        "    if 'raise' in spec:\n"
        "        raise make_error(spec['raise'])\n"
        "    if 'recorded' in spec:\n"
        "        ids = list(payload.get('message_ids') or [])\n"
        "        got = ids if spec['recorded'] == 'all' else []\n"
        "        return {'recorded': got, 'missing': [i for i in ids if i not in got]}\n"
        "    return {}\n"
        "hook_util.post_backend = stub\n"
        "hook_util.desktop_sessions_index = lambda *a, **k: {}\n"
        "hook_util.live_sessions_snapshot = lambda *a, **k: []\n"
        "hook_util.desktop_prior_session_ids = lambda *a, **k: []\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        "import stop\n"
        "stop.main()\n"
    )
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")}
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=30
    )
    calls = json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
    return (
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
        calls,
    )


def paths(calls):
    return [path for path, _ in calls]


def test_a_backend_never_reached_is_not_recorded_without_a_check(tmp_path):
    code, out, err, calls = run_stop(tmp_path, {LOG: {"raise": "unreachable"}})
    assert code == 2
    assert out == ""
    assert "unreachable" in err
    assert "was NOT recorded" in err
    assert paths(calls) == [LOG]


@pytest.mark.parametrize("kind", ["timeout", "http500", "reset"])
def test_a_failure_after_the_commit_is_silent(tmp_path, kind):
    code, out, err, calls = run_stop(
        tmp_path, {LOG: {"raise": kind}, RECORDED: {"recorded": "all"}}
    )
    assert (code, out, err) == (0, "", "")
    assert paths(calls) == [LOG, RECORDED]
    # Asked about exactly the row id the hook sent
    assert calls[0][1]["message_uuid"] == ENTRY
    assert calls[1][1] == {"session_id": "stop-check", "message_ids": [ENTRY]}


def test_a_failure_with_the_row_missing_is_not_recorded(tmp_path):
    code, out, err, _ = run_stop(
        tmp_path, {LOG: {"raise": "timeout"}, RECORDED: {"recorded": "none"}}
    )
    assert code == 2
    assert "was NOT recorded" in err
    assert "did not answer within 30 s" in err


def test_a_failed_check_is_unconfirmed(tmp_path):
    code, out, err, _ = run_stop(
        tmp_path, {LOG: {"raise": "timeout"}, RECORDED: {"raise": "unreachable"}}
    )
    assert code == 2
    assert "UNCONFIRMED" in err
    assert "NOT recorded" not in err


def test_a_turn_with_no_row_id_is_unconfirmed(tmp_path):
    code, _, err, calls = run_stop(
        tmp_path, {LOG: {"raise": "timeout"}}, entry_uuid="not-a-uuid"
    )
    assert code == 2
    assert "UNCONFIRMED" in err
    assert paths(calls) == [LOG]


def test_the_continuation_is_silent_whatever_happened(tmp_path):
    code, out, err, _ = run_stop(
        tmp_path,
        {LOG: {"raise": "timeout"}, RECORDED: {"recorded": "none"}},
        stop_hook_active=True,
    )
    assert (code, out, err) == (0, "", "")


def test_a_recorded_turn_says_nothing(tmp_path):
    assert run_stop(tmp_path, {})[:3] == (0, "", "")

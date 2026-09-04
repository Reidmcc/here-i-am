"""
The UserPromptSubmit hook's failure notice must not be false (issue #326).

/retrieve commits the turn's rows before it runs retrieval, so a 500 or a
timeout can arrive after the words are already in the archive — and the
old notice ("NOT recorded") was then misinformation of exactly the kind
the hooks exist to prevent. The hook now chooses the row ids itself and,
on any failure that could have landed after a commit, asks /recorded
which of them exist before saying anything:

- recorded              -> "WAS recorded ... no memory retrieval ran"
- not recorded          -> "NOT recorded"
- partly (prompt yes, letter no) -> names both
- check itself failed   -> "UNCONFIRMED"
- connection refused    -> "NOT recorded" without a check (provably never
                           reached the backend)

The hook runs as a subprocess with hook_util.post_backend stubbed per
path; the stub records every payload it was handed.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"

RETRIEVE = "/api/claude-code/retrieve"
RECORDED = "/api/claude-code/recorded"

LETTER = (
    '<cross-session-message from="uds:x" from-name="Porch chat">\n'
    "letter words\n"
    "</cross-session-message>\n"
)


def run_hook(prompt: str, responses: dict, tmp_path):
    """Run user_prompt_submit.main() with post_backend stubbed per path.

    responses maps a path to either a body dict, {"raise": kind} (kind in
    refused / timeout / http500 / oserror), or {"recorded": "all" | "none"
    | "first"} for the /recorded check, which answers about whatever ids
    the hook asked for. Returns (stdout, calls) where calls is the list of
    (path, payload) the hook made, in order.
    """
    calls_file = tmp_path / "calls.json"
    stdin_payload = json.dumps({"session_id": "check-test", "prompt": prompt})
    code = (
        "import io, json, sys, urllib.error\n"
        "import hook_util\n"
        f"responses = json.loads({json.dumps(json.dumps(responses))})\n"
        "calls = []\n"
        "def make_error(kind):\n"
        "    if kind == 'refused':\n"
        "        return urllib.error.URLError(ConnectionRefusedError('refused'))\n"
        "    if kind == 'timeout':\n"
        "        return TimeoutError('timed out')\n"
        "    if kind == 'http500':\n"
        "        return urllib.error.HTTPError('http://x', 500, 'Internal Server Error', {}, None)\n"
        "    return OSError('backend down')\n"
        "def stub(path, payload, timeout=30):\n"
        "    calls.append([path, payload])\n"
        f"    with open({str(calls_file)!r}, 'w', encoding='utf-8') as f:\n"
        "        json.dump(calls, f)\n"
        "    spec = responses.get(path)\n"
        "    if spec is None:\n"
        "        return {'context': ''}\n"
        "    if 'raise' in spec:\n"
        "        raise make_error(spec['raise'])\n"
        "    if 'recorded' in spec:\n"
        "        ids = list(payload.get('message_ids') or [])\n"
        "        mode = spec['recorded']\n"
        "        got = ids if mode == 'all' else ids[:1] if mode == 'first' else []\n"
        "        return {'recorded': got, 'missing': [i for i in ids if i not in got]}\n"
        "    return spec\n"
        "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(stdin_payload)})\n"
        "import user_prompt_submit\n"
        "user_prompt_submit.main()\n"
    )
    env = {**os.environ}
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    calls = (
        json.loads(calls_file.read_text(encoding="utf-8"))
        if calls_file.exists()
        else []
    )
    return result.stdout.decode("utf-8"), calls


def paths(calls):
    return [path for path, _ in calls]


# --- the ids the hook asks the backend to write


def test_payload_names_the_rows_it_asks_for(tmp_path):
    _, calls = run_hook("hello\n" + LETTER, {}, tmp_path)
    payload = calls[0][1]
    assert uuid.UUID(payload["message_id"])
    assert len(payload["peer_messages"]) == 1
    assert uuid.UUID(payload["peer_messages"][0]["message_id"])
    assert payload["message_id"] != payload["peer_messages"][0]["message_id"]


def test_wakeup_tick_names_no_prompt_row(tmp_path):
    _, calls = run_hook("[WAKEUP] tick", {}, tmp_path)
    assert calls[0][1]["message_id"] is None


# --- what the notice says after a failed call


def test_failure_after_commit_reports_the_prompt_as_recorded(tmp_path):
    out, calls = run_hook(
        "hello",
        {RETRIEVE: {"raise": "http500"}, RECORDED: {"recorded": "all"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE, RECORDED]
    assert "WAS recorded" in out
    assert "NOT recorded" not in out
    assert "no memory retrieval ran" in out
    assert "memory_query" in out
    # The check asked about exactly the id the hook sent
    assert calls[1][1]["message_ids"] == [calls[0][1]["message_id"]]
    assert calls[1][1]["session_id"] == "check-test"


def test_failure_before_commit_reports_not_recorded(tmp_path):
    out, calls = run_hook(
        "hello",
        {RETRIEVE: {"raise": "http500"}, RECORDED: {"recorded": "none"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE, RECORDED]
    assert "NOT recorded" in out
    assert "WAS recorded" not in out


def test_timeout_is_checked_not_assumed(tmp_path):
    out, calls = run_hook(
        "hello",
        {RETRIEVE: {"raise": "timeout"}, RECORDED: {"recorded": "all"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE, RECORDED]
    assert "WAS recorded" in out


def test_connection_refused_is_not_recorded_without_a_check(tmp_path):
    out, calls = run_hook(
        "hello",
        {RETRIEVE: {"raise": "refused"}, RECORDED: {"recorded": "all"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE]
    assert "unreachable" in out
    assert "NOT recorded" in out


def test_check_failure_reports_unconfirmed(tmp_path):
    out, calls = run_hook(
        "hello",
        {RETRIEVE: {"raise": "timeout"}, RECORDED: {"raise": "oserror"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE, RECORDED]
    assert "UNCONFIRMED" in out
    assert "NOT recorded" not in out
    assert "WAS recorded" not in out


def test_partial_recording_names_what_landed_and_what_did_not(tmp_path):
    out, calls = run_hook(
        "hello\n" + LETTER,
        {RETRIEVE: {"raise": "http500"}, RECORDED: {"recorded": "first"}},
        tmp_path,
    )
    check_ids = calls[1][1]["message_ids"]
    assert check_ids[0] == calls[0][1]["message_id"]  # prompt id asked first
    assert "Recorded to your long-term memory: the prompt." in out
    assert "NOT recorded: the inter-session message." in out


def test_letter_only_turn_is_described_as_the_letter(tmp_path):
    out, _ = run_hook(
        LETTER,
        {RETRIEVE: {"raise": "http500"}, RECORDED: {"recorded": "all"}},
        tmp_path,
    )
    assert "This turn's input (the inter-session message) WAS recorded" in out


def test_wakeup_tick_failure_keeps_its_own_notice_and_no_check(tmp_path):
    out, calls = run_hook(
        "[WAKEUP] tick",
        {RETRIEVE: {"raise": "http500"}, RECORDED: {"recorded": "all"}},
        tmp_path,
    )
    assert paths(calls) == [RETRIEVE]
    assert "wakeup tick" in out
    assert "NOT recorded" not in out
    assert "WAS recorded" not in out


def test_success_path_makes_no_check_call(tmp_path):
    _, calls = run_hook(
        "hello", {RETRIEVE: {"context": "", "retrieval_status": "ran"}}, tmp_path
    )
    assert paths(calls) == [RETRIEVE]

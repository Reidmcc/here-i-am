"""
Rooms registry, hook side (issue #323): what the SessionStart and
UserPromptSubmit hooks collect about live sessions and what they print
back.

Claude Code keeps a per-process registry at <config dir>/sessions/<pid>.json
(undocumented internal state); the hooks read it best-effort into a
snapshot the backend uses to refresh declared rooms' roster names and
liveness. These tests fabricate that directory under CLAUDE_CONFIG_DIR and
run the hooks as subprocesses with hook_util.post_backend stubbed (the
stub records the payload), mirroring test_claude_code_hook_wakeup.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

# The shape observed live (Claude Code 2.1.258, desktop entrypoint)
LIVE_ENTRY = {
    "pid": 6880,
    "sessionId": "096a7e9f-faf7-4082-ba8c-debb2d818b63",
    "cwd": "E:\\here-i-am-notes",
    "startedAt": 1788395082135,
    "procStart": "134328686808798513",
    "version": "2.1.258",
    "kind": "interactive",
    "entrypoint": "claude-desktop",
    "messagingSocketPath": "\\\\.\\pipe\\LOCAL\\cc-msg-2db48788b5f07d5597aa094da48d4211",
    "name": "Porch chats",
    "nameSource": "user",
    "nameSince": 1788404038531,
    "bridgeSessionId": "session_01Uhn2Qd9K6gGAXDLH3ZGp41",
}


def write_registry(config_dir: Path, *entries: dict) -> None:
    sessions = config_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (sessions / f"{entry.get('pid', 1)}.json").write_text(
            json.dumps(entry), encoding="utf-8"
        )


# --- live_sessions_snapshot


def test_snapshot_maps_observed_fields(tmp_path):
    write_registry(tmp_path, LIVE_ENTRY)
    snapshot = hook_util.live_sessions_snapshot(str(tmp_path))
    assert snapshot == [
        {
            "session_id": "096a7e9f-faf7-4082-ba8c-debb2d818b63",
            "name": "Porch chats",
            "name_source": "user",
            "name_since": "2026-09-03T02:53:58+00:00",
            "messaging_socket": "\\\\.\\pipe\\LOCAL\\cc-msg-2db48788b5f07d5597aa094da48d4211",
            "cwd": "E:\\here-i-am-notes",
            "started_at": "2026-09-03T00:24:42+00:00",
        }
    ]


def test_snapshot_records_missing_fields_as_none(tmp_path):
    write_registry(tmp_path, {"pid": 7, "sessionId": "bare-session"})
    [entry] = hook_util.live_sessions_snapshot(str(tmp_path))
    assert entry["session_id"] == "bare-session"
    assert entry["name"] is None
    assert entry["name_source"] is None
    assert entry["name_since"] is None
    assert entry["messaging_socket"] is None
    assert entry["started_at"] is None


def test_snapshot_skips_unparsable_and_idless_files(tmp_path):
    write_registry(tmp_path, LIVE_ENTRY, {"pid": 8, "name": "no id here"})
    (tmp_path / "sessions" / "9.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "sessions" / "10.json").write_text("[]", encoding="utf-8")
    snapshot = hook_util.live_sessions_snapshot(str(tmp_path))
    assert [s["session_id"] for s in snapshot] == [LIVE_ENTRY["sessionId"]]


def test_snapshot_empty_when_registry_dir_missing(tmp_path):
    assert hook_util.live_sessions_snapshot(str(tmp_path / "nowhere")) == []


def test_snapshot_ignores_key_files(tmp_path):
    # The registry keeps a <pid>.<hash>.key beside each <pid>.json
    write_registry(tmp_path, LIVE_ENTRY)
    (tmp_path / "sessions" / "6880.abc.key").write_text(
        '{"peerToken": "x"}', encoding="utf-8"
    )
    assert len(hook_util.live_sessions_snapshot(str(tmp_path))) == 1


def test_config_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert hook_util.claude_config_dir() == str(tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert hook_util.claude_config_dir().endswith(".claude")


# --- rooms_output_lines


def test_output_lines_notice_and_loud_error():
    assert hook_util.rooms_output_lines({}) == []
    assert hook_util.rooms_output_lines({"rooms_notice": "[ROOMS REGISTRY] hi"}) == [
        "[ROOMS REGISTRY] hi"
    ]
    lines = hook_util.rooms_output_lines(
        {"rooms_notice": "", "rooms_error": "could not be written at X"}
    )
    assert lines == ["[HERE I AM] could not be written at X"]


# --- The hooks as subprocesses


def run_hook(script: str, stdin_payload: dict, tmp_path, body: dict):
    """Run a hook's main() with post_backend stubbed. Returns (stdout,
    payload the hook POSTed or None)."""
    payload_file = tmp_path / "payload.json"
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = {body!r}\n"
        "def stub(path, payload, timeout=30):\n"
        f"    with open({str(payload_file)!r}, 'w', encoding='utf-8') as f:\n"
        "        json.dump(payload, f)\n"
        "    return body\n"
        "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        f"import {script}\n"
        f"{script}.main()\n"
    )
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}
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


SESSION_START_STDIN = {
    "session_id": LIVE_ENTRY["sessionId"],
    "cwd": "E:\\here-i-am-notes",
    "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\096a7e9f.jsonl",
    "source": "resume",
}


def test_session_start_sends_snapshot_and_transcript_path(tmp_path):
    write_registry(tmp_path, LIVE_ENTRY)
    _, payload = run_hook(
        "session_start", SESSION_START_STDIN, tmp_path, {"context": "", "bulk_context": ""}
    )
    assert payload["transcript_path"] == SESSION_START_STDIN["transcript_path"]
    assert payload["sessions"][0]["session_id"] == LIVE_ENTRY["sessionId"]
    assert payload["sessions"][0]["name"] == "Porch chats"


def test_session_start_prints_rooms_notice_on_plain_resume(tmp_path):
    # A plain resume prints nothing else — the notice must still reach context
    out, _ = run_hook(
        "session_start",
        SESSION_START_STDIN,
        tmp_path,
        {"context": "", "bulk_context": "", "rooms_notice": "[ROOMS REGISTRY] registered as the Porch"},
    )
    assert out.strip() == "[ROOMS REGISTRY] registered as the Porch"


def test_session_start_prints_notice_after_inline_context(tmp_path):
    out, _ = run_hook(
        "session_start",
        SESSION_START_STDIN,
        tmp_path,
        {
            "context": "[HERE I AM] identity",
            "bulk_context": "[NOTES INDEX] small",
            "rooms_notice": "[ROOMS REGISTRY] registered as the Porch",
        },
    )
    assert out.index("[HERE I AM] identity") < out.index("[NOTES INDEX] small")
    assert out.index("[NOTES INDEX] small") < out.index("[ROOMS REGISTRY]")


def test_session_start_prints_notice_after_spill_pointer(tmp_path, monkeypatch):
    out, _ = run_hook(
        "session_start",
        SESSION_START_STDIN,
        tmp_path,
        {
            "context": "[HERE I AM] identity",
            "bulk_context": "x" * 30000,
            "created": True,
            "rooms_error": "could not be written at /notes/rooms.json",
        },
    )
    assert "too large to inject inline" in out
    assert out.index("too large") < out.index("[HERE I AM] could not be written")


def test_session_start_without_registry_dir_still_posts(tmp_path):
    _, payload = run_hook(
        "session_start", SESSION_START_STDIN, tmp_path, {"context": "", "bulk_context": ""}
    )
    assert payload["sessions"] == []


def test_prompt_hook_sends_snapshot_and_prints_rooms_lines(tmp_path):
    write_registry(tmp_path, LIVE_ENTRY)
    out, payload = run_hook(
        "user_prompt_submit",
        {"session_id": "s1", "prompt": "hello", "cwd": "E:\\x"},
        tmp_path,
        {
            "context": "",
            "rooms_notice": '[ROOMS REGISTRY] Roster name change recorded — Porch: now "Porch chats"',
        },
    )
    assert payload["sessions"][0]["name"] == "Porch chats"
    assert "Roster name change recorded" in out
    assert "Start it with [WAKEUP]" in out


def test_prompt_hook_prints_rooms_error_on_wakeup_tick(tmp_path):
    # A loop session may see nothing but ticks for hours; a registry write
    # failure must still be loud there
    out, payload = run_hook(
        "user_prompt_submit",
        {"session_id": "s1", "prompt": "[WAKEUP] tick"},
        tmp_path,
        {"context": "", "rooms_error": "could not be written at X"},
    )
    assert payload["prompt"] == ""
    assert "[HERE I AM] could not be written at X" in out

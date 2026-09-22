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

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_desktop_dir(monkeypatch, tmp_path):
    """Keep every test (and every hook subprocess) away from the real
    desktop app's session records on the machine running the suite."""
    monkeypatch.setenv("HIM_DESKTOP_DATA_DIR", str(tmp_path / "desktop"))
    return tmp_path / "desktop"

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
    # The desktop app's own session id, stable across Claude Code forks
    # (observed 2026-09-22, Claude Code 2.1.275) — the key that lets a
    # fork's first prompt find its parent (issue #359)
    "hostSessionId": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
}


def write_registry(config_dir: Path, *entries: dict) -> None:
    sessions = config_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (sessions / f"{entry.get('pid', 1)}.json").write_text(
            json.dumps(entry), encoding="utf-8"
        )


# The desktop app's per-session record (Claude desktop / Claude Code
# 2.1.260, observed 2026-09-07 for issue #339), trimmed to the fields that
# matter: sessionId is the desktop app's own id — the one send_message
# takes — and cliSessionId is the Claude Code session id the hooks see
DESKTOP_RECORD = {
    "sessionId": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
    "cliSessionId": LIVE_ENTRY["sessionId"],
    "cwd": "E:\\here-i-am-notes",
    "originCwd": "E:\\here-i-am-notes",
    "createdAt": 1788395082000,
    "lastActivityAt": 1788404038000,
    "lastFocusedAt": 1788404038000,
    "model": "claude-fable-5-1",
    "effort": "high",
    "isArchived": False,
    "title": "Porch chat",
    "titleSource": "user",
    "permissionMode": "auto",
    "bridgeSessionIds": ["session_01Uhn2Qd9K6gGAXDLH3ZGp41"],
}


def write_desktop_records(desktop_dir: Path, *records: dict) -> None:
    directory = desktop_dir / "claude-code-sessions" / "org-0000" / "account-0000"
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        (directory / f"{record['sessionId']}.json").write_text(
            json.dumps(record), encoding="utf-8"
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
            "host_session_id": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
            # Taken straight from the registry now, not only from the
            # cliSessionId join, which a freshly forked session misses
            "desktop_session_id": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
            "desktop_title": None,
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
    assert entry["host_session_id"] is None
    assert entry["desktop_session_id"] is None
    assert entry["desktop_title"] is None


# --- the desktop app's session records (issue #339)


def test_snapshot_joins_desktop_record_on_cli_session_id(tmp_path, isolated_desktop_dir):
    write_registry(tmp_path, LIVE_ENTRY, {"pid": 7, "sessionId": "no-desktop-record"})
    write_desktop_records(isolated_desktop_dir, DESKTOP_RECORD)
    by_id = {s["session_id"]: s for s in hook_util.live_sessions_snapshot(str(tmp_path))}
    porch = by_id[LIVE_ENTRY["sessionId"]]
    assert porch["desktop_session_id"] == "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a"
    assert porch["desktop_title"] == "Porch chat"
    assert porch["name"] == "Porch chats"  # the registry's fields are untouched
    assert by_id["no-desktop-record"]["desktop_session_id"] is None


def test_snapshot_appends_own_session_when_only_the_desktop_record_has_it(
    tmp_path, isolated_desktop_dir
):
    # The per-process registry may be unreadable; the hook's own session
    # still gets its address recorded from the desktop record alone
    write_desktop_records(isolated_desktop_dir, DESKTOP_RECORD)
    snapshot = hook_util.live_sessions_snapshot(
        str(tmp_path / "no-registry"), own_session_id=LIVE_ENTRY["sessionId"]
    )
    assert snapshot == [{
        "session_id": LIVE_ENTRY["sessionId"],
        "name": None,
        "name_source": None,
        "name_since": None,
        "messaging_socket": None,
        "cwd": None,
        "started_at": None,
        "desktop_session_id": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
        "desktop_title": "Porch chat",
    }]
    # ...but never a sibling: only declared rows are refreshed anyway, and
    # the registry is the liveness source
    assert hook_util.live_sessions_snapshot(str(tmp_path / "no-registry")) == []


def test_snapshot_does_not_duplicate_own_session(tmp_path, isolated_desktop_dir):
    write_registry(tmp_path, LIVE_ENTRY)
    write_desktop_records(isolated_desktop_dir, DESKTOP_RECORD)
    snapshot = hook_util.live_sessions_snapshot(
        str(tmp_path), own_session_id=LIVE_ENTRY["sessionId"]
    )
    assert len(snapshot) == 1
    assert snapshot[0]["desktop_session_id"] == DESKTOP_RECORD["sessionId"]


def test_desktop_index_skips_unjoinable_and_unparsable_records(isolated_desktop_dir):
    write_desktop_records(
        isolated_desktop_dir,
        DESKTOP_RECORD,
        {"sessionId": "local_no-cli-id", "title": "orphan"},
        {"sessionId": "local_blank-cli-id", "cliSessionId": "  ", "title": "orphan"},
    )
    records = isolated_desktop_dir / "claude-code-sessions" / "org-0000" / "account-0000"
    (records / "local_broken.json").write_text("{nope", encoding="utf-8")
    (records / "local_list.json").write_text("[]", encoding="utf-8")
    # A deleted session's directory sits beside the records and is not one
    (records / "deleted_0b6bb4a2").mkdir()
    (records / "not-a-session.json").write_text(
        json.dumps({"sessionId": "x", "cliSessionId": "y"}), encoding="utf-8"
    )
    index = hook_util.desktop_sessions_index()
    assert index == {
        LIVE_ENTRY["sessionId"]: {
            "desktop_session_id": "local_ad0cb4d4-901e-4fb1-8a84-33af914a222a",
            "desktop_title": "Porch chat",
            # The fork-adoption chain rides along on the same scan (#357);
            # this record has none
            "prior_session_ids": [],
        }
    }


def test_desktop_index_records_missing_title_as_none(isolated_desktop_dir):
    write_desktop_records(
        isolated_desktop_dir, {"sessionId": "local_untitled", "cliSessionId": "cli-1"}
    )
    assert hook_util.desktop_sessions_index()["cli-1"]["desktop_title"] is None


def test_desktop_index_empty_when_directory_missing(tmp_path):
    assert hook_util.desktop_sessions_index(str(tmp_path / "nowhere")) == {}


def test_desktop_data_dir_honors_env_then_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("HIM_DESKTOP_DATA_DIR", str(tmp_path))
    assert hook_util.claude_desktop_data_dir() == str(tmp_path)
    monkeypatch.delenv("HIM_DESKTOP_DATA_DIR")
    default = hook_util.claude_desktop_data_dir()
    assert os.path.basename(default) == "Claude"
    if sys.platform == "win32":
        assert default.startswith(os.environ.get("APPDATA") or "")
    elif sys.platform == "darwin":
        assert "Application Support" in default
    else:
        assert ".config" in default or os.environ.get("XDG_CONFIG_HOME", "") in default


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


def test_prompt_hook_sends_desktop_fields_and_letter_addresses(tmp_path, isolated_desktop_dir):
    write_registry(tmp_path, LIVE_ENTRY)
    write_desktop_records(isolated_desktop_dir, DESKTOP_RECORD)
    letter = (
        '<cross-session-message from="local_d0ea5527-ad93-4031-98b9-957d27c9edb0" '
        'name="Substack engagement">the porch is asked a question</cross-session-message>'
    )
    _, payload = run_hook(
        "user_prompt_submit",
        {"session_id": LIVE_ENTRY["sessionId"], "prompt": letter, "cwd": "E:\\x"},
        tmp_path,
        {"context": ""},
    )
    [session] = payload["sessions"]
    assert session["desktop_session_id"] == DESKTOP_RECORD["sessionId"]
    assert session["desktop_title"] == "Porch chat"
    [peer] = payload["peer_messages"]
    assert peer["sender"] == "Substack engagement"
    assert peer["sender_session"] == "local_d0ea5527-ad93-4031-98b9-957d27c9edb0"
    assert peer["content"] == "the porch is asked a question"


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


# --- fork adoption at the FIRST prompt (issue #359)
#
# The desktop app rewrites its own record a few seconds AFTER a fork's first
# prompt hook fires (measured on a live rewind: prompt 16:22:14, record
# 16:22:19), so at that prompt the record still names the PARENT as its
# cliSessionId. The index is keyed on cliSessionId, so the fork's own id
# matched nothing and the hint went out empty — which is why the first hook
# lost every time. hostSessionId is the way in: it is in the per-process
# registry from process start, and it does not change when Claude Code forks.

FORK_SESSION_ID = "c4aed985-1111-2222-3333-444444444444"
PARENT_SESSION_ID = "e40799e3-5555-6666-7777-888888888888"

FORK_REGISTRY_ENTRY = {
    "pid": 9001,
    "sessionId": FORK_SESSION_ID,
    "cwd": "E:\\here-i-am-notes",
    "startedAt": 1788395082135,
    "entrypoint": "claude-desktop",
    "name": "Porch chat continuation",
    "nameSource": "derived",
    # Same desktop session as before the rewind
    "hostSessionId": "local_7d7e55dd-4952-41b5-b47e-4182d676f06b",
}

# The record as it stands at the fork's first prompt: not yet rewritten, so
# it still names the parent and lists the chain before it, oldest first
RECORD_BEFORE_THE_REWRITE = {
    "sessionId": "local_7d7e55dd-4952-41b5-b47e-4182d676f06b",
    "cliSessionId": PARENT_SESSION_ID,
    "priorCliSessionIds": ["ce122085", "7241556a", "c4c8625a", "4a17f344", "1a74d27b"],
    "title": "Porch chat",
}


def test_prior_ids_find_the_parent_at_a_forks_first_prompt(
    tmp_path, isolated_desktop_dir
):
    """The regression the live test exposed: with no transcript yet and the
    record not rewritten, the hint still resolves — through hostSessionId —
    and the immediate parent is LAST, since the backend walks the list by
    reversing it."""
    write_registry(tmp_path, FORK_REGISTRY_ENTRY)
    write_desktop_records(isolated_desktop_dir, RECORD_BEFORE_THE_REWRITE)
    priors = hook_util.desktop_prior_session_ids(
        FORK_SESSION_ID, config_dir=str(tmp_path)
    )
    assert priors == [
        "ce122085", "7241556a", "c4c8625a", "4a17f344", "1a74d27b", PARENT_SESSION_ID,
    ]
    assert priors[-1] == PARENT_SESSION_ID


def test_lineage_hints_carry_the_parent_with_no_transcript(
    tmp_path, isolated_desktop_dir
):
    """A fork's first prompt hook: the transcript path does not exist yet, so
    the whole hint has to come from the desktop record."""
    write_registry(tmp_path, FORK_REGISTRY_ENTRY)
    write_desktop_records(isolated_desktop_dir, RECORD_BEFORE_THE_REWRITE)
    hints = hook_util.lineage_hints(
        FORK_SESSION_ID,
        str(tmp_path / "does-not-exist-yet.jsonl"),
        config_dir=str(tmp_path),
    )
    assert hints["transcript_message_ids"] == []
    assert hints["prior_session_ids"][-1] == PARENT_SESSION_ID


def test_prior_ids_prefer_the_record_that_names_this_session(
    tmp_path, isolated_desktop_dir
):
    """Once the record catches up, its own priorCliSessionIds is the chain
    and the session is not its own parent."""
    caught_up = dict(
        RECORD_BEFORE_THE_REWRITE,
        cliSessionId=FORK_SESSION_ID,
        priorCliSessionIds=RECORD_BEFORE_THE_REWRITE["priorCliSessionIds"]
        + [PARENT_SESSION_ID],
    )
    write_registry(tmp_path, FORK_REGISTRY_ENTRY)
    write_desktop_records(isolated_desktop_dir, caught_up)
    priors = hook_util.desktop_prior_session_ids(
        FORK_SESSION_ID, config_dir=str(tmp_path)
    )
    assert priors[-1] == PARENT_SESSION_ID
    assert FORK_SESSION_ID not in priors


def test_a_new_session_gets_no_lineage_from_the_host_join(
    tmp_path, isolated_desktop_dir
):
    """The cost side: a genuinely new session's record names itself and has
    no priors, so the host join yields nothing and nothing waits."""
    entry = dict(FORK_REGISTRY_ENTRY, sessionId="fresh-session-0001")
    record = {
        "sessionId": "local_7d7e55dd-4952-41b5-b47e-4182d676f06b",
        "cliSessionId": "fresh-session-0001",
        "priorCliSessionIds": [],
        "title": "A new room",
    }
    write_registry(tmp_path, entry)
    write_desktop_records(isolated_desktop_dir, record)
    assert hook_util.desktop_prior_session_ids(
        "fresh-session-0001", config_dir=str(tmp_path)
    ) == []


def test_no_host_session_id_means_no_guessing(tmp_path, isolated_desktop_dir):
    """A CLI session has no desktop host. The record for some other desktop
    session must never be read as this one's parent."""
    entry = {k: v for k, v in FORK_REGISTRY_ENTRY.items() if k != "hostSessionId"}
    write_registry(tmp_path, entry)
    write_desktop_records(isolated_desktop_dir, RECORD_BEFORE_THE_REWRITE)
    assert hook_util.desktop_prior_session_ids(
        FORK_SESSION_ID, config_dir=str(tmp_path)
    ) == []


def test_host_session_id_is_read_from_a_snapshot_when_given(
    tmp_path, isolated_desktop_dir
):
    """The hooks pass the snapshot they already built rather than re-reading
    the registry in the same firing."""
    write_desktop_records(isolated_desktop_dir, RECORD_BEFORE_THE_REWRITE)
    snapshot = [{
        "session_id": FORK_SESSION_ID,
        "host_session_id": "local_7d7e55dd-4952-41b5-b47e-4182d676f06b",
    }]
    priors = hook_util.desktop_prior_session_ids(
        FORK_SESSION_ID,
        config_dir=str(tmp_path / "no-registry-here"),
        sessions=snapshot,
    )
    assert priors[-1] == PARENT_SESSION_ID

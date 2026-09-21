"""
Fork-adoption lineage hints, hook side (issue #357).

The desktop app forks a Claude Code session under a new session id on
restart/continue/rewind, copying the transcript. The hooks gather two
hints so the backend can resolve the new id to the conversation it
continues: the transcript's assistant entry uuids (which survive a fork
unchanged and are the archive's row ids) and the desktop record's
`priorCliSessionIds`. Neither read ever fails a hook.
"""
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402


def _write_transcript(tmp_path, entries):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def test_transcript_assistant_uuids_newest_last(tmp_path):
    path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "uuid": "u-user"},
            {"type": "assistant", "uuid": "a-1", "message": {"role": "assistant"}},
            {"type": "system", "uuid": "s-1"},
            {"type": "assistant", "uuid": "a-2", "message": {"role": "assistant"}},
        ],
    )
    assert hook_util.transcript_assistant_uuids(path) == ["a-1", "a-2"]


def test_transcript_assistant_uuids_limit_keeps_the_tail(tmp_path):
    entries = [
        {"type": "assistant", "uuid": f"a-{i}", "message": {"role": "assistant"}}
        for i in range(10)
    ]
    path = _write_transcript(tmp_path, entries)
    assert hook_util.transcript_assistant_uuids(path, limit=3) == ["a-7", "a-8", "a-9"]


def test_transcript_assistant_uuids_missing_file_is_empty():
    assert hook_util.transcript_assistant_uuids("/no/such/transcript.jsonl") == []
    assert hook_util.transcript_assistant_uuids(None) == []


def _write_desktop_record(desktop_dir, cli_session_id, prior):
    org = desktop_dir / "claude-code-sessions" / "org" / "acct"
    org.mkdir(parents=True, exist_ok=True)
    record = {
        "sessionId": "local_" + cli_session_id,
        "cliSessionId": cli_session_id,
        "title": "A room",
        "priorCliSessionIds": prior,
    }
    (org / f"local_{cli_session_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


def test_desktop_prior_session_ids_reads_the_chain(tmp_path):
    desktop = tmp_path / "desktop"
    _write_desktop_record(desktop, "current-id", ["old-1", "old-2"])
    assert hook_util.desktop_prior_session_ids("current-id", desktop_dir=str(desktop)) == [
        "old-1",
        "old-2",
    ]


def test_desktop_prior_session_ids_no_match_is_empty(tmp_path):
    desktop = tmp_path / "desktop"
    _write_desktop_record(desktop, "current-id", ["old-1"])
    assert hook_util.desktop_prior_session_ids("other-id", desktop_dir=str(desktop)) == []
    assert hook_util.desktop_prior_session_ids("", desktop_dir=str(desktop)) == []


def test_desktop_prior_session_ids_missing_dir_is_empty(tmp_path):
    assert hook_util.desktop_prior_session_ids(
        "current-id", desktop_dir=str(tmp_path / "nope")
    ) == []


def test_lineage_hints_bundles_both(tmp_path):
    desktop = tmp_path / "desktop"
    _write_desktop_record(desktop, "current-id", ["old-1"])
    path = _write_transcript(
        tmp_path,
        [{"type": "assistant", "uuid": "a-1", "message": {"role": "assistant"}}],
    )
    hints = hook_util.lineage_hints("current-id", path, desktop_dir=str(desktop))
    assert hints == {
        "prior_session_ids": ["old-1"],
        "transcript_message_ids": ["a-1"],
    }

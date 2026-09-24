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


def _text_entry(uid, text="a reply"):
    """An assistant entry with a text block — the kind the Stop hook records,
    so the kind whose uuid is an archive row id."""
    return {
        "type": "assistant",
        "uuid": uid,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _tool_use_entry(uid):
    """An assistant entry that only calls a tool: never recorded, so its
    uuid is never a row id."""
    return {
        "type": "assistant",
        "uuid": uid,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
        },
    }


def _prompt(uid):
    return {"type": "user", "uuid": uid, "message": {"role": "user", "content": "go"}}


def test_transcript_assistant_uuids_newest_last(tmp_path):
    path = _write_transcript(
        tmp_path,
        [
            _prompt("u-1"),
            _text_entry("a-1"),
            {"type": "system", "uuid": "s-1"},
            _prompt("u-2"),
            _text_entry("a-2"),
        ],
    )
    assert hook_util.transcript_assistant_uuids(path) == ["a-1", "a-2"]


def test_one_uuid_per_turn_the_last_text_entry(tmp_path):
    """The Stop hook records a whole turn under its last text entry (issue
    #364), so the turn's earlier text entries were never rows."""
    path = _write_transcript(
        tmp_path,
        [_prompt("u-1"), _text_entry("a-1"), _tool_use_entry("t-1"), _text_entry("a-2")],
    )
    assert hook_util.transcript_assistant_uuids(path) == ["a-2"]


def test_tool_use_only_entries_are_skipped(tmp_path):
    """One agentic turn can be dozens of tool-use entries; counting them
    would crowd every recorded id out of the window."""
    path = _write_transcript(
        tmp_path,
        [_text_entry("a-1")] + [_tool_use_entry(f"t-{i}") for i in range(20)],
    )
    assert hook_util.transcript_assistant_uuids(path, limit=5) == ["a-1"]


def test_empty_text_block_does_not_count(tmp_path):
    path = _write_transcript(tmp_path, [_text_entry("a-blank", text="   ")])
    assert hook_util.transcript_assistant_uuids(path) == []


def test_transcript_assistant_uuids_limit_keeps_the_tail(tmp_path):
    entries = []
    for i in range(10):
        entries += [_prompt(f"u-{i}"), _text_entry(f"a-{i}")]
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
    path = _write_transcript(tmp_path, [_text_entry("a-1")])
    hints = hook_util.lineage_hints("current-id", path, desktop_dir=str(desktop))
    assert hints == {
        "prior_session_ids": ["old-1"],
        "transcript_message_ids": ["a-1"],
    }


def test_lineage_hints_reuses_a_prebuilt_desktop_index(tmp_path):
    """The hooks scan the desktop records once per firing and share the
    result with the rooms snapshot."""
    desktop = tmp_path / "desktop"
    _write_desktop_record(desktop, "current-id", ["old-1", "old-2"])
    index = hook_util.desktop_sessions_index(str(desktop))
    assert index["current-id"]["prior_session_ids"] == ["old-1", "old-2"]
    path = _write_transcript(tmp_path, [_text_entry("a-1")])
    # No desktop_dir passed: it must come from the index, not a fresh scan
    hints = hook_util.lineage_hints("current-id", path, desktop_index=index)
    assert hints["prior_session_ids"] == ["old-1", "old-2"]


def test_prior_ids_keep_desktop_order_oldest_first(tmp_path):
    """The backend reverses; the hook reports what the record says."""
    desktop = tmp_path / "desktop"
    _write_desktop_record(desktop, "current-id", ["oldest", "middle", "parent"])
    assert hook_util.desktop_prior_session_ids(
        "current-id", desktop_dir=str(desktop)
    ) == ["oldest", "middle", "parent"]

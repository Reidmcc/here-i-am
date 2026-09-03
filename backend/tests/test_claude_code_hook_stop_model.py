"""
The Stop hook's model attribution (issue #321).

The archive's `model` column is written forward-only, at the one moment
the fact is knowable: for a Claude Code turn, that is the transcript entry
whose text the Stop hook records, which carries `message.model`. The hook
must read it verbatim off that entry — the same entry the text comes from,
not the last one in the file — and send it with the message; an entry
without one yields None, never a guess.
"""
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import stop  # noqa: E402


def _assistant_entry(text: str, uuid: str, model=None, content_type="text"):
    message = {"role": "assistant", "content": [{"type": content_type, "text": text}]}
    if model is not None:
        message["model"] = model
    return {"type": "assistant", "uuid": uuid, "message": message}


def _write_transcript(tmp_path, entries):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    return str(path)


def test_entry_model_reads_message_model():
    assert stop.entry_model(_assistant_entry("hi", "u1", model="claude-fable-5-1")) == "claude-fable-5-1"


def test_entry_model_absent_or_blank_is_none():
    assert stop.entry_model(_assistant_entry("hi", "u1")) is None
    assert stop.entry_model(_assistant_entry("hi", "u1", model="   ")) is None
    assert stop.entry_model({"type": "assistant", "message": "not a dict"}) is None
    assert stop.entry_model({"type": "assistant"}) is None


def test_last_assistant_text_carries_the_entry_model(tmp_path):
    path = _write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        _assistant_entry("Done.", "u-final", model="claude-fable-5-1"),
    ])
    text, entry_uuid, model = stop.last_assistant_text(path)
    assert text == "Done."
    assert entry_uuid == "u-final"
    assert model == "claude-fable-5-1"


def test_model_comes_from_the_recorded_entry_not_the_last_line(tmp_path):
    # A trailing assistant entry with no text (a tool call, say) is skipped
    # for the text; its model must not be used for the text entry either.
    path = _write_transcript(tmp_path, [
        _assistant_entry("The words.", "u-text", model="claude-opus-5"),
        {
            "type": "assistant",
            "uuid": "u-tool",
            "message": {
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "tool_use", "name": "Read", "input": {}}],
            },
        },
    ])
    text, entry_uuid, model = stop.last_assistant_text(path)
    assert text == "The words."
    assert entry_uuid == "u-text"
    assert model == "claude-opus-5"


def test_missing_model_is_none_not_inferred(tmp_path):
    path = _write_transcript(tmp_path, [
        _assistant_entry("Earlier, attributed.", "u1", model="claude-fable-5-1"),
        _assistant_entry("Later, unattributed.", "u2"),
    ])
    text, _, model = stop.last_assistant_text(path)
    assert text == "Later, unattributed."
    assert model is None


def test_unreadable_transcript_returns_triple():
    assert stop.last_assistant_text("/nonexistent/transcript.jsonl") == (None, None, None)

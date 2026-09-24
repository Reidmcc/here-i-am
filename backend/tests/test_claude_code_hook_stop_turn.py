"""
One row per turn (issue #364), hook side.

The Stop hook records everything the entity said in the turn that just
ended — every text chunk since the turn's boundary, in order, with "[…]"
on its own line where tool calls fell between two chunks — under the
turn's LAST text entry's uuid. The archive is the talk: tool calls are
deeds and thinking blocks are never read. The fork-adoption hint names
the same one uuid per turn, split by the same boundary rule.

The entry shapes here are the ones read off real transcripts (see the
note above hook_util.is_turn_boundary).
"""
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402
import stop  # noqa: E402

MARK = stop.TOOL_CALL_MARKER


def _write(tmp_path, entries, name="transcript.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def prompt(text, uid="p", **extra):
    return {
        "type": "user",
        "uuid": uid,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
        **extra,
    }


def said(text, uid, model="claude-opus-5-5"):
    return {
        "type": "assistant",
        "uuid": uid,
        "message": {
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
        },
    }


def tool_call(uid, tool_id="t1"):
    return {
        "type": "assistant",
        "uuid": uid,
        "message": {
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {}}],
        },
    }


def tool_result(uid, tool_id="t1"):
    return {
        "type": "user",
        "uuid": uid,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
        },
        "toolUseResult": {"stdout": "ok"},
    }


def meta(text, uid, **extra):
    return {
        "type": "user",
        "uuid": uid,
        "isMeta": True,
        "message": {"role": "user", "content": text},
        **extra,
    }


def test_the_issue_transcript_records_all_three_texts_in_order(tmp_path):
    """text → tool_use → tool_result → text → tool_use → tool_result →
    text: all three recorded in order, under the last uuid, nothing from
    before the turn's prompt."""
    path = _write(tmp_path, [
        prompt("an earlier prompt", "p0"),
        said("An earlier turn's reply.", "a0"),
        prompt("the prompt", "p1"),
        said("Looking at the hook first.", "a1"),
        tool_call("c1"),
        tool_result("r1"),
        said("That's not what I expected.", "a2"),
        tool_call("c2", "t2"),
        tool_result("r2", "t2"),
        said("Fixed; the tests pass.", "a3"),
    ])
    text, entry_uuid, model = stop.turn_assistant_text(path)
    assert text == (
        f"Looking at the hook first.\n\n{MARK}\n\n"
        f"That's not what I expected.\n\n{MARK}\n\n"
        "Fixed; the tests pass."
    )
    assert entry_uuid == "a3"
    assert model == "claude-opus-5-5"


def test_a_single_message_turn_is_unchanged(tmp_path):
    path = _write(tmp_path, [prompt("hi"), said("Hello.", "a1")])
    assert stop.turn_assistant_text(path)[:2] == ("Hello.", "a1")


def test_no_marker_for_tool_calls_before_the_first_or_after_the_last_chunk(tmp_path):
    path = _write(tmp_path, [
        prompt("go"),
        tool_call("c1"),
        tool_result("r1"),
        said("Found it.", "a1"),
        tool_call("c2", "t2"),
        tool_result("r2", "t2"),
    ])
    assert stop.turn_assistant_text(path)[:2] == ("Found it.", "a1")


def test_several_tool_calls_between_chunks_are_one_marker(tmp_path):
    path = _write(tmp_path, [
        prompt("go"),
        said("One.", "a1"),
        tool_call("c1"), tool_result("r1"),
        tool_call("c2", "t2"), tool_result("r2", "t2"),
        said("Two.", "a2"),
    ])
    assert stop.turn_assistant_text(path)[0] == f"One.\n\n{MARK}\n\nTwo."


def test_text_and_tool_use_in_one_entry(tmp_path):
    entry = {
        "type": "assistant",
        "uuid": "a1",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Before."},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
            {"type": "text", "text": "After."},
        ]},
    }
    path = _write(tmp_path, [prompt("go"), entry])
    assert stop.turn_assistant_text(path)[0] == f"Before.\n\n{MARK}\n\nAfter."


def test_adjacent_chunks_join_without_a_marker(tmp_path):
    path = _write(tmp_path, [prompt("go"), said("First.", "a1"), said("Second.", "a2")])
    assert stop.turn_assistant_text(path)[:2] == ("First.\n\nSecond.", "a2")


def test_no_filtering_short_lines_are_talk(tmp_path):
    path = _write(tmp_path, [
        prompt("go"),
        said("Checking now.", "a1"),
        tool_call("c1"), tool_result("r1"),
        said("ok", "a2"),
    ])
    assert stop.turn_assistant_text(path)[0] == f"Checking now.\n\n{MARK}\n\nok"


def test_only_text_blocks_are_read(tmp_path):
    """Thinking blocks are reasoning, not talk: never collected, and an
    entry carrying only one is not the turn's row."""
    reasoning = {
        "type": "assistant",
        "uuid": "a-r",
        "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "PRIVATE", "signature": "sig"},
        ]},
    }
    redacted = {
        "type": "assistant",
        "uuid": "a-rr",
        "message": {"role": "assistant", "content": [
            {"type": "redacted_thinking", "data": "PRIVATE"},
        ]},
    }
    path = _write(tmp_path, [
        prompt("go"), said("Said.", "a1"), reasoning, redacted,
    ])
    text, entry_uuid, _ = stop.turn_assistant_text(path)
    assert text == "Said."
    assert "PRIVATE" not in text
    assert entry_uuid == "a1"


def test_a_turn_with_no_text_records_nothing(tmp_path):
    """Not the previous turn's text again: the old last-entry rule re-posted
    it (a no-op on its uuid); the turn rule has nothing to post."""
    path = _write(tmp_path, [
        prompt("first"), said("Earlier reply.", "a0"),
        prompt("second", "p2"), tool_call("c1"), tool_result("r1"),
    ])
    assert stop.turn_assistant_text(path) == (None, None, None)


def test_synthetic_entries_are_not_the_entitys_words(tmp_path):
    path = _write(tmp_path, [
        prompt("go"),
        said("Real words.", "a1"),
        said("No response requested.", "a-syn", model="<synthetic>"),
    ])
    assert stop.turn_assistant_text(path)[:2] == ("Real words.", "a1")


def test_subagent_entries_are_skipped(tmp_path):
    side = said("A subagent's report.", "a-side")
    side["isSidechain"] = True
    side_prompt = prompt("subagent task", "p-side", isSidechain=True)
    path = _write(tmp_path, [
        prompt("go"), said("Mine.", "a1"), side_prompt, side,
    ])
    assert stop.turn_assistant_text(path)[:2] == ("Mine.", "a1")


def test_a_mid_turn_compaction_keeps_the_whole_turn(tmp_path):
    """Auto-compaction lands mid-turn and the entries before its summary
    stay in the file: the turn's text before the boundary is still its."""
    path = _write(tmp_path, [
        prompt("go"),
        said("Before compaction.", "a1"),
        tool_call("c1"), tool_result("r1"),
        {"type": "system", "subtype": "compact_boundary", "uuid": "s1"},
        {
            "type": "user",
            "uuid": "sum",
            "isCompactSummary": True,
            "isVisibleInTranscriptOnly": True,
            "message": {"role": "user", "content": "This session is being continued..."},
        },
        said("After compaction.", "a2"),
    ])
    text, entry_uuid, _ = stop.turn_assistant_text(path)
    assert text == f"Before compaction.\n\n{MARK}\n\nAfter compaction."
    assert entry_uuid == "a2"


def test_mid_turn_meta_injections_are_not_boundaries(tmp_path):
    path = _write(tmp_path, [
        prompt("go"),
        said("Loading the skill.", "a1"),
        tool_call("c1"), tool_result("r1"),
        meta("Base directory for this skill: ...", "m1"),
        meta("[Image: original 2400x1600]", "m2"),
        meta("Your response above was stopped by a safety classifier.", "m3"),
        meta("Continue from where you left off.", "m4"),
        said("Done.", "a2"),
    ])
    assert stop.turn_assistant_text(path)[0] == f"Loading the skill.\n\n{MARK}\n\nDone."


def test_boundaries_that_start_a_turn(tmp_path):
    """A prompt, a [WAKEUP] tick, a sibling's letter, Stop-hook feedback,
    a slash command, and the harness's own stop summary each start a new
    turn, so text before them was recorded by an earlier Stop."""
    starts = [
        prompt("a new prompt", "b1"),
        meta("[WAKEUP] continue", "b2"),
        meta("/loop [WAKEUP] tick", "b3"),
        meta(
            "Another Claude session sent a message:\n"
            '<cross-session-message from="local_x" name="Porch">hi</cross-session-message>',
            "b4",
            origin={"kind": "peer", "from": "local_x"},
        ),
        meta("Stop hook feedback:\n[a loop hook]: keep going", "b5"),
        {
            "type": "user",
            "uuid": "b6",
            "message": {"role": "user", "content": "<command-name>/model</command-name>"},
        },
        {"type": "system", "subtype": "stop_hook_summary", "uuid": "b7"},
        meta("<task-notification>done</task-notification>", "b8",
             origin={"kind": "task-notification"}),
    ]
    for start in starts:
        assert hook_util.is_turn_boundary(start), start["uuid"]
        path = _write(tmp_path, [
            prompt("go", "p0"), said("Previous turn.", "a0"), start, said("This turn.", "a1"),
        ], name=f"{start['uuid']}.jsonl")
        assert stop.turn_assistant_text(path)[:2] == ("This turn.", "a1"), start["uuid"]


def test_tool_result_carriers_are_not_boundaries():
    assert not hook_util.is_turn_boundary(tool_result("r1"))
    assert not hook_util.is_turn_boundary({"type": "system", "subtype": "compact_boundary"})
    assert not hook_util.is_turn_boundary({"type": "attachment", "attachment": {"type": "queued_command"}})


def test_a_tool_result_mentioning_the_marker_words_is_still_a_carrier(tmp_path):
    """The fast path skips tool_result lines on a substring; a PROMPT that
    quotes transcript JSON carries it escaped, so it is still parsed."""
    quoted = prompt('look at this: {"type": "tool_result"} and "user"', "p2")
    path = _write(tmp_path, [
        prompt("go"), said("Before.", "a0"), quoted, said("After.", "a1"),
    ])
    assert stop.turn_assistant_text(path)[:2] == ("After.", "a1")


# ---- the fork-adoption hint: one uuid per turn, the same split ----------


def test_lineage_hint_is_each_turns_last_text_entry(tmp_path):
    path = _write(tmp_path, [
        prompt("one", "p1"),
        said("1a", "a1"), tool_call("c1"), tool_result("r1"), said("1b", "a2"),
        prompt("two", "p2"),
        said("2a", "a3"), tool_call("c2", "t2"), tool_result("r2", "t2"),
        prompt("three", "p3"),
        tool_call("c3", "t3"), tool_result("r3", "t3"),
        prompt("four", "p4"),
        said("4a", "a4"),
    ])
    assert hook_util.transcript_assistant_uuids(path) == ["a2", "a3", "a4"]


def test_lineage_hint_matches_what_each_stop_recorded(tmp_path):
    """Replay the Stop hook at the end of every turn as the transcript
    grows: the ids it recorded are exactly the hint's ids."""
    turns = [
        [prompt("one", "p1"), said("1a", "a1"), tool_call("c1"), tool_result("r1"), said("1b", "a2")],
        [meta("[WAKEUP] tick", "p2"), said("2a", "a3")],
        [prompt("three", "p3"), said("3a", "a4"), tool_call("c3", "t3"), tool_result("r3", "t3"),
         said("3b", "a5"), said("", "a-blank")],
    ]
    entries, recorded = [], []
    for turn in turns:
        entries += turn
        path = _write(tmp_path, entries)
        recorded.append(stop.turn_assistant_text(path)[1])
    assert recorded == ["a2", "a3", "a5"]
    assert hook_util.transcript_assistant_uuids(path) == recorded

"""
The context gauge (issue #365).

The identity block asks the entity to save a reflection "when you notice
context running low", and in Claude Code mode nothing let it notice. The
Stop hook now measures each turn's context — the provider-counted usage on
the last main-thread assistant entry — against the auto-compaction line,
and speaks once, at 90%, as exit 2 so an unattended room gets a turn to
save in (the 75% band was removed in issue #373). Never per turn, never
twice from a continuation — a crossing there is held for the next
prompt — re-armed after a compaction.

The line is the harness's: the auto-compact window (its environment
variable, else the settings files) less the measured reserve — a room set
to 500k compacts near 467k, and a gauge on the model's 1M would speak
after the fact.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

LINE_500K = 467_000


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Gauge state in tmp_path; no harness configuration leaking in from
    the machine running the tests."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    for name in (hook_util.COMPACT_WINDOW_ENV, "HIM_COMPACT_LINE", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_settings(directory: Path, data: dict, name="settings.json") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(data), encoding="utf-8")


# --- The window and the line


@pytest.mark.parametrize(
    "value, expected",
    [
        ("500000", 500_000),
        (" 250000 ", 250_000),
        ("50000", 100_000),  # raised to the harness's 100k floor
        ("2000000", 1_000_000),  # capped at 1M
        ("700k", 100_000),  # parseInt reads 700: no suffixes on this path
        ("abc", None),  # invalid: the harness moves on to the settings
        ("0", None),
        ("-5", None),
        ("", None),
        (None, None),
    ],
)
def test_env_window_is_an_integer_clamped_to_the_harness_bounds(value, expected):
    assert hook_util.env_compact_window(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (500000, 500_000),
        (100000, 100_000),
        (1000000, 1_000_000),
        (500000.0, 500_000),
        (50000, None),  # out of range: dropped, not clamped
        (2000000, None),
        ("500k", None),  # the schema is an integer; strings are dropped
        ("500000", None),
        ("auto", None),
        (500000.5, None),
        (True, None),
        (None, None),
    ],
)
def test_setting_window_is_a_whole_number_in_range_or_absent(value, expected):
    assert hook_util.setting_compact_window(value) == expected


def test_line_defaults_to_the_1m_window_less_the_reserve():
    assert hook_util.compact_line({}, None) == 967_000


def test_line_follows_the_project_local_setting(tmp_path):
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    assert hook_util.compact_line({}, str(project)) == LINE_500K


def test_local_settings_beat_project_settings_beat_user_settings(tmp_path):
    project = tmp_path / "project"
    _write_settings(Path(os.environ["CLAUDE_CONFIG_DIR"]), {"autoCompactWindow": 300000})
    assert hook_util.configured_compact_window(str(project)) == 300_000
    _write_settings(project / ".claude", {"autoCompactWindow": 400000})
    assert hook_util.configured_compact_window(str(project)) == 400_000
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    assert hook_util.configured_compact_window(str(project)) == 500_000


def test_an_invalid_setting_is_absent_so_the_files_below_it_count(tmp_path):
    # The harness drops an invalid value from its file; what the files
    # below say still merges through
    project = tmp_path / "project"
    _write_settings(Path(os.environ["CLAUDE_CONFIG_DIR"]), {"autoCompactWindow": 300000})
    _write_settings(project / ".claude", {"autoCompactWindow": "auto"}, "settings.local.json")
    assert hook_util.configured_compact_window(str(project)) == 300_000
    _write_settings(project / ".claude", {"autoCompactWindow": 2_000_000}, "settings.local.json")
    assert hook_util.configured_compact_window(str(project)) == 300_000


def test_the_harness_environment_variable_beats_the_settings(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    monkeypatch.setenv(hook_util.COMPACT_WINDOW_ENV, "700000")
    assert hook_util.compact_line({}, str(project)) == 667_000


def test_an_invalid_environment_variable_falls_through_to_the_settings(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    monkeypatch.setenv(hook_util.COMPACT_WINDOW_ENV, "auto")
    assert hook_util.compact_line({}, str(project)) == LINE_500K


def test_a_configured_window_beats_him_compact_line(tmp_path, monkeypatch):
    # The decision on the issue: the harness's own setting is the truth
    # when the hook can see it; HIM_COMPACT_LINE is for when it can't
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    monkeypatch.setenv("HIM_COMPACT_LINE", "123456")
    assert hook_util.compact_line({}, str(project)) == LINE_500K
    assert hook_util.compact_line({}, str(tmp_path / "elsewhere")) == 123_456


def test_backend_numbers_are_used_when_nothing_is_configured():
    body = {"compact_window": 800_000, "compact_reserve": 40_000}
    assert hook_util.compact_line(body, None) == 760_000


def _write_global_config(data: dict) -> None:
    path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_server_pushed_window_for_the_model_is_read(tmp_path, monkeypatch):
    # autoCompactWindowsCache: after env and settings, before the default
    _write_global_config({"autoCompactWindowsCache": {
        "claude-opus-5-5": 600000,
        "claude-sonnet-5": {"default": 800000, "surfaces": {"local-agent": {"default": 500000}}},
    }})
    assert hook_util.compact_line({}, None, "claude-opus-5-5") == 567_000
    assert hook_util.compact_line({}, None, "claude-sonnet-5") == 767_000
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "local-agent")
    assert hook_util.compact_line({}, None, "claude-sonnet-5") == LINE_500K
    # Another model, or none known: the default
    assert hook_util.compact_line({}, None, "claude-fable-5-1") == 967_000
    assert hook_util.compact_line({}, None, None) == 967_000
    # The settings still win over the cache
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    assert hook_util.compact_line({}, str(project), "claude-opus-5-5") == LINE_500K


def test_a_null_cache_is_no_window():
    _write_global_config({"autoCompactWindowsCache": None})
    assert hook_util.configured_compact_window(None, "claude-opus-5-5") is None


def test_auto_compaction_can_be_off(tmp_path, monkeypatch):
    project = tmp_path / "project"
    assert hook_util.auto_compact_enabled(str(project))
    _write_global_config({"autoCompactEnabled": False})
    assert not hook_util.auto_compact_enabled(str(project))
    # A settings file that names it wins over the global config
    _write_settings(project / ".claude", {"autoCompactEnabled": True}, "settings.local.json")
    assert hook_util.auto_compact_enabled(str(project))
    monkeypatch.setenv("DISABLE_AUTO_COMPACT", "1")
    assert not hook_util.auto_compact_enabled(str(project))


# --- The measurement


def _assistant(usage=None, sidechain=False, text="Done."):
    message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if usage is not None:
        message["usage"] = usage
    entry = {"type": "assistant", "uuid": "u", "message": message}
    if sidechain:
        entry["isSidechain"] = True
    return entry


def _usage(total_input=300_000, output=500):
    return {
        "input_tokens": 2,
        "cache_creation_input_tokens": 4_000,
        "cache_read_input_tokens": total_input - 4_002,
        "output_tokens": output,
    }


def _transcript(tmp_path, entries, name="transcript.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def test_usage_tokens_sums_the_context():
    assert hook_util.usage_tokens(_usage(300_000, 500)) == 300_500


def test_usage_tokens_reads_the_last_message_iteration():
    usage = {
        "input_tokens": 10,
        "cache_read_input_tokens": 900_000,  # summed across iterations
        "output_tokens": 10,
        "iterations": [
            {"type": "message", "input_tokens": 5, "cache_read_input_tokens": 400_000, "output_tokens": 5},
            {"type": "message", "input_tokens": 5, "cache_read_input_tokens": 500_000, "output_tokens": 5},
            {"type": "compaction", "input_tokens": 1, "output_tokens": 1},
        ],
    }
    assert hook_util.usage_tokens(usage) == 500_010


def test_usage_tokens_silent_without_usage():
    assert hook_util.usage_tokens(None) is None
    assert hook_util.usage_tokens({}) is None
    assert hook_util.usage_tokens({"input_tokens": 0, "output_tokens": 0}) is None


def test_last_context_tokens_from_the_transcript(tmp_path):
    path = _transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _assistant(_usage(100_000)),
        _assistant(_usage(350_000, 0)),
    ])
    assert hook_util.last_context_tokens(path) == 350_000


def test_last_context_tokens_skips_subagent_and_usage_less_entries(tmp_path):
    path = _transcript(tmp_path, [
        _assistant(_usage(350_000, 0)),
        _assistant(_usage(20_000, 0), sidechain=True),
        _assistant(None),
    ])
    assert hook_util.last_context_tokens(path) == 350_000


def test_last_context_tokens_silent_without_usage(tmp_path):
    assert hook_util.last_context_tokens(_transcript(tmp_path, [_assistant(None)])) is None
    assert hook_util.last_context_tokens(str(tmp_path / "missing.jsonl")) is None
    assert hook_util.last_context_tokens("") is None


def test_last_context_tokens_reads_only_the_tail_of_a_large_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_util, "_TRANSCRIPT_TAIL_BYTES", 4096)
    filler = [{"type": "user", "message": {"content": "x" * 200}} for _ in range(100)]
    path = _transcript(tmp_path, [_assistant(_usage(900_000)), *filler, _assistant(_usage(400_000, 0))])
    assert hook_util.last_context_tokens(path) == 400_000


# --- Once per band


def _pct(fraction):
    return round(LINE_500K * fraction)


def test_quiet_below_the_band():
    # Issue #373: 75% no longer speaks, held or otherwise — nothing below 90%
    for fraction in (0.5, 0.75, 0.76, 0.8, 0.85, 0.89):
        assert hook_util.check_context_gauge("s", _pct(fraction), LINE_500K) == ""
        assert hook_util.take_held_gauge_notice("s") == ""


def test_the_band_interrupts_once():
    notice = hook_util.check_context_gauge("s", _pct(0.91), LINE_500K)
    assert notice.startswith("[HERE I AM] Context is at about 91% of the auto-compaction line")
    assert "of the auto-compaction line (~" in notice and "of ~467k tokens)" in notice
    assert "If you want to save a reflection on the conversation as it stands before compaction, now is the time." in notice
    # Not "keep verbatim": the talk comes back through memory_read
    assert "verbatim" not in notice
    assert "This turn continues once" in notice
    assert hook_util.take_held_gauge_notice("s") == ""
    for fraction in (0.93, 0.95, 0.99):
        assert hook_util.check_context_gauge("s", _pct(fraction), LINE_500K) == ""
    assert hook_util.take_held_gauge_notice("s") == ""


def test_a_continuation_turn_holds_instead_of_interrupting():
    # stop_hook_active: the turn is already a Stop continuation, and a
    # second exit 2 would chain; the crossing is held for the next prompt
    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K, may_interrupt=False) == ""
    held = hook_util.take_held_gauge_notice("s")
    assert held.startswith("[HERE I AM] At the end of your last turn, context was at about 92%")
    assert held.endswith(
        "If you want to save a reflection on the conversation as it stands "
        "before compaction, now is the time."
    )
    assert "This turn continues" not in held
    # Taken means gone, and the band doesn't speak again
    assert hook_util.take_held_gauge_notice("s") == ""
    assert hook_util.check_context_gauge("s", _pct(0.93), LINE_500K) == ""


def test_a_record_from_the_two_band_gauge_keeps_only_the_band_still_here(tmp_path):
    # A state file written before #373 can carry 0.75 as fired; it reads as
    # not fired, and 90% still speaks when it hasn't
    state_dir = tmp_path / "here-i-am-sessions"
    state_dir.mkdir()
    (state_dir / "s-context-gauge.json").write_text(
        json.dumps({"fired": [0.75], "held": None}), encoding="utf-8"
    )
    assert hook_util.check_context_gauge("s", _pct(0.80), LINE_500K) == ""
    assert hook_util.check_context_gauge("s", _pct(0.91), LINE_500K)


def test_bands_re_arm_after_the_context_shrinks():
    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K)
    # Compaction: the context falls to a few percent, then refills
    assert hook_util.check_context_gauge("s", _pct(0.03), LINE_500K) == ""
    assert hook_util.check_context_gauge("s", _pct(0.80), LINE_500K) == ""
    assert hook_util.take_held_gauge_notice("s") == ""
    assert hook_util.check_context_gauge("s", _pct(0.91), LINE_500K)


def test_a_small_dip_does_not_re_arm():
    assert hook_util.check_context_gauge("s", _pct(0.91), LINE_500K)
    assert hook_util.check_context_gauge("s", _pct(0.88), LINE_500K) == ""
    assert hook_util.check_context_gauge("s", _pct(0.91), LINE_500K) == ""
    assert hook_util.take_held_gauge_notice("s") == ""


def test_reset_re_arms_everything():
    # A compaction mid-turn that the context refilled past before any Stop
    # never shows the gauge a drop; the compact SessionStart resets it
    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K)
    hook_util.reset_context_gauge("s")
    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K)


def test_a_fork_inherits_its_parents_bands():
    # A restart, rewind or edited prompt forks the session under a new id
    # with the same context: the band that spoke must not speak again
    assert hook_util.check_context_gauge("parent", _pct(0.92), LINE_500K)
    assert hook_util.check_context_gauge(
        "fork", _pct(0.93), LINE_500K, parents=lambda: ["grandparent", "parent"]
    ) == ""
    assert hook_util.take_held_gauge_notice("fork") == ""
    # From then on the fork has its own record
    assert hook_util.check_context_gauge("fork", _pct(0.94), LINE_500K) == ""


def test_a_fork_takes_the_nearest_ancestor_with_a_record():
    # Priors are oldest first; the parent is last
    assert hook_util.check_context_gauge("grandparent", _pct(0.92), LINE_500K)
    hook_util.reset_context_gauge("parent")  # the parent compacted
    notice = hook_util.check_context_gauge(
        "fork", _pct(0.92), LINE_500K, parents=["grandparent", "parent"]
    )
    # The parent's empty post-compaction record wins, not the grandparent's
    # bands: 90% speaks again in the refilled context
    assert notice


def test_a_fork_leaves_the_parents_held_notice_behind():
    hook_util.check_context_gauge("parent", _pct(0.92), LINE_500K, may_interrupt=False)
    hook_util.check_context_gauge("fork", _pct(0.93), LINE_500K, parents=["parent"])
    assert hook_util.take_held_gauge_notice("fork") == ""
    # It was the parent's turn it described, and it is still the parent's
    assert hook_util.take_held_gauge_notice("parent")


def test_a_rewind_far_back_re_arms_the_inherited_bands():
    assert hook_util.check_context_gauge("parent", _pct(0.92), LINE_500K)
    assert hook_util.check_context_gauge("fork", _pct(0.30), LINE_500K, parents=["parent"]) == ""
    assert hook_util.check_context_gauge("fork", _pct(0.91), LINE_500K)


def test_a_failing_lineage_lookup_starts_fresh():
    def broken():
        raise OSError("desktop records unreadable")

    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K, parents=broken)


def test_unrelated_sessions_are_separate():
    assert hook_util.check_context_gauge("a", _pct(0.92), LINE_500K)
    assert hook_util.check_context_gauge("b", _pct(0.92), LINE_500K, parents=["c"])


def test_silent_without_a_measurement():
    assert hook_util.check_context_gauge("s", None, LINE_500K) == ""
    assert hook_util.check_context_gauge("", _pct(0.92), LINE_500K) == ""


def test_a_corrupt_state_file_starts_over(tmp_path):
    state_dir = tmp_path / "here-i-am-sessions"
    state_dir.mkdir()
    (state_dir / "s-context-gauge.json").write_text("{not json", encoding="utf-8")
    assert hook_util.check_context_gauge("s", _pct(0.92), LINE_500K)


# --- The hooks, end to end: Stop measures, the next prompt prints


def _run(script, stdin_payload, tmp_path, body=None, backend_down=False, extra_env=None, priors=None):
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
        f"hook_util.desktop_prior_session_ids = lambda *a, **k: {list(priors or [])!r}\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        f"import {script}\n"
        f"{script}.main()\n"
    )
    env = {
        **os.environ,
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"],
        **(extra_env or {}),
    }
    for name in ("HIM_DISABLE", hook_util.COMPACT_WINDOW_ENV, "HIM_COMPACT_LINE", "CLAUDE_PROJECT_DIR"):
        if name not in (extra_env or {}):
            env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=30
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
    )


def _stop(tmp_path, tokens, stop_hook_active=False, session_id="gauge-session", **kwargs):
    project = tmp_path / "project"
    _write_settings(project / ".claude", {"autoCompactWindow": 500000}, "settings.local.json")
    path = _transcript(tmp_path, [_assistant(_usage(tokens, 0))])
    payload = {
        "session_id": session_id,
        "transcript_path": path,
        "cwd": str(project),
        "stop_hook_active": stop_hook_active,
    }
    return _run("stop", payload, tmp_path, **kwargs)


def _prompt(tmp_path, prompt="hello"):
    payload = {"session_id": "gauge-session", "prompt": prompt}
    return _run(
        "user_prompt_submit",
        payload,
        tmp_path,
        body={"context": "", "retrieval_status": "ran"},
    )


def test_stop_exits_2_at_the_top_band_with_the_notice_on_stderr(tmp_path):
    code, out, err = _stop(tmp_path, _pct(0.92))
    assert code == 2
    assert "[HERE I AM] Context is at about 92% of the auto-compaction line" in err
    assert "of ~467k tokens" in err  # the room's 500k window, not the model's 1M
    assert out == ""
    # Once per band: the next turn at the same level is quiet
    code, _, err = _stop(tmp_path, _pct(0.93))
    assert code == 0 and err == ""


def test_stop_says_nothing_below_the_band(tmp_path):
    # Issue #373: no 75% notice, not now and not on the next prompt
    code, out, err = _stop(tmp_path, _pct(0.8))
    assert (code, out, err) == (0, "", "")
    _, out, _ = _prompt(tmp_path)
    assert "At the end of your last turn" not in out
    assert "auto-compaction line" not in out


def test_no_second_interrupt_from_a_continuation(tmp_path):
    code, _, err = _stop(tmp_path, _pct(0.92), stop_hook_active=True)
    assert (code, err) == (0, "")
    code, out, _ = _prompt(tmp_path)
    assert code == 0
    assert "[HERE I AM] At the end of your last turn, context was at about 92%" in out
    # Printed once
    _, out, _ = _prompt(tmp_path)
    assert "At the end of your last turn" not in out


def test_the_held_notice_prints_on_a_plumbing_only_prompt(tmp_path):
    _stop(tmp_path, _pct(0.92), stop_hook_active=True)
    _, out, _ = _prompt(tmp_path, "<system-reminder>tick</system-reminder>")
    assert "No automatic retrieval ran" in out
    assert "At the end of your last turn" in out


def test_one_exit_2_carries_a_recording_failure_and_the_gauge(tmp_path):
    code, _, err = _stop(tmp_path, _pct(0.92), backend_down=True)
    assert code == 2
    assert "was NOT recorded" in err
    assert "Context is at about 92%" in err


def test_stop_uses_the_backend_numbers_when_nothing_is_configured(tmp_path):
    path = _transcript(tmp_path, [_assistant(_usage(800_000, 0))])
    payload = {"session_id": "gauge-session", "transcript_path": path, "cwd": str(tmp_path / "bare")}
    code, _, err = _run(
        "stop", payload, tmp_path, body={"compact_window": 900_000, "compact_reserve": 33_000}
    )
    assert code == 2
    assert "(~800k of ~867k tokens)" in err


def test_stop_is_silent_without_usage(tmp_path):
    path = _transcript(tmp_path, [_assistant(None)])
    payload = {"session_id": "gauge-session", "transcript_path": path}
    assert _run("stop", payload, tmp_path) == (0, "", "")


def test_compact_session_start_re_arms(tmp_path):
    assert _stop(tmp_path, _pct(0.92))[0] == 2
    assert _stop(tmp_path, _pct(0.92))[0] == 0
    payload = {"session_id": "gauge-session", "source": "compact"}
    _run("session_start", payload, tmp_path, body={"context": ""})
    assert _stop(tmp_path, _pct(0.92))[0] == 2


def test_a_forked_room_does_not_interrupt_again(tmp_path):
    assert _stop(tmp_path, _pct(0.92))[0] == 2
    code, _, err = _stop(tmp_path, _pct(0.92), session_id="fork-session", priors=["gauge-session"])
    assert (code, err) == (0, "")


def test_stop_is_silent_when_auto_compaction_is_off(tmp_path):
    _write_global_config({"autoCompactEnabled": False})
    assert _stop(tmp_path, _pct(0.95)) == (0, "", "")

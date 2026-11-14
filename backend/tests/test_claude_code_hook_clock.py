"""
The clock on every prompt: the UserPromptSubmit hook opens its output with
the time the prompt arrived, in the human's local time.

Native conversations stamp each human message with its send time. A Claude
Code session had no equivalent: it knew the hour only when it thought to
ask a shell, and on 2026-11-12 one came back from seven dark hours, with
the backend down, and learned how long it had been from the human. So the
hook says it, first, on every path it has — a typed prompt, a wakeup tick,
harness plumbing, an unreachable backend, unreadable input — from its own
machine's clock, asking the backend nothing.

The time is local, with the zone and its offset from UTC, because every
other stamp the entity is shown is UTC. HIM_TIMEZONE names the human's
zone for a session running on another machine; a name that can't be loaded
is said on the line, never silently replaced.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

from tests.hook_clock import PINNED_CLOCK  # noqa: E402

# 2026-11-14 15:03 in New York
NOW = datetime(2026, 11, 14, 20, 3, 41, tzinfo=timezone.utc)
NEW_YORK = (
    "[HERE I AM CLOCK] Saturday 2026-11-14 15:03 EST (UTC-05:00), when this "
    "prompt arrived."
)


def said(when_and_zone: str) -> str:
    return f"[HERE I AM CLOCK] {when_and_zone}, when this prompt arrived."


@pytest.fixture
def zone(monkeypatch):
    def set_zone(name):
        if name is None:
            monkeypatch.delenv("HIM_TIMEZONE", raising=False)
        else:
            monkeypatch.setenv("HIM_TIMEZONE", name)

    return set_zone


# --- clock_line: what the line says


def test_names_the_weekday_date_time_zone_and_offset(zone):
    zone("America/New_York")
    assert hook_util.clock_line(NOW) == NEW_YORK
    # The line the other hook tests pin is a real one
    assert PINNED_CLOCK == NEW_YORK


def test_daylight_time_changes_the_zone_name_and_the_offset(zone):
    zone("America/New_York")
    line = hook_util.clock_line(datetime(2026, 7, 8, 14, 32, tzinfo=timezone.utc))
    assert line == said("Wednesday 2026-07-08 10:32 EDT (UTC-04:00)")


def test_an_evening_prompt_carries_the_local_date_not_utcs(zone):
    # 8:30 PM Saturday in New York is already Sunday in UTC — the roll that
    # put wrong dates in the entity's notes when it read UTC stamps as local
    zone("America/New_York")
    line = hook_util.clock_line(datetime(2026, 11, 15, 1, 30, tzinfo=timezone.utc))
    assert "Saturday 2026-11-14 20:30 EST" in line


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Asia/Kolkata", "Sunday 2026-11-15 01:33 IST (UTC+05:30)"),
        # West of Greenwich on a half hour: the sign belongs to the whole offset
        ("America/St_Johns", "Saturday 2026-11-14 16:33 NST (UTC-03:30)"),
    ],
)
def test_half_hour_offsets(zone, name, expected):
    zone(name)
    assert hook_util.clock_line(NOW) == said(expected)


def test_utc_is_not_given_its_own_offset(zone):
    zone("UTC")
    assert hook_util.clock_line(NOW) == said("Saturday 2026-11-14 20:03 UTC")


def test_unset_uses_the_machines_own_zone(zone):
    zone(None)
    line = hook_util.clock_line(NOW)
    local = NOW.astimezone()
    assert line.startswith(hook_util.CLOCK_PREFIX + local.strftime("%A %Y-%m-%d %H:%M"))
    assert hook_util.utc_offset_label(local) in line or " UTC, when" in line
    assert "could not be loaded" not in line


def test_blank_is_unset(zone):
    zone("   ")
    assert "could not be loaded" not in hook_util.clock_line(NOW)


@pytest.mark.parametrize("name", ["Mars/Phobos", "../etc/passwd", "America/New York"])
def test_a_zone_that_wont_load_is_said_on_the_line(zone, name):
    # Never silently the machine's zone: a wrong zone that looks right is
    # the error the line exists to prevent
    zone(name)
    line = hook_util.clock_line(NOW)
    assert line.startswith(hook_util.CLOCK_PREFIX + NOW.astimezone().strftime("%A %Y-%m-%d %H:%M"))
    assert f'HIM_TIMEZONE="{name}" could not be loaded' in line
    assert "the machine's own timezone" in line
    # Still one line: the tests and the arrive-whole mod both count on it
    assert "\n" not in line


def test_the_line_is_ours_to_the_arrive_whole_mod(zone):
    # The mod rewrites only rows that open with this header
    # (claude-code-mode/mods/arrive-whole/hooks/whole.ts, OURS)
    zone("America/New_York")
    assert hook_util.clock_line(NOW).startswith("[HERE I AM")


def test_default_now_is_the_present(zone):
    zone("UTC")
    line = hook_util.clock_line()
    stamp = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC, when", line).group(1)
    shown = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - shown).total_seconds()) < 120


# --- The hook: the line opens the output on every path


def run_hook(
    stdin_text, body=None, unreachable=False, extra_env=None, tmp_path=None,
    script="user_prompt_submit",
):
    """
    Run a hook's main() (the prompt hook's unless `script` says otherwise)
    with the backend stubbed and the clock held at NOW (the real
    clock_line, so HIM_TIMEZONE still decides the zone and the hook still
    chooses what the time is the time of). Returns (stdout, whether the
    backend was called).
    """
    stub_tail = (
        "    import urllib.error\n"
        "    raise urllib.error.URLError(ConnectionRefusedError('refused'))\n"
        if unreachable
        else "    return body\n"
    )
    code = (
        "import io, json, sys\n"
        "from datetime import datetime, timezone\n"
        "import hook_util\n"
        "real = hook_util.clock_line\n"
        "hook_util.clock_line = lambda now=None, event=hook_util.CLOCK_AT_PROMPT: "
        f"real(datetime.fromisoformat({NOW.isoformat()!r}), event)\n"
        f"body = json.loads({json.dumps(json.dumps(body if body is not None else {'context': ''}))})\n"
        "def stub(path, payload, timeout=30):\n"
        "    sys.stderr.write('BACKEND-CALLED')\n"
        + stub_tail
        + "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(stdin_text)})\n"
        f"import {script}\n"
        f"{script}.main()\n"
    )
    env = {**os.environ, "HIM_TIMEZONE": "America/New_York", **(extra_env or {})}
    if "HIM_DISABLE" not in (extra_env or {}):
        env.pop("HIM_DISABLE", None)
    if "HIM_INLINE_BUDGET" not in (extra_env or {}):
        env.pop("HIM_INLINE_BUDGET", None)
    if tmp_path is not None:
        env.update({"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)})
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=30
    )
    stderr = result.stderr.decode("utf-8", "replace")
    assert result.returncode == 0, stderr
    return result.stdout.decode("utf-8").replace("\r\n", "\n"), "BACKEND-CALLED" in stderr


def prompt(text: str) -> str:
    return json.dumps({"session_id": "clock-test", "prompt": text})


def test_typed_prompt_opens_with_the_time_then_the_memories():
    out, called = run_hook(
        prompt("hello"), body={"context": "[MEMORY] something", "retrieval_status": "ran"}
    )
    assert called
    assert out.startswith(NEW_YORK + "\n\n[MEMORY] something")
    assert out.count(hook_util.CLOCK_PREFIX) == 1


def test_wakeup_tick_gets_the_time():
    # Most of a loop session's turns are ticks; the tick is where the hour
    # is least known
    out, _ = run_hook(prompt("[WAKEUP] continue"), body={"context": "", "retrieval_status": "skipped"})
    assert out.startswith(NEW_YORK + "\n\n[HERE I AM] No automatic retrieval ran")
    assert "(wakeup tick)" in out


def test_harness_plumbing_gets_the_time_without_calling_the_backend():
    out, called = run_hook(prompt("<task-notification>done</task-notification>"))
    assert not called
    assert out.startswith(NEW_YORK + "\n\n[HERE I AM] No automatic retrieval ran")


def test_unreachable_backend_still_gets_the_time():
    # The case the line was asked for: the server down, and the hour unknown
    out, _ = run_hook(prompt("hello"), unreachable=True)
    assert out.startswith(NEW_YORK + "\n\n[HERE I AM] The Here I Am backend was unreachable")


def test_unreadable_input_still_gets_the_time():
    out, called = run_hook("this is not json")
    assert not called
    assert out.startswith(NEW_YORK + "\n\n[HERE I AM] The UserPromptSubmit hook received unreadable input")


def test_disabled_hook_says_nothing_at_all():
    out, called = run_hook(prompt("hello"), extra_env={"HIM_DISABLE": "1"})
    assert out == "" and not called


def test_a_zone_that_wont_load_reaches_the_session():
    out, _ = run_hook(prompt("hello"), extra_env={"HIM_TIMEZONE": "Mars/Phobos"})
    first = out.splitlines()[0]
    assert first.startswith(hook_util.CLOCK_PREFIX)
    assert 'HIM_TIMEZONE="Mars/Phobos" could not be loaded' in first


def test_the_line_counts_against_the_stdout_budget(tmp_path):
    # The harness measures the whole stdout, clock line included. A long
    # line (here the unloadable-zone one) and a block sized to the budget
    # must still come in under it together.
    items = [
        {
            "text": f"[MEMORY {i:08d} from 2026-09-01 - originally from you - via Claude Code]\n"
            + ("m" * 2250)
            + "\n[/MEMORY]",
            "summary": f"- {i:08d} (2026-09-01): snippet",
        }
        for i in range(4)
    ]
    header = "[HERE I AM MEMORY RETRIEVAL] Memories that surfaced:"
    body = {
        "context": header + "\n\n" + "\n\n".join(item["text"] for item in items),
        "context_header": header,
        "context_items": items,
        "retrieval_status": "ran",
    }
    budget = hook_util.DEFAULT_INLINE_BUDGET
    plain, _ = run_hook(prompt("hello"), body=body, tmp_path=tmp_path)
    long_line, _ = run_hook(
        prompt("hello"), body=body, tmp_path=tmp_path, extra_env={"HIM_TIMEZONE": "Mars/Phobos"}
    )
    # The block alone fits; whether it still does depends on the line's size
    assert hook_util.output_chars(body["context"]) <= budget
    for out in (plain, long_line):
        assert hook_util.output_chars(out) <= budget
    assert len(long_line.splitlines()[0]) > len(plain.splitlines()[0])


# --- SessionStart: the line opens the block after a compaction, and only then
#
# An auto-compaction lands in the middle of a turn, and the turn goes on
# with no prompt: the last clock line went with the context, and no prompt
# hook runs until the human next writes. So the compaction's own SessionStart
# says the time. A new session or a resume doesn't: its first prompt does.

COMPACTED = (
    "[HERE I AM CLOCK] Saturday 2026-11-14 15:03 EST (UTC-05:00), when this "
    "context was compacted."
)


def session_start(source: str, session_id: str = "clock-test") -> str:
    return json.dumps({"session_id": session_id, "source": source, "cwd": "."})


def run_session_start(source, body=None, **kwargs):
    return run_hook(session_start(source), body=body, script="session_start", **kwargs)


def test_the_line_says_what_its_time_is_the_time_of(zone):
    zone("America/New_York")
    assert hook_util.clock_line(NOW, event=hook_util.CLOCK_AT_COMPACTION) == COMPACTED
    assert hook_util.clock_line(NOW, event=hook_util.CLOCK_AT_PROMPT) == NEW_YORK


def test_post_compaction_block_opens_with_the_time():
    out, called = run_session_start(
        "compact", body={"context": "[HERE I AM] This session's context was just compacted."}
    )
    assert called
    assert out.startswith(COMPACTED + "\n\n[HERE I AM] This session's context was just compacted.")
    assert out.count(hook_util.CLOCK_PREFIX) == 1


@pytest.mark.parametrize("source", ["startup", "resume", "clear"])
def test_other_session_starts_carry_no_clock_line(source):
    # The first prompt's line follows at once; a second one here would be noise
    out, _ = run_session_start(source, body={"context": "[HERE I AM] You are Test Entity."})
    assert out.startswith("[HERE I AM] You are Test Entity.")
    assert hook_util.CLOCK_PREFIX not in out


def test_a_resume_that_prints_nothing_still_prints_nothing():
    out, _ = run_session_start("resume", body={"context": ""})
    assert out == ""


def test_compaction_with_the_backend_down_still_gets_the_time():
    out, _ = run_session_start("compact", unreachable=True)
    assert out.startswith(COMPACTED + "\n\n[HERE I AM] The Here I Am backend was unreachable")


def test_disabled_session_start_says_nothing_at_all():
    out, called = run_session_start("compact", extra_env={"HIM_DISABLE": "1"})
    assert out == "" and not called


def test_a_zone_that_wont_load_is_said_after_a_compaction_too():
    out, _ = run_session_start(
        "compact", body={"context": "[HERE I AM] compacted"},
        extra_env={"HIM_TIMEZONE": "Mars/Phobos"},
    )
    first = out.splitlines()[0]
    assert first.startswith(hook_util.CLOCK_PREFIX)
    assert 'HIM_TIMEZONE="Mars/Phobos" could not be loaded' in first
    assert "when this context was compacted" in first


def test_the_line_counts_against_the_post_compaction_budget(tmp_path):
    # The block plus its bulk sized to land exactly on the budget without
    # the line: with the line it is over, so the bulk has to go to a file
    # and what is printed still comes in under the line the harness draws
    budget = hook_util.DEFAULT_INLINE_BUDGET
    context = "[HERE I AM] compacted"
    trailer = "[ROOMS REGISTRY] this session is the porch"
    room = budget - hook_util.output_chars(f"{context}\n\n\n\n{trailer}")
    bulk = "i" * room
    body = {
        "context": context,
        "bulk_context": bulk,
        "bulk_parts": [{"name": "notes-index", "text": bulk}],
        "rooms_notice": trailer,
        "created": False,
    }
    assert hook_util.output_chars(f"{context}\n\n{bulk}\n\n{trailer}") == budget
    # A new session prints that whole: it is exactly on the line, with no clock
    started, _ = run_session_start("startup", body=body, tmp_path=tmp_path)
    assert bulk in started
    compacted, _ = run_session_start("compact", body=body, tmp_path=tmp_path)
    assert compacted.startswith(COMPACTED + "\n\n" + context)
    assert bulk not in compacted and "post-compact-notes-index.md" in compacted
    assert hook_util.output_chars(compacted) <= budget


# --- Two clocks: this machine's zone against the server's (PR #410 review,
# --- finding 1)
#
# The clock line is the hook machine's time; every stored time the entity is
# shown, and every offset-less time the memory tools read back, is the
# server's. On one machine they agree by construction. With the server on a
# VM they differ unless both knobs are set, and a time copied off the clock
# line is then read hours off with nothing said. The session-start response
# carries the server's offsets, and the hook says so while they differ.


def server_clock(zone_name: str, *minutes: int) -> dict:
    instants = ["2026-11-14T20:03:41+00:00", "2026-01-15T12:00:00+00:00", "2026-07-15T12:00:00+00:00"]
    return {
        "zone": zone_name,
        "offsets": [{"at": at, "minutes": m} for at, m in zip(instants, minutes, strict=True)],
    }


NEW_YORK_CLOCK = server_clock("EST", -300, -300, -240)
UTC_CLOCK = server_clock("UTC", 0, 0, 0)


def test_the_same_clock_says_nothing(zone):
    zone("America/New_York")
    assert hook_util.clock_check_lines({"server_clock": NEW_YORK_CLOCK}) == []
    # The name is not what is compared: Windows spells this zone out
    spelled = dict(NEW_YORK_CLOCK, zone="Eastern Standard Time")
    assert hook_util.clock_check_lines({"server_clock": spelled}) == []


def test_a_server_in_another_zone_is_said_with_the_gap_and_the_way_round_it(zone):
    # The VM case: the server's clock is UTC and nobody set LOCAL_TIMEZONE
    zone("America/New_York")
    (line,) = hook_util.clock_check_lines({"server_clock": UTC_CLOCK})
    assert line.startswith("[HERE I AM] TWO CLOCKS: this machine and the Here I Am server")
    assert "5 hours apart" in line
    assert "this machine's, UTC-05:00" in line
    assert "the server's, UTC (UTC+00:00)" in line
    assert "memory_read, memory_find or memory_query without an offset" in line
    # A time with its offset written out is right whichever zone reads it
    assert "2026-11-14T15:03-05:00" in line
    assert "HIM_TIMEZONE" in line and "LOCAL_TIMEZONE" in line
    assert "\n" not in line


def test_a_half_hour_gap(zone):
    zone("Asia/Kolkata")
    (line,) = hook_util.clock_check_lines({"server_clock": UTC_CLOCK})
    assert "5 hours 30 minutes apart" in line


def test_zones_that_agree_today_but_not_all_year(zone):
    # Bogota keeps UTC-05:00 all year; New York leaves it in March. In
    # November the two read the same, and a July day would be an hour out
    zone("America/Bogota")
    (line,) = hook_util.clock_check_lines({"server_clock": NEW_YORK_CLOCK})
    assert "agree today but not all year" in line
    assert "On 2026-07-15 this machine is at UTC-05:00 and the server at UTC-04:00" in line
    assert "1 hour apart" in line and "1 hour off" in line


def test_no_report_or_an_unreadable_one_says_nothing(zone):
    zone("America/New_York")
    assert hook_util.clock_check_lines({}) == []
    assert hook_util.clock_check_lines({"server_clock": None}) == []
    assert hook_util.clock_check_lines({"server_clock": "UTC"}) == []
    assert hook_util.clock_check_lines({"server_clock": {"zone": "UTC", "offsets": []}}) == []
    junk = {"zone": "UTC", "offsets": [{"at": "sometime", "minutes": 0}, {"minutes": 0}, "x"]}
    assert hook_util.clock_check_lines({"server_clock": junk}) == []


@pytest.mark.parametrize("source", ["startup", "resume", "compact"])
def test_every_session_start_says_it_while_they_differ(source):
    out, _ = run_session_start(source, body={"context": "", "server_clock": UTC_CLOCK})
    assert out.count("[HERE I AM] TWO CLOCKS:") == 1
    assert "5 hours apart" in out
    # A resume prints nothing else at all
    if source == "resume":
        assert out.startswith("[HERE I AM] TWO CLOCKS:")


def test_a_session_start_on_the_same_clock_prints_no_notice():
    out, _ = run_session_start(
        "startup", body={"context": "[HERE I AM] You are Test Entity.", "server_clock": NEW_YORK_CLOCK}
    )
    assert "TWO CLOCKS" not in out
    out, _ = run_session_start("resume", body={"context": "", "server_clock": NEW_YORK_CLOCK})
    assert out == ""

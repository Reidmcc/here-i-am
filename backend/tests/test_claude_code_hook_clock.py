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


def run_hook(stdin_text, body=None, unreachable=False, extra_env=None, tmp_path=None):
    """
    Run user_prompt_submit.main() with the backend stubbed and the clock
    held at NOW (the real clock_line, so HIM_TIMEZONE still decides the
    zone). Returns (stdout, whether the backend was called).
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
        f"hook_util.clock_line = lambda now=None: real(datetime.fromisoformat({NOW.isoformat()!r}))\n"
        f"body = json.loads({json.dumps(json.dumps(body if body is not None else {'context': ''}))})\n"
        "def stub(path, payload, timeout=30):\n"
        "    sys.stderr.write('BACKEND-CALLED')\n"
        + stub_tail
        + "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(stdin_text)})\n"
        "import user_prompt_submit\n"
        "user_prompt_submit.main()\n"
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

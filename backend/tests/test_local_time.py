"""
The human's local time in native conversations (services/local_time.py).

The stamp on human messages has been in the server's OS timezone since it
was introduced, which is the human's timezone only while the server runs
on the human's machine. LOCAL_TIMEZONE names the zone, so the stamp stays
the human's when the server moves (a cloud VM's clock is usually UTC), and
the per-turn [DATE CONTEXT] dates, which were UTC, now follow the same
zone: a UTC date read a day ahead of the stamp beside it every evening in
the Americas.

These tests set the zone through the setting, so unlike the TZ-env fixture
in test_session_helpers.py they run on Windows too.
"""
import logging
from datetime import datetime, timezone

import pytest

from app.config import Settings, settings

# Imported here, not inside the test: importing app.main configures logging,
# which would replace caplog's handler mid-test
from app.main import warn_unloadable_local_timezone
from app.services import local_time
from app.services.anthropic_service import build_date_context
from app.services.session_helpers import stamp_human_message


@pytest.fixture
def zone(monkeypatch):
    def set_zone(name):
        monkeypatch.setattr(settings, "local_timezone", name)
        monkeypatch.setattr(local_time, "_zones", {})

    return set_zone


# --- to_local


def test_unset_by_default():
    assert Settings.model_fields["local_timezone"].default == ""


def test_naive_datetimes_are_utc(zone):
    zone("America/New_York")
    local = local_time.to_local(datetime(2026, 11, 14, 20, 3))
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 11, 14, 15, 3)
    assert local.tzname() == "EST"


def test_aware_datetimes_keep_their_moment(zone):
    zone("America/New_York")
    moment = datetime(2026, 7, 8, 14, 32, tzinfo=timezone.utc)
    local = local_time.to_local(moment)
    assert local == moment
    assert local.strftime("%H:%M %Z") == "10:32 EDT"


def test_unset_is_the_servers_own_zone(zone):
    zone("")
    moment = datetime(2026, 11, 14, 20, 3, tzinfo=timezone.utc)
    assert local_time.to_local(moment).utcoffset() == moment.astimezone().utcoffset()
    assert local_time.to_local(moment).tzname() == moment.astimezone().tzname()


def test_a_name_that_wont_load_falls_back_and_is_warned_about_once(zone, caplog):
    zone("Mars/Phobos")
    moment = datetime(2026, 11, 14, 20, 3, tzinfo=timezone.utc)
    with caplog.at_level(logging.WARNING, logger="app.services.local_time"):
        first = local_time.to_local(moment)
        local_time.to_local(moment)
    assert first.utcoffset() == moment.astimezone().utcoffset()
    warnings = [r.getMessage() for r in caplog.records if "LOCAL_TIMEZONE" in r.getMessage()]
    assert len(warnings) == 1
    assert "'Mars/Phobos' could not be loaded" in warnings[0]
    assert "America/New_York" in warnings[0]


@pytest.mark.parametrize("name", ["Mars/Phobos", "../etc/passwd", "America/New York"])
def test_problem_names_the_setting_and_the_value(name):
    problem = local_time.local_timezone_problem(name)
    assert problem.startswith(f"LOCAL_TIMEZONE={name!r} could not be loaded")


def test_no_problem_with_a_real_zone():
    assert local_time.local_timezone_problem("Europe/Berlin") is None


def test_startup_warns_about_an_unloadable_zone(zone, caplog):
    zone("Mars/Phobos")
    with caplog.at_level(logging.WARNING):
        warn_unloadable_local_timezone()
    assert any("LOCAL_TIMEZONE='Mars/Phobos'" in r.getMessage() for r in caplog.records)
    caplog.clear()
    for quiet in ("America/New_York", ""):
        zone(quiet)
        with caplog.at_level(logging.WARNING):
            warn_unloadable_local_timezone()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_startup_and_the_first_stamp_warn_once_between_them(zone, caplog):
    # The startup check resolves the zone, which is what warns and what
    # keeps the answer; it used to check on the side, and the first render
    # then said it all again (PR #410 review, finding 3)
    zone("Mars/Phobos")
    with caplog.at_level(logging.WARNING):
        warn_unloadable_local_timezone()
        stamp_human_message("Hello", datetime(2026, 7, 8, 14, 32))
        local_time.local_stamp("2026-07-08T14:32:00")
    warnings = [r for r in caplog.records if "LOCAL_TIMEZONE" in r.getMessage()]
    assert len(warnings) == 1


# --- clock_report: the server's zone, for the hook to compare with its own


def test_clock_report_gives_offsets_now_and_either_side_of_the_year(zone):
    zone("America/New_York")
    report = local_time.clock_report(datetime(2026, 11, 14, 20, 3, 41, 500, tzinfo=timezone.utc))
    assert report == {
        "zone": "EST",
        "offsets": [
            {"at": "2026-11-14T20:03:41+00:00", "minutes": -300},
            {"at": "2026-01-15T12:00:00+00:00", "minutes": -300},
            {"at": "2026-07-15T12:00:00+00:00", "minutes": -240},
        ],
    }


def test_clock_report_in_utc_and_on_a_half_hour(zone):
    zone("UTC")
    assert {o["minutes"] for o in local_time.clock_report()["offsets"]} == {0}
    zone("Asia/Kolkata")
    report = local_time.clock_report()
    assert report["zone"] == "IST"
    assert {o["minutes"] for o in report["offsets"]} == {330}


def test_clock_report_with_no_zone_named_is_the_servers_own(zone):
    zone("")
    now = datetime(2026, 11, 14, 20, 3, tzinfo=timezone.utc)
    report = local_time.clock_report(now)
    assert report["offsets"][0]["minutes"] == now.astimezone().utcoffset().total_seconds() // 60


# --- The stamp on human messages


def test_stamp_is_in_the_named_zone(zone):
    zone("America/Chicago")
    # Naive = UTC, as Message.created_at is stored
    assert stamp_human_message("Hello", datetime(2026, 7, 8, 14, 32, 45)) == (
        "[2026-07-08 09:32 CDT] Hello"
    )


def test_stamp_carries_the_local_date_in_the_evening(zone):
    # 01:30 UTC on the 15th is still the 14th in New York
    zone("America/New_York")
    assert stamp_human_message("Hello", datetime(2026, 11, 15, 1, 30)) == (
        "[2026-11-14 20:30 EST] Hello"
    )


def test_stamp_re_renders_identically(zone):
    # The reload contract: a message's prefix is rebuilt from its
    # created_at on every session reload and has to match what was cached
    zone("America/New_York")
    sent = datetime(2026, 3, 8, 6, 59, 59)  # one second before DST starts
    assert stamp_human_message("x", sent) == stamp_human_message("x", sent)
    assert stamp_human_message("x", sent) == "[2026-03-08 01:59 EST] x"
    assert stamp_human_message("x", datetime(2026, 3, 8, 7, 0)) == "[2026-03-08 03:00 EDT] x"


# --- [DATE CONTEXT]


def test_current_date_is_local_and_says_its_zone(zone):
    zone("America/New_York")
    block = build_date_context(now=datetime(2026, 11, 15, 1, 30, tzinfo=timezone.utc))
    assert block == (
        "[DATE CONTEXT]\n"
        "Current date: 2026-11-14 (EST)\n"
        "[/DATE CONTEXT]"
    )


def test_conversation_start_date_is_local_too(zone):
    zone("America/New_York")
    block = build_date_context(
        conversation_start_date=datetime(2026, 11, 13, 2, 15),  # naive UTC: the 12th, 9:15 PM
        now=datetime(2026, 11, 14, 20, 3, tzinfo=timezone.utc),
    )
    assert block.splitlines() == [
        "[DATE CONTEXT]",
        "This conversation started: 2026-11-12",
        "Current date: 2026-11-14 (EST)",
        "[/DATE CONTEXT]",
    ]


def test_date_context_agrees_with_the_message_stamp(zone):
    # The two used to disagree every evening: a UTC date above a local stamp
    zone("America/Los_Angeles")
    sent = datetime(2026, 11, 15, 3, 0)
    stamp = stamp_human_message("hi", sent)
    block = build_date_context(now=sent.replace(tzinfo=timezone.utc))
    assert stamp.startswith("[2026-11-14 19:00 PST]")
    assert "Current date: 2026-11-14 (PST)" in block


def test_default_now_is_today(zone):
    zone("UTC")
    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    block = build_date_context()
    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Either side of midnight, should the test run across it
    assert any(f"Current date: {day} (UTC)" in block for day in (before, after))

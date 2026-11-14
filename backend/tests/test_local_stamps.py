"""
Stored times, shown in the human's local time (services/local_time.py).

The clock the entity is told "now" by went local first (test_local_time.py,
test_claude_code_hook_clock.py). These are the times it is told "then" by:
memory markers, link markers, the researcher-change notices, the archive
readers' cursor, the retrieval summary line, the compaction talk's header,
the rooms file. All of them were UTC, most of them unlabeled, next to a
local clock, and a date read off one and compared with the other was a day
out every evening in the Americas.

The rest of the suite runs with LOCAL_TIMEZONE pinned to UTC (conftest), so
these set the zone they are about. The readers' own tests are in
test_memory_read.py and test_memory_find.py.
"""
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services import compact_talk, local_time, rooms_registry
from app.services.claude_code_mode import render_retrieval_summary_line
from app.services.memory_context import (
    format_archive_change_notice,
    format_memory_as_context_message,
    format_memory_link_lines,
    format_status_change_notice,
)
from app.services.memory_service import memory_service
from app.services.memory_tools import _parse_since


@pytest.fixture
def zone(monkeypatch):
    def set_zone(name):
        monkeypatch.setattr(settings, "local_timezone", name)
        monkeypatch.setattr(local_time, "_zones", {})

    return set_zone


def marker(created_at: str) -> str:
    message = format_memory_as_context_message("abcdef1234", "Hello.", created_at, "human")
    return message["content"].splitlines()[0]


# --- The shared shapes


def test_local_stamp_and_date(zone):
    zone("America/New_York")
    # A stored time is naive UTC, as a datetime or as its ISO string
    assert local_time.local_stamp(datetime(2026, 11, 14, 20, 3, 59)) == "2026-11-14 15:03 EST"
    assert local_time.local_stamp("2026-11-14T20:03:59.123456") == "2026-11-14 15:03 EST"
    assert local_time.local_stamp("2026-11-14T20:03:59", seconds=True) == "2026-11-14 15:03:59 EST"
    assert local_time.local_date("2026-11-15T01:30:00") == "2026-11-14"
    # An aware one keeps its moment
    assert local_time.local_stamp(datetime(2026, 7, 8, 14, 32, tzinfo=timezone.utc)) == (
        "2026-07-08 10:32 EDT"
    )


def test_a_value_that_is_not_a_time_is_shown_as_given(zone):
    zone("America/New_York")
    assert local_time.local_stamp("not a time") == "not a time"
    assert local_time.local_stamp(None) == ""
    assert local_time.local_date("") == ""


def test_read_in_zone_is_the_inverse_for_input(zone):
    new_york = local_time.ZoneInfo("America/New_York")
    read = local_time.read_in_zone(datetime(2026, 11, 14, 15, 3), new_york)
    assert read.astimezone(timezone.utc) == datetime(2026, 11, 14, 20, 3, tzinfo=timezone.utc)
    # No zone = the server's own, which is what an unset LOCAL_TIMEZONE means
    naive = datetime(2026, 11, 14, 15, 3)
    assert local_time.read_in_zone(naive, None) == naive.astimezone()


# --- [MEMORY] markers


def test_marker_time_is_local_with_its_zone(zone):
    zone("America/New_York")
    assert marker("2026-08-24T20:29:42.953550") == (
        "[MEMORY abcdef12 from 2026-08-24 16:29 EDT - originally from human - via Here I Am]"
    )


def test_marker_carries_the_local_date_in_the_evening(zone):
    # 01:30 UTC on the 15th is still the 14th in New York: the date the
    # human's own stamp carried when this was said
    zone("America/New_York")
    assert " from 2026-11-14 20:30 EST - " in marker("2026-11-15T01:30:00")


def test_marker_re_renders_identically(zone):
    # The reload contract: a native marker is rebuilt from the row's
    # created_at on every session reload and has to match what was cached.
    # The offset is the one in force when the memory was made, not today's.
    zone("America/New_York")
    assert marker("2026-03-08T06:59:59") == marker("2026-03-08T06:59:59")
    assert " from 2026-03-08 01:59 EST - " in marker("2026-03-08T06:59:59")
    assert " from 2026-03-08 03:00 EDT - " in marker("2026-03-08T07:00:00")


def test_marker_follows_the_setting(zone):
    zone("UTC")
    assert " from 2026-08-24 20:29 UTC - " in marker("2026-08-24T20:29:42.953550")
    zone("Asia/Kolkata")
    assert " from 2026-08-25 01:59 IST - " in marker("2026-08-24T20:29:42.953550")


def test_marker_with_an_unreadable_time_still_renders():
    assert marker("sometime") == (
        "[MEMORY abcdef12 from sometime - originally from human - via Here I Am]"
    )


# --- Link markers


def test_link_marker_dates_are_local(zone):
    zone("America/New_York")
    links = {
        "revises": [{"id": "9f8e7d6c00", "created_at": "2026-07-15T02:00:00", "role": "assistant"}],
        "cites": [{"id": "3f2a9c1d00", "created_at": "2026-08-03T16:00:00", "role": "human"}],
        "revised_by": [{"id": "a1b2c3d400", "created_at": "2026-10-03T01:00:00", "role": "reflection"}],
    }
    assert format_memory_link_lines(links, "reflection") == [
        "[revises 9f8e7d6c (2026-07-14, you)]",
        "[sources: 3f2a9c1d (2026-08-03, human)]",
        "[later revised → a1b2c3d4 (2026-10-02)]",
    ]


def test_retrieval_summary_line_date_is_local(zone):
    zone("America/New_York")
    line = render_retrieval_summary_line({
        "id": "dddd4444aaaa", "created_at": "2026-09-02T02:00:00", "role": "human",
        "content": "The spilled one.", "source": "native",
    })
    assert line == "- dddd4444 (2026-09-01 - originally from human - via Here I Am): The spilled one."


# --- The researcher-change notices


def test_status_notice_time_is_local(zone):
    zone("America/New_York")
    notice = format_status_change_notice([{
        "id": "1a2b3c4d00", "role": "assistant", "memory_status": "released",
        "status_set_at": "2026-11-14T20:03:00", "content": "A claim.",
    }])
    assert '- 1a2b3c4d (originally from you): now released on 2026-11-14 15:03 EST: "A claim."' in notice
    assert "UTC" not in notice


def test_archive_notice_dates_and_time_are_local(zone):
    zone("America/New_York")
    notice = format_archive_change_notice([{
        # 9 to 10 PM on the 21st, Eastern: one local day, two UTC dates
        "first_message_at": "2026-09-22T01:00:00",
        "last_message_at": "2026-09-22T02:00:00",
        "message_count": 4, "source": "claude_code", "is_archived": True,
        "archive_changed_at": datetime(2026, 9, 25, 0, 0),
    }, {
        "first_message_at": datetime(2026, 9, 21, 20, 0),
        "last_message_at": datetime(2026, 9, 22, 5, 0),
        "message_count": 2, "source": "native", "is_archived": False,
        "archive_changed_at": None,
    }])
    assert (
        "- A conversation on 2026-09-21 (4 messages, via Claude Code) was withdrawn "
        "from your memory by the researcher on 2026-09-24 20:00 EDT."
    ) in notice
    assert "- A conversation from 2026-09-21 to 2026-09-22 (2 messages, via Here I Am)" in notice
    assert "UTC" not in notice


# --- The readers' cursor


def test_cursor_reads_as_local_time_and_decodes_to_the_same_row(zone):
    zone("America/New_York")
    created = datetime(2026, 11, 14, 20, 1, 56, 123456)
    cursor = memory_service.encode_read_cursor(created, "row-1", backward=True, page=3)
    assert cursor == "2026-11-14T15:01:56.123456-05:00|row-1|backward|page=3"
    decoded = memory_service.decode_read_cursor(cursor)
    assert (decoded.created_at, decoded.message_id, decoded.backward, decoded.page) == (
        created, "row-1", True, 3
    )


@pytest.mark.parametrize("name", ["", "UTC", "Asia/Kolkata", "America/St_Johns"])
def test_cursor_round_trips_in_any_zone(zone, name):
    zone(name)
    # Either side of a clock change, to the microsecond the keyset needs
    for created in (datetime(2026, 3, 8, 6, 59, 59, 999999), datetime(2026, 11, 1, 5, 30, 0, 1)):
        cursor = memory_service.encode_read_cursor(created, "row-1")
        assert memory_service.decode_read_cursor(cursor).created_at == created


def test_a_cursor_with_no_offset_is_still_utc(zone):
    # One made before cursors were local, mid-walk when the server updated
    zone("America/New_York")
    decoded = memory_service.decode_read_cursor("2026-11-14T20:01:56.123456|row-1|page=2")
    assert decoded.created_at == datetime(2026, 11, 14, 20, 1, 56, 123456)


# --- memory_query's `since`


def test_since_without_an_offset_is_local(zone):
    zone("America/New_York")
    assert _parse_since("2026-08-24T18:00:00") == (datetime(2026, 8, 24, 22, 0), None)
    assert _parse_since("2026-08-24") == (datetime(2026, 8, 24, 4, 0), None)
    # With one, it is taken as given
    assert _parse_since("2026-08-24T18:00:00+00:00") == (datetime(2026, 8, 24, 18, 0), None)
    assert _parse_since("2026-08-24T18:00:00-07:00") == (datetime(2026, 8, 25, 1, 0), None)


def test_since_that_cannot_be_read_says_how(zone):
    zone("America/New_York")
    parsed, error = _parse_since("last Tuesday")
    assert parsed is None
    assert "local time" in error and "+00:00" in error


# --- The compaction talk's header and the rooms file


def test_compact_talk_header_stamp_is_local(zone):
    zone("America/New_York")
    assert compact_talk._stamp("2026-11-14T20:01:56.123456") == "2026-11-14 15:01 EST"


def test_rooms_file_times_are_local(zone):
    zone("America/New_York")
    assert rooms_registry._render_local("2026-09-03T03:00:00+00:00") == "2026-09-02 23:00 EDT"
    assert rooms_registry._render_local(None) == "—"


# --- A reader's tz reaches the link dates on its rows (PR #410 review, finding 2)


def test_a_readers_zone_covers_the_stamp_and_the_link_dates(zone):
    from app.services.memory_tools import _format_archive_item

    zone("America/New_York")
    item = {
        "id": "0123456789abcdef", "role": "reflection", "content": "A second look.",
        "created_at": "2026-11-14T20:01:56", "source": "native",
        "conversation_id": "conv-1234", "conversation_title": "Porch",
        # 9 PM on the 13th in New York; already the 14th in Tokyo
        "links": {"revises": [
            {"id": "abcd1234ffff", "created_at": "2026-11-14T02:00:00", "role": "assistant"},
        ]},
    }
    now = datetime(2026, 11, 15)
    tokyo = local_time.ZoneInfo("Asia/Tokyo")

    local = _format_archive_item(item, None, {}, local_time.configured_zone(), False, now)[0]
    assert "2026-11-14 15:01:56 EST" in local
    assert "[revises abcd1234 (2026-11-13, you)]" in local

    asked = _format_archive_item(item, None, {}, tokyo, False, now)[0]
    assert "2026-11-15 05:01:56 JST" in asked
    assert "[revises abcd1234 (2026-11-14, you)]" in asked

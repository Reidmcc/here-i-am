"""
The human's local time, for every time the entity is shown.

Every time the server renders for the entity goes through to_local: "now"
and "when this was sent" (the timestamp prefix on human messages,
session_helpers.stamp_human_message; the dates in the per-turn
[DATE CONTEXT] block) and "when this was said" (memory markers, link
markers, the archive readers, the researcher-change notices, the
compaction talk). One clock, so a time read in one place can be compared
with a time read in any other without converting. Stored timestamps stay
naive UTC; this is rendering only, and the tools' time arguments are read
the same way they are printed (read_in_zone).

"Local" means the human's zone, which is the server's own only while the
server runs on the human's machine. LOCAL_TIMEZONE names it (an IANA name
such as America/New_York), so a server on a cloud VM, whose OS clock is
usually UTC, still tells the entity the time where the human is. Unset, the
server's OS zone is used, which is what the stamp did before the setting
existed.

The Claude Code hooks have their own knob for the same thing
(HIM_TIMEZONE, read by hook_util.clock_line): they run on the session's
machine, and have to tell the time when this server can't be reached.
"""
import logging
from datetime import datetime, timezone, tzinfo
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)

# A zone to show times in: a tzinfo, or None for the server's OS zone
Zone = Optional[tzinfo]

# name -> zone, or None for a name that didn't load (warned about once)
_zones: Dict[str, Zone] = {}


def local_timezone_problem(name: str) -> Optional[str]:
    """Why LOCAL_TIMEZONE's value can't be used, or None when it loads."""
    try:
        ZoneInfo(name)
    except Exception as e:  # unknown key, malformed key, no tz database
        return (
            f"LOCAL_TIMEZONE={name!r} could not be loaded "
            f"({e.__class__.__name__}: {e}); times shown to the entity use "
            "the server's own timezone instead. Use an IANA name such as "
            "America/New_York."
        )
    return None


def configured_zone() -> Optional[tzinfo]:
    """
    The zone LOCAL_TIMEZONE names, or None for the server's OS zone (unset,
    or a name that didn't load, which is logged once).
    """
    name = (settings.local_timezone or "").strip()
    if not name:
        return None
    if name not in _zones:
        problem = local_timezone_problem(name)
        if problem:
            logger.warning(problem)
        _zones[name] = None if problem else ZoneInfo(name)
    return _zones[name]


def to_local(moment: datetime) -> datetime:
    """
    A moment in the human's local time, zone attached. Naive datetimes are
    UTC, matching Message.created_at and the rest of the database.

    Deterministic for a given moment and setting, which the reload contract
    needs: a human message's stamp is re-rendered from its created_at on
    every session reload and has to come out byte-identical.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(configured_zone())


def local_now() -> datetime:
    return to_local(datetime.now(timezone.utc))


def in_zone(moment: datetime, zone: Optional[tzinfo]) -> datetime:
    """A moment in `zone` (None = the server's OS zone); naive is UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone)


def read_in_zone(naive: datetime, zone: Optional[tzinfo]) -> datetime:
    """
    A wall-clock time the entity wrote without an offset, as the moment it
    names in `zone` (None = the server's OS zone). The inverse of in_zone
    for input: a date or time read off a local stamp and passed back to a
    tool means that local time.
    """
    if zone is None:
        return naive.astimezone()
    return naive.replace(tzinfo=zone)


def as_moment(value: Any) -> Optional[datetime]:
    """A stored timestamp (a datetime, or its ISO string) as a datetime;
    None when there is none or it can't be parsed."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# The shapes a stored time is shown to the entity in. One vocabulary, so a
# time read in one place means the same thing in every other: the human's
# local time, with its zone wherever there is a time of day.
STAMP_FORMAT = "%Y-%m-%d %H:%M %Z"
STAMP_FORMAT_SECONDS = "%Y-%m-%d %H:%M:%S %Z"


def format_stamp(local: datetime, seconds: bool = False) -> str:
    """An aware datetime as the entity is shown it, in the zone it carries."""
    return local.strftime(STAMP_FORMAT_SECONDS if seconds else STAMP_FORMAT).rstrip()


def local_stamp(value: Any, seconds: bool = False) -> str:
    """
    '2026-11-14 15:03 EST' for a stored timestamp (naive = UTC), or with
    seconds for the places that print rows in order. A value that can't be
    read as a time comes back as it was given, so nothing is hidden behind
    a formatting failure.
    """
    moment = as_moment(value)
    if moment is None:
        return str(value or "")
    return format_stamp(to_local(moment), seconds)


def local_date(value: Any) -> str:
    """'2026-11-14': the local calendar date of a stored timestamp. No
    zone: a date shown alone is the human's date, like every other."""
    moment = as_moment(value)
    if moment is None:
        return str(value or "")[:10]
    return to_local(moment).strftime("%Y-%m-%d")

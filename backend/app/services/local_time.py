"""
The human's local time, for the clock the entity is shown.

Every time the server renders for the entity as "now" or "when this was
sent" goes through to_local: the timestamp prefix on human messages
(session_helpers.stamp_human_message) and the dates in the per-turn
[DATE CONTEXT] block. Stored timestamps stay naive UTC; this is rendering
only.

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
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)

# name -> zone, or None for a name that didn't load (warned about once)
_zones: Dict[str, Optional[tzinfo]] = {}


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

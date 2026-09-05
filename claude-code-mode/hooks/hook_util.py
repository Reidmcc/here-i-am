"""
Shared plumbing for the Here I Am Claude Code hooks.

Two jobs the individual hook scripts delegate here:

- **Fail loud.** When the backend is unreachable or errors, the entity must
  be told in-context that it is running degraded — unretrieved history and
  genuine novelty feel identical from inside, so a silent failure is an
  invisible one. Helpers print a single [HERE I AM] notice instead of
  nothing.

- **Spill and point.** Claude Code truncates oversized hook stdout to a
  short preview (observed inline cap ~20KB), without announcing the cut.
  For an identity payload that is the worst failure mode: the preview ends
  on a complete-looking paragraph and the session runs as a thin entity
  that feels fine. When a payload would blow the inline budget, the hooks
  write the bulk to a per-session file and print a loud pointer telling
  the entity to read it before doing anything else.

Environment:
    HIM_INLINE_BUDGET  max bytes of hook stdout before bulk content is
                       spilled to a file (default 18000, conservatively
                       under the observed harness cap)
"""
import glob
import json
import os
import re
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

# Claude Code speaks UTF-8 on every hook stream: the input payload arrives
# as UTF-8 JSON on stdin, and stdout/stderr are decoded as UTF-8 when
# injected into context. Python on Windows defaults piped streams to the
# ANSI codepage (cp1252), which crashed the SessionStart print outright on
# a non-breaking hyphen in the identity block — the spill file was written
# but the inline block and its read-this-file pointer never reached the
# entity — and rendered every em-dash of hook output that did survive as
# U+FFFD. Reconfigure at import, before any hook I/O, so no hook can
# forget; stop.py and session_end.py import this module for exactly this
# side effect.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_INLINE_BUDGET = 18000

# Claude Code delivers harness events through the prompt channel: background
# task notifications arrive as a bare <task-notification> block, and other
# events ride in a <system-reminder> block prepended to (or standing in for)
# the user's message. Neither is the human speaking, so both are stripped
# before recording — otherwise harness plumbing gets archived, and
# vectorized, as the human's own words. The archive stays the talk.
#
# Messages from other Claude Code sessions arrive the same way, as a bare
# attribute-carrying <cross-session-message ...> block. Two wrapper shapes
# have been observed live, and both are accepted:
#   2026-08-26 (the harness's SendMessage tool, since removed):
#     <cross-session-message from="<transport address>" from-name="<name>"
#                            from-mode="prompting">
#   2026-09-04 (the desktop app's session-management MCP,
#   mcp__ccd_session_mgmt__send_message — issue #331):
#     <cross-session-message from="local_<session id>" name="<name>">
# The sender's display name (its sidebar title) is `from-name=` in the old
# shape and `name=` in the new one; `from` went from a named-pipe address
# to the sender's real session id. They are not the human speaking either —
# but they ARE the entity speaking, from a sibling session, so they are
# extracted rather than dropped: the backend records them under the
# entity's own name with the sending session marked (issue #312). None of
# this touches what the harness delivers to the session's context — the
# message itself still arrives and can be answered.
_PLUMBING_BLOCK_RE = re.compile(
    r"<(system-reminder|task-notification)"
    r"(?:\s[^>]*)?>.*?</\1>\s*",
    re.DOTALL,
)
_CROSS_SESSION_RE = re.compile(
    r"<cross-session-message((?:\s[^>]*)?)>(.*?)</cross-session-message>\s*",
    re.DOTALL,
)
# `from-name="..."` (old wrapper) or `name="..."` (new wrapper). The word
# boundary keeps `name=` from matching inside another attribute's name.
_FROM_NAME_RE = re.compile(r'\b(?:from-)?name="([^"]*)"')

# Self-scheduled wakeup prompts (ScheduleWakeup dynamic loops, send_later
# reminders) fire back through the prompt channel verbatim — the harness
# gives the hook no marker separating a timer-fired prompt from a typed one
# (issue #318). So the convention is a sentinel the entity writes into its
# own scheduled prompts: a prompt whose user-authored part begins with
# [WAKEUP] (optionally after a slash command, since a dynamic /loop re-fires
# its whole input) is the entity's alarm clock going off, not anyone
# speaking — repeated many times and closer to a tool action than to talk.
# It is not recorded at all: not archived, not vectorized, not used as a
# retrieval query. The prompt itself still reaches the session's context
# unchanged (hooks only add; they don't rewrite the prompt), and the turn's
# work — the assistant response the Stop hook records, reflections saved —
# keeps its normal provenance.
WAKEUP_SENTINEL = "[WAKEUP]"
_WAKEUP_RE = re.compile(r"^\s*(?:/\S+\s+)?\[WAKEUP\]")


def strip_harness_blocks(prompt: str) -> str:
    """The prompt with harness-injected blocks removed; empty string when
    nothing user-authored remains (callers should skip recording then)."""
    return split_prompt_for_recording(prompt)[0]


def split_prompt_for_recording(prompt: str):
    """
    Separate a prompt into (the human's words, inter-session messages).

    Plumbing blocks (system reminders, task notifications) are discarded —
    including anything nested inside them, which is harness echo, not a
    delivery. Each <cross-session-message> block becomes one
    {"content", "sender"} dict (sender is the wrapper's name attribute —
    `name=` in the current wrapper, `from-name=` in the 2026-08 one — or
    None), in delivery order. What remains, stripped, is the human's own
    words — possibly empty.
    """
    without_plumbing = _PLUMBING_BLOCK_RE.sub("", prompt)
    peer_messages = []

    def _capture(match):
        content = match.group(2).strip()
        if content:
            name_match = _FROM_NAME_RE.search(match.group(1) or "")
            sender = (name_match.group(1).strip() if name_match else "") or None
            peer_messages.append({"content": content, "sender": sender})
        return ""

    remaining = _CROSS_SESSION_RE.sub(_capture, without_plumbing)
    return remaining.strip(), peer_messages


def is_wakeup_prompt(text: str) -> bool:
    """Whether text is a self-scheduled wakeup prompt (the [WAKEUP] sentinel
    convention — see WAKEUP_SENTINEL above). Callers pass the user-authored
    part of the prompt, i.e. split_prompt_for_recording's first element, so
    a sentinel arriving behind harness plumbing is still recognized."""
    return bool(_WAKEUP_RE.match(text))


def read_hook_input():
    """The hook payload Claude Code passes on stdin, or None if unparsable."""
    try:
        return json.load(sys.stdin)
    except Exception:
        return None


def post_backend(path: str, payload: dict, timeout: int):
    """POST to the Here I Am backend, returning the parsed JSON response.

    Raises on any failure (unreachable, HTTP error, bad JSON) — callers
    decide how loudly to report it."""
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def inline_budget() -> int:
    try:
        return int(os.environ["HIM_INLINE_BUDGET"])
    except (KeyError, ValueError):
        return DEFAULT_INLINE_BUDGET


def output_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def spill(text: str, session_id: str, name: str) -> str:
    """Write text to a per-session file and return its absolute path."""
    directory = os.path.join(tempfile.gettempdir(), "here-i-am-sessions")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{session_id}-{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.abspath(path)


def fail_loud(message: str) -> None:
    """One in-context line announcing degraded operation (stdout, exit 0)."""
    print(f"[HERE I AM] {message}")


# --- Rooms registry (issue #323): what the harness lets a hook see about
# --- live sessions.
#
# Claude Code keeps a per-process registry of running sessions at
# <config dir>/sessions/<pid>.json (config dir = CLAUDE_CONFIG_DIR or
# ~/.claude). Observed shape (Claude Code 2.1.258, undocumented internal
# state — read best-effort, never required):
#   sessionId, cwd, startedAt (ms epoch), name, nameSource ("user" |
#   "derived"), nameSince (ms epoch), messagingSocketPath, kind,
#   entrypoint, bridgeSessionId, pid, procStart, ...
# `name` is the roster name ListAgents shows, and the sidebar title the
# desktop app's session-management MCP addresses; a delivered letter
# carries it in its `name=` attribute (the removed SendMessage tool put
# `messagingSocketPath` in `from=` and the name in `from-name=`; the MCP
# puts the sender's session id in `from=`). The [ref] ListAgents shows next
# to a name is NOT derivable from any of these fields (tested against the
# session id, the socket, the peer token, and the bridge id under every
# common hash), so it is not collected — the entity records it itself if
# it wants it. A missing or unreadable directory yields an empty snapshot;
# the backend records what it didn't see as exactly that.


def claude_config_dir() -> str:
    """Claude Code's config directory (CLAUDE_CONFIG_DIR relocates all of
    ~/.claude, the sessions registry included)."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return configured
    return os.path.join(os.path.expanduser("~"), ".claude")


def _ms_to_iso(value):
    """A millisecond epoch (what the registry stores) as an ISO UTC string,
    or None when it isn't one."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    try:
        return (
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
    except (OverflowError, OSError, ValueError):
        return None


def live_sessions_snapshot(config_dir=None):
    """
    Every live session the per-process registry describes, as a list of
    {session_id, name, name_source, name_since, messaging_socket, cwd,
    started_at} dicts (values None where the file lacks them). Empty when
    the registry directory doesn't exist or nothing in it parses — a hook
    never fails over this.
    """
    directory = os.path.join(config_dir or claude_config_dir(), "sessions")
    snapshot = []
    try:
        paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    except Exception:
        return snapshot
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        session_id = data.get("sessionId")
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        snapshot.append({
            "session_id": session_id.strip(),
            "name": _optional_str(data.get("name")),
            "name_source": _optional_str(data.get("nameSource")),
            "name_since": _ms_to_iso(data.get("nameSince")),
            "messaging_socket": _optional_str(data.get("messagingSocketPath")),
            "cwd": _optional_str(data.get("cwd")),
            "started_at": _ms_to_iso(data.get("startedAt")),
        })
    return snapshot


def _optional_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def rooms_output_lines(body) -> list:
    """The rooms-registry lines a hook prints from a backend response: the
    notice (already prefixed by the backend) and, loudly, any write
    failure. Empty when the response carries neither."""
    lines = []
    notice = ((body or {}).get("rooms_notice") or "").strip()
    if notice:
        lines.append(notice)
    error = ((body or {}).get("rooms_error") or "").strip()
    if error:
        lines.append(f"[HERE I AM] {error}")
    return lines


def describe_error(error: Exception) -> str:
    return f"{error.__class__.__name__}: {error}"


def never_reached_backend(error: Exception) -> bool:
    """
    Whether a failed request provably never reached the backend — the
    connection was refused or the host didn't resolve — as opposed to a
    failure that may have landed after the server did work (an HTTP error,
    a timeout, a dropped connection). Only the first kind lets a hook say
    "NOT recorded" without checking.
    """
    reason = getattr(error, "reason", None)
    for candidate in (error, reason):
        if isinstance(candidate, (ConnectionRefusedError, socket.gaierror)):
            return True
    return False

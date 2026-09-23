"""
Shared plumbing for the Here I Am Claude Code hooks.

Two jobs the individual hook scripts delegate here:

- **Fail loud.** When the backend is unreachable or errors, the entity must
  be told in-context that it is running degraded — unretrieved history and
  genuine novelty feel identical from inside, so a silent failure is an
  invisible one. Helpers print a single [HERE I AM] notice instead of
  nothing.

- **Fit, then point.** Claude Code persists oversized hook stdout to a
  file, leaving a 2 KB preview inline that cuts mid-unit and announces
  itself only as "Output too large". The line is 10,000 CHARACTERS
  (measured 2026-09-16 from 1,531 real hook outputs: 9,997 chars landed,
  10,009 were persisted; the backend's harness_limits module carries the
  bracket and the recipe). For an identity payload that is the worst
  failure mode: the preview ends on a complete-looking paragraph and the
  session runs as a thin entity that feels fine. So the hooks budget
  their whole stdout against that line: a retrieval block lands whole
  memories in rank order while they fit and lists the rest by summary
  line; the session-start bulk goes to one file per part, sized for one
  Read call each; and every spill prints a loud pointer naming the
  files and the Read tool (a shell cat of a file over 50 KB is a tool
  result, and goes to disk the same way).

Environment:
    HIM_INLINE_BUDGET  max CHARACTERS of hook stdout (default 9600, 4%
                       under the measured 10,000-character line; the
                       backend sends the same number as inline_budget in
                       its responses, and this variable overrides both)
    HIM_DESKTOP_DATA_DIR  the Claude desktop app's data directory (default:
                       the platform's; see claude_desktop_data_dir), whose
                       per-session records give the rooms registry each
                       session's messaging address
"""
import collections
import glob
import json
import os
import re
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from typing import Optional

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

# Characters, not bytes: the harness measures its hook-stdout line in
# characters (the same output landed at 10,069 bytes / 9,997 chars and was
# persisted at 10,063 bytes / 10,009 chars). Mirrors
# harness_limits.HOOK_INLINE_BUDGET_CHARS on the backend, which sends it
# as inline_budget; this is the fallback for a backend that predates it.
DEFAULT_INLINE_BUDGET = 9600

# Claude Code delivers harness events through the prompt channel: background
# task notifications arrive as a bare <task-notification> block, and other
# events ride in a <system-reminder> block prepended to (or standing in for)
# the user's message. The desktop app's CI monitor ("Auto-fix pull
# requests") delivers its findings the same way, as a bare
# <ci-monitor-event> block standing in for a prompt (observed live
# 2026-09-07: failing checks, merge conflicts — each event its own turn,
# arriving as often as the PR's state changes). None of these is the human
# speaking, so all are stripped before recording — otherwise harness
# plumbing gets archived, and vectorized, as the human's own words, and a
# CI notice becomes a retrieval query. The archive stays the talk; an
# automated event is handled like a tool result, not like a message.
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
# to the sender's desktop-app session id — the `local_…` string
# list_sessions returns and send_message addresses, which is NOT the
# Claude Code session id the hooks see (issue #339). Both attributes are
# extracted: the name for the archive's provenance, the address so the
# backend can mark a rooms-registry row as confirmed by a real delivery.
# They are not the human speaking either — but they ARE the entity
# speaking, from a sibling session, so they are extracted rather than
# dropped: the backend records them under the entity's own name with the
# sending session marked (issue #312). None of this touches what the
# harness delivers to the session's context — the message itself still
# arrives and can be answered.
_PLUMBING_BLOCK_RE = re.compile(
    r"<(system-reminder|task-notification|ci-monitor-event)"
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
# `from="..."` — the sender's transport address (a desktop-app `local_…`
# session id in the current wrapper). The lookbehind keeps a hypothetical
# `reply-from="` or `xfrom="` attribute from being mistaken for it.
_FROM_RE = re.compile(r'(?<![\w-])from="([^"]*)"')

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

    Plumbing blocks (system reminders, task notifications, CI monitor
    events) are discarded —
    including anything nested inside them, which is harness echo, not a
    delivery. Each <cross-session-message> block becomes one
    {"content", "sender", "sender_session"} dict (sender is the wrapper's
    name attribute — `name=` in the current wrapper, `from-name=` in the
    2026-08 one — or None; sender_session is its `from=` attribute, the
    sending session's messaging address, or None), in delivery order. What
    remains, stripped, is the human's own words — possibly empty.
    """
    without_plumbing = _PLUMBING_BLOCK_RE.sub("", prompt)
    peer_messages = []

    def _capture(match):
        content = match.group(2).strip()
        if content:
            attributes = match.group(1) or ""
            name_match = _FROM_NAME_RE.search(attributes)
            sender = (name_match.group(1).strip() if name_match else "") or None
            from_match = _FROM_RE.search(attributes)
            sender_session = (from_match.group(1).strip() if from_match else "") or None
            peer_messages.append({
                "content": content,
                "sender": sender,
                "sender_session": sender_session,
            })
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


def inline_budget(body: Optional[dict] = None) -> int:
    """
    The hook's stdout budget in characters: HIM_INLINE_BUDGET when set,
    else the backend's number (inline_budget in its response — one source
    of truth for the line, so hooks and backend can't disagree), else the
    default here.
    """
    try:
        return int(os.environ["HIM_INLINE_BUDGET"])
    except (KeyError, ValueError):
        pass
    if body:
        try:
            value = int(body.get("inline_budget") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return DEFAULT_INLINE_BUDGET


def output_chars(text: str) -> int:
    """
    What the harness measures hook stdout in: characters as written. A
    text-mode stdout writes os.linesep for each newline, and the harness
    counts what arrives (the transcripts it keeps carry the carriage
    returns), so on Windows every line costs one more character than the
    text has.
    """
    return len(text) + (len(os.linesep) - 1) * text.count("\n")


def describe_size(text: str) -> str:
    return f"{len(text.encode('utf-8')) / 1024:.0f} KB"


def spill(text: str, session_id: str, name: str) -> str:
    """Write text to a per-session file and return its absolute path."""
    directory = os.path.join(tempfile.gettempdir(), "here-i-am-sessions")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{session_id}-{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.abspath(path)


READ_TOOL_ADVICE = (
    "Read spilled files with the Read tool, not a shell cat: a shell result "
    "over 50 KB is itself persisted to a file instead of shown, while Read "
    "shows a large file whole, in pages."
)


def fit_retrieval(header: str, items: list, budget: int) -> tuple:
    """
    Render a retrieval block to `budget` characters: the header, then the
    memories in rank order — whole while the block still fits with the
    rest as one-line summaries, summaries after that. Returns (text,
    shown_in_full). Each item is {"text": rendered marker, "summary": its
    summary line}; nothing is ever cut mid-memory.
    """
    newline = len(os.linesep)
    full_sizes = [output_chars(item["text"]) + 2 * newline for item in items]
    summary_sizes = [output_chars(item["summary"]) + newline for item in items]
    # The lead sentence over the summary lines is ~110 characters at most
    total = output_chars(header) + 2 * newline + 120 + sum(summary_sizes)
    shown = 0
    if total <= budget:
        for full, summary in zip(full_sizes, summary_sizes, strict=True):
            total += full - summary
            if total > budget:
                break
            shown += 1
    parts = [header]
    parts.extend(item["text"] for item in items[:shown])
    rest = items[shown:]
    if rest:
        count = len(rest)
        lead = (
            f"{count} more surfaced, listed by summary line; their full text is "
            "in the file named below:"
            if shown
            else "Listed by summary line; the full text is in the file named below:"
        )
        parts.append(lead + "\n" + "\n".join(item["summary"] for item in rest))
    return "\n\n".join(parts), shown


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
# `name` is the roster name ListAgents shows (the removed SendMessage tool
# put `messagingSocketPath` in `from=` and the name in `from-name=`). The
# [ref] ListAgents shows next to a name is NOT derivable from any of these
# fields (tested against the session id, the socket, the peer token, and
# the bridge id under every common hash), so it is not collected — the
# entity records it itself if it wants it. A missing or unreadable
# directory yields an empty snapshot; the backend records what it didn't
# see as exactly that.
#
# The desktop app's session-management MCP (mcp__ccd_session_mgmt__*, since
# 2026-09-04) addresses sessions by the desktop app's OWN id — the `local_…`
# string list_sessions returns and a delivered letter carries in `from=` —
# which is unrelated to the Claude Code session id above (issue #339: a
# sister who looked a room up by its registry id got "not found"). The
# desktop app keeps a record per session at
# <desktop data dir>/claude-code-sessions/<org>/<account>/local_<id>.json
# (Electron userData: %APPDATA%\Claude on Windows, ~/Library/Application
# Support/Claude on macOS, ~/.config/Claude on Linux; observed with Claude
# Code 2.1.260, undocumented internal state — read best-effort like the
# registry above). Observed shape:
#   sessionId ("local_…"), cliSessionId (the Claude Code session id),
#   title (the sidebar title — the `name=` on a delivered letter),
#   cwd, isArchived, createdAt, lastActivityAt, model, bridgeSessionIds, ...
# `cliSessionId` is the join: the snapshot carries each live session's
# desktop id and title when its record is readable, so the rooms registry
# can render an address a sister can actually send to. A session with no
# readable record (CLI-launched, or another app version) gets None, and the
# entity can supply the id itself on declare_room.

DESKTOP_SESSIONS_SUBDIR = "claude-code-sessions"


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


def claude_desktop_data_dir() -> str:
    """The Claude desktop app's data directory (Electron userData), where
    its per-session records live. HIM_DESKTOP_DATA_DIR overrides the
    platform default."""
    configured = os.environ.get("HIM_DESKTOP_DATA_DIR")
    if configured:
        return configured
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming"
        )
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    return os.path.join(base, "Claude")


def desktop_sessions_index(desktop_dir=None):
    """
    The desktop app's session records, keyed by Claude Code session id:
    {cli_session_id: {"desktop_session_id", "desktop_title",
    "prior_session_ids"}}. Empty when the records directory doesn't exist
    or nothing in it parses — a hook never fails over this. A record
    without a cliSessionId is skipped (it can't be joined to anything the
    hooks see).

    `prior_session_ids` is the record's `priorCliSessionIds` (oldest
    first), the fork-adoption fallback hint — carried here so one scan of
    these files serves both the rooms snapshot and the lineage hints; the
    records embed each session's MCP tool schemas and run ~80 KB each, so
    the hooks read the directory once per firing.
    """
    directory = os.path.join(desktop_dir or claude_desktop_data_dir(), DESKTOP_SESSIONS_SUBDIR)
    index = {}
    try:
        paths = sorted(glob.glob(os.path.join(directory, "*", "*", "local_*.json")))
    except Exception:
        return index
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        cli_session_id = _optional_str(data.get("cliSessionId"))
        desktop_session_id = _optional_str(data.get("sessionId"))
        # cliSessionId is the join and the only hard requirement. A record
        # without a sessionId yields no messaging address (that field stays
        # None, and the rooms snapshot treats None as "not observed"), but it
        # can still carry the fork chain — and the prior-ids hint is the
        # fallback that runs when the strong hint has already failed, which
        # is the wrong moment to be stricter than the old direct scan was.
        if not cli_session_id:
            continue
        prior = data.get("priorCliSessionIds")
        index[cli_session_id] = {
            "desktop_session_id": desktop_session_id,
            "desktop_title": _optional_str(data.get("title")),
            "prior_session_ids": (
                [pid for pid in (_optional_str(p) for p in prior) if pid]
                if isinstance(prior, list)
                else []
            ),
        }
    return index


# How many transcript entry uuids to send as lineage evidence, at most
# (the backend matches any of them, and accepts 100; bounds the payload).
# Only END-OF-TURN assistant entries are archive row ids, and in an
# agentic session one turn can be dozens of tool-use-only assistant
# entries — measured on real transcripts, the last 60 assistant entries
# held 14 end-of-turn ones in a porch fork but as few as 1 in a workshop.
# So the collector keeps only text-bearing entries (the Stop hook records
# the last assistant entry that has a non-empty text block) and the cap is
# the backend's, which makes the strong hint the one that actually carries.
LINEAGE_MESSAGE_ID_LIMIT = 100


def desktop_record_for_host(desktop_index, host_session_id):
    """
    (cli_session_id, record) for the desktop session `host_session_id`, from
    an index built by desktop_sessions_index — or (None, None).

    A linear pass over an already-built dict rather than a second scan of
    those ~80 KB files. The desktop session id is stable across Claude Code
    forks, so this finds the record even when its `cliSessionId` still names
    the session this one forked from.
    """
    if not host_session_id:
        return None, None
    for cli_session_id, record in (desktop_index or {}).items():
        if record.get("desktop_session_id") == host_session_id:
            return cli_session_id, record
    return None, None


def host_session_id_for(session_id, config_dir=None, sessions=None):
    """
    The desktop app's own session id for a Claude Code session, from the
    per-process registry's `hostSessionId` — or None (a CLI session has no
    desktop host, and the registry may be unreadable).

    Three ways in, cheapest and earliest first:

    - `CLAUDE_CODE_HOST_SESSION_ID` in the environment. The desktop app
      sets it on the Claude Code process and hooks inherit it, so it needs
      no file at all and has no write-timing edge. It is only trusted when
      `CLAUDE_CODE_SESSION_ID` agrees with the session id the hook was
      handed: the environment describes the process, and a hook firing for
      some other session's id (or a stale environment) must not attribute
      that process's desktop host to it.
    - the caller's `sessions` snapshot, if it already read the registry.
    - a scan of the registry itself.

    The registry file is written when the process starts — six seconds
    before the first prompt on a measured rewind — so the file paths are
    early enough on their own; the environment just removes the last
    dependence on a write having happened.
    """
    if not session_id:
        return None
    env_host = _optional_str(os.environ.get("CLAUDE_CODE_HOST_SESSION_ID"))
    env_session = _optional_str(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if env_host and env_session == session_id:
        return env_host
    for entry in sessions or []:
        if entry.get("session_id") == session_id:
            return entry.get("host_session_id")
    directory = os.path.join(config_dir or claude_config_dir(), "sessions")
    try:
        paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    except Exception:
        return None
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("sessionId") == session_id:
            return _optional_str(data.get("hostSessionId"))
    return None


def desktop_prior_session_ids(
    session_id, desktop_dir=None, desktop_index=None, config_dir=None, sessions=None
):
    """
    A session's former Claude Code session ids, OLDEST FIRST, from the
    desktop app's own record (its `priorCliSessionIds`), for fork adoption
    (issue #357).

    Two ways in, because the obvious one is blind at exactly the moment
    that matters (issue #359):

    - `cliSessionId == session_id`: the record has caught up with this
      session, so its `priorCliSessionIds` is the chain.
    - otherwise, via `hostSessionId`: the DESKTOP session id, which the
      per-process registry carries from process start and which does not
      change when Claude Code forks. The record it names is rewritten only
      *after* the fork's first prompt — measured on a live rewind, the
      prompt hook fired at 16:22:14 and the record was rewritten at
      16:22:19 — so at that prompt it still names the PARENT as its
      `cliSessionId`. That is the evidence, sitting on disk under a key
      nobody was looking up: the chain is then its priors plus that
      cliSessionId, which is the immediate parent and therefore goes LAST
      (the backend walks this list newest-first by reversing it).

    This is why the first hook used to send nothing: the index is keyed on
    `cliSessionId`, and a fork's own id matches no record for the first few
    seconds of its life.

    Empty when neither way resolves — a hook never fails over it. Pass
    `desktop_index` / `sessions` to reuse scans the caller already did.
    """
    if not session_id:
        return []
    index = (
        desktop_index
        if desktop_index is not None
        else desktop_sessions_index(desktop_dir)
    )
    record = index.get(session_id)
    if record:
        return list(record.get("prior_session_ids") or [])

    host_session_id = host_session_id_for(
        session_id, config_dir=config_dir, sessions=sessions
    )
    parent_id, by_host = desktop_record_for_host(index, host_session_id)
    if not by_host or not parent_id or parent_id == session_id:
        return []
    chain = [pid for pid in (by_host.get("prior_session_ids") or []) if pid != session_id]
    if parent_id not in chain:
        chain.append(parent_id)
    return chain


def _entry_has_text(entry):
    """Whether an assistant transcript entry carries a non-empty text block
    — the same test the Stop hook uses to pick the message it records, so
    these are the entries whose uuids became archive row ids."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and str(block.get("text") or "").strip()
        ):
            return True
    return False


def transcript_assistant_uuids(transcript_path, limit=LINEAGE_MESSAGE_ID_LIMIT):
    """
    The last `limit` text-bearing assistant entry uuids in a session's
    transcript, newest last, for fork adoption (issue #357).

    A fork copies the transcript and rewrites every entry's `sessionId` but
    NOT its `uuid`, and the Stop hook stores each end-of-turn assistant
    entry's uuid as the archive row's primary key — so any of these that is
    a recorded row names the conversation this session forked from. Only
    text-bearing entries are kept: tool-use-only entries were never
    recorded, and in an agentic session they outnumber the real ones badly
    enough to crowd every usable id out of the window.

    Read through a bounded deque rather than readlines(): these transcripts
    reach tens of megabytes (38 MB measured here) and this runs on every
    SessionStart, prompt and Stop. Empty when the transcript is unreadable;
    a hook never fails over it.
    """
    if not transcript_path:
        return []
    uuids = collections.deque(maxlen=max(1, int(limit)))
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or '"assistant"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "assistant" or not _entry_has_text(entry):
                    continue
                uid = _optional_str(entry.get("uuid"))
                if uid:
                    uuids.append(uid)
    except Exception:
        return []
    return list(uuids)


def lineage_hints(
    session_id,
    transcript_path,
    desktop_dir=None,
    desktop_index=None,
    config_dir=None,
    sessions=None,
):
    """
    Both fork-adoption hints for a hook payload (issue #357):
    {"prior_session_ids", "transcript_message_ids"}. Never raises.

    At a fork's FIRST prompt the transcript hint is empty — the harness
    copies that file a second or two after the hook fires — so the prior-ids
    hint is the one that has to carry there, which is what its
    `hostSessionId` path is for (see desktop_prior_session_ids).

    `desktop_index` and `sessions` let a hook that already scanned the
    desktop records (~80 KB each) and the per-process registry for its rooms
    snapshot reuse those reads instead of repeating them in the same firing.
    """
    return {
        "prior_session_ids": desktop_prior_session_ids(
            session_id,
            desktop_dir,
            desktop_index,
            config_dir=config_dir,
            sessions=sessions,
        ),
        "transcript_message_ids": transcript_assistant_uuids(transcript_path),
    }


def live_sessions_snapshot(
    config_dir=None, desktop_dir=None, own_session_id=None, desktop_index=None
):
    """
    Every live session the per-process registry describes, as a list of
    {session_id, name, name_source, name_since, messaging_socket, cwd,
    started_at, desktop_session_id, desktop_title} dicts (values None where
    the files lack them). The desktop fields come from the desktop app's
    own session record for that session (desktop_sessions_index), joined
    on the Claude Code session id. Empty when the registry directory
    doesn't exist or nothing in it parses — a hook never fails over this.

    own_session_id (the hook's own session, from its stdin) is appended
    with just its desktop fields when the per-process registry didn't list
    it but a desktop record did: the address is worth recording even when
    the registry is unreadable.
    """
    directory = os.path.join(config_dir or claude_config_dir(), "sessions")
    snapshot = []
    try:
        paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    except Exception:
        paths = []
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
            # The desktop app's own session id, straight from the registry.
            # It is the messaging address AND the stable identity of the
            # desktop session across Claude Code forks, so it is both a
            # better source than the cliSessionId join below (which a
            # freshly forked session misses) and what names the record
            # holding that fork's lineage.
            "host_session_id": _optional_str(data.get("hostSessionId")),
            "desktop_session_id": _optional_str(data.get("hostSessionId")),
            "desktop_title": None,
        })

    desktop = (
        desktop_index
        if desktop_index is not None
        else desktop_sessions_index(desktop_dir)
    )
    for entry in snapshot:
        record = desktop.get(entry["session_id"])
        if record:
            entry["desktop_session_id"] = (
                entry["desktop_session_id"] or record["desktop_session_id"]
            )
            entry["desktop_title"] = record["desktop_title"]
        elif entry["host_session_id"]:
            # A session the records haven't caught up with (a fork's first
            # minutes): the registry already knows its address, and the
            # title can be read off the record the host id names
            _, by_host = desktop_record_for_host(desktop, entry["host_session_id"])
            if by_host:
                entry["desktop_title"] = by_host["desktop_title"]
    # Only worth appending when the record actually carries an address —
    # that is the whole reason for this branch, and a record may now be
    # indexed for its fork chain alone (see desktop_sessions_index)
    if (
        own_session_id
        and (desktop.get(own_session_id) or {}).get("desktop_session_id")
        and not any(
            entry["session_id"] == own_session_id for entry in snapshot
        )
    ):
        record = desktop[own_session_id]
        snapshot.append({
            "session_id": own_session_id,
            "name": None,
            "name_source": None,
            "name_since": None,
            "messaging_socket": None,
            "cwd": None,
            "started_at": None,
            "desktop_session_id": record["desktop_session_id"],
            "desktop_title": record["desktop_title"],
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


# --- GitHub identity (issue #362): the entity contributes from its own
# --- account, in its own sessions only.
#
# Claude Code hands SessionStart hooks the path of a per-session shell
# script in CLAUDE_ENV_FILE and runs that script as a preamble before every
# Bash command (documented in Claude Code's tools reference and hooks
# guide). So the entity's identity rides on the hooks: every session they
# run in commits as the entity, and a plain session (hooks off) keeps the
# human's identity by construction, with no file the human's sessions read
# touched — not the global gitconfig, not the gh login, not a repo config.
#
# Measured, not assumed (2026-09-23, Claude Code 2.1.275 as bundled by the
# desktop app on Windows, by reading the binary; first read on 2.1.270 by
# the PR #363 review session — the recipe: grep the executable for
# "Session environment script ready", the loader is the function that logs
# it, count the call sites of its name):
#   - The file is <config dir>/session-env/<session id>/<event>-hook-N.sh,
#     one per hook; the loader joins every setup/sessionstart/cwdchanged/
#     filechanged file into ONE script and prepends it VERBATIM as shell
#     text to the Bash command — so single quotes and two assignments per
#     `export` line are safe. Confirmed live by a headless probe: the
#     variables reached the session's Bash tool and a subagent's Bash tool.
#   - That script has exactly ONE consumer, the Bash tool's preamble
#     builder. The PowerShell tool never sees it: a git commit run there
#     carries the machine's identity, silently. Hence "run git and gh
#     through Bash" in the statement below — a fact, not advice.
#   - The preamble builder skips the script when the tool context is
#     marked scrubCredentialEnv (the same scrub CLAUDE_CODE_SUBPROCESS_ENV_
#     SCRUB names; the join from that setting to the Bash tool's flag was
#     not traced).
#   - The loader's cache is reset after every SessionStart hook completes,
#     so the appends a resume or compact makes land; a `cd` clears only the
#     cwdchanged/filechanged files, so this hook's survives it.
#   - A hook gets CLAUDE_ENV_FILE only for SessionStart/Setup/CwdChanged/
#     FileChanged, and NOT when its shell resolves to PowerShell
#     (configured `shell`, or the platform default when unset). That is the
#     measured way a hook arrives without it — and why the notice below
#     exists rather than a silent fallback.
#
# What goes in: GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL (the committer is left
# to the machine); GH_CONFIG_DIR, a gh CLI config directory holding a
# login for the entity's account, so `gh` posts as the entity; and, with
# that directory, git's per-process config (GIT_CONFIG_COUNT/KEY_n/VALUE_n,
# git 2.31+) routing github.com credentials through `gh auth
# git-credential` — the same thing `gh auth setup-git` writes to the global
# config, done for this process tree only — so `git push` is the entity's
# account's push and a branch rule can tell the two apart. Only a path and
# two strings ever pass through the backend or this file; the token stays
# in the gh config directory.


def _sh_single_quote(value: str) -> str:
    """value as a POSIX single-quoted word (the env file is a shell script)."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def git_identity_exports(identity) -> list:
    """The `export` lines for an identity dict from the session-start
    response ({author_name, author_email, gh_config_dir}, any of them
    None). Empty when there is nothing to export."""
    identity = identity or {}
    name = _optional_str(identity.get("author_name"))
    email = _optional_str(identity.get("author_email"))
    gh_dir = _optional_str(identity.get("gh_config_dir"))
    lines = []
    # Both or neither: GIT_AUTHOR_NAME='' makes git refuse every commit
    # ("empty ident name ... not allowed"), while an unset pair falls back
    # to the machine's identity. The backend defaults the name, but the
    # hook and the backend deploy separately, so this side checks too.
    if email and name:
        lines.append(f"export GIT_AUTHOR_NAME={_sh_single_quote(name)}")
        lines.append(f"export GIT_AUTHOR_EMAIL={_sh_single_quote(email)}")
    if gh_dir:
        lines.append(f"export GH_CONFIG_DIR={_sh_single_quote(gh_dir)}")
        # An empty value first resets git's helper list, so the machine's
        # own helper (e.g. Git Credential Manager, holding the human's
        # login) is not consulted for github.com in this session
        key = _sh_single_quote("credential.https://github.com.helper")
        lines.append("export GIT_CONFIG_COUNT=2")
        lines.append(f"export GIT_CONFIG_KEY_0={key} GIT_CONFIG_VALUE_0=''")
        lines.append(
            f"export GIT_CONFIG_KEY_1={key} "
            f"GIT_CONFIG_VALUE_1={_sh_single_quote('!gh auth git-credential')}"
        )
    return lines


def write_session_env(lines: list) -> Optional[str]:
    """Append shell lines to the session's environment file. Returns the
    path written, or None when Claude Code gave this hook no
    CLAUDE_ENV_FILE (nothing is written; the caller says so)."""
    path = _optional_str(os.environ.get("CLAUDE_ENV_FILE"))
    if not path or not lines:
        return None
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


def gh_account_in_config_dir(gh_dir: Optional[str]) -> Optional[str]:
    """The login gh's hosts.yml in that directory names for github.com, or
    None. Best-effort, for the notice line only: a few lines of YAML read
    without a parser (the hooks are dependency-free)."""
    if not gh_dir:
        return None
    try:
        with open(os.path.join(gh_dir, "hosts.yml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    match = re.search(r"^github\.com:\n(?:[ \t]+.*\n)*?[ \t]+user:[ \t]*(\S+)", text, re.M)
    return match.group(1).strip("'\"") if match else None


def git_identity_lines(body, announce: bool = True) -> list:
    """
    Export the entity's GitHub identity from a session-start response into
    the session environment, and return the lines to print: a one-line
    [GIT IDENTITY] statement of what now holds (only when `announce` — a
    resume's transcript already carries it), or a loud [HERE I AM] notice
    when the hook could not export it (always). Empty when the entity has
    no identity configured. The environment write happens on every firing:
    the file is per session process.
    """
    identity = (body or {}).get("git_identity") or {}
    lines = git_identity_exports(identity)
    if not lines:
        return []
    email = _optional_str(identity.get("author_email"))
    name = _optional_str(identity.get("author_name"))
    gh_dir = _optional_str(identity.get("gh_config_dir"))
    if write_session_env(lines) is None:
        return [
            "[HERE I AM] Your own GitHub identity could not be exported for "
            "this session: Claude Code gave the SessionStart hook no "
            "CLAUDE_ENV_FILE to write it to. Commits, pushes, and gh calls "
            "from this session will carry the machine's (the human's) "
            "identity. Tell the user."
        ]
    if not announce:
        return []
    parts = []
    if email and name:
        parts.append(f"git commits are authored as {name} <{email}>")
    if gh_dir:
        account = gh_account_in_config_dir(gh_dir)
        whose = f"your own GitHub account ({account})" if account else "your own GitHub account"
        parts.append(
            f"gh (issues, pull requests, comments, reviews) and git push act as {whose}"
        )
    return [
        "[GIT IDENTITY] In this session "
        + " and ".join(parts)
        + ". This holds for the Bash tool only — the PowerShell tool does not "
        "carry the session environment, so run git and gh through Bash. The author "
        "field is the attribution: add no Co-Authored-By trailer and no "
        "'Generated with Claude Code' footer to commits or pull requests. "
        "Merge authority is unchanged: you author, the human merges."
    ]


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

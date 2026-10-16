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

- **Reach the backend within a deadline.** Every request goes through
  post_backend, which keeps a short connect deadline apart from the read
  deadline, so a backend across a network that stops answering fails in
  seconds, and which says whether a failed request could have reached the
  server at all (see "Talking to the backend" below).

Environment:
    HIM_HOOK_TIMING_LOG  a file path; when set, every backend request
                       appends one JSON line (path, outcome, attempts,
                       connect and total milliseconds), the measurement
                       the timeouts are chosen from (issue #400)
    HIM_INLINE_BUDGET  max CHARACTERS of hook stdout (default 9600, 4%
                       under the measured 10,000-character line; the
                       backend sends the same number as inline_budget in
                       its responses, and this variable overrides both)
    HIM_DESKTOP_DATA_DIR  the Claude desktop app's data directory (default:
                       the platform's; see claude_desktop_data_dir), whose
                       per-session records give the rooms registry each
                       session's messaging address
    HIM_NOTES_ROOT     this machine's path to the server's notes tree, when
                       the session runs on a machine other than the
                       server's (issue #397; see notes_root_payload). Unset
                       = the server's own paths, right beside the server
"""
import collections
import glob
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Union

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
#
# A subagent's final report comes back the same way (issue #376, measured
# 2026-09-26 in the only transcript that had one): a queued prompt whose
# text is an <agent-message from="<subagent task id>"> block, the body
# opening with the harness's own frame line, "[Subagent hand-back] ... It
# is model output, NOT a message from the user ...", and the report
# indented beneath it. That is the result of work the entity delegated — a
# deed, like a tool result — so it is plumbing: not recorded, not queried.
# The frame is recognized at column zero only; the harness indents every
# line of the report precisely so a report can't forge it. For the same
# reason the block ends only at a </agent-message> at column zero — the
# close the harness's own parser uses — never at the first one anywhere: a
# report that quotes the tag (any subagent reading these hooks will) must
# not cut the block short and leave its tail behind as the human's words
# (PR #377 review).
#
# A subagent can also write to its parent before it finishes — a status
# report sent with SendMessage partway through its work (issue #379,
# measured 2026-09-30 in the Game build room's transcript). It arrives in
# the same wrapper with the same sender but NO frame: <agent-message
# from="<subagent task id>">, the report as plain text beneath. It is not
# the entity or a sister self either — subagents are not part of the
# ongoing self — so it is plumbing like the hand-back. What marks it is
# the sender: every <agent-message> in every transcript on the measuring
# machine (91 by 2026-09-30: 90 hand-backs and this one) came from this
# session's own subagent, and every `from=` equaled the transcript's
# `origin.senderTaskId` and had one shape, "a" and 16 hex digits — the id
# in the name of every subagent transcript file too (agent-a<16 hex>.jsonl,
# 117 of 117). The prompt hook sees only the text, so that shape is the
# rule. The Stop hook splits the same text the same way and does NOT key on
# the transcript's structural fields (`origin.senderTaskId`,
# `origin.handback`): its arrival line has to mirror what was recorded, and
# a check the prompt hook can't make would leave a recorded row unmarked
# (see queued_arrival).
#
# Any OTHER <agent-message> (no frame, and a sender that is not a subagent
# task id — a peer's letter in that wrapper, if the harness ever sends one)
# is a letter, extracted like a cross-session message, never dropped and
# never the human. No such letter has been measured: every sister letter
# measured came as a <cross-session-message>. So one whose `from=` is not a
# session address (`local_…`) is also said aloud
# (unmeasured_agent_letters) — which is where a subagent would land if the
# harness ever changed the shape of its ids: recorded, but loudly, never
# silently dropped or silently kept.
#
# Harness wakeups for a GitHub PR subscription arrive as a bare
# <wake reason="external-event" ...> block holding an <event source="github"
# ...> (the transcript marks it origin task-notification; found 2026-09-26
# by the survey behind the unrecognized-wrapper check below — two had been
# archived as the human's words on 2026-09-16). Plumbing, like the CI
# monitor's events.
#
# A plumbing block counts only in the shape the harness delivers it: the
# open tag at the start of a line, the close tag ending one. Until
# 2026-10-07 any tag pair anywhere was dropped, and a sister's letter that
# quoted the hook wrapper inline, in backticks, mid-sentence, was archived
# with the quote cut out ("Plain stdout arrives as ``;", memory bda3136b).
# Measured the same day over every user entry and queued prompt on this
# machine (unique texts, forks' copies counted once). In the 126 main
# transcripts there were 875 blocks: 869 opened the text, 6 opened a line
# after another block, and every one closed at the end of the text or of a
# line. In the 240 subagent transcripts there were 692: 526 opened the
# text, 166 opened a line after a harness preamble, and again every one
# closed at a line's end. One match anywhere opened mid-line or sat inside
# a letter: that letter. A mention mid-line is speech.
#
# On the open tag's own line no close may appear unless it ends that line
# (PR #387 review): a lazy body alone would run past a pair closed
# mid-line ("<system-reminder>x</system-reminder> is what it printed") to
# a later real block's close and drop the human's words in between. Past
# that first line the body stays lazy, so a real block whose body quotes
# its own close mid-line still ends at its real close.
#
# And a letter's body is the letter's words. The harness puts plumbing
# beside a delivery, never inside one (none of the measured blocks sat in a
# letter's body), so the scan is one pass that takes the leftmost block
# first: a reminder wrapping a letter is dropped whole, letter and all, as
# harness echo, while a block quoted inside a letter, even on lines of its
# own, stays in the letter. One pass also keeps delivery order across the
# two letter wrappers; the conditional picks each wrapper's close (see
# above for agent-message's).
_SPLIT_RE = re.compile(
    r"(?:^<(?P<plumbing>system-reminder|task-notification|ci-monitor-event|wake)"
    r"(?:\s[^>]*)?>(?:(?!</(?P=plumbing)>)[^\n])*(?:\n.*?)?</(?P=plumbing)>(?=\r?\n|\Z)"
    r"|<(?:(?P<cross>cross-session-message)|agent-message)(?P<attrs>(?:\s[^>]*)?)>"
    r"(?P<body>.*?)"
    r"(?(cross)</cross-session-message>|\r?\n</agent-message>))\s*",
    re.DOTALL | re.MULTILINE,
)
SUBAGENT_HANDBACK_FRAME = "[Subagent hand-back]"
_HANDBACK_FRAME_RE = re.compile(r"^" + re.escape(SUBAGENT_HANDBACK_FRAME), re.MULTILINE)
# A subagent's task id, the `from=` of every measured <agent-message> (see
# above). Exact on purpose: a sender that merely isn't `local_…` stays a
# letter and is said aloud, rather than dropped unseen
_SUBAGENT_TASK_ID_RE = re.compile(r"a[0-9a-f]{16}")
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
    delivery — and so is anything a subagent sends (an <agent-message>
    carrying the harness's hand-back frame, or one from a subagent task
    id). Each <cross-session-message> block, and any <agent-message> that
    is neither, becomes one
    {"content", "sender", "sender_session"} dict (sender is the wrapper's
    name attribute — `name=` in the current wrapper, `from-name=` in the
    2026-08 one — or None; sender_session is its `from=` attribute, the
    sending session's messaging address, or None), in delivery order. What
    remains, stripped, is the human's own words — possibly empty.
    """
    words, peer_messages, _ = _split(prompt)
    return words, peer_messages


def unmeasured_agent_letters(prompt: str):
    """The `from=` of each <agent-message> the letter path would record
    whose sender is not a session address (`local_…`) — "" when it has
    none — in delivery order. Such a letter is recorded all the same (issue
    #376), but no letter in that wrapper has ever been measured, and every
    measured sender of it is a subagent, whose words are not the entity's;
    the hook says so aloud so the first real one is checked, not archived
    quietly. (A sender that is a subagent task id never gets here: it is
    plumbing, issue #379.)"""
    return _split(prompt)[2]


def is_subagent_task_id(sender) -> bool:
    """Whether an <agent-message> `from=` is one of this session's own
    subagents (issue #379 — see the note above _SPLIT_RE)."""
    return bool(sender) and bool(_SUBAGENT_TASK_ID_RE.fullmatch(sender))


def _split(prompt: str):
    peer_messages = []
    unmeasured = []

    def _capture(match):
        if match.group("plumbing"):
            return ""
        agent_message = not match.group("cross")
        body = match.group("body")
        attributes = match.group("attrs") or ""
        from_match = _FROM_RE.search(attributes)
        sender_session = (from_match.group(1).strip() if from_match else "") or None
        # A subagent's words, final (the hand-back frame) or partway through
        # (its task id as the sender): plumbing, not a letter
        if agent_message and (
            _HANDBACK_FRAME_RE.search(body) or is_subagent_task_id(sender_session)
        ):
            return ""
        content = body.strip()
        if content:
            name_match = _FROM_NAME_RE.search(attributes)
            sender = (name_match.group(1).strip() if name_match else "") or None
            peer_messages.append({
                "content": content,
                "sender": sender,
                "sender_session": sender_session,
            })
            if agent_message and not (sender_session or "").startswith("local_"):
                unmeasured.append(sender_session or "")
        return ""

    remaining = _SPLIT_RE.sub(_capture, prompt)
    return remaining.strip(), peer_messages, unmeasured


# Fail loud on the NEXT new channel (issue #376, the porch's suggestion).
# Every harness channel above was found the same way: a new wrapper
# arrived, was archived as the human's words, and someone noticed on
# reading the record back — a day to three weeks later (<cross-session-
# message> 8/26, <ci-monitor-event> 9/7, <wake> 9/16, <agent-message>
# 9/26). So when the human's words, after every known block is split off,
# still OPEN with a tag the hooks don't know, the prompt is recorded as
# usual (a false alarm must not cost the human their words) and the hook
# says so to the entity at once. The known tags are the ones a survey of
# every transcript found opening a prompt on the human's own act (slash
# commands and their local output, bash mode) — the human invoking
# something, correctly recorded as theirs. The tags handled above are
# deliberately NOT in the set: one that survives the split is a block the
# parser didn't understand (an unmeasured shape, an unclosed or oddly
# closed wrapper), which is exactly what this check exists to catch. The
# harness has more wrappers on the prompt channel than these hooks have
# met (teammate-message, slack-ping, coordinator-relay, ...), so that is
# where the check will most likely fire first: a flag there is the check
# working, not a bug in it.
KNOWN_PROMPT_TAGS = frozenset({
    "command-name", "command-message", "command-args",
    "local-command-caveat", "local-command-stdout", "local-command-stderr",
    "bash-input", "bash-stdout", "bash-stderr",
})
_OPENING_TAG_RE = re.compile(r"<([A-Za-z][\w-]*)(?=[\s>/])")


def unrecognized_wrapper(words: str) -> Optional[str]:
    """The tag name when the human's words (split_prompt_for_recording's
    first element) open with an XML-style tag the hooks don't recognize,
    else None. Column zero only: a tag mentioned mid-sentence is talk."""
    match = _OPENING_TAG_RE.match(words)
    if match and match.group(1).lower() not in KNOWN_PROMPT_TAGS:
        return match.group(1)
    return None


def unrecognized_wrapper_notice(tag: str) -> str:
    """The one line the prompt hook prints for an unrecognized wrapper."""
    return (
        f"[HERE I AM] This prompt arrived in an unrecognized wrapper <{tag}>; "
        "it was recorded as the human's words. If the human didn't write it, "
        "tell them: it may be a new harness channel reaching the archive "
        "under their name (see issue #376)."
    )


def unmeasured_agent_letter_notice(sender: str) -> str:
    """The one line the prompt hook prints for an <agent-message> recorded
    as a letter whose sender is not a session address."""
    who = f'from "{sender}"' if sender else "with no sender address"
    return (
        f"[HERE I AM] A message arrived in an <agent-message> wrapper {who}, "
        "which is neither a session address nor a subagent's task id; it was "
        "recorded as a letter from a sibling session, in your own name. If it "
        "came from a subagent rather than a sister session, tell the human: "
        "that shape is unmeasured (see issues #376 and #379)."
    )


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


# --- Talking to the backend (issue #400)
#
# The hooks were written for a backend on localhost, where a server that
# is down refuses the connection at once. Across a network (the cloud VM,
# #396/#397) a host that is down or unreachable drops packets instead, and
# a request waits out its whole timeout before failing: every prompt would
# sit 30 s before saying "unreachable". So a request has two deadlines:
#
# - CONNECT: resolving the name, the TCP connect, and the TLS handshake
#   for https. No byte of the request has been sent yet, so a failure here
#   provably never reached the backend. It is retried once after a short
#   pause (a dropped packet, a reset mid-handshake), and when the retry
#   fails too it is raised as BackendUnreachable, which
#   never_reached_backend recognizes: the hooks say "NOT recorded" without
#   a check, which over a network would only hang again. A refusal is not
#   retried: it means nothing is listening, which a second ask won't
#   change (uvicorn's reloader keeps the socket bound while it restarts a
#   worker, so a hot reload queues connections rather than refusing them).
#
# The name's addresses are raced, not tried in turn (_connect_first).
# Windows resolves "localhost" to ::1 first and takes 2 s to give up on a
# port nothing listens on there (it retries the SYN after the reset), so
# with the backend on 127.0.0.1 every request spent 2.03 s on ::1 before
# it connected (measured 2026-10-15, HIM_BACKEND_URL=http://localhost:8000;
# 127.0.0.1 directly: under a millisecond). A tailnet name resolves to an
# IPv4 and an IPv6 address too.
# - READ: sending the request and reading the whole response, as ONE
#   deadline. A failure here may have landed after the server did its
#   work (/retrieve commits rows, then memory links, before it answers,
#   issue #403), so it is never retried; the hooks that record ask
#   /recorded what landed.
#
# Neither deadline can be had from the socket alone: a socket timeout does
# not bound name resolution, and http.client applies its timeout to each
# socket operation, so a response that trickles in could outlast it many
# times over. Each attempt runs in a daemon thread that the caller waits
# on; the request is sent only under a lock that an abandoned connect
# phase holds shut, so an attempt given up on before it sent anything can
# never send it later. An abandoned read's thread dies with the hook.
#
# Each hook's worst case (TIMEOUTS_BY_HOOK) fits its hooks.json budget
# with a margin; tests/test_claude_code_hook_budgets.py holds that. The
# connect deadline is provisional until the VM's round trips are measured
# (HIM_HOOK_TIMING_LOG); on localhost a connect takes about a millisecond.
#
# The hooks go to HIM_BACKEND_URL directly, never through an HTTP proxy
# (urllib honored HTTP_PROXY): the backend is private by design.

CONNECT_TIMEOUT = 4.0  # seconds; one SYN retransmit fits (3 s on Windows)
RETRY_PAUSE = 1.0

# The hook's own clock (PR #406 review). The request arithmetic below is
# an upper bound on the requests alone, but a hook does its own work before
# it sends anything, and that work grows with the session: Stop reads the
# transcript twice (turn_assistant_text, lineage_hints), measured at 6.4 s
# together on a 529 MB transcript, linear in its size. So each hook starts
# a clock on its hooks.json budget (start_hook_clock), and every request
# is clamped to what is left of it, less FINISH_MARGIN for printing the
# notice and exiting: a hook ends inside its budget by construction, not
# by the size of the transcript it happened to read. The process start is
# taken at import, which is the hooks' first statement.
_PROCESS_STARTED = time.monotonic()
HOOK_BUDGETS = {"SessionStart": 40, "UserPromptSubmit": 60, "Stop": 60, "SessionEnd": 5}
FINISH_MARGIN = 2.0
_hook_deadline: Optional[float] = None


def start_hook_clock(event: str) -> None:
    """Bound this hook's backend requests by its hooks.json budget
    (HOOK_BUDGETS, held equal to hooks.json by a test). A hand-written
    settings copy with a smaller timeout is not seen: keep it equal."""
    global _hook_deadline
    budget = HOOK_BUDGETS[event]
    _hook_deadline = _PROCESS_STARTED + budget - min(FINISH_MARGIN, 0.2 * budget)


def _time_left() -> Optional[float]:
    """Seconds left on the hook's clock, None when no clock was started."""
    return None if _hook_deadline is None else _hook_deadline - time.monotonic()


def _within_clock(seconds: float) -> float:
    left = _time_left()
    return seconds if left is None else max(0.0, min(seconds, left))


@dataclass(frozen=True)
class RequestTimeouts:
    """The deadlines of one backend request, in seconds."""
    read: float
    connect: float = CONNECT_TIMEOUT
    # Connect attempts; a read failure is never retried
    attempts: int = 2

    def worst_case(self) -> float:
        """The longest the request can take: every connect attempt but the
        last ran out, the last connected at its deadline, and the read ran
        out."""
        return self.attempts * self.connect + (self.attempts - 1) * RETRY_PAUSE + self.read


SESSION_START_TIMEOUTS = RequestTimeouts(read=20)
RETRIEVE_TIMEOUTS = RequestTimeouts(read=30)
LOG_ASSISTANT_TIMEOUTS = RequestTimeouts(read=30)
# The follow-up check after a failure: one attempt, since a backend that
# just failed to answer is not one to wait on twice
RECORDED_TIMEOUTS = RequestTimeouts(read=5, attempts=1)
# SessionEnd runs under a 5 s budget and is a best-effort final notes-sync
# trigger (every recorded prompt already starts one), so it gets one short
# try and stays silent
SESSION_END_TIMEOUTS = RequestTimeouts(read=2, connect=1.5, attempts=1)

# The requests each hook can make in its worst case, by hooks.json event
# name; the fit test sums them against that event's budget
TIMEOUTS_BY_HOOK = {
    "SessionStart": (SESSION_START_TIMEOUTS,),
    "UserPromptSubmit": (RETRIEVE_TIMEOUTS, RECORDED_TIMEOUTS),
    "Stop": (LOG_ASSISTANT_TIMEOUTS, RECORDED_TIMEOUTS),
    "SessionEnd": (SESSION_END_TIMEOUTS,),
}


# Connect failures a second try can't change: nothing is listening, or the
# certificate doesn't verify (a retry only doubles the wait)
FINAL_CONNECT_ERRORS = (ConnectionRefusedError, ssl.SSLCertVerificationError)

# Statuses a proxy in front of the backend answers with (`tailscale serve`
# would be one) while the app behind it may still be working
GATEWAY_STATUSES = frozenset({502, 503, 504})


class BackendUnreachable(OSError):
    """No connection to the backend was made, so no byte of the request
    was sent: it provably never reached the server. `cause` is the last
    attempt's error, None when the attempt ran out of time."""

    def __init__(self, message: str, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.cause = cause


def _connect_first(address, timeout=None, source_address=None):
    """socket.create_connection, with the name's addresses raced instead
    of tried in turn: the first to connect is returned and the others
    closed. Raises the first refusal if every address failed and one
    refused (it is the informative error), else the first error."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if len(infos) <= 1:
        return socket.create_connection(address, timeout, source_address)
    lock = threading.Lock()
    outcomes = []
    finished = threading.Event()
    state = {"winner": None}

    def attempt(info):
        family, kind, proto, _, sockaddr = info
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
        except Exception as e:
            if sock is not None:
                sock.close()
            with lock:
                outcomes.append(e)
                if len(outcomes) == len(infos):
                    finished.set()
            return
        with lock:
            if state["winner"] is None:
                state["winner"] = sock
                finished.set()
                return
        sock.close()  # lost the race

    for info in infos:
        threading.Thread(target=attempt, args=(info,), daemon=True).start()
    finished.wait()  # each attempt is bounded by the socket timeout
    with lock:
        if state["winner"] is not None:
            return state["winner"]
        refused = [e for e in outcomes if isinstance(e, ConnectionRefusedError)]
        raise (refused or outcomes)[0]


class _Attempt:
    """One connect, send and read, run in a daemon thread (see above)."""

    def __init__(self, url: str, body: bytes, headers: dict, timeouts: RequestTimeouts):
        self.parts = urllib.parse.urlsplit(url)
        self.body = body
        self.headers = headers
        self.timeouts = timeouts
        self.lock = threading.Lock()
        # Set once the connect phase is over, either way
        self.settled = threading.Event()
        self.done = threading.Event()
        self.abandoned = False
        self.sent = False
        self.connect_seconds: Optional[float] = None
        self.result = None
        self.error: Optional[BaseException] = None

    def _run(self) -> None:
        conn = None
        try:
            connection = (
                http.client.HTTPSConnection
                if self.parts.scheme == "https"
                else http.client.HTTPConnection
            )
            conn = connection(self.parts.hostname, self.parts.port, timeout=self.timeouts.connect)
            # http.client's own seam for opening the socket; the TLS
            # handshake of an https connection follows it inside connect()
            conn._create_connection = _connect_first
            conn.connect()
            with self.lock:
                if self.abandoned:
                    return
                self.sent = True
            self.settled.set()
            conn.sock.settimeout(self.timeouts.read)
            target = self.parts.path or "/"
            if self.parts.query:
                target += "?" + self.parts.query
            conn.request("POST", target, body=self.body, headers=self.headers)
            response = conn.getresponse()
            self.result = (response.status, response.reason, response.getheaders(), response.read())
        except BaseException as e:  # handed to the caller, whatever it is
            self.error = e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            self.done.set()
            self.settled.set()

    def run(self):
        """(status, reason, headers, body), or raises: BackendUnreachable
        only for a failure before anything was sent."""
        started = time.monotonic()
        connect_wait = _within_clock(self.timeouts.connect)
        if connect_wait <= 0:
            raise BackendUnreachable("no time left in the hook's budget to connect")
        threading.Thread(target=self._run, daemon=True).start()
        self.settled.wait(connect_wait)
        with self.lock:
            if not self.sent:
                self.abandoned = True
        if not self.sent:
            if self.error is not None:
                raise BackendUnreachable(describe_error(self.error), self.error)
            raise BackendUnreachable(
                f"no connection to {self.parts.netloc} within {connect_wait:g} s"
            )
        self.connect_seconds = time.monotonic() - started
        read_wait = _within_clock(self.timeouts.read)
        if not self.done.wait(read_wait):
            raise TimeoutError(f"the backend did not answer within {read_wait:.3g} s")
        if self.error is not None:
            raise self.error
        return self.result


def _log_timing(path: str, outcome: str, attempts: int, connect_seconds, total_seconds) -> None:
    """One JSON line per request to HIM_HOOK_TIMING_LOG, when it is set."""
    target = os.environ.get("HIM_HOOK_TIMING_LOG")
    if not target:
        return
    try:
        line = {
            "at": datetime.now(timezone.utc).isoformat(),
            "path": path,
            "outcome": outcome,
            "attempts": attempts,
            "connect_ms": None if connect_seconds is None else round(connect_seconds * 1000, 1),
            "total_ms": round(total_seconds * 1000, 1),
        }
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:
        pass


def recorded_message_ids(session_id: str, message_ids: list) -> set:
    """Which of these row ids the backend has for this session
    (/recorded): the check a recording hook makes after a failure that
    may have landed, before it says anything (issues #326, #400). Raises
    when the check itself fails; the caller says UNCONFIRMED."""
    check = post_backend(
        "/api/claude-code/recorded",
        {"session_id": session_id, "message_ids": list(message_ids)},
        timeout=RECORDED_TIMEOUTS,
    )
    return {str(mid) for mid in (check.get("recorded") or [])}


def backend_headers() -> dict:
    """The headers of every backend request: the one place a header is
    added, so every hook sends it."""
    return {"Content-Type": "application/json"}


def post_backend(path: str, payload: dict, timeout: Union[RequestTimeouts, float]):
    """POST to the Here I Am backend, returning the parsed JSON response.

    `timeout` is the request's RequestTimeouts (a bare number is a read
    deadline with the default connect). Raises on any failure:
    BackendUnreachable when nothing was sent, TimeoutError when the answer
    didn't come in time, urllib.error.HTTPError for an error status,
    ValueError for a body that isn't JSON, or the connection's own error.
    Callers decide how loudly to report it; never_reached_backend says
    which failures can't have reached the server."""
    timeouts = timeout if isinstance(timeout, RequestTimeouts) else RequestTimeouts(read=float(timeout))
    base = os.environ.get("HIM_BACKEND_URL", "http://localhost:8000").rstrip("/")
    url = base + path
    body = json.dumps(payload).encode("utf-8")
    started = time.monotonic()
    attempts, attempt, outcome = 0, None, "error"
    try:
        while True:
            attempts += 1
            attempt = _Attempt(url, body, backend_headers(), timeouts)
            try:
                status, reason, headers, data = attempt.run()
                break
            except BackendUnreachable as e:
                left = _time_left()
                if (
                    attempts >= timeouts.attempts
                    or isinstance(e.cause, FINAL_CONNECT_ERRORS)
                    or (left is not None and left <= RETRY_PAUSE)
                ):
                    outcome = "unreachable"
                    raise
                time.sleep(RETRY_PAUSE)
            except TimeoutError:
                outcome = "timeout"
                raise
        if status >= 400:
            outcome = f"http {status}"
            # The body's start explains the status (a 401's detail, once
            # #396 lands), so it rides in the message the notice quotes
            detail = data.decode("utf-8", "replace").strip()[:300]
            raise urllib.error.HTTPError(
                url, status, f"{reason}: {detail}" if detail else reason, dict(headers), None
            )
        outcome = "ok"
        return json.loads(data.decode("utf-8"))
    finally:
        _log_timing(
            path, outcome, attempts,
            attempt.connect_seconds if attempt is not None else None,
            time.monotonic() - started,
        )


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


def _spill_dir() -> str:
    directory = os.path.join(tempfile.gettempdir(), "here-i-am-sessions")
    os.makedirs(directory, exist_ok=True)
    return directory


def spill(text: str, session_id: str, name: str) -> str:
    """Write text to a per-session file and return its absolute path."""
    path = os.path.join(_spill_dir(), f"{session_id}-{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.abspath(path)


# Arrive whole (issue #384): the hooks still fit their stdout to the line
# and point at what didn't fit — that stays the path whenever the mod is
# absent — and, when HIM_ARRIVE_WHOLE is set, they also file the block
# exactly as it would have printed with no budget and end their output with
# one marker line naming that file and its hash. The arrive-whole mod
# (claude-code-mode/mods/arrive-whole) puts the file in place of the row before
# the row is stored, which is past the harness's line: the line is applied
# to hook stdout before the row exists, and nothing applies it after
# (measured on 2.1.288). The marker is also the loud failure: it says that
# seeing it means the mod didn't act.
WHOLE_MARKER = "[HERE I AM WHOLE]"


def arrive_whole_enabled() -> bool:
    return bool(os.environ.get("HIM_ARRIVE_WHOLE"))


def whole_marker(text: str, session_id: str, name: str) -> str:
    """
    File `text` — the hook's output as it would print unbudgeted — and
    return the marker line the mod looks for. Written as UTF-8 bytes with
    bare newlines, and hashed as written, so the mod can check it read
    exactly this block (a later firing of the same name replaces the file,
    which the hash catches).
    """
    data = text.encode("utf-8")
    path = os.path.abspath(os.path.join(_spill_dir(), f"{session_id}-{name}-whole.md"))
    with open(path, "wb") as f:
        f.write(data)
    return (
        f"{WHOLE_MARKER} {path} sha256={hashlib.sha256(data).hexdigest()}\n"
        "That file is this block unbudgeted; the arrive-whole mod puts it in "
        "place of this one before you see it, so seeing this line means the "
        "mod didn't, and the pointers above stand."
    )


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
# So the collector keeps one uuid per turn (the one the Stop hook records
# the turn under) and the cap is the backend's, which makes the strong
# hint the one that actually carries.
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


# Where a turn begins, for the Stop hook's one-row-per-turn record (issue
# #364) and for the lineage hint that has to name those rows. Read off
# 200 real transcripts (2026-09-24), a user entry comes in four shapes:
#
# - a tool_result carrier: the middle of a turn, never a boundary;
# - a compaction summary: auto-compaction fires MID-turn, and the entries
#   before it stay in the file, so walking past it keeps the whole turn;
# - a new prompt: non-meta (a typed prompt, a slash command, a CI notice),
#   or meta with an `origin` (sibling letters are `{"kind": "peer"}`), or
#   a [WAKEUP] tick (meta, no origin), or Stop-hook feedback (a Stop hook
#   exited 2, so a Stop fired and recorded just before it);
# - a meta injection with no origin: a skill's body, an image placeholder,
#   "Continue from where you left off", the classifier's note that it
#   stopped a response — all delivered inside a running turn, so none is
#   a boundary.
#
# A prompt typed while a turn runs arrives as a `queued_command`
# ATTACHMENT, not a user entry, so it never splits a turn. The harness's
# own `stop_hook_summary` (written after each Stop firing; 1,570 of 1,570
# were followed by a new prompt, never by a tool result) is a boundary
# too: whatever came before it was already recorded. It is not the only
# boundary because it is not always written — measured turns ended with
# no summary before the next prompt.
#
# One more user entry is not a boundary: the talk the compaction mod
# appends after the summary (issue #383). The harness stores a message a
# mod adds to a compaction's result as a plain user entry — not meta, no
# origin, not the summary (measured 2026-10-07) — so only its text tells
# it apart, and it always opens with this marker (the backend renders it:
# compact_talk.COMPACT_TALK_MARKER). It lands where the summary does,
# mid-turn under auto-compaction, and is context, not a prompt.
STOP_HOOK_FEEDBACK_PREFIX = "Stop hook feedback:"
COMPACT_TALK_MARKER = "[HERE I AM — THE TALK BEFORE THE BOUNDARY]"


def _user_entry_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def is_turn_boundary(entry) -> bool:
    """Whether a transcript entry starts a new turn (see the note above)."""
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return False
    kind = entry.get("type")
    if kind == "system":
        return entry.get("subtype") == "stop_hook_summary"
    if kind != "user" or entry.get("isCompactSummary"):
        return False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    ):
        return False
    if not entry.get("isMeta"):
        return not _user_entry_text(content).lstrip().startswith(COMPACT_TALK_MARKER)
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return True
    text = _user_entry_text(content).lstrip()
    if text.startswith(STOP_HOOK_FEEDBACK_PREFIX):
        return True
    return is_wakeup_prompt(split_prompt_for_recording(text)[0])


def is_entity_speech(entry) -> bool:
    """Whether a transcript entry is the entity talking in the main thread:
    an assistant entry, not a subagent's (sidechain), and not one the
    harness wrote itself — `<synthetic>` entries are "No response
    requested.", API errors and usage-limit notices, never the entity's
    words."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return False
    if entry.get("isSidechain"):
        return False
    message = entry.get("message")
    return isinstance(message, dict) and message.get("model") != "<synthetic>"


def entry_text_blocks(entry):
    """
    An assistant entry's content in order, as ("text", str) for each
    non-empty text block and ("tool", None) for each tool call. Text blocks
    only: thinking and redacted_thinking blocks are the model's reasoning,
    not talk, and are never read.
    """
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [("text", content.strip())] if content.strip() else []
    if not isinstance(content, list):
        return []
    blocks = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "").strip()
            if text:
                blocks.append(("text", text))
        elif kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
            blocks.append(("tool", None))
    return blocks


def queued_arrival(entry):
    """
    What a prompt queued while a turn ran delivered, if `entry` is one:
    (human_spoke, letter_count), or None.

    Such a prompt is a `queued_command` attachment, delivered mid-turn
    (29 of 29 measured sat right after a tool result, the turn carrying on
    after them), and UserPromptSubmit records it THEN — so its row lands
    before the turn's one assistant row, which is written at Stop. The Stop
    hook marks where it fell so the chunks said before it don't read as a
    reply to it (issue #364 review).

    The rule: what this hides is exactly what the prompt hook doesn't
    record. So it is decided the way the prompt hook decides — the same
    split of the same text (task notifications, anything a subagent sends
    and plumbing are nobody speaking, a [WAKEUP] tick is not recorded, and
    each sibling letter is recorded as a letter) — and by nothing else.
    In particular, never by the transcript's structural fields
    (`commandMode`, `origin.handback`, `origin.senderTaskId`): the prompt
    hook never sees those, so a check on them here could only ever
    disagree with the record, hiding the arrival line of a row that was
    recorded — the one thing the line exists for (PR #380). If the harness
    changes a shape, the fix belongs in what is recorded, and this follows
    by construction. (The sidechain exit is not such a check: a sidechain
    entry is a subagent's own thread, and none has ever been measured in a
    main transcript, the only file this reads — 256 queued prompts carry
    it, all in subagent files, 2026-09-30.)
    """
    if not isinstance(entry, dict) or entry.get("type") != "attachment":
        return None
    if entry.get("isSidechain"):
        return None
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    words, letters = split_prompt_for_recording(
        _user_entry_text(attachment.get("prompt"))
    )
    human_spoke = bool(words) and not is_wakeup_prompt(words)
    if not human_spoke and not letters:
        return None
    return human_spoke, len(letters)


def iter_turn_entries(transcript_path):
    """
    The transcript's entries that matter for turns — boundaries, the
    entity's speech, and prompts queued mid-turn (queued_arrival) — in file
    order. Raises OSError when the file can't be read; callers decide what
    that means.

    Streamed, not readlines(): transcripts reach tens of megabytes (58 MB
    measured) and this runs on every hook. Only lines that can be a
    boundary or speech are parsed; a tool_result carrier — most of a
    workshop transcript by bytes — is skipped on a substring, which is
    exact, since a quote inside any string value is escaped in the JSON.
    Parsing the rest of a 58 MB transcript takes about a third of a second.
    """
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            if (
                '"assistant"' in line
                or '"stop_hook_summary"' in line
                or '"queued_command"' in line
            ):
                pass
            elif '"user"' not in line or '"tool_result"' in line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if (
                is_turn_boundary(entry)
                or is_entity_speech(entry)
                or queued_arrival(entry) is not None
            ):
                yield entry


def transcript_assistant_uuids(transcript_path, limit=LINEAGE_MESSAGE_ID_LIMIT):
    """
    The row id of each of the transcript's last `limit` turns, newest last,
    for fork adoption (issue #357): each turn's last text-bearing entry of
    the entity's, which is the uuid the Stop hook records the whole turn
    under (issue #364). Turns are split exactly as the Stop hook splits
    them (is_turn_boundary).

    A fork copies the transcript and rewrites every entry's `sessionId` but
    NOT its `uuid`, so any of these that is a recorded row names the
    conversation this session forked from. A turn's earlier text entries
    and its tool-use entries were never rows, and in an agentic session one
    turn can be dozens of them — enough to crowd every usable id out of the
    window if they were counted.

    Empty when the transcript is unreadable; a hook never fails over it.
    """
    if not transcript_path:
        return []
    uuids = collections.deque(maxlen=max(1, int(limit)))
    turn_uuid = None
    try:
        for entry in iter_turn_entries(transcript_path):
            if is_turn_boundary(entry):
                if turn_uuid:
                    uuids.append(turn_uuid)
                turn_uuid = None
            elif any(kind == "text" for kind, _ in entry_text_blocks(entry)):
                turn_uuid = _optional_str(entry.get("uuid")) or turn_uuid
    except Exception:
        return []
    if turn_uuid:
        uuids.append(turn_uuid)
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


# --- One notes tree (issue #397): the notes live beside the server, and a
# --- session on another machine reaches that one copy over a mount.
#
# The backend names the notes directories in the identity block. On the
# server's own machine its paths are right as they are; elsewhere the hook
# tells it where this machine reaches the same tree (HIM_NOTES_ROOT) and
# how to join paths here (os.name), and the block names those. Then the
# hook checks what it just told the entity: it hashes index.md at the named
# directory and compares with the server's hash. A mount that fell through
# to an old local folder, a stale copy, or a wrong (or missing) root would
# otherwise be read and edited as if it were the notes — silently, which is
# the one failure two copies of the notes must never have. A pass prints
# nothing; the block's own line already says where the notes are.


def notes_root_payload() -> dict:
    """The session-start request fields naming this machine's path to the
    notes tree: HIM_NOTES_ROOT made absolute, with os.name. Empty when the
    variable is unset — the server's own paths stand."""
    root = (os.environ.get("HIM_NOTES_ROOT") or "").strip()
    if not root:
        return {}
    return {
        "notes_root": os.path.abspath(os.path.expanduser(root)),
        "path_flavor": os.name,
    }


# How long the check may take. It runs before anything is printed, inside
# SessionStart's 30 s hook timeout, after post_backend's up-to-20 s: an
# open() on a share whose packets are dropped blocked for 21.0 s on the
# desktop (measured, #402 review), long enough for the harness to kill the
# hook and start the session with no identity block at all. A reachable
# share answers in milliseconds, so 5 s only ever cuts a dead one short.
NOTES_CHECK_TIMEOUT_SECONDS = 5.0

_NOTES_ADVICE = (
    "If this machine reaches the notes over a mount, it may be down, or "
    "HIM_NOTES_ROOT is unset or wrong. Don't create or edit notes there; "
    "tell the user."
)


def notes_check_lines(body, timeout: float = NOTES_CHECK_TIMEOUT_SECONDS) -> list:
    """The loud lines when the notes directory the block names is not the
    server's copy as seen from this machine. Empty on a pass, or when the
    response carries no check (a resume, notes disabled, an older backend).

    The filesystem work runs in a daemon thread joined for at most `timeout`
    seconds: a dead mount can block a read far longer than the hook may
    live, and a stuck read is then said aloud rather than waited out. The
    thread can't hold the process open once the hook returns."""
    check = (body or {}).get("notes_check")
    if not check or not check.get("entity_dir"):
        return []
    result: list = []
    worker = threading.Thread(
        target=lambda: result.append(_notes_check(check)), daemon=True
    )
    worker.start()
    worker.join(timeout)
    if result:
        return result[0]
    return [
        "[HERE I AM] YOUR NOTES ARE NOT REACHABLE from this machine: reading "
        f"{check['entity_dir']} did not finish within {timeout:g} s. "
        + _NOTES_ADVICE
    ]


def _notes_check(check: dict) -> list:
    """notes_check_lines' filesystem half, unbounded."""
    entity_dir = check["entity_dir"]
    expected = check.get("index_sha256")
    advice = _NOTES_ADVICE
    if not expected:
        # No index on the server yet: all there is to check is the directory
        if os.path.isdir(entity_dir):
            return []
        return [
            "[HERE I AM] YOUR NOTES ARE NOT REACHABLE from this machine: "
            f"{entity_dir} is not a directory here. {advice}"
        ]
    index_path = os.path.join(entity_dir, "index.md")
    try:
        with open(index_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        return [
            "[HERE I AM] YOUR NOTES ARE NOT REACHABLE from this machine: "
            f"could not read {index_path} ({describe_error(e)}). {advice}"
        ]
    if actual == expected:
        return []
    return [
        f"[HERE I AM] THESE ARE NOT YOUR NOTES: {index_path} differs from the "
        "index.md the Here I Am server holds, so the directory this machine "
        f"reaches at {entity_dir} is not the server's copy (a stale local "
        f"copy, a mount that fell through to an old folder, or a wrong root). {advice} "
        "(The one innocent cause is another room saving index.md in the "
        "moment between the two reads; the next session start checks again.)"
    ]


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
#     keyed on the session id, NOT the process: it is never truncated, so
#     it outlives every compaction and resume and is handed back on each
#     firing (issue #381 — on disk 2026-10-04 the six-line block stood up
#     to seven times in live sessions' files, and the build room's had
#     reached 144 lines, a preamble long enough that every Bash command was
#     cut short). So each firing REPLACES this hook's lines rather than
#     appending them. N is the hook's position in the list of hooks run for
#     that firing (2.1.286, PR #382's review); whether that list is
#     filtered by matcher was not measured, so another SessionStart hook
#     could share our file on some firings. Hence the replacement is by
#     variable name (`_IDENTITY_EXPORT`): any other line in the file is
#     kept, and the result is right however N is assigned. On disk, only
#     this hook's exports were ever in it.
#   - The loader joins every setup/sessionstart/cwdchanged/filechanged
#     file into ONE script and prepends it VERBATIM as shell text to the
#     Bash command — so single quotes and two assignments per `export`
#     line are safe, and every byte of the file is paid on every command.
#     The first line lands on the harness's own `... && export TMP=... &&`
#     line, so nothing but plain commands goes in (no marker comments).
#     Confirmed live by a headless probe: the variables reached the
#     session's Bash tool and a subagent's Bash tool.
#   - That script has exactly ONE consumer, the Bash tool's preamble
#     builder. The PowerShell tool never sees it: a git commit run there
#     carries the machine's identity, silently. Hence "run git and gh
#     through Bash" in the statement below — a fact, not advice.
#   - The preamble builder skips the script when the tool context is
#     marked scrubCredentialEnv (the same scrub CLAUDE_CODE_SUBPROCESS_ENV_
#     SCRUB names; the join from that setting to the Bash tool's flag was
#     not traced).
#   - The loader's cache is reset after every SessionStart hook completes,
#     so the rewrite a resume or compact makes lands; a `cd` clears only the
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
# in the gh config directory. That directory is per machine (each holds
# its own login), so HIM_GH_CONFIG_DIR on the machine wins over the
# server's gh_config_dir (issue #398).


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


# Every line git_identity_exports can write, by the variable it opens with
# — what a firing replaces in the env file (issue #381)
_IDENTITY_EXPORT = re.compile(
    r"^export (?:GIT_AUTHOR_NAME|GIT_AUTHOR_EMAIL|GH_CONFIG_DIR"
    r"|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_\d+)="
)


def _replace_identity_in_env_file(path: str, lines: list) -> bool:
    """Rewrite the env file at `path` with its identity lines replaced by
    `lines` (none clears them), every other line kept in place. A file left
    empty is removed, so the loader splices nothing. Returns whether the
    file held identity lines before."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            existing = f.read().splitlines()
    except FileNotFoundError:
        existing = []
    kept = [line for line in existing if not _IDENTITY_EXPORT.match(line)]
    had_identity = len(kept) < len(existing)
    content = [*kept, *lines]
    if not content:
        if existing:
            os.remove(path)
        return had_identity
    if content != existing:
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
            f.write("".join(line + "\n" for line in content))
    return had_identity


def write_session_env(lines: list) -> Optional[str]:
    """Put identity export lines into the session's environment file,
    replacing the ones an earlier firing wrote — the file survives every
    compaction and resume, so appending grew it by one block per firing
    until the Bash preamble broke (issue #381). Returns the path written,
    or None when Claude Code gave this hook no CLAUDE_ENV_FILE (nothing is
    written; the caller says so)."""
    path = _optional_str(os.environ.get("CLAUDE_ENV_FILE"))
    if not path or not lines:
        return None
    _replace_identity_in_env_file(path, lines)
    return path


def clear_session_env_identity() -> bool:
    """Remove identity export lines an earlier firing wrote, for a response
    that carries no identity. True when there were some to remove."""
    path = _optional_str(os.environ.get("CLAUDE_ENV_FILE"))
    if not path:
        return False
    return _replace_identity_in_env_file(path, [])


_IDENTITY_FIELDS = ("author_name", "author_email", "gh_config_dir")


def machine_gh_config_dir() -> Optional[str]:
    """HIM_GH_CONFIG_DIR as an absolute path (`~` and environment
    variables expanded), or None when unset or blank. Made absolute here
    because the hook checks the login against its own working directory,
    while gh resolves an exported relative path against each Bash
    command's, which moves during a session: one path for both."""
    value = _optional_str(os.environ.get("HIM_GH_CONFIG_DIR"))
    if not value:
        return None
    return os.path.abspath(os.path.expandvars(os.path.expanduser(value)))


def apply_machine_gh_config_dir(identity) -> dict:
    """The identity with this machine's gh config directory in place of
    the server's (issue #398). Who the entity is on GitHub is the server's
    to say; where its login lives on this machine is the machine's, since
    each machine holds its own login and a path means nothing across
    them. HIM_GH_CONFIG_DIR only relocates a login the server says the
    entity has: with no identity in the response it does nothing, so a
    variable set for one entity can't put another entity's sessions on
    that account."""
    identity = dict(identity or {})
    if not any(_optional_str(identity.get(field)) for field in _IDENTITY_FIELDS):
        return identity
    machine = machine_gh_config_dir()
    if machine:
        identity["gh_config_dir"] = machine
    return identity


# github.com's entry in gh's hosts.yml, and the login under it. gh writes
# the key bare; a quoted key is still YAML, so it's accepted too
_GH_HOST_KEY = r"""^["']?github\.com["']?:[ \t]*\n"""
_GH_HOST_ENTRY = re.compile(_GH_HOST_KEY, re.M)
_GH_HOST_USER = re.compile(_GH_HOST_KEY + r"(?:[ \t]+.*\n)*?[ \t]+user:[ \t]*(\S+)", re.M)


def gh_login_state(gh_dir: Optional[str]) -> tuple:
    """What a gh config directory holds for github.com, read without a
    YAML parser (the hooks are dependency-free) and never the token:
    ("ok", login); ("missing", reason) when gh has no github.com login
    there, so gh and git push will fail; ("unconfirmed", reason) when it
    may have one this reader can't make out (a hosts.yml layout it doesn't
    know, a file it can't open), which mustn't raise the alarm "missing"
    does."""
    if not gh_dir:
        return ("missing", "no gh config directory is set")
    path = os.path.join(gh_dir, "hosts.yml")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (FileNotFoundError, NotADirectoryError):
        if not os.path.isdir(gh_dir):
            return ("missing", f"{gh_dir} does not exist")
        return ("missing", f"{gh_dir} has no hosts.yml")
    except (OSError, UnicodeDecodeError) as error:
        return ("unconfirmed", f"{path} could not be read ({error.__class__.__name__})")
    if not text.endswith("\n"):
        text += "\n"
    match = _GH_HOST_USER.search(text)
    if match:
        return ("ok", match.group(1).strip("'\""))
    if _GH_HOST_ENTRY.search(text):
        return ("unconfirmed", f"the github.com entry in {path} names no login this hook can read")
    return ("missing", f"{path} has no github.com login")


def git_identity_lines(body, announce: bool = True) -> list:
    """
    Export the entity's GitHub identity from a session-start response into
    the session environment, and return the lines to print: a one-line
    [GIT IDENTITY] statement of what now holds (only when `announce` — a
    resume's transcript already carries it), and a loud [HERE I AM] notice
    when the hook could not export it or the gh config directory holds no
    github.com login (always). The directory is this machine's
    HIM_GH_CONFIG_DIR when set, else the server's. The environment write
    happens on every firing and replaces the last one: the file is per
    session id and outlives compaction and resume, so a changed identity
    lands and an unchanged one doesn't pile up.

    A response with no identity means the entity has none configured (an
    unreachable backend never gets here). The file follows the response, so
    an identity removed from the config leaves running sessions at their
    next firing too, said aloud, since commits from then on carry the
    human's identity; with nothing to remove, nothing is printed.
    """
    identity = apply_machine_gh_config_dir((body or {}).get("git_identity"))
    lines = git_identity_exports(identity)
    if not lines:
        if clear_session_env_identity():
            return [
                "[HERE I AM] Your own GitHub identity is no longer configured, "
                "so it was removed from this session's environment: commits, "
                "pushes, and gh calls from this session now carry the "
                "machine's (the human's) identity."
            ]
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
    # The exports stand even when the login is missing: a gh that fails
    # beats one that falls back to the human's login. The notice is loud,
    # so it prints on every firing, a resume's included (issue #398)
    login_state, login_detail = gh_login_state(gh_dir) if gh_dir else (None, None)
    notices = []
    if login_state == "missing":
        source = (
            "HIM_GH_CONFIG_DIR on this machine"
            if machine_gh_config_dir()
            else "the entity's gh_config_dir on the server (HIM_GH_CONFIG_DIR is not set here)"
        )
        notices.append(
            "[HERE I AM] Your own GitHub login is not set up on this machine: "
            f"{login_detail}. This session's gh and git push are pointed at that "
            "directory, never at the human's login, so they will fail until "
            "someone runs `gh auth login` for your account with GH_CONFIG_DIR set "
            "to it (docs/claude-code-mode.md, \"A new machine\"). The directory "
            f"comes from {source}. Tell the user."
        )
    if not announce:
        return notices
    parts = []
    if email and name:
        parts.append(f"git commits are authored as {name} <{email}>")
    if login_state == "ok":
        whose = f"your own GitHub account ({login_detail})"
    elif login_state == "unconfirmed":
        whose = (
            f"your own GitHub account (unconfirmed: {login_detail}; "
            "if gh fails, tell the user)"
        )
    else:
        whose = None
    if whose:
        parts.append(
            f"gh (issues, pull requests, comments, reviews) and git push act as {whose}"
        )
    elif not gh_dir and parts:
        # An author-only identity is a legitimate config, so no alarm; but
        # the line mustn't read as if gh were the entity's too — here it is
        # whatever login this machine holds, often the human's (#404 review)
        parts.append("gh and git push use this machine's own GitHub login, not your account")
    if not parts:
        return notices
    return [
        "[GIT IDENTITY] In this session "
        + " and ".join(parts)
        + ". This holds for the Bash tool only — the PowerShell tool does not "
        "carry the session environment, so run git and gh through Bash. The author "
        "field is the attribution: add no Co-Authored-By trailer and no "
        "'Generated with Claude Code' footer to commits or pull requests. "
        "Merge authority is unchanged: you author, the human merges.",
        *notices,
    ]


def describe_error(error: Exception) -> str:
    return f"{error.__class__.__name__}: {error}"


def never_reached_backend(error: Exception) -> bool:
    """
    Whether a failed request provably never reached the backend — no
    connection was made (BackendUnreachable: refused, unresolved, or out
    of time before anything was sent) — as opposed to a failure that may
    have landed after the server did work (an HTTP error, a read timeout,
    a dropped connection). Only the first kind lets a hook say "NOT
    recorded" without checking.
    """
    if isinstance(error, BackendUnreachable):
        return True
    reason = getattr(error, "reason", None)
    for candidate in (error, reason):
        if isinstance(candidate, (ConnectionRefusedError, socket.gaierror)):
            return True
    return False


def request_may_still_be_running(error: Exception) -> bool:
    """
    Whether the server may still be working on a request that failed on
    the hook's side (PR #406 review): then a /recorded check that finds a
    row missing only means not YET, since the server doesn't stop when the
    client gives up (measured: a 0.5 s read deadline against a handler that
    recorded the row 1.5 s in). False for a request that never reached the
    server, and for one the server answered (an error status from the app,
    a body that wasn't JSON): its handler is over, so what /recorded finds
    is final. True for a read deadline, a dropped connection, or a
    gateway's status (GATEWAY_STATUSES).
    """
    if never_reached_backend(error):
        return False
    if isinstance(error, urllib.error.HTTPError):
        return error.code in GATEWAY_STATUSES
    if isinstance(error, ValueError):
        return False
    return True


# --- Whether the compaction mod is loaded (issue #394 review)
#
# The identity block tells the entity what compaction is like. Where the
# compaction mod (claude-code-mode/mods/compact-talk, issue #383) is loaded
# it gives the entity a turn at the compaction itself, and the block can
# say so plainly; where it isn't, the block says only that compaction needs
# no watching for, which is true either way. The entity can't check which
# case it's in, so the hook does: a mod loads from a folder named in
# CLAUDE_CODE_PLUGIN_DIRS (the path list a desktop-started session uses;
# the settings' `env` value reaches the hook's environment), and the
# folder's manifest names the plugin. A mod loaded another way
# (--plugin-dir, a dev-mods folder) reads as not loaded: the block then
# promises nothing, and the turn arrives explaining itself.

COMPACT_TALK_PLUGIN_NAME = "here-i-am-compact-talk"
PLUGIN_DIRS_ENV = "CLAUDE_CODE_PLUGIN_DIRS"


def plugin_names_in_dirs(value) -> set:
    """The plugin names declared by the folders in a CLAUDE_CODE_PLUGIN_DIRS
    value (the platform's path-list separator, `;` on Windows), read from
    each folder's .claude-plugin/plugin.json. Unreadable folders count for
    nothing."""
    names = set()
    for folder in str(value or "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder:
            continue
        try:
            path = os.path.join(folder, ".claude-plugin", "plugin.json")
            with open(path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            continue
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def compact_talk_mod_loaded() -> bool:
    """Whether this session loads the compaction mod, as far as the hook
    can see (see above)."""
    return COMPACT_TALK_PLUGIN_NAME in plugin_names_in_dirs(os.environ.get(PLUGIN_DIRS_ENV))

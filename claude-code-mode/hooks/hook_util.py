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
import json
import os
import re
import sys
import tempfile
import urllib.request

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
# the user's message. Messages from other Claude Code sessions (SendMessage
# deliveries) arrive the same way, as a bare attribute-carrying
# <cross-session-message from="..." from-name="..." from-mode="..."> block —
# another session's words, not the human's. UserPromptSubmit fires for all
# of them, so without stripping, harness plumbing and peer messages get
# archived — and vectorized — as the human's own words. The archive stays
# the talk. (Stripping is archive-side only: the delivered message itself
# still reaches the entity's context untouched, so it can read and reply.)
_HARNESS_BLOCK_RE = re.compile(
    r"<(system-reminder|task-notification|cross-session-message)"
    r"(?:\s[^>]*)?>.*?</\1>\s*",
    re.DOTALL,
)


def strip_harness_blocks(prompt: str) -> str:
    """The prompt with harness-injected blocks removed; empty string when
    nothing user-authored remains (callers should skip recording then)."""
    return _HARNESS_BLOCK_RE.sub("", prompt).strip()


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


def describe_error(error: Exception) -> str:
    return f"{error.__class__.__name__}: {error}"

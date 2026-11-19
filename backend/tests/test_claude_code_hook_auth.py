"""
The hooks and the backend's lock (issue #396): every request carries this
machine's token, and a refusal is said as what it is.

"The backend was unreachable" and "the backend refused your token" need
different things done about them, and from inside they would otherwise
look alike. So each hook names which of three things happened (no token on
this machine, a token the server doesn't accept, a token not allowed this),
and the recording hooks say NOT recorded without asking /recorded: the
backend checks a credential before it writes anything, and the check
itself would be refused.

The hooks run for real here, as subprocesses, against a local server that
demands a token the way the backend does.
"""
import json
import os
import subprocess
import sys
import threading
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

GOOD = "him_the-good-token-for-these-tests-0123456789abcdef"
REVOKED = "him_a-token-the-server-no-longer-accepts-0123456789"
# Bound to another entity than the session's: accepted, then refused
OTHER = "him_a-token-bound-to-another-entity-0123456789abcd"

FORBIDDEN_DETAIL = (
    "This session's conversation belongs to another entity than the one this "
    "machine's token is bound to."
)


class LockedBackend:
    """Answers as the real backend does to a credential: 401 with none or
    an unknown one, 403 for one that isn't allowed, 200 otherwise. Records
    every request that reached it."""

    def __init__(self):
        self.requests = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, body):
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                header = self.headers.get("Authorization")
                backend.requests.append({"path": self.path, "body": body, "authorization": header})
                if header is None:
                    return self._answer(401, {"detail": "Authentication required: no credential."})
                if header == f"Bearer {OTHER}":
                    return self._answer(403, {"detail": FORBIDDEN_DETAIL})
                if header != f"Bearer {GOOD}":
                    return self._answer(401, {"detail": "The bearer token was refused."})
                if self.path.endswith("/recorded"):
                    return self._answer(200, {"recorded": [], "missing": body.get("message_ids")})
                return self._answer(200, {"context": "", "retrieval_status": "ran"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def paths(self):
        return [request["path"] for request in self.requests]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend():
    server = LockedBackend()
    yield server
    server.close()


def run_hook(script, backend, tmp_path, token=None, payload=None, extra_env=None):
    """Run a hook script as Claude Code does: JSON on stdin, the session's
    environment. Returns (exit code, stdout, stderr)."""
    env = {**os.environ, "HIM_BACKEND_URL": backend.url, "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
    for name in ("HIM_DISABLE", "HIM_TOKEN", "HIM_ENTITY", "HIM_ARRIVE_WHOLE", "HIM_NOTES_ROOT",
                 "HIM_HOOK_TIMING_LOG", "CLAUDE_ENV_FILE"):
        env.pop(name, None)
    env["HIM_DESKTOP_DATA_DIR"] = str(tmp_path / "desktop")
    if token is not None:
        env["HIM_TOKEN"] = token
    env.update(extra_env or {})
    stdin = {"session_id": str(uuid.uuid4()), "cwd": str(tmp_path), **(payload or {})}
    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(stdin).encode("utf-8"),
        capture_output=True, cwd=HOOKS_DIR, env=env, timeout=60,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
    )


def transcript(tmp_path):
    entries = [
        {"type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant", "uuid": str(uuid.uuid4()),
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
        },
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


# --- the header


class TestTheHeader:
    def test_the_token_rides_every_request_as_a_bearer_header(self, monkeypatch):
        monkeypatch.setenv("HIM_TOKEN", GOOD)
        assert hook_util.backend_headers() == {
            "Content-Type": "application/json", "Authorization": f"Bearer {GOOD}",
        }

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_with_no_token_no_header_is_invented(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("HIM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("HIM_TOKEN", value)
        assert hook_util.backend_headers() == {"Content-Type": "application/json"}

    def test_surrounding_whitespace_is_not_part_of_the_token(self, monkeypatch):
        # A value pasted into a settings file with a trailing newline
        monkeypatch.setenv("HIM_TOKEN", f"  {GOOD}\n")
        assert hook_util.backend_headers()["Authorization"] == f"Bearer {GOOD}"

    def test_post_backend_sends_it_and_only_in_the_header(self, backend, monkeypatch):
        monkeypatch.setenv("HIM_BACKEND_URL", backend.url)
        monkeypatch.setenv("HIM_TOKEN", GOOD)
        hook_util.post_backend("/api/claude-code/retrieve", {"session_id": "s"}, timeout=5)
        request = backend.requests[0]
        assert request["authorization"] == f"Bearer {GOOD}"
        assert GOOD not in request["path"]
        assert GOOD not in json.dumps(request["body"])


# --- which failures are refusals


class TestRefusals:
    @pytest.mark.parametrize("error,refused", [
        (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), True),
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), True),
        (urllib.error.HTTPError("u", 400, "Bad Request", {}, None), False),
        (urllib.error.HTTPError("u", 404, "Not Found", {}, None), False),
        (urllib.error.HTTPError("u", 500, "Internal Server Error", {}, None), False),
        (urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None), False),
        (TimeoutError("x"), False),
        (hook_util.BackendUnreachable("x"), False),
        (ConnectionResetError("x"), False),
    ])
    def test_only_401_and_403_are_refusals(self, error, refused):
        assert hook_util.refused_before_recording(error) is refused
        assert (hook_util.auth_refusal_notice(error) is not None) is refused

    def test_a_refusal_is_over_not_still_running(self):
        # The server answered; nothing is in flight that could still land
        for code in (401, 403):
            error = urllib.error.HTTPError("u", code, "x", {}, None)
            assert hook_util.request_may_still_be_running(error) is False

    def test_no_token_and_a_refused_token_are_told_apart(self, backend, monkeypatch):
        monkeypatch.setenv("HIM_BACKEND_URL", backend.url)
        monkeypatch.delenv("HIM_TOKEN", raising=False)
        with pytest.raises(urllib.error.HTTPError) as none:
            hook_util.post_backend("/x", {}, timeout=5)
        missing = hook_util.auth_refusal_notice(none.value)
        assert "this machine has none" in missing and "HIM_TOKEN is not set" in missing

        monkeypatch.setenv("HIM_TOKEN", REVOKED)
        with pytest.raises(urllib.error.HTTPError) as bad:
            hook_util.post_backend("/x", {}, timeout=5)
        refused = hook_util.auth_refusal_notice(bad.value)
        assert "refused this machine's token" in refused and "revoked" in refused
        assert "this machine has none" not in refused
        for notice in (missing, refused):
            assert "python -m app.auth token create" in notice
            assert "memory tools" in notice
            assert REVOKED not in notice

    def test_a_403_carries_the_backends_own_sentence(self, backend, monkeypatch):
        monkeypatch.setenv("HIM_BACKEND_URL", backend.url)
        monkeypatch.setenv("HIM_TOKEN", OTHER)
        with pytest.raises(urllib.error.HTTPError) as caught:
            hook_util.post_backend("/x", {}, timeout=5)
        notice = hook_util.auth_refusal_notice(caught.value)
        assert "not allowed for this machine's token" in notice
        assert FORBIDDEN_DETAIL.rstrip(".") in notice
        assert '{"detail"' not in notice and ".)." not in notice
        assert OTHER not in notice


# --- each hook, run for real


class TestThePromptHook:
    PROMPT = {"prompt": "hello"}

    def test_with_the_token_it_works(self, backend, tmp_path):
        code, out, _err = run_hook("user_prompt_submit.py", backend, tmp_path, GOOD, self.PROMPT)
        assert code == 0
        assert backend.paths() == ["/api/claude-code/retrieve"]
        assert backend.requests[0]["authorization"] == f"Bearer {GOOD}"
        assert "refused" not in out and "requires a token" not in out

    @pytest.mark.parametrize("token,words", [
        (None, "requires a token and this machine has none"),
        (REVOKED, "refused this machine's token"),
        (OTHER, "not allowed for this machine's token"),
    ])
    def test_a_refusal_is_said_and_nothing_was_recorded(self, backend, tmp_path, token, words):
        code, out, err = run_hook("user_prompt_submit.py", backend, tmp_path, token, self.PROMPT)
        assert code == 0
        assert "[HERE I AM CLOCK]" in out
        assert words in out
        assert "NOT recorded" in out and "UNCONFIRMED" not in out
        assert "unreachable" not in out
        # Refused before anything was written, so /recorded is not asked
        # (it would be refused the same way)
        assert backend.paths() == ["/api/claude-code/retrieve"]
        for token_value in (GOOD, REVOKED, OTHER):
            assert token_value not in out + err

    def test_a_wakeup_tick_says_it_too(self, backend, tmp_path):
        code, out, _err = run_hook(
            "user_prompt_submit.py", backend, tmp_path, None, {"prompt": "[WAKEUP] continue"}
        )
        assert code == 0
        assert "requires a token and this machine has none" in out
        assert "Nothing needed recording" in out
        assert "unreachable" not in out


class TestTheSessionStartHook:
    @pytest.mark.parametrize("token,words", [
        (None, "requires a token and this machine has none"),
        (REVOKED, "refused this machine's token"),
        (OTHER, "not allowed for this machine's token"),
    ])
    def test_a_refusal_is_not_called_unreachable(self, backend, tmp_path, token, words):
        code, out, _err = run_hook(
            "session_start.py", backend, tmp_path, token, {"source": "startup"}
        )
        assert code == 0
        assert words in out
        assert "WITHOUT your identity block" in out
        assert "unreachable" not in out
        assert out.count("Tell the user") == 1

    def test_a_dead_backend_is_still_called_unreachable(self, backend, tmp_path):
        url = backend.url
        backend.close()
        dead = type("Dead", (), {"url": url})()
        code, out, _err = run_hook("session_start.py", dead, tmp_path, GOOD, {"source": "startup"})
        assert code == 0
        assert "unreachable at session start" in out and "Tell the user." in out
        assert "token" not in out.lower().replace("him_token", "")


class TestTheStopHook:
    def stop(self, backend, tmp_path, token):
        return run_hook(
            "stop.py", backend, tmp_path, token, {"transcript_path": transcript(tmp_path)}
        )

    def test_with_the_token_it_records_silently(self, backend, tmp_path):
        code, out, err = self.stop(backend, tmp_path, GOOD)
        assert (code, out, err) == (0, "", "")
        assert backend.paths() == ["/api/claude-code/log-assistant"]
        assert backend.requests[0]["authorization"] == f"Bearer {GOOD}"

    @pytest.mark.parametrize("token,words", [
        (None, "requires a token and this machine has none"),
        (REVOKED, "refused this machine's token"),
        (OTHER, "not allowed for this machine's token"),
    ])
    def test_a_refusal_stops_the_turn_and_says_not_recorded(self, backend, tmp_path, token, words):
        code, out, err = self.stop(backend, tmp_path, token)
        assert code == 2 and out == ""
        assert words in err
        assert "NOT recorded" in err and "UNCONFIRMED" not in err
        # The memory tools are refused too, so the notes are what is left
        assert "notes files" in err and "memory_save" not in err
        assert backend.paths() == ["/api/claude-code/log-assistant"]


class TestTheSessionEndHook:
    def test_it_sends_the_token_and_stays_silent_either_way(self, backend, tmp_path):
        for token in (GOOD, None):
            code, out, err = run_hook("session_end.py", backend, tmp_path, token)
            assert (code, out, err) == (0, "", "")
        assert [r["authorization"] for r in backend.requests] == [f"Bearer {GOOD}", None]


class TestCredentialLines:
    """Two setups that work and are wrong, so nothing else would say so."""

    def test_a_researcher_token_in_a_sessions_hands_is_said(self, monkeypatch):
        monkeypatch.delenv("HIM_BACKEND_URL", raising=False)
        notice = "This machine's HIM_TOKEN is a researcher token: ..."
        assert hook_util.credential_lines({"credential_notice": notice}) == [f"[HERE I AM] {notice}"]

    @pytest.mark.parametrize("body", [{}, {"credential_notice": None}, {"credential_notice": ""}, None])
    def test_nothing_is_said_when_all_is_well(self, monkeypatch, body):
        monkeypatch.setenv("HIM_BACKEND_URL", "https://him.example.ts.net")
        assert hook_util.credential_lines(body) == []

    def test_a_trailing_slash_is_said_because_it_only_breaks_the_memory_tools(self, monkeypatch):
        monkeypatch.setenv("HIM_BACKEND_URL", "https://him.example.ts.net/ ")
        (line,) = hook_util.credential_lines({})
        assert "ends with a slash" in line and "memory tools" in line
        assert "${HIM_BACKEND_URL}/mcp" in line

    def test_the_session_start_hook_prints_them(self, backend, tmp_path):
        code, out, _err = run_hook(
            "session_start.py", backend, tmp_path, GOOD, {"source": "resume"},
            extra_env={"HIM_BACKEND_URL": backend.url + "/"},
        )
        assert code == 0
        assert "HIM_BACKEND_URL ends with a slash" in out
        # The hooks themselves strip it, which is why nothing else notices
        assert backend.paths() == ["/api/claude-code/session-start"]

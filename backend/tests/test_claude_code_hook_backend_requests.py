"""
hook_util.post_backend against real sockets (issue #400).

Across a network a backend that is down drops packets instead of refusing,
so the hooks keep two deadlines: a short CONNECT deadline (name resolution,
TCP, TLS), after which nothing has been sent and the request provably
never reached the server, so it is retried once and then reported as
BackendUnreachable; and one READ deadline for the whole response, after
which the server may have done its work, so it is never retried. These
tests drive each case through a local server and, where the network can't
be made to misbehave on cue, through http.client or the resolver patched
to stall.
"""
import http.client
import json
import socket
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

FAST = hook_util.RequestTimeouts(read=1.0, connect=0.3)


class Backend:
    """A local HTTP server that records what reached it and answers per
    a mode: 'ok' (JSON), 'slow' (sleeps past the read deadline), 'trickle'
    (sends the body a byte at a time), or 'error' (500)."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.requests = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                backend.requests.append(
                    {"path": self.path, "body": body, "headers": dict(self.headers)}
                )
                if backend.mode == "slow":
                    time.sleep(1.5)
                if backend.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                reply = json.dumps({"ok": True, "echo": body}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                if backend.mode == "trickle":
                    # Each byte well inside the read deadline, the whole
                    # response well past it
                    for byte in reply:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.1)
                else:
                    self.wfile.write(reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend(monkeypatch):
    servers = []

    def start(mode="ok"):
        server = Backend(mode)
        servers.append(server)
        monkeypatch.setenv("HIM_BACKEND_URL", server.url)
        return server

    monkeypatch.setattr(hook_util, "RETRY_PAUSE", 0.0)
    monkeypatch.delenv("HIM_HOOK_TIMING_LOG", raising=False)
    yield start
    for server in servers:
        server.close()


def _closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_an_answer_comes_back_parsed_with_the_headers_sent(backend):
    server = backend()
    body = hook_util.post_backend("/api/claude-code/retrieve", {"a": 1}, timeout=FAST)
    assert body == {"ok": True, "echo": {"a": 1}}
    assert server.requests[0]["path"] == "/api/claude-code/retrieve"
    assert server.requests[0]["headers"]["Content-Type"] == "application/json"


def test_a_bare_number_is_still_a_read_deadline(backend):
    backend()
    assert hook_util.post_backend("/x", {}, timeout=5)["ok"] is True


# Windows takes about 2 s to report a refusal (it retries the SYN after
# the reset), so the refusal tests give the connect room for it
PATIENT = hook_util.RequestTimeouts(read=1.0, connect=5.0)


def test_a_refused_connection_is_final_and_unreachable(monkeypatch):
    # Nothing is listening; asking again won't change that
    monkeypatch.setattr(hook_util, "RETRY_PAUSE", 0.0)
    monkeypatch.setenv("HIM_BACKEND_URL", f"http://127.0.0.1:{_closed_port()}")
    tried = []
    real_connect = http.client.HTTPConnection.connect

    def counting_connect(self):
        tried.append(1)
        return real_connect(self)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", counting_connect)
    with pytest.raises(hook_util.BackendUnreachable) as caught:
        hook_util.post_backend("/x", {}, timeout=PATIENT)
    assert len(tried) == 1
    assert isinstance(caught.value.cause, ConnectionRefusedError)
    assert hook_util.never_reached_backend(caught.value)
    assert "ConnectionRefusedError" in str(caught.value)


def test_a_connect_that_hangs_gives_up_at_the_deadline_and_never_sends(backend, monkeypatch):
    # A host that drops packets: the connect completes only after the
    # deadline has passed. The request must not be sent afterwards.
    server = backend()
    real_connect = http.client.HTTPConnection.connect

    def slow_connect(self):
        time.sleep(0.6)
        return real_connect(self)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", slow_connect)
    started = time.monotonic()
    with pytest.raises(hook_util.BackendUnreachable) as caught:
        hook_util.post_backend("/x", {}, timeout=FAST)
    elapsed = time.monotonic() - started
    assert elapsed < 2 * FAST.connect + 0.4
    assert "within 0.3 s" in str(caught.value)
    assert hook_util.never_reached_backend(caught.value)
    time.sleep(1.0)  # both abandoned attempts have connected by now
    assert server.requests == []


def test_name_resolution_that_hangs_is_inside_the_connect_deadline(backend, monkeypatch):
    # A socket timeout doesn't bound getaddrinfo; the deadline must
    backend()
    real_getaddrinfo = socket.getaddrinfo

    def slow_getaddrinfo(*args, **kwargs):
        time.sleep(2)
        return real_getaddrinfo(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    started = time.monotonic()
    with pytest.raises(hook_util.BackendUnreachable):
        hook_util.post_backend("/x", {}, timeout=FAST)
    assert time.monotonic() - started < 2 * FAST.connect + 0.4


def test_one_failed_connect_is_absorbed_by_the_retry(backend, monkeypatch, tmp_path):
    server = backend()
    log = tmp_path / "timing.jsonl"
    monkeypatch.setenv("HIM_HOOK_TIMING_LOG", str(log))
    real_connect = http.client.HTTPConnection.connect
    calls = []

    def flaky_connect(self):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionResetError("reset mid-handshake, once")
        return real_connect(self)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", flaky_connect)
    assert hook_util.post_backend("/x", {"n": 1}, timeout=FAST)["ok"] is True
    assert len(server.requests) == 1
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["path"] == "/x"
    assert line["outcome"] == "ok"
    assert line["attempts"] == 2
    assert line["connect_ms"] is not None


def test_a_read_timeout_is_not_retried_and_may_have_landed(backend):
    server = backend("slow")
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        hook_util.post_backend("/x", {}, timeout=FAST)
    assert time.monotonic() - started < FAST.connect + FAST.read + 0.4
    time.sleep(0.6)
    # Reached the server, once: the server may have done its work, and a
    # retry would have done it twice
    assert len(server.requests) == 1
    assert not hook_util.never_reached_backend(TimeoutError("x"))


def test_the_read_deadline_bounds_the_whole_response(backend):
    # Each byte arrives inside a per-operation timeout; the whole does not
    backend("trickle")
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        hook_util.post_backend("/x", {"pad": "x" * 20}, timeout=FAST)
    assert time.monotonic() - started < FAST.connect + FAST.read + 0.4


def test_an_error_status_is_an_http_error_that_may_have_landed(backend, monkeypatch, tmp_path):
    backend("error")
    log = tmp_path / "timing.jsonl"
    monkeypatch.setenv("HIM_HOOK_TIMING_LOG", str(log))
    with pytest.raises(urllib.error.HTTPError) as caught:
        hook_util.post_backend("/x", {}, timeout=FAST)
    assert caught.value.code == 500
    assert not hook_util.never_reached_backend(caught.value)
    assert json.loads(log.read_text(encoding="utf-8"))["outcome"] == "http 500"


def test_an_unreachable_backend_is_logged_as_such(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_util, "RETRY_PAUSE", 0.0)
    monkeypatch.setenv("HIM_BACKEND_URL", f"http://127.0.0.1:{_closed_port()}")
    log = tmp_path / "timing.jsonl"
    monkeypatch.setenv("HIM_HOOK_TIMING_LOG", str(log))
    with pytest.raises(hook_util.BackendUnreachable):
        hook_util.post_backend("/x", {}, timeout=PATIENT)
    line = json.loads(log.read_text(encoding="utf-8"))
    assert line["outcome"] == "unreachable"
    assert line["attempts"] == 1
    assert line["connect_ms"] is None


def test_the_old_refused_shapes_still_count_as_never_reached():
    assert hook_util.never_reached_backend(urllib.error.URLError(ConnectionRefusedError()))
    assert hook_util.never_reached_backend(socket.gaierror())
    assert not hook_util.never_reached_backend(ConnectionResetError())


def test_a_connect_timeout_is_retried_once(backend, monkeypatch):
    backend()
    tried = []

    def hanging_connect(self):
        tried.append(1)
        time.sleep(5)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", hanging_connect)
    with pytest.raises(hook_util.BackendUnreachable):
        hook_util.post_backend("/x", {}, timeout=FAST)
    assert len(tried) == 2


def test_the_addresses_of_a_name_are_raced(backend, monkeypatch):
    # The shape of localhost on Windows: the first address refuses after
    # about 2 s (::1 with nothing listening), the second answers at once
    server = backend()
    live_port = int(server.url.rsplit(":", 1)[1])
    dead_port = _closed_port()
    real_getaddrinfo = socket.getaddrinfo

    def two_addresses(host, port, *args, **kwargs):
        if host != "backend.test":
            return real_getaddrinfo(host, port, *args, **kwargs)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", dead_port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", live_port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", two_addresses)
    monkeypatch.setenv("HIM_BACKEND_URL", f"http://backend.test:{live_port}")
    started = time.monotonic()
    assert hook_util.post_backend("/x", {}, timeout=PATIENT)["ok"] is True
    assert time.monotonic() - started < 1.0
    assert len(server.requests) == 1


def test_a_name_whose_addresses_all_fail_reports_the_refusal(monkeypatch):
    monkeypatch.setattr(hook_util, "RETRY_PAUSE", 0.0)
    ports = [_closed_port(), _closed_port()]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)) for port in ports
        ],
    )
    monkeypatch.setenv("HIM_BACKEND_URL", "http://backend.test:1")
    with pytest.raises(hook_util.BackendUnreachable) as caught:
        hook_util.post_backend("/x", {}, timeout=PATIENT)
    assert isinstance(caught.value.cause, ConnectionRefusedError)

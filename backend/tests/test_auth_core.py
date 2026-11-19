"""
The pieces of authentication that need no server (issue #396): password
hashing, the access policy, the login throttle, and the same-origin rule.
"""
import pytest
from starlette.requests import Request

from app.auth import passwords, policy
from app.auth.origin import arrived_over_https, cookie_secure, origin_problem
from app.auth.policy import SCOPE_ENTITY, SCOPE_RESEARCHER, VIA_TOKEN, Principal
from app.auth.throttle import (
    BASE_WAIT_SECONDS,
    FREE_FAILURES,
    MAX_WAIT_SECONDS,
    LoginThrottle,
)
from app.config import settings

PASSWORD = "correct horse battery staple"


@pytest.fixture
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "SCRYPT_N", 2 ** 10)


def request_with(headers: dict, method: str = "POST", scheme: str = "http") -> Request:
    return Request({
        "type": "http",
        "method": method,
        "scheme": scheme,
        "path": "/api/x",
        "query_string": b"",
        "server": ("test", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


class TestPasswords:
    def test_round_trip_at_the_real_cost(self):
        stored = passwords.hash_password(PASSWORD)
        assert stored.startswith(f"scrypt${2 ** 16}$8$1$")
        assert PASSWORD not in stored
        assert passwords.verify_password(PASSWORD, stored)
        assert not passwords.verify_password(PASSWORD + "x", stored)

    def test_each_hash_has_its_own_salt(self, fast_scrypt):
        assert passwords.hash_password(PASSWORD) != passwords.hash_password(PASSWORD)

    def test_verification_uses_the_stored_parameters(self, fast_scrypt, monkeypatch):
        stored = passwords.hash_password(PASSWORD)
        monkeypatch.setattr(passwords, "SCRYPT_N", 2 ** 11)
        assert passwords.verify_password(PASSWORD, stored)
        assert passwords.needs_rehash(stored)
        assert not passwords.needs_rehash(passwords.hash_password(PASSWORD))

    @pytest.mark.parametrize("stored", [
        "", "plaintext", "scrypt$1024$8$1$onlyfive", "bcrypt$1024$8$1$c2FsdA$a2V5",
        "scrypt$notanumber$8$1$c2FsdA$a2V5", "scrypt$1024$8$1$c2FsdA$!!!",
    ])
    def test_a_damaged_hash_fails_closed(self, stored):
        assert passwords.verify_password(PASSWORD, stored) is False

    def test_a_stored_hash_cannot_ask_for_unbounded_memory(self):
        # n=2**24 with r=8 is 16 GB; the ceiling refuses it instead of trying
        assert passwords.verify_password(PASSWORD, f"scrypt${2 ** 24}$8$1$c2FsdA$a2V5") is False

    def test_length_limits(self):
        assert passwords.password_problem("short") is not None
        assert passwords.password_problem("x" * passwords.MIN_PASSWORD_LENGTH) is None
        assert passwords.password_problem("x" * (passwords.MAX_PASSWORD_LENGTH + 1)) is not None
        assert passwords.password_problem(None) is not None

    def test_an_oversized_guess_is_not_hashed(self, fast_scrypt):
        stored = passwords.hash_password(PASSWORD)
        assert passwords.verify_password("x" * (passwords.MAX_PASSWORD_LENGTH + 1), stored) is False


class TestPolicy:
    researcher = Principal(scope=SCOPE_RESEARCHER, via=VIA_TOKEN, name="r")
    entity = Principal(scope=SCOPE_ENTITY, via=VIA_TOKEN, name="e", entity_id="kira")

    def test_what_is_public_is_exactly_health_and_login(self):
        assert policy.PUBLIC_ROUTES == {("GET", "/api/health"), ("POST", "/api/auth/login")}
        assert policy.is_public("get", "/api/health")
        assert not policy.is_public("POST", "/api/health")
        assert not policy.is_public("GET", "/api/auth/login")
        assert not policy.is_public("GET", None)

    def test_the_researcher_reaches_everything(self):
        for path in ("/api/conversations/", "/api/claude-code/retrieve", "/mcp", "/anything"):
            assert policy.allowed(self.researcher, "DELETE", path)

    @pytest.mark.parametrize("path", [
        "/api/claude-code/session-start", "/api/claude-code/retrieve",
        "/api/claude-code/compact-talk/taken", "/mcp",
    ])
    def test_an_entity_token_reaches_the_claude_code_endpoints(self, path):
        assert policy.allowed(self.entity, "POST", path)

    @pytest.mark.parametrize("path", [
        "/api/conversations/{conversation_id}/archive", "/api/memories/{message_id}/status",
        "/api/chat/stream", "/api/claude-code", "/api/claude-codex/x", "/mcp/x", "/mcpx",
        "/api/auth/logout-all", "/docs", None, "",
    ])
    def test_an_entity_token_reaches_nothing_else(self, path):
        assert not policy.allowed(self.entity, "POST", path)

    def test_any_principal_may_ask_who_it_is(self):
        assert policy.allowed(self.entity, "GET", "/api/auth/session")
        assert not policy.allowed(self.entity, "POST", "/api/auth/session")

    def test_an_unknown_scope_reaches_nothing(self):
        stranger = Principal(scope="admin", via=VIA_TOKEN, name="x")
        for path in ("/api/claude-code/retrieve", "/api/auth/session", "/api/conversations/"):
            assert not policy.allowed(stranger, "GET", path)


class TestThrottle:
    def make(self):
        now = [1000.0]
        return LoginThrottle(clock=lambda: now[0]), now

    def test_a_few_mistakes_are_free(self):
        throttle, _ = self.make()
        for _ in range(FREE_FAILURES):
            throttle.failure()
        assert throttle.retry_after() == 0

    def test_each_further_mistake_doubles_the_wait(self):
        throttle, now = self.make()
        for _ in range(FREE_FAILURES):
            throttle.failure()
        waits = []
        for _ in range(4):
            throttle.failure()
            waits.append(throttle.retry_after())
            now[0] += throttle.retry_after()
        assert waits == [BASE_WAIT_SECONDS * m for m in (1, 2, 4, 8)]

    def test_the_wait_is_capped(self):
        throttle, _ = self.make()
        for _ in range(FREE_FAILURES + 40):
            throttle.failure()
        assert throttle.retry_after() == MAX_WAIT_SECONDS

    def test_the_wait_runs_out(self):
        throttle, now = self.make()
        for _ in range(FREE_FAILURES + 1):
            throttle.failure()
        assert throttle.retry_after() == BASE_WAIT_SECONDS
        now[0] += BASE_WAIT_SECONDS
        assert throttle.retry_after() == 0

    def test_waiting_it_out_does_not_forgive_the_count(self):
        throttle, now = self.make()
        for _ in range(FREE_FAILURES + 1):
            throttle.failure()
        now[0] += 3600
        throttle.failure()
        assert throttle.retry_after() == BASE_WAIT_SECONDS * 2

    def test_a_right_password_clears_it(self):
        throttle, _ = self.make()
        for _ in range(FREE_FAILURES + 3):
            throttle.failure()
        throttle.success()
        assert throttle.retry_after() == 0
        throttle.failure()
        assert throttle.retry_after() == 0


class TestOrigin:
    def test_the_servers_own_origin_passes(self):
        request = request_with({"Host": "localhost:8000", "Origin": "http://localhost:8000"})
        assert origin_problem(request) is None

    def test_scheme_is_not_compared(self):
        # Behind a TLS terminator the browser's origin is https and this
        # server is spoken to over http
        request = request_with({"Host": "him.example.ts.net", "Origin": "https://him.example.ts.net"})
        assert origin_problem(request) is None

    def test_another_site_is_refused(self):
        request = request_with({"Host": "localhost:8000", "Origin": "https://elsewhere.example"})
        problem = origin_problem(request)
        assert "https://elsewhere.example" in problem
        assert "AUTH_ALLOWED_ORIGINS" in problem

    def test_another_port_is_another_origin(self):
        request = request_with({"Host": "localhost:8000", "Origin": "http://localhost:3000"})
        assert origin_problem(request) is not None

    def test_a_lookalike_host_is_refused(self):
        request = request_with({"Host": "localhost:8000", "Origin": "http://localhost:8000.evil.example"})
        assert origin_problem(request) is not None

    @pytest.mark.parametrize("headers", [{}, {"Origin": ""}, {"Origin": "null"}])
    def test_no_origin_is_refused(self, headers):
        problem = origin_problem(request_with({"Host": "localhost:8000", **headers}))
        assert "bearer token" in problem

    def test_a_proxy_that_rewrites_host_is_covered_by_forwarded_host(self):
        request = request_with({
            "Host": "127.0.0.1:8000",
            "X-Forwarded-Host": "him.example.ts.net",
            "Origin": "https://him.example.ts.net",
        })
        assert origin_problem(request) is None

    def test_the_first_forwarded_host_is_the_one_the_browser_used(self):
        request = request_with({
            "Host": "127.0.0.1:8000",
            "X-Forwarded-Host": "him.example.ts.net, inner.proxy",
            "Origin": "https://inner.proxy",
        })
        assert origin_problem(request) is not None

    def test_allowed_origins_name_one_outright(self, monkeypatch):
        monkeypatch.setattr(
            settings, "auth_allowed_origins", "https://a.example , https://him.example.ts.net/"
        )
        request = request_with({"Host": "127.0.0.1:8000", "Origin": "https://him.example.ts.net"})
        assert origin_problem(request) is None
        request = request_with({"Host": "127.0.0.1:8000", "Origin": "https://b.example"})
        assert origin_problem(request) is not None


class TestCookieSecure:
    def test_auto_follows_how_the_login_arrived(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_cookie_secure", "auto")
        assert cookie_secure(request_with({"Origin": "https://him.example.ts.net"})) is True
        assert cookie_secure(request_with({"Origin": "http://localhost:8000"})) is False

    def test_the_origin_outranks_the_scheme_this_server_saw(self):
        # The terminator speaks http to the server; the browser spoke https
        assert arrived_over_https(request_with({"Origin": "https://x.example"}, scheme="http"))
        assert not arrived_over_https(request_with({"Origin": "http://localhost:8000"}, scheme="https"))

    def test_with_no_origin_the_scheme_decides(self):
        assert arrived_over_https(request_with({}, scheme="https"))
        assert not arrived_over_https(request_with({}, scheme="http"))

    def test_always_and_never(self, monkeypatch):
        plain = request_with({"Origin": "http://localhost:8000"})
        tls = request_with({"Origin": "https://x.example"})
        monkeypatch.setattr(settings, "auth_cookie_secure", "always")
        assert cookie_secure(plain) is True
        monkeypatch.setattr(settings, "auth_cookie_secure", "never")
        assert cookie_secure(tls) is False

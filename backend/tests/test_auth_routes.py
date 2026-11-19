"""
Logging in, staying logged in, and being refused (issue #396): the real
dependency on the real app, with nothing signed in for it.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import passwords, store
from app.auth.throttle import FREE_FAILURES, login_throttle
from app.config import settings
from app.models.auth import ApiToken, AuthSession

pytestmark = pytest.mark.real_auth

PASSWORD = "correct horse battery staple"
ORIGIN = {"Origin": "http://test"}
# Any route that needs a credential and nothing else; cheap, and reads only
PROTECTED = "/api/config/presets"


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "SCRYPT_N", 2 ** 10)


@pytest.fixture
async def client(auth_app):
    app, _maker = auth_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def maker(auth_app):
    return auth_app[1]


@pytest.fixture
async def with_password(maker):
    async with maker() as db:
        await store.set_password(db, PASSWORD)


async def log_in(client, password=PASSWORD, headers=ORIGIN):
    return await client.post("/api/auth/login", json={"password": password}, headers=headers)


async def mint(maker, name="laptop", scope="entity", entity_id="test-entity"):
    async with maker() as db:
        value, _row = await store.create_token(db, name, scope, entity_id)
    return {"Authorization": f"Bearer {value}"}


class TestNoCredential:
    async def test_a_protected_route_answers_401(self, client):
        response = await client.get(PROTECTED)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "no credential" in response.json()["detail"]

    async def test_health_answers_anyone(self, client):
        assert (await client.get("/api/health")).status_code == 200

    async def test_asking_who_you_are_is_not_public(self, client):
        assert (await client.get("/api/auth/session")).status_code == 401


class TestLogin:
    async def test_the_right_password_opens_a_session(self, client, with_password, maker):
        response = await log_in(client)
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "researcher" and body["via"] == "session"
        assert body["expires_at"].endswith("Z")
        # The cookie is the session; with it the protected route answers
        assert (await client.get(PROTECTED)).status_code == 200
        who = (await client.get("/api/auth/session")).json()
        assert who["name"] == "researcher" and who["entity_id"] is None

    async def test_the_cookie_is_unreadable_to_scripts_and_never_cross_site(
        self, client, with_password
    ):
        cookie = (await log_in(client)).headers["set-cookie"].lower()
        assert cookie.startswith("him_session=")
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie
        assert f"max-age={settings.auth_session_max_days * 86400}" in cookie
        # http://test is plain http: Secure would make some browsers drop it
        assert "secure" not in cookie

    async def test_over_https_the_cookie_is_secure(self, auth_app, with_password):
        app, _ = auth_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://him.example") as c:
            response = await log_in(c, headers={"Origin": "https://him.example"})
        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()

    async def test_the_database_holds_a_digest_not_the_cookie(self, client, with_password, maker):
        await log_in(client)
        value = client.cookies.get(store.SESSION_COOKIE)
        async with maker() as db:
            rows = (await db.execute(select(AuthSession))).scalars().all()
        assert [row.id for row in rows] == [store.digest(value)]
        assert value not in rows[0].id

    async def test_a_wrong_password_is_refused_and_sets_nothing(self, client, with_password):
        response = await log_in(client, "not the password at all")
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
        assert (await client.get(PROTECTED)).status_code == 401

    async def test_with_no_password_set_nobody_gets_in(self, client):
        response = await log_in(client, "")
        assert response.status_code == 503
        assert "python -m app.auth init" in response.json()["detail"]
        assert (await log_in(client)).status_code == 503

    async def test_login_must_come_from_the_servers_own_page(self, client, with_password):
        assert (await log_in(client, headers={})).status_code == 403
        elsewhere = await log_in(client, headers={"Origin": "https://elsewhere.example"})
        assert elsewhere.status_code == 403
        assert "set-cookie" not in elsewhere.headers

    async def test_a_cross_site_attempt_does_not_count_as_a_guess(self, client, with_password):
        # Refused before the password is looked at, so another site can
        # neither guess through the researcher's browser nor lock them out
        for _ in range(FREE_FAILURES + 3):
            await log_in(client, "wrong wrong wrong", headers={"Origin": "https://elsewhere.example"})
        assert login_throttle.retry_after() == 0
        assert (await log_in(client)).status_code == 200


class TestThrottledLogin:
    async def test_wrong_guesses_buy_a_wait(self, client, with_password):
        for _ in range(FREE_FAILURES):
            assert (await log_in(client, "wrong wrong wrong")).status_code == 401
        assert (await log_in(client, "wrong wrong wrong")).status_code == 401
        waiting = await log_in(client)
        # Even the right password is not looked at while the wait runs
        assert waiting.status_code == 429
        assert int(waiting.headers["retry-after"]) >= 1
        assert "set-cookie" not in waiting.headers

    async def test_a_burst_sent_at_once_gets_no_extra_guesses(self, client, with_password, monkeypatch):
        looked_at = []
        real = passwords.verify_password

        def counting(password, stored):
            looked_at.append(password)
            return real(password, stored)

        monkeypatch.setattr(passwords, "verify_password", counting)
        responses = await asyncio.gather(*[
            log_in(client, f"guess number {i:04d}") for i in range(40)
        ])
        statuses = sorted(r.status_code for r in responses)
        # Every guess past the free ones plus the one that set the wait
        # found the wait in force when its turn came
        assert len(looked_at) == FREE_FAILURES + 1
        assert statuses == [401] * (FREE_FAILURES + 1) + [429] * (40 - FREE_FAILURES - 1)

    async def test_a_right_password_clears_the_count(self, client, with_password):
        for _ in range(FREE_FAILURES):
            await log_in(client, "wrong wrong wrong")
        assert (await log_in(client)).status_code == 200
        for _ in range(FREE_FAILURES):
            assert (await log_in(client, "wrong wrong wrong")).status_code == 401
        assert login_throttle.retry_after() == 0


class TestSessionLifetime:
    async def test_logout_ends_the_session_on_the_server(self, client, with_password, maker):
        await log_in(client)
        value = client.cookies.get(store.SESSION_COOKIE)
        response = await client.post("/api/auth/logout", headers=ORIGIN)
        assert response.json() == {"signed_out": 1}
        assert 'him_session=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()
        # The old cookie, replayed, is dead: the row is gone, not just the cookie
        replay = await client.get(PROTECTED, cookies={store.SESSION_COOKIE: value})
        assert replay.status_code == 401
        async with maker() as db:
            assert (await db.execute(select(AuthSession))).scalars().all() == []

    async def test_sign_out_everywhere(self, auth_app, with_password):
        app, _ = auth_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as here, \
                AsyncClient(transport=transport, base_url="http://test") as there:
            await log_in(here)
            await log_in(there)
            response = await here.post("/api/auth/logout-all", headers=ORIGIN)
            assert response.json() == {"signed_out": 2}
            assert (await there.get(PROTECTED)).status_code == 401

    async def test_a_session_ends_at_its_absolute_limit(self, client, with_password, maker, monkeypatch):
        await log_in(client)
        later = datetime.utcnow() + timedelta(days=settings.auth_session_max_days, minutes=1)
        # Kept busy the whole time, so only the absolute limit can end it
        async with maker() as db:
            row = (await db.execute(select(AuthSession))).scalar_one()
            row.last_seen_at = later - timedelta(minutes=5)
            await db.commit()
        monkeypatch.setattr(store, "_now", lambda: later)
        response = await client.get(PROTECTED)
        assert response.status_code == 401
        assert "expired" in response.json()["detail"]
        async with maker() as db:
            assert (await db.execute(select(AuthSession))).scalars().all() == []

    async def test_a_session_ends_after_sitting_idle(self, client, with_password, monkeypatch):
        await log_in(client)
        later = datetime.utcnow() + timedelta(days=settings.auth_session_idle_days, minutes=1)
        monkeypatch.setattr(store, "_now", lambda: later)
        assert (await client.get(PROTECTED)).status_code == 401

    async def test_use_keeps_a_session_alive_past_the_idle_limit(self, client, with_password, monkeypatch):
        await log_in(client)
        start = datetime.utcnow()
        step = timedelta(days=settings.auth_session_idle_days - 1)
        for visit in (1, 2):
            monkeypatch.setattr(store, "_now", lambda visit=visit: start + step * visit)
            assert (await client.get(PROTECTED)).status_code == 200

    async def test_changing_the_password_signs_every_browser_out(self, client, with_password, maker):
        await log_in(client)
        async with maker() as db:
            ended = await store.set_password(db, "a different long password")
        assert ended == 1
        assert (await client.get(PROTECTED)).status_code == 401
        assert (await log_in(client)).status_code == 401
        assert (await log_in(client, "a different long password")).status_code == 200


class TestSameOriginRule:
    async def test_a_cookie_request_that_changes_something_needs_this_origin(
        self, client, with_password
    ):
        await log_in(client)
        # Reads are covered by SameSite and by there being no CORS
        assert (await client.get(PROTECTED)).status_code == 200
        assert (await client.post("/api/auth/logout")).status_code == 403
        cross = await client.post("/api/auth/logout", headers={"Origin": "https://elsewhere.example"})
        assert cross.status_code == 403
        # Refused, so still logged in
        assert (await client.get(PROTECTED)).status_code == 200
        assert (await client.post("/api/auth/logout", headers=ORIGIN)).status_code == 200

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    async def test_every_unsafe_method_is_held_to_it(self, client, with_password, method):
        await log_in(client)
        # A path that exists for none of these methods would be a 404/405
        # after the check; the check comes first for one that does exist
        response = await client.request(method, "/api/auth/logout")
        assert response.status_code in (403, 405)
        if method == "POST":
            assert response.status_code == 403


class TestTokens:
    async def test_a_token_is_a_credential(self, client, maker):
        headers = await mint(maker, scope="researcher", entity_id=None)
        response = await client.get(PROTECTED, headers=headers)
        assert response.status_code == 200
        who = (await client.get("/api/auth/session", headers=headers)).json()
        assert who == {
            "authenticated": True, "scope": "researcher", "via": "token",
            "name": "laptop", "entity_id": None, "expires_at": None,
        }

    async def test_a_token_needs_no_origin(self, client, maker):
        # Nothing attaches a bearer token unasked, so there is no other
        # site's request to rule out
        headers = await mint(maker, scope="researcher", entity_id=None)
        assert (await client.post("/api/auth/logout", headers=headers)).status_code == 200

    async def test_the_database_holds_a_digest_not_the_token(self, client, maker):
        headers = await mint(maker)
        value = headers["Authorization"].split(" ", 1)[1]
        assert value.startswith("him_") and len(value) > 40
        async with maker() as db:
            row = (await db.execute(select(ApiToken))).scalar_one()
        assert row.token_hash == store.digest(value)
        assert value not in (row.token_hash, row.id, row.name)

    async def test_an_entity_token_is_held_to_its_scope(self, client, maker):
        headers = await mint(maker)
        refused = await client.get(PROTECTED, headers=headers)
        assert refused.status_code == 403
        assert "entity-scoped" in refused.json()["detail"]
        assert (await client.post("/api/auth/logout-all", headers=headers)).status_code == 403
        who = (await client.get("/api/auth/session", headers=headers)).json()
        assert who["scope"] == "entity" and who["entity_id"] == "test-entity"

    async def test_a_revoked_token_is_refused(self, client, maker):
        headers = await mint(maker, scope="researcher", entity_id=None)
        assert (await client.get(PROTECTED, headers=headers)).status_code == 200
        async with maker() as db:
            assert [t.name for t in await store.revoke_tokens(db, "laptop")] == ["laptop"]
        response = await client.get(PROTECTED, headers=headers)
        assert response.status_code == 401
        assert "revoked" in response.json()["detail"]

    @pytest.mark.parametrize("header", [
        "Bearer him_notarealtoken", "Bearer ", "Bearer", "Basic dXNlcjpwYXNz",
        "him_notarealtoken", "bearer nothim_x", "",
    ])
    async def test_a_bad_authorization_header_is_refused(self, client, header):
        response = await client.get(PROTECTED, headers={"Authorization": header})
        assert response.status_code == 401

    async def test_a_bad_token_is_not_rescued_by_a_good_cookie(self, client, with_password):
        # A credential that was offered is the one that is judged
        await log_in(client)
        assert (await client.get(PROTECTED)).status_code == 200
        response = await client.get(PROTECTED, headers={"Authorization": "Bearer him_wrong"})
        assert response.status_code == 401

    async def test_the_scheme_is_case_insensitive(self, client, maker):
        headers = await mint(maker, scope="researcher", entity_id=None)
        value = headers["Authorization"].split(" ", 1)[1]
        response = await client.get(PROTECTED, headers={"Authorization": f"bearer {value}"})
        assert response.status_code == 200

    async def test_last_used_is_stamped_but_not_on_every_request(self, client, maker, monkeypatch):
        headers = await mint(maker, scope="researcher", entity_id=None)
        start = datetime.utcnow().replace(microsecond=0)
        monkeypatch.setattr(store, "_now", lambda: start)
        await client.get(PROTECTED, headers=headers)
        monkeypatch.setattr(store, "_now", lambda: start + timedelta(seconds=20))
        await client.get(PROTECTED, headers=headers)
        async with maker() as db:
            assert (await db.execute(select(ApiToken))).scalar_one().last_used_at == start
        later = start + store.STAMP_INTERVAL + timedelta(seconds=1)
        monkeypatch.setattr(store, "_now", lambda: later)
        await client.get(PROTECTED, headers=headers)
        async with maker() as db:
            assert (await db.execute(select(ApiToken))).scalar_one().last_used_at == later

    async def test_a_token_in_the_query_string_is_not_a_credential(self, client, maker):
        headers = await mint(maker, scope="researcher", entity_id=None)
        value = headers["Authorization"].split(" ", 1)[1]
        for param in ("token", "access_token", "him_token"):
            assert (await client.get(PROTECTED, params={param: value})).status_code == 401


class TestTokenStore:
    async def test_an_entity_token_must_name_its_entity(self, maker):
        async with maker() as db:
            with pytest.raises(ValueError, match="bound to an entity"):
                await store.create_token(db, "x", "entity", None)
            with pytest.raises(ValueError, match="not bound"):
                await store.create_token(db, "x", "researcher", "test-entity")
            with pytest.raises(ValueError, match="Unknown scope"):
                await store.create_token(db, "x", "admin", None)
            with pytest.raises(ValueError, match="needs a name"):
                await store.create_token(db, "  ", "researcher", None)

    async def test_an_active_name_means_one_token(self, maker):
        async with maker() as db:
            await store.create_token(db, "laptop", "entity", "test-entity")
            with pytest.raises(ValueError, match="already exists"):
                await store.create_token(db, "laptop", "entity", "test-entity")
            await store.revoke_tokens(db, "laptop")
            # A revoked name can be used again: a replaced machine keeps its name
            await store.create_token(db, "laptop", "entity", "test-entity")
            assert len(await store.list_tokens(db)) == 2

    async def test_revoke_by_id_prefix_and_never_twice(self, maker):
        async with maker() as db:
            _value, row = await store.create_token(db, "laptop", "entity", "test-entity")
            assert await store.revoke_tokens(db, row.id[:4]) == []
            assert [t.id for t in await store.revoke_tokens(db, row.id[:8])] == [row.id]
            assert await store.revoke_tokens(db, row.id) == []
            assert await store.revoke_tokens(db, "") == []

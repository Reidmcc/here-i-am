"""
Closed by default (issue #396): walk every route the app serves and ask
each one, with no credential, for anything at all.

This is the test that keeps a new endpoint from shipping open. It doesn't
list the routes; it reads them off the app, so a route added next month is
walked without anyone editing this file. If it fails on your new route,
the route is reachable by anyone who can reach the server: don't add it to
a list here to make the failure go away. The only list is
`policy.PUBLIC_ROUTES`, and a route belongs there only if it is safe to
answer a stranger.
"""
import re

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from app.auth import policy, store
from app.main import app

pytestmark = pytest.mark.real_auth


def api_routes():
    return [route for route in app.routes if isinstance(route, APIRoute)]


def concrete(path_template: str) -> str:
    """A real path for a template: every parameter filled with a value
    that names nothing."""
    return re.sub(r"\{[^}]+\}", "nothing-by-this-name", path_template)


def every_method_and_route():
    return [
        (method, route.path)
        for route in api_routes()
        for method in sorted(route.methods or ())
    ]


PROTECTED = [
    pair for pair in every_method_and_route() if not policy.is_public(*pair)
]
RESEARCHER_ONLY = [
    (method, path) for method, path in PROTECTED
    if not policy.in_entity_scope(method, path) and (method, path) not in policy.ANY_PRINCIPAL_ROUTES
]
ENTITY_REACHABLE = [(m, p) for m, p in PROTECTED if policy.in_entity_scope(m, p)]


@pytest.fixture
async def client(auth_app):
    served, _maker = auth_app
    async with AsyncClient(transport=ASGITransport(app=served), base_url="http://test") as c:
        yield c


@pytest.fixture
async def entity_token(auth_app):
    _app, maker = auth_app
    async with maker() as db:
        value, _row = await store.create_token(db, "walker", "entity", "test-entity")
    return {"Authorization": f"Bearer {value}"}


class TestTheWalkCoversTheApp:
    def test_everything_the_app_serves_is_an_api_route_or_the_frontend(self):
        """The auth dependency covers API routes. Anything else on the app
        (a raw Starlette route, a second mount, a websocket) would be
        outside it, so there must be none: only the frontend's static
        mount, which holds no data."""
        others = [route for route in app.routes if not isinstance(route, APIRoute)]
        assert len(others) == 1, [getattr(r, "path", r) for r in others]
        assert isinstance(others[0], Mount) and others[0].name == "frontend"

    def test_the_app_carries_the_dependency(self):
        from app.auth import authenticate

        assert [d.dependency for d in app.router.dependencies] == [authenticate]
        for route in api_routes():
            assert authenticate in [d.call for d in route.dependant.dependencies], route.path

    def test_the_walk_is_not_empty(self):
        # A walk over nothing passes; these are here so that can't happen
        assert len(PROTECTED) > 60
        assert ("POST", "/mcp") in ENTITY_REACHABLE
        assert ("POST", "/api/claude-code/retrieve") in ENTITY_REACHABLE
        assert ("DELETE", "/api/conversations/{conversation_id}") in RESEARCHER_ONLY
        assert ("POST", "/api/chat/stream") in RESEARCHER_ONLY

    def test_public_routes_all_exist(self):
        # A public entry naming no route would be a hole waiting for one
        assert policy.PUBLIC_ROUTES <= set(every_method_and_route())
        assert policy.ANY_PRINCIPAL_ROUTES <= set(every_method_and_route())
        assert policy.ENTITY_ROUTES <= set(every_method_and_route())

    def test_the_entity_list_is_exactly_the_claude_code_routes(self):
        """An entity token's reach is a list of routes, not a path prefix,
        so a route added to the Claude Code router starts out closed to
        entity tokens. This is where that addition is noticed: it fails
        until the new route is either listed in policy.ENTITY_ROUTES (after
        checking that it binds its caller to the token's entity before it
        reads or writes; see routes/claude_code.py) or moved to another
        router because it is the researcher's."""
        claude_code = {
            (method, path) for method, path in every_method_and_route()
            if path.startswith("/api/claude-code/") or path == "/mcp"
        }
        assert claude_code == set(policy.ENTITY_ROUTES)

    def test_nothing_is_both_public_and_scoped(self):
        assert not policy.PUBLIC_ROUTES & policy.ENTITY_ROUTES
        assert not policy.PUBLIC_ROUTES & policy.ANY_PRINCIPAL_ROUTES


class TestWithoutACredential:
    @pytest.mark.parametrize("method,path", PROTECTED)
    async def test_route_answers_401(self, client, method, path):
        response = await client.request(method, concrete(path))
        assert response.status_code == 401, f"{method} {path} answered a stranger"

    @pytest.mark.parametrize("method,path", PROTECTED)
    async def test_route_answers_401_to_a_made_up_token(self, client, method, path):
        response = await client.request(
            method, concrete(path), headers={"Authorization": "Bearer him_made_up"}
        )
        assert response.status_code == 401, f"{method} {path} took a made-up token"

    async def test_a_well_formed_body_does_not_reach_the_handler(self, client):
        """What this proves is that no handler runs. It does not prove the
        body goes unread: FastAPI reads and parses a request's body before
        it resolves dependencies, so a stranger's body is buffered and
        parsed, and a malformed one is answered 422 before the 401. No data
        is in either answer. See docs/authentication.md, "What it does not
        do"."""
        response = await client.post(
            "/api/claude-code/retrieve", json={"session_id": "s1", "prompt": "hello"}
        )
        assert response.status_code == 401

    async def test_a_malformed_body_is_told_only_that_it_is_malformed(self, client):
        response = await client.post(
            "/api/chat/stream", content=b"{bad", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert "json_invalid" in response.text
        assert "conversation" not in response.text.lower()


class TestWithAnEntityToken:
    @pytest.mark.parametrize("method,path", RESEARCHER_ONLY)
    async def test_researcher_route_answers_403(self, client, entity_token, method, path):
        response = await client.request(method, concrete(path), headers=entity_token)
        assert response.status_code == 403, f"{method} {path} answered an entity token"

    @pytest.mark.parametrize("method,path", ENTITY_REACHABLE)
    async def test_claude_code_route_lets_it_through(self, client, entity_token, method, path):
        # Past the check, whatever the handler then says of an empty request
        # (404 with the mode off, 405, 422): anything but a refusal
        response = await client.request(method, concrete(path), headers=entity_token)
        assert response.status_code not in (401, 403), f"{method} {path}"


class TestAnEntityTokenThatNamesNoEntity:
    """`store.create_token` never makes one. A row edited by hand could be
    one, and in the binding checks "no entity" reads as "the researcher,
    bound to nobody": it must never get that far."""

    @pytest.mark.parametrize("entity_id", [None, ""])
    async def test_it_is_refused_everywhere(self, client, auth_app, entity_id):
        from app.models.auth import ApiToken

        _app, maker = auth_app
        async with maker() as db:
            value, row = await store.create_token(db, "edited", "entity", "test-entity")
            (await db.get(ApiToken, row.id)).entity_id = entity_id
            await db.commit()
        headers = {"Authorization": f"Bearer {value}"}
        for method, path in sorted(policy.ENTITY_ROUTES) + [("GET", "/api/auth/session")]:
            response = await client.request(method, concrete(path), headers=headers, json={})
            assert response.status_code == 401, f"{method} {path}"


class TestTheStaticMount:
    """The one thing served without a credential is the page. The
    directory it lives in also holds what the page is built and tested
    with, and anything dropped there later."""

    @pytest.mark.parametrize("path", ["/", "/index.html", "/css/styles.css", "/js/api.js",
                                      "/js/app-modular.js", "/js/modules/auth.js"])
    async def test_the_page_and_its_files_are_served(self, client, path):
        assert (await client.get(path)).status_code == 200

    @pytest.mark.parametrize("path", [
        "/package.json", "/package-lock.json", "/eslint.config.mjs", "/vitest.config.js",
        "/node_modules/.package-lock.json", "/node_modules/vitest/package.json",
        "/js/__tests__/auth.test.js", "/js/__tests__/setup.js",
        "/.env", "/.gitignore", "/js/.hidden", "/css/../package.json",
        "/coverage/index.html", "/notes/Kira/index.md",
    ])
    async def test_nothing_else_in_the_directory_is(self, client, path):
        response = await client.get(path)
        assert response.status_code == 404, path

    def test_the_rule_itself(self):
        from app.main import FrontendFiles

        served = ["", ".", "index.html", "css/styles.css", "js/api.js", "js\\modules\\auth.js"]
        refused = ["package.json", "node_modules/x/index.js", "js/__tests__/a.test.js",
                   "js\\__tests__\\a.test.js", "css/../package.json", ".env", "js/.secret",
                   "index.html.bak", "notes/index.md", "coverage/index.html"]
        for path in served:
            assert FrontendFiles.is_served(path), path
        for path in refused:
            assert not FrontendFiles.is_served(path), path


class TestFraming:
    @pytest.mark.parametrize("path", ["/", "/api/health", "/api/config/presets"])
    async def test_no_response_may_be_framed(self, client, path):
        response = await client.get(path)
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


class TestPathsCannotBeTalkedAround:
    @pytest.mark.parametrize("path", [
        "/api/health/../config/presets",
        "/api/health/%2e%2e/config/presets",
        "/api/auth/login/../../config/presets",
        "//api/config/presets",
        "/api/config/presets/",
        "/API/config/presets",
        "/api/health?next=/api/config/presets",
    ])
    async def test_a_public_prefix_buys_nothing(self, client, path):
        response = await client.get(path)
        # Either it is not the protected route at all (404, a redirect to
        # it, or health itself answering only its own liveness), or it is
        # and says 401. It is never the protected route's content.
        assert "presets" not in response.text
        assert response.status_code in (200, 307, 401, 404)
        if response.status_code == 200:
            assert response.json() == {"status": "healthy", "version": "0.1.0"}

"""
What the server exposes apart from authentication (issue #396, phase 0):
where it listens, no CORS, no docs pages by default, and a health check
that says nothing about the configuration. What a request needs in order
to be answered at all is tests/test_auth_route_walk.py.
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.auth import authenticate
from app.config import Settings
from app.main import add_api_docs, app
from run import server_options


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestListening:
    def test_defaults_to_loopback_with_reload(self, monkeypatch):
        monkeypatch.delenv("HIM_HOST", raising=False)
        monkeypatch.delenv("HIM_RELOAD", raising=False)
        options = server_options(Settings(_env_file=None))
        assert options == {"host": "127.0.0.1", "port": 8000, "reload": True}

    def test_env_opts_in_to_every_interface_and_off_reload(self, monkeypatch):
        monkeypatch.setenv("HIM_HOST", "0.0.0.0")
        monkeypatch.setenv("HIM_RELOAD", "false")
        options = server_options(Settings(_env_file=None))
        assert options["host"] == "0.0.0.0"
        assert options["reload"] is False


class TestNoCors:
    async def test_cross_origin_read_gets_no_allow_origin(self, client):
        response = await client.get(
            "/api/health", headers={"Origin": "https://elsewhere.example"}
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

    async def test_cross_origin_read_with_cookie_gets_no_allow_origin(self, client):
        # The case the old middleware answered by echoing the caller's origin
        response = await client.get(
            "/api/health",
            headers={"Origin": "https://elsewhere.example", "Cookie": "session=x"},
        )
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers

    async def test_preflight_is_not_granted(self, client):
        response = await client.options(
            "/api/conversations/",
            headers={
                "Origin": "https://elsewhere.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-methods" not in response.headers


class TestDocsPages:
    def test_off_by_default(self):
        assert Settings(_env_file=None).api_docs_enabled is False
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_not_served(self, client, path):
        response = await client.get(path)
        assert response.status_code == 404

    def _docs_app(self) -> FastAPI:
        docs_app = FastAPI(
            dependencies=[Depends(authenticate)],
            docs_url=None, redoc_url=None, openapi_url=None,
        )
        add_api_docs(docs_app)
        return docs_app

    def test_setting_registers_them_as_ordinary_routes(self):
        # FastAPI's built-in docs routes are plain Starlette routes, which
        # an app-level dependency doesn't cover; these are API routes, so
        # they are behind the same check as everything else
        routes = {r.path: r for r in self._docs_app().routes}
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert isinstance(routes[path], APIRoute)

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_enabled_pages_answer_the_researcher(self, path):
        transport = ASGITransport(app=self._docs_app())
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get(path)).status_code == 200

    @pytest.mark.real_auth
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_enabled_pages_refuse_a_stranger(self, path):
        transport = ASGITransport(app=self._docs_app())
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get(path)).status_code == 401


class TestHealth:
    async def test_liveness_only(self, client):
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "version": "0.1.0"}


class TestRouteOrder:
    def test_frontend_mount_is_last(self):
        # A mount at "/" matches every path, so a route after it is dead
        from starlette.routing import Mount

        mounts = [i for i, r in enumerate(app.routes) if isinstance(r, Mount) and r.path == ""]
        assert mounts == [len(app.routes) - 1]

    async def test_presets_reachable(self, client):
        response = await client.get("/api/config/presets")
        assert response.status_code == 200
        assert response.json()["presets"]

"""
What the server exposes before authentication exists (issue #396, phase 0):
where it listens, no CORS, no docs pages, and a health check that says
nothing about the configuration.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import api_docs_urls, app
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

    def test_setting_turns_them_on(self):
        assert api_docs_urls(True) == {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }


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

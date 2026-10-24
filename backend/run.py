#!/usr/bin/env python3
"""
Run the Here I Am application.
"""
import uvicorn


def server_options(settings) -> dict:
    """uvicorn's options from settings: HIM_HOST (default 127.0.0.1) and
    HIM_RELOAD (default on). See issue #396."""
    return {
        "host": settings.him_host,
        "port": 8000,
        "reload": settings.him_reload,
    }


if __name__ == "__main__":
    from app.config import settings

    uvicorn.run("app.main:app", **server_options(settings))

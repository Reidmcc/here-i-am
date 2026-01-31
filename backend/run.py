#!/usr/bin/env python3
"""
Run the Here I Am application.

Server configuration is controlled via environment variables:
- SERVER_HOST: Host to bind to (default: 127.0.0.1)
- SERVER_PORT: Port to listen on (default: 8000)

For remote server deployment, also set:
- AUTH_ENABLED=true
- AUTH_PASSWORD=your_secure_password
"""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
    )

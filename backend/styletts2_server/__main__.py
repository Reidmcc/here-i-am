"""
Entry point for running the StyleTTS 2 server as a module.

Usage:
    python -m styletts2_server
    python -m styletts2_server --port 8021 --allow-origin https://<host>.<tailnet>.ts.net
"""

import argparse

import uvicorn

from voice_common.origin_guard import set_allowed_origins_env


def main():
    parser = argparse.ArgumentParser(
        description="Run the StyleTTS 2 TTS server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind the server to. Keep the default: the browser on this "
             "machine calls the server directly, and it has no authentication.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="An origin whose pages may call this server, besides the local app "
             "(http://localhost:8000). Repeatable. Example: https://him.example.ts.net",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8021,
        help="Port to run the server on",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (use 1 for GPU)",
    )

    args = parser.parse_args()
    origins = set_allowed_origins_env(args.allow_origin)

    print(f"""
+==============================================================+
|                    StyleTTS 2 Server                         |
+==============================================================+
|  Starting server at http://{args.host}:{args.port}                    |
|                                                              |
|  The StyleTTS 2 model will be downloaded on first run.       |
|  A GPU is strongly recommended for acceptable performance.   |
|                                                              |
|  Press Ctrl+C to stop the server.                            |
+==============================================================+
""")
    print(f"Pages allowed to call it: {', '.join(origins)}")

    uvicorn.run(
        "styletts2_server.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

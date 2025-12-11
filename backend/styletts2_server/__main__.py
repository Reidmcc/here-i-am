"""
Entry point for running the StyleTTS 2 server as a module.

Usage:
    python -m styletts2_server
    python -m styletts2_server --port 8021 --host 0.0.0.0
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="Run the StyleTTS 2 TTS server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to",
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

    uvicorn.run(
        "styletts2_server.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

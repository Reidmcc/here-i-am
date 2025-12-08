"""
Entry point for running the XTTS server as a module.

Usage:
    python -m xtts_server
    python -m xtts_server --port 8020 --host 0.0.0.0
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="Run the XTTS v2 TTS server",
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
        default=8020,
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
╔══════════════════════════════════════════════════════════════╗
║                    XTTS v2 Server                            ║
╠══════════════════════════════════════════════════════════════╣
║  Starting server at http://{args.host}:{args.port}                     ║
║                                                              ║
║  The XTTS model will be downloaded on first run (~2GB).      ║
║  A GPU is recommended for faster synthesis.                  ║
║                                                              ║
║  Press Ctrl+C to stop the server.                            ║
╚══════════════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "xtts_server.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

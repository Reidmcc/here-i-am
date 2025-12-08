#!/usr/bin/env python3
"""
Run the XTTS v2 TTS Server

This script starts the local XTTS server that provides text-to-speech
functionality for the Here I Am application.

Prerequisites:
    pip install -r requirements-xtts.txt

Usage:
    python run_xtts.py
    python run_xtts.py --port 8020
    python run_xtts.py --help

The server will:
    1. Download the XTTS v2 model on first run (~2GB)
    2. Start a FastAPI server on port 8020 (default)
    3. Accept TTS requests from the main Here I Am application

Once running, configure the main app with:
    XTTS_ENABLED=true
    XTTS_API_URL=http://localhost:8020
"""

import sys
import os

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Check for required dependencies
def check_dependencies():
    missing = []

    try:
        import torch
    except ImportError:
        missing.append("torch")

    try:
        import TTS
    except ImportError:
        missing.append("TTS")

    try:
        import scipy
    except ImportError:
        missing.append("scipy")

    if missing:
        print("=" * 60)
        print("ERROR: Missing required dependencies for XTTS server")
        print("=" * 60)
        print()
        print("Please install the XTTS dependencies:")
        print()
        print("    pip install -r requirements-xtts.txt")
        print()
        print("Missing packages:", ", ".join(missing))
        print()
        print("Note: For GPU support, install PyTorch with CUDA:")
        print("    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118")
        print()
        sys.exit(1)


if __name__ == "__main__":
    check_dependencies()

    from xtts_server.__main__ import main
    main()

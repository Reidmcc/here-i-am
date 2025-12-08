#!/usr/bin/env python3
"""
Run the XTTS v2 TTS Server

This script starts the local XTTS server that provides text-to-speech
functionality for the Here I Am application.

Prerequisites:
    1. Install PyTorch first (required):
       pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
       (or use /cpu for CPU-only)

    2. Install TTS and other dependencies:
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


def check_dependencies():
    """Check for required dependencies and provide helpful error messages."""
    missing = []
    suggestions = []

    # Check PyTorch
    try:
        import torch
        print(f"[OK] PyTorch {torch.__version__} found")
        if torch.cuda.is_available():
            print(f"[OK] CUDA available: {torch.cuda.get_device_name(0)}")
        else:
            print("[INFO] CUDA not available, will use CPU (slower)")
    except ImportError:
        missing.append("torch")
        suggestions.append(
            "Install PyTorch first:\n"
            "  GPU:  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118\n"
            "  CPU:  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
        )

    # Check TTS library
    try:
        import TTS
        print(f"[OK] TTS library found")
    except ImportError:
        missing.append("TTS (coqui-tts)")
        suggestions.append(
            "Install TTS library:\n"
            "  pip install coqui-tts\n"
            "  Or if that fails: pip install TTS"
        )

    # Check scipy
    try:
        import scipy
        print(f"[OK] scipy {scipy.__version__} found")
    except ImportError:
        missing.append("scipy")
        suggestions.append("  pip install scipy")

    if missing:
        print()
        print("=" * 60)
        print("ERROR: Missing required dependencies")
        print("=" * 60)
        print()
        print("Missing packages:", ", ".join(missing))
        print()
        print("To fix this:")
        print()
        for suggestion in suggestions:
            print(suggestion)
            print()
        print("Or install all at once (after PyTorch):")
        print("  pip install -r requirements-xtts.txt")
        print()
        sys.exit(1)

    print()
    return True


if __name__ == "__main__":
    print("XTTS v2 Server - Dependency Check")
    print("=" * 40)
    check_dependencies()

    from xtts_server.__main__ import main
    main()

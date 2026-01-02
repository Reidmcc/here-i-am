#!/usr/bin/env python3
"""
Run the Whisper STT Server

This script starts the local Whisper server that provides speech-to-text
functionality for the Here I Am application.

Prerequisites:
    1. Install PyTorch first (required):
       pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
       (or use /cpu for CPU-only)

    2. Install faster-whisper and other dependencies:
       pip install -r requirements-whisper.txt

Usage:
    python run_whisper.py
    python run_whisper.py --port 8030
    python run_whisper.py --model distil-large-v3  # Faster but slightly lower quality
    python run_whisper.py --help

The server will:
    1. Download the Whisper model on first run (~3GB for large-v3)
    2. Start a FastAPI server on port 8030 (default)
    3. Accept transcription requests from the main Here I Am application

Once running, configure the main app with:
    WHISPER_ENABLED=true
    WHISPER_API_URL=http://localhost:8030
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
            print(f"[OK] CUDA version: {torch.version.cuda}")
        else:
            print("[INFO] CUDA not available, will use CPU (slower)")
    except ImportError:
        missing.append("torch")
        suggestions.append(
            "Install PyTorch first:\n"
            "  GPU:  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118\n"
            "  CPU:  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
        )

    # Check faster-whisper
    try:
        import faster_whisper
        print(f"[OK] faster-whisper found")
    except ImportError:
        missing.append("faster-whisper")
        suggestions.append(
            "Install faster-whisper:\n"
            "  pip install faster-whisper"
        )

    # Check FastAPI and uvicorn
    try:
        import fastapi
        import uvicorn
        print(f"[OK] FastAPI and uvicorn found")
    except ImportError:
        missing.append("fastapi/uvicorn")
        suggestions.append("  pip install fastapi uvicorn[standard]")

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
        print("  pip install -r requirements-whisper.txt")
        print()
        sys.exit(1)

    print()
    return True


if __name__ == "__main__":
    print("Whisper STT Server - Dependency Check")
    print("=" * 40)
    check_dependencies()

    from whisper_server.__main__ import main
    main()

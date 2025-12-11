#!/usr/bin/env python3
"""
Run the StyleTTS 2 TTS Server

This script starts the local StyleTTS 2 server that provides text-to-speech
functionality for the Here I Am application.

Prerequisites:
    1. Install PyTorch first (required):
       pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
       (or use /cpu for CPU-only)

    2. Install StyleTTS 2 and other dependencies:
       pip install -r requirements-styletts2.txt

Usage:
    python run_styletts2.py
    python run_styletts2.py --port 8021
    python run_styletts2.py --help

The server will:
    1. Download the StyleTTS 2 model on first run
    2. Start a FastAPI server on port 8021 (default)
    3. Accept TTS requests from the main Here I Am application

Once running, configure the main app with:
    STYLETTS2_ENABLED=true
    STYLETTS2_API_URL=http://localhost:8021
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

    # Check StyleTTS 2 library
    try:
        import styletts2
        print(f"[OK] StyleTTS 2 library found")
    except ImportError:
        missing.append("styletts2")
        suggestions.append(
            "Install StyleTTS 2:\n"
            "  pip install styletts2\n"
            "  Or from source: pip install git+https://github.com/yl4579/StyleTTS2.git"
        )

    # Check scipy
    try:
        import scipy
        print(f"[OK] scipy {scipy.__version__} found")
    except ImportError:
        missing.append("scipy")
        suggestions.append("  pip install scipy")

    # Check phonemizer (required for StyleTTS 2)
    try:
        import phonemizer
        print(f"[OK] phonemizer found")
    except ImportError:
        missing.append("phonemizer")
        suggestions.append(
            "Install phonemizer:\n"
            "  pip install phonemizer\n"
            "  Note: phonemizer requires espeak-ng:\n"
            "    Ubuntu/Debian: sudo apt install espeak-ng\n"
            "    macOS: brew install espeak-ng\n"
            "    Windows: Download from https://github.com/espeak-ng/espeak-ng/releases"
        )

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
        print("  pip install -r requirements-styletts2.txt")
        print()
        sys.exit(1)

    print()
    return True


if __name__ == "__main__":
    print("StyleTTS 2 Server - Dependency Check")
    print("=" * 40)
    check_dependencies()

    from styletts2_server.__main__ import main
    main()

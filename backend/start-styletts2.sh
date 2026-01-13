#!/usr/bin/env bash
#
# Start the StyleTTS 2 TTS server with automatic venv activation.
#
# Usage:
#   ./start-styletts2.sh
#   ./start-styletts2.sh --port 8021
#
# This script will:
#   1. Look for a Python virtual environment in ./venv
#   2. Activate the venv
#   3. Run the StyleTTS 2 server
#
# Prerequisites:
#   - PyTorch installed (with CUDA support recommended)
#   - pip install -r requirements-styletts2.txt
#   - espeak-ng installed (if using espeak phonemizer)
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"

# Check if venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment not found at: $VENV_DIR"
    echo ""
    echo "Please create a virtual environment first:"
    echo "  cd $SCRIPT_DIR"
    echo "  python -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118"
    echo "  pip install -r requirements-styletts2.txt"
    echo ""
    exit 1
fi

# Check for activation script
if [ -f "$VENV_DIR/bin/activate" ]; then
    ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
else
    echo "ERROR: Cannot find venv activation script"
    echo "Expected: $VENV_DIR/bin/activate"
    exit 1
fi

# Activate the virtual environment
echo "Activating virtual environment: $VENV_DIR"
source "$ACTIVATE_SCRIPT"

# Verify we're in the venv
if [ -z "$VIRTUAL_ENV" ]; then
    echo "ERROR: Failed to activate virtual environment"
    exit 1
fi

echo "Using Python: $(which python)"
echo ""

# Run the StyleTTS 2 server
exec python run_styletts2.py "$@"

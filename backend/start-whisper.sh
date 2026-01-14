#!/usr/bin/env bash
#
# Start the Whisper STT server with automatic venv activation.
#
# Usage:
#   ./start-whisper.sh
#   ./start-whisper.sh --port 8030
#   ./start-whisper.sh --model distil-large-v3
#
# This script will:
#   1. Look for a Python virtual environment in ./venv
#   2. Activate the venv
#   3. Run the Whisper server
#
# Prerequisites:
#   - PyTorch installed (with CUDA support recommended for large models)
#   - pip install -r requirements-whisper.txt
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
    echo "  pip install -r requirements-whisper.txt"
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

# Run the Whisper server
exec python run_whisper.py "$@"

#!/usr/bin/env bash
#
# Set the login password and manage machine tokens for the Here I Am
# server, using the server's own virtual environment.
#
# Usage:
#   ./auth.sh init --entity <entity>
#   ./auth.sh token create --name <machine> --entity <entity>
#   ./auth.sh token list
#   ./auth.sh status
#   ./auth.sh --help
#
# The same as `python -m app.auth ...` with ./venv active. See
# docs/authentication.md.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "ERROR: Virtual environment not found at: $VENV_DIR"
    echo "Create it first (see start.sh), then run this again."
    exit 1
fi

source "$VENV_DIR/bin/activate"

exec python -m app.auth "$@"

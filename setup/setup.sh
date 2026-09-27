#!/usr/bin/env bash
# ============================================================
#  Piano Sheet Generator - environment setup (Linux / macOS)
#  Usage:  ./setup/setup.sh              full setup (core + separation)
#          ./setup/setup.sh --core-only  main env only
#          ./setup/setup.sh --cpu        separation env with CPU torch
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 1

# --- pick bootstrap python: project venv > PSG_PYTHON_SCORE > python3.x ---
BOOT=""
if [ -x ".venv-score/bin/python" ]; then
    BOOT=".venv-score/bin/python"
elif [ -n "${PSG_PYTHON_SCORE:-}" ] && [ -x "$PSG_PYTHON_SCORE" ]; then
    BOOT="$PSG_PYTHON_SCORE"
else
    for v in 3.12 3.11 3.10 3; do
        if command -v "python$v" >/dev/null 2>&1; then
            BOOT="python$v"
            break
        fi
    done
fi

if [ -z "$BOOT" ]; then
    echo "[ERROR] No Python found."
    echo "Install Python 3.10-3.12 first, e.g.:"
    echo "  Debian/Ubuntu : sudo apt install python3.11 python3.11-venv"
    echo "  Fedora/RHEL   : sudo dnf install python3.12"
    echo "  Arch          : sudo pacman -S python"
    exit 1
fi

# ensure venv module is available (Debian/Ubuntu split it out)
if ! "$BOOT" -c "import venv" >/dev/null 2>&1; then
    echo "[ERROR] Python venv module missing."
    echo "Install it, e.g.:  sudo apt install python3-venv  (or python3.11-venv)"
    exit 1
fi

# ensure basic audio system libs (needed by soundfile/librosa on Linux)
if command -v apt-get >/dev/null 2>&1; then
    if ! ldconfig -p 2>/dev/null | grep -q libsndfile; then
        echo "[NOTE] libsndfile not found. If audio loading fails later, run:"
        echo "       sudo apt install libsndfile1 ffmpeg"
    fi
fi

echo "Bootstrap python: $BOOT"
exec "$BOOT" setup/install.py "$@"

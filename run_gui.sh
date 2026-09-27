#!/usr/bin/env bash
# ============================================================
#  Piano Sheet Generator - GUI launcher (Linux / macOS)
#  If the GUI does not start, run ./setup/setup.sh once first.
# ============================================================
cd "$(dirname "$0")" || exit 1

PYEXE=""
# 1) project venv created by setup.sh (preferred)
if [ -x ".venv-score/bin/python" ]; then
    PYEXE=".venv-score/bin/python"
# 2) user override
elif [ -n "${PSG_PYTHON_SCORE:-}" ] && [ -x "$PSG_PYTHON_SCORE" ]; then
    PYEXE="$PSG_PYTHON_SCORE"
# 3) system python
elif command -v python3 >/dev/null 2>&1; then
    PYEXE="python3"
else
    echo "[ERROR] Python not found. Please run ./setup/setup.sh first."
    exit 1
fi

echo "Starting Piano Sheet Generator ..."
echo "Python: $PYEXE"
exec "$PYEXE" run.py "$@"

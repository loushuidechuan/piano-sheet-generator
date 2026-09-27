#!/usr/bin/env bash
# ============================================================
#  Piano Sheet Generator - environment setup entry (Linux / macOS)
#  Thin wrapper: forwards everything to setup/setup.sh
#  Usage:  ./setup.sh              full setup (core + separation)
#          ./setup.sh --core-only  main env only
#          ./setup.sh --cpu        separation env with CPU torch
#          ./setup.sh --no-models  skip model/weight download
# ============================================================
cd "$(dirname "$0")" || exit 1
exec bash setup/setup.sh "$@"

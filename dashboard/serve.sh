#!/usr/bin/env bash
# Serve the dashboard from the REPO ROOT so fetch("data/...") resolves.
# Run from anywhere:  bash dashboard/serve.sh   (or  ./dashboard/serve.sh)
set -e
PORT="${1:-8000}"
# repo root = parent of the folder this script lives in
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "Serving repo root: $ROOT"
echo "Open →  http://localhost:$PORT/dashboard/"
python3 -m http.server "$PORT"
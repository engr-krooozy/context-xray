#!/usr/bin/env bash
# Starts the Context X-ray demo on http://localhost:8080
set -e
cd "$(dirname "$0")"

# Needs Python 3.11+ (google-adk 2.x). macOS's default python3 is often 3.9.
find_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$py"; return 0
    fi
  done
  return 1
}

# (Re)build the venv if it's missing, on an old Python, or a previous install failed.
if [ ! -x .venv/bin/uvicorn ] ||
   ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  PY="$(find_python)" || { echo "Python 3.11+ is required. Install it (e.g. 'uv python install 3.12') and retry." >&2; exit 1; }
  echo "Setting up .venv with $($PY --version)..."
  rm -rf .venv
  "$PY" -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

[ -f .env ] || cp .env.example .env
exec ./.venv/bin/uvicorn xray.server:app --port "${PORT:-8080}"

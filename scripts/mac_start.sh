#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8010}"
HOST="${HOST:-127.0.0.1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

mkdir -p data

echo "Starting AI English Lab at http://${HOST}:${PORT}"
python -m uvicorn app:app --reload --host "$HOST" --port "$PORT"

#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: bash scripts/deploy_to_server.sh user@server [commit message]"
  echo "Example: bash scripts/deploy_to_server.sh ubuntu@1.2.3.4 \"update reading layout\""
  exit 1
fi

SERVER="$1"
MESSAGE="${2:-update app}"
REPO_URL="${REPO_URL:-https://github.com/guai6mmt/ai-english-intensive-reading-lab.git}"
REMOTE_DIR="${REMOTE_DIR:-/opt/ai-english-intensive-reading-lab}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8010}"

cd "$(dirname "$0")/.."

echo "Checking local files..."
python -B -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['app.py','V6_english_analyzer.py']]"
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo "Node is not installed; skipping static/app.js syntax check."
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  git add .gitignore .env.example README.md requirements.txt app.py V6_english_analyzer.py scripts static
  git commit -m "$MESSAGE"
else
  echo "No local changes to commit."
fi

git push origin "$BRANCH"

echo "Deploying to ${SERVER}:${REMOTE_DIR} on port ${PORT}..."
ssh "$SERVER" "set -e
  if [ ! -d '${REMOTE_DIR}/.git' ]; then
    sudo mkdir -p '${REMOTE_DIR}'
    sudo chown \$(whoami):\$(whoami) '${REMOTE_DIR}'
    git clone '${REPO_URL}' '${REMOTE_DIR}'
  fi
  cd '${REMOTE_DIR}'
  git fetch origin '${BRANCH}'
  git checkout '${BRANCH}'
  git pull --ff-only origin '${BRANCH}'
  PORT='${PORT}' bash scripts/server_install_or_update.sh
"

echo "Done. Open http://${SERVER#*@}:${PORT}"

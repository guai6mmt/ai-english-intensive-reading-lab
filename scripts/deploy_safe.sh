#!/usr/bin/env bash
# 在本地（Mac / Linux / Windows + Git Bash）发起的安全部署：
#   1. 提交本地改动 → 推送 GitHub
#   2. SSH 到服务器执行 server_safe_update.sh（拉取 → 校验 → 备份 data → 受控重启）
#
# 用法：
#   bash scripts/deploy_safe.sh user@server "本次修改说明"
# 例如：
#   bash scripts/deploy_safe.sh ubuntu@1.2.3.4 "fix dictation feedback layout"
#
# 与 deploy_to_server.sh 的差别：
#   - 远端使用 server_safe_update.sh，会先做语法/导入检查再 restart
#   - 远端会自动备份 data 目录到 data-backup-*.tar.gz（保留最近 5 份）
#   - 失败时会非零退出，方便 CI 或脚本检测
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: bash scripts/deploy_safe.sh user@server [commit message]"
  echo "Example: bash scripts/deploy_safe.sh ubuntu@1.2.3.4 \"update reading layout\""
  exit 1
fi

SERVER="$1"
MESSAGE="${2:-update app}"
REPO_URL="${REPO_URL:-https://github.com/guai6mmt/ai-english-intensive-reading-lab.git}"
REMOTE_DIR="${REMOTE_DIR:-/opt/ai-english-intensive-reading-lab}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8010}"

cd "$(dirname "$0")/.."

echo "==> 本地校验"
python -B -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['app.py','V6_english_analyzer.py']]"
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo "    （未检测到 node，跳过 static/app.js 语法检查）"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "==> 提交本地改动"
  git add .gitignore .env.example README.md requirements.txt app.py V6_english_analyzer.py scripts static
  git commit -m "$MESSAGE"
else
  echo "==> 工作区干净，无需新建提交"
fi

echo "==> 推送到 origin/${BRANCH}"
git push origin "$BRANCH"

echo "==> SSH 到 ${SERVER} 执行安全更新（服务会短暂重启）"
ssh "$SERVER" "set -e
  if [ ! -d '${REMOTE_DIR}/.git' ]; then
    echo '远端尚未克隆，首次部署...'
    sudo mkdir -p '${REMOTE_DIR}'
    sudo chown \$(whoami):\$(whoami) '${REMOTE_DIR}'
    git clone '${REPO_URL}' '${REMOTE_DIR}'
    cd '${REMOTE_DIR}'
    PORT='${PORT}' bash scripts/server_install_or_update.sh
  else
    cd '${REMOTE_DIR}'
    PORT='${PORT}' BRANCH='${BRANCH}' bash scripts/server_safe_update.sh
  fi
"

echo
echo "✓ 部署完成：http://${SERVER#*@}:${PORT}"

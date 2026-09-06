#!/usr/bin/env bash
# 一键更新：校验 → 停服完整备份 → 更新依赖 → 启动和健康检查。
set -euo pipefail
umask 077
APP_DIR="${ENGLISH_LAB_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
export ENGLISH_LAB_APP_DIR="$APP_DIR"
SERVICE_NAME="${SERVICE_NAME:-ai-english-lab}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8010}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
cd "$APP_DIR"
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '代码目录有未提交修改，请先提交；更新不会覆盖本地改动。' >&2; exit 1
fi
OLD_HEAD=$(git rev-parse HEAD)
WAS_ACTIVE=0
systemctl is-active --quiet "$SERVICE_NAME" && WAS_ACTIVE=1
STOPPED=0
CHANGED=0
rollback() {
  code=$?
  trap - EXIT
  if [ "$code" -ne 0 ]; then
    echo '更新失败，正在恢复旧代码；数据目录和备份保持原样。' >&2
    if [ "$CHANGED" = 1 ]; then
      git checkout --detach "$OLD_HEAD" || true
      "$APP_DIR/.venv/bin/python" -m pip install -r requirements.txt --quiet || true
    fi
    if [ "$STOPPED" = 1 ] && [ "$WAS_ACTIVE" = 1 ]; then
      $SUDO systemctl restart "$SERVICE_NAME" || true
      echo "旧版本已尝试启动，请检查 systemctl status $SERVICE_NAME。" >&2
    fi
  fi
  exit "$code"
}
trap rollback EXIT
# Fetch before downtime. No runtime paths or environment configuration are rewritten.
git fetch origin "$BRANCH"
git merge-base --is-ancestor "$OLD_HEAD" "origin/$BRANCH" || { echo '本地分支与远端分叉，停止更新。' >&2; exit 1; }
HELPERS=$(mktemp -d)
# Read the new backup helper before changing the checkout (also supports upgrades from old versions).
git show "origin/$BRANCH:scripts/storage_config.py" > "$HELPERS/storage_config.py"
git show "origin/$BRANCH:scripts/backup_data.py" > "$HELPERS/backup_data.py"
echo '正在停止服务并备份文章、数据库、音频、导入文件和配置。大音频库需要更长时间。'
$SUDO systemctl stop "$SERVICE_NAME"
STOPPED=1
"$PYTHON_BIN" "$HELPERS/backup_data.py"
CHANGED=1
git checkout "$BRANCH"
git merge --ff-only "origin/$BRANCH"
if [ ! -d .venv ]; then "$PYTHON_BIN" -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt --quiet
.venv/bin/python -B -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8-sig')) for p in [pathlib.Path('app.py'),*pathlib.Path('english_lab').glob('*.py')]]"
# Import using the persistent paths, never the repository's default data directory.
export ENGLISH_LAB_DATA_DIR="$("$PYTHON_BIN" scripts/storage_config.py ENGLISH_LAB_DATA_DIR)"
export MEDIA_STORAGE_ROOT="$("$PYTHON_BIN" scripts/storage_config.py MEDIA_STORAGE_ROOT)"
export MEDIA_IMPORT_ROOT="$("$PYTHON_BIN" scripts/storage_config.py MEDIA_IMPORT_ROOT)"
.venv/bin/python -B -c 'import app; assert app.app'
if command -v node >/dev/null 2>&1; then
  for file in static/app.js static/auth.js static/login.js static/media.js static/service-worker.js; do node --check "$file"; done
fi
$SUDO systemctl restart "$SERVICE_NAME"
for attempt in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/health/ready" >/dev/null; then
    STOPPED=0
    echo '更新完成，已有文章、音频及学习记录已保留。'
    exit 0
  fi
  sleep 1
done
echo '健康检查失败。' >&2
exit 1

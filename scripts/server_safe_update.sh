#!/usr/bin/env bash
# 服务器端安全增量更新：拉取新代码 → 语法校验 → 健康检查 → 平滑重启
# 用法（在服务器上执行）：
#   bash scripts/server_safe_update.sh
# 可选环境变量：
#   PORT          监听端口（默认 8010）
#   SERVICE_NAME  systemd 服务名（默认 ai-english-lab）
#   BRANCH        要拉取的分支（默认 main）
#   PYTHON_BIN    Python 可执行文件（默认 python3）
#   SKIP_BACKUP   设为 1 时跳过 data 备份
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8010}"
SERVICE_NAME="${SERVICE_NAME:-ai-english-lab}"
BRANCH="${BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SKIP_BACKUP="${SKIP_BACKUP:-0}"

cd "$APP_DIR"

echo "==> 1/6 当前服务状态"
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "    服务正在运行：$SERVICE_NAME"
else
  echo "    服务未运行（首次部署？将照常更新）"
fi

# 备份 data 目录（小项目数据量不大，几秒就能完成；用户数据是最重要的）
if [ "$SKIP_BACKUP" != "1" ] && [ -d "data" ]; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  BACKUP_FILE="data-backup-${STAMP}.tar.gz"
  echo "==> 2/6 备份 data → ${BACKUP_FILE}"
  tar -czf "${BACKUP_FILE}" data
  # 仅保留最近 5 份备份
  ls -1t data-backup-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
else
  echo "==> 2/6 已跳过 data 备份"
fi

echo "==> 3/6 拉取最新代码（${BRANCH}）"
# 保存本地未提交修改（如服务端编辑过 .env），避免冲突
git fetch origin "$BRANCH"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse "origin/${BRANCH}")
if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
  echo "    远端无新提交，仍执行依赖检查与可能的服务重启"
fi
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> 4/6 安装/更新依赖（不影响运行中的进程）"
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

echo "==> 5/6 语法 / 导入校验（不重启服务）"
python -B -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['app.py','V6_english_analyzer.py']]"
# 完整 import 测试，确保依赖齐全且模块结构未损坏
python -B -c "import importlib, sys; sys.path.insert(0, '.'); m = importlib.import_module('app'); assert hasattr(m, 'app'), 'FastAPI app 对象未找到'"
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
fi

echo "==> 6/6 平滑重启 systemd 服务"
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"

# 等待端口重新可用并做健康检查（最多 20 秒）
echo "    等待端口 ${PORT} 重新上线 ..."
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/library" >/dev/null 2>&1; then
    echo "    ✓ 服务已重新上线（${i}s 内恢复）"
    echo "Done. http://0.0.0.0:${PORT}"
    exit 0
  fi
  sleep 1
done

echo "    ✗ 警告：20s 内端口仍未响应。请检查："
echo "         sudo systemctl status ${SERVICE_NAME}"
echo "         journalctl -u ${SERVICE_NAME} -f"
exit 1

#!/usr/bin/env bash
# 公开仓库一键部署入口：下载/更新项目，然后调用完整安装器。
# 用法：curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | bash -s -- english.example.com
set -euo pipefail

REPOSITORY_URL="https://github.com/guai6mmt/ai-english-intensive-reading-lab.git"
APP_DIR="/opt/ai-english-intensive-reading-lab"
DOMAIN="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 运行，普通用户可执行：" >&2
  echo "curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | sudo bash -s -- 你的域名" >&2
  exit 2
fi

if [ -z "$DOMAIN" ]; then
  echo "错误：请在命令末尾填写已经解析到本服务器的域名。" >&2
  echo "示例：curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | bash -s -- english.example.com" >&2
  exit 2
fi

if ! [[ "$DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]; then
  echo "错误：域名只填写 english.example.com 这种格式，不要包含 https://、端口或路径。" >&2
  exit 2
fi

echo "==> 准备下载公开仓库"
apt-get update
apt-get install -y git ca-certificates curl

if [ -d "$APP_DIR/.git" ]; then
  echo "==> 检测到现有安装，安全更新代码"
  git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true
  git -C "$APP_DIR" remote set-url origin "$REPOSITORY_URL"
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" checkout main
  git -C "$APP_DIR" pull --ff-only origin main
elif [ -e "$APP_DIR" ]; then
  BACKUP_DIR="${APP_DIR}.backup-$(date +%Y%m%d-%H%M%S)"
  echo "==> 现有目录不是 Git 仓库，保留为备份：${BACKUP_DIR}"
  mv "$APP_DIR" "$BACKUP_DIR"
  git clone --branch main --single-branch "$REPOSITORY_URL" "$APP_DIR"
else
  git clone --branch main --single-branch "$REPOSITORY_URL" "$APP_DIR"
fi

git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true

echo "==> 启动一键配置"
DOMAIN="$DOMAIN" bash "$APP_DIR/scripts/server_install_or_update.sh"

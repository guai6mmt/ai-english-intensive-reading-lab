#!/usr/bin/env bash
# Debian / Ubuntu 服务器一键安装或重建配置。
# 推荐用法：sudo env DOMAIN=listen.example.com bash scripts/server_install_or_update.sh
# 仅本机部署：sudo bash scripts/server_install_or_update.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8010}"
DOMAIN="${DOMAIN:-}"
SERVICE_NAME="${SERVICE_NAME:-ai-english-lab}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DATA_DIR="${ENGLISH_LAB_DATA_DIR:-/srv/english-lab/data}"
MEDIA_DIR="${MEDIA_STORAGE_ROOT:-/srv/english-lab/media}"
IMPORT_DIR="${MEDIA_IMPORT_ROOT:-/srv/english-lab/import}"

if [ -n "${APP_USER:-}" ]; then
  APP_USER="$APP_USER"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  APP_USER="$SUDO_USER"
elif [ "$(id -u)" -eq 0 ]; then
  APP_USER="englishlab"
else
  APP_USER="$(id -un)"
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "错误：PORT 必须是 1-65535 之间的数字。" >&2
  exit 2
fi

if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]; then
  echo "错误：DOMAIN 只填写域名，不要包含 http://、路径或端口。" >&2
  exit 2
fi

if [ "$APP_USER" = "root" ]; then
  APP_USER="englishlab"
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  $SUDO useradd --system --user-group --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
APP_GROUP="${APP_GROUP:-$(id -gn "$APP_USER")}"

if [ "$(id -un)" = "$APP_USER" ]; then
  APP_IS_READABLE=1
elif [ "$(id -u)" -eq 0 ]; then
  if runuser -u "$APP_USER" -- test -r "$APP_DIR/app.py"; then APP_IS_READABLE=1; else APP_IS_READABLE=0; fi
elif sudo -u "$APP_USER" test -r "$APP_DIR/app.py"; then
  APP_IS_READABLE=1
else
  APP_IS_READABLE=0
fi
if [ "$APP_IS_READABLE" != "1" ]; then
  echo "错误：运行用户 ${APP_USER} 无法读取项目目录 ${APP_DIR}。" >&2
  echo "root 用户请把仓库克隆到 /opt/ai-english-intensive-reading-lab，而不是 /root 下。" >&2
  exit 2
fi

echo "==> 1/7 安装系统依赖"
$SUDO apt-get update
$SUDO apt-get install -y git python3 python3-venv python3-pip ffmpeg sqlite3 curl ca-certificates
if [ -n "$DOMAIN" ] && ! command -v caddy >/dev/null 2>&1; then
  # 使用 Caddy 官方 Debian/Ubuntu 软件源，避免发行版默认源没有 caddy 包。
  $SUDO apt-get install -y debian-keyring debian-archive-keyring apt-transport-https gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  $SUDO chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  $SUDO chmod o+r /etc/apt/sources.list.d/caddy-stable.list
  $SUDO apt-get update
  $SUDO apt-get install -y caddy
fi

echo "==> 2/7 创建 Python 环境并安装依赖"
cd "$APP_DIR"
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

echo "==> 3/7 创建数据、媒体和导入目录"
$SUDO install -d -m 750 -o "$APP_USER" -g "$APP_GROUP" "$DATA_DIR" "$MEDIA_DIR" "$IMPORT_DIR"
$SUDO install -d -m 750 -o "$APP_USER" -g "$APP_GROUP" "$MEDIA_DIR/originals" "$MEDIA_DIR/staging"

echo "==> 4/7 生成运行配置"
if [ ! -f ".env" ]; then
  touch .env
fi

# 清除旧安装从 .env.example 复制出的中文占位符；AI Key 统一在网页中填写。
sed -i '/=你的 .*\(API Key\|AccessKey\|Bucket\|GroupId\)/d' .env

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped_value="${value//\\/\\\\}"
  escaped_value="${escaped_value//&/\\&}"
  escaped_value="${escaped_value//|/\\|}"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

set_env_value "ENGLISH_LAB_DATA_DIR" "$DATA_DIR"
set_env_value "MEDIA_STORAGE_ROOT" "$MEDIA_DIR"
set_env_value "MEDIA_IMPORT_ROOT" "$IMPORT_DIR"
set_env_value "EXPOSE_API_DOCS" "false"
if [ -n "$DOMAIN" ]; then
  set_env_value "COOKIE_SECURE" "true"
else
  set_env_value "COOKIE_SECURE" "false"
fi
$SUDO chown "$APP_USER":"$APP_GROUP" .env
$SUDO chmod 600 .env

echo "==> 5/7 安装并启动 systemd 服务"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
$SUDO tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=AI English Intensive Reading Lab
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port ${PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1
User=${APP_USER}
Group=${APP_GROUP}
UMask=0077
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME"

echo "==> 6/7 检查应用健康状态"
READY=0
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/health/ready" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  echo "错误：服务在 30 秒内未通过健康检查。" >&2
  $SUDO systemctl status "$SERVICE_NAME" --no-pager || true
  exit 1
fi

echo "==> 7/7 配置访问入口"
if [ -n "$DOMAIN" ]; then
  CADDY_SNIPPET_DIR="/etc/caddy/Caddyfile.d"
  CADDY_SNIPPET="${CADDY_SNIPPET_DIR}/${SERVICE_NAME}.caddy"
  CADDYFILE="/etc/caddy/Caddyfile"
  $SUDO install -d -m 755 "$CADDY_SNIPPET_DIR"
  $SUDO tee "$CADDY_SNIPPET" >/dev/null <<EOF
${DOMAIN} {
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "same-origin"
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        -Server
    }
    reverse_proxy 127.0.0.1:${PORT}
}
EOF
  if [ ! -f "$CADDYFILE" ]; then
    printf 'import /etc/caddy/Caddyfile.d/*.caddy\n' | $SUDO tee "$CADDYFILE" >/dev/null
  elif ! grep -Fq 'import /etc/caddy/Caddyfile.d/*.caddy' "$CADDYFILE"; then
    printf '\nimport /etc/caddy/Caddyfile.d/*.caddy\n' | $SUDO tee -a "$CADDYFILE" >/dev/null
  fi
  $SUDO caddy validate --config "$CADDYFILE"
  $SUDO systemctl enable caddy
  $SUDO systemctl reload caddy || $SUDO systemctl restart caddy
  echo
  echo "安装完成：请访问 https://${DOMAIN} 创建管理员账号。"
  echo "如果 HTTPS 尚未生效，请确认域名已解析到本机，且防火墙开放 TCP 80/443。"
else
  echo
  echo "安装完成：应用仅监听 http://127.0.0.1:${PORT}。"
  echo "公网使用请重新运行：${SUDO:+sudo }env DOMAIN=你的域名 bash scripts/server_install_or_update.sh"
fi

echo "服务状态：${SUDO:+sudo }systemctl status ${SERVICE_NAME}"
echo "实时日志：${SUDO:+sudo }journalctl -u ${SERVICE_NAME} -f"

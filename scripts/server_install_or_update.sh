#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8010}"
SERVICE_NAME="${SERVICE_NAME:-ai-english-lab}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_USER="${APP_USER:-${SUDO_USER:-$(id -un)}}"

if [ "$APP_USER" = "root" ]; then
  APP_USER="englishlab"
  if ! id "$APP_USER" >/dev/null 2>&1; then
    sudo useradd --system --user-group --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  fi
fi
APP_GROUP="${APP_GROUP:-$(id -gn "$APP_USER")}"

cd "$APP_DIR"

sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg sqlite3

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p data
sudo chown -R "$APP_USER":"$APP_GROUP" data
sudo chmod 750 data

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it if you want server-side API keys."
fi
if [ -f ".env" ]; then
  sudo chown "$APP_USER":"$APP_GROUP" .env
  sudo chmod 600 .env
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=AI English Intensive Reading Lab
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port ${PORT}
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

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Server is running on 127.0.0.1:${PORT}."
echo "Use Caddy/Nginx with HTTPS; do not expose ${PORT} directly to the Internet."
echo "Check status: sudo systemctl status ${SERVICE_NAME}"
echo "View logs:    journalctl -u ${SERVICE_NAME} -f"

#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8010}"
SERVICE_NAME="${SERVICE_NAME:-ai-english-lab}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p data

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it if you want server-side API keys."
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=AI English Intensive Reading Lab
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow "$PORT"/tcp || true
fi

echo "Server is running on port ${PORT}."
echo "Check status: sudo systemctl status ${SERVICE_NAME}"
echo "View logs:    journalctl -u ${SERVICE_NAME} -f"

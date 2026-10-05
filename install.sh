#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SUDO_USER:-${USER}}"
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
PYTHON="$(command -v python3)"

if [[ "$(id -u)" -ne 0 ]]; then
	echo "Run with sudo: sudo ./install.sh" >&2
	exit 1
fi
if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
	echo "Install from a Git checkout so the updater can pull releases." >&2
	exit 1
fi

"${PYTHON}" -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/pip" install --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install --editable "${PROJECT_DIR}"
chown -R "${SERVICE_USER}" "${PROJECT_DIR}/.venv"

cat > /etc/systemd/system/rpi-utils.service <<UNIT
[Unit]
Description=Raspberry Pi Control Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
Restart=on-failure
RestartSec=3
EnvironmentFile=-${SERVICE_HOME}/.config/rpi-utils/env
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now rpi-utils.service
echo "Pi Console is running at http://127.0.0.1:8002"
echo "Optional Telegram settings: ${SERVICE_HOME}/.config/rpi-utils/env"

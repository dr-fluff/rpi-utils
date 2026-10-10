#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SUDO_USER:-${USER}}"
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
PYTHON="$(command -v python3)"
UPGRADE_HELPER="/usr/local/sbin/rpi-utils-system-upgrade"
SUDOERS_FILE="/etc/sudoers.d/rpi-utils-updater"

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

install -o root -g root -m 0755 "${PROJECT_DIR}/scripts/rpi-utils-system-upgrade" "${UPGRADE_HELPER}"
SUDOERS_TEMP="$(mktemp)"
trap 'rm -f "${SUDOERS_TEMP}"' EXIT
printf '%s ALL=(root) NOPASSWD: %s\n' "${SERVICE_USER}" "${UPGRADE_HELPER}" > "${SUDOERS_TEMP}"
visudo -cf "${SUDOERS_TEMP}"
install -o root -g root -m 0440 "${SUDOERS_TEMP}" "${SUDOERS_FILE}"

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
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable rpi-utils.service
systemctl restart rpi-utils.service
echo "Pi Console is running at http://127.0.0.1:8002"
echo "Optional Telegram settings: ${SERVICE_HOME}/.config/rpi-utils/env"

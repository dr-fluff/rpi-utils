#!/usr/bin/env bash
set -euo pipefail

echo "Starting Pi Console installation..."
STEP=0
TOTAL_STEPS=7
progress() {
	STEP=$((STEP + 1))
	printf '\n[%d/%d] %s\n' "${STEP}" "${TOTAL_STEPS}" "$1"
}

progress "Checking installer prerequisites"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SUDO_USER:-${USER}}"
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
PYTHON="$(command -v python3)"
UPGRADE_HELPER="/usr/local/sbin/rpi-utils-system-upgrade"
REBOOT_HELPER="/usr/local/sbin/rpi-utils-reboot"
SUDOERS_FILE="/etc/sudoers.d/rpi-utils-updater"

if [[ "$(id -u)" -ne 0 ]]; then
	echo "Run with sudo: sudo ./install.sh" >&2
	exit 1
fi
if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
	echo "Install from a Git checkout so the updater can pull releases." >&2
	exit 1
fi

progress "Creating Python virtual environment"
"${PYTHON}" -m venv "${PROJECT_DIR}/.venv"

progress "Upgrading pip (this may take a few minutes)"
"${PROJECT_DIR}/.venv/bin/pip" install --progress-bar on --upgrade pip

progress "Installing Pi Console and Python dependencies"
"${PROJECT_DIR}/.venv/bin/pip" install --progress-bar on --editable "${PROJECT_DIR}"
chown -R "${SERVICE_USER}" "${PROJECT_DIR}/.venv"

progress "Installing restricted system upgrade and reboot helpers"
install -o root -g root -m 0755 "${PROJECT_DIR}/scripts/rpi-utils-system-upgrade" "${UPGRADE_HELPER}"
install -o root -g root -m 0755 "${PROJECT_DIR}/scripts/rpi-utils-reboot" "${REBOOT_HELPER}"
SUDOERS_TEMP="$(mktemp)"
trap 'rm -f "${SUDOERS_TEMP}"' EXIT
printf '%s ALL=(root) NOPASSWD: %s, %s\n' \
	"${SERVICE_USER}" "${UPGRADE_HELPER}" "${REBOOT_HELPER}" > "${SUDOERS_TEMP}"
visudo -cf "${SUDOERS_TEMP}"
install -o root -g root -m 0440 "${SUDOERS_TEMP}" "${SUDOERS_FILE}"

progress "Configuring the systemd service"
cat > /etc/systemd/system/rpi-utils.service <<UNIT
[Unit]
Description=Raspberry Pi Control Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8002
Restart=on-failure
RestartSec=3
EnvironmentFile=-${SERVICE_HOME}/.config/rpi-utils/env
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

progress "Enabling and starting Pi Console"
systemctl daemon-reload
systemctl enable rpi-utils.service
systemctl restart rpi-utils.service
echo "Pi Console is running on port 8002. Open http://<pi-ip-address>:8002 from a device on the same private network."
echo "Optional Telegram settings: ${SERVICE_HOME}/.config/rpi-utils/env"

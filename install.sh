#!/usr/bin/env bash
set -euo pipefail

echo "Starting Pi Console installation..."
STEP=0
TOTAL_STEPS=8
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
AUTH_ENV_FILE="${SERVICE_HOME}/.config/rpi-utils/env"

if [[ "$(id -u)" -ne 0 ]]; then
	echo "Run with sudo: sudo ./install.sh" >&2
	exit 1
fi
if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
	echo "Install from a Git checkout so the updater can pull releases." >&2
	exit 1
fi
if [[ -z "${SERVICE_HOME}" ]]; then
	echo "Could not determine the home directory for ${SERVICE_USER}." >&2
	exit 1
fi

progress "Preparing dashboard sign-in"
install -d -o "${SERVICE_USER}" -m 0700 "$(dirname "${AUTH_ENV_FILE}")"
touch "${AUTH_ENV_FILE}"
chown "${SERVICE_USER}" "${AUTH_ENV_FILE}"
chmod 0600 "${AUTH_ENV_FILE}"
TEMP_PASSWORD=""
if ! grep -Eq '^[[:space:]]*RPI_UTILS_WEB_PASSWORD[[:space:]]*=[[:space:]]*[^[:space:]#]' "${AUTH_ENV_FILE}"; then
	TEMP_PASSWORD="$("${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(24))')"
	printf '\nRPI_UTILS_WEB_PASSWORD=%s\nRPI_UTILS_PASSWORD_CHANGE_REQUIRED=1\n' \
		"${TEMP_PASSWORD}" >> "${AUTH_ENV_FILE}"
	chown "${SERVICE_USER}" "${AUTH_ENV_FILE}"
	chmod 0600 "${AUTH_ENV_FILE}"
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
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8003 --proxy-headers --forwarded-allow-ips=127.0.0.1 --ws-max-size=65536
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
echo "Pi Console backend is listening on 127.0.0.1:8003. Configure Caddy for HTTPS on port 8002."
echo "Optional Telegram settings: ${SERVICE_HOME}/.config/rpi-utils/env"
LOGIN_HOST="$("${PYTHON}" - "${AUTH_ENV_FILE}" <<'PY'
import re
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text().splitlines():
    match = re.match(r"^\s*RPI_UTILS_ALLOWED_HOSTS\s*=\s*(.*?)\s*$", line)
    if not match:
        continue
    hosts = match.group(1).strip("\"'")
    host = hosts.split(",", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z0-9.-]+", host):
        print(host)
        break
PY
)"
if [[ -n "${LOGIN_HOST}" ]]; then
	echo "Login URL: https://${LOGIN_HOST}:8002/"
else
	echo "Local development URL: http://127.0.0.1:8002/"
	echo "For LAN access, set RPI_UTILS_ALLOWED_HOSTS and configure Caddy to proxy HTTPS port 8002 to 127.0.0.1:8003."
fi
if [[ -n "${TEMP_PASSWORD}" ]]; then
	echo
	echo "Temporary dashboard password (change it after first sign-in): ${TEMP_PASSWORD}"
else
	echo "Existing dashboard password preserved."
fi

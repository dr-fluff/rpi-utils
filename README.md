# Pi Console

A small Raspberry Pi control service with a local web dashboard, process controls, Telegram commands, and a Git-based updater.

## Run locally

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002
```

On the Pi, verify the service responds:

```sh
curl http://127.0.0.1:8002/api/status
```

The service listens only on the Pi's loopback address, so opening the Pi's LAN IP directly from your laptop will not work. Run the SSH tunnel from a terminal on the laptop, not in the Pi's shell. If the Pi's hostname does not resolve, run `hostname -I` on the Pi and use its Wi-Fi/Ethernet LAN address instead:

```sh
ssh -N -L 8002:127.0.0.1:8002 <pi-username>@<pi-ip-address>
```

Replace both placeholders with the Pi's SSH username and LAN address. The SSH password prompt requires that Pi user's login password; `Permission denied` means the username/password or SSH key is not accepted. First confirm `ssh <pi-username>@<pi-ip-address>` works, then retry with the tunnel command. Leave the tunnel terminal open and visit [http://127.0.0.1:8002](http://127.0.0.1:8002) on the laptop. If the Pi-side `curl` fails, check the service with `sudo systemctl status rpi-utils --no-pager` and its logs with `sudo journalctl -u rpi-utils -n 50 --no-pager`.

## Manage programs

Choose **Add program** in the dashboard and enter an executable followed by its arguments, for example `/usr/bin/python3 /home/pi/scripts/backup.py`. The command is tokenized and executed directly, without a shell. Program definitions are stored in `~/.config/rpi-utils/programs.json` (override with `RPI_UTILS_PROGRAMS_FILE`).

## Telegram

Create a bot with BotFather, then create `~/.config/rpi-utils/env` with the token and the numeric Telegram chat IDs allowed to control this device:

```sh
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
```

The bot accepts `/ip`, `/status`, `/start program-id`, and `/stop program-id`. It does not start unless both settings are present. Keep the env file private (`chmod 600 ~/.config/rpi-utils/env`).

## Install as a service

After committing and pushing this version to GitHub, clone it on the Pi and install the service:

```sh
git clone git@github.com:dr-fluff/rpi-utils.git
cd rpi-utils
chmod +x ./install.sh
sudo ./install.sh
```

The installer uses its Bash shebang; do not invoke it with `sh`.

The script creates a systemd service listening on `127.0.0.1:8002`. Set Telegram variables in `~/.config/rpi-utils/env` before or after installation, then restart the service with `sudo systemctl restart rpi-utils`.

The dashboard's **Update device** action requires a clean checkout, finds the latest published GitHub release, checks out that release tag (not unreleased branch commits), installs available system upgrades with `apt-get update` and `apt-get upgrade`, refreshes the installed package dependencies, and restarts the service after a successful update. It reports an error if no GitHub release has been published yet. Local changes stop the update rather than being overwritten. The installer provisions a root-owned apt helper and grants the service account passwordless sudo for that helper only; re-run `sudo ./install.sh` on existing installations to enable system upgrades. Review the repository and configure its Git remote before installing; the updater fetches the selected release tag from that checkout's `origin`.

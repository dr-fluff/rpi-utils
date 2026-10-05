# Pi Console

A small Raspberry Pi control service with a local web dashboard, process controls, Telegram commands, and a Git-based updater.

## Run locally

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002
```

Open <http://127.0.0.1:8002>. The server binds only to loopback, so it is not available to other devices on the network. To open the dashboard from another computer, use an SSH tunnel rather than changing the bind address:

```sh
ssh -L 8002:127.0.0.1:8002 pi@raspberrypi
```

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
sudo bash ./install.sh
```

The script creates a systemd service listening on `127.0.0.1:8002`. Set Telegram variables in `~/.config/rpi-utils/env` before or after installation, then restart the service with `sudo systemctl restart rpi-utils`.

The dashboard's **Update device** action requires a clean checkout, runs `git pull --ff-only`, refreshes the installed package dependencies, and restarts the service after a successful update. Local changes and non-fast-forward branches stop the update rather than being overwritten. Review the repository and configure its Git remote before installing; the updater trusts that checkout's configured origin.

# Pi Console

A small Raspberry Pi control service with a local web dashboard, process controls, Telegram commands, and a Git-based updater.

## Run locally

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
make dev
```

`make dev` automatically reloads the app when Python, HTML, JavaScript, or CSS files change. For a non-reloading local run, use `make run`.

On the Pi, verify the service responds:

```sh
curl http://127.0.0.1:8002/api/status
```

The service listens on all interfaces but allows browser requests only when addressed to a private-network IP or localhost. From a device on the same network, open `http://<pi-ip-address>:8002`; find the Pi's address with `hostname -I`. This dashboard can start and stop programs and run system upgrades, so keep it on a trusted private network and do not forward port 8002 to the internet. If you prefer not to expose the dashboard to the LAN, use the SSH tunnel from a terminal on your laptop instead:

```sh
ssh -N -L 8002:127.0.0.1:8002 <pi-username>@<pi-ip-address>
```

Replace both placeholders with the Pi's SSH username and LAN address. The SSH password prompt requires that Pi user's login password; `Permission denied` means the username/password or SSH key is not accepted. First confirm `ssh <pi-username>@<pi-ip-address>` works, then retry with the tunnel command. Leave the tunnel terminal open and visit [http://127.0.0.1:8002](http://127.0.0.1:8002) on the laptop. If the Pi-side `curl` fails, check the service with `sudo systemctl status rpi-utils --no-pager` and its logs with `sudo journalctl -u rpi-utils -n 50 --no-pager`.

## Manage programs

Choose **Add program** in the dashboard and enter an executable followed by its arguments, for example `/usr/bin/python3 /home/pi/scripts/backup.py`. Optionally add an HTTP or HTTPS web address to show an **Open web interface** link on the program row (for example, AdGuard Home at `http://<pi-ip>:3000`, depending on its configuration). Use **Add running program** to select a process that is already running and save it to the program list without starting a duplicate; you can add its web address there too. Imported processes are tracked by PID and start identity, so the dashboard will not mistake a later process that reused that PID for the imported one. Program definitions are stored in `~/.config/rpi-utils/programs.json` (override with `RPI_UTILS_PROGRAMS_FILE`), outside the app checkout, so app releases do not overwrite them. Stopping a process requires the service account to have permission to signal it. This manages processes, not systemd services or kernel-managed interfaces; for example, WireGuard is commonly managed by `wg-quick`/systemd rather than as a long-running user process.

## Telegram

Create a bot with BotFather, then create `~/.config/rpi-utils/env` with the token and the numeric Telegram chat IDs allowed to control this device:

```sh
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
```

The bot accepts `/ip` (global and local addresses), `/status` (running programs), `/start program-id`, `/stop program-id`, `/restart` (reboot the Pi), and `/update` (install the latest published release and system upgrades). `/help` lists the commands. The bot does not start unless both settings are present. The installer provisions a root-owned reboot helper and grants the service account passwordless sudo for that helper and the system-upgrade helper only; re-run `sudo ./install.sh` on an existing installation to enable `/restart`. Keep the env file private (`chmod 600 ~/.config/rpi-utils/env`).

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

The dashboard's **Update device** action requires a clean checkout, finds the latest published GitHub release, checks out that release tag (not unreleased branch commits), installs available system upgrades with `apt-get update` and `apt-get upgrade`, refreshes the installed package dependencies, and restarts the service after a successful update. It reports an error if no GitHub release has been published yet. Local changes stop the update rather than being overwritten. The installer provisions a root-owned apt helper and grants the service account passwordless sudo for that helper only; re-run `sudo ./install.sh` on existing installations to enable system upgrades and LAN access. Review the repository and configure its Git remote before installing; the updater fetches the selected release tag from that checkout's `origin`.

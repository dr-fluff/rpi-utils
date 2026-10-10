# Pi Console

A small Raspberry Pi control service with a local web dashboard, process controls, Telegram commands, and a Git-based updater.

## Run locally

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
make dev
```

`make dev` automatically reloads the app when Python, HTML, JavaScript, or CSS files change. For a non-reloading local run, use `make run`.
When running on your Mac at `http://127.0.0.1:8002`, login is optional and the terminal button is available only to the local machine. The sign-in page appears when `RPI_UTILS_WEB_PASSWORD` is set; `make dev` does not load the Pi's service environment automatically. To test login locally, start the app with `RPI_UTILS_WEB_PASSWORD` set to a password at least 12 characters long. Remote access remains disabled unless the HTTPS/Caddy settings below are configured.

For local development, verify the app responds:

```sh
curl http://127.0.0.1:8002/
```

The installer runs Pi Console on `127.0.0.1:8003` and expects Caddy to serve HTTPS on port `8002`. Opening `http://<pi-ip>:8002` directly will not work; use the Caddy HTTPS URL shown by the installer. The dashboard can start and stop programs, run system upgrades, and open a shell as the service account, so never expose it directly to the internet. For temporary access before configuring Caddy, create an SSH tunnel from your Mac:

```sh
ssh -N -L 18002:127.0.0.1:8003 <pi-username>@<pi-ip-address>
```

Replace both placeholders with the Pi's SSH username and LAN address. The SSH password prompt requires that Pi user's login password; `Permission denied` means the username/password or SSH key is not accepted. First confirm `ssh <pi-username>@<pi-ip-address>` works, then retry with the tunnel command. Leave the tunnel terminal open and visit [http://127.0.0.1:18002](http://127.0.0.1:18002) on your Mac. If the Pi-side `curl` fails, check the service with `sudo systemctl status rpi-utils --no-pager` and its logs with `sudo journalctl -u rpi-utils -n 50 --no-pager`.

### Secure Caddy access and web terminal

The installer keeps Pi Console on loopback port `8003` and trusts forwarded HTTPS information only from Caddy on `127.0.0.1`. Caddy exposes the secure dashboard on port `8002`, so you can sign in from your Mac without a screen attached to the Pi. On a fresh install, the installer creates a temporary dashboard password, requires a password change after first sign-in, and prints the temporary password and HTTPS login URL. Find the Pi's LAN IP with `hostname -I`, then use that address or a hostname that resolves to the Pi in `/etc/caddy/Caddyfile`:

```caddyfile
https://<pi-ip-address>:8002 {
    tls internal
    bind <pi-ip-address>
    reverse_proxy 127.0.0.1:8003
}
```

Alternatively, use a local hostname such as `pi-console.home.arpa` instead of the IP. Make it resolve to the Pi from your Mac (for example, add it to your router's local DNS or the Mac's `/etc/hosts`), use that hostname in the Caddy site address, and set it in `RPI_UTILS_ALLOWED_HOSTS`.

Validate and reload Caddy, then add the same IP or hostname to `~/.config/rpi-utils/env`:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
mkdir -p ~/.config/rpi-utils
nano ~/.config/rpi-utils/env
chmod 600 ~/.config/rpi-utils/env
```

If Caddy serves the Pi's IP address, set that same IP:

```sh
RPI_UTILS_ALLOWED_HOSTS=<pi-ip-address>
```

If you use a hostname instead, set that exact hostname (without a scheme or port):

```sh
RPI_UTILS_ALLOWED_HOSTS=pi-console.home.arpa
```

Keep that file private and do not remove the installer-generated `RPI_UTILS_WEB_PASSWORD` or `RPI_UTILS_PASSWORD_CHANGE_REQUIRED` entries. The installer prints a temporary password once; after first sign-in, Pi Console requires you to set a new password. Caddy must serve the same IP/hostname on port `8002`; the app rejects unlisted hosts and remote HTTP access. Caddy's `tls internal` uses its own local certificate authority. If the browser does not trust the local certificate, copy Caddy's public root certificate to your Mac over SSH and add it to the System keychain:

```sh
ssh <pi-username>@<pi-ip-address> 'sudo cat /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt' > ~/Downloads/pi-console-root.crt
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/Downloads/pi-console-root.crt
```

The root certificate is public; never copy Caddy's private key. The login protects the dashboard APIs as well as the terminal, uses an eight-hour HTTP-only session cookie, and rate-limits failed logins. Use **Change password** in the dashboard header to update it later. The terminal runs as the Pi Console service account, not root; it has that account's normal filesystem permissions and the limited sudo permissions configured by the installer.

After configuring Caddy and `RPI_UTILS_ALLOWED_HOSTS`, re-run `sudo ./install.sh` to apply the service configuration. Open `https://<pi-ip-address>:8002/` (or your chosen hostname with `:8002`) on your Mac and use the temporary password printed by the installer. The browser terminal uses a PTY with the service account's interactive shell. Loopback HTTP is allowed for local development or an SSH tunnel; remote access requires HTTPS. The terminal client is bundled locally (xterm.js 6.0.0), so it does not depend on a third-party CDN.

## Manage programs

Choose **Add program** in the dashboard and enter an executable followed by its arguments, for example `/usr/bin/python3 /home/pi/scripts/backup.py`. Optionally add an HTTP or HTTPS web address to show an **Open web interface** link on the program row (for example, AdGuard Home at `http://<pi-ip>:3000`, depending on its configuration). Use **Add running program** to select a process that is already running and save it to the program list without starting a duplicate; you can add its web address there too. Use **Remove** on a program row to remove its saved entry; this does not stop a running process. Imported processes are tracked by PID and start identity, so the dashboard will not mistake a later process that reused that PID for the imported one. Program definitions are stored in `~/.config/rpi-utils/programs.json` (override with `RPI_UTILS_PROGRAMS_FILE`), outside the app checkout, so app releases do not overwrite them. Stopping a process requires the service account to have permission to signal it. This manages processes, not systemd services or kernel-managed interfaces; for example, WireGuard is commonly managed by `wg-quick`/systemd rather than as a long-running user process.

## Telegram

Create a bot with BotFather, then create `~/.config/rpi-utils/env` with the token and the numeric Telegram chat IDs allowed to control this device:

```sh
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
```

The same command registry powers the Telegram bot and the dashboard's **Device commands** buttons. It includes `/ip` (global and local addresses), `/status` (running programs), `/start program-id`, `/stop program-id`, `/restart` (reboot the Pi), and `/update` (install the latest published release and system upgrades); `/help` lists the commands. Program-row Start/Stop buttons also use this shared dispatcher. To add a command to both interfaces, add its metadata and implementation in `app/commands.py`; the Telegram handler and dashboard buttons are generated from that registry. The bot does not start unless both settings are present. The installer provisions a root-owned reboot helper and grants the service account passwordless sudo for that helper and the system-upgrade helper only; re-run `sudo ./install.sh` on an existing installation to enable `/restart`. Keep the env file private (`chmod 600 ~/.config/rpi-utils/env`).

## Install as a service

After committing and pushing this version to GitHub, clone it on the Pi and install the service:

```sh
git clone git@github.com:dr-fluff/rpi-utils.git
cd rpi-utils
chmod +x ./install.sh
sudo ./install.sh
```

The installer uses its Bash shebang; do not invoke it with `sh`.

The script creates a systemd service listening on `127.0.0.1:8003`, behind Caddy's HTTPS listener on port `8002`. Set Telegram and allowed-host settings in `~/.config/rpi-utils/env` before or after installation, then restart with `sudo systemctl restart rpi-utils`.

The dashboard checks GitHub for a newer published release on page load and every five minutes, showing an **Update now** banner when the release tag points to a different commit than the installed checkout. The dashboard's **Update device** action requires a clean checkout, checks out that release tag (not unreleased branch commits), installs available system upgrades with `apt-get update` and `apt-get upgrade`, refreshes the installed package dependencies, and restarts the service after a successful update. It reports an error if no GitHub release has been published yet. Local changes stop the update rather than being overwritten. The installer provisions a root-owned apt helper and grants the service account passwordless sudo for that helper only; re-run `sudo ./install.sh` on existing installations to enable system upgrades and loopback-only service access. Review the repository and configure its Git remote before installing; the updater fetches the selected release tag from that checkout's `origin`.

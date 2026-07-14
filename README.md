# jellyfin-shim-manager

A single CLI to manage multiple [jellyfin-mpv-shim](https://github.com/jellyfin/jellyfin-mpv-shim)
instances on one shared, headless box (e.g. a Raspberry Pi hooked up to a TV).
Each user gets their own systemd-managed shim instance, login state, and
optional auto-expiring "guest" login — all driven from one `jellyfin-shim-manager`
command.

## What it does

- **`jellyfin-shim-manager add/remove/list`** — CLI-driven per-user instance
  management, each running as its own `jellyfin-mpv-shim@<user>` systemd
  service.
- **`jellyfin-shim-manager join`** — a small local web app: scan a QR code,
  log in with Jellyfin Quick Connect, and choose "permanent" (this is my TV
  too) or "temporary" (just watching now, auto-expires after inactivity).
- **`jellyfin-shim-manager reap`** — disables and deletes temporary instances
  that have been idle too long. Meant to run on a timer.
- **`jellyfin-shim-manager monitor`** — drives a framebuffer status screen
  (via `fbi`) showing whether Jellyfin/the network/a stream is up, for a
  TV-connected Pi with no monitor attached to it otherwise.
- **`jellyfin-shim-manager setup`** — installs the systemd units, a narrowly
  scoped sudoers rule, and a default config file in one shot.

## Install

One-liner (installs OS deps, the CLI via `pipx`, and runs `setup`):

```bash
curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/install.sh | bash
```

Or manually:

```bash
sudo apt install -y git python3 python3-pip python3-venv jq qrencode
pipx install git+https://github.com/DD00031/jellyfin-shim-manager.git
jellyfin-shim-manager setup
```

`setup` writes `/etc/jellyfin-shim-manager/config.json` (if it doesn't already
exist), installs the `jellyfin-mpv-shim@.service` template unit plus the
`jellyfin-shim-manager-join` and `jellyfin-shim-manager-reaper` units, and
adds a sudoers rule so the join app and reaper can start/stop/enable/disable
`jellyfin-mpv-shim@*` services without running as root.

## Configure

Edit `/etc/jellyfin-shim-manager/config.json`:

```jsonc
{
  "config_base": "/home/pi/mpv-shim-configs",  // per-user shim configs live here
  "run_as_user": "pi",                          // linux user the shim services run as
  "service_prefix": "jellyfin-mpv-shim@",
  "jellyfin_url": "http://192.168.1.10:8096",   // your Jellyfin server
  "jellyfin_port": 8096,
  "local_ip": "192.168.1.10",                   // match jellyfin_url's host
  "tailscale_ip": "",                           // optional fallback address
  "bind_host": "0.0.0.0",                       // join web app bind address
  "bind_port": 5005,
  "image_dir": "/home/pi/Resources",            // status screen images
  "temporary_timeout_seconds": 10800            // 3h idle timeout for guest logins
}
```

After editing, restart the join service so it picks up the change:

```bash
sudo systemctl restart jellyfin-shim-manager-join
```

## Usage

```bash
jellyfin-shim-manager add alice          # interactive Jellyfin login, enables the service
jellyfin-shim-manager list               # show every instance + status
jellyfin-shim-manager remove alice       # stop, disable, optionally delete config
jellyfin-shim-manager join               # run the QR onboarding page in the foreground
jellyfin-shim-manager reap               # manually run the temporary-login sweep
jellyfin-shim-manager reap --dry-run     # see what reap would do without doing it
jellyfin-shim-manager monitor            # run the status-screen loop in the foreground
jellyfin-shim-manager config             # print the effective config
```

In normal operation the `join` and `reaper` units run as services/timers (via
`setup`), and you mostly just use `add`/`remove`/`list`. `monitor` is meant to
be launched from an autologin `.bash_profile`/`.xinitrc` on the Pi's local
console, since it drives the physical framebuffer.

## Things to verify for your setup

1. **Quick Connect code format.** `code_regex` in the config assumes a bare
   6-digit code. Run once by hand and check the log if unsure:
   ```
   jellyfin-mpv-shim --config /tmp/qc-test --quick-connect --server http://<ip>:8096
   ```
2. **Playback log lines.** `monitor` looks for `playMedia` and
   `Sessions/Playing/Stopped` in `journalctl` output for a given
   `jellyfin-mpv-shim@<user>` unit to detect activity. Confirm with:
   ```
   journalctl -u jellyfin-mpv-shim@<user> -f
   ```
3. **Status screen images.** `setup` copies placeholder PNGs into `image_dir`
   for `no_jellyfin` / `no_internet` / `no_server` / `playing` states — swap
   in your own. The `idle` state expects `join-qr.png`, a static QR code
   pointing at `http://<local_ip>:<bind_port>/join`, e.g.:
   ```
   qrencode -o /home/pi/Resources/join-qr.png -s 10 "http://192.168.1.10:5005/join"
   ```

## Notes

- The join app uses Flask's built-in dev server, which is fine for a LAN-only
  tool like this but isn't hardened for anything beyond that.
- `reap` only prunes `temporary` instances; anything without a `meta.json`
  (e.g. an instance added by hand outside this tool) is left alone.
- Config lives in one file (`/etc/jellyfin-shim-manager/config.json`) instead
  of being duplicated across scripts, so there's one place to change the
  server URL, paths, or timeouts.

## License

MIT

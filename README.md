# jellyfin-shim-manager

A single CLI to manage multiple [jellyfin-mpv-shim](https://github.com/jellyfin/jellyfin-mpv-shim)
instances on one shared, headless box (e.g. a Raspberry Pi hooked up to a TV).
Each user gets their own systemd-managed shim instance and login state, with
a clean web UI for both onboarding and admin control.

## What it does

- **`jellyfin-shim-manager add/remove/list`** — CLI-driven per-user instance
  management, each running as its own `jellyfin-mpv-shim@<user>` systemd
  service.
- **`jellyfin-shim-manager join`** — serves two web pages on one port:
  - `/join` — a clean sign-in page where a new user enters their normal
    Jellyfin username and password, then picks "permanent" (this is my TV
    too) or "temporary" (just watching now, auto-expires after inactivity).
  - `/admin` — a session-authenticated dashboard to view, start/stop, add,
    and remove instances, and trigger a manual reap.
  Both share a light/dark theme toggle and a purple accent matching
  Jellyfin's brand color.
- **`jellyfin-shim-manager reap`** — disables and deletes temporary instances
  that have been idle too long. Meant to run on a timer.
- **`jellyfin-shim-manager monitor`** — drives a framebuffer status screen
  (via `fbi`) showing whether Jellyfin/the network/a stream is up, for a
  TV-connected Pi with no monitor attached to it otherwise.
- **`jellyfin-shim-manager setup`** — installs the systemd units, a narrowly
  scoped sudoers rule, a default config file, checks/installs `mpv` and
  `jellyfin-mpv-shim` itself, and (interactively) your first admin account,
  in one shot.
- **`jellyfin-shim-manager deps`** — checks for `mpv`, `jellyfin-mpv-shim`,
  and the optional tools (`openssl`, `qrencode`, `fbi`), and can install
  what's missing with `--install`.
- **`jellyfin-shim-manager update`** / **`update.sh`** — pulls the latest
  version, reinstalls via `pipx`, and refreshes the systemd units.
- **`jellyfin-shim-manager uninstall`** / **`uninstall.sh`** — removes the
  systemd units, sudoers rule, and (opt-in) instances/config/the CLI itself.

### How login actually works

`jellyfin-mpv-shim` has no Quick Connect CLI flag — its `--server`,
`--username`, and `--password` flags feed the `add` subcommand, which stores
credentials non-interactively:

```
jellyfin-mpv-shim --config <dir> --no-gui --server <url> --username <user> --password <pass> add
```

So `/join` (and `jellyfin-shim-manager add`) collect a username+password and
run that synchronously — no code, no polling, just a few seconds and either
a saved login or an error. The systemd-managed instance is then started
using just `--config <dir>`.

**Known limitation:** the password is briefly visible to other local
processes via `ps` while that command runs — this is a constraint of the
upstream `jellyfin-mpv-shim` CLI, not something this tool can work around.
Use TLS (below) so it's at least encrypted in transit, and treat this as a
LAN-only tool.

## Install

One-liner (installs OS deps, the CLI via `pipx`, and runs `setup`):

```bash
curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/install.sh | bash
```

Or manually:

```bash
sudo apt install -y git python3 python3-pip python3-venv
pipx install git+https://github.com/DD00031/jellyfin-shim-manager.git
jellyfin-shim-manager setup           # add --tls for a self-signed HTTPS cert
```

`setup` doesn't need to be run as root or as any particular user — it shells
out to `sudo` itself for the handful of steps that need it (writing under
`/etc`, installing systemd units, apt/pip installs), the same way `sudo apt
install` does. Just run it as yourself.

If a terminal is attached and no config exists yet, `setup` asks a few
questions (skip a prompt to accept the default shown in `[brackets]`):

```
Linux user the shim services should run as [rvdk]:
Directory to store per-user shim configs [/home/rvdk/mpv-shim-configs]:
Jellyfin server URL (e.g. http://192.168.1.10:8096): http://192.168.1.10:8096
No admin account exists yet -- set one up now for the /admin panel.
Admin username [admin]:
Admin password:
Confirm password:
```

The "run as" user defaults to whoever is actually running the command (or
`$SUDO_USER` if invoked with `sudo`) — **not** a hardcoded `pi` — and
`config_base`/`image_dir` default to that user's real home directory. Pass
`--run-as-user`/`--config-base`/`--jellyfin-url`/`--local-ip` to set any of
these non-interactively (useful for scripted installs — add
`--non-interactive` to also suppress the admin-password prompt).

`setup` also:
- checks for `mpv` and `jellyfin-mpv-shim` and installs them (via `apt` and
  `pip install --break-system-packages` respectively) if missing — same as
  running `jellyfin-shim-manager deps --install --required-only`,
- installs the `jellyfin-mpv-shim@.service` template unit plus the
  `jellyfin-shim-manager-join` and `jellyfin-shim-manager-reaper` units,
- adds a sudoers rule so the web app and reaper can start/stop/enable/disable
  `jellyfin-mpv-shim@*` services without running as root,
- with `--tls`, generates a self-signed cert (`openssl`) and turns on HTTPS
  for the web app,
- generates `join-qr.png` — a QR code for the `/join` page composited onto
  `ready.png` — via `jellyfin-shim-manager generate-qr` (see below).

Admin credentials, the session secret key, and the TLS key are all written
owned by the "run as" user (mode 0600) — that's the account the join/admin
service actually runs as, so it's the one that needs to read them back.

Optional tools aren't installed automatically — run `jellyfin-shim-manager
deps --install` to also grab `openssl` (for `--tls`) and `fbi` (for
`monitor`'s framebuffer screen). `qrencode` is listed there too but isn't
part of the default flow anymore — see the status screen images note below.

## Configure

Edit `/etc/jellyfin-shim-manager/config.json`:

```jsonc
{
  "config_base": "/home/<run_as_user>/mpv-shim-configs",  // per-user shim configs live here
  "run_as_user": "<run_as_user>",               // linux user the shim services run as
  "service_prefix": "jellyfin-mpv-shim@",
  "jellyfin_url": "http://192.168.1.10:8096",   // your Jellyfin server
  "jellyfin_port": 8096,
  "local_ip": "192.168.1.10",                   // match jellyfin_url's host
  "tailscale_ip": "",                           // optional fallback address
  "bind_host": "0.0.0.0",                       // web app bind address
  "bind_port": 5005,
  "login_timeout_seconds": 45,
  "tls_enabled": false,
  "tls_cert": "/etc/jellyfin-shim-manager/tls/cert.pem",
  "tls_key": "/etc/jellyfin-shim-manager/tls/key.pem",
  "image_dir": "/home/<run_as_user>/Resources", // status screen images
  "temporary_timeout_seconds": 10800            // 3h idle timeout for guest logins
}
```

(`<run_as_user>` above is whatever `setup` resolved `run_as_user` to — check
with `jellyfin-shim-manager config`.)

Admin credentials and the Flask session secret key live outside this file
(in `/etc/jellyfin-shim-manager/admin.json` and `.../secret_key`, both mode
0600) since they're secrets, not settings.

After editing, restart the web app so it picks up the change:

```bash
sudo systemctl restart jellyfin-shim-manager-join
```

## Usage

```bash
jellyfin-shim-manager add alice              # prompts for a password, enables the service
jellyfin-shim-manager list                   # show every instance + status
jellyfin-shim-manager remove alice           # stop, disable, optionally delete config
jellyfin-shim-manager join                   # run the join+admin web app in the foreground
jellyfin-shim-manager admin set-password     # (re)set the /admin login
jellyfin-shim-manager reap                   # manually run the temporary-login sweep
jellyfin-shim-manager reap --dry-run         # see what reap would do without doing it
jellyfin-shim-manager monitor                # run the status-screen loop in the foreground
jellyfin-shim-manager config                 # print the effective config
jellyfin-shim-manager deps                   # check mpv/jellyfin-mpv-shim/openssl/qrencode/fbi
jellyfin-shim-manager deps --install         # install whatever's missing
jellyfin-shim-manager generate-qr            # (re)composite join-qr.png onto ready.png
jellyfin-shim-manager generate-qr --force    # rebuild even if join-qr.png already exists
jellyfin-shim-manager update                 # pull latest + reinstall + refresh units
jellyfin-shim-manager uninstall              # remove systemd units + sudoers rule
```

In normal operation the `join` (which also serves `/admin`) and `reaper`
units run as services/timers (via `setup`), and you mostly just use the web
UI plus `add`/`remove`/`list` for anything scripted. `monitor` is meant to be
launched from an autologin `.bash_profile`/`.xinitrc` on the Pi's local
console, since it drives the physical framebuffer.

## Update / uninstall

Update to the latest version in place:

```bash
curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/update.sh | bash
# or, if already installed:
jellyfin-shim-manager update
```

This pulls the latest source, reinstalls via `pipx`, and re-runs `setup` to
refresh the systemd units — your existing `config.json`, admin credentials,
and TLS certs are left untouched.

Uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/uninstall.sh | bash
```

By default this only removes jellyfin-shim-manager itself (its systemd
units, sudoers rule, the pipx-installed CLI, and the cloned source). Your
configured `jellyfin-mpv-shim@*` instances and their saved logins keep
running untouched. Add `--purge-instances` to also stop/remove every
instance, or `--purge-config` to also delete `/etc/jellyfin-shim-manager`
(admin credentials, TLS certs). The underlying command (usable directly, or
if you'd rather keep the CLI installed) is:

```bash
jellyfin-shim-manager uninstall [--purge-instances] [--purge-config] [-y]
```

## Things to verify for your setup

1. **Playback log lines.** `monitor` looks for `playMedia` and
   `Sessions/Playing/Stopped` in `journalctl` output for a given
   `jellyfin-mpv-shim@<user>` unit to detect activity. Confirm with:
   ```
   journalctl -u jellyfin-mpv-shim@<user> -f
   ```
2. **Status screen images.** `setup` copies placeholder PNGs into `image_dir`
   (check the path with `jellyfin-shim-manager config`) for `no_jellyfin` /
   `no_internet` / `no_server` / `playing` states — swap in your own. The
   `idle` state expects `join-qr.png`, which `setup` generates automatically
   by compositing a QR code for `http(s)://<local_ip>:<bind_port>/join`
   (scheme depending on `tls_enabled`) onto a copy of `ready.png` — the QR
   is 700×700px, centered at `(900, 960)` on the (assumed 3840×2160)
   `ready.png` canvas. `ready.png` itself is never modified, since it's also
   used standalone for the `playing` state.

   Swap in your own `ready.png` (same 3840×2160 canvas, or adjust
   `QR_SIZE`/`QR_CENTER_X`/`QR_CENTER_Y` in `qrgen.py` if yours differs) and
   re-run:
   ```
   jellyfin-shim-manager generate-qr --force
   ```
   This also picks up changes to `local_ip`, `bind_port`, or `tls_enabled`
   without needing `--reset-config`. If you'd rather hand-roll a fully
   custom `join-qr.png` (different background, styling, etc.), just write
   your own file to `image_dir/join-qr.png` directly — `generate-qr` (and
   `setup`, without `--regenerate-qr`) leave it alone if it already exists.
   The `qrencode` CLI (`jellyfin-shim-manager deps --install`) is handy for
   that by-hand path, but isn't used by the default flow.

## Notes

- The web app uses Flask's built-in dev server, which is fine for a LAN-only
  tool like this but isn't hardened for anything beyond that.
- `reap` only prunes `temporary` instances; anything without a `meta.json`
  (e.g. an instance added by hand outside this tool) is left alone.
- Config lives in one file (`/etc/jellyfin-shim-manager/config.json`) instead
  of being duplicated across scripts, so there's one place to change the
  server URL, paths, or timeouts.

## License

MIT

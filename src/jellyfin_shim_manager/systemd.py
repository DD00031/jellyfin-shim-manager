"""Generates and installs the systemd units and sudoers rule this tool needs.

Four kinds of units are involved:
  - jellyfin-mpv-shim@.service   template unit, one instance per user, started
                                  by `add` / the join web app.
  - jellyfin-shim-manager-join.service       runs `jellyfin-shim-manager join`
  - jellyfin-shim-manager-reaper.service/.timer  periodically runs `jellyfin-shim-manager reap`
  - jellyfin-shim-manager-monitor.service    runs `jellyfin-shim-manager monitor`
                                  (the framebuffer status screen); opt-out via
                                  `setup --no-monitor-service`, since it takes
                                  over tty1's login getty.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import privileged

SYSTEMD_DIR = Path("/etc/systemd/system")
SUDOERS_PATH = Path("/etc/sudoers.d/jellyfin-shim-manager")

SHIM_TEMPLATE_UNIT = "jellyfin-mpv-shim@.service"
JOIN_UNIT = "jellyfin-shim-manager-join.service"
REAPER_SERVICE_UNIT = "jellyfin-shim-manager-reaper.service"
REAPER_TIMER_UNIT = "jellyfin-shim-manager-reaper.timer"
MONITOR_UNIT = "jellyfin-shim-manager-monitor.service"


def _shim_template_unit(cfg: dict) -> str:
    # Resolved at generation time rather than hardcoded: apt puts it at
    # /usr/bin, `pip install --break-system-packages` typically puts it at
    # /usr/local/bin. Falls back to the apt path if it isn't found at all
    # (e.g. this is being generated before jellyfin-mpv-shim is installed).
    shim_path = shutil.which("jellyfin-mpv-shim") or "/usr/bin/jellyfin-mpv-shim"
    return f"""[Unit]
Description=Jellyfin MPV Shim (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={cfg['run_as_user']}
Environment=DISPLAY={cfg['display']}
ExecStart={shim_path} --config {cfg['config_base']}/%i
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _join_unit(cfg: dict, exe: str) -> str:
    return f"""[Unit]
Description=jellyfin-shim-manager onboarding web app (QR join page)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={cfg['run_as_user']}
ExecStart={exe} join
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _monitor_unit(cfg: dict, exe: str) -> str:
    # Runs as root (not run_as_user, unlike join/reaper): fbi needs direct
    # framebuffer/VT access. Conflicts with getty@tty1 because they'd
    # otherwise fight over the same VT -- starting this stops the tty1
    # login prompt, which is the intended tradeoff (see setup's output).
    return f"""[Unit]
Description=jellyfin-shim-manager status screen (framebuffer)
After=network-online.target getty@tty1.service
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
Type=simple
User=root
ExecStart={exe} monitor
Restart=on-failure
RestartSec=5
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes

[Install]
WantedBy=multi-user.target
"""


def _reaper_service_unit(exe: str) -> str:
    return f"""[Unit]
Description=Reap expired temporary jellyfin-mpv-shim instances

[Service]
Type=oneshot
ExecStart={exe} reap
"""


def _reaper_timer_unit() -> str:
    return """[Unit]
Description=Periodically check for expired temporary jellyfin-mpv-shim logins

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
"""


def _sudoers_rule(cfg: dict, run_as_user: str) -> str:
    prefix = cfg["service_prefix"]
    return (
        f"# Managed by jellyfin-shim-manager. Do not run the join app or reaper as root;\n"
        f"# this grants only the narrow systemctl commands they need.\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable --now {prefix}*\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start {prefix}*\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable --now {prefix}*\n"
    )


def ensure_shim_template_unit(cfg: dict, force: bool = False) -> bool:
    """Writes jellyfin-mpv-shim@.service if missing (or if force). Returns True if written."""
    path = SYSTEMD_DIR / SHIM_TEMPLATE_UNIT
    if path.exists() and not force:
        return False
    _write_unit(path, _shim_template_unit(cfg))
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    return True


def install_manager_units(cfg: dict, exe: str = None, enable: bool = True, monitor_service: bool = True):
    """Installs the join-app and reaper units, plus the shim template unit.

    monitor_service installs+enables jellyfin-shim-manager-monitor.service
    too -- opt out with setup --no-monitor-service if this box isn't a
    headless console (e.g. it's running a desktop on tty1).
    """
    exe = exe or shutil.which("jellyfin-shim-manager") or "/usr/local/bin/jellyfin-shim-manager"

    ensure_shim_template_unit(cfg)
    _write_unit(SYSTEMD_DIR / JOIN_UNIT, _join_unit(cfg, exe))
    _write_unit(SYSTEMD_DIR / REAPER_SERVICE_UNIT, _reaper_service_unit(exe))
    _write_unit(SYSTEMD_DIR / REAPER_TIMER_UNIT, _reaper_timer_unit())
    if monitor_service:
        _write_unit(SYSTEMD_DIR / MONITOR_UNIT, _monitor_unit(cfg, exe))

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)

    if enable:
        subprocess.run(["sudo", "systemctl", "enable", "--now", REAPER_TIMER_UNIT], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "--now", JOIN_UNIT], check=True)
        if monitor_service:
            subprocess.run(["sudo", "systemctl", "enable", "--now", MONITOR_UNIT], check=True)


def generate_self_signed_cert(cfg: dict, days: int = 825):
    """Generates a self-signed TLS cert/key for the join/admin web app.

    A self-signed cert still gets you an encrypted connection (browsers just
    won't trust it by default) -- good enough for a LAN-only tool. Generated
    in a scratch dir first (openssl needs somewhere it can write directly),
    then installed to /etc with `sudo`, owned by run_as_user since that's the
    account the web app -- which needs to read its own key -- runs as.
    """
    from . import config as cfgmod

    cert_path = Path(cfg.get("tls_cert") or cfgmod.TLS_DIR / "cert.pem")
    key_path = Path(cfg.get("tls_key") or cfgmod.TLS_DIR / "key.pem")

    with tempfile.TemporaryDirectory(prefix="jellyfin-shim-manager-tls-") as tmp:
        tmp_key = Path(tmp) / "key.pem"
        tmp_cert = Path(tmp) / "cert.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-nodes", "-days", str(days),
                "-keyout", str(tmp_key),
                "-out", str(tmp_cert),
                "-subj", "/CN=jellyfin-shim-manager",
            ],
            check=True,
        )
        owner = cfg.get("run_as_user")
        privileged.write_bytes(key_path, tmp_key.read_bytes(), mode=0o600, owner=owner)
        privileged.write_bytes(cert_path, tmp_cert.read_bytes(), mode=0o644, owner=owner)

    return cert_path, key_path


def install_sudoers_rule(cfg: dict, run_as_user: str = None):
    run_as_user = run_as_user or cfg["run_as_user"]
    content = _sudoers_rule(cfg, run_as_user)
    with tempfile.NamedTemporaryFile("w", prefix="jellyfin-shim-manager-sudoers-", delete=False) as f:
        f.write(content)
        tmp = Path(f.name)
    try:
        subprocess.run(["sudo", "visudo", "-cf", str(tmp)], check=True)
        privileged.write_file(SUDOERS_PATH, content, mode=0o440)
    finally:
        tmp.unlink(missing_ok=True)


def _remove_unit(unit_name: str):
    subprocess.run(["sudo", "systemctl", "disable", "--now", unit_name], check=False)
    path = SYSTEMD_DIR / unit_name
    subprocess.run(["sudo", "rm", "-f", str(path)], check=False)


def uninstall_manager_units():
    """Stops, disables, and removes the join + reaper + monitor units (not the per-user shim units)."""
    for unit in (JOIN_UNIT, REAPER_TIMER_UNIT, REAPER_SERVICE_UNIT, MONITOR_UNIT):
        _remove_unit(unit)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)


def remove_shim_template_unit():
    """Removes jellyfin-mpv-shim@.service. Only safe once no per-user instances remain."""
    subprocess.run(["sudo", "rm", "-f", str(SYSTEMD_DIR / SHIM_TEMPLATE_UNIT)], check=False)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)


def remove_sudoers_rule():
    subprocess.run(["sudo", "rm", "-f", str(SUDOERS_PATH)], check=False)


def _write_unit(path: Path, content: str):
    privileged.write_file(path, content, mode=0o644)

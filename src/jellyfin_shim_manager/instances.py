"""Shared helpers for working with per-user jellyfin-mpv-shim instances.

An "instance" is a directory under config_base/<name>/ holding conf.json /
cred.json (written by jellyfin-mpv-shim itself) and meta.json (written by us:
{"type": "permanent"|"temporary", "created": <epoch>, "last_active": <epoch>}).
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

PENDING_DIRNAME = ".pending"


class SystemctlError(RuntimeError):
    pass


class ShimLoginError(RuntimeError):
    """Raised when a non-interactive `jellyfin-mpv-shim ... add` fails."""


def run_shim_login(config_dir: Path, server_url: str, username: str, password: str, timeout: int = 45):
    """Stores Jellyfin credentials in config_dir via `jellyfin-mpv-shim add`.

    jellyfin-mpv-shim's --server/--username/--password flags only apply to
    the `add` positional command; they store credentials non-interactively
    instead of prompting. The long-running shim process is started
    separately afterwards using just --config.

    NOTE: the password is briefly visible to other local processes via `ps`
    while this subprocess is running -- that's a limitation of the upstream
    CLI, not something we can avoid short of patching it.

    Raises ShimLoginError with a user-facing message on failure.
    """
    try:
        result = subprocess.run(
            [
                "jellyfin-mpv-shim",
                "--config", str(config_dir),
                "--no-gui",
                "--server", server_url,
                "--username", username,
                "--password", password,
                "add",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ShimLoginError("Timed out contacting the Jellyfin server.") from exc
    except FileNotFoundError as exc:
        raise ShimLoginError("jellyfin-mpv-shim is not installed or not on PATH.") from exc

    if not (config_dir / "conf.json").exists():
        output = (result.stderr or result.stdout or "").strip()
        hint = output.splitlines()[-1] if output else "login did not complete"
        raise ShimLoginError(f"Login failed: {hint}")


def run_systemctl(*args, check=True):
    try:
        subprocess.run(["sudo", "systemctl", *args], check=check)
    except subprocess.CalledProcessError as exc:
        raise SystemctlError(str(exc)) from exc


def unit_name(cfg: dict, user: str) -> str:
    return f"{cfg['service_prefix']}{user}"


def config_base(cfg: dict) -> Path:
    return Path(cfg["config_base"])


def instance_dir(cfg: dict, user: str) -> Path:
    return config_base(cfg) / user


def list_instance_names(cfg: dict):
    base = config_base(cfg)
    if not base.is_dir():
        return []
    names = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if d.name == PENDING_DIRNAME:
            continue
        names.append(d.name)
    return names


def read_meta(cfg: dict, user: str) -> dict:
    meta_path = instance_dir(cfg, user) / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return {}


def write_meta(config_dir: Path, type_: str, created: int = None, last_active: int = None):
    now = int(time.time())
    meta = {
        "type": type_,
        "created": created if created is not None else now,
        "last_active": last_active if last_active is not None else now,
    }
    (config_dir / "meta.json").write_text(json.dumps(meta))
    return meta


def touch_activity(cfg: dict, user: str):
    meta_path = instance_dir(cfg, user) / "meta.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return
    meta["last_active"] = int(time.time())
    meta_path.write_text(json.dumps(meta))


def service_status(cfg: dict, user: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", unit_name(cfg, user)],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def is_service_active(cfg: dict, user: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit_name(cfg, user)]
    )
    return result.returncode == 0


def recent_journal(cfg: dict, user: str, lines: int = 20) -> str:
    result = subprocess.run(
        ["journalctl", "-u", unit_name(cfg, user), "-n", str(lines), "--no-pager"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def purge_all_instances(cfg: dict):
    """Stops, disables, and deletes every configured instance. Used by `uninstall --purge-instances`."""
    for user in list_instance_names(cfg):
        run_systemctl("disable", "--now", unit_name(cfg, user), check=False)
        shutil.rmtree(instance_dir(cfg, user), ignore_errors=True)


def instance_playback_state(cfg: dict, user: str, lines: int = 200) -> str:
    """Returns 'playing' or 'idle' based on the most recent playback log line."""
    log = recent_journal(cfg, user, lines=lines)
    last_event = None
    for line in log.splitlines():
        if "playMedia" in line or "Sessions/Playing/Stopped" in line:
            last_event = line
    if last_event and "playMedia" in last_event:
        return "playing"
    return "idle"

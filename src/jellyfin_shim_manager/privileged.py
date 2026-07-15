"""Writes files under root-owned paths (/etc/jellyfin-shim-manager/...)
regardless of whether the current process itself is running as root.

Everything here goes through `sudo`, mirroring how systemd.py already
installs unit files: write a temp file as the current user, then
`sudo install` it into place with the right mode/owner. `sudo` as root is a
no-op (no prompt), so this works whether jellyfin-shim-manager is invoked
directly by a sudo-capable user or already running as root.
"""

import grp
import os
import pwd
import subprocess
import tempfile
from pathlib import Path


def _primary_group(owner: str) -> str:
    """The owner's actual primary group -- not assumed to share its name."""
    try:
        gid = pwd.getpwnam(owner).pw_gid
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return owner


def _install(tmp_path: Path, dest: Path, mode: int, owner: str = None):
    subprocess.run(["sudo", "mkdir", "-p", str(dest.parent)], check=True)
    cmd = ["sudo", "install", "-m", f"{mode:04o}"]
    if owner:
        cmd += ["-o", owner, "-g", _primary_group(owner)]
    cmd += [str(tmp_path), str(dest)]
    subprocess.run(cmd, check=True)


def write_file(path, content: str, mode: int = 0o644, owner: str = None):
    dest = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix="jellyfin-shim-manager-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        _install(tmp, dest, mode, owner)
    finally:
        tmp.unlink(missing_ok=True)


def write_bytes(path, data: bytes, mode: int = 0o644, owner: str = None):
    dest = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix="jellyfin-shim-manager-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _install(tmp, dest, mode, owner)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_owned_dir(path, owner: str):
    """Creates path if missing, then makes sure `owner` actually owns it.

    Needed because setup may run as root (via sudo) or as a plain user, but
    the directories it creates (config_base, image_dir, ...) must end up
    owned by run_as_user -- the account the systemd services actually run
    as -- or those services can't write into them.
    """
    path = Path(path)
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            subprocess.run(["sudo", "mkdir", "-p", str(path)], check=True)

    try:
        target_uid = pwd.getpwnam(owner).pw_uid
    except KeyError:
        return  # unknown user -- leave ownership alone, ensure_owned_dir is best-effort

    if path.stat().st_uid != target_uid:
        subprocess.run(["sudo", "chown", owner, str(path)], check=False)

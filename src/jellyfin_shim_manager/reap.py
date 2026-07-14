"""jellyfin-shim-manager reap — disables and deletes expired temporary instances.

Meant to be run periodically (see the jellyfin-shim-manager-reaper.timer unit
installed by `jellyfin-shim-manager setup`), not in a loop itself.
"""

import shutil
import time

from . import instances as inst


def run(cfg: dict, dry_run: bool = False) -> list:
    """Reaps expired temporary instances. Returns the list of reaped names."""
    now = int(time.time())
    timeout = cfg["temporary_timeout_seconds"]
    reaped = []

    for user in inst.list_instance_names(cfg):
        meta = inst.read_meta(cfg, user)
        if not meta:
            continue  # no marker, e.g. an older manually-added instance -- leave it alone
        if meta.get("type", "permanent") != "temporary":
            continue

        age = now - meta.get("last_active", 0)
        if age <= timeout:
            continue

        print(f"Reaping '{user}' (idle for {age}s, limit {timeout}s)")
        reaped.append(user)
        if dry_run:
            continue

        inst.run_systemctl("disable", "--now", inst.unit_name(cfg, user), check=False)
        shutil.rmtree(inst.instance_dir(cfg, user), ignore_errors=True)

    return reaped

"""jellyfin-shim-manager — manage multi-user jellyfin-mpv-shim instances on a
shared, headless Jellyfin client box (e.g. a Raspberry Pi).

Subcommands:
  add <user>       interactively log in a new permanent instance and enable it
  remove <user>     stop, disable, and optionally delete an instance
  list              show all configured instances and their status
  join              run the QR-code onboarding web app
  monitor           run the framebuffer status screen loop
  reap              disable+delete expired temporary instances (for the timer)
  setup             install systemd units, sudoers rule, and config file
  config            show or initialize the config file
"""

import argparse
import importlib.resources
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import instances as inst
from . import monitor as monitor_mod
from . import reap as reap_mod
from . import systemd
from . import web as web_mod


def cmd_add(cfg: dict, args):
    user = args.username
    config_dir = inst.instance_dir(cfg, user)

    systemd.ensure_shim_template_unit(cfg)

    if config_dir.is_dir() and (config_dir / "conf.json").exists():
        print(f"Config for '{user}' already exists at {config_dir}")
        confirm = input("Re-run login and overwrite it? [y/N] ").strip().lower()
        if confirm != "y":
            print("Leaving existing config in place. Re-enabling service just in case...")
            inst.run_systemctl("enable", "--now", inst.unit_name(cfg, user))
            return
        shutil.rmtree(config_dir)

    config_dir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"Launching jellyfin-mpv-shim for '{user}' to log in.")
    print("Enter the server URL and credentials (or use Quick Connect).")
    print("Once it shows as connected, quit the app (press 'q' or Ctrl+C) to continue.")
    print()
    subprocess.run(["jellyfin-mpv-shim", "--config", str(config_dir)])

    if not (config_dir / "conf.json").exists():
        print("No conf.json was created -- login likely didn't complete. Aborting.", file=sys.stderr)
        sys.exit(1)

    # CLI-added users are always permanent; temporary ones only come from `join`.
    inst.write_meta(config_dir, "permanent")

    print(f"Login saved. Enabling systemd service for '{user}'...")
    inst.run_systemctl("enable", "--now", inst.unit_name(cfg, user))
    print(f"Done. Check status with: systemctl status {inst.unit_name(cfg, user)}")


def cmd_remove(cfg: dict, args):
    user = args.username
    config_dir = inst.instance_dir(cfg, user)

    print(f"Stopping and disabling service for '{user}'...")
    try:
        inst.run_systemctl("disable", "--now", inst.unit_name(cfg, user))
    except inst.SystemctlError:
        pass

    if config_dir.is_dir():
        if args.yes:
            confirm = "y"
        else:
            confirm = input(f"Delete config directory {config_dir} too? [y/N] ").strip().lower()
        if confirm == "y":
            shutil.rmtree(config_dir)
            print(f"Removed {config_dir}")


def cmd_list(cfg: dict, args):
    names = inst.list_instance_names(cfg)
    print("Configured instances:")
    if not names:
        print(f"  (none yet -- config base {cfg['config_base']} doesn't exist or is empty)")
        return
    for user in names:
        status = inst.service_status(cfg, user)
        meta = inst.read_meta(cfg, user)
        type_ = meta.get("type", "unknown")
        print(f"  - {user} ({status}, {type_})")


def cmd_join(cfg: dict, args):
    web_mod.run(cfg)


def cmd_monitor(cfg: dict, args):
    monitor_mod.run(cfg)


def cmd_reap(cfg: dict, args):
    reap_mod.run(cfg, dry_run=args.dry_run)


def cmd_setup(cfg: dict, args):
    path = cfgmod.config_path()
    if not path.exists() or args.reset_config:
        overrides = {}
        if args.run_as_user:
            overrides["run_as_user"] = args.run_as_user
        if args.config_base:
            overrides["config_base"] = args.config_base
        if args.jellyfin_url:
            overrides["jellyfin_url"] = args.jellyfin_url
        cfgmod.write_default_config(path, overrides)
        print(f"Wrote config to {path}")
        cfg = cfgmod.load_config()
    else:
        print(f"Config already exists at {path} (use --reset-config to overwrite)")

    _install_default_images(cfg)

    systemd.install_sudoers_rule(cfg)
    print(f"Installed sudoers rule at {systemd.SUDOERS_PATH}")

    systemd.install_manager_units(cfg, enable=not args.no_enable)
    print("Installed and enabled systemd units:")
    print(f"  - {systemd.SHIM_TEMPLATE_UNIT}")
    print(f"  - {systemd.JOIN_UNIT}")
    print(f"  - {systemd.REAPER_SERVICE_UNIT} / {systemd.REAPER_TIMER_UNIT}")
    print()
    print(f"Edit {path} to set your Jellyfin server URL, LAN IP, image directory, etc.,")
    print("then re-run `jellyfin-shim-manager setup` (or just restart the join service).")


def _install_default_images(cfg: dict):
    """Copies the bundled placeholder status-screen images to image_dir if missing."""
    image_dir = Path(cfg["image_dir"])
    image_dir.mkdir(parents=True, exist_ok=True)
    assets = importlib.resources.files("jellyfin_shim_manager") / "assets"
    for name in ("jellyfin-error.png", "no-network.png", "ready.png", "server-down.png"):
        dest = image_dir / name
        if dest.exists():
            continue
        src = assets / name
        dest.write_bytes(src.read_bytes())
    print(f"Placeholder status images installed to {image_dir} (customize these, and add join-qr.png)")


def cmd_config(cfg: dict, args):
    path = cfgmod.config_path()
    if args.init:
        cfgmod.write_default_config(path)
        print(f"Wrote default config to {path}")
        return
    if not path.exists():
        print(f"No config file at {path} yet -- using built-in defaults.")
        print("Run `jellyfin-shim-manager config --init` to write one out.")
    import json
    print(json.dumps(cfg, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jellyfin-shim-manager",
        description="Manage multi-user jellyfin-mpv-shim instances on a shared box.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="interactively add and enable a new permanent instance")
    p_add.add_argument("username")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="stop, disable, and optionally delete an instance")
    p_remove.add_argument("username")
    p_remove.add_argument("-y", "--yes", action="store_true", help="delete config dir without prompting")
    p_remove.set_defaults(func=cmd_remove)

    p_list = sub.add_parser("list", help="list configured instances and their status")
    p_list.set_defaults(func=cmd_list)

    p_join = sub.add_parser("join", help="run the QR-code onboarding web app")
    p_join.set_defaults(func=cmd_join)

    p_monitor = sub.add_parser("monitor", help="run the framebuffer status screen loop")
    p_monitor.set_defaults(func=cmd_monitor)

    p_reap = sub.add_parser("reap", help="disable+delete expired temporary instances")
    p_reap.add_argument("--dry-run", action="store_true", help="only print what would be reaped")
    p_reap.set_defaults(func=cmd_reap)

    p_setup = sub.add_parser("setup", help="install systemd units, sudoers rule, and config file")
    p_setup.add_argument("--reset-config", action="store_true", help="overwrite an existing config file")
    p_setup.add_argument("--run-as-user", help="linux user the shim services run as (default: pi)")
    p_setup.add_argument("--config-base", help="directory to store per-user shim configs")
    p_setup.add_argument("--jellyfin-url", help="Jellyfin server URL, e.g. http://192.168.1.10:8096")
    p_setup.add_argument("--no-enable", action="store_true", help="install units without enabling/starting them")
    p_setup.set_defaults(func=cmd_setup)

    p_config = sub.add_parser("config", help="show or initialize the config file")
    p_config.add_argument("--init", action="store_true", help="write out the default config file")
    p_config.set_defaults(func=cmd_config)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = cfgmod.load_config()
    args.func(cfg, args)


if __name__ == "__main__":
    main()

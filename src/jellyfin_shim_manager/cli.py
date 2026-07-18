"""jellyfin-shim-manager — manage multi-user jellyfin-mpv-shim instances on a
shared, headless Jellyfin client box (e.g. a Raspberry Pi).

Subcommands:
  add <user>       interactively log in a new permanent instance and enable it
  remove <user>     stop, disable, and optionally delete an instance
  list              show all configured instances and their status
  join              run the join (onboarding) + admin web app
  monitor           run the framebuffer status screen loop
  reap              disable+delete expired temporary instances (for the timer)
  setup             install systemd units, sudoers rule, and config file
  config            show or initialize the config file
  admin             manage the admin panel account
  deps              check for (and optionally install) required tools
  generate-qr       composite the /join QR code onto ready.png -> join-qr.png
  update            pull the latest version and reinstall via pipx
  uninstall         remove systemd units, sudoers rule, and optionally data
"""

import argparse
import getpass
import importlib.resources
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import auth
from . import config as cfgmod
from . import deps
from . import instances as inst
from . import monitor as monitor_mod
from . import privileged
from . import qrgen
from . import reap as reap_mod
from . import systemd
from . import web as web_mod

DEFAULT_SRC_DIR = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_SRC", str(Path.home() / ".local" / "src" / "jellyfin-shim-manager"))
)


def cmd_add(cfg: dict, args):
    user = args.username
    config_dir = inst.instance_dir(cfg, user)

    systemd.ensure_shim_template_unit(cfg)

    if config_dir.is_dir() and (config_dir / "cred.json").exists():
        print(f"Config for '{user}' already exists at {config_dir}")
        confirm = input("Re-run login and overwrite it? [y/N] ").strip().lower()
        if confirm != "y":
            print("Leaving existing config in place. Re-enabling service just in case...")
            inst.run_systemctl("enable", "--now", inst.unit_name(cfg, user))
            return
        shutil.rmtree(config_dir)

    config_dir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"Logging in '{user}' against {cfg['jellyfin_url']}")
    password = getpass.getpass("Jellyfin password: ")
    print("Contacting the server...")
    try:
        inst.run_shim_login(config_dir, cfg["jellyfin_url"], user, password, timeout=cfg["login_timeout_seconds"])
    except inst.ShimLoginError as exc:
        print(str(exc), file=sys.stderr)
        shutil.rmtree(config_dir, ignore_errors=True)
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
    interactive = sys.stdin.isatty() and not args.non_interactive

    if not path.exists() or args.reset_config:
        overrides = _gather_setup_overrides(args, interactive)
        cfgmod.write_default_config(path, overrides)
        print(f"Wrote config to {path}")
        cfg = cfgmod.load_config()
        if not cfg["jellyfin_url"]:
            print(
                "WARNING: no Jellyfin server URL was set. Logins won't work until you set "
                f"\"jellyfin_url\" in {path}.",
                file=sys.stderr,
            )
    else:
        print(f"Config already exists at {path} (use --reset-config to overwrite)")

    print(f"Services will run as user '{cfg['run_as_user']}'.")
    privileged.ensure_owned_dir(cfg["config_base"], cfg["run_as_user"])
    privileged.ensure_owned_dir(cfg["image_dir"], cfg["run_as_user"])

    _install_default_images(cfg)

    if not args.skip_deps:
        _ensure_required_deps()

    if args.tls:
        cert, key = systemd.generate_self_signed_cert(cfg)
        cfgmod.write_default_config(path, {**cfg, "tls_enabled": True, "tls_cert": str(cert), "tls_key": str(key)})
        cfg = cfgmod.load_config()
        print(f"Generated a self-signed TLS cert at {cert} (owned by '{cfg['run_as_user']}') and enabled TLS.")

    # Run after the --tls block above so the composited QR encodes the
    # final http(s) scheme, not whatever it was before --tls flipped it on.
    _generate_join_qr(cfg, force=args.regenerate_qr)

    auth.load_or_create_secret_key(owner=cfg["run_as_user"])
    if not interactive:
        if not auth.admin_configured():
            print()
            print("No interactive terminal -- skipping admin account setup.")
            print("Run `jellyfin-shim-manager admin set-password` once you have a terminal.")
    elif not auth.admin_configured():
        print()
        print("No admin account exists yet -- set one up now for the /admin panel.")
        _prompt_set_admin_password(owner=cfg["run_as_user"])
    elif args.reset_admin_password:
        _prompt_set_admin_password(owner=cfg["run_as_user"])

    systemd.install_sudoers_rule(cfg)
    print(f"Installed sudoers rule at {systemd.SUDOERS_PATH}")

    systemd.install_manager_units(cfg, enable=not args.no_enable)
    print("Installed and enabled systemd units:")
    print(f"  - {systemd.SHIM_TEMPLATE_UNIT}")
    print(f"  - {systemd.JOIN_UNIT}")
    print(f"  - {systemd.REAPER_SERVICE_UNIT} / {systemd.REAPER_TIMER_UNIT}")
    print()
    print(f"Config is at {path} -- edit it any time, then re-run `jellyfin-shim-manager setup`")
    print("(or just restart the join service) to pick up changes.")


def _normalize_jellyfin_url(url: str) -> str:
    """Prepends http:// if the user left the scheme off (e.g. '192.168.1.10:8096')."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    return url


def _gather_setup_overrides(args, interactive: bool) -> dict:
    defaults = cfgmod.DEFAULTS

    run_as_user = args.run_as_user
    if not run_as_user and interactive:
        run_as_user = input(f"Linux user the shim services should run as [{defaults['run_as_user']}]: ").strip()
    run_as_user = run_as_user or defaults["run_as_user"]

    home = cfgmod.home_dir_for(run_as_user)
    default_config_base = str(home / "mpv-shim-configs")
    default_image_dir = str(home / "Resources")

    config_base = args.config_base
    if not config_base and interactive:
        config_base = input(f"Directory to store per-user shim configs [{default_config_base}]: ").strip()
    config_base = config_base or default_config_base

    if args.jellyfin_url:
        jellyfin_url = _normalize_jellyfin_url(args.jellyfin_url)
        if not urlparse(jellyfin_url).hostname:
            print(
                f"WARNING: couldn't parse a hostname out of --jellyfin-url '{args.jellyfin_url}' "
                f"(normalized to '{jellyfin_url}'). Fix \"jellyfin_url\" in the config by hand.",
                file=sys.stderr,
            )
    elif interactive:
        jellyfin_url = ""
        while True:
            raw = input("Jellyfin server URL (e.g. http://192.168.1.10:8096): ").strip()
            if not raw:
                print("This is required -- jellyfin-mpv-shim needs it to log in.")
                continue
            jellyfin_url = _normalize_jellyfin_url(raw)
            if urlparse(jellyfin_url).hostname:
                break
            print(f"Couldn't parse a hostname out of '{raw}' -- try again (e.g. http://192.168.1.10:8096).")
    else:
        jellyfin_url = defaults["jellyfin_url"]

    parsed = urlparse(jellyfin_url) if jellyfin_url else None
    local_ip = args.local_ip or (parsed.hostname if parsed else None) or defaults["local_ip"]
    jellyfin_port = (parsed.port if parsed and parsed.port else None) or defaults["jellyfin_port"]

    return {
        "run_as_user": run_as_user,
        "config_base": config_base,
        "jellyfin_url": jellyfin_url,
        "local_ip": local_ip,
        "jellyfin_port": jellyfin_port,
        "image_dir": default_image_dir,
    }


def _prompt_set_admin_password(owner: str = None):
    username = input("Admin username [admin]: ").strip() or "admin"
    while True:
        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if not password:
            print("Password cannot be empty.")
            continue
        if password != confirm:
            print("Passwords didn't match, try again.")
            continue
        break
    auth.set_admin_password(username, password, owner=owner)
    print(f"Admin account '{username}' saved to {cfgmod.ADMIN_CREDENTIALS_PATH}")


def cmd_admin(cfg: dict, args):
    if args.action == "set-password":
        _prompt_set_admin_password(owner=cfg["run_as_user"])


def _ensure_required_deps():
    status = deps.check_all()
    missing_required = [n for n in deps.REQUIRED if not status[n]]
    if not missing_required:
        print("Required dependencies already installed: " + ", ".join(deps.REQUIRED))
        return
    print()
    print("Missing required dependencies: " + ", ".join(missing_required))
    deps.install_missing(missing_required)
    print("Installed: " + ", ".join(missing_required))


def cmd_deps(cfg: dict, args):
    status = deps.check_all()
    print("Dependency status:")
    for name in deps.REQUIRED + deps.OPTIONAL:
        kind = "required" if name in deps.REQUIRED else "optional"
        state = "installed" if status[name] else "MISSING"
        print(f"  - {name:<20} {state:<10} ({kind}) -- {deps.DESCRIPTIONS[name]}")

    if not args.install:
        if any(not ok for ok in status.values()):
            print()
            print("Run `jellyfin-shim-manager deps --install` to install what's missing.")
        return

    missing = [n for n, ok in status.items() if not ok]
    if args.required_only:
        missing = [n for n in missing if n in deps.REQUIRED]
    if not missing:
        print()
        print("Nothing to install.")
        return

    print()
    print(f"Installing: {', '.join(missing)}")
    deps.install_missing(missing)
    print("Done.")


def cmd_update(cfg: dict, args):
    src_dir = Path(args.src_dir) if args.src_dir else DEFAULT_SRC_DIR
    if not (src_dir / ".git").is_dir():
        print(f"No git checkout found at {src_dir}.", file=sys.stderr)
        print(
            "Re-run the installer to update instead:\n"
            "  curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/install.sh | bash",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Pulling latest changes in {src_dir}...")
    subprocess.run(["git", "-C", str(src_dir), "pull", "--ff-only"], check=True)

    pipx = shutil.which("pipx")
    if not pipx:
        print("pipx not found -- install it with `python3 -m pip install --user pipx` and re-run.", file=sys.stderr)
        sys.exit(1)

    print("Reinstalling with pipx...")
    subprocess.run([pipx, "install", "--force", str(src_dir)], check=True)

    new_exe = shutil.which("jellyfin-shim-manager")
    if new_exe:
        print("Refreshing systemd units and config (safe to re-run; existing config.json is left alone)...")
        subprocess.run([new_exe, "setup"], check=False)

    print("Update complete.")


def cmd_uninstall(cfg: dict, args):
    if not args.yes:
        scope = "the join/admin web app and reaper timer"
        if args.purge_instances:
            scope += ", every configured instance, and the shim template unit + sudoers rule"
        if args.purge_config:
            scope += f", and {cfgmod.config_path().parent} (config, admin credentials, TLS certs)"
        confirm = input(f"This will remove: {scope}. Continue? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    print("Stopping and removing jellyfin-shim-manager-join / -reaper units...")
    systemd.uninstall_manager_units()

    if args.purge_instances:
        print("Stopping, disabling, and deleting all configured instances...")
        inst.purge_all_instances(cfg)
        print("Removing the jellyfin-mpv-shim@ template unit and sudoers rule...")
        systemd.remove_shim_template_unit()
        systemd.remove_sudoers_rule()
    else:
        print("Leaving per-user jellyfin-mpv-shim@ instances and the sudoers rule in place")
        print("(pass --purge-instances to remove those too).")

    if args.purge_config:
        etc_dir = cfgmod.config_path().parent
        print(f"Removing {etc_dir}...")
        subprocess.run(["sudo", "rm", "-rf", str(etc_dir)], check=False)

    print()
    print("jellyfin-shim-manager's systemd units have been removed.")
    print("To remove the CLI itself: pipx uninstall jellyfin-shim-manager")
    print("(the uninstall.sh script in the repo does all of the above in one step)")


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
    print(f"Placeholder status images installed to {image_dir} (customize these any time)")


def _generate_join_qr(cfg: dict, force: bool = False):
    """Wraps qrgen.generate_join_qr with the warning-not-abort handling setup wants."""
    try:
        dest = qrgen.generate_join_qr(cfg, force=force)
    except qrgen.QrGenerationError as exc:
        print(f"WARNING: couldn't generate join-qr.png: {exc}", file=sys.stderr)
        return
    except ImportError as exc:
        print(f"WARNING: qrcode/Pillow not installed -- skipping join-qr.png ({exc})", file=sys.stderr)
        return
    print(f"join-qr.png ready at {dest} (pass --regenerate-qr to rebuild it)")


def cmd_generate_qr(cfg: dict, args):
    try:
        dest = qrgen.generate_join_qr(cfg, force=args.force)
    except qrgen.QrGenerationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(f"qrcode/Pillow not installed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {dest} (join URL: {qrgen.join_url(cfg)})")


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

    p_join = sub.add_parser("join", help="run the join (onboarding) + admin web app")
    p_join.set_defaults(func=cmd_join)

    p_monitor = sub.add_parser("monitor", help="run the framebuffer status screen loop")
    p_monitor.set_defaults(func=cmd_monitor)

    p_reap = sub.add_parser("reap", help="disable+delete expired temporary instances")
    p_reap.add_argument("--dry-run", action="store_true", help="only print what would be reaped")
    p_reap.set_defaults(func=cmd_reap)

    p_setup = sub.add_parser("setup", help="install systemd units, sudoers rule, and config file")
    p_setup.add_argument("--reset-config", action="store_true", help="overwrite an existing config file")
    p_setup.add_argument(
        "--run-as-user",
        help="linux user the shim services run as (default: whoever invoked this, or $SUDO_USER under sudo)",
    )
    p_setup.add_argument("--config-base", help="directory to store per-user shim configs (default: <their home>/mpv-shim-configs)")
    p_setup.add_argument("--jellyfin-url", help="Jellyfin server URL, e.g. http://192.168.1.10:8096")
    p_setup.add_argument("--local-ip", help="LAN IP `monitor` health-checks (default: parsed from --jellyfin-url)")
    p_setup.add_argument("--no-enable", action="store_true", help="install units without enabling/starting them")
    p_setup.add_argument("--tls", action="store_true", help="generate a self-signed cert and enable TLS for the web app")
    p_setup.add_argument("--reset-admin-password", action="store_true", help="prompt to set a new admin password even if one exists")
    p_setup.add_argument("--skip-deps", action="store_true", help="don't check/install mpv and jellyfin-mpv-shim")
    p_setup.add_argument(
        "--non-interactive", action="store_true",
        help="never prompt, even if a terminal is attached (use --run-as-user/--jellyfin-url/etc. instead)",
    )
    p_setup.add_argument(
        "--regenerate-qr", action="store_true",
        help="rebuild join-qr.png even if it already exists (e.g. after changing ready.png or local_ip)",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_qr = sub.add_parser("generate-qr", help="composite the /join QR code onto ready.png -> join-qr.png")
    p_qr.add_argument("--force", action="store_true", help="regenerate even if join-qr.png already exists")
    p_qr.set_defaults(func=cmd_generate_qr)

    p_config = sub.add_parser("config", help="show or initialize the config file")
    p_config.add_argument("--init", action="store_true", help="write out the default config file")
    p_config.set_defaults(func=cmd_config)

    p_admin = sub.add_parser("admin", help="manage the admin panel account")
    p_admin.add_argument("action", choices=["set-password"])
    p_admin.set_defaults(func=cmd_admin)

    p_deps = sub.add_parser("deps", help="check for (and optionally install) required tools like jellyfin-mpv-shim and mpv")
    p_deps.add_argument("--install", action="store_true", help="install missing dependencies")
    p_deps.add_argument("--required-only", action="store_true", help="with --install, skip optional tools (openssl, qrencode, fbi)")
    p_deps.set_defaults(func=cmd_deps)

    p_update = sub.add_parser("update", help="pull the latest version and reinstall via pipx")
    p_update.add_argument("--src-dir", help="path to the git checkout (default: ~/.local/src/jellyfin-shim-manager)")
    p_update.set_defaults(func=cmd_update)

    p_uninstall = sub.add_parser("uninstall", help="remove systemd units, sudoers rule, and optionally data")
    p_uninstall.add_argument("--purge-instances", action="store_true", help="also remove every configured jellyfin-mpv-shim instance")
    p_uninstall.add_argument("--purge-config", action="store_true", help="also delete /etc/jellyfin-shim-manager")
    p_uninstall.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")
    p_uninstall.set_defaults(func=cmd_uninstall)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = cfgmod.load_config()
    args.func(cfg, args)


if __name__ == "__main__":
    main()

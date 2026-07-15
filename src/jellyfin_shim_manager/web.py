"""jellyfin-shim-manager web app: the public /join onboarding page and the
session-authenticated /admin panel, served from one Flask app/process.

Login flow: jellyfin-mpv-shim has no Quick Connect CLI flag. Instead its
--server/--username/--password flags feed the `add` subcommand, which stores
credentials non-interactively. So /join collects a username+password, runs
that synchronously (a few seconds), and on success starts the systemd
service for that instance -- no more polling for a code.
"""

import shutil
import sys
import uuid
from pathlib import Path

from flask import (
    Flask, abort, flash, get_flashed_messages, jsonify, redirect,
    render_template, request, session, url_for,
)

from . import auth
from . import instances as inst
from . import reap as reap_mod

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _create_instance(cfg: dict, username: str, password: str, label: str, login_type: str) -> str:
    """Logs in via jellyfin-mpv-shim, then names, tags, and starts the instance.

    Raises inst.ShimLoginError on a failed login (bad credentials, unreachable
    server, etc.) -- nothing is left behind in that case.
    """
    if login_type not in ("permanent", "temporary"):
        raise ValueError("type must be 'permanent' or 'temporary'")

    base = inst.config_base(cfg)
    pending_dir = base / inst.PENDING_DIRNAME
    pending_dir.mkdir(parents=True, exist_ok=True)
    session_dir = pending_dir / uuid.uuid4().hex[:8]
    session_dir.mkdir(parents=True)

    try:
        inst.run_shim_login(
            session_dir, cfg["jellyfin_url"], username, password,
            timeout=cfg["login_timeout_seconds"],
        )
    except inst.ShimLoginError:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise

    base_name = label or (username if login_type == "permanent" else f"guest-{uuid.uuid4().hex[:6]}")
    final_name = base_name
    n = 2
    while (base / final_name).exists():
        final_name = f"{base_name}-{n}"
        n += 1

    final_dir = base / final_name
    shutil.move(str(session_dir), str(final_dir))
    inst.write_meta(final_dir, login_type)

    unit = inst.unit_name(cfg, final_name)
    if login_type == "permanent":
        inst.run_systemctl("enable", "--now", unit)
    else:
        inst.run_systemctl("start", unit)  # not enabled -- won't survive a reboot on its own

    return final_name


def create_app(cfg: dict) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.secret_key = auth.load_or_create_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg.get("tls_enabled")),
    )

    inst.config_base(cfg).mkdir(parents=True, exist_ok=True)

    # --- public onboarding page ---

    @app.route("/")
    def index():
        return redirect(url_for("join_page"))

    @app.route("/join")
    def join_page():
        return render_template("join.html")

    @app.route("/api/join", methods=["POST"])
    def api_join():
        body = request.get_json(force=True, silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        label = (body.get("name") or "").strip()
        login_type = body.get("type")

        if not username or not password:
            return jsonify({"error": "Username and password are required."}), 400
        if login_type not in ("permanent", "temporary"):
            return jsonify({"error": "type must be 'permanent' or 'temporary'."}), 400

        try:
            name = _create_instance(cfg, username, password, label, login_type)
        except inst.ShimLoginError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify({"status": "ok", "name": name})

    # --- admin panel ---

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        error = None
        if not auth.admin_configured():
            error = "No admin account is set up yet. Run `jellyfin-shim-manager admin set-password` on the server."
        elif request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if auth.verify_admin(username, password):
                session.clear()
                session["admin"] = username
                return redirect(request.args.get("next") or url_for("admin_dashboard"))
            error = "Invalid username or password."
        return render_template("admin_login.html", error=error)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.route("/admin")
    @auth.login_required
    def admin_dashboard():
        rows = []
        for user in inst.list_instance_names(cfg):
            meta = inst.read_meta(cfg, user)
            rows.append({
                "name": user,
                "status": inst.service_status(cfg, user),
                "type": meta.get("type", "unknown"),
                "created": meta.get("created"),
                "last_active": meta.get("last_active"),
            })
        return render_template(
            "admin_dashboard.html",
            instances=rows,
            csrf_token=auth.csrf_token(),
            admin_user=session.get("admin"),
            messages=get_flashed_messages(with_categories=True),
        )

    @app.route("/admin/instances", methods=["POST"])
    @auth.login_required
    def admin_add_instance():
        if not auth.csrf_valid(request.form.get("csrf_token")):
            abort(400)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        label = request.form.get("name", "").strip()
        login_type = request.form.get("type")

        if not (username and password and login_type in ("permanent", "temporary")):
            flash("Username, password, and type are required.", "error")
        else:
            try:
                name = _create_instance(cfg, username, password, label, login_type)
                flash(f"Added instance '{name}'.", "success")
            except inst.ShimLoginError as exc:
                flash(str(exc), "error")

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/instances/<name>/<action>", methods=["POST"])
    @auth.login_required
    def admin_instance_action(name, action):
        if not auth.csrf_valid(request.form.get("csrf_token")):
            abort(400)
        if name not in inst.list_instance_names(cfg):
            abort(404)

        unit = inst.unit_name(cfg, name)
        if action == "start":
            inst.run_systemctl("start", unit, check=False)
            flash(f"Started '{name}'.", "success")
        elif action == "stop":
            inst.run_systemctl("stop", unit, check=False)
            flash(f"Stopped '{name}'.", "success")
        elif action == "remove":
            inst.run_systemctl("disable", "--now", unit, check=False)
            shutil.rmtree(inst.instance_dir(cfg, name), ignore_errors=True)
            flash(f"Removed '{name}'.", "success")
        else:
            abort(404)

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/reap", methods=["POST"])
    @auth.login_required
    def admin_reap():
        if not auth.csrf_valid(request.form.get("csrf_token")):
            abort(400)
        reaped = reap_mod.run(cfg)
        flash(f"Reaped: {', '.join(reaped)}" if reaped else "Nothing to reap.", "success")
        return redirect(url_for("admin_dashboard"))

    return app


def run(cfg: dict):
    app = create_app(cfg)

    ssl_context = None
    if cfg.get("tls_enabled"):
        cert, key = cfg.get("tls_cert"), cfg.get("tls_key")
        if cert and key and Path(cert).exists() and Path(key).exists():
            ssl_context = (cert, key)
        else:
            print(
                f"tls_enabled is true but {cert} / {key} were not found -- "
                "serving plain HTTP instead. Run `jellyfin-shim-manager setup --tls`.",
                file=sys.stderr,
            )

    app.run(host=cfg["bind_host"], port=cfg["bind_port"], ssl_context=ssl_context)

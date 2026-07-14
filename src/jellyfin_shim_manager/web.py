"""jellyfin-shim-manager join — local web app for onboarding new users.

Lets a new user scan a QR code, log in via Jellyfin Quick Connect, and choose
a permanent or temporary shim instance on this box.

VERIFY BEFORE RELYING ON THIS (see README):
  1. The exact text jellyfin-mpv-shim prints for a Quick Connect code.
     `code_regex` in the config is a best guess (a bare 6-digit number) --
     run `jellyfin-mpv-shim --config /tmp/test --quick-connect --server <url>`
     by hand once and adjust if it doesn't match.
  2. This process needs permission to run `systemctl` for the shim service
     prefix without a password prompt -- see `jellyfin-shim-manager setup`.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import instances as inst

TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(cfg: dict) -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

    config_base = inst.config_base(cfg)
    pending_dir = config_base / inst.PENDING_DIRNAME
    pending_dir.mkdir(parents=True, exist_ok=True)

    code_re = re.compile(cfg["code_regex"])
    pending = {}  # id -> {"process", "config_dir", "log_path", "started"}
    pending_lock = threading.Lock()

    def cleanup_abandoned_sessions():
        while True:
            time.sleep(60)
            now = time.time()
            with pending_lock:
                stale_ids = [
                    sid for sid, e in pending.items()
                    if now - e["started"] > cfg["pending_timeout_seconds"]
                ]
                stale = [(sid, pending.pop(sid)) for sid in stale_ids]
            for sid, entry in stale:
                _kill(entry["process"])
                shutil.rmtree(entry["config_dir"], ignore_errors=True)

    threading.Thread(target=cleanup_abandoned_sessions, daemon=True).start()

    @app.route("/join")
    def join_page():
        return render_template("join.html")

    @app.route("/join/start", methods=["POST"])
    def join_start():
        session_id = uuid.uuid4().hex[:8]
        session_dir = pending_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        log_path = session_dir / "shim.log"

        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            [
                "jellyfin-mpv-shim",
                "--config", str(session_dir),
                "--quick-connect",
                "--server", cfg["jellyfin_url"],
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        with pending_lock:
            pending[session_id] = {
                "process": proc,
                "config_dir": session_dir,
                "log_path": log_path,
                "started": time.time(),
            }

        return jsonify({"id": session_id})

    @app.route("/join/status/<session_id>")
    def join_status(session_id):
        with pending_lock:
            entry = pending.get(session_id)
        if entry is None:
            return jsonify({"error": "unknown session"}), 404

        code = None
        if entry["log_path"].exists():
            text = entry["log_path"].read_text(errors="ignore")
            match = code_re.search(text)
            if match:
                code = match.group(1)

        done = (entry["config_dir"] / "conf.json").exists()
        return jsonify({"code": code, "done": done})

    @app.route("/join/finish", methods=["POST"])
    def join_finish():
        body = request.get_json(force=True)
        session_id = body.get("id")
        login_type = body.get("type")
        label = (body.get("name") or "").strip()

        if login_type not in ("permanent", "temporary"):
            return jsonify({"error": "type must be permanent or temporary"}), 400

        with pending_lock:
            entry = pending.pop(session_id, None)
        if entry is None:
            return jsonify({"error": "unknown or already-finished session"}), 404

        session_dir = entry["config_dir"]
        if not (session_dir / "conf.json").exists():
            return jsonify({"error": "login did not complete yet"}), 409

        # Stop the temporary quick-connect process; the systemd-managed
        # instance takes over using the same, now-populated config dir.
        _kill(entry["process"])

        final_name = label if label else f"{login_type}-{session_id}"
        final_dir = config_base / final_name
        shutil.move(str(session_dir), str(final_dir))

        inst.write_meta(final_dir, login_type)

        unit = inst.unit_name(cfg, final_name)
        if login_type == "permanent":
            inst.run_systemctl("enable", "--now", unit)
        else:
            inst.run_systemctl("start", unit)  # not enabled -> won't survive reboot

        return jsonify({"status": "ok", "name": final_name})

    return app


def _kill(proc):
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except ProcessLookupError:
        pass


def run(cfg: dict):
    app = create_app(cfg)
    app.run(host=cfg["bind_host"], port=cfg["bind_port"])

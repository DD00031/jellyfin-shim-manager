"""Admin authentication: credential storage, session secret key, and the
login_required / CSRF helpers used by the admin panel routes in web.py.

Admin credentials and the session secret key are kept out of config.json
(which may be more widely readable) in their own 0600 files, owned by
run_as_user -- the account the join/admin systemd service actually runs as,
which is what needs to read them back at request time.
"""

import functools
import json
import secrets

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import config as cfgmod
from . import privileged


def admin_configured() -> bool:
    return cfgmod.ADMIN_CREDENTIALS_PATH.exists()


def set_admin_password(username: str, password: str, owner: str = None):
    data = {"username": username, "password_hash": generate_password_hash(password)}
    privileged.write_file(
        cfgmod.ADMIN_CREDENTIALS_PATH, json.dumps(data, indent=2) + "\n", mode=0o600, owner=owner
    )


def verify_admin(username: str, password: str) -> bool:
    if not admin_configured():
        return False
    data = json.loads(cfgmod.ADMIN_CREDENTIALS_PATH.read_text())
    if username != data.get("username"):
        return False
    return check_password_hash(data.get("password_hash", ""), password)


def load_or_create_secret_key(owner: str = None) -> str:
    path = cfgmod.SECRET_KEY_PATH
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    privileged.write_file(path, key + "\n", mode=0o600, owner=owner)
    return key


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["_csrf_token"] = token
    return token


def csrf_valid(submitted: str) -> bool:
    return bool(submitted) and secrets.compare_digest(submitted, session.get("_csrf_token", ""))

import functools
import hashlib
import hmac
import base64
import os
import secrets
import threading
import logging
import time
import uuid
from datetime import datetime, timezone
from flask import Flask, abort, redirect, request, session, url_for
from boxwatchr import config
from boxwatchr.crypto import encrypt_password
from boxwatchr.database import get_config, set_config, bulk_set_config, upsert_account, get_first_account
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.web")

_VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
try:
    with open(_VERSION_FILE) as _f:
        APP_VERSION = _f.read().strip()
except OSError:
    APP_VERSION = "unknown"

app = Flask(__name__, template_folder="templates")

_EMAILS_PAGE_SIZE = 15
_LOGS_PAGE_SIZE = 100
_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
_TLS_MODES = ["ssl", "starttls", "none"]

def _csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]

def _csrf_valid():
    token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(token and hmac.compare_digest(token, _csrf_token()))

def _check_csrf():
    if not _csrf_valid():
        abort(403)

def _require_csrf(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        _check_csrf()
        return f(*args, **kwargs)
    return decorated

def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return "%s:%s" % (base64.b64encode(salt).decode(), base64.b64encode(digest).decode())

def _check_password(password, stored):
    if not stored or not password:
        return False
    if ":" not in stored:
        return False
    try:
        salt_b64, hash_b64 = stored.split(":", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False

_LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo

def _utc_to_local(dt_str):
    if not dt_str:
        return dt_str
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(_LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.warning("Failed to convert UTC timestamp %r to local time: %s", dt_str, e)
        return dt_str

def _local_date_to_utc(date_str, time_str):
    try:
        naive = datetime.strptime("%s %s" % (date_str, time_str), "%Y-%m-%d %H:%M:%S")
        local_dt = naive.replace(tzinfo=_LOCAL_TZ)
        return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.warning("Failed to convert local date %r %r to UTC: %s", date_str, time_str, e)
        return "%s %s" % (date_str, time_str)

def _get_or_create_session_secret():
    stored = get_config("session_secret")
    if stored:
        return stored
    secret = secrets.token_hex(32)
    set_config("session_secret", secret)
    return secret

def _require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not config.SETUP_COMPLETE:
            return redirect(url_for("setup"))
        if config.WEB_PASSWORD and not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _score_class(score):
    if score is None:
        return ""
    if score >= 10:
        return "text-danger fw-bold"
    if score >= 5:
        return "text-warning fw-bold"
    return ""

def _save_app_config(form):
    host = form.get("imap_host", "").strip()
    port = form.get("imap_port", "993").strip()
    username = form.get("imap_username", "").strip()
    new_password = form.get("imap_password", "")
    folder = form.get("imap_folder", "").strip()
    tls_mode = form.get("tls_mode", "ssl").strip()
    if tls_mode not in _TLS_MODES:
        tls_mode = "ssl"

    existing_account = get_first_account()
    existing_encrypted = existing_account["password"] if (existing_account and not new_password) else ""

    try:
        port_int = int(port)
    except ValueError:
        port_int = 993

    account_id = config.ACCOUNT_ID if config.ACCOUNT_ID else str(uuid.uuid4())
    account_name = form.get("account_name", "Default").strip() or "Default"
    encrypted_password = encrypt_password(new_password) if new_password else existing_encrypted

    upsert_account(
        account_id=account_id,
        name=account_name,
        host=host,
        port=port_int,
        username=username,
        password=encrypted_password,
        folder=folder,
        poll_interval=60,
        tls_mode=tls_mode,
    )

    log_level = form.get("log_level", "INFO").strip().upper()
    if log_level not in _LEVELS:
        log_level = "INFO"

    try:
        db_prune_days = int(form.get("db_prune_days", "0"))
        if db_prune_days < 0:
            db_prune_days = 0
    except ValueError:
        db_prune_days = 0

    dry_run = form.get("dry_run") == "true"
    check_for_updates = form.get("check_for_updates") != "false"

    disable_password = form.get("disable_password") == "1"
    new_web_password_raw = form.get("web_password", "")
    if disable_password:
        web_password_stored = ""
    elif new_web_password_raw:
        web_password_stored = _hash_password(new_web_password_raw)
    else:
        web_password_stored = config.WEB_PASSWORD

    bulk_set_config({
        "log_level": log_level,
        "dry_run": "true" if dry_run else "false",
        "web_password": web_password_stored,
        "db_prune_days": str(db_prune_days),
        "check_for_updates": "true" if check_for_updates else "false",
    })

    return web_password_stored

app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.filters["localtime"] = _utc_to_local

@app.context_processor
def _inject_globals():
    return {"dry_run": config.DRYRUN, "app_version": APP_VERSION}

@app.route("/")
def index():
    return redirect(url_for("dashboard"))

def _run_server():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("werkzeug").propagate = False
    logging.getLogger("flask").setLevel(logging.ERROR)
    logging.getLogger("flask").propagate = False
    from werkzeug.serving import make_server
    port = int(os.environ.get("WEB_PORT", 8143))
    server = make_server("0.0.0.0", port, app, threaded=True)
    server.serve_forever()

def start_dashboard():
    app.secret_key = _get_or_create_session_secret()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    # Only mark cookies Secure when the deployment is behind HTTPS. Set SECURE_COOKIES=true
    # in the container environment when proxying through nginx/Caddy/etc. with TLS.
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SECURE_COOKIES", "false").lower() == "true"
    import boxwatchr.web.login
    import boxwatchr.web.setup
    import boxwatchr.web.config
    import boxwatchr.web.dashboard
    import boxwatchr.web.emails
    import boxwatchr.web.email_detail
    import boxwatchr.web.logs
    import boxwatchr.web.rules
    import boxwatchr.web.rule_form
    import boxwatchr.web.training
    import boxwatchr.web.version
    port = int(os.environ.get("WEB_PORT", 8143))
    t = threading.Thread(target=_run_server, daemon=True, name="web-server")
    t.start()
    logger.debug("Web server started on port %d", port)

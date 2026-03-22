import time
import threading
from flask import render_template, request, redirect, session, url_for
from boxwatchr import config, imap
from boxwatchr.database import set_config
from boxwatchr.web.app import app, _require_csrf, _csrf_valid, _save_app_config, _TLS_MODES, _LEVELS, logger

_test_imap_attempts = {}
_test_imap_lock = threading.Lock()
_TEST_IMAP_MAX_ATTEMPTS = 10
_TEST_IMAP_WINDOW = 60.0

def _test_imap_rate_limited():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _test_imap_lock:
        attempts = [t for t in _test_imap_attempts.get(ip, []) if now - t < _TEST_IMAP_WINDOW]
        attempts.append(now)
        _test_imap_attempts[ip] = attempts
        stale = [k for k, v in _test_imap_attempts.items() if k != ip and not any(now - t < _TEST_IMAP_WINDOW for t in v)]
        for k in stale:
            del _test_imap_attempts[k]
        return len(attempts) > _TEST_IMAP_MAX_ATTEMPTS

@app.route("/setup", methods=["GET"])
def setup():
    if config.SETUP_COMPLETE:
        if session.pop("setup_done", False):
            return render_template("setup.html", completed=True, levels=_LEVELS,
                                   tls_modes=_TLS_MODES,
                                   show_logout=False, setup_mode=True)
        return redirect(url_for("dashboard"))
    return render_template("setup.html", levels=_LEVELS, tls_modes=_TLS_MODES,
                           show_logout=False, setup_mode=True)

@app.route("/setup", methods=["POST"])
@_require_csrf
def setup_post():
    if config.SETUP_COMPLETE:
        return redirect(url_for("dashboard"))

    errors = []
    host = request.form.get("imap_host", "").strip()
    username = request.form.get("imap_username", "").strip()
    password = request.form.get("imap_password", "")
    folder = request.form.get("imap_folder", "").strip()

    if not host:
        errors.append("IMAP host is required.")
    if not username:
        errors.append("IMAP username is required.")
    if not password:
        errors.append("IMAP password is required.")
    if not folder:
        errors.append("Watch folder is required. Use Test Credentials to load available folders.")

    if errors:
        return render_template("setup.html", errors=errors, levels=_LEVELS,
                               tls_modes=_TLS_MODES,
                               show_logout=False, setup_mode=True)

    _save_app_config(request.form)
    set_config("setup_complete", "true")
    config.reload()

    logger.info("Setup completed. Restart the container to begin monitoring.")
    session["setup_done"] = True
    return redirect(url_for("setup"))

@app.route("/api/test-imap", methods=["POST"])
def test_imap():
    if config.SETUP_COMPLETE and config.WEB_PASSWORD and not session.get("authenticated"):
        return {"error": "Unauthorized."}, 401

    if not _csrf_valid():
        return {"error": "Invalid CSRF token."}, 403

    if _test_imap_rate_limited():
        logger.warning("test-imap rate limit exceeded for %s", request.remote_addr)
        return {"error": "Too many attempts. Try again in a minute."}, 429

    data = request.get_json() or {}
    host = data.get("host", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    tls_mode = data.get("tls_mode", "ssl")
    if tls_mode not in _TLS_MODES:
        tls_mode = "ssl"

    try:
        port = int(data.get("port", 993))
    except (ValueError, TypeError):
        return {"success": False, "error": "Port must be a number."}

    if not host or not username or not password:
        return {"success": False, "error": "Host, username, and password are required."}

    try:
        from imapclient import IMAPClient
        from imapclient.exceptions import LoginError
        use_ssl = tls_mode == "ssl"
        client = IMAPClient(host, port=port, ssl=use_ssl, timeout=10)
        if tls_mode == "starttls":
            client.starttls()
        try:
            client.login(username, password)
            folders = sorted(f[2] for f in client.list_folders())
            client.logout()
            return {
                "success": True,
                "folders": folders,
            }
        except LoginError as e:
            try:
                client.logout()
            except Exception:
                pass
            return {"success": False, "error": "Authentication failed: %s" % e}
        except Exception as e:
            try:
                client.logout()
            except Exception:
                pass
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": "Could not connect to %s:%s: %s" % (host, port, e)}

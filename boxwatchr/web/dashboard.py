import os
import json
import hmac
import hashlib
import base64
import secrets
import sqlite3
import yaml
import logging
import threading
import time
import collections
import functools
from datetime import datetime, timezone
from email import message_from_string
from flask import Flask, render_template, request, redirect, url_for, session, abort
from boxwatchr import config, imap, spam
from boxwatchr.notes import action_sentence
from boxwatchr.database import get_connection, set_user_action, enqueue_email_update, get_config, set_config, bulk_set_config
from boxwatchr.rules import validate_rule, check_rule, TERMINAL_ACTIONS
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.web")

app = Flask(__name__, template_folder="templates")

_EMAILS_PAGE_SIZE = 15
_LOGS_PAGE_SIZE = 100
_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
_SPAM_ACTIONS = ["spam", "delete"]
_SPAM_LEARNING_OPTIONS = ["both", "spam", "ham", "off"]
_TLS_MODES = ["ssl", "starttls", "none"]

_folder_cache = {"folders": [], "expires": 0.0, "fetching": False}
_folder_cache_lock = threading.Lock()

_login_failures = {}
_login_failures_lock = threading.Lock()
_LOGIN_WINDOW = 60.0
_LOGIN_MAX_FAILURES = 5

_test_imap_attempts = {}
_test_imap_lock = threading.Lock()
_TEST_IMAP_MAX_ATTEMPTS = 10

def _is_rate_limited():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _login_failures_lock:
        failures = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW]
        _login_failures[ip] = failures
        return len(failures) >= _LOGIN_MAX_FAILURES

def _record_login_failure():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _login_failures_lock:
        failures = _login_failures.get(ip, [])
        failures.append(now)
        _login_failures[ip] = failures

def _test_imap_rate_limited():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _test_imap_lock:
        attempts = [t for t in _test_imap_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
        attempts.append(now)
        _test_imap_attempts[ip] = attempts
        return len(attempts) > _TEST_IMAP_MAX_ATTEMPTS

def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return "%s:%s" % (base64.b64encode(salt).decode(), base64.b64encode(digest).decode())

def _check_password(password, stored):
    if not stored or not password:
        return False
    if ":" not in stored:
        return hmac.compare_digest(password, stored)
    try:
        salt_b64, hash_b64 = stored.split(":", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False

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

app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.filters["localtime"] = _utc_to_local

@app.context_processor
def _inject_globals():
    return {"dry_run": config.DRYRUN}

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
    if score >= config.SPAM_THRESHOLD:
        return "text-danger fw-bold"
    if score <= config.HAM_THRESHOLD:
        return "text-info"
    return "text-warning fw-bold"

def _extract_message_id(raw_headers):
    if not raw_headers:
        return ""
    try:
        msg_obj = message_from_string(raw_headers)
        return (msg_obj.get("Message-ID") or "").strip()
    except Exception:
        return ""

def _imap_find_by_message_id(client, message_id, folders):
    for folder in folders:
        if not folder:
            continue
        try:
            client.select_folder(folder)
            uids = client.search(["HEADER", "Message-ID", message_id])
            if uids:
                logger.debug("Found message-id %r in folder %s (UID %s)", message_id, folder, uids[0])
                return uids[0], folder
        except Exception as e:
            logger.debug("Could not search folder %s for message-id %r: %s", folder, message_id, e)
    logger.debug("Message-id %r not found in any of: %s", message_id, folders)
    return None, None

def _learn_from_imap(email_id, raw_headers, learn_type, search_folders):
    message_id = _extract_message_id(raw_headers)
    if not message_id or not spam.should_learn(learn_type):
        logger.debug(
            "Skipping %s learning for email %s: message_id=%r, should_learn=%s",
            learn_type, email_id, message_id, spam.should_learn(learn_type)
        )
        return
    logger.debug("Fetching RFC822 for %s learning: email=%s, message_id=%r", learn_type, email_id, message_id)
    try:
        client = imap.connect()
        try:
            uid, found_folder = _imap_find_by_message_id(client, message_id, search_folders)
            if uid is not None:
                logger.debug("Fetching RFC822 for UID %s in %s for %s learning", uid, found_folder, learn_type)
                result = client.fetch([uid], ["RFC822"])
                raw_message = result.get(uid, {}).get(b"RFC822", b"")
                if raw_message:
                    if learn_type == "spam":
                        spam.learn_spam(raw_message, email_id=email_id)
                    else:
                        spam.learn_ham(raw_message, email_id=email_id)
                else:
                    logger.warning("RFC822 body empty for UID %s during %s learning", uid, learn_type)
            else:
                logger.warning("Email %s not found in IMAP for %s learning", email_id, learn_type)
        finally:
            client.logout()
    except Exception as e:
        logger.error("IMAP error during %s learning for email %s: %s", learn_type, email_id, e)

def _resolve_run_actions(rule_actions):
    actions = []
    non_terminal = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS]
    terminal = [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]
    for action in non_terminal + terminal:
        action_type = action["type"]
        if action_type == "delete":
            if not config.IMAP_TRASH_FOLDER:
                raise RuntimeError("trash folder not configured — set it in the Config page")
            actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
        elif action_type == "spam":
            if not config.IMAP_SPAM_FOLDER:
                raise RuntimeError("spam folder not configured — set it in the Config page")
            actions.append({"type": "spam", "destination": config.IMAP_SPAM_FOLDER})
        elif action_type == "move":
            actions.append({"type": "move", "destination": action["destination"]})
        else:
            actions.append({"type": action_type})
    return actions

def _get_stats():
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM emails WHERE processed = 0").fetchone()[0]

        spam_caught = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE rspamd_learned = 'spam'"
        ).fetchone()[0]

        ham_learned = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE rspamd_learned = 'ham'"
        ).fetchone()[0]

        rule_rows = conn.execute(
            "SELECT rule_matched FROM emails WHERE rule_matched IS NOT NULL"
        ).fetchall()

        rule_counts = collections.Counter()
        for row in rule_rows:
            try:
                rule = json.loads(row["rule_matched"])
                rule_counts[rule["name"]] += 1
            except (json.JSONDecodeError, KeyError):
                pass

        score_rows = conn.execute(
            "SELECT spam_score FROM emails WHERE spam_score IS NOT NULL"
        ).fetchall()

        buckets = {"<0": 0, "0-2": 0, "2-5": 0, "5-10": 0, "10-15": 0, "15+": 0}
        for row in score_rows:
            s = row["spam_score"]
            if s < 0:
                buckets["<0"] += 1
            elif s < 2:
                buckets["0-2"] += 1
            elif s < 5:
                buckets["2-5"] += 1
            elif s < 10:
                buckets["5-10"] += 1
            elif s < 15:
                buckets["10-15"] += 1
            else:
                buckets["15+"] += 1

        return {
            "total": total,
            "pending": pending,
            "spam_caught": spam_caught,
            "ham_learned": ham_learned,
            "rule_counts": rule_counts.most_common(),
            "score_buckets": buckets,
        }
    except sqlite3.Error as e:
        logger.error("Failed to query stats: %s", e)
        raise
    finally:
        conn.close()

@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.SETUP_COMPLETE:
        return redirect(url_for("setup"))
    if not config.WEB_PASSWORD:
        return redirect(url_for("index"))

    if request.method == "POST":
        _check_csrf()
        if _is_rate_limited():
            logger.warning("Login rate limit exceeded for %s", request.remote_addr)
            return render_template("login.html", error="Too many failed attempts. Try again in a minute."), 429
        password = request.form.get("password", "")
        if _check_password(password, config.WEB_PASSWORD):
            session["authenticated"] = True
            return redirect(url_for("index"))
        _record_login_failure()
        return render_template("login.html", error="Incorrect password.")

    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    if not config.SETUP_COMPLETE:
        return redirect(url_for("setup"))
    session.clear()
    return redirect(url_for("login"))

def _save_app_config(form):
    host = form.get("imap_host", "").strip()
    port = form.get("imap_port", "993").strip()
    username = form.get("imap_username", "").strip()
    new_password = form.get("imap_password", "")
    folder = form.get("imap_folder", "").strip()
    trash_folder = form.get("imap_trash_folder", "").strip() or None
    spam_folder = form.get("imap_spam_folder", "").strip() or None
    tls_mode = form.get("tls_mode", "ssl").strip()
    if tls_mode not in _TLS_MODES:
        tls_mode = "ssl"

    if not new_password:
        try:
            saved = json.loads(get_config("imap_accounts", "[]"))
            new_password = (saved[0].get("password", "") if saved else "")
        except (json.JSONDecodeError, IndexError, KeyError):
            new_password = ""

    try:
        port_int = int(port)
    except ValueError:
        port_int = 993

    account = {
        "name": form.get("account_name", "Default").strip() or "Default",
        "host": host,
        "port": port_int,
        "username": username,
        "password": new_password,
        "folder": folder,
        "trash_folder": trash_folder,
        "spam_folder": spam_folder,
        "poll_interval": 60,
        "tls_mode": tls_mode,
    }

    log_level = form.get("log_level", "INFO").strip().upper()
    if log_level not in _LEVELS:
        log_level = "INFO"

    spam_action = form.get("spam_action", "spam").strip()
    if spam_action not in _SPAM_ACTIONS:
        spam_action = "spam"

    spam_learning = form.get("spam_learning", "both").strip()
    if spam_learning not in _SPAM_LEARNING_OPTIONS:
        spam_learning = "both"

    try:
        spam_threshold = float(form.get("spam_threshold", "6.0"))
    except ValueError:
        spam_threshold = 6.0

    try:
        ham_threshold = float(form.get("ham_threshold", "2.0"))
    except ValueError:
        ham_threshold = 2.0

    try:
        db_prune_days = int(form.get("db_prune_days", "0"))
        if db_prune_days < 0:
            db_prune_days = 0
    except ValueError:
        db_prune_days = 0

    dry_run = form.get("dry_run") == "true"

    disable_password = form.get("disable_password") == "1"
    new_web_password_raw = form.get("web_password", "")
    if disable_password:
        web_password_stored = ""
    elif new_web_password_raw:
        web_password_stored = _hash_password(new_web_password_raw)
    else:
        web_password_stored = config.WEB_PASSWORD

    bulk_set_config({
        "imap_accounts": json.dumps([account]),
        "log_level": log_level,
        "dry_run": "true" if dry_run else "false",
        "spam_threshold": str(spam_threshold),
        "spam_action": spam_action,
        "spam_learning": spam_learning,
        "ham_threshold": str(ham_threshold),
        "web_password": web_password_stored,
        "db_prune_days": str(db_prune_days),
    })

    return web_password_stored

@app.route("/setup", methods=["GET"])
def setup():
    if config.SETUP_COMPLETE:
        return redirect(url_for("index"))
    return render_template("setup.html", levels=_LEVELS, spam_actions=_SPAM_ACTIONS,
                           spam_learning_options=_SPAM_LEARNING_OPTIONS, tls_modes=_TLS_MODES,
                           show_logout=False, setup_mode=True)

@app.route("/setup", methods=["POST"])
@_require_csrf
def setup_post():
    if config.SETUP_COMPLETE:
        return redirect(url_for("index"))

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
                               spam_actions=_SPAM_ACTIONS,
                               spam_learning_options=_SPAM_LEARNING_OPTIONS,
                               tls_modes=_TLS_MODES,
                               show_logout=False, setup_mode=True)

    _save_app_config(request.form)
    set_config("setup_complete", "true")
    config.reload()

    logger.info("Setup completed. Restart the container to begin monitoring.")
    return render_template("setup.html", completed=True, levels=_LEVELS,
                           spam_actions=_SPAM_ACTIONS,
                           spam_learning_options=_SPAM_LEARNING_OPTIONS,
                           tls_modes=_TLS_MODES,
                           show_logout=False, setup_mode=True)

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
            detected_trash, detected_spam = imap.detect_special_folders(client)
            client.logout()
            return {
                "success": True,
                "folders": folders,
                "trash_folder": detected_trash or "",
                "spam_folder": detected_spam or "",
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

@app.route("/config", methods=["GET"])
@_require_auth
def config_page():
    account = config.IMAP_ACCOUNTS[0] if config.IMAP_ACCOUNTS else {}
    return render_template(
        "config.html",
        account=account,
        levels=_LEVELS,
        spam_actions=_SPAM_ACTIONS,
        spam_learning_options=_SPAM_LEARNING_OPTIONS,
        tls_modes=_TLS_MODES,
        log_level=config.LOG_LEVEL,
        dry_run=config.DRYRUN,
        spam_threshold=config.SPAM_THRESHOLD,
        spam_action=config.SPAM_ACTION,
        spam_learning=config.SPAM_LEARNING,
        ham_threshold=config.HAM_THRESHOLD,
        db_prune_days=config.DB_PRUNE_DAYS,
        has_password=bool(config.WEB_PASSWORD),
        tls_mode=config.IMAP_TLS_MODE,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/config", methods=["POST"])
@_require_auth
@_require_csrf
def config_save():
    old_password_hash = config.WEB_PASSWORD
    new_password_hash = _save_app_config(request.form)
    config.reload()
    logger.info("Configuration updated")
    if config.SETUP_COMPLETE:
        imap.request_reconnect()
    if new_password_hash != old_password_hash:
        session.clear()
        return redirect(url_for("login"))
    return redirect(url_for("config_page"))

@app.route("/")
@_require_auth
def index():
    stats = _get_stats()
    return render_template(
        "stats.html",
        stats=stats,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/emails")
@_require_auth
def emails():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    offset = (page - 1) * _EMAILS_PAGE_SIZE
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        rows = conn.execute(
            """SELECT id, sender, subject, date_received, spam_score,
                      processed_notes, processed, rule_matched, user_action, actions
               FROM emails
               ORDER BY date_received DESC
               LIMIT ? OFFSET ?""",
            (_EMAILS_PAGE_SIZE, offset)
        ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query emails (page=%s): %s", page, e)
        raise
    finally:
        conn.close()

    total_pages = max(1, (total + _EMAILS_PAGE_SIZE - 1) // _EMAILS_PAGE_SIZE)

    email_list = []
    for row in rows:
        rule_name = None
        if row["rule_matched"]:
            try:
                rule_name = json.loads(row["rule_matched"])["name"]
            except (json.JSONDecodeError, KeyError):
                pass
        try:
            actions = json.loads(row["actions"] or "[]")
        except json.JSONDecodeError:
            actions = []
        spammed = (
            any(a.get("type") == "spam" for a in actions)
            and row["user_action"] != "ham"
        )
        email_list.append({
            "id": row["id"],
            "sender": row["sender"],
            "subject": row["subject"],
            "date_received": row["date_received"],
            "spam_score": row["spam_score"],
            "score_class": _score_class(row["spam_score"]),
            "processed_notes": row["processed_notes"],
            "processed": row["processed"],
            "rule_name": rule_name,
            "user_action": row["user_action"],
            "spammed": spammed,
        })

    return render_template(
        "emails.html",
        emails=email_list,
        page=page,
        total_pages=total_pages,
        total=total,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/logs")
@_require_auth
def system_logs():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    level = request.args.get("level", config.LOG_LEVEL).upper()
    if level not in _LEVELS:
        level = config.LOG_LEVEL if config.LOG_LEVEL in _LEVELS else "INFO"

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    level_index = _LEVELS.index(level)
    visible_levels = _LEVELS[level_index:]
    placeholders = ",".join("?" * len(visible_levels))

    where_clauses = ["level IN (%s)" % placeholders]
    params = list(visible_levels)

    if date_from:
        where_clauses.append("logged_at >= ?")
        params.append(_local_date_to_utc(date_from, "00:00:00"))
    if date_to:
        where_clauses.append("logged_at <= ?")
        params.append(_local_date_to_utc(date_to, "23:59:59"))

    where_sql = "WHERE " + " AND ".join(where_clauses)
    offset = (page - 1) * _LOGS_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM logs %s" % where_sql, params).fetchone()[0]
        rows = conn.execute(
            """SELECT id, level, logger_name, message, logged_at, email_id
               FROM logs %s
               ORDER BY logged_at DESC
               LIMIT ? OFFSET ?""" % where_sql,
            params + [_LOGS_PAGE_SIZE, offset]
        ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query system logs (page=%s): %s", page, e)
        raise
    finally:
        conn.close()

    total_pages = max(1, (total + _LOGS_PAGE_SIZE - 1) // _LOGS_PAGE_SIZE)

    return render_template(
        "logs.html",
        log_rows=[dict(r) for r in rows],
        page=page,
        total_pages=total_pages,
        total=total,
        levels=_LEVELS,
        selected_level=level,
        date_from=date_from,
        date_to=date_to,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/emails/<email_id>")
@_require_auth
def email_detail(email_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM emails WHERE id = ?", (email_id,)
        ).fetchone()

        if row is None:
            abort(404)

        log_rows = conn.execute(
            """SELECT level, logger_name, message, logged_at
               FROM logs
               WHERE email_id = ?
               ORDER BY logged_at ASC""",
            (email_id,)
        ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query email detail for %s: %s", email_id, e)
        raise
    finally:
        conn.close()

    actions = json.loads(row["actions"] or "[]")
    attachments = json.loads(row["attachments"] or "[]")
    history = json.loads(row["history"] or "[]")
    rule = None
    if row["rule_matched"]:
        try:
            rule = json.loads(row["rule_matched"])
        except json.JSONDecodeError:
            pass

    spammed = (
        any(a.get("type") == "spam" for a in actions)
        and row["user_action"] != "ham"
    )

    email = {
        "id": row["id"],
        "uid": row["uid"],
        "folder": row["folder"],
        "sender": row["sender"],
        "recipients": row["recipients"],
        "subject": row["subject"],
        "date_received": row["date_received"],
        "message_size": row["message_size"],
        "spam_score": row["spam_score"],
        "score_class": _score_class(row["spam_score"]),
        "rule": rule,
        "actions": actions,
        "history": history,
        "spammed": spammed,
        "attachments": attachments,
        "raw_headers": row["raw_headers"],
        "processed": row["processed"],
        "processed_at": row["processed_at"],
        "processed_notes": row["processed_notes"],
        "user_action": row["user_action"],
    }

    logs = [dict(r) for r in log_rows]

    return render_template(
        "emails_view.html",
        email=email,
        logs=logs,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/emails/<email_id>/not-spam", methods=["POST"])
@_require_auth
@_require_csrf
def not_spam(email_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT id, raw_headers FROM emails WHERE id = ?", (email_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to query email %s for not-spam action: %s", email_id, e)
        raise
    finally:
        conn.close()

    if row is None:
        abort(404)

    set_user_action(email_id, "ham")
    logger.info("User reported email %s as ham", email_id)
    _learn_from_imap(email_id, row["raw_headers"], "ham",
                     [config.IMAP_SPAM_FOLDER, config.IMAP_TRASH_FOLDER, config.IMAP_FOLDER])
    return redirect(url_for("email_detail", email_id=email_id))

@app.route("/emails/<email_id>/is-spam", methods=["POST"])
@_require_auth
@_require_csrf
def is_spam(email_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT id, raw_headers FROM emails WHERE id = ?", (email_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to query email %s for is-spam action: %s", email_id, e)
        raise
    finally:
        conn.close()

    if row is None:
        abort(404)

    set_user_action(email_id, "spam")
    logger.info("User reported email %s as spam", email_id)
    _learn_from_imap(email_id, row["raw_headers"], "spam",
                     [config.IMAP_FOLDER, config.IMAP_SPAM_FOLDER, config.IMAP_TRASH_FOLDER])
    return redirect(url_for("email_detail", email_id=email_id))

_FIELD_LABELS = {
    "sender": "Full address",
    "sender_local": "Local part (before @)",
    "sender_domain": "Full domain",
    "sender_domain_name": "Domain name",
    "sender_domain_root": "Domain root",
    "sender_domain_tld": "TLD",
    "recipient": "Full address",
    "recipient_local": "Local part (before @)",
    "recipient_domain": "Full domain",
    "recipient_domain_name": "Domain name",
    "recipient_domain_root": "Domain root",
    "recipient_domain_tld": "TLD",
    "subject": "Subject",
    "raw_headers": "Raw headers",
    "attachment_name": "File name",
    "attachment_extension": "Extension",
    "attachment_content_type": "Content type",
}

_ACTION_LABELS = {
    "move": "Move to folder",
    "delete": "Delete (move to trash)",
    "spam": "Mark as spam",
    "mark_read": "Mark as read",
    "mark_unread": "Mark as unread",
    "flag": "Flag message",
    "unflag": "Remove flag",
}

def _get_imap_folders():
    with _folder_cache_lock:
        now = time.monotonic()
        if _folder_cache["expires"] > now:
            return _folder_cache["folders"]
        if _folder_cache["fetching"]:
            return _folder_cache["folders"]
        _folder_cache["fetching"] = True

    try:
        client = imap.connect()
        try:
            folders = sorted(name for flags, delimiter, name in client.list_folders())
        finally:
            client.logout()
    except Exception as e:
        logger.warning("Could not fetch IMAP folders for rule editor: %s", e)
        folders = []

    with _folder_cache_lock:
        _folder_cache["folders"] = folders
        _folder_cache["expires"] = time.monotonic() + 10.0
        _folder_cache["fetching"] = False

    return folders

def _read_rules_raw():
    if not os.path.exists(config.RULES_PATH):
        return []
    try:
        with open(config.RULES_PATH, "r") as f:
            data = yaml.safe_load(f)
        if not data or "rules" not in data:
            return []
        return data["rules"] or []
    except (OSError, yaml.YAMLError) as e:
        logger.error("Failed to read rules file: %s", e)
        return []

class _IndentedDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)

def _write_rules_raw(raw_rules):
    os.makedirs(os.path.dirname(config.RULES_PATH), exist_ok=True)
    with open(config.RULES_PATH, "w") as f:
        yaml.dump({"rules": raw_rules}, f, Dumper=_IndentedDumper, default_flow_style=False, allow_unicode=True, sort_keys=False)

def _parse_rule_form(form):
    condition_fields = form.getlist("condition_field")
    condition_operators = form.getlist("condition_operator")
    condition_values = form.getlist("condition_value")
    action_types = form.getlist("action_type")
    action_destinations = form.getlist("action_destination")

    conditions = []
    for field, operator, value in zip(condition_fields, condition_operators, condition_values):
        if field and operator:
            conditions.append({"field": field, "operator": operator, "value": value})

    actions = []
    for action_type, destination in zip(action_types, action_destinations):
        if not action_type:
            continue
        action = {"type": action_type}
        if action_type == "move":
            action["destination"] = destination
        actions.append(action)

    return {
        "name": form.get("name", "").strip(),
        "match": form.get("match", "all"),
        "learn": form.get("learn", "spam"),
        "conditions": conditions,
        "actions": actions,
    }

@app.route("/rules")
@_require_auth
def rules_list():
    raw_rules = _read_rules_raw()
    return render_template(
        "rules.html",
        rules=raw_rules,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
        run_result=request.args.get("run_result"),
    )

@app.route("/rules/new", methods=["GET", "POST"])
@_require_auth
def rule_new():
    error = None
    rule = {"name": "", "match": "all", "learn": "spam", "conditions": [], "actions": []}
    folders = _get_imap_folders()

    if request.method == "POST":
        _check_csrf()
        rule = _parse_rule_form(request.form)
        validated = validate_rule(rule)
        if validated is None:
            error = "Rule is invalid. Check that all fields are filled in correctly and try again."
        else:
            raw_rules = _read_rules_raw()
            raw_rules.append(validated)
            try:
                _write_rules_raw(raw_rules)
                logger.info("User created rule '%s'", validated["name"])
                return redirect(url_for("rules_list"))
            except OSError as e:
                error = "Failed to save rules file: %s" % e

    return render_template(
        "rules_edit.html",
        rule=rule,
        form_action=url_for("rule_new"),
        page_title="New Rule",
        error=error,
        folders=folders,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/rules/<int:index>/edit", methods=["GET", "POST"])
@_require_auth
def rule_edit(index):
    raw_rules = _read_rules_raw()
    if index < 0 or index >= len(raw_rules):
        abort(404)

    error = None
    rule = raw_rules[index]
    folders = _get_imap_folders()

    if request.method == "POST":
        _check_csrf()
        rule = _parse_rule_form(request.form)
        validated = validate_rule(rule)
        if validated is None:
            error = "Rule is invalid. Check that all fields are filled in correctly and try again."
        else:
            raw_rules[index] = validated
            try:
                _write_rules_raw(raw_rules)
                logger.info("User updated rule '%s'", validated["name"])
                return redirect(url_for("rules_list"))
            except OSError as e:
                error = "Failed to save rules file: %s" % e

    return render_template(
        "rules_edit.html",
        rule=rule,
        form_action=url_for("rule_edit", index=index),
        page_title="Edit Rule",
        error=error,
        folders=folders,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/rules/<int:index>/delete", methods=["POST"])
@_require_auth
@_require_csrf
def rule_delete(index):
    raw_rules = _read_rules_raw()
    if index < 0 or index >= len(raw_rules):
        abort(404)
    deleted_name = raw_rules[index].get("name", "unknown")
    raw_rules.pop(index)
    try:
        _write_rules_raw(raw_rules)
        logger.info("User deleted rule '%s'", deleted_name)
    except OSError as e:
        logger.error("Failed to write rules file after delete: %s", e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<int:index>/run", methods=["POST"])
@_require_auth
@_require_csrf
def rule_run(index):
    raw_rules = _read_rules_raw()
    if index < 0 or index >= len(raw_rules):
        abort(404)

    rule = validate_rule(raw_rules[index])
    if rule is None:
        return redirect(url_for("rules_list"))

    logger.info(
        "Rule '%s' run manually (learn=%s, DRYRUN=%s)",
        rule["name"], rule["learn"], config.DRYRUN
    )

    matched = 0
    actioned = 0

    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM emails WHERE folder = ?", (config.IMAP_FOLDER,)
            ).fetchall()
        except sqlite3.Error as e:
            logger.error("Rule run: failed to query emails: %s", e)
            raise
        finally:
            conn.close()
    except Exception as e:
        logger.error("Rule run: database error for rule '%s': %s", rule["name"], e)
        return redirect(url_for("rules_list"))

    if not rows:
        logger.debug("Rule run: no emails in database for folder %s", config.IMAP_FOLDER)
        return redirect(url_for("rules_list", run_result="Rule '%s' ran: no emails in database." % rule["name"]))

    logger.debug("Rule run: evaluating %s email(s) against rule '%s'", len(rows), rule["name"])

    try:
        resolved_actions = _resolve_run_actions(rule["actions"])
    except RuntimeError as e:
        logger.error("Rule run: cannot execute rule '%s': %s", rule["name"], e)
        return redirect(url_for("rules_list", run_result="Rule '%s' failed: %s" % (rule["name"], e)))

    try:
        client = imap.connect()
        try:
            client.select_folder(config.IMAP_FOLDER)
            current_uids = set(client.search(["ALL"]))
            logger.debug("Rule run: %s UID(s) currently in %s", len(current_uids), config.IMAP_FOLDER)
            processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            for row in rows:
                uid = int(row["uid"])
                email_id = row["id"]

                if uid not in current_uids:
                    logger.debug("Rule run: UID %s not in current folder, skipping", uid)
                    continue

                email_data = {
                    "sender": row["sender"] or "",
                    "subject": row["subject"] or "",
                    "recipients": [r for r in (row["recipients"] or "").split(",") if r],
                    "raw_headers": row["raw_headers"] or "",
                    "attachments": json.loads(row["attachments"] or "[]"),
                }

                if not check_rule(rule, email_data):
                    logger.debug("Rule run: UID %s did not match rule '%s'", uid, rule["name"])
                    continue

                matched += 1
                logger.debug(
                    "Rule run: UID %s (email_id=%s) matched rule '%s'",
                    uid, email_id, rule["name"],
                    extra={"email_id": email_id}
                )

                will_learn = spam.should_learn(rule["learn"])
                raw_message = b""
                if will_learn and not config.DRYRUN:
                    logger.debug(
                        "Rule run: fetching RFC822 for UID %s to submit as %s",
                        uid, rule["learn"],
                        extra={"email_id": email_id}
                    )
                    try:
                        fetch_result = client.fetch([uid], ["RFC822"])
                        raw_message = fetch_result.get(uid, {}).get(b"RFC822", b"")
                        if not raw_message:
                            logger.warning(
                                "Rule run: RFC822 body empty for UID %s, skipping learning",
                                uid, extra={"email_id": email_id}
                            )
                            will_learn = False
                    except Exception as e:
                        logger.error(
                            "Rule run: failed to fetch RFC822 for UID %s: %s",
                            uid, e, extra={"email_id": email_id}
                        )
                        will_learn = False

                executed = []
                for action in resolved_actions:
                    action_type = action["type"]
                    logger.debug(
                        "Rule run: executing action %s for UID %s",
                        action_type, uid, extra={"email_id": email_id}
                    )
                    try:
                        imap.execute_action(client, action, uid, email_id=email_id)
                        executed.append(action)
                        actioned += 1
                    except Exception as e:
                        logger.error(
                            "Rule run: failed action %s on UID %s: %s",
                            action_type, uid, e, extra={"email_id": email_id}
                        )
                    if action_type in TERMINAL_ACTIONS:
                        break

                learned_ok = False
                if will_learn and raw_message and not config.DRYRUN:
                    try:
                        if rule["learn"] == "spam":
                            learned_ok = spam.learn_spam(raw_message, email_id=email_id)
                        elif rule["learn"] == "ham":
                            learned_ok = spam.learn_ham(raw_message, email_id=email_id)
                    except Exception as e:
                        logger.error(
                            "Rule run: rspamd learning failed for UID %s: %s",
                            uid, e, extra={"email_id": email_id}
                        )
                        will_learn = False

                prefix = "[DRY RUN] " if config.DRYRUN else ""
                notes_parts = ["%sRule '%s' applied manually." % (prefix, rule["name"])]
                for a in executed:
                    notes_parts.append(action_sentence(a, config.DRYRUN))
                if will_learn:
                    notes_parts.append(spam.learn_sentence(rule["learn"], config.DRYRUN))

                rspamd_learned = None
                if not config.DRYRUN and will_learn and learned_ok:
                    rspamd_learned = rule["learn"]
                elif config.DRYRUN and will_learn:
                    rspamd_learned = rule["learn"]

                history = json.loads(row["history"] or "[]")
                new_entries = []
                if not config.DRYRUN:
                    new_entries = [
                        dict({"at": processed_at, "by": "boxwatchr", "action": a["type"]},
                             **{"destination": a["destination"]} if "destination" in a else {})
                        for a in executed
                    ]
                enqueue_email_update(
                    email_id,
                    json.dumps(rule),
                    executed,
                    processed=0 if config.DRYRUN else 1,
                    processed_at=processed_at,
                    processed_notes=" ".join(notes_parts),
                    history=history + new_entries,
                    rspamd_learned=rspamd_learned,
                )
                logger.debug(
                    "Rule run: enqueued update for email_id=%s (actions=%s)",
                    email_id, [a["type"] for a in executed],
                    extra={"email_id": email_id}
                )

        finally:
            client.logout()
    except Exception as e:
        logger.error("Rule run: IMAP error for rule '%s': %s", rule["name"], e)

    logger.info("Rule '%s' run manually: %s matched, %s action(s) taken", rule["name"], matched, actioned)
    return redirect(url_for(
        "rules_list",
        run_result="Rule '%s' ran: %s email(s) matched, %s action(s) taken." % (rule["name"], matched, actioned)
    ))

def _run_server():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("werkzeug").propagate = False
    logging.getLogger("flask").setLevel(logging.ERROR)
    logging.getLogger("flask").propagate = False
    from werkzeug.serving import make_server
    server = make_server("0.0.0.0", 80, app, threaded=True)
    server.serve_forever()

def start_dashboard():
    app.secret_key = _get_or_create_session_secret()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    t = threading.Thread(target=_run_server, daemon=True, name="web-dashboard")
    t.start()
    logger.debug("Web dashboard started on port 80")

import os
import json
import hmac
import logging
import threading
import collections
from email import message_from_string
from flask import Flask, render_template, request, redirect, url_for, session, abort
from boxwatchr import config, imap, spam
from boxwatchr.database import get_connection, set_user_action
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.web")

app = Flask(__name__, template_folder="templates")
app.secret_key = os.urandom(24)

_PAGE_SIZE = 50


def _require_auth(f):
    import functools
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not config.WEB_PASSWORD:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _score_class(score):
    if score is None:
        return ""
    if score < 0:
        return "score-neg"
    if score < 2:
        return "score-low"
    if score < 5:
        return "score-med"
    return "score-high"


def _imap_find_by_message_id(client, message_id, folders):
    for folder in folders:
        if not folder:
            continue
        try:
            client.select_folder(folder)
            uids = client.search(["HEADER", "Message-ID", message_id])
            if uids:
                return uids[0], folder
        except Exception:
            pass
    return None, None


def _get_stats():
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM emails WHERE processed = 0").fetchone()[0]

        spam_caught = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE processed_notes LIKE '%rspamd as spam%'"
        ).fetchone()[0]

        ham_learned = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE processed_notes LIKE '%rspamd as ham%'"
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
    finally:
        conn.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.WEB_PASSWORD:
        return redirect(url_for("index"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, config.WEB_PASSWORD):
            session["authenticated"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Incorrect password.")

    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@_require_auth
def index():
    stats = _get_stats()
    return render_template(
        "stats.html",
        stats=stats,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/logs")
@_require_auth
def logs():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    offset = (page - 1) * _PAGE_SIZE
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        rows = conn.execute(
            """SELECT id, sender, subject, date_received, spam_score,
                      processed_notes, processed, rule_matched
               FROM emails
               ORDER BY date_received DESC
               LIMIT ? OFFSET ?""",
            (_PAGE_SIZE, offset)
        ).fetchall()
    finally:
        conn.close()

    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    emails = []
    for row in rows:
        rule_name = None
        if row["rule_matched"]:
            try:
                rule_name = json.loads(row["rule_matched"])["name"]
            except (json.JSONDecodeError, KeyError):
                pass
        emails.append({
            "id": row["id"],
            "sender": row["sender"],
            "subject": row["subject"],
            "date_received": row["date_received"],
            "spam_score": row["spam_score"],
            "score_class": _score_class(row["spam_score"]),
            "processed_notes": row["processed_notes"],
            "processed": row["processed"],
            "rule_name": rule_name,
        })

    return render_template(
        "logs.html",
        emails=emails,
        page=page,
        total_pages=total_pages,
        total=total,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/email/<email_id>")
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
    finally:
        conn.close()

    actions = json.loads(row["actions"] or "[]")
    attachments = json.loads(row["attachments"] or "[]")
    rule = None
    if row["rule_matched"]:
        try:
            rule = json.loads(row["rule_matched"])
        except json.JSONDecodeError:
            pass

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
        "attachments": attachments,
        "raw_headers": row["raw_headers"],
        "processed": row["processed"],
        "processed_at": row["processed_at"],
        "processed_notes": row["processed_notes"],
        "message_id": row["message_id"] or "",
        "user_action": row["user_action"],
    }

    logs = [dict(r) for r in log_rows]

    return render_template(
        "email.html",
        email=email,
        logs=logs,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/email/<email_id>/not-spam", methods=["POST"])
@_require_auth
def not_spam(email_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        abort(404)

    message_id = row["message_id"] or ""
    if not message_id and row["raw_headers"]:
        try:
            msg_obj = message_from_string(row["raw_headers"])
            message_id = (msg_obj.get("Message-ID") or "").strip()
        except Exception:
            pass

    set_user_action(email_id, "ham", message_id=message_id or None)
    logger.info("User marked email %s as not spam", email_id)

    if message_id:
        try:
            client = imap.connect()
            try:
                search_folders = [config.IMAP_SPAM_FOLDER, config.IMAP_TRASH_FOLDER, config.IMAP_FOLDER]
                uid, found_folder = _imap_find_by_message_id(client, message_id, search_folders)
                if uid is not None:
                    result = client.fetch([uid], ["RFC822"])
                    raw_message = result.get(uid, {}).get(b"RFC822", b"")
                    if raw_message and config.SPAM_LEARNING in ("ham", "both"):
                        spam.learn_ham(raw_message, email_id=email_id)
                    if found_folder != config.IMAP_FOLDER:
                        client.select_folder(found_folder)
                        client.move([uid], config.IMAP_FOLDER)
                        logger.info("Moved email %s back to %s", email_id, config.IMAP_FOLDER)
                else:
                    logger.warning("Email %s not found in IMAP for not-spam action", email_id)
            finally:
                client.logout()
        except Exception as e:
            logger.error("IMAP error during not-spam action for email %s: %s", email_id, e)

    return redirect(url_for("email_detail", email_id=email_id))


@app.route("/email/<email_id>/is-spam", methods=["POST"])
@_require_auth
def is_spam(email_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        abort(404)

    message_id = row["message_id"] or ""
    if not message_id and row["raw_headers"]:
        try:
            msg_obj = message_from_string(row["raw_headers"])
            message_id = (msg_obj.get("Message-ID") or "").strip()
        except Exception:
            pass

    set_user_action(email_id, "spam", message_id=message_id or None)
    logger.info("User marked email %s as spam", email_id)

    if message_id and config.IMAP_SPAM_FOLDER:
        try:
            client = imap.connect()
            try:
                search_folders = [config.IMAP_FOLDER, config.IMAP_SPAM_FOLDER, config.IMAP_TRASH_FOLDER]
                uid, found_folder = _imap_find_by_message_id(client, message_id, search_folders)
                if uid is not None:
                    result = client.fetch([uid], ["RFC822"])
                    raw_message = result.get(uid, {}).get(b"RFC822", b"")
                    if raw_message and config.SPAM_LEARNING in ("spam", "both"):
                        spam.learn_spam(raw_message, email_id=email_id)
                    if found_folder != config.IMAP_SPAM_FOLDER:
                        client.select_folder(found_folder)
                        client.move([uid], config.IMAP_SPAM_FOLDER)
                        logger.info("Moved email %s to %s", email_id, config.IMAP_SPAM_FOLDER)
                else:
                    logger.warning("Email %s not found in IMAP for is-spam action", email_id)
            finally:
                client.logout()
        except Exception as e:
            logger.error("IMAP error during is-spam action for email %s: %s", email_id, e)

    return redirect(url_for("email_detail", email_id=email_id))


def _run_server():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=80, use_reloader=False, threaded=True)


def start_dashboard():
    t = threading.Thread(target=_run_server, daemon=True, name="web-dashboard")
    t.start()
    logger.debug("Web dashboard started on port 80")

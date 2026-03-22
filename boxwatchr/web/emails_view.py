import json
import sqlite3
from email import message_from_string
from flask import render_template, redirect, url_for, abort
from boxwatchr import config, imap, spam
from boxwatchr.database import get_connection, set_user_action
from boxwatchr.web.app import app, _require_auth, _require_csrf, _score_class, logger

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
    if not message_id:
        logger.debug("Skipping %s learning for email %s: no message_id", learn_type, email_id)
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

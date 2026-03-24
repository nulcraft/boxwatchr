import json
import sqlite3
from flask import render_template, abort
from boxwatchr import config
from boxwatchr.database import db_connection
from boxwatchr.web.app import app, _require_auth, _score_class, logger


@app.route("/emails/<email_id>")
@_require_auth
def email_detail(email_id):
    try:
        with db_connection() as conn:
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

    actions = json.loads(row["actions"] or "[]")
    attachments = json.loads(row["attachments"] or "[]")
    history = json.loads(row["history"] or "[]")
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
        "history": history,
        "attachments": attachments,
        "raw_headers": row["raw_headers"],
        "processed": row["processed"],
        "processed_at": row["processed_at"],
        "processed_notes": row["processed_notes"],
    }

    logs = [dict(r) for r in log_rows]

    return render_template(
        "email_detail.html",
        email=email,
        logs=logs,
        show_logout=bool(config.WEB_PASSWORD),
    )

import json
import sqlite3
from flask import render_template, request
from boxwatchr import config
from boxwatchr.database import get_connection
from boxwatchr.web.app import app, _require_auth, _score_class, _EMAILS_PAGE_SIZE, logger

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

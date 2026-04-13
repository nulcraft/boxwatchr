import json
import sqlite3
from flask import render_template, request, session
from boxwatchr import config
from boxwatchr.database import db_connection
from boxwatchr.web.app import app, _require_auth, _score_class, _EMAILS_PAGE_SIZE, get_selected_account_id, logger

@app.route("/emails")
@_require_auth
def emails():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    account_id = get_selected_account_id()
    offset = (page - 1) * _EMAILS_PAGE_SIZE

    try:
        with db_connection() as conn:
            if account_id:
                total = conn.execute("SELECT COUNT(*) FROM emails WHERE account_id = ?", (account_id,)).fetchone()[0]
                rows = conn.execute(
                    """SELECT e.id, e.sender, e.subject, e.date_received, e.spam_score,
                              e.processed_notes, e.processed, e.rule_matched, a.name AS account_name
                       FROM emails e
                       LEFT JOIN accounts a ON a.id = e.account_id
                       WHERE e.account_id = ?
                       ORDER BY e.date_received DESC
                       LIMIT ? OFFSET ?""",
                    (account_id, _EMAILS_PAGE_SIZE, offset)
                ).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
                rows = conn.execute(
                    """SELECT e.id, e.sender, e.subject, e.date_received, e.spam_score,
                              e.processed_notes, e.processed, e.rule_matched, a.name AS account_name
                       FROM emails e
                       LEFT JOIN accounts a ON a.id = e.account_id
                       ORDER BY e.date_received DESC
                       LIMIT ? OFFSET ?""",
                    (_EMAILS_PAGE_SIZE, offset)
                ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query emails (page=%s): %s", page, e)
        raise

    total_pages = max(1, (total + _EMAILS_PAGE_SIZE - 1) // _EMAILS_PAGE_SIZE)

    email_list = []
    for row in rows:
        rule_name = None
        if row["rule_matched"]:
            try:
                rule_name = json.loads(row["rule_matched"])["name"]
            except (json.JSONDecodeError, KeyError):
                pass
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
            "account_name": row["account_name"],
        })

    return render_template(
        "emails.html",
        emails=email_list,
        page=page,
        total_pages=total_pages,
        total=total,
        show_logout=bool(config.WEB_PASSWORD),
        show_account_column=not account_id,
    )

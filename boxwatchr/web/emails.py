import json
import sqlite3
from flask import render_template, request
from boxwatchr import config
from boxwatchr.database import db_connection, get_rule
from boxwatchr.web.app import app, _require_auth, _score_class, _EMAILS_PAGE_SIZE, logger


def _get_distinct_folders():
    try:
        with db_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT folder FROM emails"
                " WHERE folder IS NOT NULL AND folder != '' ORDER BY folder"
            ).fetchall()
        return [row["folder"] for row in rows]
    except Exception:
        return []


def _resolve_rule_name(rule_matched_json):
    if not rule_matched_json:
        return None, None
    try:
        data = json.loads(rule_matched_json)
    except (json.JSONDecodeError, TypeError):
        return None, None
    rule_id = data.get("id")
    stored_name = data.get("name")
    if rule_id:
        rule_row = get_rule(rule_id)
        if rule_row:
            return rule_row["name"], rule_id
    return stored_name, rule_id


@app.route("/emails")
@_require_auth
def emails():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    folder = request.args.get("folder", "").strip()
    q = request.args.get("q", "").strip()
    rule_filter = request.args.get("rule_filter", "").strip()

    conditions = []
    params = []

    if folder:
        conditions.append("folder = ?")
        params.append(folder)

    if q:
        escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("(sender LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\')")
        like = "%" + escaped_q + "%"
        params.extend([like, like])

    if rule_filter == "matched":
        conditions.append("rule_matched IS NOT NULL")
    elif rule_filter == "unmatched":
        conditions.append("rule_matched IS NULL")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    offset = (page - 1) * _EMAILS_PAGE_SIZE
    try:
        with db_connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM emails" + where, params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, sender, subject, date_received, spam_score,"
                " processed_notes, processed, rule_matched, folder"
                " FROM emails" + where +
                " ORDER BY date_received DESC LIMIT ? OFFSET ?",
                params + [_EMAILS_PAGE_SIZE, offset],
            ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query emails (page=%s, folder=%s, q=%s, rule_filter=%s): %s",
                     page, folder, q, rule_filter, e)
        raise

    total_pages = max(1, (total + _EMAILS_PAGE_SIZE - 1) // _EMAILS_PAGE_SIZE)

    email_list = []
    for row in rows:
        rule_name, rule_id = _resolve_rule_name(row["rule_matched"])
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
            "rule_id": rule_id,
            "folder": row["folder"],
        })

    return render_template(
        "emails.html",
        emails=email_list,
        page=page,
        total_pages=total_pages,
        total=total,
        folder=folder,
        q=q,
        rule_filter=rule_filter,
        folders=_get_distinct_folders(),
        show_logout=bool(config.WEB_PASSWORD),
    )

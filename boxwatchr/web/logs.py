import sqlite3
from flask import render_template, request
from boxwatchr import config
from boxwatchr.database import db_connection
from boxwatchr.web.app import app, _require_auth, _LEVELS, _LOGS_PAGE_SIZE, _local_date_to_utc, logger

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

    try:
        with db_connection() as conn:
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

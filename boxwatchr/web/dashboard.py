import sqlite3
import requests
from flask import render_template
from boxwatchr import config
from boxwatchr.database import db_connection
from boxwatchr.web.app import app, _require_auth, get_selected_account_id, logger

def _get_rspamd_training_counts():
    try:
        url = "http://%s:%s/stat" % (config.RSPAMD_HOST, config.RSPAMD_CONTROLLER_PORT)
        response = requests.get(url, timeout=3)
        if response.status_code != 200:
            return None, None
        data = response.json()
        spam_rev = None
        ham_rev = None
        for entry in data.get("statfiles", []):
            if entry.get("class") == "spam":
                spam_rev = entry.get("revision", 0)
            elif entry.get("class") == "ham":
                ham_rev = entry.get("revision", 0)
        return spam_rev, ham_rev
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch rspamd stat: %s", e)
        return None, None

def _get_stats(account_id=None):
    try:
        with db_connection() as conn:
            if account_id:
                where = " WHERE account_id = ?"
                params = (account_id,)
            else:
                where = ""
                params = ()

            total = conn.execute("SELECT COUNT(*) FROM emails" + where, params).fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM emails WHERE processed = 0" + (" AND account_id = ?" if account_id else ""), params).fetchone()[0]

            rule_query = (
                "SELECT JSON_EXTRACT(rule_matched, '$.name') AS rule_name, COUNT(*) AS cnt"
                " FROM emails WHERE rule_matched IS NOT NULL"
            )
            if account_id:
                rule_query += " AND account_id = ?"
            rule_query += " GROUP BY rule_name ORDER BY cnt DESC"
            rule_rows = conn.execute(rule_query, params).fetchall()
            rule_counts = [(row["rule_name"], row["cnt"]) for row in rule_rows if row["rule_name"]]

            score_query = "SELECT spam_score FROM emails WHERE spam_score IS NOT NULL"
            if account_id:
                score_query += " AND account_id = ?"
            score_rows = conn.execute(score_query, params).fetchall()

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

            spam_trained, ham_trained = _get_rspamd_training_counts()

            return {
                "total": total,
                "pending": pending,
                "spam_trained": spam_trained,
                "ham_trained": ham_trained,
                "rule_counts": rule_counts,
                "score_buckets": buckets,
            }
    except sqlite3.Error as e:
        logger.error("Failed to query stats: %s", e)
        raise

@app.route("/dashboard")
@_require_auth
def dashboard():
    account_id = get_selected_account_id()
    stats = _get_stats(account_id or None)
    return render_template(
        "dashboard.html",
        stats=stats,
        show_logout=bool(config.WEB_PASSWORD),
    )

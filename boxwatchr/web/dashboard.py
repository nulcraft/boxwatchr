import collections
import json
import sqlite3
from flask import render_template
from boxwatchr import config
from boxwatchr.database import db_connection
from boxwatchr.web.app import app, _require_auth, logger

def _get_stats():
    try:
        with db_connection() as conn:
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

@app.route("/dashboard")
@_require_auth
def dashboard():
    stats = _get_stats()
    return render_template(
        "dashboard.html",
        stats=stats,
        show_logout=bool(config.WEB_PASSWORD),
    )

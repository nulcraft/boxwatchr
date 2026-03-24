import sqlite3
import os
import signal
import sys
import uuid
import time
import json
import threading
import collections
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "boxwatchr.db")

CURRENT_VERSION = 1

_log_queue = collections.deque()
_email_queue = collections.deque()
_email_update_queue = collections.deque()
_queue_lock = threading.Lock()
_flusher_lock = threading.Lock()
_flusher_started = False
_last_prune_time = 0.0
_PRUNE_INTERVAL = 3600.0
_processing_active = False
_processing_lock = threading.Lock()
_flush_failures = 0
_MAX_FLUSH_FAILURES = 20

def set_processing(active):
    global _processing_active
    with _processing_lock:
        _processing_active = active

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

@contextmanager
def _db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

db_connection = _db

def _get_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]

def _set_version(conn, version):
    conn.execute(f"PRAGMA user_version = {version}")

def _create_schema(conn):
    logger.info("Creating database schema (v1)")

    conn.execute("""
        CREATE TABLE accounts (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            host         TEXT NOT NULL,
            port         INTEGER NOT NULL DEFAULT 993,
            username     TEXT NOT NULL,
            password     TEXT NOT NULL DEFAULT '',
            folder       TEXT NOT NULL DEFAULT 'INBOX',
            poll_interval INTEGER NOT NULL DEFAULT 60,
            tls_mode     TEXT NOT NULL DEFAULT 'ssl',
            created_at   TEXT NOT NULL
        )
    """)
    logger.debug("Accounts table created")

    conn.execute("""
        CREATE TABLE rules (
            id                  TEXT PRIMARY KEY,
            account_id          TEXT NOT NULL REFERENCES accounts(id),
            position            INTEGER NOT NULL,
            name                TEXT NOT NULL,
            match               TEXT NOT NULL DEFAULT 'all',
            conditions          TEXT NOT NULL DEFAULT '[]',
            actions             TEXT NOT NULL DEFAULT '[]',
            continue_processing INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX idx_rules_account_position ON rules (account_id, position)")
    logger.debug("Rules table created")

    conn.execute("""
        CREATE TABLE emails (
            id TEXT PRIMARY KEY,
            account_id TEXT REFERENCES accounts(id),
            uid TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT '',
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            date_received TEXT,
            message_size INTEGER,
            spam_score REAL,
            rule_matched TEXT,
            actions TEXT NOT NULL DEFAULT '[]',
            history TEXT NOT NULL DEFAULT '[]',
            raw_headers TEXT,
            attachments TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL,
            processed_notes TEXT,
            message_id TEXT,
            rspamd_learned TEXT,
            UNIQUE(uid, folder, account_id)
        )
    """)
    conn.execute("CREATE INDEX idx_emails_message_id ON emails (message_id)")
    logger.debug("Emails table created")

    conn.execute("""
        CREATE TABLE logs (
            id TEXT PRIMARY KEY,
            level TEXT NOT NULL,
            logger_name TEXT NOT NULL,
            message TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            email_id TEXT DEFAULT NULL,
            FOREIGN KEY (email_id) REFERENCES emails (id)
        )
    """)
    logger.debug("Logs table created")

    conn.execute("""
        CREATE TABLE config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    logger.debug("Config table created")

    logger.info("Database schema v1 created")

def initialize():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    try:
        with _db() as conn:
            current_version = _get_version(conn)

            if current_version == CURRENT_VERSION:
                return

            if current_version > CURRENT_VERSION:
                logger.error(
                    "Database version %s is newer than the application expects (%s). "
                    "Please update boxwatchr.",
                    current_version, CURRENT_VERSION
                )
                raise RuntimeError("Database version is newer than the application supports")

            _create_schema(conn)
            _set_version(conn, CURRENT_VERSION)
            conn.commit()
            logger.info("Database initialized at version %s", CURRENT_VERSION)

    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise

def get_config(key, default=None):
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
    except sqlite3.Error as e:
        logger.error("Failed to read config key %r: %s", key, e)
        return default

def set_config(key, value):
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to write config key %r: %s", key, e)
        raise

def bulk_set_config(items_dict):
    try:
        with _db() as conn:
            for key, value in items_dict.items():
                conn.execute(
                    "INSERT INTO config (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value))
                )
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to bulk-write config: %s", e)
        raise

def get_first_account():
    try:
        with _db() as conn:
            return conn.execute("SELECT * FROM accounts LIMIT 1").fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to fetch account: %s", e)
        return None

def upsert_account(account_id, name, host, port, username, password, folder, poll_interval, tls_mode):
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _db() as conn:
            conn.execute("""
                INSERT INTO accounts (id, name, host, port, username, password, folder, poll_interval, tls_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    host = excluded.host,
                    port = excluded.port,
                    username = excluded.username,
                    password = excluded.password,
                    folder = excluded.folder,
                    poll_interval = excluded.poll_interval,
                    tls_mode = excluded.tls_mode
            """, (account_id, name, host, port, username, password, folder, poll_interval, tls_mode, created_at))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to upsert account: %s", e)
        raise

def get_rules(account_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM rules WHERE account_id = ? ORDER BY position ASC",
                (account_id,)
            ).fetchall()
            return rows
    except sqlite3.Error as e:
        logger.error("Failed to fetch rules for account %s: %s", account_id, e)
        return []

def get_rule(rule_id):
    try:
        with _db() as conn:
            return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to fetch rule %s: %s", rule_id, e)
        return None

def insert_rule(account_id, name, match, conditions_json, actions_json, continue_processing=0):
    rule_id = str(uuid.uuid4())
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM rules WHERE account_id = ?",
                (account_id,)
            ).fetchone()
            position = row["next_pos"]
            conn.execute("""
                INSERT INTO rules (id, account_id, position, name, match, conditions, actions, continue_processing)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (rule_id, account_id, position, name, match, conditions_json, actions_json, int(continue_processing)))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to insert rule: %s", e)
        raise
    return rule_id

def update_rule(rule_id, name, match, conditions_json, actions_json, continue_processing=0):
    try:
        with _db() as conn:
            conn.execute("""
                UPDATE rules SET name = ?, match = ?, conditions = ?, actions = ?, continue_processing = ?
                WHERE id = ?
            """, (name, match, conditions_json, actions_json, int(continue_processing), rule_id))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to update rule %s: %s", rule_id, e)
        raise

def delete_rule(rule_id, account_id):
    try:
        with _db() as conn:
            conn.execute("DELETE FROM rules WHERE id = ? AND account_id = ?", (rule_id, account_id))
            _renumber_rule_positions(conn, account_id)
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to delete rule %s: %s", rule_id, e)
        raise

def move_rule_up(rule_id, account_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, position FROM rules WHERE account_id = ? ORDER BY position ASC",
                (account_id,)
            ).fetchall()
            ids = [r["id"] for r in rows]
            if rule_id not in ids:
                return
            idx = ids.index(rule_id)
            if idx == 0:
                return
            id_above = ids[idx - 1]
            pos_current = rows[idx]["position"]
            pos_above = rows[idx - 1]["position"]
            conn.execute("UPDATE rules SET position = ? WHERE id = ?", (pos_above, rule_id))
            conn.execute("UPDATE rules SET position = ? WHERE id = ?", (pos_current, id_above))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to move rule %s up: %s", rule_id, e)
        raise

def move_rule_down(rule_id, account_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, position FROM rules WHERE account_id = ? ORDER BY position ASC",
                (account_id,)
            ).fetchall()
            ids = [r["id"] for r in rows]
            if rule_id not in ids:
                return
            idx = ids.index(rule_id)
            if idx == len(ids) - 1:
                return
            id_below = ids[idx + 1]
            pos_current = rows[idx]["position"]
            pos_below = rows[idx + 1]["position"]
            conn.execute("UPDATE rules SET position = ? WHERE id = ?", (pos_below, rule_id))
            conn.execute("UPDATE rules SET position = ? WHERE id = ?", (pos_current, id_below))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to move rule %s down: %s", rule_id, e)
        raise

def _renumber_rule_positions(conn, account_id):
    rows = conn.execute(
        "SELECT id FROM rules WHERE account_id = ? ORDER BY position ASC",
        (account_id,)
    ).fetchall()
    for i, row in enumerate(rows, start=1):
        conn.execute("UPDATE rules SET position = ? WHERE id = ?", (i, row["id"]))

def start_flusher():
    global _flusher_started
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
    t = threading.Thread(target=_flush_loop, daemon=True, name="db-flusher")
    t.start()
    logger.debug("Database flusher thread started (interval=0.25s)")

def _flush_loop():
    global _flush_failures
    while True:
        time.sleep(0.25)
        _flush()
        if _flush_failures >= _MAX_FLUSH_FAILURES:
            logger.error(
                "Database flush has failed %s times in a row. Shutting down.",
                _flush_failures
            )
            os.kill(os.getpid(), signal.SIGTERM)
            sys.exit(2)

def _maybe_prune(conn):
    global _last_prune_time
    if config.DB_PRUNE_DAYS <= 0:
        return
    now = time.time()
    if now - _last_prune_time < _PRUNE_INTERVAL:
        return
    _last_prune_time = now
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.DB_PRUNE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    result = conn.execute("DELETE FROM logs WHERE logged_at < ?", (cutoff,))
    if result.rowcount > 0:
        logger.info("Pruned %s log entries older than %s days", result.rowcount, config.DB_PRUNE_DAYS)

def _flush():
    global _flush_failures
    with _processing_lock:
        if _processing_active:
            return

    with _queue_lock:
        if not _log_queue and not _email_queue and not _email_update_queue:
            return
        emails_batch = list(_email_queue)
        logs_batch = list(_log_queue)
        updates_batch = list(_email_update_queue)
        _email_queue.clear()
        _log_queue.clear()
        _email_update_queue.clear()

    if emails_batch or updates_batch:
        logger.debug(
            "Flushing DB queues: %s email(s), %s update(s), %s log(s)",
            len(emails_batch), len(updates_batch), len(logs_batch)
        )

    try:
        conn = get_connection()
    except sqlite3.Error as e:
        _flush_failures += 1
        logger.error(
            "Failed to open database connection for flush: %s (%s/%s consecutive failures)",
            e, _flush_failures, _MAX_FLUSH_FAILURES
        )
        with _queue_lock:
            for item in reversed(emails_batch):
                _email_queue.appendleft(item)
            for item in reversed(logs_batch):
                _log_queue.appendleft(item)
            for item in reversed(updates_batch):
                _email_update_queue.appendleft(item)
        return

    try:
        for item in emails_batch:
            conn.execute("""
                INSERT INTO emails (
                    id, account_id, uid, folder, sender, recipients, subject, date_received, message_size,
                    spam_score, rule_matched, actions, history, raw_headers, attachments,
                    processed, processed_at, processed_notes, message_id, rspamd_learned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid, folder, account_id) DO UPDATE SET
                    sender = excluded.sender,
                    recipients = excluded.recipients,
                    subject = excluded.subject,
                    date_received = excluded.date_received,
                    message_size = excluded.message_size,
                    raw_headers = excluded.raw_headers
            """, (
                item["id"], item["account_id"], item["uid"], item["folder"], item["sender"],
                item["recipients"], item["subject"], item["date_received"], item["message_size"],
                item["spam_score"], item["rule_matched"], item["actions"], item["history"],
                item["raw_headers"], item["attachments"],
                item["processed"], item["processed_at"], item["processed_notes"],
                item["message_id"], item["rspamd_learned"]
            ))

        for item in updates_batch:
            cols = [
                "rule_matched = ?",
                "actions = ?",
                "processed = ?",
                "processed_at = ?",
                "processed_notes = ?",
            ]
            vals = [
                item["rule_matched"],
                item["actions"],
                item["processed"],
                item["processed_at"],
                item["processed_notes"],
            ]
            if item["history"] is not None:
                cols.append("history = ?")
                vals.append(json.dumps(item["history"]))
            if item["rspamd_learned"] is not None:
                cols.append("rspamd_learned = ?")
                vals.append(item["rspamd_learned"])
            vals.append(item["id"])
            conn.execute(
                "UPDATE emails SET %s WHERE id = ?" % ", ".join(cols),
                vals
            )

        for item in logs_batch:
            conn.execute("""
                INSERT INTO logs (id, level, logger_name, message, logged_at, email_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                item["id"], item["level"], item["logger_name"],
                item["message"], item["logged_at"], item["email_id"]
            ))

        _maybe_prune(conn)
        conn.commit()

    except sqlite3.Error as e:
        _flush_failures += 1
        logger.error(
            "Failed to flush queued items to database: %s (%s/%s consecutive failures)",
            e, _flush_failures, _MAX_FLUSH_FAILURES
        )
        conn.rollback()
        with _queue_lock:
            for item in reversed(emails_batch):
                _email_queue.appendleft(item)
            for item in reversed(logs_batch):
                _log_queue.appendleft(item)
            for item in reversed(updates_batch):
                _email_update_queue.appendleft(item)

    else:
        _flush_failures = 0
        if emails_batch or updates_batch:
            logger.debug(
                "Flush complete: wrote %s email(s), %s update(s), %s log(s)",
                len(emails_batch), len(updates_batch), len(logs_batch)
            )

    finally:
        conn.close()

def flush():
    _flush()

def enqueue_log(level, logger_name, message, logged_at, email_id=None):
    with _queue_lock:
        _log_queue.append({
            "id": str(uuid.uuid4()),
            "level": level,
            "logger_name": logger_name,
            "message": message,
            "logged_at": logged_at,
            "email_id": email_id,
        })

def clear_email_id_from_logs(email_id):
    with _queue_lock:
        for entry in _log_queue:
            if entry.get("email_id") == email_id:
                entry["email_id"] = None

def get_email_by_message_id(message_id):
    if not message_id:
        return None
    try:
        with _db() as conn:
            return conn.execute(
                "SELECT * FROM emails WHERE message_id = ?",
                (message_id,)
            ).fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to query email by message_id: %s", e)
        return None

def update_email_uid(email_id, uid):
    try:
        with _db() as conn:
            conn.execute("UPDATE emails SET uid = ? WHERE id = ?", (uid, email_id))
            conn.commit()
            logger.debug("Updated UID to %s for email %s", uid, email_id)
    except sqlite3.Error as e:
        logger.error("Failed to update UID for email %s: %s", email_id, e)
        raise

def enqueue_email(uid, folder, sender, recipients, subject, date_received, message_size,
                  spam_score, rule_matched, actions, raw_headers, attachments, processed,
                  processed_at, processed_notes, email_id=None, history=None,
                  message_id=None, rspamd_learned=None, account_id=None):
    with _queue_lock:
        _email_queue.append({
            "id": email_id,
            "account_id": account_id,
            "uid": uid,
            "folder": folder,
            "sender": sender.lower() if sender else "",
            "recipients": recipients.lower() if recipients else "",
            "subject": subject,
            "date_received": date_received,
            "message_size": message_size,
            "spam_score": spam_score,
            "rule_matched": rule_matched,
            "actions": json.dumps(actions),
            "history": json.dumps(history or []),
            "raw_headers": raw_headers,
            "attachments": json.dumps(attachments) if attachments is not None else None,
            "processed": processed,
            "processed_at": processed_at,
            "processed_notes": processed_notes,
            "message_id": message_id,
            "rspamd_learned": rspamd_learned,
        })
        queue_size = len(_email_queue)
    logger.debug("Enqueued email uid=%s id=%s (email queue size: %s)", uid, email_id, queue_size)
    return email_id

def enqueue_email_update(email_id, rule_matched, actions, processed, processed_at, processed_notes,
                         history=None, rspamd_learned=None):
    with _queue_lock:
        _email_update_queue.append({
            "id": email_id,
            "rule_matched": rule_matched,
            "actions": json.dumps(actions),
            "processed": processed,
            "processed_at": processed_at,
            "processed_notes": processed_notes,
            "history": history,
            "rspamd_learned": rspamd_learned,
        })
        queue_size = len(_email_update_queue)
    logger.debug("Enqueued email update id=%s processed=%s (update queue size: %s)", email_id, processed, queue_size)

def verify():
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"Database file not found at {DB_PATH}")

    try:
        with _db() as conn:
            version = _get_version(conn)

            if version != CURRENT_VERSION:
                raise RuntimeError(f"Database version mismatch: expected {CURRENT_VERSION}, got {version}")

            expected_tables = {"accounts", "rules", "emails", "logs", "config"}
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            found_tables = {row["name"] for row in rows}
            missing = expected_tables - found_tables

            if missing:
                raise RuntimeError(f"Database missing expected tables: {missing}")

    except sqlite3.Error as e:
        logger.error("Database verification failed: %s", e)
        raise

def get_known_uids(folder, account_id=None):
    logger.debug("Fetching known UIDs for folder %s", folder)

    try:
        with _db() as conn:
            if account_id is not None:
                rows = conn.execute(
                    "SELECT uid FROM emails WHERE folder = ? AND account_id = ?",
                    (folder, account_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT uid FROM emails WHERE folder = ?",
                    (folder,)
                ).fetchall()
            known = {int(row["uid"]) for row in rows}
            logger.debug("Found %s known UID(s) in %s", len(known), folder)
            return known

    except sqlite3.Error as e:
        logger.error("Failed to fetch known UIDs for folder %s: %s", folder, e)
        raise

def get_unprocessed_emails(account_id=None):
    logger.debug("Fetching unprocessed email records")

    try:
        with _db() as conn:
            if account_id is not None:
                rows = conn.execute(
                    "SELECT * FROM emails WHERE processed = 0 AND account_id = ?",
                    (account_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM emails WHERE processed = 0").fetchall()
            logger.debug("Found %s unprocessed email record(s)", len(rows))
            return rows

    except sqlite3.Error as e:
        logger.error("Failed to fetch unprocessed email records: %s", e)
        raise

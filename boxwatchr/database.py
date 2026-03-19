import sqlite3
import os
import uuid
import time
import json
import threading
import collections
from datetime import datetime, timezone, timedelta
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "boxwatchr.db")

CURRENT_VERSION = 2

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
    return conn

def _get_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]

def _set_version(conn, version):
    conn.execute(f"PRAGMA user_version = {version}")

def _migrate_v1(conn):
    logger.info("Creating database schema")

    conn.execute("""
        CREATE TABLE emails (
            id TEXT PRIMARY KEY,
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
            raw_headers TEXT,
            attachments TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL,
            processed_notes TEXT,
            UNIQUE(uid, folder)
        )
    """)
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

    logger.info("Database schema created")

def _migrate_v2(conn):
    logger.info("Adding message_id and user_action columns to emails table")
    conn.execute("ALTER TABLE emails ADD COLUMN message_id TEXT")
    conn.execute("ALTER TABLE emails ADD COLUMN user_action TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails(message_id)")
    logger.info("Migration v2 complete")


_MIGRATIONS = [
    None,
    _migrate_v1,
    _migrate_v2,
]

def initialize():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    logger.info("Initializing database at %s", DB_PATH)

    try:
        conn = get_connection()
        current_version = _get_version(conn)
        logger.debug("Current database version: %s", current_version)
        logger.debug("Required database version: %s", CURRENT_VERSION)

        if current_version == CURRENT_VERSION:
            logger.info("Database is up to date at version %s", CURRENT_VERSION)
            conn.close()
            return

        if current_version > CURRENT_VERSION:
            logger.error(
                "Database version %s is newer than the application expects (%s). "
                "Please update boxwatchr.",
                current_version, CURRENT_VERSION
            )
            conn.close()
            raise RuntimeError("Database version is newer than the application supports")

        for version in range(current_version + 1, CURRENT_VERSION + 1):
            logger.info("Migrating database from version %s to version %s", version - 1, version)
            migration_fn = _MIGRATIONS[version]
            migration_fn(conn)
            _set_version(conn, version)
            conn.commit()
            logger.info("Database is now at version %s", version)

        conn.close()
        logger.info("Database initialization complete. Version: %s", CURRENT_VERSION)

    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise

def start_flusher():
    global _flusher_started
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
    t = threading.Thread(target=_flush_loop, daemon=True, name="db-flusher")
    t.start()
    logger.debug("Database flusher thread started")

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
            import os, signal
            os.kill(1, signal.SIGTERM)

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

    try:
        conn = get_connection()
    except sqlite3.Error as e:
        logger.error("Failed to open database connection for flush: %s", e)
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
                    id, uid, folder, sender, recipients, subject, date_received, message_size,
                    spam_score, rule_matched, actions, raw_headers, attachments,
                    processed, processed_at, processed_notes, message_id, user_action
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid, folder) DO UPDATE SET
                    sender = excluded.sender,
                    recipients = excluded.recipients,
                    subject = excluded.subject,
                    date_received = excluded.date_received,
                    message_size = excluded.message_size,
                    raw_headers = excluded.raw_headers
            """, (
                item["id"], item["uid"], item["folder"], item["sender"], item["recipients"],
                item["subject"], item["date_received"], item["message_size"],
                item["spam_score"], item["rule_matched"], item["actions"],
                item["raw_headers"], item["attachments"],
                item["processed"], item["processed_at"], item["processed_notes"],
                item["message_id"], item["user_action"]
            ))

        for item in updates_batch:
            conn.execute("""
                UPDATE emails
                SET rule_matched = ?,
                    actions = ?,
                    processed = ?,
                    processed_at = ?,
                    processed_notes = ?
                WHERE id = ?
            """, (
                item["rule_matched"], item["actions"],
                item["processed"], item["processed_at"], item["processed_notes"], item["id"]
            ))

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
        global _flush_failures
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
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM emails WHERE message_id = ? ORDER BY user_action IS NULL ASC, processed_at DESC LIMIT 1",
            (message_id,)
        ).fetchone()
        conn.close()
        return row
    except sqlite3.Error as e:
        logger.error("Failed to query email by message_id: %s", e)
        return None


def update_email_uid(email_id, uid):
    try:
        conn = get_connection()
        conn.execute("UPDATE emails SET uid = ? WHERE id = ?", (uid, email_id))
        conn.commit()
        conn.close()
        logger.debug("Updated UID to %s for email %s", uid, email_id)
    except sqlite3.Error as e:
        logger.error("Failed to update UID for email %s: %s", email_id, e)
        raise

def relink_logs_to_email(old_email_id, new_email_id):
    with _queue_lock:
        for entry in _log_queue:
            if entry.get("email_id") == old_email_id:
                entry["email_id"] = new_email_id

def set_user_action(email_id, user_action, message_id=None):
    try:
        conn = get_connection()
        if message_id:
            conn.execute(
                "UPDATE emails SET user_action = ?, message_id = ? WHERE id = ?",
                (user_action, message_id, email_id)
            )
        else:
            conn.execute("UPDATE emails SET user_action = ? WHERE id = ?", (user_action, email_id))
        conn.commit()
        conn.close()
        logger.info("Set user_action=%s for email %s", user_action, email_id)
    except sqlite3.Error as e:
        logger.error("Failed to set user_action for email %s: %s", email_id, e)
        raise


def enqueue_email(uid, folder, sender, recipients, subject, date_received, message_size,
                  spam_score, rule_matched, actions, raw_headers, attachments, processed,
                  processed_at, processed_notes, email_id=None, message_id="", user_action=None):
    if email_id is None:
        email_id = str(uuid.uuid4())
    with _queue_lock:
        _email_queue.append({
            "id": email_id,
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
            "raw_headers": raw_headers,
            "attachments": json.dumps(attachments) if attachments is not None else None,
            "processed": processed,
            "processed_at": processed_at,
            "processed_notes": processed_notes,
            "message_id": message_id,
            "user_action": user_action,
        })
    return email_id

def enqueue_email_update(email_id, rule_matched, actions, processed, processed_at, processed_notes):
    with _queue_lock:
        _email_update_queue.append({
            "id": email_id,
            "rule_matched": rule_matched,
            "actions": json.dumps(actions),
            "processed": processed,
            "processed_at": processed_at,
            "processed_notes": processed_notes,
        })

def verify():
    logger.debug("Verifying database integrity")

    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"Database file not found at {DB_PATH}")

    try:
        conn = get_connection()
        version = _get_version(conn)

        if version != CURRENT_VERSION:
            conn.close()
            raise RuntimeError(f"Database version mismatch: expected {CURRENT_VERSION}, got {version}")

        expected_tables = {"emails", "logs"}
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        found_tables = {row["name"] for row in rows}
        missing = expected_tables - found_tables

        if missing:
            conn.close()
            raise RuntimeError(f"Database missing expected tables: {missing}")

        conn.close()
        logger.debug("Database verification passed (version=%s, tables=%s)", version, sorted(found_tables))

    except sqlite3.Error as e:
        logger.error("Database verification failed: %s", e)
        raise

def get_known_uids(folder):
    logger.debug("Fetching known UIDs for folder %s", folder)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT uid FROM emails WHERE folder = ?", (folder,))
        rows = cursor.fetchall()
        conn.close()
        known = {int(row["uid"]) for row in rows}
        logger.debug("Found %s known UID(s) in %s", len(known), folder)
        return known

    except sqlite3.Error as e:
        logger.error("Failed to fetch known UIDs for folder %s: %s", folder, e)
        raise

def get_unprocessed_emails():
    logger.info("Fetching unprocessed email records")

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM emails WHERE processed = 0")
        rows = cursor.fetchall()
        conn.close()
        logger.info("Found %s unprocessed email record(s)", len(rows))
        return rows

    except sqlite3.Error as e:
        logger.error("Failed to fetch unprocessed email records: %s", e)
        raise

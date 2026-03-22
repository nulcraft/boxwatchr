import sqlite3
import os
import uuid
import time
import json
import threading
import collections
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

def _get_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]

def _set_version(conn, version):
    conn.execute(f"PRAGMA user_version = {version}")

def _create_schema(conn):
    logger.info("Creating database schema (v1)")

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
            history TEXT NOT NULL DEFAULT '[]',
            raw_headers TEXT,
            attachments TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL,
            processed_notes TEXT,
            user_action TEXT,
            message_id TEXT,
            rspamd_learned TEXT,
            UNIQUE(uid, folder)
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
        conn = get_connection()
    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise

    try:
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
    finally:
        conn.close()

def get_config(key, default=None):
    try:
        conn = get_connection()
        try:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to read config key %r: %s", key, e)
        return default

def set_config(key, value):
    try:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to write config key %r: %s", key, e)
        raise

def bulk_set_config(items_dict):
    try:
        conn = get_connection()
        try:
            for key, value in items_dict.items():
                conn.execute(
                    "INSERT INTO config (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value))
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to bulk-write config: %s", e)
        raise

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
                    spam_score, rule_matched, actions, history, raw_headers, attachments,
                    processed, processed_at, processed_notes, user_action, message_id, rspamd_learned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                item["spam_score"], item["rule_matched"], item["actions"], item["history"],
                item["raw_headers"], item["attachments"],
                item["processed"], item["processed_at"], item["processed_notes"],
                item["user_action"], item["message_id"], item["rspamd_learned"]
            ))

        for item in updates_batch:
            if item["rspamd_learned"] is not None:
                if item["history"] is not None:
                    conn.execute("""
                        UPDATE emails
                        SET rule_matched = ?,
                            actions = ?,
                            processed = ?,
                            processed_at = ?,
                            processed_notes = ?,
                            history = ?,
                            rspamd_learned = ?
                        WHERE id = ?
                    """, (
                        item["rule_matched"], item["actions"],
                        item["processed"], item["processed_at"], item["processed_notes"],
                        json.dumps(item["history"]), item["rspamd_learned"], item["id"]
                    ))
                else:
                    conn.execute("""
                        UPDATE emails
                        SET rule_matched = ?,
                            actions = ?,
                            processed = ?,
                            processed_at = ?,
                            processed_notes = ?,
                            rspamd_learned = ?
                        WHERE id = ?
                    """, (
                        item["rule_matched"], item["actions"],
                        item["processed"], item["processed_at"], item["processed_notes"],
                        item["rspamd_learned"], item["id"]
                    ))
            else:
                if item["history"] is not None:
                    conn.execute("""
                        UPDATE emails
                        SET rule_matched = ?,
                            actions = ?,
                            processed = ?,
                            processed_at = ?,
                            processed_notes = ?,
                            history = ?
                        WHERE id = ?
                    """, (
                        item["rule_matched"], item["actions"],
                        item["processed"], item["processed_at"], item["processed_notes"],
                        json.dumps(item["history"]), item["id"]
                    ))
                else:
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

def get_email_by_id(email_id):
    if not email_id:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            return row
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to query email by id: %s", e)
        return None

def get_email_by_message_id(message_id):
    if not message_id:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM emails WHERE message_id = ?",
                (message_id,)
            ).fetchone()
            return row
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to query email by message_id: %s", e)
        return None

def update_email_uid(email_id, uid):
    try:
        conn = get_connection()
        try:
            conn.execute("UPDATE emails SET uid = ? WHERE id = ?", (uid, email_id))
            conn.commit()
            logger.debug("Updated UID to %s for email %s", uid, email_id)
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to update UID for email %s: %s", email_id, e)
        raise

def set_rspamd_learned(email_id, learned):
    try:
        conn = get_connection()
        try:
            conn.execute("UPDATE emails SET rspamd_learned = ? WHERE id = ?", (learned, email_id))
            conn.commit()
            logger.debug("Set rspamd_learned=%s for email %s", learned, email_id)
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to set rspamd_learned for email %s: %s", email_id, e)
        raise

def set_user_action(email_id, user_action, rspamd_learned=None):
    try:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT history FROM emails WHERE id = ?", (email_id,)).fetchone()
            current_history = json.loads(row["history"] or "[]") if row else []
            entry = {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "by": "user",
                "action": user_action,
            }
            new_history = json.dumps(current_history + [entry])
            if rspamd_learned is not None:
                conn.execute(
                    "UPDATE emails SET user_action = ?, history = ?, rspamd_learned = ? WHERE id = ?",
                    (user_action, new_history, rspamd_learned, email_id)
                )
            else:
                conn.execute(
                    "UPDATE emails SET user_action = ?, history = ? WHERE id = ?",
                    (user_action, new_history, email_id)
                )
            conn.commit()
            logger.debug("Set user_action=%s for email %s", user_action, email_id)
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to set user_action for email %s: %s", email_id, e)
        raise

def enqueue_email(uid, folder, sender, recipients, subject, date_received, message_size,
                  spam_score, rule_matched, actions, raw_headers, attachments, processed,
                  processed_at, processed_notes, email_id=None, user_action=None, history=None,
                  message_id=None, rspamd_learned=None):
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
            "history": json.dumps(history or []),
            "raw_headers": raw_headers,
            "attachments": json.dumps(attachments) if attachments is not None else None,
            "processed": processed,
            "processed_at": processed_at,
            "processed_notes": processed_notes,
            "user_action": user_action,
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
        conn = get_connection()
        try:
            version = _get_version(conn)

            if version != CURRENT_VERSION:
                raise RuntimeError(f"Database version mismatch: expected {CURRENT_VERSION}, got {version}")

            expected_tables = {"emails", "logs", "config"}
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            found_tables = {row["name"] for row in rows}
            missing = expected_tables - found_tables

            if missing:
                raise RuntimeError(f"Database missing expected tables: {missing}")
        finally:
            conn.close()

    except sqlite3.Error as e:
        logger.error("Database verification failed: %s", e)
        raise

def get_known_uids(folder):
    logger.debug("Fetching known UIDs for folder %s", folder)

    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT uid FROM emails WHERE folder = ?", (folder,)).fetchall()
            known = {int(row["uid"]) for row in rows}
            logger.debug("Found %s known UID(s) in %s", len(known), folder)
            return known
        finally:
            conn.close()

    except sqlite3.Error as e:
        logger.error("Failed to fetch known UIDs for folder %s: %s", folder, e)
        raise

def get_unprocessed_emails():
    logger.debug("Fetching unprocessed email records")

    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM emails WHERE processed = 0").fetchall()
            logger.debug("Found %s unprocessed email record(s)", len(rows))
            return rows
        finally:
            conn.close()

    except sqlite3.Error as e:
        logger.error("Failed to fetch unprocessed email records: %s", e)
        raise

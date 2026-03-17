import sqlite3
import os
from datetime import datetime, timezone
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "boxwatchr.db")

CURRENT_VERSION = 1

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def _get_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]

def _set_version(conn, version):
    conn.execute(f"PRAGMA user_version = {version}")

def _migrate_v1(conn):
    logger.info("Running migration to version 1")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            date_received TEXT,
            message_size INTEGER,
            spam_score REAL,
            rule_matched TEXT,
            action_taken TEXT,
            destination_folder TEXT,
            raw_headers TEXT,
            processed_at TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0
        )
    """)
    logger.debug("Emails table created")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            logger_name TEXT NOT NULL,
            message TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            email_id INTEGER DEFAULT NULL,
            FOREIGN KEY (email_id) REFERENCES emails (id)
        )
    """)
    logger.debug("Logs table created")

    logger.info("Migration to version 1 complete")

_MIGRATIONS = [
    None,
    _migrate_v1,
]

def initialize():
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
                "Please update Boxwatchr.",
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

def insert_email(uid, sender, recipients, subject, date_received, message_size,
                 spam_score, rule_matched, action_taken, destination_folder,
                 raw_headers, processed_at, dry_run=0):
    logger.debug("Inserting email record for UID %s from %s", uid, sender)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO emails (
                uid, sender, recipients, subject, date_received, message_size,
                spam_score, rule_matched, action_taken, destination_folder,
                raw_headers, processed_at, dry_run
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid, sender, recipients, subject, date_received, message_size,
              spam_score, rule_matched, action_taken, destination_folder,
              raw_headers, processed_at, dry_run))
        conn.commit()
        email_id = cursor.lastrowid
        conn.close()
        logger.info("Email record inserted for UID %s from %s (id=%s)", uid, sender, email_id)
        return email_id

    except sqlite3.Error as e:
        logger.error("Failed to insert email record for UID %s: %s", uid, e)
        raise

def insert_log(level, logger_name, message, logged_at, email_id=None):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (level, logger_name, message, logged_at, email_id)
            VALUES (?, ?, ?, ?, ?)
        """, (level, logger_name, message, logged_at, email_id))
        conn.commit()
        conn.close()

    except sqlite3.Error:
        pass

def get_dry_run_emails():
    logger.info("Fetching unprocessed dry run email records")

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM emails WHERE dry_run = 1")
        rows = cursor.fetchall()
        conn.close()
        logger.info("Found %s dry run email record(s) to reprocess", len(rows))
        return rows

    except sqlite3.Error as e:
        logger.error("Failed to fetch dry run email records: %s", e)
        raise

def update_email_after_reprocess(email_id, rule_matched, action_taken, destination_folder, processed_at):
    logger.debug("Updating email record id %s after real processing", email_id)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE emails
            SET rule_matched = ?,
                action_taken = ?,
                destination_folder = ?,
                processed_at = ?,
                dry_run = 0
            WHERE id = ?
        """, (rule_matched, action_taken, destination_folder, processed_at, email_id))
        conn.commit()
        conn.close()
        logger.info("Email record id %s updated after real processing", email_id)

    except sqlite3.Error as e:
        logger.error("Failed to update email record id %s: %s", email_id, e)
        raise
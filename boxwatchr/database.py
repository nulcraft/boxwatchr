import sqlite3
import os
from datetime import datetime, timezone
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "boxwatchr.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize():
    logger.info("Initializing database at %s", DB_PATH)

    try:
        conn = get_connection()
        cursor = conn.cursor()

        logger.debug("Checking for the emails table")
        cursor.execute("""
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
                processed_at TEXT NOT NULL
            )
        """)
        logger.debug("Emails table is ready")

        logger.debug("Checking for the logs table")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                logger_name TEXT NOT NULL,
                message TEXT NOT NULL,
                logged_at TEXT NOT NULL
            )
        """)
        logger.debug("Logs table is ready")

        conn.commit()
        conn.close()
        logger.info("Database has been initialized successfully")

    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise

def insert_email(uid, sender, recipients, subject, date_received, message_size,
                 spam_score, rule_matched, action_taken, destination_folder,
                 raw_headers, processed_at):
    logger.debug("Inserting email record for UID %s from %s", uid, sender)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO emails (
                uid, sender, recipients, subject, date_received, message_size,
                spam_score, rule_matched, action_taken, destination_folder,
                raw_headers, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid, sender, recipients, subject, date_received, message_size,
              spam_score, rule_matched, action_taken, destination_folder,
              raw_headers, processed_at))
        conn.commit()
        conn.close()
        logger.info("Email record inserted for UID %s from %s", uid, sender)

    except sqlite3.Error as e:
        logger.error("Failed to insert email record for UID %s: %s", uid, e)
        raise

def insert_log(level, logger_name, message, logged_at):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (level, logger_name, message, logged_at)
            VALUES (?, ?, ?, ?)
        """, (level, logger_name, message, logged_at))
        conn.commit()
        conn.close()

    except sqlite3.Error:
        pass
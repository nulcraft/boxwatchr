import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))

IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USERNAME = os.environ.get("IMAP_USERNAME", "")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
IMAP_POLL_INTERVAL = int(os.environ.get("IMAP_POLL_INTERVAL", "60"))
IMAP_TRASH_FOLDER = os.environ.get("IMAP_TRASH_FOLDER", "") or None
IMAP_SPAM_FOLDER = os.environ.get("IMAP_SPAM_FOLDER", "") or None

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DRYRUN = os.environ.get("DRYRUN", "false").lower() == "true"

SPAM_LEARNING = os.environ.get("SPAM_LEARNING", "both").lower()
SPAM_THRESHOLD = float(os.environ.get("SPAM_THRESHOLD", "5.0"))
SPAM_ACTION = os.environ.get("SPAM_ACTION", "junk").lower()
HAM_THRESHOLD = float(os.environ.get("HAM_THRESHOLD", "2.0"))

RSPAMD_HOST = os.environ.get("RSPAMD_HOST", "127.0.0.1")
RSPAMD_PORT = int(os.environ.get("RSPAMD_PORT", "11333"))
RSPAMD_CONTROLLER_PORT = int(os.environ.get("RSPAMD_CONTROLLER_PORT", "11334"))
RSPAMD_PASSWORD = os.environ.get("RSPAMD_PASSWORD", "")

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

DB_PRUNE_DAYS = int(os.environ.get("DB_PRUNE_DAYS", "0"))
import os
from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USERNAME = os.environ.get("IMAP_USERNAME", "")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
IMAP_POLL_INTERVAL = int(os.environ.get("IMAP_POLL_INTERVAL", "60"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
TESTING = os.environ.get("TESTING", "false").lower() == "true"

SPAM_THRESHOLD = float(os.environ.get("SPAM_THRESHOLD", "5.0"))
SPAM_ACTION = os.environ.get("SPAM_ACTION", "move").lower()
SPAM_FOLDER = os.environ.get("SPAM_FOLDER", "Junk")

RSPAMD_HOST = os.environ.get("RSPAMD_HOST", "127.0.0.1")
RSPAMD_PORT = int(os.environ.get("RSPAMD_PORT", "11333"))

WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
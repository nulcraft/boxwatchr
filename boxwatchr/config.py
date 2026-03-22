import os
import json
import logging

# Infrastructure settings — read from environment, needed before the DB is available.
# These are Docker/container-level concerns, not app config.
RSPAMD_HOST = "127.0.0.1"
RSPAMD_PORT = 11333
RSPAMD_CONTROLLER_PORT = 11334
RSPAMD_PASSWORD = os.environ.get("RSPAMD_PASSWORD", "")

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rules.yaml")

# App settings — defaults only. Overridden by load() after database.initialize().
SETUP_COMPLETE = False

IMAP_ACCOUNTS = []
IMAP_HOST = ""
IMAP_PORT = 993
IMAP_USERNAME = ""
IMAP_PASSWORD = ""
IMAP_FOLDER = "INBOX"
IMAP_POLL_INTERVAL = 60
IMAP_TRASH_FOLDER = None
IMAP_SPAM_FOLDER = None
IMAP_TLS_MODE = "ssl"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DRYRUN = False
WEB_PASSWORD = ""
DB_PRUNE_DAYS = 0

def load():
    """Load app settings from the config table. Call after database.initialize()."""
    from boxwatchr.database import get_config
    global SETUP_COMPLETE
    global IMAP_ACCOUNTS, IMAP_HOST, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD
    global IMAP_FOLDER, IMAP_POLL_INTERVAL, IMAP_TRASH_FOLDER, IMAP_SPAM_FOLDER, IMAP_TLS_MODE
    global LOG_LEVEL, DRYRUN, WEB_PASSWORD, DB_PRUNE_DAYS

    SETUP_COMPLETE = get_config("setup_complete", "false") == "true"

    try:
        IMAP_ACCOUNTS = json.loads(get_config("imap_accounts", "[]"))
    except (json.JSONDecodeError, TypeError):
        IMAP_ACCOUNTS = []

    if IMAP_ACCOUNTS:
        acc = IMAP_ACCOUNTS[0]
        IMAP_HOST = acc.get("host", "")
        IMAP_PORT = int(acc.get("port", 993))
        IMAP_USERNAME = acc.get("username", "")
        IMAP_PASSWORD = acc.get("password", "")
        IMAP_FOLDER = acc.get("folder", "INBOX")
        IMAP_POLL_INTERVAL = int(acc.get("poll_interval", 60))
        IMAP_TRASH_FOLDER = acc.get("trash_folder") or None
        IMAP_SPAM_FOLDER = acc.get("spam_folder") or None
        IMAP_TLS_MODE = acc.get("tls_mode", "ssl")

    LOG_LEVEL = get_config("log_level", LOG_LEVEL).upper()
    DRYRUN = get_config("dry_run", "false") == "true"
    WEB_PASSWORD = get_config("web_password", "")
    DB_PRUNE_DAYS = int(get_config("db_prune_days", "0"))

    _update_log_level()

def reload():
    load()

def _update_log_level():
    for name in logging.Logger.manager.loggerDict:
        log = logging.getLogger(name)
        for handler in log.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(LOG_LEVEL)

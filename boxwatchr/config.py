import os
import logging

# Infrastructure settings — read from environment, needed before the DB is available.
# These are Docker/container-level concerns, not app config.
RSPAMD_HOST = "127.0.0.1"
RSPAMD_PORT = 11333
RSPAMD_CONTROLLER_PORT = 11334
RSPAMD_PASSWORD = os.environ.get("RSPAMD_PASSWORD", "")

# App settings — defaults only. Overridden by load() after database.initialize().
SETUP_COMPLETE = False

ACCOUNT_ID = ""
ACCOUNT_NAME = ""

IMAP_HOST = ""
IMAP_PORT = 993
IMAP_USERNAME = ""
IMAP_PASSWORD = ""
IMAP_FOLDER = "INBOX"
IMAP_POLL_INTERVAL = 60
IMAP_TLS_MODE = "ssl"

LOG_LEVEL = "INFO"
DRYRUN = False
WEB_PASSWORD = ""
DB_PRUNE_DAYS = 0
CHECK_FOR_UPDATES = True

def load():
    """Load app settings from the database. Call after database.initialize()."""
    from boxwatchr.database import get_config, get_first_account
    from boxwatchr.crypto import decrypt_password
    global SETUP_COMPLETE
    global ACCOUNT_ID, ACCOUNT_NAME
    global IMAP_HOST, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD
    global IMAP_FOLDER, IMAP_POLL_INTERVAL, IMAP_TLS_MODE
    global LOG_LEVEL, DRYRUN, WEB_PASSWORD, DB_PRUNE_DAYS, CHECK_FOR_UPDATES

    SETUP_COMPLETE = get_config("setup_complete", "false") == "true"

    account = get_first_account()
    if account:
        ACCOUNT_ID = account["id"]
        ACCOUNT_NAME = account["name"]
        IMAP_HOST = account["host"]
        IMAP_PORT = int(account["port"])
        IMAP_USERNAME = account["username"]
        IMAP_PASSWORD = decrypt_password(account["password"])
        IMAP_FOLDER = account["folder"]
        IMAP_POLL_INTERVAL = int(account["poll_interval"])
        IMAP_TLS_MODE = account["tls_mode"]

    LOG_LEVEL = get_config("log_level", LOG_LEVEL).upper()
    DRYRUN = get_config("dry_run", "false") == "true"
    WEB_PASSWORD = get_config("web_password", "")
    DB_PRUNE_DAYS = int(get_config("db_prune_days", "0"))
    CHECK_FOR_UPDATES = get_config("check_for_updates", "true") == "true"

    _update_log_level()

def reload():
    load()

def _update_log_level():
    for name in logging.Logger.manager.loggerDict:
        log = logging.getLogger(name)
        for handler in log.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(LOG_LEVEL)

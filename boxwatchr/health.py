import os
import sys
import signal
import time
import socket
import threading
import collections
import requests
from imapclient import IMAPClient
from boxwatchr import config
from boxwatchr.database import flush as flush_db, initialize as db_initialize, start_flusher as db_start_flusher, verify as db_verify, DB_PATH as _db_path
from boxwatchr import imap as _imap
from boxwatchr.rules import load_rules as _load_rules
from boxwatchr.logger import get_logger

_CheckResult = collections.namedtuple("_CheckResult", ["ok", "reason", "fatal"])

logger = get_logger("boxwatchr.health")

_MONITOR_INTERVAL = 60
_RETRY_INTERVAL = 5
_MAX_FAILURES = 10
_STARTUP_CHECK_INTERVAL = 5
_STARTUP_PER_SERVICE_TIMEOUT = 30
_DIVIDER = "=" * 35

def _tcp_check(host, port):
    logger.debug("TCP check: connecting to %s:%s", host, port)
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        logger.debug("TCP check: %s:%s is reachable", host, port)
        return _CheckResult(True, "", False)
    except OSError as e:
        logger.debug("TCP check: %s:%s unreachable: %s", host, port, e)
        return _CheckResult(False, str(e), False)

def _check_rspamd():
    try:
        url = "http://%s:%s/ping" % (config.RSPAMD_HOST, config.RSPAMD_PORT)
        logger.debug("rspamd health check: GET %s", url)
        response = requests.get(url, timeout=2)
        if response.text.strip() == "pong":
            logger.debug("rspamd health check: OK")
            return _CheckResult(True, "", False)
        logger.debug("rspamd health check: unexpected response: %s", response.text.strip())
        return _CheckResult(False, "unexpected response: %s" % response.text.strip(), False)
    except requests.exceptions.RequestException as e:
        logger.debug("rspamd health check: failed: %s", e)
        return _CheckResult(False, str(e), False)

def _check_redis():
    return _tcp_check("127.0.0.1", 6379)

def _check_unbound():
    return _tcp_check("127.0.0.1", 5335)

def _check_web():
    return _tcp_check("127.0.0.1", int(os.environ.get("WEB_PORT", 8143)))

def _check_imap():
    if not config.SETUP_COMPLETE or not config.IMAP_HOST:
        return _CheckResult(True, "", False)
    logger.debug("IMAP health check: connecting to %s:%s", config.IMAP_HOST, config.IMAP_PORT)
    try:
        _use_ssl = config.IMAP_TLS_MODE != "none" and config.IMAP_TLS_MODE != "starttls"
        client = IMAPClient(config.IMAP_HOST, port=config.IMAP_PORT, ssl=_use_ssl, timeout=10)
        if config.IMAP_TLS_MODE == "starttls":
            client.starttls()
    except Exception as e:
        logger.debug("IMAP health check: connection failed: %s", e)
        return _CheckResult(False, str(e), False)

    try:
        client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        logger.debug("IMAP health check: authentication failed: %s", e)
        return _CheckResult(False, "authentication failed: %s" % e, True)

    try:
        client.select_folder(config.IMAP_FOLDER)
        client.logout()
        logger.debug("IMAP health check: OK")
        return _CheckResult(True, "", False)
    except Exception:
        reason = "folder %r does not exist on the server" % config.IMAP_FOLDER
        try:
            folders = client.list_folders()
            names = sorted(f[2] for f in folders)
            reason += ". We found these folders:\n" + "\n".join("- %s" % n for n in names)
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass
        logger.debug("IMAP health check: folder check failed for %r", config.IMAP_FOLDER)
        return _CheckResult(False, reason, True)

def initialize_database():
    print(_DIVIDER, flush=True)
    print("Initializing database...", flush=True)
    print(_DIVIDER, flush=True)

    logger.info("Initializing database at %s", _db_path)

    try:
        db_initialize()
        db_start_flusher()
        db_verify()
    except Exception as e:
        logger.error("Fatal: database initialization failed: %s\n\nShutting down.", e)
        fatal_shutdown()

    logger.info("Database ready.")
    print(flush=True)

def fatal_shutdown():
    flush_db()
    os.kill(os.getpid(), signal.SIGTERM)
    sys.exit(2)

def load_rules_startup():
    print(_DIVIDER, flush=True)
    print("Loading rules...", flush=True)
    print(_DIVIDER, flush=True)

    try:
        loaded_rules = _load_rules()
    except Exception as e:
        logger.error("Fatal: could not load rules: %s\n\nShutting down.", e)
        fatal_shutdown()

    logger.info("Loaded %s valid rule(s)", len(loaded_rules))
    print(flush=True)
    return loaded_rules

_STARTUP_SERVICES = [
    ("Unbound", _check_unbound),
    ("Redis", _check_redis),
    ("rspamd", _check_rspamd),
]

def start_services_sequentially():
    for name, check_fn in _STARTUP_SERVICES:
        print(_DIVIDER, flush=True)
        print("Starting: %s service..." % name, flush=True)
        print(_DIVIDER, flush=True)

        deadline = time.monotonic() + _STARTUP_PER_SERVICE_TIMEOUT
        ready = False
        last_reason = ""

        while time.monotonic() < deadline:
            result = check_fn()
            if result.ok:
                ready = True
                break
            if result.fatal:
                logger.error(
                    "Fatal: %s service failed to start: %s\n\nShutting down.",
                    name, result.reason
                )
                fatal_shutdown()
            last_reason = result.reason
            logger.debug("Waiting for %s to be ready...", name)
            time.sleep(_STARTUP_CHECK_INTERVAL)

        if not ready:
            if last_reason:
                logger.error(
                    "Fatal: %s service did not start within %ds: %s\n\nShutting down.",
                    name, _STARTUP_PER_SERVICE_TIMEOUT, last_reason
                )
            else:
                logger.error(
                    "Fatal: %s service did not start within %ds.\n\nShutting down.",
                    name, _STARTUP_PER_SERVICE_TIMEOUT
                )
            fatal_shutdown()

        logger.info("%s service is up and ready.", name)
        print(flush=True)

def start_web():
    from boxwatchr.web.app import start_dashboard
    print(_DIVIDER, flush=True)
    print("Starting: Web service...", flush=True)
    print(_DIVIDER, flush=True)
    start_dashboard()
    print(flush=True)

def start_imap(loaded_rules):
    print(_DIVIDER, flush=True)
    print("Starting: IMAP service...", flush=True)
    print(_DIVIDER, flush=True)

    deadline = time.monotonic() + _STARTUP_PER_SERVICE_TIMEOUT
    client = None
    last_reason = ""

    while time.monotonic() < deadline:
        logger.debug("Attempting IMAP connection to %s:%s", config.IMAP_HOST, config.IMAP_PORT)
        try:
            client = _imap.connect()
        except _imap.FatalImapError as e:
            logger.error("Fatal: IMAP authentication failed: %s\n\nShutting down.", e)
            fatal_shutdown()
        except Exception as e:
            last_reason = str(e)
            logger.debug("IMAP connection attempt failed: %s", e)
            time.sleep(_STARTUP_CHECK_INTERVAL)
            continue

        logger.info("Connected to %s:%s as %s", config.IMAP_HOST, config.IMAP_PORT, config.IMAP_USERNAME)
        break
    else:
        logger.error(
            "Fatal: IMAP service did not start within %ds: %s\n\nShutting down.",
            _STARTUP_PER_SERVICE_TIMEOUT, last_reason
        )
        fatal_shutdown()

    try:
        try:
            folder_names = _imap.list_folder_names(client)
        except Exception as e:
            logger.error("Fatal: Could not list IMAP folders: %s\n\nShutting down.", e)
            fatal_shutdown()

        folder_set = set(folder_names)
        folder_list = "\n".join("- %s" % f for f in folder_names)
        logger.debug("Found %s IMAP folder(s): %s", len(folder_names), ", ".join(sorted(folder_names)))

        if config.IMAP_FOLDER not in folder_set:
            logger.error(
                "Fatal: Watched folder %r does not exist on the server. We found these folders:\n%s\n\nShutting down.",
                config.IMAP_FOLDER, folder_list
            )
            fatal_shutdown()

        logger.debug("Watched folder %r verified on server", config.IMAP_FOLDER)

        destinations = {
            action["destination"]
            for rule in loaded_rules
            for action in rule["actions"]
            if action["type"] == "move"
        }
        logger.debug(
            "Verifying %s rule move destination(s): %s",
            len(destinations), ", ".join(sorted(destinations)) if destinations else "none"
        )
        missing = [d for d in sorted(destinations) if d not in folder_set]
        if missing:
            logger.error(
                "Fatal: Rule destination folder(s) not found on the server: %s\n\nWe found these folders:\n%s\n\nShutting down.",
                ", ".join(missing), folder_list
            )
            fatal_shutdown()

        logger.info("All IMAP folders verified on server")

    finally:
        try:
            client.logout()
        except Exception:
            pass

    logger.info("IMAP service is up and ready.")
    print(flush=True)

def service_check():
    logger.debug("Running service health check")
    checks = {
        "rspamd": _check_rspamd,
        "redis": _check_redis,
        "unbound": _check_unbound,
        "web": _check_web,
        "imap": _check_imap,
    }
    failed = []
    for name, fn in checks.items():
        result = fn()
        if result.fatal:
            logger.error("Fatal service failure (%s): %s. Shutting down.", name, result.reason)
            fatal_shutdown()
        if not result.ok:
            failed.append(name)
    if failed:
        logger.warning("Service check failed: %s", ", ".join(failed))
    else:
        logger.debug("All services healthy")
    return failed

def wait_for_services():
    logger.info("Waiting for all services to recover...")
    while True:
        failed = service_check()
        if not failed:
            logger.info("All services recovered")
            return
        time.sleep(_RETRY_INTERVAL)

def _monitor_loop():
    consecutive_failures = 0
    while True:
        time.sleep(_MONITOR_INTERVAL)
        logger.debug("Health monitor: running periodic service check")
        failed = service_check()
        if not failed:
            if consecutive_failures > 0:
                logger.info("All services recovered after %s failure(s)", consecutive_failures)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            logger.warning(
                "Services not yet available, retrying (%s/%s)",
                consecutive_failures, _MAX_FAILURES
            )
            if consecutive_failures >= _MAX_FAILURES:
                logger.error(
                    "Services have been unavailable for too long. Still failing: %s. Shutting down.",
                    ", ".join(failed)
                )
                fatal_shutdown()

def start_monitor():
    t = threading.Thread(target=_monitor_loop, daemon=True, name="health-monitor")
    t.start()
    logger.debug("Health monitor started, checking every %s seconds", _MONITOR_INTERVAL)

import os
import signal
import time
import socket
import threading
import requests
from imapclient import IMAPClient
from boxwatchr import config
from boxwatchr.database import flush as flush_db
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.health")

_MONITOR_INTERVAL = 60
_RETRY_INTERVAL = 5
_MAX_FAILURES = 10
_STARTUP_TIMEOUT = 300


def _tcp_check(host, port):
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _check_rspamd():
    try:
        url = f"http://{config.RSPAMD_HOST}:{config.RSPAMD_PORT}/ping"
        response = requests.get(url, timeout=2)
        return response.text.strip() == "pong"
    except requests.exceptions.RequestException:
        return False


def _check_redis():
    return _tcp_check("127.0.0.1", 6379)


def _check_unbound():
    return _tcp_check("127.0.0.1", 5335)


def _check_web():
    return _tcp_check("127.0.0.1", 80)


def _check_imap():
    try:
        client = IMAPClient(config.IMAP_HOST, port=config.IMAP_PORT, ssl=True)
        client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        client.select_folder(config.IMAP_FOLDER)
        client.logout()
        return True
    except Exception:
        return False


def service_check():
    checks = {
        "rspamd": _check_rspamd,
        "redis": _check_redis,
        "unbound": _check_unbound,
        "web": _check_web,
        "imap": _check_imap,
    }
    failed = [name for name, fn in checks.items() if not fn()]
    if failed:
        logger.warning("Service check failed: %s", ", ".join(failed))
        return False
    logger.debug("All services healthy")
    return True


def wait_for_services(startup=False):
    logger.info("Waiting for all services to be ready")
    waited = 0
    while True:
        if service_check():
            logger.info("All services are ready")
            return
        if startup and waited >= _STARTUP_TIMEOUT:
            logger.error("Services did not come up within %s seconds. Shutting down.", _STARTUP_TIMEOUT)
            flush_db()
            os.kill(1, signal.SIGTERM)
            return
        time.sleep(_RETRY_INTERVAL)
        if startup:
            waited += _RETRY_INTERVAL


def _monitor_loop():
    consecutive_failures = 0
    while True:
        time.sleep(_MONITOR_INTERVAL)
        if service_check():
            if consecutive_failures > 0:
                logger.info("All services recovered after %s failure(s)", consecutive_failures)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            logger.error(
                "Service check failed (%s/%s consecutive failures)",
                consecutive_failures, _MAX_FAILURES
            )
            if consecutive_failures >= _MAX_FAILURES:
                logger.error("Services have been unavailable for too long. Shutting down.")
                flush_db()
                os.kill(1, signal.SIGTERM)


def start_monitor():
    t = threading.Thread(target=_monitor_loop, daemon=True, name="health-monitor")
    t.start()
    logger.debug("Health monitor started, checking every %s seconds", _MONITOR_INTERVAL)

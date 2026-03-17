import logging
import os
from datetime import datetime, timezone

class DatabaseHandler(logging.Handler):
    def emit(self, record):
        try:
            from boxwatchr.database import insert_log
            logged_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            insert_log(record.levelname, record.name, record.getMessage(), logged_at)
        except Exception:
            pass

def get_logger(name):
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        db_handler = DatabaseHandler()
        db_handler.setLevel(logging.DEBUG)
        db_handler.setFormatter(formatter)
        logger.addHandler(db_handler)

    return logger
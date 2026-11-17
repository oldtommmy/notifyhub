import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(log_path="", verbose=False):
    logger = logging.getLogger("notifyhub")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler = None
    failure = None
    if log_path:
        path = Path(log_path).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        except OSError as error:
            failure = (path, error)

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if failure is not None:
        path, error = failure
        logger.warning("cannot open log file %s: %s; logging to stderr", path, error)

    return logger

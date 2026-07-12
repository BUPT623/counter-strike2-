"""Project logging setup."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from project_paths import LOG_DIR


def setup_logging(name: str, log_file_prefix: str | None = None) -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file_prefix:
        timestamp = datetime.now().strftime("%Y%m%d")
        file_path = os.path.join(LOG_DIR, f"{log_file_prefix}_{timestamp}.log")
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger

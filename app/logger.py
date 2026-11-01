# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler

from .cfg import LOG_DIR, LOG_PATH

LOGGER = logging.getLogger("disguise-dashboard")


def setup_logging(config: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level_name = str(config.get("logging", {}).get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    LOGGER.setLevel(level)
    LOGGER.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s %(threadName)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=int(config.get("logging", {}).get("max_bytes", 5_000_000)),
        backupCount=int(config.get("logging", {}).get("backup_count", 3)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    LOGGER.addHandler(console)


def read_log_tail(lines: int = 200) -> str:
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
        return "".join(content[-max(1, min(lines, 2000)) :])
    except FileNotFoundError:
        return "Log file has not been created yet."


def open_log_folder():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(LOG_DIR))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(LOG_DIR)])

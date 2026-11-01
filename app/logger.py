# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler

from app import cfg

LOGGER = logging.getLogger("dpd")


def setup_logging(config: dict) -> None:
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(cfg.value("logging.level", "INFO"))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(module)s %(threadName)s]: %(message)s"
    )
    file_handler = RotatingFileHandler(
        cfg.LOG_FILE,
        maxBytes=cfg.value("logging.max_bytes", 5_000_000),
        backupCount=cfg.value("logging.backup_count", 3),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    LOGGER.addHandler(console)


def read_log_tail(lines: int = 200) -> str:
    try:
        with cfg.LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
        return "".join(content[-max(1, min(lines, 2000)) :])
    except FileNotFoundError:
        return "Log file has not been created yet."


def open_log_folder():
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(cfg.LOG_DIR))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(cfg.LOG_DIR)])

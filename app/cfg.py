# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


from pathlib import Path


MAX_FLOAT_SENTINEL = 1.0e300

APP_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"

LOCAL_DIR = APP_DIR / ".private"

DEFAULT_CONFIG_PATH = LOCAL_DIR / "config.json"
if not DEFAULT_CONFIG_PATH.exists():
    DEFAULT_CONFIG_PATH = APP_DIR / "app" / "config.example.json"

LOG_DIR = APP_DIR / "logs"
LOG_PATH = LOG_DIR / "dashboard.log"

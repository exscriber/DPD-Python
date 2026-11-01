# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import json
from functools import reduce
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import yaml

MAX_FLOAT_SENTINEL = 1.0e300

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

CONFIG_FILE = APP_DIR / "config.default.yaml"

LOG_DIR = APP_DIR.parent / "logs"
LOG_FILE = LOG_DIR / "dashboard.log"


class Config:
    T = TypeVar('T')
    _data: ClassVar[dict | list] = None

    @classmethod
    def load(cls, file_path: Path):
        """Loads config file: `.json` and `.yaml` formats supported."""

        with file_path.open("r", encoding="utf-8") as file:
            file_type = file_path.suffix.lower()
            if file_type == '.json':
                data = json.load(file)
            elif file_type == '.yaml':
                data = yaml.safe_load(file)
            else:
                raise ValueError(f"Unsupported config file format: '{file_type}'")

        if "designer" not in data or "timelines" not in data:
            raise ValueError("config must contain 'designer' and 'timelines'")

        for role in ("main", "aux"):
            if role not in data["timelines"]:
                raise ValueError(f"config is missing timelines: {role}")

        cls._data = data
        return data

    @classmethod
    def value(cls, path: str, default: T = None) -> T:
        """Gets value from nested data tree by path: `timelines.main.transport` for example."""
        try:
            return reduce(
                lambda res, key: res[int(key)] if isinstance(res, list) else res[key],
                path.split("."),
                cls._data,
            )
        except KeyError, IndexError, TypeError, ValueError:
            return default


load = Config.load
value = Config.value

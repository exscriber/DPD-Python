# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("disguise-playback-dashboard")
except PackageNotFoundError:
    __version__ = "0.0.0"

APP_VERSION = __version__

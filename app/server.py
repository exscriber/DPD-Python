# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import argparse
import errno
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app import APP_VERSION, cfg
from app.logger import LOGGER, setup_logging

from .DashboardHTTPServer import DashboardHTTPServer
from .DashboardRequestHandler import DashboardRequestHandler
from .DashboardStore import DashboardStore
from .DemoClient import DemoClient
from .DisguiseLiveClient import DisguiseLiveClient


def local_url(config: dict) -> str:
    host = cfg.value('server.bind', '0.0.0.0')
    port = cfg.value('server.port', 8080)
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{display_host}:{port}/"


def parse_arguments():
    parser = argparse.ArgumentParser(description="Disguise playback dashboard")
    parser.add_argument("--config", default=str(cfg.CONFIG_FILE), help="Path to config.yaml")
    parser.add_argument("--demo", action="store_true", help="Run with generated data and no Designer connection")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    config_path = Path(args.config).resolve()
    try:
        config_data = cfg.load(config_path)
    except Exception as e:
        print(f"Cannot load {config_path}: {e}", file=sys.stderr)
        return os.EX_CONFIG

    setup_logging(config_data)
    LOGGER.info("Starting Disguise Playback Dashboard %s", APP_VERSION)
    LOGGER.info("Configuration: %s", config_path)
    stop_event = threading.Event()
    store = DashboardStore(config_data)
    client = DemoClient(config_data, store, stop_event) if args.demo else DisguiseLiveClient(config_data, store, stop_event)

    bind = cfg.value('server.bind', '0.0.0.0')
    port = cfg.value('server.port', 8080)
    try:
        bind_deadline = time.monotonic() + 10
        while True:
            try:
                httpd = DashboardHTTPServer((bind, port), DashboardRequestHandler, store, config_data)
                break
            except OSError as e:
                if (sys.platform != "win32" or time.monotonic() >= bind_deadline
                        or (e.errno not in (errno.EADDRINUSE, errno.EACCES)
                            and getattr(e, "winerror", None) not in (10048, 10013))):
                    raise
                time.sleep(.2)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        LOGGER.error("Cannot start local web server on %s:%d: %s", bind, port, e)
        stop_event.set()
        return 3

    client.start()
    url = local_url(config_data)
    LOGGER.info("Dashboard: %s", url)
    LOGGER.info("Log file: %s", cfg.LOG_FILE)
    print(f"\nDashboard: {url}")
    print("Press Ctrl+C to stop.\n")

    browser_failed = threading.Event()
    browser_timer = None
    if args.open:
        def open_dashboard():
            try:
                webbrowser.open(url)
            except OSError as e:
                LOGGER.error("Cannot open dashboard window: %s", e)
                browser_failed.set()
                httpd.shutdown()
        browser_timer = threading.Timer(0.8, open_dashboard)
        browser_timer.start()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOGGER.info("Shutdown requested")
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        stop_event.set()
        httpd.server_close()
        client.join(timeout=3.0)
        LOGGER.info("Dashboard stopped")
    return 4 if browser_failed.is_set() else 0


def demo_main():
    if "--demo" not in sys.argv:
        sys.argv.append("--demo")
    if "--open" not in sys.argv:
        sys.argv.append("--open")
    return main()


if __name__ == "__main__":
    sys.exit(main())

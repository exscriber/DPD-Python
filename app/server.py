# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import argparse
import errno
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

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


def windows_listeners(port: int) -> list:
    script = (
        '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '
        '$items=@(Get-NetTCPConnection -State Listen -LocalPort ' + str(int(port)) +
        ' -ErrorAction SilentlyContinue | ForEach-Object { '
        '$entry=$_; $proc=Get-CimInstance Win32_Process '
        "-Filter ('ProcessId=' + $entry.OwningProcess) -ErrorAction Stop; "
        '[pscustomobject]@{OwningProcess=$entry.OwningProcess; '
        'LocalAddress=$entry.LocalAddress; Name=$proc.Name; CommandLine=$proc.CommandLine} }); '
        'ConvertTo-Json -InputObject $items -Compress'
    )
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                             '-Command', script], check=True, capture_output=True,
                            text=True, encoding='utf-8-sig', timeout=15)
    data = json.loads(result.stdout or '[]')
    if isinstance(data, dict):
        data = [data]
    return [{'pid': int(item['OwningProcess']), 'address': item['LocalAddress'],
             'name': item.get('Name') or 'unknown process',
             'commandLine': item.get('CommandLine') or ''} for item in data]


def stop_windows_process(pid: int):
    if pid <= 0 or pid == os.getpid():
        raise OSError('Cannot stop an invalid or current process')
    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], check=True,
                   capture_output=True, text=True, timeout=10)


def replace_windows_instance(bind: str, port: int):
    host = '127.0.0.1' if bind in ('0.0.0.0', '::') else bind
    address = socket.gethostbyname(host)

    with socket.socket() as local_check:
        try:
            local_check.bind((address, 0))
        except OSError as exc:
            raise OSError('server.bind must be an address of this computer (or 0.0.0.0); '
                          'put the Director address in designer.host.') from exc
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex((address, port)) != 0:
            return
    stopped = set()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(.2)
            if probe.connect_ex((address, port)) != 0:
                return len(stopped)
        owners = windows_listeners(port)
        owners = [owner for owner in owners
                  if owner['address'] in (address, '0.0.0.0', '::', '::ffff:' + address)]
        by_pid = {owner['pid']: owner for owner in owners}
        if len(by_pid) != 1:
            raise OSError(f'Cannot identify one local owner of {host}:{port}; no process was stopped.')
        owner = next(iter(by_pid.values()))
        if owner['pid'] in stopped:
            time.sleep(.2)
            continue
        identified = False
        try:

            endpoint = f'http://{address}:{port}/api/state'
            with build_opener(ProxyHandler({})).open(endpoint, timeout=2) as response:
                data = json.loads(response.read(1024 * 1024))
                identified = (response.geturl() == endpoint
                              and response.headers.get('Server', '').startswith('DisguisePlaybackDashboard/')
                              and isinstance(data, dict) and isinstance(data.get('appVersion'), str)
                              and isinstance(data.get('main'), dict) and isinstance(data.get('aux'), dict))
        except (OSError, ValueError):
            pass
        if not identified:
            raise OSError(f"Port {port} is used by {owner['name']} (PID {owner['pid']}), "
                          'not a verified DPD instance. It was left running. '
                          'Choose an unused server.port in config.json.')
        LOGGER.info('Replacing previous DPD: PID %s on %s:%s', owner['pid'], host, port)
        stop_windows_process(owner['pid'])
        stopped.add(owner['pid'])
        if len(stopped) >= 8:
            raise OSError(f'Too many DPD restarts on port {port}; check for an external auto-restart service.')
        time.sleep(.1)
    raise OSError(f'Previous DPD did not release port {port}; new instance was not started.')


def main(browser_opener=None) -> int:
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
            except OSError as exc:

                if (sys.platform != "win32" or time.monotonic() >= bind_deadline
                        or (exc.errno not in (errno.EADDRINUSE, errno.EACCES)
                            and getattr(exc, "winerror", None) not in (10048, 10013))):
                    raise
                time.sleep(.2)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        LOGGER.error("Cannot start local web server on %s:%d: %s", bind, port, exc)
        stop_event.set()
        return 3

    client.start()
    url = local_url(config_data)
    LOGGER.info("Dashboard: %s", url)
    LOGGER.info("Log file: %s", cfg.LOG_FILE)
    print("\nDashboard: %s" % url)
    print("Press Ctrl+C to stop.\n")

    browser_failed = threading.Event()
    browser_timer = None
    if args.open or browser_opener is not None:
        def open_dashboard():
            try:
                (browser_opener or webbrowser.open)(url)
            except OSError as exc:
                LOGGER.error("Cannot open dashboard window: %s", exc)
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
    raise SystemExit(main())

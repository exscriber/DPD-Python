# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import ipaddress
import json
from http.server import SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from app import APP_VERSION

from .cfg import WEB_DIR
from .logger import LOGGER, open_log_folder, read_log_tail


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    server_version = f"DisguisePlaybackDashboard/{APP_VERSION}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        LOGGER.debug("HTTP %s - %s", self.address_string(), format % args)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type="text/plain; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            snapshot = self.server.store.snapshot()
            self.server.store.log_snapshot_summary(snapshot)
            self._send_json(snapshot)
            return
        if parsed.path == "/api/health":
            snapshot = self.server.store.snapshot()
            code = 200 if snapshot["status"]["connected"] else 503
            self._send_json(snapshot["status"], status=code)
            return
        if parsed.path == "/api/log-tail":
            query = parse_qs(parsed.query)
            try:
                lines = int((query.get("lines") or ["200"])[0])
            except ValueError:
                lines = 200
            self._send_text(read_log_tail(lines))
            return
        if parsed.path == "/api/config":
            safe = {
                "designer": self.server.config_data.get("designer", {}),
                "timelines": self.server.config_data.get("timelines", {}),
                "updates": self.server.config_data.get("updates", {}),
                "logic": self.server.config_data.get("logic", {}),
                "ui": self.server.config_data.get("ui", {}),
            }
            self._send_json(safe)
            return
        if parsed.path in ("", "/"):
            self.path = "/dashboard.html"
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/open-log-folder":
            origin = urlparse(self.headers.get("Origin", ""))
            host = urlparse("http://" + self.headers.get("Host", ""))
            try:
                local = (ipaddress.ip_address(self.client_address[0]).is_loopback
                         and host.hostname in ("localhost", "127.0.0.1", "::1")
                         and (host.port or 80) == self.server.server_port
                         and origin.scheme == "http" and origin.netloc == host.netloc
                         and self.headers.get("X-DPD-Action") == "open-log-folder")
            except ValueError:
                local = False
            if not local:
                self._send_json({"error": "Open DPD on the bridge computer to open its log folder."}, status=403)
                return
            try:
                open_log_folder()
                self._send_json({"ok": True})
            except OSError:
                LOGGER.exception("Cannot open log folder")
                self._send_json({"error": "Could not open the log folder. Open .private/logs manually."}, status=500)
            return
        if parsed.path != "/api/client-log":
            self._send_json({"error": "Not found"}, status=404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16_384)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            level = str(payload.get("level", "ERROR")).upper()
            message = str(payload.get("message", "Browser event"))[:4000]
            context = payload.get("context")
            if context is not None:
                message += " | context=" + json.dumps(context, ensure_ascii=False)[:4000]
            log_method = {
                "DEBUG": LOGGER.debug,
                "INFO": LOGGER.info,
                "WARNING": LOGGER.warning,
                "ERROR": LOGGER.error,
            }.get(level, LOGGER.error)
            log_method("Browser: %s", message)
            self._send_json({"ok": True})
        except Exception as exc:
            LOGGER.warning("Invalid browser log request: %s", exc)
            self._send_json({"error": "Invalid request"}, status=400)

    def end_headers(self):
        if urlparse(self.path).path.endswith((".html", ".css", ".js")):
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()
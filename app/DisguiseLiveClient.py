# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as websocket_connect

from app import APP_VERSION

from .DashboardStore import DashboardStore
from .logger import LOGGER
from .props import (
    ACTIVE_BRIGHTNESS_PROPERTY,
    ACTIVE_GRAPHICAL_PROPERTY,
    ACTIVE_STANDARD_VIDEO_DETAILS_PROPERTY,
    ACTIVE_STANDARD_VIDEO_ELAPSED_PROPERTY,
    ACTIVE_STANDARD_VIDEO_FRAMES_PROPERTY,
    ACTIVE_STANDARD_VIDEO_REMAINING_PROPERTY,
    ACTIVE_VARIABLE_VIDEO_DETAILS_PROPERTY,
    ACTIVE_VARIABLE_VIDEO_ELAPSED_PROPERTY,
    ACTIVE_VARIABLE_VIDEO_FRAMES_PROPERTY,
    ACTIVE_VARIABLE_VIDEO_REMAINING_PROPERTY,
    DIRECTOR_FPS_OBJECT,
    DIRECTOR_FPS_RAW_PROPERTY,
    GRAPHICAL_PREVIEW_BRIGHTNESS_PROPERTY,
    GRAPHICAL_SCHEDULE_PROPERTY,
    MEDIA_AUDIO_PROPERTY,
    MEDIA_FILES_PROPERTY,
    MEDIA_METADATA_PROPERTY,
    MEDIA_SCHEDULE_PROPERTY,
    MEDIA_VERSIONS_PROPERTY,
    OUTPUT_STATE_PROPERTY,
    PREVIEW_BRIGHTNESS_PROPERTY,
    SECTIONS_PROPERTY,
    TRANSPORT_STATE_PROPERTY,
)
from .utils import director_fps_property, split_property_errors, transport_expression


class DisguiseLiveClient(threading.Thread):
    
    def __init__(self, config: dict, store: DashboardStore, stop_event: threading.Event):
        super().__init__(name="DesignerLiveUpdate", daemon=True)
        self.config = config
        self.store = store
        self.stop_event = stop_event
        self.subscription_specs = []
        self.id_to_key = {}
        self.pending_values = {}
        self.logged_stream_states = set()
        self.last_annotations_track = {"main": None, "aux": None}
        self.last_annotations_refresh = {"main": 0.0, "aux": 0.0}
        self.annotations_lock = threading.Lock()
        self.director_host = ""
        self.port = int(config["designer"].get("port", 80))
        self._build_subscriptions()

    def _build_subscriptions(self):
        self.subscription_specs = [
            {
                "key": "output_state",
                "object": "domain:MappedMedia",
                "property": OUTPUT_STATE_PROPERTY,
                "frequency": int(self.config.get("updates", {}).get("outputs_ms", 100)),
            },
            {
                "key": "media_schedule",
                "object": "domain:MappedMedia",
                "property": MEDIA_SCHEDULE_PROPERTY,
                "frequency": int(self.config.get("updates", {}).get("schedule_ms", 1000)),
            }
        ]
        for role in ("main", "aux"):
            obj = transport_expression(self.config["timelines"][role])
            self.subscription_specs.extend(
                [
                    {
                        "key": f"{role}.transport",
                        "object": obj,
                        "property": TRANSPORT_STATE_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("transport_ms", 100)),
                    },
                    {
                        "key": f"{role}.sections",
                        "object": obj,
                        "property": SECTIONS_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("sections_ms", 1000)),
                    },
                    {
                        "key": f"{role}.active_content",
                        "object": obj,
                        "property": ACTIVE_GRAPHICAL_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("active_layers_ms", 100)),
                    },
                    {
                        "key": f"{role}.active_brightness",
                        "object": obj,
                        "property": ACTIVE_BRIGHTNESS_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("brightness_ms", 100)),
                    },
                    {
                        "key": f"{role}.active_standard_video_details",
                        "object": obj,
                        "property": ACTIVE_STANDARD_VIDEO_DETAILS_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_details_ms", 500)),
                    },
                    {
                        "key": f"{role}.active_standard_video_frames",
                        "object": obj,
                        "property": ACTIVE_STANDARD_VIDEO_FRAMES_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                    {
                        "key": f"{role}.active_standard_video_elapsed",
                        "object": obj,
                        "property": ACTIVE_STANDARD_VIDEO_ELAPSED_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                    {
                        "key": f"{role}.active_standard_video_remaining",
                        "object": obj,
                        "property": ACTIVE_STANDARD_VIDEO_REMAINING_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                    {
                        "key": f"{role}.active_variable_video_details",
                        "object": obj,
                        "property": ACTIVE_VARIABLE_VIDEO_DETAILS_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_details_ms", 500)),
                    },
                    {
                        "key": f"{role}.active_variable_video_frames",
                        "object": obj,
                        "property": ACTIVE_VARIABLE_VIDEO_FRAMES_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                    {
                        "key": f"{role}.active_variable_video_elapsed",
                        "object": obj,
                        "property": ACTIVE_VARIABLE_VIDEO_ELAPSED_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                    {
                        "key": f"{role}.active_variable_video_remaining",
                        "object": obj,
                        "property": ACTIVE_VARIABLE_VIDEO_REMAINING_PROPERTY,
                        "frequency": int(self.config.get("updates", {}).get("video_timing_ms", 250)),
                    },
                ]
            )


        self.subscription_specs.append({
            "key": "director_fps", "object": DIRECTOR_FPS_OBJECT,
            "property": director_fps_property(self.config),
            "frequency": int(self.config.get("updates", {}).get("fps_ms", 1000)),
        })
        self.subscription_specs.extend([
            {"key": "director_fps_raw", "object": DIRECTOR_FPS_OBJECT,
             "property": DIRECTOR_FPS_RAW_PROPERTY,
             "frequency": int(self.config.get("updates", {}).get("fps_ms", 1000))},
            {"key": "media_metadata", "object": "domain:MappedMedia",
             "property": MEDIA_METADATA_PROPERTY,
             "frequency": int(self.config.get("updates", {}).get("media_metadata_ms", 1000))},
            {"key": "media_audio", "object": "domain:MappedMedia",
             "property": MEDIA_AUDIO_PROPERTY,
             "frequency": int(self.config.get("updates", {}).get("media_audio_ms", 1000))},
            {"key": "media_versions", "object": "domain:MappedMedia",
             "property": MEDIA_VERSIONS_PROPERTY, "frequency": 1000},
            {"key": "media_files", "object": "domain:MappedMedia",
             "property": MEDIA_FILES_PROPERTY, "frequency": 1000},
            {"key": "preview_brightness", "object": "domain:MappedMedia",
             "property": PREVIEW_BRIGHTNESS_PROPERTY,
             "frequency": int(self.config.get("updates", {}).get("preview_brightness_ms", 1000))},
        ])

        for role in ("main", "aux"):
            obj = transport_expression(self.config["timelines"][role])
            self.subscription_specs.extend([
                {"key": role + ".graphical_schedule", "object": obj,
                 "property": GRAPHICAL_SCHEDULE_PROPERTY,
                 "frequency": int(self.config.get("updates", {}).get("layers_ms", 1000))},
                {"key": role + ".graphical_preview_brightness", "object": obj,
                 "property": GRAPHICAL_PREVIEW_BRIGHTNESS_PROPERTY,
                 "frequency": int(self.config.get("updates", {}).get("preview_brightness_ms", 1000))},
            ])

    def _http_json(self, host: str, endpoint: str, timeout: float = 4.0):
        url = f"http://{host}:{self.port}{endpoint}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"DisguisePlaybackDashboard/{APP_VERSION}",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload)
        status = data.get("status") if isinstance(data, dict) else None
        if isinstance(status, dict):
            try:
                code = int(status.get("code", 0))
            except (TypeError, ValueError):
                code = -1
            if code != 0:
                raise RuntimeError(
                    f"Designer API {endpoint} returned code {code}: {status.get("message") or "Unknown API error"}"
                )
        return data

    def _host_reachable(self, host: str) -> bool:
        try:
            data = self._http_json(host, "/api/session/status/project", timeout=2.0)
            return int((data.get("status") or {}).get("code", 0)) == 0
        except Exception:
            return False

    def _refresh_project_info(self):
        try:
            response = self._http_json(
                self.director_host, "/api/session/status/project", timeout=3.0
            )
            self.store.update_project(response.get("result") or {})
            LOGGER.info(
                "Project: %s",
                (self.store.project or {}).get("name") or "unknown",
            )
        except Exception as e:
            LOGGER.warning("Couldn't load project information: %s", e)

    def _resolve_director(self) -> str:
        settings = self.config["designer"]
        seed = str(settings.get("host", "127.0.0.1")).strip()
        self.director_name = seed
        override = str(settings.get("director_host_override", "")).strip()
        if override or not settings.get("auto_discover_director", True):
            endpoint = override or seed
            self.director_name = endpoint

            try:
                data = self._http_json(endpoint, "/api/session/status/session")
                director = (data.get("result") or {}).get("director") or {}
                self.director_name = str(director.get("hostname") or endpoint).strip()
            except (OSError, ValueError):
                LOGGER.debug("Director identity unavailable for %s", endpoint)
            LOGGER.info("Using configured Director endpoint: %s", endpoint)
            return endpoint

        LOGGER.info("Querying session status via %s:%d", seed, self.port)
        data = self._http_json(seed, "/api/session/status/session")
        result = data.get("result") or {}
        director = result.get("director") or {}
        hostname = str(director.get("hostname") or "").strip()
        self.director_name = hostname or seed
        if result.get("isRunningSolo", False):
            LOGGER.info("Designer reports Solo mode; using %s", seed)
            return seed
        if not hostname:
            LOGGER.warning("Session status has no Director hostname; using configured host %s", seed)
            return seed
        if self._host_reachable(hostname):
            LOGGER.info("Discovered Director: %s", hostname)
            return hostname
        if self._host_reachable(seed):
            LOGGER.warning(
                f"Director hostname '{hostname}' isn't reachable from this PC; using configured host '{seed}'. "
                "If the configured host is an Actor, set director_host_override to the Director IP.",
            )
            return seed
        return hostname

    def _subscribe(self, websocket):
        self.id_to_key.clear()
        self.pending_values.clear()
        self.logged_stream_states.clear()
        for spec in self.subscription_specs:
            message = {
                "subscribe": {
                    "object": spec["object"],
                    "properties": [spec["property"]],
                    "configuration": {"updateFrequencyMs": spec["frequency"]},
                }
            }
            LOGGER.debug(f"Subscribe {spec["key"]}: {spec["object"]} / {spec["property"]}")
            websocket.send(json.dumps(message, separators=(",", ":")))

            deadline = time.monotonic() + float(
                self.config.get("designer", {}).get("subscribe_timeout_seconds", 2.0)
            )
            while time.monotonic() < deadline:
                try:
                    raw = websocket.recv(timeout=max(0.05, deadline - time.monotonic()))
                except TimeoutError:
                    LOGGER.warning(f"No subscription acknowledgement for {spec["key"]}")
                    break
                if raw is None:
                    raise ConnectionError("Live Update socket returned no data")
                response = self._handle_message(raw, subscription_context=spec["key"])
                if "error" in response or spec["key"] in self.id_to_key.values():
                    break

    def _handle_subscriptions(self, subscriptions: list):
        by_pair = {(spec["object"], spec["property"]): spec["key"] for spec in self.subscription_specs}
        by_property = {}
        for spec in self.subscription_specs:
            by_property.setdefault(spec["property"], []).append(spec["key"])

        for item in subscriptions:
            if not isinstance(item, dict):
                continue
            subscription_id = item.get("id")
            pair = (item.get("objectPath"), item.get("propertyPath"))
            key = by_pair.get(pair)
            if not key:
                candidates = by_property.get(item.get("propertyPath"), [])
                if len(candidates) == 1:
                    key = candidates[0]
            if key is not None and isinstance(subscription_id, int):
                self.id_to_key[subscription_id] = key
                if subscription_id in self.pending_values:
                    self._store_stream_value(
                        key, self.pending_values.pop(subscription_id)
                    )
        LOGGER.debug(f"Mapped {len(self.id_to_key)} Live Update subscription IDs")

    def _store_stream_value(self, key: str, value):
        clean_value, property_errors = split_property_errors(value)
        if property_errors:
            message = " | ".join(dict.fromkeys(property_errors))

            if clean_value not in (None, [], {}):
                self.store.update_value(key, clean_value)
            self.store.set_stream_error(key, message)
            marker = (key, "property-error", message)
            if marker not in self.logged_stream_states:
                self.logged_stream_states.add(marker)
                LOGGER.error(f"Live stream {key} property error:{message}")
            if clean_value in (None, [], {}):
                return
            value = clean_value
        else:
            self.store.update_value(key, value)

        if isinstance(value, list):
            state = "nonempty" if value else "empty"
            count = len(value)
            sample = value[0] if value else None
        elif isinstance(value, dict):
            state = "nonempty" if value else "empty"
            count = len(value)
            sample = value
        else:
            state = "value"
            count = 1
            sample = value
        marker = (key, state)

        if marker in self.logged_stream_states:
            return
        self.logged_stream_states.add(marker)
        sample_text = json.dumps(
            sample, ensure_ascii=False, separators=(",", ":")
        )[:1600]
        LOGGER.info(f"Live stream {key}: {count} item(s); sample={sample_text}")

    def _handle_values(self, values: list):
        for item in values:
            if not isinstance(item, dict):
                continue
            subscription_id = item.get("id")
            key = self.id_to_key.get(subscription_id)
            if key:
                self._store_stream_value(key, item.get("value"))
            elif isinstance(subscription_id, int):
                self.pending_values[subscription_id] = item.get("value")

    def _refresh_annotations_if_needed(self, force=False):
        label_sources = {
            str(value).lower()
            for value in self.config.get("logic", {}).get(
                "section_label_priority", ["number"]
            )
        }
        cards_enabled = self.config.get("logic", {}).get("section_metadata_enabled", True)
        if not cards_enabled and not label_sources.intersection({"note", "cue", "index"}):
            return
        if not self.director_host or not self.store.connected or self.stop_event.is_set():
            return
        if not self.annotations_lock.acquire(blocking=False):
            return
        identities = self.store.track_identities()
        now = time.monotonic()
        interval = max(1.0, float(self.config.get("updates", {}).get("annotations_seconds", 10.0)))
        due = any(
            identity["name"] and identity["name"] != "NOT CONNECTED"
            and (force or identity != self.last_annotations_track[role]
                 or now - self.last_annotations_refresh[role] >= interval)
            for role, identity in identities.items()
        )
        if not due:
            self.annotations_lock.release()
            return
        threading.Thread(
            target=self._refresh_annotations, args=(force,),
            name="SectionMetadata", daemon=True,
        ).start()

    def _refresh_annotations(self, force=False):
        try:
            self._load_annotations(force)
        finally:
            self.annotations_lock.release()

    def _load_annotations(self, force=False):
        identities = self.store.track_identities()
        host = self.director_host
        now = time.monotonic()
        interval = max(1.0, float(self.config.get("updates", {}).get("annotations_seconds", 10.0)))
        for role in ("main", "aux"):
            identity = identities[role]
            track_name = identity["name"]
            if not track_name or track_name == "NOT CONNECTED":
                continue
            changed = identity != self.last_annotations_track[role]
            expired = now - self.last_annotations_refresh[role] >= interval
            if not force and not changed and not expired:
                continue
            locator = "uid=%s" % quote(identity["uid"], safe="") if identity["uid"] else "name=%s" % quote(track_name, safe="")
            endpoint = "/api/session/transport/annotations?%s" % locator

            self.last_annotations_track[role] = identity
            self.last_annotations_refresh[role] = now
            try:
                response = self._http_json(host, endpoint)
                result = response.get("result") or {}
                if (host != self.director_host or self.stop_event.is_set()
                        or not self.store.update_annotations(role, result, expected_track=identity)):
                    continue
                LOGGER.debug(f"Annotations refreshed for {role} track '{track_name}'")
            except Exception as e:
                if host == self.director_host and self.store.track_identities()[role] == identity:
                    self.store.set_stream_error(f"{role}.annotations", str(e))
                LOGGER.warning(f"Couldn't load annotations for track '{track_name}': {e}")

    def _handle_message(self, raw, subscription_context: str = ""):
        LOGGER.debug("Live Update RX: %s", raw[:5000])
        data = json.loads(raw)
        if "error" in data:
            message = str(data.get("error"))
            contextual = f"{subscription_context}: {message}" if subscription_context else message
            self.store.set_error(contextual)
            if subscription_context:
                self.store.set_stream_error(subscription_context, message)
            LOGGER.error("Designer Live Update error %s: %s", " for %s" % subscription_context if subscription_context else "", message)
        if isinstance(data.get("subscriptions"), list):
            self._handle_subscriptions(data["subscriptions"])
        if isinstance(data.get("valuesChanged"), list):
            self._handle_values(data["valuesChanged"])
            self._refresh_annotations_if_needed()
        return data

    def run(self):
        if websocket_connect is None:
            message = "Missing dependency 'websockets': Run uv sync"
            LOGGER.error(message)
            self.store.set_connection(False, "dependency-error", error=message)
            return

        initial_delay = float(self.config.get("designer", {}).get("reconnect_initial_seconds", 1.0))
        max_delay = float(self.config.get("designer", {}).get("reconnect_max_seconds", 15.0))
        delay = initial_delay
        while not self.stop_event.is_set():
            self.store.connection_attempt += 1
            try:
                self.store.set_connection(False, "connecting")
                self.director_host = self._resolve_director()
                self._refresh_project_info()
                ws_url = f"ws://{self.director_host}:{self.port}/api/session/liveupdate"
                LOGGER.info("Connecting to %s", ws_url)
                with websocket_connect(
                    ws_url,
                    open_timeout=float(self.config["designer"].get("connect_timeout_seconds", 5.0)),
                    close_timeout=2.0,
                    max_size=16 * 1024 * 1024,
                ) as websocket:
                    self.store.set_connection(True, "live", self.director_host)
                    remote = getattr(websocket, "remote_address", None)
                    self.store.set_director_identity(self.director_name, remote[0] if remote else "")
                    LOGGER.info("Live Update connected")
                    self._subscribe(websocket)
                    delay = initial_delay

                    while not self.stop_event.is_set():
                        try:
                            message = websocket.recv(timeout=1.0)
                        except TimeoutError:
                            self.store.touch()
                            self._refresh_annotations_if_needed()
                            continue
                        if message is None:
                            raise ConnectionError("Live Update socket returned no data")
                        self._handle_message(message)
            except (ConnectionClosed, ConnectionError, OSError, HTTPError, URLError, socket.gaierror) as e:
                message = f"{type(e).__name__}: {e}"
                self.store.set_connection(False, "reconnecting", self.director_host, message)
                LOGGER.warning(f"Designer connection lost: {message}; retrying in {delay}")
            except Exception as e:
                message = f"{type(e).__name__}: {e}"
                self.store.set_connection(False, "error", self.director_host, message)
                LOGGER.exception(f"Unexpected Designer client error: {message}")

            if self.stop_event.wait(delay):
                break
            delay = min(max_delay, max(initial_delay, delay * 1.8))

        self.store.set_connection(False, "stopped", self.director_host)
        LOGGER.info("Live Update client stopped")
# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import copy
import ipaddress
import json
import math
import re
import threading
import time

from app import APP_VERSION, cfg

from .cfg import LOG_FILE, MAX_FLOAT_SENTINEL
from .logger import LOGGER
from .props import (
    DIRECTOR_FPS_OBJECT,
    DIRECTOR_FPS_RAW_PROPERTY,
    OUTPUT_STATE_PROPERTY,
    OUTPUT_STATES,
)
from .utils import (
    as_float,
    clean_media_name,
    director_fps_property,
    end_point_label,
    fps_average_samples,
    is_still_media_name,
    module_label,
    resource_name,
    resource_type,
    resource_uid,
    uid_key,
    uid_text,
)


class DashboardStore:
    def __init__(self, config: dict):
        self.config = config
        self.lock = threading.RLock()
        self.connected = False
        self.connection_mode = "starting"
        self.seed_host = str(config["designer"].get("host", "127.0.0.1"))
        self.director_host = ""
        self.director_name = ""
        self.director_ip = ""
        self.last_update_monotonic = 0.0
        self.last_update_wall = None
        self.last_error = ""
        self.last_error_wall = None
        self.connection_attempt = 0
        self.stream_status = {}
        self.stream_errors = {}
        self.output_code = None
        self.director_fps = None
        self.director_fps_raw = None
        self.fps_raw_stats = {"count": 0, "min": None, "max": None}
        self.media_metadata_by_uid = {}
        self.media_audio_by_uid = {}
        self.media_versions_by_uid = {}
        self.media_files_by_uid = {}
        self.preview_brightness = []
        self.last_snapshot_log_monotonic = 0.0
        self.active_history = {role: {"context": None, "rows": None,
                                     "previous": None, "sequence": 0}
                               for role in ("main", "aux")}
        self.project = {
            "name": str(config.get("ui", {}).get("project_name") or "").strip(),
            "path": "",
            "version": {},
        }
        self.media_schedule = []
        self.roles = {
            "main": {
                "transport": {},
                "sections": [],
                "graphical_schedule": {},
                "graphical_preview_brightness": {},
                "content_layers": [],
                "active_content": [],
                "active_membership": {},
                "active_brightness": [],
                "active_standard_video_details": [],
                "active_standard_video_frames": [],
                "active_standard_video_elapsed": [],
                "active_standard_video_remaining": [],
                "active_standard_video_tsec": [],
                "active_variable_video_details": [],
                "active_variable_video_frames": [],
                "active_variable_video_elapsed": [],
                "active_variable_video_remaining": [],
                "active_variable_video_tsec": [],
                "active": [],
                "annotations": {},
            },
            "aux": {
                "transport": {},
                "sections": [],
                "graphical_schedule": {},
                "graphical_preview_brightness": {},
                "content_layers": [],
                "active_content": [],
                "active_membership": {},
                "active_brightness": [],
                "active_standard_video_details": [],
                "active_standard_video_frames": [],
                "active_standard_video_elapsed": [],
                "active_standard_video_remaining": [],
                "active_standard_video_tsec": [],
                "active_variable_video_details": [],
                "active_variable_video_frames": [],
                "active_variable_video_elapsed": [],
                "active_variable_video_remaining": [],
                "active_variable_video_tsec": [],
                "active": [],
                "annotations": {},
            },
        }

    def set_connection(self, connected: bool, mode: str, host: str = "", error: str = ""):
        with self.lock:
            if not connected or not self.connected or (host and host != self.director_host):
                self.director_ip = ""
                self.director_name = ""
                for role in ("main", "aux"):
                    self.active_history[role]["rows"] = None
                    if host and self.director_host and host != self.director_host:
                        self.active_history[role]["previous"] = None
                    self.roles[role]["active_membership"] = {}
                    self.roles[role]["active_content"] = []
                    self.stream_status.pop(role + ".active_content", None)


                self.output_code = None
                self.director_fps = None
                self.director_fps_raw = None
                self.fps_raw_stats = {"count": 0, "min": None, "max": None}
                self.media_metadata_by_uid = {}
                self.media_audio_by_uid = {}
                self.media_versions_by_uid = {}
                self.media_files_by_uid = {}
                self.preview_brightness = []
                for role in ("main", "aux"):
                    for field in ("graphical_schedule", "graphical_preview_brightness"):
                        self.roles[role][field] = {}
                        self.stream_status.pop(role + "." + field, None)
                        if connected:
                            self.stream_errors.pop(role + "." + field, None)
                for key in ("output_state", "director_fps", "director_fps_raw", "media_metadata", "media_audio", "media_versions", "media_files", "preview_brightness"):
                    self.stream_status.pop(key, None)
                    if connected:
                        self.stream_errors.pop(key, None)
            self.connected = connected
            self.connection_mode = mode
            if host:
                self.director_host = host
            self.last_error = error
            self.last_error_wall = time.time() if error else None
            if connected:
                self.last_update_monotonic = time.monotonic()
                self.last_update_wall = time.time()

    def set_director_identity(self, name: str, address: str):
        with self.lock:
            self.director_name = str(name or self.director_host)
            try:
                self.director_ip = str(ipaddress.ip_address(address))
            except ValueError:
                self.director_ip = ""

    def touch(self):
        with self.lock:
            self.last_update_monotonic = time.monotonic()
            self.last_update_wall = time.time()

    def update_value(self, key: str, value):
        with self.lock:
            self.stream_errors.pop(key, None)
            if key == "output_state":

                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and value in OUTPUT_STATES)
                if not valid:
                    self.output_code = None
                    self.set_stream_error(key, f"Unknown LocalState.output value: {value!r}")
                    return
                code = int(value)
                if code != self.output_code:
                    LOGGER.info("Output state: %s (LocalState.directorState().output=%d)", OUTPUT_STATES[code][1], code)
                self.output_code = code
            elif key in ("director_fps", "director_fps_raw"):
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value >= 0)
                if not valid:
                    if key == "director_fps":
                        self.director_fps = None
                    else:
                        self.director_fps_raw = None
                    self.set_stream_error(key, f"Invalid Director FPS value: {value!r}")
                    return
                if key == "director_fps":
                    self.director_fps = float(value)
                else:
                    self.director_fps_raw = float(value)
                    stats = self.fps_raw_stats
                    stats["count"] += 1
                    stats["min"] = min(stats["min"], value) if stats["min"] is not None else value
                    stats["max"] = max(stats["max"], value) if stats["max"] is not None else value
            elif key == "media_metadata":
                self.media_metadata_by_uid = {
                    uid_key(item.get("mediaResource")): copy.deepcopy(item)
                    for item in (value if isinstance(value, list) else [])
                    if isinstance(item, dict) and uid_key(item.get("mediaResource"))
                }
            elif key == "media_audio":
                self.media_audio_by_uid = {
                    uid_key(item.get("mediaResource")): copy.deepcopy(item)
                    for item in (value if isinstance(value, list) else [])
                    if isinstance(item, dict) and uid_key(item.get("mediaResource"))
                }
            elif key in ("media_versions", "media_files"):
                samples = {
                    uid_key(item.get("mediaResource")): copy.deepcopy(item)
                    for item in (value if isinstance(value, list) else [])
                    if isinstance(item, dict) and uid_key(item.get("mediaResource"))
                }
                if key == "media_versions":
                    self.media_versions_by_uid = samples
                else:
                    self.media_files_by_uid = samples
            elif key == "preview_brightness":
                self.preview_brightness = [copy.deepcopy(item) for item in (value if isinstance(value, list) else [])
                                           if isinstance(item, dict)]
            elif key == "media_schedule":
                self.media_schedule = value if isinstance(value, list) else []
            else:
                role, field = key.split(".", 1)
                if field == "transport":
                    previous_track = self.roles[role][field].get("trackName")
                    previous_uid = self.roles[role][field].get("trackUid")
                    self.roles[role][field] = value if isinstance(value, dict) else {}
                    transport = self.roles[role][field]
                    if not transport.get("trackUid"):
                        transport["trackUid"] = resource_uid(transport.get("trackResource"))
                    if not transport.get("trackName"):
                        transport["trackName"] = resource_name(transport.get("trackResource"))
                    current_track = self.roles[role][field].get("trackName")
                    current_uid = transport.get("trackUid")
                    switched = (
                        uid_text(previous_uid) != uid_text(current_uid)
                        if previous_uid and current_uid
                        else bool(previous_track and current_track and current_track != previous_track)
                    )


                    if switched:
                        self.roles[role]["annotations"] = {}
                        self.stream_status.pop(f"{role}.annotations", None)
                        self.stream_errors.pop(f"{role}.annotations", None)
                        self.roles[role]["active_brightness"] = []
                        self.roles[role]["graphical_schedule"] = {}
                        self.roles[role]["graphical_preview_brightness"] = {}
                        self.roles[role]["content_layers"] = []
                        self.roles[role]["active_content"] = []
                        self.roles[role]["active_membership"] = {}
                        self.stream_status.pop(role + ".active_content", None)
                        self.active_history[role] = {"context": None, "rows": None,
                                                     "previous": None, "sequence": 0}
                        self.roles[role]["active_standard_video_details"] = []
                        self.roles[role]["active_standard_video_frames"] = []
                        self.roles[role]["active_standard_video_elapsed"] = []
                        self.roles[role]["active_standard_video_remaining"] = []
                        self.roles[role]["active_standard_video_tsec"] = []
                        self.roles[role]["active_variable_video_details"] = []
                        self.roles[role]["active_variable_video_frames"] = []
                        self.roles[role]["active_variable_video_elapsed"] = []
                        self.roles[role]["active_variable_video_remaining"] = []
                        self.roles[role]["active_variable_video_tsec"] = []
                elif field == "active_content":


                    if isinstance(value, dict) and isinstance(value.get("layers"), list):
                        if (as_float(value.get("beat")) is None or
                                as_float(value.get("trackSeconds")) is None or
                                any(not isinstance(row, dict) or row.get("errorType")
                                    for row in value["layers"])):
                            self.set_stream_error(key, "Incomplete atomic active membership packet")
                            return
                        self.roles[role]["active_membership"] = copy.deepcopy(value)
                        self.roles[role][field] = copy.deepcopy(value["layers"])
                    elif isinstance(value, list):
                        self.roles[role]["active_membership"] = {}
                        self.roles[role][field] = copy.deepcopy(value)
                    else:
                        self.set_stream_error(key, "Invalid active membership packet")
                        return
                elif field in ("graphical_schedule", "graphical_preview_brightness"):
                    self.roles[role][field] = copy.deepcopy(value) if isinstance(value, dict) else {}
                elif field in (
                    "sections",
                    "content_layers",
                    "active_brightness",
                    "active_standard_video_details",
                    "active_standard_video_frames",
                    "active_standard_video_elapsed",
                    "active_standard_video_remaining",
                    "active_standard_video_tsec",
                    "active_variable_video_details",
                    "active_variable_video_frames",
                    "active_variable_video_elapsed",
                    "active_variable_video_remaining",
                    "active_variable_video_tsec",
                    "active_video_details",
                    "active_video_tsec",
                    "active_video",
                    "video_layers",
                    "active_media",
                    "active_elapsed",
                    "active_remaining",
                    "active",
                ):
                    self.roles[role][field] = value if isinstance(value, list) else []
            self.stream_status[key] = {
                "lastUpdate": time.time(),
                "kind": type(value).__name__,
                "items": len(value) if isinstance(value, (list, dict)) else 1,
            }
            self.touch()

    def set_stream_error(self, key: str, message: str):

        with self.lock:
            now = time.time()
            clean = str(message or "Unknown property-path error")[:2000]
            self.stream_errors[key] = {"message": clean, "lastUpdate": now}
            self.stream_status[key] = {
                "lastUpdate": now,
                "kind": "error",
                "items": 0,
                "error": clean,
            }
            if not key.endswith(".annotations"):
                self.touch()

    def update_annotations(self, role: str, data: dict, expected_track=None):
        with self.lock:
            if expected_track is not None:
                current = self.track_identities()[role]
                if current != expected_track:
                    return False
            self.roles[role]["annotations"] = data if isinstance(data, dict) else {}
            values = self.roles[role]["annotations"]
            values = values.get("annotations") or values
            key = f"{role}.annotations"
            self.stream_errors.pop(key, None)
            self.stream_status[key] = {
                "lastUpdate": time.time(), "kind": "annotations",
                "items": sum(len(values.get(field) or []) for field in ("sections", "notes", "tags")),
            }

            return True

    def update_project(self, data: dict):
        with self.lock:
            result = data if isinstance(data, dict) else {}
            project_path = str(result.get("projectPath") or "").strip()
            configured_name = str(
                self.config.get("ui", {}).get("project_name") or ""
            ).strip()
            path_name = ""
            if project_path:
                normalised = project_path.replace("\\", "/").rstrip("/")
                path_name = normalised.rsplit("/", 1)[-1]
                for suffix in (".d3project", ".d3"):
                    if path_name.lower().endswith(suffix):
                        path_name = path_name[: -len(suffix)]
                        break
            self.project = {
                "name": configured_name or path_name or "DISGUISE PROJECT",
                "path": project_path,
                "version": copy.deepcopy(result.get("version") or {}),
            }
            self.touch()

    def set_error(self, message: str):
        with self.lock:
            self.last_error = message
            self.last_error_wall = time.time()

    def track_names(self) -> dict:

        with self.lock:
            return {
                role: str((self.roles[role].get("transport") or {}).get("trackName") or "")
                for role in ("main", "aux")
            }

    def track_identities(self) -> dict:
        with self.lock:
            return {
                role: {
                    "name": str((self.roles[role].get("transport") or {}).get("trackName") or ""),
                    "uid": uid_text((self.roles[role].get("transport") or {}).get("trackUid")),
                }
                for role in ("main", "aux")
            }

    def _section_metadata(self, role: str, role_data: dict, section: dict) -> dict:

        result = {"tc": None, "midi": None, "osc": None, "notes": None,
                  "metadataMatch": None}
        if not section:
            return result
        if f"{role}.annotations" in self.stream_errors:
            result["metadataMatch"] = "api-error"
            return result
        annotations = role_data.get("annotations") or {}
        annotations = annotations.get("annotations") or annotations
        api_sections = [item for item in annotations.get("sections", [])
                        if isinstance(item, dict) and as_float(item.get("time")) is not None]
        api_sections.sort(key=lambda item: as_float(item["time"]))
        track_sections = [item for item in role_data.get("sections", []) if isinstance(item, dict)]
        track_sections.sort(key=lambda item: int(item.get("index", 0)))
        if not api_sections:
            result["metadataMatch"] = "no-annotations"
            return result
        if len(api_sections) != len(track_sections):
            result["metadataMatch"] = "section-count-mismatch"
            return result
        if len({as_float(item["time"]) for item in api_sections}) != len(api_sections):
            result["metadataMatch"] = "duplicate-section-times"
            return result
        indices = [int(item.get("index", 0)) for item in track_sections]
        try:
            ordinal = indices.index(int(section.get("index", 0)))
        except (ValueError, TypeError):
            return result
        at_time = as_float(api_sections[ordinal]["time"])
        tolerance = max(0.0, cfg.value("logic.annotation_time_tolerance", 0.01))
        grouped = {"tc": [], "midi": [], "osc": [], "notes": []}


        tag_fields = {"TC": "tc", "MIDI": "midi", "CUE": "osc"}
        for item in annotations.get("tags", []):
            if not isinstance(item, dict):
                continue
            position = as_float(item.get("time"))
            field = tag_fields.get(str(item.get("type") or "").strip().upper())
            if field and position is not None and abs(position - at_time) <= tolerance:
                value = str(item.get("value") if item.get("value") is not None else "").strip()
                if value and value not in grouped[field]:
                    grouped[field].append(value)
        for item in annotations.get("notes", []):
            if not isinstance(item, dict):
                continue
            position = as_float(item.get("time"))
            if position is not None and abs(position - at_time) <= tolerance:
                value = str(item.get("text") or "").strip()
                if value and value not in grouped["notes"]:
                    grouped["notes"].append(value)
        result.update({key: " · ".join(values) if values else None for key, values in grouped.items()})
        result["metadataMatch"] = "section-ordinal"
        return result

    def _section_label(self, role_data: dict, section: dict) -> str:
        index = int(section.get("index", 0))
        annotations = role_data.get("annotations") or {}
        sections_api = list((annotations.get("annotations") or annotations).get("sections") or [])
        notes_api = list((annotations.get("annotations") or annotations).get("notes") or [])
        tags_api = list((annotations.get("annotations") or annotations).get("tags") or [])

        section_annotation = sections_api[index] if index < len(sections_api) else {}
        section_time = as_float(section_annotation.get("time"))

        note = ""
        cue = ""
        if section_time is not None:
            tolerance = cfg.value("logic.annotation_time_tolerance", 0.01)
            for item in notes_api:
                item_time = as_float(item.get("time"))
                if item_time is not None and abs(item_time - section_time) <= tolerance:
                    note = str(item.get("text") or "").strip()
                    if note:
                        break
            for item in tags_api:
                item_time = as_float(item.get("time"))
                tag_type = str(item.get("type") or "").upper()
                if item_time is not None and abs(item_time - section_time) <= tolerance and tag_type == "CUE":
                    cue = str(item.get("value") or "").strip()
                    if cue:
                        break

        api_index = str(section_annotation.get("index") or "").strip()
        values = {
            "note": note,
            "cue": cue,
            "index": (f"SECTION {api_index}") if api_index else "",
            "number": f"SECTION {index + 1:02d}",
        }
        priority = self.config.get("logic", {}).get(
            "section_label_priority", ["number"]
        )
        for source in priority:
            value = values.get(str(source), "")
            if value:
                return value
        return f"SECTION {index + 1:02d}"

    @staticmethod
    def _play_mode(transport: dict) -> str:
        if transport.get("loopSection"):
            return "Loop section"
        if transport.get("holding"):
            return "Holding"
        if transport.get("sectionPlayMode"):
            return "Play to end"
        if transport.get("playing"):
            return "Play"
        if transport.get("stopped"):
            return "Stopped"
        return "Idle"

    @staticmethod
    def _media_interval(item: dict):
        start = as_float(item.get("effectiveStartBeat"))
        end = as_float(item.get("effectiveEndBeat"))
        if start is None:
            layer_start = as_float(item.get("layerStartBeat"), 0.0)
            content_start = as_float(item.get("contentStartBeat"), layer_start)
            start = max(layer_start, content_start)
        if end is None:
            layer_end = as_float(item.get("layerEndBeat"), start)
            content_end = as_float(item.get("contentEndBeat"), layer_end)
            if content_end is None or content_end > MAX_FLOAT_SENTINEL:
                content_end = layer_end
            end = min(layer_end, content_end)
        return start, end

    @staticmethod
    def _normalise_layer(item: dict) -> dict:
        result = copy.deepcopy(item)
        layer_resource = result.get("layerResource") or result.get("layer")
        module_resource = result.get("moduleResource") or result.get("module")
        result["layerResource"] = layer_resource
        result["moduleResource"] = module_resource
        result["layerUid"] = uid_text(
            result.get("layerUid") or resource_uid(layer_resource)
        )
        result["layerName"] = str(result.get("layerName") or "").strip()
        result["moduleType"] = str(
            result.get("moduleType") or resource_type(module_resource) or ""
        ).strip()
        result["layerType"] = str(
            result.get("layerType") or module_label(result["moduleType"])
        ).strip()
        return result

    def _layer_catalog(self, role_data: dict):



        layers = []
        for source_name in (
            "content_layers",
            "video_layers",
            "active_content",
            "active_standard_video_details",
            "active_variable_video_details",
        ):
            for raw in role_data.get(source_name, []):
                if not isinstance(raw, dict):
                    continue
                item = self._normalise_layer(raw)
                if any(self._same_layer(item, existing) for existing in layers):
                    continue
                layers.append(item)
        by_uid = {
            uid_key(item.get("layerUid")): item
            for item in layers
            if uid_key(item.get("layerUid"))
        }
        by_name = {
            str(item.get("layerName") or "").casefold(): item
            for item in layers
            if str(item.get("layerName") or "").strip()
        }
        return layers, by_uid, by_name

    def _track_media(self, track_name: str, track_uid: str, role_data: dict) -> list:
        _, layers_by_uid, layers_by_name = self._layer_catalog(role_data)
        wanted_uid = uid_key(track_uid)
        wanted_name = str(track_name or "").strip().casefold()
        result = []

        for ordinal, raw in enumerate(self.media_schedule):
            if not isinstance(raw, dict):
                continue

            track_resource = raw.get("trackResource") or raw.get("track")
            raw_track_uid = raw.get("trackUid") or resource_uid(track_resource)
            raw_track_name = str(
                raw.get("trackName") or resource_name(track_resource) or ""
            ).strip()
            same_uid = bool(wanted_uid and uid_key(raw_track_uid) == wanted_uid)
            same_name = bool(
                wanted_name and raw_track_name.casefold() == wanted_name
            )
            if not same_uid and not same_name:
                continue

            item = copy.deepcopy(raw)
            item["_ordinal"] = ordinal
            item["trackResource"] = track_resource
            item["trackUid"] = uid_text(raw_track_uid)
            item["trackName"] = raw_track_name
            item["mediaResource"] = item.get("mediaResource") or item.get("resource")

            layer_resource = item.get("layerResource") or item.get("layer")
            layer_uid = item.get("layerUid") or resource_uid(layer_resource)
            layer_name = str(item.get("layerName") or "").strip()
            catalogue_item = layers_by_uid.get(uid_key(layer_uid))
            if catalogue_item is None and layer_name:
                catalogue_item = layers_by_name.get(layer_name.casefold())
            if catalogue_item:
                layer_uid = layer_uid or catalogue_item.get("layerUid")
                layer_name = layer_name or catalogue_item.get("layerName", "")
                item["moduleResource"] = copy.deepcopy(
                    catalogue_item.get("moduleResource")
                )
                item["moduleType"] = catalogue_item.get("moduleType")
                item["layerType"] = catalogue_item.get("layerType")

            item["layerResource"] = layer_resource
            item["layerUid"] = uid_text(layer_uid)
            item["layerName"] = layer_name or resource_name(layer_resource) or "—"
            item["moduleType"] = str(
                item.get("moduleType") or "VariableVideoModule"
            )
            item["layerType"] = str(
                item.get("layerType") or module_label(item["moduleType"])
            )
            item["mediaName"] = clean_media_name(item)
            start, end = self._media_interval(item)
            item["effectiveStartBeat"] = start
            item["effectiveEndBeat"] = end
            result.append(item)
        return result

    def _audio_metadata(self, item: dict) -> dict:
        uid = uid_key(item.get("mediaResource"))
        sample = self.media_audio_by_uid.get(uid, {}) if "media_audio" not in self.stream_errors else {}
        value = sample.get("hasAudio")
        if isinstance(value, bool):
            return {"hasAudio": value, "audioSource": "VideoClip.hasAudio"}
        return {"hasAudio": None, "audioSource": None}

    def _media_identity_metadata(self, item: dict) -> dict:
        result = {"mediaName": clean_media_name(item), "mediaVersion": None}
        uid = uid_key(item.get("mediaResource"))
        if "media_versions" in self.stream_errors:
            return result
        version = self.media_versions_by_uid.get(uid, {}).get("version")
        if (not isinstance(version, str) or len(version) > 32
                or not re.fullmatch(r"[0-9][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*", version)):
            return result
        result["mediaVersion"] = version if version != "0" else None
        sample = self.media_files_by_uid.get(uid, {}) if "media_files" not in self.stream_errors else {}
        if sample.get("version") == version:
            files = sample.get("files")
            names = {
                resource_name(file) for file in (files if isinstance(files, list) else [])
                if isinstance(file, dict) and isinstance(file.get("path"), str)
                and file["path"].strip() and not file["path"].lower().endswith(".apx")
            }


            if len(names) == 1:
                result["mediaName"] = names.pop()
        return result

    def _preview_brightness_metadata(self, item: dict) -> dict:
        start, _ = self._media_interval(item)
        result = {"brightness": None, "brightnessSource": None,
                  "brightnessSampleBeat": start, "brightnessReason": "no-preview-sample"}
        if "preview_brightness" in self.stream_errors:
            result["brightnessReason"] = "preview-stream-error"
            return result
        track_uid = uid_key(item.get("trackUid") or item.get("trackResource"))
        layer_uid = uid_key(item.get("layerUid") or item.get("layerResource"))
        media_uid = uid_key(item.get("mediaResource"))
        if not all((track_uid, layer_uid, media_uid)):
            return result
        for sample in self.preview_brightness:
            beat = as_float(sample.get("sampleBeat"))
            if (uid_key(sample.get("trackResource")) != track_uid or
                    uid_key(sample.get("layerResource")) != layer_uid or
                    uid_key(sample.get("mediaResource")) != media_uid or
                    beat is None or abs(beat - start) > 1e-6):
                continue
            controls = [sample.get(key) for key in ("expressionActive", "sockpuppetControlled", "externallyControlled")]
            if any(value is True for value in controls):
                result["brightnessReason"] = "external-control"
                return result
            if not all(value is False for value in controls):
                result["brightnessReason"] = "control-status-unknown"
                return result
            value = as_float(sample.get("brightness"))
            if value is not None and not isinstance(sample.get("brightness"), bool) and abs(value) < MAX_FLOAT_SENTINEL:
                result.update(brightness=value, brightnessSource="FieldSequence.getSequencedValue",
                              brightnessReason="sequenced-at-media-start")
            else:
                result["brightnessReason"] = "invalid-preview-value"
            return result
        return result

    def _media_preview(self, item: dict) -> dict:
        result = {
            "layerName": item.get("layerName", "—"),
            "layerType": item.get("layerType", "Video"),
            "mediaName": item.get("mediaName", "—"),
            "layerUid": item.get("layerUid"),
            "mediaUid": resource_uid(item.get("mediaResource")),
            "durationTimecode": None, "durationSeconds": None,
            "durationFps": None, "durationSource": None,
            **self._preview_brightness_metadata(item),
            **self._audio_metadata(item),
            **self._media_identity_metadata(item),
        }
        uid = uid_key(item.get("mediaResource"))
        metadata = self.media_metadata_by_uid.get(uid, {}) if "media_metadata" not in self.stream_errors else {}
        duration = as_float(metadata.get("durationSeconds"))
        fps = as_float(metadata.get("durationFps"))
        tc = metadata.get("durationTimecode")
        if (not is_still_media_name(str(result["mediaName"])) and duration is not None
                and 0 < duration < MAX_FLOAT_SENTINEL and fps is not None and fps > 0
                and isinstance(tc, str) and tc.strip()):
            result.update(durationTimecode=tc.strip(), durationSeconds=duration,
                          durationFps=fps, durationSource="VideoClip.transportDuration")
        return result

    def _media_for_section(self, section: dict | None, track_media: list) -> dict:
        if not section:
            return {"layerName": "—", "mediaName": "—"}
        start = as_float(section.get("startBeat"), 0.0)
        end = as_float(section.get("endBeat"), start)
        candidates = []
        for item in track_media:
            media_start, media_end = self._media_interval(item)
            overlap = min(end, media_end) - max(start, media_start)
            if overlap > 1.0e-6:
                candidates.append((max(start, media_start), item.get("_ordinal", 0), item))
        candidates.sort(key=lambda entry: (entry[0], entry[1]))
        if not candidates:
            return {"layerName": "—", "mediaName": "—"}
        item = candidates[0][2]
        return self._media_preview(item)

    def _adjacent_media(self, beat, track_media: list, direction: str) -> dict:

        current = as_float(beat)
        if current is None:
            return {"layerName": "—", "mediaName": "—"}
        candidates = []
        for item in track_media:
            start, end = self._media_interval(item)
            if direction == "next" and start > current + 1.0e-6:
                candidates.append((start, item.get("_ordinal", 0), item))
            elif direction == "previous" and end <= current + 1.0e-6:
                candidates.append((end, item.get("_ordinal", 0), item))
        if not candidates:
            return {"layerName": "—", "mediaName": "—"}
        candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=direction == "previous")
        item = candidates[0][2]
        return self._media_preview(item)

    def _layer_for_section(self, section: dict | None, role_data: dict) -> dict:
        if not section:
            return {"layerName": "—", "mediaName": "—"}
        start = as_float(section.get("startBeat"), 0.0)
        end = as_float(section.get("endBeat"), start)
        layers, _, _ = self._layer_catalog(role_data)
        candidates = []
        for ordinal, item in enumerate(layers):
            if item.get("enabled") is False:
                continue
            layer_start = as_float(item.get("layerStartBeat"))
            layer_end = as_float(item.get("layerEndBeat"))
            if layer_start is None or layer_end is None:
                continue
            overlap = min(end, layer_end) - max(start, layer_start)
            if overlap > 1.0e-6:
                candidates.append((max(start, layer_start), ordinal, item))
        candidates.sort(key=lambda entry: (entry[0], entry[1]))
        if not candidates:
            return {"layerName": "—", "mediaName": "—"}
        return {
            "layerName": candidates[0][2].get("layerName") or "—",
            "layerType": candidates[0][2].get("layerType") or "Content",
            "mediaName": "—",
        }

    def _adjacent_layer(self, beat, role_data: dict, direction: str) -> dict:
        current = as_float(beat)
        if current is None:
            return {"layerName": "—", "mediaName": "—"}
        layers, _, _ = self._layer_catalog(role_data)
        candidates = []
        for ordinal, item in enumerate(layers):
            if item.get("enabled") is False:
                continue
            start = as_float(item.get("layerStartBeat"))
            end = as_float(item.get("layerEndBeat"))
            if start is None or end is None:
                continue
            if direction == "next" and start > current + 1.0e-6:
                candidates.append((start, ordinal, item))
            elif direction == "previous" and end <= current + 1.0e-6:
                candidates.append((end, ordinal, item))
        if not candidates:
            return {"layerName": "—", "mediaName": "—"}
        candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=direction == "previous")
        return {
            "layerName": candidates[0][2].get("layerName") or "—",
            "layerType": candidates[0][2].get("layerType") or "Content",
            "mediaName": "—",
        }

    @staticmethod
    def _payload_matches_track(payload: dict, transport: dict) -> bool:
        wanted = uid_key(transport.get("trackUid") or transport.get("trackResource"))
        actual = uid_key(payload.get("trackResource") or payload.get("trackUid"))

        return bool(wanted and actual and wanted == actual)

    def _future_layers(self, role: str, role_data: dict) -> tuple[list, str]:
        key = role + ".graphical_schedule"
        if key in self.stream_errors:
            return [], "stream-error"
        payload = role_data.get("graphical_schedule") or {}
        if payload:
            if not self._payload_matches_track(payload, role_data.get("transport") or {}):
                return [], "track-mismatch"
            if not isinstance(payload.get("layers"), list):
                return [], "invalid-payload"
            return [self._normalise_layer(row) for row in payload["layers"]
                    if isinstance(row, dict)], "ok"



        rows = role_data.get("content_layers") or []
        return [self._normalise_layer(row) for row in rows if isinstance(row, dict)], (
            "compatibility-catalog" if rows else "no-data")

    def _layer_preview(self, role: str, role_data: dict, layer: dict, beat: float) -> dict:
        result = {
            "layerName": layer.get("layerName") or "—",
            "layerType": layer.get("layerType") or "Content",
            "layerUid": layer.get("layerUid"), "mediaName": "—", "mediaUid": None,
            "hasAudio": None, "audioSource": None,
            "durationTimecode": None, "durationSeconds": None,
            "durationFps": None, "durationSource": None,
            "brightness": None, "brightnessSource": None,
            "brightnessSampleBeat": beat, "brightnessReason": "no-preview-sample",
        }
        if role + ".graphical_preview_brightness" in self.stream_errors:
            result["brightnessReason"] = "preview-stream-error"
            return result
        payload = role_data.get("graphical_preview_brightness") or {}
        if not self._payload_matches_track(payload, role_data.get("transport") or {}):
            return result
        samples = payload.get("samples")
        if not isinstance(samples, list):
            return result
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            sample_beat = as_float(sample.get("sampleBeat"))
            if (not self._same_layer(layer, sample) or sample_beat is None or
                    abs(sample_beat - beat) > 1e-6):
                continue
            controls = [sample.get(key) for key in
                        ("expressionActive", "sockpuppetControlled", "externallyControlled")]
            if any(value is True for value in controls):
                result["brightnessReason"] = "external-control"
            elif not all(value is False for value in controls):
                result["brightnessReason"] = "control-status-unknown"
            else:
                value = as_float(sample.get("brightness"))
                if value is not None and not isinstance(sample.get("brightness"), bool) and abs(value) < MAX_FLOAT_SENTINEL:
                    result.update(brightness=value, brightnessSource="FieldSequence.getSequencedValue",
                                  brightnessReason="sequenced-at-layer-start")
                else:
                    result["brightnessReason"] = "invalid-preview-value"
            break
        return result

    def _next_scheduled(self, role: str, role_data: dict, track_media: list) -> tuple[dict, dict]:

        transport = role_data.get("transport") or {}
        current = as_float(transport.get("beat"))
        layers, catalog_status = self._future_layers(role, role_data)
        selection = {"catalogStatus": catalog_status, "complete": catalog_status == "ok",
                     "catalogLayers": len(layers), "candidates": 0, "kind": None,
                     "startBeat": None, "stackIndex": None,
                     "simultaneousLayers": 0, "tieBreak": "lowest-layer"}
        empty = {"layerName": "—", "layerType": "Content", "mediaName": "—"}
        if current is None or abs(current) >= MAX_FLOAT_SENTINEL:
            selection["reason"] = "unknown-playhead"
            return empty, selection
        if catalog_status in ("stream-error", "track-mismatch", "invalid-payload"):


            selection["reason"] = catalog_status
            return empty, selection
        candidates = []
        for ordinal, layer in enumerate(layers):
            start, end = as_float(layer.get("layerStartBeat")), as_float(layer.get("layerEndBeat"))
            if (layer.get("enabled") is False or start is None or end is None or
                    abs(start) >= MAX_FLOAT_SENTINEL or start <= current + 1e-6 or end <= start):
                continue
            candidates.append((start, ordinal, 1, end, "layer-start", layer))
        wanted_uid = uid_key(transport.get("trackUid") or transport.get("trackResource"))
        for item in track_media:
            actual_uid = uid_key(item.get("trackUid") or item.get("trackResource"))
            if wanted_uid and actual_uid and wanted_uid != actual_uid:
                continue
            matched = next(((ordinal, layer) for ordinal, layer in enumerate(layers)
                            if self._same_layer(item, layer)), None)
            if matched and matched[1].get("enabled") is False:
                continue
            if catalog_status == "ok" and matched is None:
                continue
            start, end = self._media_interval(item)
            if abs(start) >= MAX_FLOAT_SENTINEL or start <= current + 1e-6 or end <= start:
                continue
            ordinal = matched[0] if matched else len(layers) + item.get("_ordinal", 0)


            candidates.append((start, ordinal, 0, end, "media-start", item))
        selection["candidates"] = len(candidates)
        if not candidates:
            selection["reason"] = "no-future-start"
            return empty, selection
        earliest = min(entry[0] for entry in candidates)
        simultaneous = [entry for entry in candidates if abs(entry[0] - earliest) <= 1e-6]





        start, ordinal, _, end, kind, item = min(
            simultaneous, key=lambda entry: (-entry[1], entry[2], entry[0]))
        selection.update(kind=kind, startBeat=start, stackIndex=ordinal,
                         simultaneousLayers=len({entry[1] for entry in simultaneous}),
                         reason="nearest-start")
        result = (self._media_preview(item) if kind == "media-start" else
                  self._layer_preview(role, role_data, item, start))
        result.update(scheduledStartBeat=start, scheduledEndBeat=end)
        return result, selection

    def _sort_active(self, role: str, active: list) -> list:
        priority = self.config["timelines"][role].get("layer_priority", [])
        priority_map = {str(name): i for i, name in enumerate(priority)}
        indexed = list(enumerate(active))
        indexed.sort(
            key=lambda pair: (
                priority_map.get(str(pair[1].get("layerName") or ""), len(priority_map)),
                pair[0],
            )
        )
        return [item for _, item in indexed]

    @staticmethod
    def _same_layer(left: dict, right: dict) -> bool:
        left_uid = uid_key(left.get("layerUid") or left.get("layerResource"))
        right_uid = uid_key(right.get("layerUid") or right.get("layerResource"))
        if left_uid and right_uid:
            return left_uid == right_uid
        left_module_uid = uid_key(left.get("moduleResource"))
        right_module_uid = uid_key(right.get("moduleResource"))
        if left_module_uid and right_module_uid:
            return left_module_uid == right_module_uid
        left_name = str(left.get("layerName") or "").strip().casefold()
        right_name = str(right.get("layerName") or "").strip().casefold()
        return bool(left_name and right_name and left_name == right_name)

    def _layer_hidden(self, row: dict) -> bool:
        return (row.get("enabled") is False or row.get("enabledIncludingParents") is False
                or row.get("disabled") is True)

    def _active_rows(self, role: str, role_data: dict, track_media: list) -> list:
        transport = role_data.get("transport") or {}
        beat = as_float(transport.get("beat"))
        track_seconds = as_float(transport.get("trackSeconds"))
        layer_catalog, _, _ = self._layer_catalog(role_data)
        authoritative = role + ".active_content" in self.stream_status
        if self.connection_mode == "live" and not authoritative:
            return []
        legacy_active = [
            self._normalise_layer(item)
            for item in role_data.get("active", [])
            if isinstance(item, dict)
        ]
        exact_active = [
            self._normalise_layer(item)
            for item in (role_data.get("active_content", []) if authoritative
                         else role_data.get("active_media", []))
            if isinstance(item, dict)
        ]
        for member in exact_active:
            stored = next((item for item in layer_catalog if self._same_layer(member, item)), None)
            if stored:
                for key, value in stored.items():
                    if member.get(key) in (None, "") and value not in (None, ""):
                        member[key] = copy.deepcopy(value)

        for source_name in (
            "active_standard_video_details",
            "active_standard_video_frames",
            "active_standard_video_elapsed",
            "active_standard_video_remaining",
            "active_standard_video_tsec",
            "active_variable_video_details",
            "active_variable_video_frames",
            "active_variable_video_elapsed",
            "active_variable_video_remaining",
            "active_variable_video_tsec",
            "active_video_details",
            "active_video_tsec",
            "active_video",
        ):
            for raw in role_data.get(source_name, []):
                if not isinstance(raw, dict):
                    continue
                item = self._normalise_layer(raw)
                target = next(
                    (row for row in exact_active if self._same_layer(row, item)),
                    None,
                )
                if target is None:
                    if authoritative:
                        continue
                    target = item
                    exact_active.append(target)
                else:
                    for key, value in item.items():
                        if (value is not None and value != "" and
                                (not authoritative or key not in (
                                    "layerResource", "layerUid", "layerName", "enabled",
                                    "layerStartBeat", "layerEndBeat", "layerStartSec", "layerEndSec"))):
                            target[key] = copy.deepcopy(value)


        for source_name, value_key in (
            ("active_elapsed", "elapsed"),
            ("active_remaining", "remaining"),
        ):
            for raw in role_data.get(source_name, []):
                if not isinstance(raw, dict):
                    continue
                item = self._normalise_layer(raw)
                target = next(
                    (row for row in exact_active if self._same_layer(row, item)),
                    None,
                )
                if target is None:
                    if authoritative:
                        continue
                    target = item
                    exact_active.append(target)
                if value_key in item:
                    target[value_key] = item.get(value_key)
        if not exact_active and not authoritative:
            exact_active = legacy_active





        basic_active = []
        if authoritative:
            basic_active = exact_active
        elif layer_catalog and beat is not None:
            for item in layer_catalog:
                start = as_float(item.get("layerStartBeat"))
                end = as_float(item.get("layerEndBeat"))
                if item.get("enabled") is False or start is None or end is None:
                    continue
                if start <= beat < end:
                    basic_active.append(item)
        elif exact_active:
            basic_active = exact_active
        elif legacy_active:
            basic_active = legacy_active



        for exact in exact_active:
            if not any(self._same_layer(exact, base) for base in basic_active):
                basic_active.append(exact)



        unique_active = []
        for item in basic_active:
            if any(self._same_layer(item, existing) for existing in unique_active):
                continue
            unique_active.append(item)
        basic_active = unique_active

        min_brightness = float(
            cfg.value("logic.minimum_brightness", 0.001)
        )
        live_threshold = float(
            cfg.value("ui.live_threshold_seconds", 86_400)
        )
        default_fps = as_float(cfg.value("ui.default_fps", 25.0))
        rows = []

        for base in basic_active:
            brightness = as_float(base.get("brightness"), 1.0)
            if brightness is not None and brightness < min_brightness:
                continue

            row = self._normalise_layer(base)
            exact = next(
                (item for item in exact_active if self._same_layer(item, row)),
                None,
            )
            if exact:
                for key, value in exact.items():
                    if value is not None and value != "":
                        row[key] = copy.deepcopy(value)
                row = self._normalise_layer(row)

            if self._layer_hidden(row):
                continue

            brightness_key = f"{role}.active_brightness"
            samples = role_data.get("active_brightness") or []
            brightness_sample = next(
                (sample for sample in samples if isinstance(sample, dict)
                 and self._same_layer(row, self._normalise_layer(sample))), None,
            )
            if brightness_key in self.stream_errors:
                row["brightness"] = None
            elif brightness_sample is not None:
                row["brightness"] = as_float(brightness_sample.get("brightness"))
            else:
                row["brightness"] = as_float(row.get("brightness"))

            mapped_candidates = []
            for item in track_media:
                if not self._same_layer(item, row):
                    continue
                start, end = self._media_interval(item)
                if beat is None or start <= beat < end:
                    mapped_candidates.append(item)
            mapped_candidates.sort(
                key=lambda item: (
                    self._media_interval(item)[0],
                    item.get("_ordinal", 0),
                ),
                reverse=True,
            )
            mapped = mapped_candidates[0] if mapped_candidates else None

            if mapped and not row.get("mediaResource"):
                row["mediaResource"] = copy.deepcopy(mapped.get("mediaResource"))
            if mapped and not str(row.get("mediaName") or "").strip():
                row["mediaName"] = mapped.get("mediaName")
            row["mediaName"] = clean_media_name(row)
            row["layerName"] = str(row.get("layerName") or "—")
            row["layerUid"] = uid_text(
                row.get("layerUid") or resource_uid(row.get("layerResource"))
            )
            row["moduleType"] = str(row.get("moduleType") or "").strip()
            row["layerType"] = str(
                row.get("layerType") or module_label(row.get("moduleType"))
            )
            row.update(self._audio_metadata(row))
            row.update(self._media_identity_metadata(row))



            if row["layerName"] in ("", "—") and not row["layerUid"]:
                continue

            row["endPoint"] = (
                None
                if is_still_media_name(row["mediaName"])
                else end_point_label(row.get("atEndPoint"))
            )

            fps = as_float(row.get("fps"), default_fps)
            elapsed = as_float(row.get("elapsed"))
            remaining = as_float(row.get("remaining"))
            frame = as_float(row.get("frameNumber"))
            clip_start = as_float(row.get("clipStartFrame"))
            clip_end = as_float(row.get("clipEndFrame"))
            clip_frames = as_float(row.get("clipNFrames"))
            frame_range_valid = bool(
                fps and fps > 0
                and clip_start is not None
                and clip_end is not None
                and clip_end > clip_start
                and (clip_frames is None or clip_frames > 1)
            )





            if frame_range_valid and frame is not None:
                frame_elapsed = max(0.0, (frame - clip_start) / fps)
                frame_remaining = max(0.0, (clip_end - frame) / fps)
                used_frame_fallback = False



                if elapsed is None or (elapsed <= 0.0 and frame_elapsed > 0.0):
                    elapsed = frame_elapsed
                    used_frame_fallback = True
                if remaining is None or (remaining <= 0.0 and frame_remaining > 0.0):
                    remaining = frame_remaining
                    used_frame_fallback = True
                if used_frame_fallback:
                    row["timingFallback"] = "frame"

            media_name = str(row.get("mediaName") or "")
            media_runtime_valid = bool(
                (elapsed is not None and remaining is not None)
                and (
                    remaining > 0.0
                    or elapsed > 0.0
                    or frame_range_valid
                )
                and not is_still_media_name(media_name)
            )

            if media_runtime_valid:


                row["elapsed"] = max(0.0, elapsed)
                row["remaining"] = max(0.0, remaining)
                row["timingSource"] = "media"
                row["timingApproximate"] = False
            else:
                row["elapsed"] = None
                row["remaining"] = None
                row["timingSource"] = "none"




            if row["elapsed"] is None or row["remaining"] is None:
                start_sec = as_float(row.get("layerStartSec"))
                end_sec = as_float(row.get("layerEndSec"))
                if mapped:
                    start_sec = as_float(mapped.get("effectiveStartSec"), start_sec)
                    end_sec = as_float(mapped.get("effectiveEndSec"), end_sec)
                if (
                    track_seconds is not None
                    and start_sec is not None
                    and end_sec is not None
                    and end_sec >= start_sec
                ):
                    row["elapsed"] = max(0.0, track_seconds - start_sec)
                    row["remaining"] = max(0.0, end_sec - track_seconds)
                    row["timingSource"] = "layer"
                    row["timingApproximate"] = True

            row["fps"] = fps
            row["timingAvailable"] = bool(
                row.get("elapsed") is not None and row.get("remaining") is not None
            )

            if row["remaining"] is not None and row["remaining"] >= live_threshold:
                row["remaining"] = None
            rows.append(row)

        rows = self._sort_active(role, rows)
        limit = int(
            self.config["timelines"][role].get(
                "max_active_layers", 3 if role == "main" else 1
            )
        )
        return rows[: max(1, limit)]

    def _derive_role(self, role: str, role_data: dict, connected: bool = True) -> dict:
        transport = copy.deepcopy(role_data.get("transport") or {})
        membership = role_data.get("active_membership") or {}
        roster_uid = uid_key(membership.get("trackResource"))
        track_key = uid_key(transport.get("trackUid") or transport.get("trackResource"))
        membership_known = role + ".active_content" in self.stream_status
        membership_valid = (membership_known and
                            role + ".active_content" not in self.stream_errors and
                            (not membership or bool(roster_uid and track_key and roster_uid == track_key)))
        if membership_valid and membership:


            transport["beat"] = membership.get("beat")
            transport["trackSeconds"] = membership.get("trackSeconds")
            sample_beat = as_float(membership.get("beat"))
            sample_section = next((s for s in role_data.get("sections", [])
                                   if sample_beat is not None and
                                   as_float(s.get("startBeat"), 0) <= sample_beat <
                                   as_float(s.get("endBeat"), MAX_FLOAT_SENTINEL)), None)
            if sample_section:
                transport["sectionIndex"] = sample_section.get("index")
        role_data = {**role_data, "transport": transport}
        if membership and (not roster_uid or not track_key or roster_uid != track_key):
            role_data["active_content"] = []
        sections = [copy.deepcopy(s) for s in role_data.get("sections", []) if isinstance(s, dict)]
        sections.sort(key=lambda item: int(item.get("index", 0)))
        for section in sections:
            section["label"] = self._section_label(role_data, section)

        track_name = str(transport.get("trackName") or "NOT CONNECTED")
        track_uid = uid_text(transport.get("trackUid"))
        track_media = self._track_media(track_name, track_uid, role_data)
        current_index = transport.get("sectionIndex")
        try:
            current_index = int(current_index)
        except (TypeError, ValueError):
            current_index = None

        by_index = {int(section.get("index", 0)): section for section in sections}
        current_section = by_index.get(current_index) if current_index is not None else None
        previous_section = by_index.get(current_index - 1) if current_index is not None else None
        next_section = by_index.get(current_index + 1) if current_index is not None else None

        active = self._active_rows(role, role_data, track_media)
        next_media, next_selection = self._next_scheduled(role, role_data, track_media)

        countdown_source = next(
            (
                item for item in active
                if item.get("timingSource") == "media"
                and item.get("remaining") is not None
            ),
            None,
        )
        if countdown_source is None:
            countdown_source = next(
                (
                    item for item in active
                    if item.get("remaining") is not None
                    and str(item.get("mediaName") or "") not in ("", "—")
                    and not is_still_media_name(str(item.get("mediaName") or ""))
                ),
                None,
            )
        if countdown_source is None:
            countdown_source = next(
                (
                    item for item in active
                    if item.get("layerType") == "Video"
                    and str(item.get("mediaName") or "") not in ("", "—")
                    and not is_still_media_name(str(item.get("mediaName") or ""))
                ),
                None,
            )

        def section_payload(section, media):
            if not section:
                return {"available": False, "label": "—", **media,
                        **self._section_metadata(role, role_data, None)}
            return {
                "available": True,
                "index": section.get("index"),
                "label": section.get("label", "—"),
                "startBeat": section.get("startBeat"),
                "endBeat": section.get("endBeat"),
                **media,
                **self._section_metadata(role, role_data, section),
            }

        history = self.active_history[role]
        context = (self.project.get("path") or self.project.get("name"),
                   transport.get("transportName"), uid_key(track_uid) or track_name)
        if history["context"] != context:
            history.update(context=context, rows=None, previous=None, sequence=0)


        can_observe = connected and (membership_valid or
                                    (not membership_known and self.connection_mode != "live"))
        if can_observe:
            old_rows = history["rows"]
            hidden_rows = [row for row in role_data.get("active_content", [])
                           if isinstance(row, dict) and self._layer_hidden(row)]
            def hidden(row):
                return self._layer_hidden(row) or any(self._same_layer(row, item) for item in hidden_rows)
            if history["previous"] and hidden(history["previous"]):
                history["previous"] = None
            departed = ([row for row in old_rows
                         if not hidden(row) and not any(self._same_layer(row, now) for now in active)]
                        if old_rows is not None else [])
            if departed:


                catalogue, _ = self._future_layers(role, role_data)
                def departure_order(row):
                    stack = next((i for i, layer in enumerate(catalogue)
                                  if self._same_layer(row, layer)), old_rows.index(row))
                    return (as_float(row.get("layerEndBeat"), -MAX_FLOAT_SENTINEL), stack)
                last = max(departed, key=departure_order)
                history["sequence"] += 1
                history["previous"] = {**copy.deepcopy(last), "available": True,
                                       "departedAt": time.time(),
                                       "departureSequence": history["sequence"],
                                       "historySource": "active-exit"}
                LOGGER.info("ACTIVE exit [%s]: %s; Previous=%s", role,
                            ", ".join(row.get("layerName", "—") for row in departed),
                            last.get("layerName", "—"))
            history["rows"] = [section_payload(current_section, row) for row in active]
        previous = copy.deepcopy(history["previous"]) if history["previous"] else {
            "available": False, "label": "—", "layerName": "—", "layerType": "Content",
            "mediaName": "—", "historySource": "awaiting-observed-exit"}

        return {
            "role": role,
            "transportName": str(transport.get("transportName") or self.config["timelines"][role].get("transport", "—")),
            "trackName": track_name,
            "trackUid": track_uid,
            "trackMediaItems": len(track_media),
            "playMode": self._play_mode(transport),
            "playbackMode": ("Loop section" if transport.get("loopSection") else
                             "Play to end" if transport.get("sectionPlayMode") else
                             "Continuous" if "sectionPlayMode" in transport else None),
            "playing": bool(transport.get("playing")),
            "holding": bool(transport.get("holding")),
            "currentBeat": as_float(transport.get("beat")),
            "currentSeconds": as_float(transport.get("trackSeconds")),
            "currentSection": section_payload(
                current_section,
                {"layerName": "—", "layerType": "Content", "mediaName": "—"},
            ),
            "previous": previous,
            "activeMembership": {"available": connected and membership_valid,
                                 "authoritative": membership_known,
                                 "atomic": bool(membership),
                                 "trackMatches": not roster_uid or not track_key or roster_uid == track_key,
                                 "sampleBeat": transport.get("beat"),
                                 "source": "TrackPlayer.activeLayers" if membership_known else "legacy"},
            "previousSelection": {"source": previous["historySource"],
                                  "sequence": history["sequence"],
                                  "layerUid": previous.get("layerUid")},
            "active": active,
            "next": section_payload(next_section, next_media),
            "nextSelection": next_selection,
            "nextSection": section_payload(
                next_section, {"layerName": "—", "layerType": "Content", "mediaName": "—"},
            ),
            "countdownSeconds": (
                countdown_source.get("remaining") if countdown_source else None
            ),
            "countdownSource": (
                {
                    "layerUid": countdown_source.get("layerUid") or "",
                    "layerName": countdown_source.get("layerName") or "—",
                    "layerType": countdown_source.get("layerType") or "Video",
                    "mediaName": countdown_source.get("mediaName") or "—",
                    "timingSource": countdown_source.get("timingSource") or "unknown",
                    "timingApproximate": bool(
                        countdown_source.get("timingApproximate")
                    ),
                }
                if countdown_source
                else None
            ),
        }

    def snapshot(self) -> dict:
        with self.lock:
            now = time.monotonic()
            stale_after = cfg.value("logic.stale_after_seconds", 3.0)
            stale = not self.last_update_monotonic or (now - self.last_update_monotonic) > stale_after
            connected = bool(self.connected and not stale)
            output_available = connected and self.output_code in OUTPUT_STATES and "output_state" not in self.stream_errors
            output_code = self.output_code if output_available else None
            output_state, output_label = OUTPUT_STATES.get(output_code, ("unknown", "NO DATA"))
            fps_available = connected and self.director_fps is not None and "director_fps" not in self.stream_errors
            fps_raw_available = connected and self.director_fps_raw is not None and "director_fps_raw" not in self.stream_errors
            main = self._derive_role("main", self.roles["main"], connected)
            aux = self._derive_role("aux", self.roles["aux"], connected)
            return {
                "appVersion": APP_VERSION,
                "generatedAt": time.time(),
                "project": copy.deepcopy(self.project),
                "outputs": {
                    "state": output_state, "label": output_label, "code": output_code,
                    "available": output_available, "source": OUTPUT_STATE_PROPERTY,
                    "updatedAt": self.stream_status.get("output_state", {}).get("lastUpdate"),
                    "error": self.stream_errors.get("output_state", {}).get("message", ""),
                },
                "performance": {
                    "fps": self.director_fps if fps_available else None,
                    "rawFps": self.director_fps_raw if fps_raw_available else None,
                    "averageSamples": fps_average_samples(self.config),
                    "rawAvailable": fps_raw_available,
                    "rawStats": copy.deepcopy(self.fps_raw_stats),
                    "available": fps_available,
                    "source": DIRECTOR_FPS_OBJECT + " → " + director_fps_property(self.config),
                    "rawSource": DIRECTOR_FPS_OBJECT + " → " + DIRECTOR_FPS_RAW_PROPERTY,
                },
                "status": {
                    "connected": connected,
                    "socketConnected": self.connected,
                    "stale": stale,
                    "mode": self.connection_mode,
                    "seedHost": self.seed_host,
                    "directorHost": self.director_host,
                    "directorName": self.director_name or self.director_host,
                    "directorIp": self.director_ip,
                    "lastUpdate": self.last_update_wall,
                    "lastError": self.last_error,
                    "lastErrorAt": self.last_error_wall,
                    "logFile": str(LOG_FILE),
                    "scheduleItems": len(self.media_schedule),
                    "streams": copy.deepcopy(self.stream_status),
                    "streamErrors": copy.deepcopy(self.stream_errors),
                },
                "main": main,
                "aux": aux,
                "ui": copy.deepcopy(self.config.get("ui", {})),
            }

    def log_snapshot_summary(self, snapshot: dict):

        interval = float(
            cfg.value("logging.debug_snapshot_seconds", 10.0)
        )
        now = time.monotonic()
        with self.lock:
            if interval <= 0 or now - self.last_snapshot_log_monotonic < interval:
                return
            self.last_snapshot_log_monotonic = now
            stats = copy.deepcopy(self.fps_raw_stats)
            self.fps_raw_stats = {"count": 0, "min": None, "max": None}

        perf = snapshot.get("performance") or {}
        LOGGER.debug("Performance: director=%s fps_average=%s average_samples=%s "
                    "fps_raw=%s raw_samples=%s raw_min=%s raw_max=%s source=%s",
                    (snapshot.get("status") or {}).get("directorHost"),
                    perf.get("fps"), perf.get("averageSamples"), perf.get("rawFps"),
                    stats["count"], stats["min"], stats["max"], perf.get("source"))

        for role in ("main", "aux"):
            data = snapshot.get(role) or {}
            active = [
                {
                    "layer": item.get("layerName"),
                    "type": item.get("layerType"),
                    "media": item.get("mediaName"),
                    "elapsed": item.get("elapsed"),
                    "remaining": item.get("remaining"),
                    "endPoint": item.get("endPoint"),
                    "brightness": item.get("brightness"),
                    "hasAudio": item.get("hasAudio"),
                }
                for item in data.get("active", [])
            ]
            LOGGER.debug(
                "Derived %s: transport=%s track=%s beat=%s section=%s "
                "trackMedia=%s previous=%s active=%s next=%s countdown=%s currentSection=%s",
                role,
                data.get("transportName"),
                data.get("trackName"),
                data.get("currentBeat"),
                (data.get("currentSection") or {}).get("index"),
                data.get("trackMediaItems"),
                json.dumps(data.get("previous") or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(active, ensure_ascii=False, separators=(",", ":")),
                json.dumps(data.get("next") or {}, ensure_ascii=False, separators=(",", ":")),
                data.get("countdownSeconds"),
                json.dumps(data.get("currentSection") or {}, ensure_ascii=False, separators=(",", ":")),
            )
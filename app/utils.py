# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import math
import re
from pathlib import Path

from app import cfg


def fps_average_samples(config: dict) -> int:
    try:
        return max(1, min(300, cfg.value("updates.fps_average_samples", 60)))
    except TypeError, ValueError:
        return 60


def director_fps_property(config: dict) -> str:
    return f'object.seriesAverage("Actual", {fps_average_samples(config)})'


def as_float(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except TypeError, ValueError:
        return default


def uid_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def uid_key(value) -> str:
    if isinstance(value, dict):
        value = value.get("uid")
    text = uid_text(value).strip().lower()
    if not text:
        return ""
    try:
        return str(int(text, 0))
    except TypeError, ValueError:
        return text


def resource_uid(resource) -> str:
    if isinstance(resource, dict):
        return uid_text(resource.get("uid"))
    return ""


def resource_path(resource) -> str:
    if isinstance(resource, str):
        return resource
    if not isinstance(resource, dict):
        return ""
    path = resource.get("path", "")
    if isinstance(path, str):
        return path
    if isinstance(path, dict):
        for key in ("path", "value", "filename", "relativePath"):
            if isinstance(path.get(key), str):
                return path[key]
    return ""


def resource_name(resource) -> str:
    path = resource_path(resource)
    if not path:
        return ""
    normalised = path.replace("\\", "/")
    name = normalised.rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".apx") else name


def resource_type(resource) -> str:
    if not isinstance(resource, dict):
        return ""
    value = str(resource.get("type") or "").strip()
    return value.rsplit(".", 1)[-1]


def module_label(value) -> str:
    raw = resource_type(value) if isinstance(value, dict) else str(value or "")
    raw = raw.strip().rsplit(".", 1)[-1]

    class_match = re.search(r"([A-Za-z0-9_]+Module)", raw)
    if class_match:
        raw = class_match.group(1)
    raw = raw.removesuffix("Module")
    labels = {
        "VariableVideo": "Video",
        "VariableVideoTransition": "Video",
        "Video": "Video",
        "VideoMove": "Video",
        "VideoTransition": "Video",
        "SockPuppetVideo": "Video",
        "DepthVideo": "Depth Video",
        "TestPattern": "Test Pattern",
        "RenderStream": "RenderStream",
        "RenderStreamLocal": "RenderStream",
        "StageRender": "Stage Render",
        "RGBColour": "RGB Colour",
        "TimecodeReadout": "Timecode",
    }
    if raw in labels:
        return labels[raw]

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    return spaced or "Content"


def end_point_label(value):
    try:
        return {0: "Loop", 1: "Ping-Pong", 2: "Pause"}.get(int(value))
    except TypeError, ValueError:
        return None


def is_still_media_name(name: str) -> bool:
    suffix = Path(str(name or "")).suffix.lower()
    return suffix in {
        ".bmp", ".dpx", ".exr", ".gif", ".jpeg", ".jpg", ".png",
        ".psd", ".tga", ".tif", ".tiff"
    }


def split_property_errors(value):
    if isinstance(value, list):
        valid = []
        errors = []
        for item in value:
            if isinstance(item, dict) and item.get("errorType"):
                errors.append(
                    f'{item.get("errorType")}: {item.get("message") or "Unknown property-path error"}'
                )
            else:
                valid.append(item)
        return valid, errors
    if isinstance(value, dict) and value.get("errorType"):
        return None, [
            f'{value.get("errorType")}: {value.get("message") or "Unknown property-path error"}'
        ]
    return value, []


def clean_media_name(item: dict) -> str:
    explicit = str(item.get("mediaName") or "").strip()
    if explicit:
        return explicit
    name = resource_name(item.get("mediaResource") or item.get("resource"))
    if name:
        return name
    return "—"


def transport_expression(timeline_config: dict) -> str:
    override = str(timeline_config.get("transport_expression", "")).strip()
    if override:
        return override
    name = str(timeline_config.get("transport", "default"))
    cleaned = name.strip()

    if re.fullmatch(r"[A-Za-z0-9_]+", cleaned):
        return f'transportmanager:{cleaned}'
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'transportmanager:"{escaped}"'
# Copyright (C) 2026 Vladimir Zakharov / viavision.pro
# SPDX-License-Identifier: GPL-3.0-only


import math
import threading
import time

from .DashboardStore import DashboardStore


class DemoClient(threading.Thread):
    def __init__(self, config: dict, store: DashboardStore, stop_event: threading.Event):
        super().__init__(name="DemoData", daemon=True)
        self.config = config
        self.store = store
        self.stop_event = stop_event

    @staticmethod
    def sections():
        return [
            {"index": 0, "startBeat": 0.0, "endBeat": 24.0, "startSec": 0.0, "endSec": 24.0},
            {"index": 1, "startBeat": 24.0, "endBeat": 54.0, "startSec": 24.0, "endSec": 54.0},
            {"index": 2, "startBeat": 54.0, "endBeat": 84.0, "startSec": 54.0, "endSec": 84.0},
            {"index": 3, "startBeat": 84.0, "endBeat": 110.0, "startSec": 84.0, "endSec": 110.0},
        ]

    def run(self):
        self.store.set_connection(True, "demo", "DEMO-DIRECTOR")
        self.store.update_project(
            {
                "projectPath": "C:/Disguise/Projects/Playback Dashboard Demo",
                "version": {"major": 34, "minor": 0, "hotfix": 0},
            }
        )
        section_names = ["OPENING", "PRODUCT REVEAL", "SPEAKER WALK-IN", "FINALE"]
        section_times = [0.0, 24.0, 54.0, 84.0]
        annotations = {
            "annotations": {
                "sections": [{"time": section_times[i], "index": str(i + 1)} for i in range(4)],
                "notes": [{"time": section_times[i], "text": name} for i, name in enumerate(section_names)],
                "tags": [
                    {"time": section_times[i], "type": tag_type, "value": value}
                    for i in range(4)
                    for tag_type, value in [
                        ("TC", "10:00:%02d:00" % int(section_times[i] % 60)),
                        ("MIDI", "1.%d" % (60 + i)), ("CUE", "1.%d" % (i + 1)),
                    ]
                ],
            }
        }
        self.store.update_annotations("main", annotations)
        self.store.update_annotations("aux", {"annotations": {"sections": [{"time": 0, "index": "S"}], "notes": [{"time": 0, "text": "COMPANY LOGO"}], "tags": [{"time": 0, "type": "CUE", "value": "2.1"}]}})

        schedule = [
            {"mediaName": "00_opening_ident.mov", "trackName": "training", "trackUid": 100, "layerName": "Opening Ident", "layerUid": 1000, "effectiveStartBeat": 0.0, "effectiveEndBeat": 24.0, "effectiveStartSec": 0.0, "effectiveEndSec": 24.0},
            {"mediaName": "01_product_background.mov", "trackName": "training", "trackUid": 100, "layerName": "LED Background", "layerUid": 1001, "effectiveStartBeat": 24.0, "effectiveEndBeat": 54.0, "effectiveStartSec": 24.0, "effectiveEndSec": 54.0},
            {"mediaName": "01_product_alpha_overlay_v12.mov", "trackName": "training", "trackUid": 100, "layerName": "Product Overlay", "layerUid": 1002, "effectiveStartBeat": 24.0, "effectiveEndBeat": 54.0, "effectiveStartSec": 24.0, "effectiveEndSec": 54.0},
            {"mediaName": "01_particles_loop.mov", "trackName": "training", "trackUid": 100, "layerName": "Particles", "layerUid": 1003, "effectiveStartBeat": 24.0, "effectiveEndBeat": 54.0, "effectiveStartSec": 24.0, "effectiveEndSec": 54.0},
            {"mediaName": "02_speaker_walkin.mov", "trackName": "training", "trackUid": 100, "layerName": "Speaker Walk-in", "layerUid": 1004, "effectiveStartBeat": 54.0, "effectiveEndBeat": 84.0, "effectiveStartSec": 54.0, "effectiveEndSec": 84.0},
            {"mediaName": "03_finale.mov", "trackName": "training", "trackUid": 100, "layerName": "Finale", "layerUid": 1005, "effectiveStartBeat": 84.0, "effectiveEndBeat": 110.0, "effectiveStartSec": 84.0, "effectiveEndSec": 110.0},
        ]
        for ordinal, item in enumerate(schedule):
            item["mediaResource"] = {"uid": str(10000 + ordinal), "path": "objects/videoclip/" + item["mediaName"] + ".apx", "type": "VideoClip"}
        self.store.update_value("media_schedule", schedule)
        self.store.update_value("media_audio", [
            {"mediaResource": item["mediaResource"], "hasAudio": ordinal in (0, 1, 4, 5)}
            for ordinal, item in enumerate(schedule)
        ])
        self.store.update_value("preview_brightness", [
            {"mediaResource": item["mediaResource"], "trackResource": {"uid": item["trackUid"]},
             "layerResource": {"uid": item["layerUid"]}, "sampleBeat": item["effectiveStartBeat"],
             "brightness": 0.75 if ordinal == 4 else 1.0, "expressionActive": False,
             "sockpuppetControlled": False, "externallyControlled": False}
            for ordinal, item in enumerate(schedule)
        ])
        self.store.update_value("media_metadata", [
            {"mediaResource": item["mediaResource"], "durationSeconds": item["effectiveEndSec"] - item["effectiveStartSec"],
             "durationTimecode": "00:00:%02d:00" % int(item["effectiveEndSec"] - item["effectiveStartSec"]), "durationFps": 25}
            for item in schedule
        ])
        started = time.monotonic()
        while not self.stop_event.wait(0.1):
            self.store.update_value("output_state", 1)
            self.store.update_value("director_fps", 60.0)
            self.store.update_value("director_fps_raw", 60.0)
            beat = (time.monotonic() - started + 35.0) % 110.0
            section_index = 0 if beat < 24 else 1 if beat < 54 else 2 if beat < 84 else 3
            section = self.sections()[section_index]
            active_schedule = [
                item for item in schedule
                if item["trackName"] == "training" and item["effectiveStartBeat"] <= beat < item["effectiveEndBeat"]
            ]
            active = []
            for item in active_schedule:
                active.append(
                    {
                        "layerName": item["layerName"],
                        "layerUid": item["layerUid"],
                        "mediaName": item["mediaName"],
                        "moduleType": "VariableVideoModule",
                        "layerType": "Video",
                        "atEndPoint": 0 if "loop" in item["mediaName"].lower() else 2,
                        "elapsed": beat - item["effectiveStartBeat"],
                        "remaining": item["effectiveEndBeat"] - beat,
                        "fps": 25.0,
                        "brightness": 1.0,
                        "blendMode": 0,
                    }
                )
            self.store.update_value(
                "main.transport",
                {
                    "transportName": "default",
                    "trackName": "training",
                    "trackUid": 100,
                    "beat": beat,
                    "trackSeconds": beat,
                    "sectionIndex": section_index,
                    "playing": True,
                    "holding": False,
                    "stopped": False,
                    "loopSection": section_index == 1,
                    "sectionPlayMode": section_index != 1,
                },
            )
            self.store.update_value("main.sections", self.sections())
            self.store.update_value("main.active", active)
            self.store.update_value("main.active_brightness", [
                {"layerUid": item["layerUid"], "layerName": item["layerName"],
                 "brightness": 1.0 if ordinal == 0 else (
                     0.65 + 0.30 * math.sin(beat * 0.20) if ordinal == 1 else 0.30)}
                for ordinal, item in enumerate(active)
            ])
            self.store.update_value(
                "aux.transport",
                {
                    "transportName": "Second",
                    "trackName": "SECOND",
                    "trackUid": 200,
                    "beat": beat,
                    "trackSeconds": beat,
                    "sectionIndex": 0,
                    "playing": True,
                    "holding": False,
                    "stopped": False,
                    "loopSection": True,
                    "sectionPlayMode": False,
                },
            )
            self.store.update_value(
                "aux.sections",
                [{"index": 0, "startBeat": 0.0, "endBeat": 999999.0, "startSec": 0.0, "endSec": 999999.0}],
            )
            self.store.update_value(
                "aux.active",
                [
                    {
                        "layerName": "Logo still",
                        "layerUid": 1999,
                        "layerStartSec": 0.0,
                        "layerEndSec": 110.0,
                        "mediaName": "company_logo.png",
                        "moduleType": "VideoModule",
                        "layerType": "Video",
                        "atEndPoint": 0,
                        "elapsed": None,
                        "remaining": None,
                        "fps": 25.0,
                        "brightness": 1.0,
                    },
                    {
                        "layerName": "Web information",
                        "layerUid": 2000,
                        "layerStartSec": 0.0,
                        "layerEndSec": 110.0,
                        "mediaName": "—",
                        "moduleType": "WebModule",
                        "layerType": "Web",
                        "elapsed": None,
                        "remaining": None,
                        "fps": 50.0,
                        "brightness": 1.0,
                        "blendMode": 0,
                    }
                ],
            )
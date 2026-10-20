# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own workflow state independently of the Monitor property surface.

"""Fresh-status runout pause workflow; no host UI or QObject dependency."""
from collections.abc import Mapping
from ..printing.RunoutEvents import pause_owner


class RunoutPause:
    def __init__(self, *, send, connected, config, print_state, changed):
        self._send = send
        self._connected = connected
        self._config = config
        self._print_state = print_state
        self._changed = changed
        self.pending = False
        self.note = ""
        self.job = None
        self.generation = 0

    def invalidate(self):
        self.generation += 1
        self.pending = False
        self.note = "Connection changed; confirm the printer before another runout pause."

    def bind(self, job):
        if job == self.job:
            return False
        self.generation += 1
        self.job = job
        self.pending = False
        self.note = ""
        return True

    def request(self, event, snapshot):
        if not getattr(self._config(), "recovery_auto_pause", True):
            self.note = "Runout recorded; automatic follower pause is off."
            return
        observation = getattr(snapshot, "observation", None)
        if (not self._connected() or self.pending
                or getattr(observation, "state", None) != "printing"):
            return
        self.pending = True
        self.note = "Checking the print before requesting PAUSE…"
        job = event.job_key
        generation = self.generation

        def checked(payload, error):
            if generation != self.generation:
                return
            self.pending = False
            if error or not isinstance(payload, Mapping):
                self.note = "Runout recorded; the pause check failed. Check the printer."
                self._changed()
                return
            status = (payload.get("result") or {}).get("status") or {}
            stats = status.get("print_stats") or {}
            sd = status.get("virtual_sdcard") or {}
            pause = status.get("pause_resume") or {}
            fresh_configfile = status.get("configfile") or {}
            fresh_config = fresh_configfile.get("config") or {}
            fresh_owner = pause_owner(fresh_config, event.sensor)
            if fresh_owner != "follower":
                self.note = ("Klipper owns this sensor's runout pause."
                                           if fresh_owner == "klipper" else
                                           "Sensor pause setting is unavailable; check the printer.")
                self._changed()
                return
            sensor_status = status.get(event.sensor) or {}
            if (not isinstance(sensor_status, Mapping)
                    or sensor_status.get("filament_detected") is not False
                    or sensor_status.get("enabled") is not True):
                self.note = "Runout changed before PAUSE; inspect the sensor and printer."
                self._changed()
                return
            current = self._print_state()
            if (getattr(current, "job_key", None) != job
                    or stats.get("state") != "printing"
                    or stats.get("filename") != job[0]
                    or sd.get("file_size") != job[1]
                    or pause.get("is_paused") is not False):
                self.note = "Runout recorded; the print changed before PAUSE."
                self._changed()
                return

            def paused(_payload, command_error):
                if generation != self.generation:
                    return
                self.pending = False
                self.note = ("PAUSE requested for runout; waiting for Klipper confirmation."
                                           if not command_error else
                                           f"PAUSE request was uncertain: {command_error}")
                self._changed()

            self.pending = True
            started = self._send(
                "recovery", "runout-pause", "POST", "printer/gcode/script", paused,
                body={"script": "PAUSE"}, category="command")
            if not started:
                self.pending = False
                self.note = "PAUSE could not be submitted; check the printer."
            self._changed()

        started = self._send(
            "recovery", "runout-check", "POST", "printer/objects/query", checked,
            body={"objects": {"print_stats": None, "virtual_sdcard": None,
                              "pause_resume": None, "configfile": ["config"],
                              event.sensor: ["filament_detected", "enabled"]}}, category="command")
        if not started:
            self.pending = False
            self.note = "Runout pause check could not start; check the printer."


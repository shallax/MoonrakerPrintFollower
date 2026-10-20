# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Isolate recovery source discovery, selection and stale-request fences.

"""Recovery file selection without Qt, indexing or printer-command ownership."""
from collections.abc import Mapping
from ..files.browser.FileManagerPolicy import is_gcode_name


class RecoverySources:
    def __init__(self, *, send, max_bytes, changed):
        self._send = send
        self._max_bytes = max_bytes
        self._changed = changed
        self.job = None
        self.options = []
        self.loading = False
        self.error = ""
        self.generation = 0
        self._closed = False

    def state(self, *, active):
        return {"available": not active, "loading": self.loading,
                "error": self.error, "selected": self.job[0] if self.job else "",
                "options": [dict(item) for item in self.options]}

    def reset(self):
        self.job = None
        self.options = []
        self.loading = False
        self.error = ""
        self.generation += 1

    def observe_active(self, active):
        if active and self.job is not None:
            self.job = None
            self.generation += 1

    def close(self):
        self._closed = True
        self.generation += 1

    def select(self, filename, *, active, recovering):
        if active or recovering:
            return None
        row = next((item for item in self.options if item["name"] == filename), None)
        if row is None:
            return None
        self.job = (row["name"], row["size"], 0)
        return self.job

    def refresh(self, *, active, configured):
        """List selectable root G-code without touching the virtual SD worker."""
        if active or not configured:
            return False
        self.generation += 1
        generation = self.generation
        self.loading = True
        self.error = ""
        self._changed()

        def received(payload, error):
            if generation != self.generation or self._closed:
                return
            self.loading = False
            result = (payload or {}).get("result") or {}
            rows = result.get("files") if isinstance(result, Mapping) else None
            if error or not isinstance(rows, list):
                self.error = "Could not list G-code files on the printer."
                self.options = []
            else:
                found = {}
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    name = row.get("filename")
                    try:
                        size = int(row.get("size"))
                    except (TypeError, ValueError):
                        continue
                    if (isinstance(name, str) and name == name.strip()
                            and "/" not in name and "\\" not in name
                            and not any(char in name for char in ";\r\n\x00")
                            and is_gcode_name(name) and 0 < size <= self._max_bytes()):
                        def timestamp(value):
                            try:
                                value = float(value or 0)
                                return value if 0 < value < float("inf") else 0
                            except (TypeError, ValueError, OverflowError):
                                return 0
                        found[name] = {"name": name, "size": size,
                                       "lastPrinted": timestamp(row.get("print_start_time")),
                                       "modified": timestamp(row.get("modified"))}
                self.options = sorted(found.values(), key=lambda item: (
                    -item["lastPrinted"], -item["modified"], item["name"].casefold()))
                if not found:
                    self.error = "No root G-code files are available for unloaded recovery."
            self._changed()

        started = self._send(
            "recovery-files", "root-directory", "GET",
            "server/files/directory?path=gcodes&extended=true", received,
            category="metadata")
        if not started:
            self.loading = False
            self.error = "Could not request the printer's G-code list."
            self._changed()
        return bool(started)


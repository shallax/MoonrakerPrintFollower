"""Temperature chart preferences and cached UI projections.

This owner has no Monitor-model reference: raw samples enter its history and
configuration writes use the injected per-printer capability. Each chart surface
retains its own revision-keyed payload so unrelated Monitor publications are cheap.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import replace
from PyQt6.QtCore import QObject, pyqtSignal

from .MonitorTemperatureHistory import (
    DORMANT_CHART, PALETTE, TemperatureHistory, chart_payload, latest_values,
    mini_chart_payload, mini_names, series_metadata,
)


class TemperaturePresentation(QObject):
    changed = pyqtSignal()

    def __init__(self, config, apply_config, initial_config, parent=None):
        super().__init__(parent)
        self._config = config
        self._apply_config = apply_config
        self._chart_config = initial_config
        self._history = TemperatureHistory()
        self._chart_mini = None
        self._chart_mini_key = None
        self._chart_full = None
        self._chart_full_key = None
        self._chart_latest = None
        self._chart_latest_revision = -1
        self._chart_open = False
        self._legend_payload = None

    def setTemperatureSensorVisible(self, name, visible):
        # Missing keys mean the default (visible); only a real change saves.
        if self._chart_config.get("visible", {}).get(str(name), True) is bool(visible):
            return  # idempotent: a re-bound checkbox must not rewrite the config
        config = self._prune_chart_config(self._chart_config)
        config = deepcopy(config)
        config.setdefault("visible", {})[str(name)] = bool(visible)
        self._chart_config = config
        self._apply_chart_config()
        self.changed.emit()


    def setTemperatureSensorColor(self, name, color):
        color = str(color)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return  # the canvas only renders #rrggbb; anything else would silently draw grey
        # A missing entry means the palette default, so compare against
        # the colour the series actually renders with right now.
        if any(series["name"] == str(name) and series["color"] == color
               for series in series_metadata(self._history, self._chart_config)):
            return
        config = self._prune_chart_config(self._chart_config)
        config = deepcopy(config)
        config.setdefault("colors", {})[str(name)] = color
        self._chart_config = config
        self._apply_chart_config()
        self.changed.emit()


    def setShowTemperatureTargets(self, show):
        if self._chart_config.get("showTargets") is bool(show):
            return
        self._chart_config = {**self._chart_config, "showTargets": bool(show)}
        self._apply_chart_config()
        self.changed.emit()


    def setShowTemperaturePower(self, show):
        if self._chart_config.get("showPower") is bool(show):
            return
        self._chart_config = {**self._chart_config, "showPower": bool(show)}
        self._apply_chart_config()
        self.changed.emit()


    def _chart_config_key(self):
        return json.dumps(self._chart_config, sort_keys=True)


    def _chart_mini_value(self):
        """The compact preview (temperatureChartMini): a bounded
        payload rebuilt per history revision — its SIZE stops growing
        once the window outgrows the mini render budget (the build
        stays one allocation-light scan over the raw window)."""
        key = (self._history.revision, self._chart_config_key())
        if self._chart_mini is None or self._chart_mini_key != key:
            self._chart_mini = mini_chart_payload(self._history, self._chart_config)
            self._chart_mini_key = key
        return self._chart_mini


    def _chart_full_value(self):
        """The pop-over payload (temperatureChartFull): DORMANT while
        the pop-over is closed — the same empty object every publish,
        so the property never re-converts and the full-chart signal
        never fires on a raw history sample. Opening hydrates it;
        closing returns the path to dormancy on the very next publish."""
        if not self._chart_open:
            return DORMANT_CHART
        key = (self._history.revision, self._chart_config_key())
        if self._chart_full is None or self._chart_full_key != key:
            self._chart_full = chart_payload(self._history, self._chart_config)
            self._chart_full_key = key
        return self._chart_full


    def _chart_latest_value(self):
        """One scalar per series for the legends' live-value labels —
        never a full payload search."""
        if self._chart_latest is None or self._chart_latest_revision != self._history.revision:
            self._chart_latest = latest_values(self._history)
            self._chart_latest_revision = self._history.revision
        return self._chart_latest


    def _legend_value(self):
        """Legend metadata (identity, labels, colours, visibility, and
        the mini selection's row list): its own property so legend
        delegates only rebuild when the config or the sensor set
        actually changed, never at the sample cadence."""
        if self._legend_payload is None:
            self._legend_payload = {}
        key = json.dumps(self._chart_config, sort_keys=True) + "|" + "|".join(self._history.names())
        if self._legend_payload.get("_key") != key:
            metadata = series_metadata(self._history, self._chart_config)
            selected = set(mini_names([series["name"] for series in metadata],
                                      self._chart_config.get("visible")
                                      if isinstance(self._chart_config.get("visible"), dict) else {}))
            self._legend_payload = {
                "_key": key,
                "series": [{"name": series["name"], "label": series["label"],
                            "color": series["color"], "visible": series["visible"],
                            "primary": series["primary"]} for series in metadata],
                "miniSeries": [{"name": series["name"], "label": series["label"],
                                "color": series["color"]}
                               for series in metadata if series["name"] in selected],
                "showTargets": bool(self._chart_config.get("showTargets", True)),
                "showPower": bool(self._chart_config.get("showPower", True)),
                "palette": list(PALETTE),
            }
        return self._legend_payload


    def setChartOpen(self, opened):
        # The pop-over's hydration gate: the full chart payload
        # materialises only while the pop-over is open; closed, it
        # is the shared dormant object.
        opened = bool(opened)
        if opened == self._chart_open:
            return
        self._chart_open = opened
        self._chart_full = None  # hydration or dormancy lands on the next publish
        self.changed.emit()


    def _apply_chart_config(self):
        """Persist the chart config into the per-printer record (the
        camera_selected precedent); the global JSON keeps chrome only."""
        config = self._config()
        if getattr(config, "temperature_chart", None) != self._chart_config:
            self._apply_config(replace(config, temperature_chart=self._chart_config))


    def _prune_chart_config(self, config):
        """Drop colours/visibility for sensors that no longer exist; never
        prune while the live set is empty (startup before the first aux)."""
        names = self._history.names()
        if not names:
            return config
        for key in ("visible", "colors"):
            entries = config.get(key)
            if isinstance(entries, dict) and any(name not in names for name in entries):
                config = dict(config)
                config[key] = {name: value for name, value in entries.items() if name in names}
        return config

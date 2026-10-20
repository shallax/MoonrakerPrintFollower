# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Delegate attestation and action state to portable effect-driven owners.
# 2026-10-04 [3fda19f] Finish per-camera failure detection controls and training
# 2026-10-04 [31f1629] Align release introduction with durable pause protection
# 2026-10-04 [7981ac6] Harden local detection and add per-print mute and monitored regions

"""Qt bridge for detection observations, tuning, evidence and host effects.

Explicit run and action owners supply history attestation and per-run decisions.
The bridge applies persistence/command effects and constructs host notifications.
"""

import time
from dataclasses import replace
from hashlib import sha256
from math import isfinite
from urllib.parse import parse_qsl, urlsplit

from PyQt6.QtCore import QObject, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Logger import Logger

from .camera.MonitorCamera import MonitorCamera
from .camera.CameraSourceIdentity import source_key
from .MonitorPermissions import R_UNKNOWN, Verdict, can_pause
from ..detection.DetectionPolicy import DetectionPolicy
from ..detection import EvidenceStore
from ..detection.DetectionObservation import FRESH_SECONDS
from ..geometry.DetectionRegions import validate_regions, fingerprint
from ..detection.DetectionRun import DetectionRun, DetectionRunInput
from ..detection.DetectionActions import DetectionActions

class MonitorDetection(QObject):
    detectionChanged = pyqtSignal()

    def __init__(self, *, detection, data, client, camera, recovery, config,
                 apply_config, identity, print_state, commands, store, publish,
                 schedule_publish, published_values, printer_name, monotonic=None, parent=None):
        super().__init__(parent)
        self._monotonic = monotonic
        self._detection = detection
        self._data, self._client = data, client
        self._camera, self._camera_recovery = camera, recovery
        self._config, self._apply_config = config, apply_config
        self._identity, self._print_state = identity, print_state
        self._commands, self._store = commands, store
        self._publish, self._schedule_publish = publish, schedule_publish
        self._published_values, self._printer_name = published_values, printer_name
        self._actions = DetectionActions(self._detection_effect)
        self._run = DetectionRun(current=self._detection_run_input, request=data.request,
            changed=self._on_detection_run_transition, monotonic=self._now, wall_time=time.time)
        self.destroyed.connect(self._run.close)
        self._detection_epoch = 0
        self._detection_editing = False
        self._detection_edit_camera = None
        self._detection_boxes = ()
        self._detection_accepted_at = None
        self._detection_sample_at = None
        self._detection_result_at = None
        self._detection_message = None
        self._detection_message_token = None
        self._detection_sample_error = ""
        self._detection_regions_cache = None
        self._detection_policy = DetectionPolicy()
        self._detection_context_seen = None
        self._detection_thresholds_seen = (38, 78, 300)
        self._detection_frame = None
        self._detection_baseline_printer = identity()[0] if identity is not None else None
        self._detection_baseline_camera = None
        self._detection_baselines = {}
        self._detection_camera_seen = None
        if hasattr(store, "get_machine_state") and identity is not None:
            saved = store.get_machine_state(self._detection_baseline_printer) or {}
            baselines = saved.get("detectionBaselines")
            if isinstance(baselines, dict):
                self._detection_baselines = baselines
            self._actions.load_printer(saved.get("detectionActions") or {})
        if detection is not None:
            if hasattr(detection, "observationReady"):
                detection.observationReady.connect(self._on_detection_observation)
            else:
                detection.resultReady.connect(self._on_detection_result)
            detection.stateChanged.connect(self._refresh_detection)
            detection.stateChanged.connect(self.detectionChanged.emit)

    def _now(self):
        return self._monotonic() if self._monotonic is not None else time.monotonic()

    def _invalidate_detection(self):
        self._hide_detection_message()
        self._detection_sample_error = ""
        self._detection_epoch += 1
        self._detection_context_seen = None
        self._detection_policy.reset()
        self._detection_boxes = ()
        self._detection_result_at = None
        self._detection_sample_at = None
        self._detection_accepted_at = None
        if self._detection is not None:
            self._detection.reset()
        self.detectionChanged.emit()

    def _active_detection_regions(self):
        saved = self._config().detection_regions
        if "invalid" in saved:
            return None
        return saved.get(self._detection_camera_id(), ())

    def _potential_saved_mute(self):
        saved = self._actions.state.saved
        run = saved.get("run") if isinstance(saved, dict) else None
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        return (isinstance(run, dict) and saved.get("muted") is True
                and run.get("filename") == stats.get("filename")
                and run.get("binding") == sha256(self._config().url.encode()).hexdigest())

    def _detection_run_input(self):
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        job = getattr(self._print_state(), "job_key", None)
        config = self._config()
        key = (self._detection_printer_id(), self._client.session.generation,
               job, config.url, stats.get("state") in ("printing", "paused"))
        eligible = (config.detection_enabled and self.detectionCameraReady
                    and self._detection is not None and self._detection.enabled)
        return DetectionRunInput(key, str(stats.get("filename") or ""), bool(eligible))

    def _on_detection_run_transition(self, transition):
        if transition.kind == "reset":
            self._actions.run_changed(active=self._run.context[4], potential_saved_mute=self._potential_saved_mute())
            self._hide_detection_message()
            return
        if transition.kind == "admitted":
            if self._run.identity != transition.previous:
                self._invalidate_detection()
                self._detection_context_seen = self._detection_context()
            self._actions.sync((self._run.context[0], self._run.identity), time.time())
            context = self._detection_context()
            state = self._detection_policy.state(now=self._now(), context=context, active=context is not None)
            if self._run.identity == transition.previous and context is not None and state.name in ("warning", "failure"):
                self._handle_detection_action(context, state.name, time.time())
        elif transition.kind == "lost":
            self._actions.run_changed(potential_saved_mute=self._potential_saved_mute())
            self._invalidate_detection()
            self._detection_context_seen = self._detection_context()
        self.detectionChanged.emit()
        self._schedule_publish()

    def _detection_context(self):
        if self._detection_editing or self._active_detection_regions() is None \
                or self._detection is None or not self._detection.ready or not self._detection.enabled \
                or not self._config().detection_enabled or self._config().camera_disabled or not self._data.active \
                or self._data.connection_state != "yes" or not self._camera_recovery.stream_enabled \
                or not getattr(self._camera, "url", "") or self._identity is None:
            return None
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        job = getattr(self._print_state(), "job_key", None)
        if stats.get("state") != "printing" or job is None:
            return None
        camera = self._camera.values
        camera_id = self._detection_camera_id()
        if camera_id is None:
            return None
        printer_id = self._detection_printer_id()
        if printer_id is None:
            return None
        # A transport reload (including foregrounding Cura) does not change
        # the analysis identity. Frame intake still rejects old reload URLs.
        return (printer_id, self._client.session.generation, job,
                camera_id, str(camera.get("cameraName") or ""),
                self._detection_source_key(),
                self._config().detection_warning_threshold,
                self._config().detection_failure_threshold,
                self._config().detection_safe_seconds,
                self._config().detection_notify_enabled,
                self._config().detection_pause_enabled,
                self._config().detection_sensitivity, self._regions_fingerprint(),
                self._run.identity or "unresolved", self._detection_epoch)

    def _detection_source_key(self):
        return source_key(self._camera.url)

    def _regions_fingerprint(self):
        saved = self._config().detection_regions
        camera = self._detection_camera_id()
        cached = self._detection_regions_cache
        if cached is None or cached[0] is not saved or cached[1] != camera:
            regions = self._active_detection_regions()
            cached = (saved, camera, fingerprint(regions) if regions is not None else "invalid")
            self._detection_regions_cache = cached
        return cached[2]

    def _detection_printer_id(self):
        if self._identity is None:
            return None
        try:
            return self._identity()[0]
        except RuntimeError:
            return None

    def _detection_camera_id(self):
        cameras = self._data.snapshot.webcams or []
        index = self._camera.values.get("activeWebcamIndex")
        if cameras:
            if type(index) is int and 0 <= index < len(cameras):
                camera = cameras[index]
                upstream = str(camera.get("stream_url") or self._config().camera_url or camera.get("snapshot_url") or "")
                stable = source_key(upstream)
                camera_key = sha256(MonitorCamera.identity(camera, index).encode()).hexdigest()[:20]
                return "upstream:" + camera_key + ":source:" + sha256(stable.encode()).hexdigest()[:20]
            return None
        if not getattr(self._camera, "url", ""):
            return None
        source = source_key(self._config().camera_url)
        return "configured:" + sha256(source.encode("utf-8")).hexdigest()

    def _sync_detection_printer(self):
        printer_id = self._detection_printer_id()
        if printer_id != self._detection_baseline_printer:
            self._detection_baseline_printer = printer_id
            self._detection_baseline_camera = None
            self._detection_baselines = {}
            self._actions.load_printer({})
            if printer_id is not None and hasattr(self._store, "get_machine_state"):
                saved = self._store.get_machine_state(printer_id) or {}
                baselines = saved.get("detectionBaselines")
                if isinstance(baselines, dict):
                    self._detection_baselines = baselines
                self._actions.load_printer(saved.get("detectionActions") or {})
        camera_id = self._detection_camera_id() if printer_id is not None else None
        if camera_id is not None:
            # Cropped input has a different score distribution from the old
            # full-frame mask. Keep full-frame baselines, retire masked ones.
            mode = ":crop-v1:" if self._active_detection_regions() else ":mask-v1:"
            camera_id += mode + self._regions_fingerprint()
        if camera_id != self._detection_baseline_camera:
            self._detection_baseline_camera = camera_id
            self._detection_policy = DetectionPolicy()
            self._detection_policy.restore_baseline(self._detection_baselines.get(camera_id))
            self._detection_thresholds_seen = (38, 78, 300)
            self._detection_context_seen = None

    def _sync_detection_thresholds(self):
        self._sync_detection_printer()
        config = self._config()
        thresholds = (config.detection_warning_threshold, config.detection_failure_threshold,
                      config.detection_safe_seconds, config.detection_sensitivity)
        if thresholds != self._detection_thresholds_seen:
            baseline = self._detection_policy.baseline
            self._detection_policy = DetectionPolicy(
                warning_threshold=thresholds[0], failure_threshold=thresholds[1],
                safe_seconds=thresholds[2], sensitivity=thresholds[3])
            self._detection_policy.restore_baseline(baseline)
            self._detection_thresholds_seen = thresholds
            self._detection_context_seen = None

    def _detection_values(self):
        self._sync_detection_thresholds()
        if self._detection_editing and (self._detection_edit_camera != self._detection_camera_id()
                                       or not self.detectionCameraReady or not self.detectionGlobalEnabled
                                       or not self.detectionEnabled):
            self._detection_editing = False
            self._invalidate_detection()
        self._run.ensure()
        context = self._detection_context()
        if context != self._detection_context_seen:
            self._invalidate_detection()
            context = self._detection_context()
            self._detection_context_seen = context
        state = self._detection_policy.state(now=self._now(), context=context,
                                             active=context is not None)
        if context is None:
            if self._detection_editing:
                reason = "Editing monitored regions; detection suspended"
            elif self._active_detection_regions() is None:
                reason = "Saved monitored regions are invalid; edit or reset them"
            elif self._detection is not None and self._detection.ready and not self._detection.enabled:
                reason = "Local detection is disabled in Settings"
            elif not self._config().detection_enabled:
                reason = "Off for this printer"
            elif self._detection is None or not self._detection.ready:
                reason = "Local detection is unavailable"
            elif not getattr(self._camera, "url", "") or not self._camera_recovery.stream_enabled:
                reason = "Camera is unavailable"
            else:
                reason = "Waiting for an active print"
        else:
            reason = {
                "waiting": "Waiting for an analysed frame",
                "learning": "Learning baseline — keep the camera scene clear (%d/%d frames)" % (
                    self._detection_policy.baseline["frames"], self._detection_policy.BASELINE_LEARNING_FRAMES),
                "stale": "Camera analysis is stale",
                "normal": "Normal",
                "warning": "Warning",
                "failure": "Possible failure",
            }[state.name]
        if context is not None and self._detection_sample_error:
            reason = self._detection_sample_error
        if context is not None and self._actions.state.muted:
            reason += " — alerts and automatic pause muted for this print"
        elif context is not None and not self._run.fresh(30):
            reason += " — pause and mute waiting for print identity"
        return {"detectionState": state.name if context is not None else "idle",
                "detectionScore": state.score if state.score is not None else -1,
                "detectionRawScore": state.raw_score if state.raw_score is not None else -1.0,
                "detectionStatus": reason}

    def _refresh_detection(self):
        if self._detection_result_at is not None:
            self.detectionChanged.emit()
        if self._detection is not None and any(
                self._published_values().get(key) != value for key, value in self._detection_values().items()):
            self._schedule_publish()

    def _on_detection_observation(self, result):
        if result.sample.context != self._detection_context():
            return
        age = self._now() - result.sample.captured_at
        if not 0 <= age <= FRESH_SECONDS or (self._detection_accepted_at is not None and result.sample.captured_at <= self._detection_accepted_at):
            return
        self._detection_accepted_at = result.sample.captured_at
        self._detection_sample_error = result.unavailable_reason
        if result.unavailable_reason:
            self._detection_policy.reset()
            self._detection_boxes = ()
            self._detection_result_at = None
            self.detectionChanged.emit()
            self._schedule_publish()
            return
        self._detection_frame = result.sample.image
        self._detection_sample_at = result.sample.captured_at
        self._detection_result_at = result.sample.captured_at
        self._detection_boxes = result.detections.boxes
        self._on_detection_result(result.sample.context, result.detections.score)
        self.detectionChanged.emit()

    def _on_detection_result(self, context, confidence):
        if context != self._detection_context():
            return
        self._sync_detection_thresholds()
        now = self._detection_sample_at if self._detection_sample_at is not None else self._now()
        if not 0 <= self._now() - now <= FRESH_SECONDS:
            return
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        duration = stats.get("print_duration")
        elapsed = duration if type(duration) in (int, float) and 0 <= duration <= 3153600000 and isfinite(duration) else None
        try:
            level = self._detection_policy.observe(
                confidence, now=now, context=context, print_elapsed_seconds=elapsed)
        except (TypeError, ValueError, OverflowError):
            Logger.logException("e", "Moonraker Print Follower: invalid detection observation")
            return
        self._record_detection_sample(context, now, confidence)
        if self._detection_baseline_camera is not None and hasattr(self._store, "set_machine_state"):
            self._detection_baselines.pop(self._detection_baseline_camera, None)
            self._detection_baselines[self._detection_baseline_camera] = self._detection_policy.baseline
            while len(self._detection_baselines) > 32:
                del self._detection_baselines[next(iter(self._detection_baselines))]
            baselines = dict(self._detection_baselines)
            self._queue_detection_io("baseline:" + str(context[0]),
                lambda: self._store.set_machine_state(context[0], {"detectionBaselines": baselines}))
        self._handle_detection_action(context, level, time.time())
        self._schedule_publish()

    def _detection_evidence_root(self):
        """Where the alert evidence belongs: the detection service's own
        storage, so a removal takes the frames and timelines with the
        downloads. A stand-in without it keeps only the timeline's
        in-memory effect — the alert itself never depends on this."""
        getter = getattr(self._detection, "storage_root", None)
        try:
            return getter() if callable(getter) else None
        except Exception:
            return None

    def _detection_evidence_key(self):
        return self._run.identity or self._run.notice_identity

    def _queue_detection_io(self, key, callback):
        enqueue = getattr(self._detection, "enqueue_io", None)
        if callable(enqueue):
            return enqueue(key, callback)
        return False

    def _record_detection_sample(self, context, now, confidence) -> None:
        """Every analysed frame lands in its print's timeline: the
        record a user (or a bug report) reads to see what the signal
        did before an alert, and what it was tuned against."""
        root = self._detection_evidence_root()
        if root is None:
            return
        state = self._detection_policy.state(now=now, context=context, active=True)
        if state.score is None:
            return
        try:
            at = time.time() - (self._now() - now)
            print_key = self._detection_evidence_key()
            self._queue_detection_io("timeline", lambda: EvidenceStore.append_sample(
                root, printer=context[0], print_key=print_key,
                at=at, score=state.score, raw=confidence))
        except Exception:
            # This runs inside the result slot, and an exception
            # escaping a slot aborts Cura: a diagnostic write is never
            # worth the print.
            Logger.logException("e", "Moonraker Print Follower: detection timeline write failed")

    def _retain_detection_evidence(self, level: str, context) -> str:
        """The alert's triggering frame on disk, best-effort."""
        root = self._detection_evidence_root()
        frame = self._detection_frame
        if root is None or frame is None or frame.isNull():
            return ""
        try:
            state = self._detection_policy.state(now=self._now(), context=context, active=True)
            at = time.time() - (self._now() - (self._detection_sample_at or self._now()))
            print_key = self._detection_evidence_key()
            self._queue_detection_io("frame", lambda: EvidenceStore.save_frame(
                root, frame, printer=context[0], print_key=print_key, level=level,
                score=state.score, at=at))
            return ""
        except Exception:
            Logger.logException("e", "Moonraker Print Follower: detection evidence write failed")
            return ""

    def _notify_detection(self, level: str, paused: bool) -> None:
        from UM.Message import Message
        status = "Possible print failure" if level == "failure" else "Print failure warning"
        suffix = " — print paused" if paused else " — check the camera"
        # The alert names the printer: two machines on one Cura make an
        # unattributed toast ambiguous.
        printer = self._printer_name()
        summary = ("%s on %s%s" % (status, printer, suffix)) if printer else status + suffix
        self._hide_detection_message()
        message = Message(summary, 60, True)
        self._detection_message = message
        self._detection_message_token = self._actions.state.context
        message.setTitle("Moonraker — local failure detection")
        # The alert's own acknowledgement: it repeats while nobody
        # acknowledges it (bounded per print), and this button is the
        # direct way to stop that without hunting for the panel.
        message.addAction("detectionAcknowledge", "Acknowledge", "",
                          "Acknowledge this failure-detection alert")
        message.pyQtActionTriggered.connect(self._on_detection_alert_action)
        message.show()

    def _on_detection_alert_action(self, message, action_id) -> None:
        if (action_id != "detectionAcknowledge" or message is not self._detection_message
                or self._detection_message_token != self._actions.state.context):
            return
        self.acknowledgeDetectionAlert()
        if not self.detectionAlertPending:
            self._hide_detection_message()

    def _hide_detection_message(self):
        if self._detection_message is not None:
            try:
                self._detection_message.hide()
            except Exception:
                pass
        self._detection_message = None
        self._detection_message_token = None

    def _detection_effect(self, effect):
        if effect.kind == "hide":
            self._hide_detection_message()
        elif effect.kind == "persist":
            return (hasattr(self._store, "set_machine_state")
                    and self._store.set_machine_state(effect.context[0], {"detectionActions": effect.record}))
        elif effect.kind == "refresh_run":
            self._run.ensure(force=True)
            return effect.context == self._detection_context(), self._run.fresh(2)
        elif effect.kind == "pause_gate":
            observation = getattr(self._data, "observation", None)
            verdict = can_pause(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
            return verdict.mode, verdict.reason
        elif effect.kind == "pause":
            return self._commands.send("Pause", "printer/print/pause", command_id=effect.command_id)
        elif effect.kind == "status":
            self._commands.report_status(effect.message)
        elif effect.kind == "changed":
            self.detectionChanged.emit()
        elif effect.kind == "retain":
            self._retain_detection_evidence(effect.level, effect.context)
        elif effect.kind == "notify":
            self._notify_detection(effect.level, False)

    def _handle_detection_action(self, context, level, now):
        config = self._config()
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        self._actions.consider(
            context=context, current_context=self._detection_context(),
            run_identity=self._run.identity, notice_identity=self._run.notice_identity,
            potential_saved_mute=self._potential_saved_mute(), level=level, now=now,
            pause_enabled=config.detection_pause_enabled, notify_enabled=config.detection_notify_enabled,
            attestation_fresh=self._run.fresh(2), print_state=stats.get("state"))

    def _on_detection_pause_command(self, event):
        self._actions.pause_result(event, run_identity=self._run.identity,
            run_job=self._run.context[2] if self._run.context is not None else None,
            current_job=getattr(self._print_state(), "job_key", None))

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionNotifyEnabled(self):
        return self._config().detection_notify_enabled

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionEnabled(self):
        return self._config().detection_enabled

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionReady(self):
        return self._detection is not None and self._detection.ready

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionGlobalEnabled(self):
        return self._detection is not None and self._detection.ready and self._detection.enabled

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionCameraReady(self):
        return bool(getattr(self._camera, "url", "") and self._camera_recovery.stream_enabled
                    and not self._config().camera_disabled and self._data.active and self._data.connection_state == "yes")

    @pyqtProperty(int, notify=detectionChanged)
    def detectionWarningThreshold(self):
        return self._config().detection_warning_threshold

    @pyqtProperty(int, notify=detectionChanged)
    def detectionFailureThreshold(self):
        return self._config().detection_failure_threshold

    @pyqtProperty(int, notify=detectionChanged)
    def detectionSafeSeconds(self):
        return self._config().detection_safe_seconds

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionPauseEnabled(self):
        return self._config().detection_pause_enabled

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionAlertPending(self):
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        return self._actions.pending_alert(print_state=stats.get("state"),
                                          identity=self._run.identity or self._run.notice_identity)

    @pyqtProperty(str, notify=detectionChanged)
    def detectionAlertLevel(self):
        """The level of the alert standing unacknowledged ("warning",
        "failure", or ""): the pending indicators colour by it rather
        than guessing a severity."""
        if not self.detectionAlertPending:
            return ""
        return self._actions.state.alert_level

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionPauseRearmable(self):
        if not (self.detectionGlobalEnabled and self.detectionEnabled and self.detectionPauseEnabled):
            return False
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        job = getattr(self._print_state(), "job_key", None)
        printer = self._detection_printer_id()
        if stats.get("state") not in ("printing", "paused") or job is None or printer is None:
            return False
        if self._run.identity is None:
            return False
        print_key = (printer, self._run.identity)
        if print_key == self._actions.state.context:
            return (self._actions.state.paused or self._actions.state.pause_uncertain)
        saved = self._actions.state.saved
        return (isinstance(saved, dict) and saved.get("run") == self._run.identity.as_dict()
                and any(saved.get(key) is True for key in ("paused", "pausePending", "pauseUncertain")))

    @pyqtSlot()
    def rearmDetectionPause(self):
        if not self.detectionPauseRearmable:
            self._commands.report_status("Re-arm unavailable: enable automatic pause during an active print with a used pause latch")
            self._publish()
            return
        self._actions.sync((self._detection_printer_id(), self._run.identity), time.time())
        if not self._actions.rearm(self._run.identity):
            self._publish()
            return
        self._invalidate_detection()
        self._commands.report_status("Automatic pause re-armed for this print")
        self.detectionChanged.emit()
        self._schedule_publish()

    @pyqtSlot(bool)
    def setDetectionNotifyEnabled(self, enabled):
        self._set_detection_action("detection_notify_enabled", enabled)

    @pyqtSlot(bool)
    def setDetectionEnabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Detection enablement must be a checkbox value")
        if enabled and (not self.detectionGlobalEnabled or not self.detectionCameraReady):
            self._commands.report_status("Select a connected camera and set up local detection before enabling")
            self._publish()
            return
        self._set_detection_config(detection_enabled=enabled)

    @pyqtSlot(int, int)
    def setDetectionThresholds(self, warning, failure):
        if (type(warning) is not int or type(failure) is not int
                or not 0 <= warning < failure <= 100):
            raise ValueError("Detection thresholds must be ordered integer percentages")
        self._set_detection_config(
            detection_warning_threshold=warning, detection_failure_threshold=failure)

    @pyqtSlot(int)
    def setDetectionSafeSeconds(self, seconds):
        if type(seconds) is not int or not 0 <= seconds <= 900 or seconds % 10:
            raise ValueError("Detection safe period must be 0 to 900 seconds in ten-second steps")
        self._set_detection_config(detection_safe_seconds=seconds)

    @pyqtSlot(bool)
    def setDetectionPauseEnabled(self, enabled):
        self._set_detection_action("detection_pause_enabled", enabled)

    def _set_detection_action(self, field, enabled):
        if type(enabled) is not bool:
            raise ValueError("Detection action must be a checkbox value")
        self._set_detection_config(**{field: enabled})

    def _set_detection_config(self, **changes):
        if not self.detectionReady:
            self._commands.report_status("Local detection is not ready; set it up in Detection settings")
            self._publish()
            return
        config = self._config()
        if not self.detectionCameraReady and changes.get("detection_enabled") is not False:
            self._commands.report_status("Detection is suspended: enable a usable camera first")
            self._publish()
            return
        if "detection_enabled" not in changes and not config.detection_enabled:
            self._commands.report_status("Enable failure detection for this printer before changing its controls")
            self._publish()
            return
        if all(getattr(config, field) == value for field, value in changes.items()):
            return
        if self._apply_config(replace(config, **changes)) is False:
            self._commands.report_status("Could not save failure-detection controls for this printer")
            self._publish()
            return
        self._detection_values()
        self._publish()
        self.detectionChanged.emit()

    @pyqtSlot()
    def acknowledgeDetectionAlert(self):
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        if (not self.detectionEnabled or not self.detectionAlertPending or not self._data.active
                or self._data.connection_state != "yes" or not self._detection.enabled or self._actions.state.muted
                or self._run.context is None or stats.get("state") not in ("printing", "paused")
                or getattr(self._print_state(), "job_key", None) != self._run.context[2]):
            return
        self._actions.acknowledge(time.time(), self._run.identity)

    def acceptDetectionFrame(self, image, captured_at=None, source_url=None):
        self._detection_values()
        if source_url is not None:
            reloads = [value for name, value in parse_qsl(urlsplit(str(source_url)).query,
                       keep_blank_values=True) if name == "mpf_reload"]
            if any(value != str(self._camera_recovery.nonce) for value in reloads):
                return
            if source_key(source_url) != self._detection_source_key():
                return
        context = self._detection_context()
        if context is not None:
            self._detection.sample(image, context, captured_at, self._active_detection_regions())

    @pyqtProperty(float, notify=detectionChanged)
    def detectionSensitivity(self):
        return self._config().detection_sensitivity

    @pyqtSlot(float)
    def setDetectionSensitivity(self, value):
        if type(value) not in (int, float) or not .8 <= value <= 1.2 or not isfinite(value):
            raise ValueError("Detection sensitivity must be between 0.8 and 1.2")
        self._set_detection_config(detection_sensitivity=round(value, 2))

    @pyqtSlot()
    def resetDetectionTuning(self):
        self._set_detection_config(detection_sensitivity=1.0, detection_warning_threshold=38,
                                   detection_failure_threshold=78)

    @pyqtProperty(float, notify=detectionChanged)
    def detectionBaseline(self):
        return self._detection_policy.baseline["mean"]

    @pyqtSlot()
    def resetDetectionBaseline(self):
        self._reset_detection_training(False)

    @pyqtSlot()
    def resetPrinterDetectionTraining(self):
        self._reset_detection_training(True)

    def _reset_detection_training(self, all_cameras):
        if not (self.detectionGlobalEnabled and self.detectionEnabled) or (not all_cameras and not self.detectionCameraReady) or self._detection_editing:
            self._commands.report_status("Enable detection and finish region editing before resetting the camera baseline")
            return
        self._sync_detection_thresholds()
        printer = self._detection_baseline_printer
        camera = self._detection_baseline_camera
        if printer is None or (not all_cameras and camera is None):
            return
        prefix = str(self._detection_camera_id()) + ":"
        baselines = {} if all_cameras else {key: value for key, value in self._detection_baselines.items()
                                            if not key.startswith(prefix)}
        if camera is not None:
            baselines[camera] = {"mean": 0.0, "frames": 0}
        saved_baselines = dict(baselines)
        # Use the same ordered worker lane/key as samples: replace a pending
        # old write, or follow an in-flight one, so it cannot resurrect the mean.
        if not self._queue_detection_io("baseline:" + str(printer),
                lambda: self._store.set_machine_state(printer, {"detectionBaselines": saved_baselines})):
            self._commands.report_status("Camera baseline could not be reset; try again when detection is ready")
            return
        self._detection_baselines = baselines
        self._detection_policy.restore_baseline({"mean": 0.0, "frames": 0})
        self._invalidate_detection()
        self._publish()


    @pyqtProperty(bool, notify=detectionChanged)
    def detectionMuted(self):
        return self._actions.state.muted

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionMuteAvailable(self):
        stats = (self._data.snapshot.core or {}).get("print_stats") or {}
        return self.detectionCameraReady and self.detectionEnabled and self._run.fresh(30) and stats.get("state") in ("printing", "paused") and self._data.active and self._data.connection_state == "yes"

    @pyqtSlot(bool)
    def setDetectionMuted(self, muted):
        if type(muted) is not bool or not self.detectionMuteAvailable:
            return
        if not self._actions.mute(muted, self._run.identity):
            self._commands.report_status("Could not save mute for this print")
            self._publish()
            return
        self._hide_detection_message()
        if not muted:
            self._invalidate_detection()
        self.detectionChanged.emit()
        self._schedule_publish()

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionShowBoxes(self):
        return self._config().detection_show_boxes

    @pyqtSlot(bool)
    def setDetectionShowBoxes(self, enabled):
        self._set_detection_config(detection_show_boxes=enabled)

    @pyqtProperty(QVariant, notify=detectionChanged)
    def detectionBoxes(self):
        if self._detection_context() is None or self._detection_result_at is None \
                or not 0 <= self._now() - self._detection_result_at <= FRESH_SECONDS:
            return []
        return [box.as_dict() for box in self._detection_boxes]

    @pyqtProperty(float, notify=detectionChanged)
    def detectionAnalysisAge(self):
        return max(0.0, self._now() - self._detection_result_at) if self._detection_result_at is not None else -1.0

    @pyqtProperty(QVariant, notify=detectionChanged)
    def detectionRegions(self):
        regions = self._active_detection_regions()
        return [[list(point) for point in region] for region in regions] if regions is not None else []

    @pyqtProperty(str, notify=detectionChanged)
    def detectionRegionCamera(self):
        return self._detection_camera_id() or ""

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionRegionsValid(self):
        return self._active_detection_regions() is not None

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionEditingRegions(self):
        return self._detection_editing

    @pyqtSlot(bool)
    def setDetectionEditingRegions(self, editing):
        if editing and not self.detectionCameraReady:
            return
        self._detection_editing = editing
        self._detection_edit_camera = self._detection_camera_id() if editing else None
        self._invalidate_detection()
        self._schedule_publish()

    @pyqtSlot(QVariant, result=str)
    def validateDetectionRegions(self, value):
        try:
            raw = value.toVariant() if hasattr(value, "toVariant") else value
            validate_regions(raw)
            return ""
        except (TypeError, ValueError, OverflowError) as exc:
            return str(exc)

    @pyqtSlot(QVariant, result=bool)
    def saveDetectionRegions(self, value):
        camera = self._detection_camera_id()
        if not camera or camera != self._detection_edit_camera or not self._detection_editing:
            return False
        try:
            raw = value.toVariant() if hasattr(value, "toVariant") else value
            regions = validate_regions(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            self._commands.report_status(str(exc))
            self._publish()
            return False
        saved = dict(self._config().detection_regions)
        saved.pop("invalid", None)
        if camera not in saved and len(saved) >= 32:
            self._commands.report_status("Monitored regions already contain 32 cameras")
            return False
        saved[camera] = regions
        if self._apply_config(replace(self._config(), detection_regions=saved)) is False:
            self._commands.report_status("Could not save monitored regions")
            self._publish()
            return False
        self._detection_editing = False
        self._invalidate_detection()
        self._sync_detection_thresholds()
        self._schedule_publish()
        return True

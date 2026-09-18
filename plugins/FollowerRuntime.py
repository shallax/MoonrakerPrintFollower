"""Composition root. This module constructs dependencies; it implements no domain policy.

4.5.0: the persistence facade is constructed HERE — one instance per
process (the panel's H5: the in-memory document makes a single owner
mandatory), with Cura's SaveFile as the injected atomic write and a
lock on the shared documents; the per-machine state shards have a
single writer by construction and take none. The one-shot migration
runs from Cura's initializationFinished (B1)."""

from __future__ import annotations

import os
from UM.Resources import Resources

from .BedMeshPresenter import BedMeshPresenter
from .CuraIntegration import CuraIntegration
from .GCodeIndex import PersistentIndexCache
from .GCodeIndexService import GCodeIndexService
from .MigrationNotice import MigrationNotice
from .MoonrakerClient import MoonrakerClient
from .PauseController import PauseController
from .PluginPersistence import OLD_STATE_FILE_NAME, PluginPersistence
from .PreviewFollower import PreviewFollower
from .PreviewMotion import PreviewMotion
from .PreviewPresentation import PreviewPresentation
from .PrintCoordinator import PrintCoordinator
from .PrinterBinding import PrinterBinding
from .RemoteFileService import RemoteFileService
from .FileDownload import FileDownload
from .WhatsNew import should_show as whats_new_should_show


def _savefile_write(path, text):
    """The injected atomic write (M8): Cura's SaveFile commits with a
    same-directory temp file, an fsync and an flock — the plugin's
    JSON gains the host's own durability story. The folder is
    recreated on demand: a config folder deleted by hand must not
    turn every subsequent save into a silent no-op."""
    try:
        from UM.SaveFile import SaveFile
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with SaveFile(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True
    except Exception:
        return False


def _raise_migration_toast(record):
    """The UM.Message toast (the UX spec): flavour A carries the
    backup-folder action, flavour B has no backup to open. The action
    handler is a bound function, never a lambda — Uranium's Signal
    holds plain functions weakly (the MoonrakerOutputDevice
    precedent)."""
    try:
        from UM.Message import Message
    except Exception:
        return
    backup = str(record.get("backupName") or "")
    if record.get("backupWritten") and backup:
        message = Message(
            ("Moonraker Print Follower could not move your settings into its new files, so it started with them empty. "
             "Cura's configuration file was copied to %s before anything was removed. To go back: close Cura, reinstall "
             "the previous version of the plugin, then copy that file over cura.cfg in Cura's configuration folder.") % backup,
            0, False,
        )
        message.addAction("show_backup_folder", "Show backup folder", "", "Open Cura's configuration folder")

        def open_folder(_message, _action):
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            from UM.Resources import Resources
            QDesktopServices.openUrl(QUrl.fromLocalFile(Resources.getConfigStoragePath()))

        message.actionTriggered.connect(open_folder)
    else:
        message = Message(
            ("Moonraker Print Follower could not move your settings into its new files, so it started with them empty. "
             "Nothing was removed — your existing Cura configuration is untouched. The move is tried again the next "
             "time Cura starts."),
            0, False,
        )
    message.setTitle("Moonraker — settings did not carry over")
    message.show()


def _state_lock(state_root):
    """The cross-process lock for the SHARED documents (the settings
    file and the global chrome — E9/H2), Cura's own lock-file recipe:
    the per-machine shards have a single writer by construction and
    need none."""
    try:
        from UM.LockFile import LockFile
        return LockFile(
            os.path.join(state_root, "moonraker.lock"),
            wait_msg="Waiting for the Moonraker settings lock...",
        )
    except Exception:
        return None


class FollowerRuntime:
    def __init__(self, application, parent):
        self.client = MoonrakerClient(parent)
        # One plugin-owned folder for everything (the 2026-09-18
        # ruling): the settings document, the global state document
        # and the per-machine shards all live under it — one entry
        # in the config dir to browse, one folder to remove.
        persistence_root = Resources.getStoragePath(Resources.Preferences, "MoonrakerPrintFollower")
        os.makedirs(os.path.join(persistence_root, "machines"), exist_ok=True)
        self.persistence = PluginPersistence(
            settings_path=os.path.join(persistence_root, "settings.json"),
            state_global_path=os.path.join(persistence_root, "state.json"),
            state_machine_dir=os.path.join(persistence_root, "machines"),
            save=_savefile_write,
            lock=lambda: _state_lock(persistence_root),
        )
        self.binding = PrinterBinding(
            application, self.client, self.persistence,
            cura_cfg_path=os.path.join(Resources.getConfigStoragePath(), "cura.cfg"),
            old_state_path=Resources.getStoragePath(Resources.Preferences, OLD_STATE_FILE_NAME),
            parent=parent,
        )
        self.cura = CuraIntegration(application, parent)
        self.files = RemoteFileService(self.client.transport, parent)
        self.file_download = FileDownload(self.files, self.cura, parent,
            active_identity=lambda: self.binding.identity,
            session_generation=lambda: self.client.session.generation)
        # A session invalidation cancels every in-flight one-shot
        # download — the unconditional entry point the bind/close
        # transitions cannot provide while idle-browsing.
        self.client.sessionInvalidated.connect(self.files.cancel_one_shots)
        cache_dir = os.path.join(Resources.getCacheStoragePath(), "MoonrakerPrintFollower")
        cache = PersistentIndexCache(os.path.join(cache_dir, "indexes"))
        self.index = GCodeIndexService(self.files, cache, parent)
        self.preview = PreviewFollower(self.cura)
        # The smoothing CSV trace is an opt-in diagnostic (see INSTRUCTIONS.md
        # "Diagnostics"); it is never written in ordinary operation.
        trace_name = os.environ.get("MOONRAKER_FOLLOWER_SMOOTHING_TRACE")
        trace_path = os.path.join(cache_dir, trace_name) if trace_name else None
        self.motion = PreviewMotion(self.cura, self.preview.remember, parent, trace_path=trace_path)
        self.preview.bind_motion(self.motion)
        self.pauses = PauseController(self.client, parent)
        self.presentation = PreviewPresentation(application, self.cura, parent)
        self.bed_mesh = BedMeshPresenter(application, self.cura, self.presentation, parent,
                                         persistence=self.persistence)
        self.coordinator = PrintCoordinator(client=self.client, binding=self.binding,
            files=self.files, index=self.index, cura=self.cura, preview=self.preview,
            pauses=self.pauses, presentation=self.presentation, bed_mesh=self.bed_mesh, parent=parent)
        self._closed = False
        # The migration notice (the UX spec): the toast raises once
        # per failure, after the What's-New sequence, from ONE
        # plugin-level owner — never per device.
        self.notice = MigrationNotice(
            self.persistence,
            whats_new_gate=lambda: whats_new_should_show(
                (self.persistence.state_global_document() or {}).get("whatsNewSeen") or ""
            ),
            raise_toast=lambda record: _raise_migration_toast(record),
            parent=parent,
        )
        # The one-shot's trigger (B1): the clean must run after Cura's
        # second preference read, never from construction — and
        # saveSettings() only works once Cura has started.
        finished = getattr(application, "initializationFinished", None)
        if finished is not None:
            finished.connect(self.binding.run_persistence_migration)
            finished.connect(self.notice.announce)
        self.binding.start()

    def close(self):
        if self._closed: return
        self._closed = True
        self.binding.close()
        self.coordinator.close()
        self.pauses.close()
        self.bed_mesh.close()
        self.presentation.close()
        self.motion.close()
        self.cura.close()
        self.index.close()
        self.file_download.close()  # in-flight downloads retire BEFORE the files root goes
        self.files.close()
        self.client.transport.close()  # the manager's pooled sockets close with the plugin

"""Coverage for the runtime's small modules: the Cura view/identity
adapters, the download stream and the file-manager lane built on it, the
upload writer's lease, the follow state machine, the lifecycle bridge and
the composition root.

Stdlib-only modules (CuraAdapter, DownloadStream, CuraOutputWriter,
FollowController, CuraLifecycleBridge) are exercised directly. FileDownload
and FollowerRuntime sit behind Qt/UM imports, so they run inside
tests/qt_runtime_support.py's real-Qt harness — the same stub recipe the
suite's other Qt suites use; runtime() registers the UM modules
FollowerRuntime imports at load.

Leftover lines in these seven modules: none — every statement runs here.
Cura's own GCodeWriter/UFPWriter and the SaveFile/LockFile/Message host
objects are doubles, so those seams exercise the plugin's side of the
contract, not Cura's.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from qt_runtime_support import QT_AVAILABLE, ScriptedSocket, ScriptedTransport, runtime

from plugins import CuraOutputWriter as cura_output_writer
from plugins.CuraAdapter import (active_machine_identity, apply_preview_decision,
                                 preview_current_layer, preview_current_path, preview_max_paths,
                                 preview_minimum_layer, preview_minimum_path, reset_preview_layer_data,
                                 set_preview_minimum_path, set_preview_path)
from plugins.CuraLifecycleBridge import CuraLifecycleBridge
from plugins.CuraOutputWriter import CuraOutputWriter
from plugins.DownloadStream import DownloadOperation, DownloadTarget
from plugins.FollowController import FollowController, FollowMode, FollowState, decide_layers
from plugins.PrinterConfig import PrinterConfig

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal


def wait_for(predicate, timeout=2.0):
    """Poll a predicate from the test thread while a writer thread works."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# --------------------------------------------------------------------------
# CuraAdapter
# --------------------------------------------------------------------------

class MetadataStack:
    """A Cura container stack that only speaks the metadata API."""

    def __init__(self, entries=None):
        self.entries = entries or {}

    def getMetaDataEntry(self, key, default=""):
        return self.entries.get(key, default)


class PreviewViewDouble:
    """The SimulationView surface the preview helpers touch."""

    def __init__(self):
        self.layer = 4
        self.minimum = 1
        self.path = 2.5
        self.resets = 0
        self.path_sets = []
        self.minimum_sets = []
        self.minimum_path_sets = []

    def getCurrentLayer(self): return self.layer
    def getMinimumLayer(self): return self.minimum
    def getCurrentPath(self): return self.path
    def getMinimumPath(self): return 0
    def getMaxPaths(self): return 12
    def setLayer(self, value): self.layer = value
    def setMinimumLayer(self, value): self.minimum_sets.append(value)
    def setPath(self, value): self.path_sets.append(value)
    def setMinimumPath(self, value): self.minimum_path_sets.append(value)
    def resetLayerData(self): self.resets += 1


class CuraAdapterIdentityTests(unittest.TestCase):
    """The identity ladder: the global stack is the only authority, every
    getter is optional, and an unreadable stack must never raise into
    startup."""

    def test_a_stack_lookup_that_raises_reports_an_unknown_identity(self):
        class App:
            def getGlobalContainerStack(self):
                raise RuntimeError("half-initialized CuraApplication")

        self.assertEqual(active_machine_identity(App()), ("unknown", "Unknown Cura printer"))

    def test_a_metadata_only_stack_supplies_the_identity(self):
        # No getId: the metadata entry is the next rung of the ladder.
        stack = MetadataStack({"id": "meta-42", "name": "Printer M"})
        self.assertEqual(active_machine_identity(SimpleNamespace(getGlobalContainerStack=lambda: stack)),
                         ("meta-42", "Printer M"))

    def test_an_id_getter_that_raises_still_reads_the_metadata_entry(self):
        class Stack:
            def getId(self):
                raise RuntimeError("no id yet")
            def getMetaDataEntry(self, key, default=""):
                return "meta-7" if key == "id" else default
            def getName(self):
                return "Printer 7"

        self.assertEqual(active_machine_identity(SimpleNamespace(getGlobalContainerStack=lambda: Stack())),
                         ("meta-7", "Printer 7"))

    def test_a_stack_without_any_identity_falls_back_to_the_object_id(self):
        class Stack:
            def getId(self):
                return ""
            def getMetaDataEntry(self, key, default=""):
                return default
            def getName(self):
                return ""

        stack = Stack()
        machine_id, name = active_machine_identity(SimpleNamespace(getGlobalContainerStack=lambda: stack))
        self.assertEqual(machine_id, str(id(stack)))
        self.assertEqual(name, machine_id)  # nothing else to name it by

    def test_a_metadata_read_that_raises_keeps_the_id_as_the_name(self):
        class Stack:
            def getId(self):
                return "machine-9"
            def getName(self):
                return ""
            def getMetaDataEntry(self, *args):
                raise RuntimeError("metadata registry gone")

        self.assertEqual(active_machine_identity(SimpleNamespace(getGlobalContainerStack=lambda: Stack())),
                         ("machine-9", "machine-9"))


class CuraAdapterPreviewTests(unittest.TestCase):
    """The preview helpers stay behind the view's public surface and degrade
    to None instead of raising into a follow tick."""

    def test_typed_reads_read_through_the_public_getters(self):
        view = PreviewViewDouble()
        self.assertEqual(preview_current_layer(view), 4)
        self.assertEqual(preview_minimum_layer(view), 1)
        self.assertAlmostEqual(preview_current_path(view), 2.5)
        self.assertEqual(preview_minimum_path(view), 0)
        self.assertEqual(preview_max_paths(view), 12)

    def test_a_broken_or_absent_view_reads_as_none(self):
        self.assertIsNone(preview_current_layer(None))
        self.assertIsNone(preview_max_paths(object()))

        class Exploding:
            def getCurrentPath(self):
                raise RuntimeError("view torn down")

        self.assertIsNone(preview_current_path(Exploding()))

    def test_writes_are_guarded_for_views_that_cannot_take_them(self):
        view = PreviewViewDouble()
        set_preview_path(view, 12.0)
        set_preview_minimum_path(view, 3)
        self.assertEqual(view.path_sets, [12.0])
        self.assertEqual(view.minimum_path_sets, [3])
        set_preview_path(None, 1.0)
        set_preview_minimum_path(object(), 1)
        reset_preview_layer_data(object())  # no resetLayerData on the view

    def test_apply_preview_decision_sets_the_floor_only_where_the_view_supports_it(self):
        view = PreviewViewDouble()
        apply_preview_decision(view, 8, minimum_layer=2)
        self.assertEqual(view.layer, 8)
        self.assertEqual(view.minimum_sets, [2])
        apply_preview_decision(view, 9)
        self.assertEqual(view.layer, 9)
        self.assertEqual(view.minimum_sets, [2])  # no floor asked for: untouched

        class Bare:
            def __init__(self):
                self.layer = None
            def setLayer(self, value):
                self.layer = value

        bare = Bare()
        apply_preview_decision(bare, 5, minimum_layer=2)
        self.assertEqual(bare.layer, 5)

    def test_reset_preview_layer_data_drops_the_current_layers_meshes(self):
        view = PreviewViewDouble()
        reset_preview_layer_data(view)
        self.assertEqual(view.resets, 1)
        reset_preview_layer_data(None)


# --------------------------------------------------------------------------
# DownloadStream
# --------------------------------------------------------------------------

class RecordingTarget:
    """A target double: records the operation's retirement calls."""

    def __init__(self, write_error=None, flush_error=None):
        self.write_error = write_error
        self.flush_error = flush_error
        self.chunks = []
        self.flush_closes = 0
        self.aborts = []

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        self.chunks.append(bytes(data))
        return len(data)

    def flush_close(self):
        self.flush_closes += 1
        if self.flush_error is not None:
            raise self.flush_error

    def abort(self, remove=True):
        self.aborts.append(remove)


class DownloadTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mpfxtest-download-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_target_that_never_opened_closes_without_a_handle(self):
        DownloadTarget(path=os.path.join(self.tmp, "never-opened.bin"), handle=None).flush_close()

    def test_open_writes_and_closes_a_nested_target(self):
        path = os.path.join(self.tmp, "nested", "part.bin")
        target = DownloadTarget.open(path)
        self.assertEqual(target.write(b""), 0)  # an empty chunk is not a write
        self.assertEqual(target.write(bytearray(b"abc")), 3)
        self.assertEqual(target.bytes_written, 3)
        target.flush_close()
        self.assertEqual(pathlib.Path(path).read_bytes(), b"abc")

    def test_abort_removes_the_temp_file_and_keeps_it_on_request(self):
        kept = os.path.join(self.tmp, "kept.bin")
        DownloadTarget.open(kept).abort(remove=False)
        self.assertTrue(os.path.exists(kept))

        dropped = os.path.join(self.tmp, "dropped.bin")
        DownloadTarget.open(dropped).abort()
        self.assertFalse(os.path.exists(dropped))

    def test_abort_tolerates_a_handle_that_will_not_close_and_a_missing_file(self):
        class BrokenHandle:
            def close(self):
                raise OSError("closed from elsewhere")

        target = DownloadTarget(path=os.path.join(self.tmp, "gone.bin"), handle=BrokenHandle())
        target.abort()  # the close fails, and the file was never there
        self.assertFalse(os.path.exists(target.path))

    def test_abort_tolerates_a_path_that_is_not_a_file(self):
        directory = os.path.join(self.tmp, "a-directory")
        os.makedirs(directory)
        handle = open(os.path.join(self.tmp, "throwaway.bin"), "wb")
        DownloadTarget(path=directory, handle=handle).abort()  # IsADirectoryError: the OSError seat
        self.assertTrue(os.path.isdir(directory))


class DownloadOperationTests(unittest.TestCase):
    """The writer thread: byte accounting, the drain crossing, error
    latching and the retirement rules."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mpfxtest-operation-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.operations = []
        self.addCleanup(self.retire)

    def retire(self):
        for operation in self.operations:
            operation.stop()
            writer = operation._writer
            if writer is not None:
                writer.join(timeout=2.0)

    def operation(self, target=None):
        target = DownloadTarget.open(os.path.join(self.tmp, "part.bin")) if target is None else target
        operation = DownloadOperation(target, reply=None, size=0, generation=1, job="job")
        self.operations.append(operation)
        operation.start()
        return operation

    def test_the_writer_writes_its_queue_and_closes_the_target(self):
        path = os.path.join(self.tmp, "part.bin")
        operation = self.operation(DownloadTarget.open(path))
        operation.queue.put(b"abc")
        operation.queue.put(b"de")
        self.assertTrue(wait_for(lambda: operation.written == 5))
        operation.stop()
        operation._writer.join(timeout=2.0)
        self.assertFalse(operation._writer.is_alive())
        self.assertIsNone(operation.writer_error)
        self.assertEqual(pathlib.Path(path).read_bytes(), b"abcde")

    def test_stop_signals_the_writer_exactly_once(self):
        operation = DownloadOperation(RecordingTarget(), reply=None, size=0, generation=1, job="job")
        operation.stop()
        operation.stop()
        self.assertTrue(operation.sentinel_put)
        self.assertEqual(operation.queue.qsize(), 1)  # the second stop added no sentinel
        self.assertIsNone(operation.queue.get())

    def test_the_drain_callback_fires_once_per_above_to_below_crossing(self):
        operation = self.operation(RecordingTarget())
        operation.LOW_WATER_BYTES = 4
        drained = []
        drained_event = threading.Event()
        operation.on_writer_drained = lambda: (drained.append(1), drained_event.set())
        # Backlog 100 bytes over the mark: no crossing yet.
        operation.received = 100
        operation.queue.put(b"a")
        self.assertTrue(wait_for(lambda: operation.written == 1))
        self.assertEqual(drained, [])
        # Below the mark: one crossing, one callback.
        operation.received = 6
        operation.queue.put(b"b")
        self.assertTrue(drained_event.wait(2.0))
        operation.queue.put(b"c")  # already drained: no second callback
        self.assertTrue(wait_for(lambda: operation.written == 3))
        self.assertEqual(drained, [1])

    def test_a_write_failure_is_latched_and_the_target_still_closes(self):
        target = RecordingTarget(write_error=OSError("disk full"))
        operation = self.operation(target)
        operation.queue.put(b"abc")
        operation.stop()
        operation._writer.join(timeout=2.0)
        self.assertIn("disk full", operation.writer_error)
        self.assertEqual(target.flush_closes, 1)

    def test_a_flush_failure_is_latched_and_never_masks_a_write_failure(self):
        operation = self.operation(RecordingTarget(flush_error=OSError("flush failed")))
        operation.stop()
        operation._writer.join(timeout=2.0)
        self.assertIn("flush failed", operation.writer_error)

        both = self.operation(RecordingTarget(write_error=OSError("disk full"),
                                              flush_error=OSError("flush failed")))
        both.queue.put(b"abc")
        both.stop()
        both._writer.join(timeout=2.0)
        self.assertIn("disk full", both.writer_error)  # the first error wins

    def test_the_writer_done_hook_runs_and_a_gone_service_is_swallowed(self):
        operation = self.operation(RecordingTarget())
        done = MagicMock()
        operation.on_writer_done = done
        operation.stop()
        operation._writer.join(timeout=2.0)
        done.assert_called_once_with()

        shutting_down = self.operation(RecordingTarget())
        shutting_down.on_writer_done = MagicMock(side_effect=RuntimeError("service gone"))
        shutting_down.stop()
        shutting_down._writer.join(timeout=2.0)
        self.assertFalse(shutting_down._writer.is_alive())

    def test_abort_retires_the_operation_without_the_gui_thread_closing_the_file(self):
        target = RecordingTarget()
        operation = self.operation(target)
        operation.queue.put(b"data")
        self.assertTrue(wait_for(lambda: operation.written == 4))
        operation.abort()
        operation._writer.join(timeout=2.0)
        self.assertTrue(operation.aborted)
        self.assertEqual(target.aborts, [False])  # the service owns the temp root
        self.assertEqual(target.flush_closes, 0)  # the retired writer never flushes

    def test_abort_swallows_a_target_that_cannot_be_retired(self):
        class Hostile:
            def abort(self, remove=True):
                raise OSError("already gone")

        operation = DownloadOperation(Hostile(), reply=None, size=0, generation=1, job="job")
        operation.abort()
        self.assertTrue(operation.aborted)


# --------------------------------------------------------------------------
# CuraOutputWriter
# --------------------------------------------------------------------------

class WriterPlugin:
    """Cura's GCodeWriter/UFPWriter surface: write(stream, node) -> bool."""

    def __init__(self, accepted=True, information="the writer refused the model"):
        self.accepted = accepted
        self.information = information
        self.calls = []

    def write(self, stream, node):
        self.calls.append((stream.mode, node, getattr(stream, "encoding", None)))
        stream.write(b"G1 X0\n" if "b" in stream.mode else "G1 X0\n")
        return self.accepted

    def getInformation(self):
        return self.information


DEFAULT_INFO = SimpleNamespace(jobName="part", preSliced=False)


class OutputApplication:
    """The CuraApplication surface the writer reaches for."""

    def __init__(self, objects=None, info=DEFAULT_INFO, registry_error=None):
        self.objects = objects or {}
        self.info = info
        self.registry_error = registry_error
        self.requested = []

    def getPrintInformation(self):
        return self.info

    def getPluginRegistry(self):
        if self.registry_error is not None:
            raise self.registry_error

        def getPluginObject(name):
            self.requested.append(name)
            return self.objects.get(name)

        return SimpleNamespace(getPluginObject=getPluginObject)


class CuraOutputWriterTests(unittest.TestCase):
    """Preparation is a lease: a fresh temp directory the caller releases,
    retired on every failure path."""

    def writer(self, application):
        return CuraOutputWriter(application)

    def capture_leases(self):
        """Record each lease directory so failure paths can be checked for
        leftovers."""
        created = []
        real = tempfile.mkdtemp

        def recording(*args, **kwargs):
            created.append(real(*args, **kwargs))
            return created[-1]

        patcher = patch.object(cura_output_writer.tempfile, "mkdtemp", recording)
        patcher.start()
        self.addCleanup(patcher.stop)
        return created

    def test_gcode_preparation_writes_through_curas_writer_into_a_lease(self):
        plugin = WriterPlugin()
        application = OutputApplication({"GCodeWriter": plugin})
        prepared = self.writer(application).prepare(PrinterConfig(), "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "part.gcode")
        self.assertEqual(application.requested, ["GCodeWriter"])
        # utf-8, no newline translation: the file Cura will read is byte-exact.
        self.assertEqual(plugin.calls, [("w", None, "utf-8")])
        self.assertEqual(pathlib.Path(prepared.path).read_text(encoding="utf-8"), "G1 X0\n")
        directory = prepared.directory
        prepared.close()
        self.assertFalse(os.path.exists(directory))

    def test_a_ufp_job_uses_the_binary_writer(self):
        plugin = WriterPlugin()
        application = OutputApplication({"UFPWriter": plugin})
        prepared = self.writer(application).prepare(PrinterConfig(output_format="ufp"), "part.ufp")
        self.addCleanup(prepared.close)
        self.assertEqual(application.requested, ["UFPWriter"])
        self.assertEqual(prepared.filename, "part.ufp")
        self.assertEqual(plugin.calls, [("wb", None, None)])

    def test_a_presliced_or_absent_job_degrades_to_gcode(self):
        plugin = WriterPlugin()
        application = OutputApplication({"GCodeWriter": plugin}, info=SimpleNamespace(jobName="part", preSliced=True))
        prepared = self.writer(application).prepare(PrinterConfig(output_format="ufp"), "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(application.requested, ["GCodeWriter"])

        application = OutputApplication({"GCodeWriter": plugin}, info=None)
        prepared = self.writer(application).prepare(PrinterConfig(output_format="ufp"), "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(application.requested, ["GCodeWriter"])
        self.assertEqual(prepared.filename, "part.gcode")  # no job name: the filename stands

    def test_the_name_is_sanitised_before_it_becomes_a_path(self):
        application = OutputApplication({"GCodeWriter": WriterPlugin()})
        writer = self.writer(application)

        # The upload's own name is what Cura will show, never a directory.
        prepared = writer.prepare(PrinterConfig(), "/tmp/elsewhere/part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "part.gcode")
        self.assertEqual(os.path.dirname(prepared.path), prepared.directory)

        # An empty job name falls back to a usable one.
        blank = OutputApplication({"GCodeWriter": WriterPlugin()}, info=SimpleNamespace(jobName="", preSliced=False))
        prepared = self.writer(blank).prepare(PrinterConfig(), None)
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "print.gcode")

        # The suffix strip is case-insensitive, so no double extension escapes.
        prepared = writer.prepare(PrinterConfig(), "PART.GCODE")
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "PART.gcode")

    def test_the_translate_table_rewrites_the_name_only_when_it_fits(self):
        config = PrinterConfig(filename_translate_input="AB", filename_translate_output="ab",
                               filename_translate_remove="X")
        application = OutputApplication({"GCodeWriter": WriterPlugin()})
        prepared = self.writer(application).prepare(config, "AXXB.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "ab.gcode")  # A->a, B->b, X removed

        # Unequal lengths cannot form a table: the name passes through.
        ragged = PrinterConfig(filename_translate_input="ABC", filename_translate_output="ab")
        prepared = self.writer(application).prepare(ragged, "ABC.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(prepared.filename, "ABC.gcode")

    def test_an_unsafe_name_is_refused_before_any_lease_exists(self):
        created = self.capture_leases()
        application = OutputApplication({"GCodeWriter": WriterPlugin()})
        with self.assertRaises(ValueError):
            self.writer(application).prepare(PrinterConfig(), "part:bad.gcode")
        self.assertEqual(created, [])  # nothing to clean up

    def test_a_missing_writer_plugin_removes_the_lease_directory(self):
        created = self.capture_leases()
        application = OutputApplication({})
        with self.assertRaises(RuntimeError) as caught:
            self.writer(application).prepare(PrinterConfig(), "part.gcode")
        self.assertIn("unavailable", str(caught.exception))
        self.assertFalse(os.path.exists(created[0]))

    def test_a_writer_that_refuses_the_model_reports_its_information(self):
        created = self.capture_leases()
        refusing = WriterPlugin(accepted=False, information="No G-code was produced")
        application = OutputApplication({"GCodeWriter": refusing})
        with self.assertRaises(RuntimeError) as caught:
            self.writer(application).prepare(PrinterConfig(), "part.gcode")
        self.assertEqual(str(caught.exception), "No G-code was produced")
        self.assertFalse(os.path.exists(created[0]))

    def test_a_registry_failure_removes_the_lease_directory(self):
        created = self.capture_leases()
        application = OutputApplication(registry_error=RuntimeError("no plugin registry"))
        with self.assertRaises(RuntimeError):
            self.writer(application).prepare(PrinterConfig(), "part.gcode")
        self.assertFalse(os.path.exists(created[0]))


# --------------------------------------------------------------------------
# FollowController
# --------------------------------------------------------------------------

class FollowControllerTests(unittest.TestCase):
    """Intent precedence: an explicit user pause outranks the remote state,
    and a Cura suspend outranks a remote pause."""

    def test_disabled_outranks_every_latched_reason(self):
        controller = FollowController()
        controller.set_connection(False, connecting=True, error="connect failed")
        self.assertEqual(controller.state, FollowState.DISABLED)
        controller.set_enabled(True)
        self.assertEqual(controller.state, FollowState.ERROR)

    def test_an_error_outranks_the_connection_states_until_the_next_report(self):
        controller = FollowController()
        controller.set_enabled(True)
        controller.set_connection(False, connecting=True)
        self.assertEqual(controller.state, FollowState.CONNECTING)
        controller.set_connection(False, connecting=True, error="refused")
        self.assertEqual(controller.state, FollowState.ERROR)
        controller.set_connection(False, connecting=True, error="")
        self.assertEqual(controller.state, FollowState.CONNECTING)  # the next report clears it
        controller.set_connection(True)
        self.assertEqual(controller.state, FollowState.IDLE)

        # The all-clear also comes from the user's resume.
        controller.pause_by_user("layer slider")
        controller.set_connection(True, error="transient")
        self.assertEqual(controller.state, FollowState.ERROR)
        controller.resume()
        self.assertEqual(controller.state, FollowState.IDLE)

    def test_disabling_clears_the_user_pause_and_the_error(self):
        controller = FollowController()
        controller.set_enabled(True)
        controller.set_connection(True)
        controller.pause_by_user("layer slider")
        controller.set_connection(True, error="transient")
        self.assertEqual(controller.state, FollowState.ERROR)
        controller.set_enabled(False)
        self.assertEqual(controller.state, FollowState.DISABLED)
        self.assertFalse(controller.user_paused)
        self.assertEqual(controller.error, "")
        controller.set_enabled(True)
        controller.set_connection(True)
        self.assertEqual(controller.state, FollowState.IDLE)

    def test_pause_and_resume_round_trip_carries_the_reason(self):
        controller = FollowController()
        controller.set_enabled(True)
        controller.set_connection(True)
        controller.set_remote_state("printing")
        controller.pause_by_user()
        self.assertEqual(controller.user_pause_reason, "manual")
        self.assertEqual(controller.state, FollowState.USER_OVERRIDE)
        self.assertFalse(controller.may_write_preview)
        controller.resume()
        self.assertEqual(controller.state, FollowState.FOLLOWING)
        self.assertTrue(controller.may_write_preview)
        controller.pause_by_user("")
        self.assertEqual(controller.user_pause_reason, "manual")  # blank reasons are not kept

    def test_a_cura_suspend_outranks_a_remote_pause(self):
        controller = FollowController()
        controller.set_enabled(True)
        controller.set_connection(True)
        controller.set_remote_state("paused")
        self.assertEqual(controller.state, FollowState.REMOTE_PAUSED)
        self.assertTrue(controller.may_write_preview)
        controller.set_cura_suspended(True)
        self.assertEqual(controller.state, FollowState.CURA_SUSPENDED)
        self.assertFalse(controller.may_write_preview)
        controller.set_cura_suspended(False)
        self.assertEqual(controller.state, FollowState.REMOTE_PAUSED)

    def test_a_reconnected_idle_printer_is_idle_not_following(self):
        controller = FollowController()
        controller.set_enabled(True)
        controller.set_connection(True)
        controller.set_remote_state("printing")
        self.assertEqual(controller.state, FollowState.FOLLOWING)
        controller.set_remote_state("complete")
        self.assertEqual(controller.state, FollowState.IDLE)
        controller.set_remote_state("")
        self.assertEqual(controller.state, FollowState.IDLE)
        controller.set_connection(False)
        self.assertEqual(controller.state, FollowState.DISCONNECTED)

    def test_the_follow_modes_place_the_layers_they_promise(self):
        exact = decide_layers(10, 50, FollowMode.EXACT.value)
        self.assertEqual((exact.current_layer, exact.minimum_layer, exact.follow_path), (10, 0, True))

        completed = decide_layers(10, 50, FollowMode.COMPLETED.value)
        self.assertEqual((completed.current_layer, completed.minimum_layer, completed.follow_path), (9, 0, False))

        lookahead = decide_layers(10, 50, FollowMode.LOOKAHEAD.value)
        self.assertEqual((lookahead.current_layer, lookahead.minimum_layer, lookahead.follow_path), (11, 0, False))

        window = decide_layers(10, 50, FollowMode.WINDOW.value, window_radius=3)
        self.assertEqual((window.current_layer, window.minimum_layer, window.follow_path), (13, 7, False))

    def test_the_decision_clamps_to_the_print_and_defaults_unknown_modes(self):
        # A remote layer past the last one, or below zero, cannot leave the print.
        self.assertEqual(decide_layers(99, 5, "exact").current_layer, 5)
        self.assertEqual(decide_layers(-4, 5, "exact").current_layer, 0)
        self.assertEqual(decide_layers(3, -9, "exact").current_layer, 0)  # a negative max clamps to 0
        self.assertEqual(decide_layers(0, 5, "completed").current_layer, 0)  # never negative
        self.assertEqual(decide_layers(5, 5, "lookahead").current_layer, 5)

        # An unknown mode is not a crash: the follower keeps the print exact.
        unknown = decide_layers(4, 5, "sideways")
        self.assertEqual((unknown.current_layer, unknown.minimum_layer, unknown.follow_path), (4, 0, True))

        # A window is at least one layer wide in each direction.
        tight = decide_layers(0, 5, "window", window_radius=0)
        self.assertEqual((tight.current_layer, tight.minimum_layer), (1, 0))


# --------------------------------------------------------------------------
# CuraLifecycleBridge
# --------------------------------------------------------------------------

class CuraLifecycleBridgeTests(unittest.TestCase):
    def test_a_fresh_bridge_reports_the_startup_generation(self):
        bridge = CuraLifecycleBridge()
        self.assertEqual(bridge.generation, 0)
        self.assertEqual(bridge.token(), 0)
        self.assertTrue(bridge.is_current(0))
        self.assertEqual(bridge.state.reason, "startup")

    def test_invalidate_bumps_the_generation_and_records_the_reason(self):
        bridge = CuraLifecycleBridge()
        token = bridge.token()
        self.assertEqual(bridge.invalidate("scene replaced"), 1)
        self.assertEqual(bridge.generation, 1)
        self.assertEqual(bridge.state.reason, "scene replaced")
        self.assertFalse(bridge.is_current(token))
        self.assertTrue(bridge.is_current(bridge.token()))
        bridge.invalidate("")
        self.assertEqual(bridge.state.reason, "Cura lifecycle changed")  # a blank reason is not kept

    def test_the_generation_setter_clamps_to_a_non_negative_int(self):
        bridge = CuraLifecycleBridge()
        bridge.generation = -5
        self.assertEqual(bridge.generation, 0)
        bridge.generation = "7"
        self.assertEqual(bridge.generation, 7)
        bridge.generation = None
        self.assertEqual(bridge.generation, 0)

    def test_record_reason_leaves_the_generation_alone(self):
        bridge = CuraLifecycleBridge()
        bridge.invalidate("scene replaced")
        bridge.record_reason("machine switched")
        self.assertEqual(bridge.generation, 1)
        self.assertEqual(bridge.state.reason, "machine switched")
        bridge.record_reason("")
        self.assertEqual(bridge.state.reason, "Cura lifecycle changed")

    def test_is_current_compares_against_the_live_generation(self):
        bridge = CuraLifecycleBridge()
        bridge.invalidate("scene replaced")
        self.assertTrue(bridge.is_current("1"))  # tokens arriving from QML are strings
        self.assertFalse(bridge.is_current("0"))

    def test_guarded_runs_the_callback_only_while_the_token_is_current(self):
        bridge = CuraLifecycleBridge()
        observed = []
        token = bridge.token()
        bridge.invalidate("scene replaced")
        self.assertFalse(bridge.guarded(token, lambda: observed.append("stale")))
        self.assertEqual(observed, [])
        self.assertTrue(bridge.guarded(bridge.token(), lambda: observed.append("current")))
        self.assertEqual(observed, ["current"])


# --------------------------------------------------------------------------
# FileDownload (Qt)
# --------------------------------------------------------------------------

if QT_AVAILABLE:
    class CuraDouble(QObject):
        loadFailed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.loads = []

        def load(self, lease):
            self.loads.append(lease)

    class DownloadHandle:
        """The one-shot handle's contract: one terminal, cancellable
        with the caller's reason, and a dead handle delivers none."""

        def __init__(self, done=False, on_ready=None):
            self.cancelled = 0
            self.reasons = []
            self.done = done
            self._on_ready = on_ready

        def cancel(self, reason="The download was cancelled"):
            self.cancelled += 1
            self.reasons.append(reason)
            if self._on_ready is not None and not self.done:
                self.done = True
                self._on_ready(None, reason)

        def finish(self, path=None, error=None):
            """The stream's own terminal, as the real lane delivers it."""
            if self.done:
                return
            self.done = True
            if self._on_ready is not None:
                self._on_ready(path, error)

    class GatedSource:
        """The copy's source handle, gated at its first read (RC-04):
        `released_by_loop` is True only when the app's own event loop
        ran the release — a timeout means the copy held the owner
        thread for the whole wait, which is the defect."""

        GATE_TIMEOUT = 5.0

        def __init__(self, handle, entered, release):
            self._handle = handle
            self._entered = entered
            self._release = release
            self._gated = False
            self.released_by_loop = False

        def read(self, size=-1):
            data = self._handle.read(size)
            if not self._gated:
                self._gated = True
                self._entered.set()
                self.released_by_loop = bool(self._release.wait(self.GATE_TIMEOUT))
            return data

        def close(self):
            self._handle.close()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()

    class FilesDouble:
        def __init__(self):
            self.calls = []
            self.handles = []
            self.fraction = None

        def download_once(self, relpath, *, on_ready):
            handle = DownloadHandle(on_ready=on_ready)
            self.calls.append((relpath, on_ready))
            self.handles.append(handle)
            return handle

        def download_fraction(self):
            return self.fraction


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class FileDownloadTests(unittest.TestCase):
    """The file-manager lane: one terminal, identity-checked before the
    file reaches Cura."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.download_type = self.qt.load("FileDownload").FileDownload
        self.files = FilesDouble()
        self.cura = CuraDouble()

    def download(self, **kwargs):
        download = self.download_type(self.files, self.cura, **kwargs)
        self.addCleanup(download.close)
        return download

    def request_save(self, download, target, relpath="prints/part.gcode"):
        """Drive a save request with the picker answered — the real
        dialog cannot open offscreen."""
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            return download.request_save(relpath)

    def streamed_file(self, payload="G1 X0\n", name="part.gcode"):
        """The streamed source in its temp directory, as the one-shot
        lane hands it to the terminal."""
        directory = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-temp-"), "one-shot")
        os.makedirs(directory)
        path = os.path.join(directory, name)
        pathlib.Path(path).write_text(payload, encoding="utf-8")
        return path

    def save_target(self, name="saved.gcode", payload=None):
        target = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), name)
        if payload is not None:
            pathlib.Path(target).write_text(payload, encoding="utf-8")
        return target

    def publish(self, download, timeout=5.0):
        """Drive the owner thread's loop until the save publication
        lands: the finished stream is copied to the staging sibling on
        a worker, and the terminal comes back through a queued signal,
        so a test that called the stream's `finish` must pump."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.qt.events()
            if download._save is None:
                return True
            time.sleep(0.005)
        return download._save is None

    def gated_copy(self, source, entered, release):
        """Hold the copy open at its first source read: every opener in
        the process (the chunk loop's own, or a copy helper's) is gated
        on that one path, so the publication is inspectable while it
        runs. The returned namespace carries the live gate."""
        real_open = open
        gate = SimpleNamespace(source=None)

        def gated_open(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            if str(path) == source and "r" in mode and "b" in mode:
                gate.source = GatedSource(handle, entered, release)
                return gate.source
            return handle

        return patch("builtins.open", gated_open), gate

    def test_a_synchronously_failed_download_does_not_accumulate(self):
        # The hardening pass: a constructor failure delivers its
        # terminal before download_once returns — the dead handle must
        # never enter _active and the failure surfaces exactly once
        # per attempt, however many attempts fail.
        def sync_fail(relpath, *, on_ready):
            handle = DownloadHandle(done=True)
            on_ready(None, "the setup failed")
            return handle

        self.files.download_once = sync_fail
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        for _ in range(5):
            self.assertTrue(download.request("part.gcode"))
        self.assertEqual(download._active, set())
        self.assertEqual(len(failures), 5)

    def test_a_ready_download_loads_the_file_through_a_lease(self):
        failures = []
        download = self.download()
        download.failed.connect(failures.append)
        self.assertTrue(download.request("part.gcode"))
        self.assertEqual(self.files.calls[0][0], "part.gcode")
        on_ready = self.files.calls[0][1]
        self.assertEqual(len(download._active), 1)

        root = tempfile.mkdtemp(prefix="mpfxtest-lease-")
        directory = os.path.join(root, "one-shot")
        os.makedirs(directory)
        path = os.path.join(directory, "part.gcode")
        pathlib.Path(path).write_text("G1 X0\n", encoding="utf-8")
        on_ready(path, None)

        self.assertEqual(failures, [])
        self.assertEqual(download._active, set())  # the terminal retired the handle
        self.assertEqual(len(self.cura.loads), 1)
        lease = self.cura.loads[0]
        self.assertEqual(lease.path, path)
        lease.close()
        self.assertFalse(os.path.exists(directory))  # the release owns the temp root

    def test_a_download_error_surfaces_on_the_failure_signal(self):
        failures = []
        download = self.download()
        download.failed.connect(failures.append)
        download.request("part.gcode")
        on_ready = self.files.calls[0][1]
        on_ready(None, "The download failed")
        on_ready("", None)  # no path and no error: still a failure, never a load
        self.assertEqual(failures, ["The download failed", "The download failed"])
        self.assertEqual(self.cura.loads, [])

    def test_a_completion_after_a_printer_switch_is_discarded(self):
        identity = ["A"]
        generation = [1]
        download = self.download(active_identity=lambda: (identity[0], "Printer A"),
                                 session_generation=lambda: generation[0])
        failures = []
        download.failed.connect(failures.append)
        download.request("part.gcode")
        on_ready = self.files.calls[0][1]
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-stale-"), "part.gcode")
        pathlib.Path(path).write_text("G1 X0\n", encoding="utf-8")

        identity[0] = "B"
        on_ready(path, None)
        generation[0] = 2
        identity[0] = "A"
        on_ready(path, None)

        self.assertEqual(failures, ["The printer connection changed; the download was discarded"] * 2)
        self.assertEqual(self.cura.loads, [])  # a stale file never reaches Cura

    def test_cura_load_refusals_relay_to_the_failure_signal(self):
        failures = []
        download = self.download()
        download.failed.connect(failures.append)
        self.cura.loadFailed.emit("No printer is selected")
        self.assertEqual(failures, ["No printer is selected"])

    def test_close_cancels_every_in_flight_download(self):
        download = self.download()
        download.request("one.gcode")
        download.request("two.gcode")
        download.close()
        self.assertEqual([handle.cancelled for handle in self.files.handles], [1, 1])
        self.assertEqual(download._active, set())

    def test_request_save_writes_the_picked_path_and_never_loads(self):
        # The live ruling: the file-manager Download is STRICTLY a file
        # transfer — the stream lands at the picked path and nothing
        # else observes it (no Cura load, no index, no state).
        target = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode")
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            failures = []
            download = self.download()
            download.failed.connect(failures.append)
            self.assertTrue(download.request_save("prints/part.gcode"))
        self.assertEqual(self.files.calls[0][0], "prints/part.gcode")
        on_ready = self.files.calls[0][1]
        root = tempfile.mkdtemp(prefix="mpfxtest-temp-")
        directory = os.path.join(root, "one-shot")
        os.makedirs(directory)
        path = os.path.join(directory, "part.gcode")
        pathlib.Path(path).write_text("G1 X0\n", encoding="utf-8")
        on_ready(path, None)
        self.assertTrue(self.publish(download))
        self.assertEqual(failures, [])
        self.assertEqual(download._active, set())
        self.assertEqual(self.cura.loads, [])  # the save flow NEVER loads
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "G1 X0\n")
        self.assertFalse(os.path.exists(directory))  # the temp root is cleaned

    def test_request_save_cancel_starts_nothing(self):
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = ("", "")
            download = self.download()
            self.assertFalse(download.request_save("part.gcode"))
        self.assertEqual(self.files.calls, [])
        self.assertEqual(download._active, set())

    def test_request_save_opens_the_picker_in_downloads_or_home(self):
        # The picker's default lands in the OS Downloads directory
        # (or home when none is exposed), carrying the file's name.
        target = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode")
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            self.download().request_save("prints/part.gcode")
            initial = dialog.getSaveFileName.call_args[0][2]
        self.assertTrue(initial.endswith("part.gcode"), initial)
        self.assertTrue(os.path.dirname(initial), "the picker opened without a directory")

    def test_request_save_falls_back_to_home_without_downloads(self):
        target = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode")
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            with patch.object(module.QStandardPaths, "writableLocation",
                              side_effect=lambda location: (
                                  "" if location == module.QStandardPaths.StandardLocation.DownloadLocation
                                  else "/home/user")):
                self.download().request_save("part.gcode")
            initial = dialog.getSaveFileName.call_args[0][2]
        self.assertTrue(initial.startswith("/home/user"), initial)

    def test_request_save_failure_surfaces_without_a_file(self):
        target = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode")
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            failures = []
            download = self.download()
            download.failed.connect(failures.append)
            download.request_save("part.gcode")
        on_ready = self.files.calls[0][1]
        on_ready(None, "boom")
        self.assertEqual(failures, ["boom"])
        self.assertFalse(os.path.exists(target))

    def test_progress_reports_the_streams_fraction_and_name(self):
        # The progress window's payload: the one-shot operation's
        # fraction as a percent and the picked file's name; None when
        # nothing streams (the popup's gate).
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (
                os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode"), "")
            download = self.download()
            self.assertIsNone(download.progress())
            download.request_save("prints/part.gcode")
        self.files.handles[0]._op = SimpleNamespace(received=42, size=100)
        self.assertEqual(download.progress(),
                         {"name": "part.gcode", "percent": 42, "received": 42,
                          "total": 100, "indeterminate": False})

    def test_an_unknown_total_keeps_the_window_open_and_indeterminate(self):
        # The reviewer's B: a response without Content-Length used to
        # empty the payload, which closed the popup and took the user's
        # Cancel with it. The window now stays open — the name alone
        # gates it — with the bytes received in place of a fraction and
        # the indeterminate flag saying why.
        download = self.download()
        self.assertTrue(self.request_save(download, self.save_target()))
        self.files.handles[0]._op = SimpleNamespace(received=512, size=0)
        self.assertEqual(download.progress(),
                         {"name": "part.gcode", "received": 512,
                          "percent": 0, "total": 0, "indeterminate": True})

    def test_the_total_arriving_mid_stream_turns_the_payload_determinate(self):
        download = self.download()
        self.assertTrue(self.request_save(download, self.save_target()))
        handle = self.files.handles[0]
        handle._op = SimpleNamespace(received=512, size=0)
        self.assertTrue(download.progress()["indeterminate"])
        handle._op.size = 2048  # the headers named a total after streaming started
        self.assertEqual(download.progress(),
                         {"name": "part.gcode", "received": 512,
                          "percent": 25, "total": 2048, "indeterminate": False})

    def test_progress_ignores_a_load_stream_and_an_idle_lane(self):
        # The payload describes the SAVE stream alone: a load in flight
        # must not lend its bytes or its progress to the save window.
        download = self.download()
        self.assertIsNone(download.progress())
        download.request("part.gcode")
        self.files.handles[0]._op = SimpleNamespace(received=50, size=100)
        self.assertIsNone(download.progress())
        # A save whose operation has not been built yet reads as
        # nothing streaming rather than as a broken payload.
        self.assertTrue(self.request_save(download, self.save_target()))
        self.assertIsNone(download.progress())

    def test_a_user_cancel_is_its_own_terminal(self):
        # The reviewer's A: pressing Cancel is not a connection change.
        # Every in-flight stream retires with the user-cancel message
        # and the destination is never written.
        target = self.save_target()
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        self.files.handles[0]._op = SimpleNamespace(received=50, size=100)
        download.cancel()

        self.assertEqual(failures, ["The download was cancelled"])
        self.assertEqual(self.files.handles[0].reasons, ["The download was cancelled"])
        self.assertIsNone(download.progress())
        self.assertFalse(os.path.exists(target))
        self.assertEqual(download._active, set())

    def test_a_load_stream_survives_the_save_windows_cancel(self):
        # RC-05: the popup describes the SAVE transfer alone, so its
        # Cancel retires that transfer alone. A load opened alongside
        # was never cancelled by any UI the user touched, and killing
        # it silently was the defect. Shutdown and the session door
        # still retire everything (the two tests below).
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        download.request("load-me.gcode")
        self.assertTrue(self.request_save(download, self.save_target(), "save-me.gcode"))
        download.cancel()

        self.assertEqual([handle.cancelled for handle in self.files.handles], [0, 1])
        self.assertEqual([handle.reasons for handle in self.files.handles],
                         [[], ["The download was cancelled"]])
        self.assertEqual(failures, ["The download was cancelled"])
        self.assertIsNone(download.progress())
        # The load is still streaming: only the save retired.
        self.assertEqual(len(download._active), 1)

        # The shutdown door still retires everything, load included.
        download.close()
        self.assertEqual([handle.cancelled for handle in self.files.handles], [1, 1])
        self.assertEqual(download._active, set())

    def test_a_shutdown_cancel_retires_quietly(self):
        # Shutdown (the runtime closes this before the files service)
        # retires the same way but reports nothing — no note line is
        # left to read it.
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, self.save_target()))
        download.close()

        self.assertEqual(failures, [])
        self.assertEqual(self.files.handles[0].cancelled, 1)
        self.assertIsNone(download.progress())

    def test_a_second_save_request_is_refused_while_one_streams(self):
        # The reviewer's C: one save transfer at a time, refused
        # outright rather than letting one name describe two streams —
        # and refused BEFORE the picker opens.
        module = self.qt.load("FileDownload")
        target = self.save_target()
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            self.assertTrue(download.request_save("one.gcode"))
            self.assertFalse(download.request_save("two.gcode"))
            self.assertEqual(dialog.getSaveFileName.call_count, 1)
        self.assertEqual(failures, [download.BUSY_MESSAGE])
        self.assertEqual([call[0] for call in self.files.calls], ["one.gcode"])

        # The window keeps tracking the transfer that IS running.
        self.files.handles[0]._op = SimpleNamespace(received=1, size=10)
        self.assertEqual(download.progress()["name"], "one.gcode")

        # The slot frees with the terminal, so the next request lands.
        self.files.handles[0].finish(None, "The download failed")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (self.save_target("two.gcode"), "")
            self.assertTrue(download.request_save("two.gcode"))
        self.assertEqual([call[0] for call in self.files.calls], ["one.gcode", "two.gcode"])

    def test_a_cancelled_picker_leaves_the_save_slot_free(self):
        module = self.qt.load("FileDownload")
        download = self.download()
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = ("", "")
            self.assertFalse(download.request_save("part.gcode"))
        self.assertEqual(self.files.calls, [])
        # Nothing latched: the next request is honoured.
        self.assertTrue(self.request_save(download, self.save_target(), "part.gcode"))
        self.assertEqual([call[0] for call in self.files.calls], ["part.gcode"])

    def test_a_load_in_flight_does_not_block_a_save(self):
        # The single-transfer policy is the SAVE lane's: a load into
        # Cura is a different flow with its own terminal.
        download = self.download()
        download.request("load-me.gcode")
        self.assertTrue(self.request_save(download, self.save_target(), "save-me.gcode"))
        self.assertEqual([call[0] for call in self.files.calls], ["load-me.gcode", "save-me.gcode"])

    def test_a_save_landing_after_a_printer_switch_never_reaches_the_disk(self):
        # The reviewer's A, session half: an invalidated printer keeps
        # its connection-change explanation and the destination the
        # user already had is left exactly as it was.
        identity = ["A"]
        generation = [1]
        target = self.save_target(payload="OLD\n")
        download = self.download(active_identity=lambda: (identity[0], "Printer A"),
                                 session_generation=lambda: generation[0])
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("NEW\n")

        identity[0] = "B"
        generation[0] = 2
        self.files.handles[0].finish(source, None)

        self.assertEqual(failures, ["The printer connection changed; the download was discarded"])
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "OLD\n")
        self.assertFalse(os.path.exists(os.path.dirname(source)))
        self.assertIsNone(download.progress())

    def test_a_save_replaces_the_file_the_picker_selected(self):
        # The reviewer's D: an existing destination is replaced by the
        # completed transfer, with no staging left beside it.
        root = tempfile.mkdtemp(prefix="mpfxtest-save-")
        target = os.path.join(root, "saved.gcode")
        pathlib.Path(target).write_text("OLD\n", encoding="utf-8")
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("NEW\n")
        self.files.handles[0].finish(source, None)
        self.assertTrue(self.publish(download))

        self.assertEqual(failures, [])
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "NEW\n")
        self.assertEqual(sorted(os.listdir(root)), ["saved.gcode"])
        self.assertFalse(os.path.exists(os.path.dirname(source)))

    def test_the_save_copy_leaves_the_owner_thread_free(self):
        # RC-04: the finished stream used to be copied to the staging
        # sibling ON the owner thread, so a large save onto a slow
        # volume froze Cura's repaints, its Cancel and its close. The
        # copy now runs on a worker: the real chunk loop is held open
        # at its first read, and only the app's own event loop can
        # release it — a copy on the owner thread could never reach
        # the loop at all.
        target = self.save_target(payload="OLD\n")
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        payload_bytes = "NEW\n" * 4096
        source = self.streamed_file(payload_bytes)
        self.files.handles[0]._op = SimpleNamespace(received=len(payload_bytes), size=len(payload_bytes))
        entered, release = threading.Event(), threading.Event()

        timer = self.qt.QTimer()
        timer.setInterval(0)
        timer.timeout.connect(release.set)
        self.addCleanup(timer.stop)
        timer.start()

        gate_patch, gate = self.gated_copy(source, entered, release)
        with gate_patch:
            self.files.handles[0].finish(source, None)  # the owner-thread terminal
            self.assertTrue(entered.wait(2.0), "the copy never started")
            payload = download.progress()
            self.assertIsNotNone(payload, "the progress window vanished before the copy finished")
            self.assertEqual(payload["percent"], 100)
            # A hang guard, not a budget: the invariant is that the
            # owner thread CAN reach its loop while the copy is held,
            # and on a starved runner a 3 s budget is a machine-speed
            # assertion instead (the loaded 2-CPU rig's "the copy held
            # the owner thread: the event loop never ran"). The loop
            # gets scheduled when the machine gets round to it.
            deadline = time.monotonic() + 15.0
            while not release.is_set() and time.monotonic() < deadline:
                self.qt.events()
                time.sleep(0.005)

        self.assertTrue(gate.source is not None and gate.source.released_by_loop,
                        "the copy held the owner thread: the event loop never ran")
        self.assertTrue(self.publish(download), "the publication never completed")
        self.assertEqual(failures, [])
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), payload_bytes)
        self.assertFalse(os.path.exists(os.path.dirname(source)))

    def test_the_bar_holds_at_full_until_the_file_is_published(self):
        # The payload across the publication edge: the stream is
        # complete but the bytes are not at the destination yet, so the
        # bar holds full and the window stays open (clearing the save
        # latch first is what made it vanish mid-stall).
        target = self.save_target()
        download = self.download()
        self.assertTrue(self.request_save(download, target))
        handle = self.files.handles[0]
        handle._op = SimpleNamespace(received=5, size=10)
        self.assertEqual(download.progress()["percent"], 50)
        source = self.streamed_file("NEW\n")
        entered, release = threading.Event(), threading.Event()

        gate_patch, gate = self.gated_copy(source, entered, release)
        with gate_patch:
            handle._op = SimpleNamespace(received=10, size=10)
            handle.finish(source, None)
            self.assertTrue(entered.wait(2.0), "the copy never started")
            self.assertEqual(download.progress(),
                             {"name": "part.gcode", "percent": 100, "received": 10,
                              "total": 10, "indeterminate": False})
            release.set()
            self.assertTrue(self.publish(download))
        self.assertIsNone(download.progress())

    def test_a_cancel_while_publishing_abandons_the_copy(self):
        # The Cancel stays reachable until publication: while the copy
        # runs the window still describes the transfer, so pressing it
        # retires the stream and leaves the destination the user
        # already had exactly as it was — staging included.
        target = self.save_target(payload="OLD\n")
        directory = os.path.dirname(target)
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("NEW\n")
        entered, release = threading.Event(), threading.Event()

        gate_patch, gate = self.gated_copy(source, entered, release)
        with gate_patch:
            self.files.handles[0].finish(source, None)
            self.assertTrue(entered.wait(2.0), "the copy never started")
            download.cancel()
            release.set()
            deadline = time.monotonic() + 5.0
            while not failures and time.monotonic() < deadline:
                self.qt.events()
                time.sleep(0.005)

        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "OLD\n")
        self.assertEqual(sorted(os.listdir(directory)), ["saved.gcode"])
        self.assertFalse(os.path.exists(os.path.dirname(source)))
        self.assertEqual(failures, ["The download was cancelled"])
        self.assertIsNone(download.progress())

    def test_a_switch_during_the_publication_copy_never_lands(self):
        # The reviewer's catch: the identity gate ran when the stream
        # finished, BEFORE the copy to the destination's volume — and that
        # copy can run for minutes. A printer switch landing mid-copy still
        # published the retired printer's file over the user's destination
        # with no note. The gate has to be re-read at publication time,
        # immediately before the swap, so the same connection-change
        # outcome the other stale terminals use is what the user sees.
        identity = ["A"]
        generation = [1]
        target = self.save_target(payload="OLD\n")
        directory = os.path.dirname(target)
        download = self.download(active_identity=lambda: (identity[0], "Printer A"),
                                 session_generation=lambda: generation[0])
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("NEW\n")
        entered, release = threading.Event(), threading.Event()

        gate_patch, gate = self.gated_copy(source, entered, release)
        with gate_patch:
            self.files.handles[0].finish(source, None)
            self.assertTrue(entered.wait(2.0), "the copy never started")
            # Mid-copy: the printer is switched away AND the session rolls.
            identity[0] = "B"
            generation[0] = 2
            release.set()
            self.assertTrue(self.publish(download))

        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "OLD\n")
        self.assertEqual(sorted(os.listdir(directory)), ["saved.gcode"])
        self.assertFalse(os.path.exists(os.path.dirname(source)))
        self.assertEqual(failures, ["The printer connection changed; the download was discarded"])
        self.assertIsNone(download.progress())

    def test_a_failed_save_keeps_the_file_the_user_already_had(self):
        # A failed landing is reported, never half-written: the swap is
        # one atomic replace of a fully-copied staging file.
        target = self.save_target(payload="OLD\n")
        module = self.qt.load("FileDownload")
        download = self.download()
        failures = []
        download.failed.connect(failures.append)
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("NEW\n")

        with patch.object(module, "_replace_saved_file", side_effect=OSError("the disk is full")):
            self.files.handles[0].finish(source, None)
            self.assertTrue(self.publish(download))

        self.assertEqual(failures, ["The download could not be saved: the disk is full"])
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "OLD\n")
        self.assertFalse(os.path.exists(os.path.dirname(source)))

    def test_a_refused_swap_clears_its_staging_and_leaves_the_destination_alone(self):
        # The replace helper on its own, with a refusal the platform
        # raises for real (a directory where the file should go): the
        # staging file never survives a failed swap.
        module = self.qt.load("FileDownload")
        root = tempfile.mkdtemp(prefix="mpfxtest-save-")
        blocked = os.path.join(root, "saved.gcode")
        os.makedirs(blocked)
        source = self.streamed_file("NEW\n")

        with self.assertRaises(OSError):
            module._replace_saved_file(source, blocked)

        self.assertEqual(os.listdir(root), ["saved.gcode"])
        self.assertEqual(os.listdir(blocked), [])
        self.assertTrue(os.path.exists(source))  # the caller still owns the source

    def test_a_staging_collision_moves_to_the_next_name(self):
        # A file already sitting on the first staging name belongs to
        # whoever made it: the save steps over it, never through it.
        module = self.qt.load("FileDownload")
        root = tempfile.mkdtemp(prefix="mpfxtest-save-")
        target = os.path.join(root, "saved.gcode")
        blocker = os.path.join(root, ".saved.gcode.mpf-part-1")
        pathlib.Path(blocker).write_text("SOMEONE ELSE\n", encoding="utf-8")
        source = self.streamed_file("NEW\n")

        module._replace_saved_file(source, target)

        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "NEW\n")
        self.assertEqual(pathlib.Path(blocker).read_text(encoding="utf-8"), "SOMEONE ELSE\n")
        self.assertEqual(sorted(os.listdir(root)), [".saved.gcode.mpf-part-1", "saved.gcode"])

    def test_an_unremovable_staging_does_not_mask_the_real_failure(self):
        # A staging file the platform refuses to delete (a locked file
        # on Windows) must not change what the caller is told: the
        # storage error that caused the failure is the one that
        # surfaces.
        module = self.qt.load("FileDownload")
        root = tempfile.mkdtemp(prefix="mpfxtest-save-")
        blocked = os.path.join(root, "saved.gcode")
        os.makedirs(blocked)
        source = self.streamed_file("NEW\n")

        with self.assertRaises(OSError) as raised:
            with patch.object(module.os, "remove", side_effect=OSError("in use")):
                module._replace_saved_file(source, blocked)

        self.assertNotIn("in use", str(raised.exception))

    def test_every_save_terminal_retires_what_this_lane_owns(self):
        # The cleanup split, across all four terminals. A terminal that
        # CARRIES a path (a success, or a stale completion) hands this
        # lane a temp directory and this lane retires it; a pathless
        # terminal was retired by the one-shot's own abort before it
        # arrived (the service's tests pin that side). Either way the
        # destination only ever appears through a successful swap.
        identity = ["A"]
        generation = [1]
        for label in ("success", "failure", "user cancel", "printer switch"):
            with self.subTest(terminal=label):
                download = self.download(active_identity=lambda: (identity[0], "Printer A"),
                                         session_generation=lambda: generation[0])
                target = self.save_target("{}.gcode".format(label.replace(" ", "-")))
                self.assertTrue(self.request_save(download, target))
                handle = self.files.handles[-1]
                # A path-carrying terminal only: the pathless ones never
                # reach this lane with a directory to clean.
                source = self.streamed_file("G1 X0\n") if label in ("success", "printer switch") else None
                directory = os.path.dirname(source) if source is not None else None

                if label == "success":
                    handle.finish(source, None)
                    self.assertTrue(self.publish(download), label)
                elif label == "failure":
                    handle.finish(None, "The download failed")
                elif label == "user cancel":
                    download.cancel()
                else:
                    identity[0] = "B"
                    handle.finish(source, None)

                if directory is not None:
                    self.assertFalse(os.path.exists(directory), label)
                self.assertEqual(download._active, set(), label)
                self.assertIsNone(download.progress(), label)
                self.assertEqual(os.path.exists(target), label == "success", label)

    def test_the_save_flow_never_loads_or_leases_a_file(self):
        # The author's ruling: Save is a file transfer and nothing
        # else — no Cura load, no lease, no index.
        module = self.qt.load("FileDownload")
        target = self.save_target()
        download = self.download()
        self.assertTrue(self.request_save(download, target))
        source = self.streamed_file("G1 X0\n")

        with patch.object(module, "FileLease") as lease:
            self.files.handles[0].finish(source, None)
            self.assertTrue(self.publish(download))

        self.assertFalse(lease.called)
        self.assertEqual(self.cura.loads, [])
        self.assertEqual(pathlib.Path(target).read_text(encoding="utf-8"), "G1 X0\n")

    def test_cancel_retires_every_in_flight_stream(self):
        module = self.qt.load("FileDownload")
        with patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (
                os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "saved.gcode"), "")
            download = self.download()
            download.request_save("one.gcode")
        self.files.fraction = 0.5
        download.cancel()
        self.assertEqual([handle.cancelled for handle in self.files.handles], [1])
        self.assertEqual(download._active, set())
        self.files.fraction = None  # the real service clears on the terminal
        self.assertIsNone(download.progress())


# --------------------------------------------------------------------------
# FollowerRuntime (Qt + UM stubs)
# --------------------------------------------------------------------------

@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class FollowerRuntimeTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transports = []

    def build(self, app=None, transport=None):
        app = self.qt.Application() if app is None else app
        transport = transport or ScriptedTransport()
        self.transports.append(transport)
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        with patch.object(root, "MoonrakerClient",
                          lambda parent: real(parent, transport=transport, socket=ScriptedSocket())):
            follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.addCleanup(follower.deinitialize)
        self.qt.events()
        return app, follower

    def test_the_runtime_composes_every_part_under_one_persistence_folder(self):
        _, follower = self.build()
        runtime_instance = follower._runtime
        for part in ("client", "binding", "persistence", "cura", "files", "file_download", "index",
                     "preview", "motion", "pauses", "presentation", "bed_mesh", "coordinator", "notice"):
            self.assertTrue(hasattr(runtime_instance, part), part)

        # One plugin-owned folder: the settings document, the global state
        # document and the per-machine shards all live under it.
        root = pathlib.Path(runtime_instance.persistence.settings_path).parent
        self.assertEqual(root.name, "MoonrakerPrintFollower")
        self.assertEqual(pathlib.Path(runtime_instance.persistence.state_global_path).parent, root)
        self.assertEqual(pathlib.Path(runtime_instance.persistence.state_dir).parent, root)
        self.assertTrue(pathlib.Path(runtime_instance.persistence.state_dir).is_dir())

        # The smoothing trace is a diagnostic: nothing is written by default.
        self.assertIsNone(runtime_instance.motion._trace_path)

    def test_close_retires_the_runtime_once_and_closes_the_transport(self):
        _, follower = self.build()
        runtime_instance = follower._runtime
        transport = self.transports[0]
        before = len(transport.cancelled)
        runtime_instance.close()
        self.assertTrue(runtime_instance._closed)
        self.assertGreater(len(transport.cancelled), before)  # the pooled sockets died with it
        runtime_instance.close()  # idempotent: a second close is a no-op
        self.assertTrue(runtime_instance._closed)

    def test_the_smoothing_trace_lands_under_the_cache_directory_when_opted_in(self):
        from plugins.CacheNamespaces import CACHE_DIRECTORY_NAME
        with patch.dict(os.environ, {"MOONRAKER_FOLLOWER_SMOOTHING_TRACE": "smoothing.csv"}):
            _, follower = self.build()
        trace_path = follower._runtime.motion._trace_path
        self.assertEqual(pathlib.Path(trace_path).name, "smoothing.csv")
        self.assertEqual(pathlib.Path(trace_path).parent.name, CACHE_DIRECTORY_NAME)

    def test_the_cache_directory_name_never_matches_the_package_id(self):
        # The 2026-09-22 collision: Uranium's package purge deletes
        # every child directory of the storage root named after the
        # replaced package. The cache directory must never match the
        # package ID, and the purge's own walk must leave it alone
        # (the legacy name dies wholesale — that is exactly the bug).
        from plugins.CacheNamespaces import CACHE_DIRECTORY_NAME
        self.assertNotEqual(CACHE_DIRECTORY_NAME, "MoonrakerPrintFollower")
        with tempfile.TemporaryDirectory() as root:
            legacy = os.path.join(root, "MoonrakerPrintFollower")
            os.makedirs(os.path.join(legacy, "cache-v2"), exist_ok=True)
            safe = os.path.join(root, CACHE_DIRECTORY_NAME)
            os.makedirs(os.path.join(safe, "cache-v2"), exist_ok=True)
            # The purge's recipe: any child dir matching the package
            # being replaced is removed wholesale.
            for name in os.listdir(root):
                if name == "MoonrakerPrintFollower":
                    shutil.rmtree(os.path.join(root, name))
            self.assertTrue(os.path.isdir(safe),
                            "the package purge reached the renamed cache")

    def test_the_migration_clean_and_the_toast_run_from_initialization_finished(self):
        binding_module = self.qt.load("PrinterBinding")
        notice_module = self.qt.load("MigrationNotice")

        class BootApplication(self.qt.Application):
            initializationFinished = pyqtSignal()

        with patch.object(binding_module.PrinterBinding, "run_persistence_migration", MagicMock()) as migrate, \
                patch.object(notice_module.MigrationNotice, "announce", MagicMock()) as announce:
            app, _ = self.build(BootApplication())
            # The earlier triggers (the machine-switch path) have had their
            # turn; the boot signal must be wired to both slots as well.
            before = (migrate.call_count, announce.call_count)
            app.initializationFinished.emit()
            self.assertEqual((migrate.call_count, announce.call_count), (before[0] + 1, before[1] + 1))

    def test_a_session_invalidation_cancels_the_in_flight_oneshots(self):
        root = self.qt.load("FollowerRuntime")
        with patch.object(root.RemoteFileService, "cancel_one_shots", MagicMock()) as cancel:
            app, follower = self.build()
            before = cancel.call_count  # the boot's own invalidations have run
            follower.client.sessionInvalidated.emit()
            self.assertEqual(cancel.call_count, before + 1)

    def test_the_injected_savefile_write_reports_a_host_that_refuses(self):
        root = self.qt.load("FollowerRuntime")
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-save-"), "settings.json")
        self.assertTrue(root._savefile_write(path, "{}\n"))
        self.assertEqual(pathlib.Path(path).read_text(encoding="utf-8"), "{}\n")

        save_module = sys.modules["UM.SaveFile"]
        with patch.object(save_module, "SaveFile", side_effect=OSError("read-only config dir")):
            self.assertFalse(root._savefile_write(path, "{}\n"))

    def test_the_state_lock_degrades_to_no_lock_when_the_host_lacks_one(self):
        root = self.qt.load("FollowerRuntime")
        root_directory = tempfile.mkdtemp(prefix="mpfxtest-lock-")
        self.assertIsNotNone(root._state_lock(root_directory))

        lock_module = sys.modules["UM.LockFile"]
        with patch.object(lock_module, "LockFile", side_effect=OSError("no lock files here")):
            self.assertIsNone(root._state_lock(root_directory))

    def test_the_migration_toast_has_a_backup_flavour_and_a_bare_one(self):
        root = self.qt.load("FollowerRuntime")
        shown = []

        class RecordingMessage(sys.modules["UM.Message"].Message):
            def __init__(self, *args):
                super().__init__(*args)
                self.text = args[0] if args else ""
                self.actions = []
                self.triggered = None
                self.actionTriggered.connect(self._record)
                shown.append(self)

            def _record(self, _message, action):
                self.triggered = action

            def addAction(self, *args):
                self.actions.append(args)

        message_module = sys.modules["UM.Message"]
        # Patch the module object, never the dotted string: the Qt harness
        # restores a sys.modules snapshot on exit, which evicts a module first
        # imported inside it — the string target can then resolve to that
        # evicted PyQt6.QtGui through the PyQt6 package attribute while the
        # plugin's own import gets a freshly loaded module, so the mock misses
        # the real call. Importing here pins the module the plugin resolves.
        import PyQt6.QtGui as qt_gui_module
        with patch.object(message_module, "Message", RecordingMessage), \
                patch.object(qt_gui_module, "QDesktopServices") as desktop_services:
            root._raise_migration_toast({"backupWritten": True, "backupName": "cura.cfg.2026-09-18-14-30-12"})
            root._raise_migration_toast({"backupWritten": False})
            root._raise_migration_toast({"backupWritten": True, "backupName": ""})  # a flag without a file is bare
            self.assertEqual(len(shown), 3)
            backed, bare, flag_only = shown
            self.assertEqual(len(backed.actions), 1)  # flavour A offers the backup folder
            self.assertEqual(backed.actions[0][0], "show_backup_folder")
            self.assertEqual(backed.actions[0][1], "Show backup folder")
            self.assertIn("cura.cfg.2026-09-18-14-30-12", backed.text)
            self.assertIn("Nothing was removed", bare.text)  # flavour B: nothing to open
            self.assertEqual((bare.actions, flag_only.actions), ([], []))
            backed.actionTriggered.emit(backed, "show_backup_folder")
            self.assertEqual(backed.triggered, "show_backup_folder")  # the action is wired to a handler

        # The folder action opens Cura's configuration folder.
        desktop_services.openUrl.assert_called_once()
        self.assertTrue(desktop_services.openUrl.call_args[0][0].toLocalFile())

    def test_a_missing_message_module_leaves_the_failure_unreported(self):
        root = self.qt.load("FollowerRuntime")
        with patch.dict(sys.modules, {"UM.Message": None}):
            root._raise_migration_toast({"backupWritten": True, "backupName": "cura.cfg.2026-09-18-14-30-12"})


if __name__ == "__main__":
    unittest.main()

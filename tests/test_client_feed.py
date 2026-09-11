"""The client's websocket feed branch: admission, proof, fallback, refusals."""
from __future__ import annotations

import unittest

from tests.qt_runtime_support import QT_AVAILABLE, ScriptedTransport

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

    from plugins.MoonrakerClient import MoonrakerClient
    from plugins.MoonrakerSession import MoonrakerSession


    class ScriptedSocket(QObject):
        syncSnapshot = pyqtSignal(object, float)
        subscribeRefused = pyqtSignal(object)
        klippyReady = pyqtSignal()
        klippyLost = pyqtSignal(str)
        failed = pyqtSignal(str)
        upgraded = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.starts = []
            self.subscriptions = []
            self.rpcs = []
            self.stops = 0
            self.is_upgraded = False
            self.core_patch = None
            self.core_stamp = 0.0
            self.aux_patch = None

        def start(self, url, api_key, core_names, aux_names):
            self.starts.append((url, api_key, set(core_names), set(aux_names)))
            self.is_upgraded = True
            self.upgraded.emit()

        def subscribe(self, objects):
            self.subscriptions.append(dict(objects))

        def stop(self):
            self.stops += 1
            self.is_upgraded = False

        def drain_core(self):
            patch, stamp = self.core_patch, self.core_stamp
            self.core_patch = None
            self.core_stamp = 0.0
            return patch, stamp

        def drain_aux(self):
            patch = self.aux_patch
            self.aux_patch = None
            return patch, 0.0

        def request(self, method, params, callback):
            self.rpcs.append((method, dict(params), callback))
            return len(self.rpcs)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ClientFeedTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.transport = ScriptedTransport()
        self.socket = ScriptedSocket()
        self.session = MoonrakerSession(transport=self.transport, socket=self.socket)
        self.client = MoonrakerClient(session=self.session, proof_timeout_ms=150)

    def pump(self, seconds=0.3):
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_websocket_start_connects_with_the_mapped_url_and_merged_set(self):
        self.client.configure("https://printer.local", "key", 750, feed_mode="websocket")
        self.client.start()
        url, key, core, aux = self.socket.starts[0]
        self.assertEqual(url, "wss://printer.local/websocket")
        self.assertEqual(key, "key")
        self.assertEqual(core, {"print_stats", "gcode_move", "virtual_sdcard", "motion_report", "bed_mesh"})
        self.assertEqual(self.socket.subscriptions[0], {
            "bed_mesh": None, "gcode_move": None, "motion_report": None,
            "print_stats": None, "virtual_sdcard": None,
        })

    def test_sync_snapshot_admits_and_marks_connected(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 12.0)
        self.assertTrue(received)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_fragment_drain_admits_on_the_tick(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.core_patch = {"print_stats": {"state": "printing"}}
        self.socket.core_stamp = 5.0
        self.client.force_refresh()
        self.assertTrue(received)
        self.assertEqual(received[0]["print_stats"]["state"], "printing")

    def test_stale_sync_is_dropped_after_newer_fragments(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.core_patch = {"print_stats": {"state": "printing"}}
        self.socket.core_stamp = 50.0
        self.client.force_refresh()
        count = len(received)
        # A sync issued earlier than the applied fragments is dropped whole.
        self.socket.syncSnapshot.emit({"print_stats": {"state": "cancelled"}}, 10.0)
        self.assertEqual(len(received), count)
        self.assertEqual(received[-1]["print_stats"]["state"], "printing")
        # A newer sync applies.
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 60.0)
        self.assertEqual(received[-1]["print_stats"]["state"], "idle")

    def test_silent_startup_proof_degrades_to_http_without_a_session_reset(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        generation = self.session.generation
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.pump(0.4)
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.session.generation, generation)
        # Three stops, all expected: the configure's client stop, the
        # session rebind's socket stop, and the proof failure's stop.
        self.assertEqual(self.socket.stops, 3)
        self.assertTrue(any("silent" in reason for _, reason in notes), notes)
        self.assertTrue(self.transport.requests)  # HTTP polling resumed

    def test_proof_passes_once_data_has_flowed(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 1.0)
        self.pump(0.4)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_subscribe_refusal_degrades_with_a_reason(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        generation = self.session.generation
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.socket.subscribeRefused.emit({"code": -32602, "message": "Unknown"})
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.session.generation, generation)
        self.assertTrue(any("refused" in reason for _, reason in notes), notes)

    def test_unauthorized_refusal_is_a_key_rejection_failure(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.socket.subscribeRefused.emit({"code": -32602, "message": "Unauthorized"})
        self.assertTrue(any("key" in reason.lower() for _, reason in notes), notes)

    def test_klippy_ready_resubscribes_and_clears_the_estop_assumption(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.client.assume_print_stopped()
        self.assertTrue(self.session.state.assume_print_stopped)
        subscriptions = len(self.socket.subscriptions)
        self.socket.klippyReady.emit()
        self.assertEqual(len(self.socket.subscriptions), subscriptions + 1)
        self.assertFalse(self.session.state.assume_print_stopped)

    def test_klippy_loss_is_a_failure(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.socket.klippyLost.emit("notify_klippy_shutdown")
        self.assertTrue(any("Klippy" in reason for _, reason in notes), notes)

    def test_mode_change_rebinds_through_the_session(self):
        self.client.configure("http://p", "k", 750)
        self.assertEqual(self.client.effective_feed_mode, "http")
        invalidated = []
        self.client.sessionInvalidated.connect(lambda: invalidated.append(True))
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.assertTrue(invalidated)
        self.assertEqual(self.client.configured_feed_mode, "websocket")


    def test_rpc_lane_routes_to_the_socket_when_live(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 1.0)
        replies = []
        self.assertTrue(self.client.rpc("server.gcode_store", {"count": 100},
                                        lambda payload, error: replies.append((payload, error))))
        self.assertEqual(self.socket.rpcs[-1][:2], ("server.gcode_store", {"count": 100}))
        # The reply shape converts to the transport's convention: the
        # result passes through, the refusal surfaces the server's words.
        method, params, callback = self.socket.rpcs[-1]
        callback({"jsonrpc": "2.0", "result": {"gcode_store": []}, "id": 1})
        self.assertEqual(replies[-1][0]["result"]["gcode_store"], [])
        callback({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Unauthorized"}, "id": 2})
        self.assertIn("Unauthorized", replies[-1][1])

    def test_rpc_lane_falls_back_when_the_socket_is_not_live(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        # The lane opens on the upgrade — an RPC reply is itself an
        # authenticated reply; the proof governs the status feed only.
        self.assertTrue(self.client.rpc_available())
        # It closes when the feed degrades (the proof failure path)...
        self.client._proof_failed(self.client._generation)
        self.assertFalse(self.client.rpc_available())
        self.assertFalse(self.client.rpc("server.info", {}, lambda p, e: None))
        # ...and in HTTP mode it is never open.
        self.client.configure("http://p", "k", 750, feed_mode="http")
        self.client.start()
        self.assertFalse(self.client.rpc_available())

if __name__ == "__main__":
    unittest.main()

"""Real-socket tests for MoonrakerSocket against a scripted loopback server."""
from __future__ import annotations

import unittest

from tests.qt_runtime_support import QT_AVAILABLE
from tests.ws_loopback import WSServer

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

    from plugins.MoonrakerSocket import MoonrakerSocket


FIVE = {
    "print_stats": None, "gcode_move": None, "virtual_sdcard": None,
    "motion_report": None, "bed_mesh": None,
}
SNAPSHOT = {"status": {
    "print_stats": {"state": "printing"},
    "gcode_move": {"speed": 50},
    "virtual_sdcard": {"progress": 0.1},
    "motion_report": {"live_position": [1, 2, 3, 4]},
    "bed_mesh": {"profile_name": "default"},
}}


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class SocketOwnerTests(unittest.TestCase):
    def setUp(self):
        import threading
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.server = WSServer(("127.0.0.1", 0))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._owners = []
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self._stop_owners)

    def _stop_owners(self):
        for owner in self._owners:
            owner.stop()

    def wait_until(self, predicate, timeout=5.0):
        """Poll while pumping the Qt loop — client-side writes only
        reach the wire through the event loop."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.app.processEvents()
            time.sleep(0.01)
        return False

    def url(self, scheme="ws"):
        return f"{scheme}://127.0.0.1:{self.port}/websocket"

    def socket(self, **kwargs):
        owner = MoonrakerSocket(**kwargs)
        self._owners.append(owner)
        return owner

    def wait_signal(self, signal, timeout_ms=5000):
        loop = QEventLoop()
        received = []
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(timeout_ms)
        signal.connect(lambda *args: (received.append(args), loop.quit()))
        timer.timeout.connect(loop.quit)
        timer.start()
        loop.exec()
        return received

    def test_handshake_carries_the_api_key_and_upgrades(self):
        owner = self.socket()
        owner.start(self.url(), "test-key", set(FIVE), set())
        received = self.wait_signal(owner.upgraded)
        self.assertTrue(received, "upgrade never arrived")
        self.assertTrue(self.server.wait_for(lambda s: s.handshakes))
        headers = self.server.handshakes[0]
        self.assertEqual(headers.get("x-api-key"), "test-key")

    def test_subscribe_reply_emits_the_sync_snapshot(self):
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        snapshots = []
        owner.syncSnapshot.connect(lambda status, stamp: snapshots.append((status, stamp)))
        self.server.queue(("reply", {"result": SNAPSHOT}))
        owner.subscribe(FIVE)
        self.assertTrue(self.wait_signal(owner.syncSnapshot))
        status, stamp = snapshots[0]
        self.assertEqual(status["print_stats"]["state"], "printing")
        self.assertGreater(stamp, 0)
        # The subscribe carried the merged object set (F4/F5: one call).
        request = self.server.requests[0]
        self.assertEqual(sorted(request["params"]["objects"]), sorted(FIVE))

    def test_subscribe_reply_seeds_the_aux_accumulator(self):
        # The subscribe response carries the full state ONCE; Moonraker
        # then pushes only changes. Objects that never change must still
        # reach the Monitor: the sync seeds the aux accumulator so the
        # next drain publishes them (the author's live report).
        # The aux names arrive on the RE-SUBSCRIBE (the wanted set is
        # only known after discovery) — not on start — so the seeding
        # must use the subscribe call's aux subset.
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        reply = {"result": {"status": dict(SNAPSHOT["status"], heater_bed={"temperature": 60})}}
        self.server.queue(("reply", reply))
        owner.subscribe(dict(FIVE, heater_bed=None), aux_names={"heater_bed"})
        self.assertTrue(self.wait_signal(owner.syncSnapshot))
        aux, stamp = owner.drain_aux()
        self.assertIsNotNone(aux)
        self.assertEqual(aux["heater_bed"]["temperature"], 60)
        self.assertGreater(stamp, 0)
        # The core objects from the same sync do NOT seed aux.
        self.assertNotIn("print_stats", aux)

    def test_notify_patches_route_per_class_with_bed_mesh_in_both(self):
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), {"bed_mesh", "heater_bed"})
        self.wait_signal(owner.upgraded)
        # The queued notify rides AHEAD of the subscribe reply (the
        # fixture's push semantics), so it is on the wire by the time the
        # reply lands.
        self.server.queue(("notify", "notify_status_update", [
            {"bed_mesh": {"profile_name": "new"}, "heater_bed": {"temperature": 60}},
            123.0,
        ]))
        owner.subscribe(dict(FIVE, heater_bed=None))
        import time
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not owner._core:
            self.app.processEvents()
            time.sleep(0.01)
        core, core_stamp = owner.drain_core()
        aux, aux_stamp = owner.drain_aux()
        self.assertIsNotNone(core)
        self.assertEqual(core["bed_mesh"]["profile_name"], "new")
        self.assertEqual(aux["bed_mesh"]["profile_name"], "new")
        self.assertEqual(aux["heater_bed"]["temperature"], 60)
        self.assertNotIn("heater_bed", core)
        self.assertGreater(core_stamp, 0)
        self.assertGreater(aux_stamp, 0)
        # A second drain is empty — fragments are consumed, not retained.
        self.assertIsNone(owner.drain_core()[0])
        self.assertIsNone(owner.drain_aux()[0])

    def test_server_ping_is_answered_with_a_pong(self):
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        self.server.queue(("ping", b"probe"))
        self.assertTrue(
            self.wait_until(lambda: any(entry[0] == "pong" and entry[1] == b"probe" for entry in self.server.inbound)),
            "client never answered the ping with a matching pong",
        )

    def test_keepalive_round_trip_updates_last_auth_reply(self):
        owner = self.socket(keepalive_interval_ms=50, keepalive_deadline_ms=500)
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        before = owner.last_auth_reply_at
        self.assertTrue(
            self.wait_until(lambda: any(r.get("method") == "server.info" for r in self.server.requests)),
            "keepalive request never arrived",
        )
        self.assertTrue(
            self.wait_until(lambda: owner.last_auth_reply_at > before),
            "keepalive reply never updated the auth-reply clock",
        )

    def test_keepalive_deadline_fails_the_connection(self):
        owner = self.socket(keepalive_interval_ms=50, keepalive_deadline_ms=150)
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        # The loopback server answers every request, so force the deadline
        # path by bumping the clock backwards.
        owner._last_auth_reply_at -= 1.0
        failures = self.wait_signal(owner.failed)
        self.assertTrue(failures, "deadline never fired")

    def test_protocol_violation_fails_with_a_reason(self):
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), set())
        self.wait_signal(owner.upgraded)
        # A masked server frame is the RFC 5.1 violation.
        masked = bytes([0x81, 0x80 | 1]) + b"mask" + bytes([ord("x") ^ ord("m")])
        owner._buffer = masked
        failures = []
        owner.failed.connect(lambda reason: failures.append(reason))
        owner._process_buffer()
        self.assertTrue(failures, "violation produced no failure")
        self.assertIn("masked", failures[0])

    def test_stop_sends_a_close_and_wipes_state(self):
        owner = self.socket()
        owner.start(self.url(), "", set(FIVE), {"bed_mesh"})
        self.wait_signal(owner.upgraded)
        self.server.queue(("notify", "notify_status_update", [
            {"bed_mesh": {"profile_name": "x"}}, 1.0]))
        owner.subscribe(dict(FIVE, bed_mesh=None))
        import time
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not owner._core:
            self.app.processEvents()
            time.sleep(0.01)
        owner.stop()
        self.assertTrue(
            self.wait_until(lambda: any(entry[0] == "close" for entry in self.server.inbound)),
            "stop never sent the close frame",
        )
        self.assertEqual(owner.subscribed_names, [])


if __name__ == "__main__":
    unittest.main()

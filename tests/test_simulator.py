"""The Moonraker simulator's contract tests: the real protocols, the
one-permitted-fake's fidelity. Skipped wherever tornado is absent (the
stdlib host and the pinned dev image; the harness image pins it)."""
from __future__ import annotations

import json
import unittest

try:
    import tornado.ioloop
    import tornado.testing
    import tornado.websocket
    from tornado.httpclient import AsyncHTTPClient
except ImportError:
    tornado = None  # type: ignore[assignment]

from plugins.MoonrakerProtocol import (
    delete_endpoint,
    directory_create_endpoint,
    directory_delete_endpoint,
    download_endpoint,
    gcode_script_endpoint,
    metadata_endpoint,
    move_endpoint,
    objects_list_endpoint,
    print_start_endpoint,
    server_info_endpoint,
    status_endpoint,
    websocket_endpoint,
)


if tornado is not None:

    class SimulatorContractTests(tornado.testing.AsyncTestCase):
        def get_new_ioloop(self):
            return tornado.ioloop.IOLoop.current()

        def setUp(self):
            super().setUp()
            from tests.harness.simulator import Simulator
            self.sim = Simulator(0)
            self.base = self.sim.base_url

        def test_http_lanes_respond(self):
            async def exercise():
                client = AsyncHTTPClient()
                for path in ("/server/info", "/printer/objects/list", "/server/webcams/list",
                             "/machine/device_power/devices"):
                    response = await client.fetch(self.base + path)
                    self.assertEqual(response.code, 200)
                    self.assertIn(b"result", response.body)
                response = await client.fetch(self.base + "/printer/print/start?filename=sim.gcode",
                                              method="POST", body=b"{}")
                self.assertEqual(response.code, 200)
                self.assertEqual(self.sim.printer.state["print_stats"]["state"], "printing")
            self.io_loop.run_sync(exercise)

        def test_websocket_subscribe_snapshot_once_and_changes_only(self):
            async def exercise():
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                subscribe = {"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                             "params": {"objects": {"print_stats": None, "virtual_sdcard": None,
                                                    "display_status": None}}, "id": 1}
                conn.write_message(json.dumps(subscribe))
                reply = json.loads(await conn.read_message())
                self.assertEqual(reply["id"], 1)
                self.assertIn("print_stats", reply["result"]["status"])
                self.assertIn("virtual_sdcard", reply["result"]["status"])
                # The pushes that follow are notify frames with CHANGES only.
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_status_update")
                self.assertEqual(set(frame["params"][0]), {"print_stats", "virtual_sdcard", "display_status"})
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_klippy_restart_wipes_subscriptions(self):
            async def exercise():
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                               "params": {"objects": {"print_stats": None}}, "id": 1}))
                await conn.read_message()
                self.sim.printer.klippy_restart()
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_klippy_ready")
                # The wipe holds the pump until a fresh subscribe re-arms it.
                conn.write_message(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                               "params": {"objects": {"print_stats": None}}, "id": 2}))
                reply = json.loads(await conn.read_message())
                self.assertEqual(reply["id"], 2)
                conn.close()
            self.io_loop.run_sync(exercise)


@unittest.skipIf(tornado is None, "tornado is not available in this environment")
class SimulatorCoverageTests(unittest.TestCase):
    """Every production endpoint builder must be served by the
    simulator's route table — a silent gap would let tier-2 scenarios
    prove nothing about real Moonraker."""

    def test_every_protocol_endpoint_has_a_simulator_route(self):
        from tests.harness.simulator import make_app
        base = "http://printer.local"
        app = make_app()
        endpoints = [
            status_endpoint(base),
            server_info_endpoint(base),
            objects_list_endpoint(base),
            gcode_script_endpoint(base),
            metadata_endpoint(base, "a.gcode"),
            download_endpoint(base, "a.gcode"),
            print_start_endpoint(base, "a.gcode"),
            delete_endpoint(base, "gcodes", "a.gcode"),
            directory_delete_endpoint(base, "gcodes", "sub"),
            directory_create_endpoint(base, "gcodes", "sub"),
            move_endpoint(base),
            websocket_endpoint(base),
        ]
        served = [rule.matcher.regex.pattern for rule in app.wildcard_router.rules]
        for endpoint in endpoints:
            if endpoint.startswith("ws"):
                self.assertTrue(any("/websocket" in pattern for pattern in served),
                                f"websocket endpoint {endpoint!r} has no simulator route")
                continue
            path = endpoint if endpoint.startswith("/") else "/" + endpoint.split("/", 3)[-1]
            import re
            matched = any(re.match(pattern + "$", path) is not None for pattern in served)
            self.assertTrue(matched, f"endpoint {endpoint!r} has no simulator route ({served})")


if __name__ == "__main__":
    unittest.main()

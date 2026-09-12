"""The Moonraker simulator's contract tests: the real protocols, the
one-permitted-fake's fidelity. Skipped wherever tornado is absent (the
stdlib host and the pinned dev image; the harness image pins it)."""
from __future__ import annotations

import json
import time
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
                # The pushes that follow are notify frames with CHANGES
                # only (a real Moonraker never pushes an unchanged
                # object) — drive a standby change that touches
                # print_stats alone to make a frame happen.
                self.sim.printer.scenario(print_stats={**self.sim.printer.state["print_stats"],
                                                       "message": "sim-note"})
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_status_update")
                self.assertEqual(set(frame["params"][0]), {"print_stats"})
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_ledger_records_requests_and_stats(self):
            async def exercise():
                client = AsyncHTTPClient()
                for _ in range(3):
                    await client.fetch(self.base + "/server/info")
                response = await client.fetch(self.base + "/ledger")
                body = json.loads(response.body)
                stats = body["result"]
                self.assertGreaterEqual(stats["total"], 3)
                self.assertGreaterEqual(stats["peak_inflight"], 1)
                self.assertGreaterEqual(len(body["entries"]), 3)
            self.io_loop.run_sync(exercise)

        def test_cold_start_recovers_to_printing(self):
            async def exercise():
                client = AsyncHTTPClient()
                # The pump is the lifecycle clock and it runs for
                # subscribers (the plugin is always subscribed in the
                # harness) — subscribe first, then arm the cold start.
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                               "params": {"objects": {"print_stats": None,
                                                                      "extruder": None}}, "id": 1}))
                await conn.read_message()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"cold_start": True, "extruder_ramp_deg_s": 500.0}))
                await client.fetch(self.base + "/printer/print/start?filename=scenario1.gcode",
                                   method="POST", body=b"{}")
                deadline = time.monotonic() + 5
                body = None
                while time.monotonic() < deadline:
                    body = json.loads((await client.fetch(self.base + "/harness/state")).body)
                    if body["result"]["print_stats"]["state"] == "printing":
                        break
                    await tornado.gen.sleep(0.1)
                state = body["result"]
                self.assertEqual(state["print_stats"]["state"], "printing")
                self.assertEqual(state["print_stats"]["message"], "")
                self.assertEqual(state["cold_start"], False)
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_broken_start_stays_error(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"cold_start": True, "broken_start": True,
                                                    "extruder_ramp_deg_s": 500.0}))
                await client.fetch(self.base + "/printer/print/start?filename=scenario1.gcode",
                                   method="POST", body=b"{}")
                body = json.loads((await client.fetch(self.base + "/harness/state")).body)
                state = body["result"]
                self.assertEqual(state["print_stats"]["state"], "error")
                self.assertIn("minimum temp", state["print_stats"]["message"])
            self.io_loop.run_sync(exercise)

        def test_download_serves_parseable_gcode(self):
            async def exercise():
                client = AsyncHTTPClient()
                response = await client.fetch(
                    self.base + "/server/files/gcodes/scenario1.gcode")
                body = response.body.decode("utf-8")
                self.assertIn(";LAYER_COUNT:40", body)
                self.assertIn(";LAYER:39", body)
                self.assertIn("M73 P100", body)
                self.assertGreater(len(body), 2000)
            self.io_loop.run_sync(exercise)

        def test_connection_count_and_files_listing(self):
            async def exercise():
                client = AsyncHTTPClient()
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                body = json.loads((await client.fetch(self.base + "/harness/state")).body)
                self.assertEqual(body["result"]["connections"], 1)
                listing = json.loads((await client.fetch(
                    self.base + "/server/files/directory?path=gcodes&extended=true")).body)
                result = listing["result"]
                self.assertIn("files", result)
                self.assertIn("disk_usage", result)
                self.assertEqual(result["files"][0]["filename"], "scenario1.gcode")
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_push_patch_carries_every_subscribed_object(self):
            async def exercise():
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                               "params": {"objects": {"configfile": None,
                                                                      "fan": None}}, "id": 1}))
                await conn.read_message()
                # The regression the tier-2 i6/h6 calibration exposed:
                # the pump once diffed only five hard-coded objects and
                # silently dropped configfile/fan changes.
                self.sim.printer.scenario(configfile={"save_config_pending": True,
                                                      "save_config_pending_items": {}})
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_status_update")
                self.assertIn("configfile", frame["params"][0])
                self.sim.printer.scenario(fan={"speed": 0.4})
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_status_update")
                self.assertIn("fan", frame["params"][0])
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_reset_clears_state_arms_and_ledger(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"require_api_key": True,
                                                    "refuse_subscribe": "down",
                                                    "console_lines": [{"type": "response",
                                                                       "message": "// x", "time": 1.0}],
                                                    "print_stats": {"state": "printing",
                                                                    "filename": "x.gcode"}}))
                # The 401 the arm produces still lands in the ledger
                # (prepare records before the auth gate) — the reset
                # must clear it.
                from tornado.httpclient import HTTPClientError
                try:
                    await client.fetch(self.base + "/server/info")
                except HTTPClientError:
                    pass
                await client.fetch(self.base + "/harness/reset", method="POST", body=b"{}")
                state = json.loads((await client.fetch(self.base + "/harness/state")).body)["result"]
                self.assertFalse(state["require_api_key"])
                self.assertEqual(state["print_stats"]["state"], "standby")
                self.assertEqual(self.sim.printer.refuse_subscribe, "")
                self.assertEqual(self.sim.printer.ledger, [])
                self.assertLessEqual(len(self.sim.printer.console_lines), 1)
            self.io_loop.run_sync(exercise)

        def test_pause_resume_cancel_routes_move_print_stats(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"print_stats": {"state": "printing",
                                                                    "filename": "x.gcode"}}))
                for route, expected in (("print/pause", "paused"), ("print/resume", "printing"),
                                        ("print/cancel", "cancelled")):
                    await client.fetch(self.base + f"/printer/{route}", method="POST", body=b"{}")
                    state = json.loads((await client.fetch(self.base + "/harness/state")).body)["result"]
                    self.assertEqual(state["print_stats"]["state"], expected, route)
            self.io_loop.run_sync(exercise)

        def test_delete_removes_the_file_from_the_listing(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/server/files/gcodes/scenario1.gcode",
                                   method="DELETE")
                listing = json.loads((await client.fetch(
                    self.base + "/server/files/directory?path=gcodes&extended=true")).body)
                names = [entry["filename"] for entry in listing["result"]["files"]]
                self.assertNotIn("scenario1.gcode", names)
                self.assertIn("benchy.gcode", names)
            self.io_loop.run_sync(exercise)

        def test_ws_upgrade_refuses_without_the_key(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"require_api_key": True}))
                from tornado.httpclient import HTTPClientError
                with self.assertRaises(HTTPClientError) as caught:
                    await tornado.websocket.websocket_connect(
                        f"ws://127.0.0.1:{self.sim.port}/websocket")
                self.assertEqual(caught.exception.code, 401)
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

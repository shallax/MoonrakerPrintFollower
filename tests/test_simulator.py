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

        def test_power_devices_arm_serves_both_lanes(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"power_devices": [
                                       {"device": "DFU", "status": "on",
                                        "locked_while_printing": False},
                                       {"device": "Printer", "status": "off",
                                        "locked_while_printing": True}]}))
                response = await client.fetch(self.base + "/machine/device_power/devices")
                self.assertEqual(json.loads(response.body)["result"]["devices"][0]["device"],
                                 "DFU")
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0",
                                               "method": "machine.device_power.devices",
                                               "id": 2}))
                reply = json.loads(await conn.read_message())
                self.assertEqual(reply["result"]["devices"][1]["status"], "off")
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_presets_arm_serves_the_database_value(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"presets_value": {
                                       "presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}))
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0",
                                               "method": "server.database.get_item",
                                               "params": {"namespace": "mainsail", "key": "presets"},
                                               "id": 3}))
                reply = json.loads(await conn.read_message())
                self.assertEqual(reply["result"]["value"]["presets"]["fast"]["name"], "Fast")
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_gcode_stream_serves_the_whole_file_in_chunks(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"gcode_stream_ms": 30}))
                started = time.monotonic()
                response = await client.fetch(self.base + "/server/files/gcodes/scenario1.gcode")
                elapsed = time.monotonic() - started
                self.assertEqual(response.code, 200)
                self.assertEqual(len(response.body), len(self.sim.printer.gcode_bytes))
                # 8734 bytes / 256-byte chunks ≈ 35 chunks at 30 ms:
                # the stream must actually take time, not one-shot.
                self.assertGreaterEqual(elapsed, 0.4)
            self.io_loop.run_sync(exercise)

        def test_device_power_post_real_semantics(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"power_devices": [
                                       {"device": "DFU", "status": "on", "locked_while_printing": False},
                                       {"device": "Printer", "status": "off", "locked_while_printing": True}]}))
                response = await client.fetch(self.base + "/machine/device_power/device",
                                              method="POST",
                                              body=json.dumps({"device": "DFU", "action": "off"}))
                # The reply is keyed by the device name (real Moonraker).
                self.assertEqual(json.loads(response.body)["result"]["DFU"]["status"], "off")
                # A no-op action is a 400.
                noop = await client.fetch(self.base + "/machine/device_power/device",
                                          method="POST",
                                          body=json.dumps({"device": "DFU", "action": "off"}))
                self.assertEqual(json.loads(noop.body)["error"]["code"], 400)
                # In STANDBY a locked device toggles fine (real Moonraker
                # only refuses while a print is active).
                standby = await client.fetch(self.base + "/machine/device_power/device",
                                             method="POST",
                                             body=json.dumps({"device": "Printer", "action": "on"}))
                self.assertEqual(json.loads(standby.body)["result"]["Printer"]["status"], "on")
                # While PRINTING the locked device refuses.
                self.sim.printer.scenario(print_stats={**self.sim.printer.state["print_stats"],
                                                       "state": "printing"})
                refused = await client.fetch(self.base + "/machine/device_power/device",
                                             method="POST",
                                             body=json.dumps({"device": "Printer", "action": "off"}))
                self.assertEqual(json.loads(refused.body)["error"]["code"], 403)
            self.io_loop.run_sync(exercise)

        def test_database_item_serves_over_http(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"presets_value": {
                                       "presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}))
                response = await client.fetch(
                    self.base + "/server/database/item?namespace=mainsail&key=presets")
                self.assertEqual(json.loads(response.body)["result"]["value"]["presets"]["fast"]["name"],
                                 "Fast")
            self.io_loop.run_sync(exercise)

        def test_pause_script_pauses_and_the_arm_refuses(self):
            async def exercise():
                client = AsyncHTTPClient()
                self.sim.printer.scenario(print_stats={**self.sim.printer.state["print_stats"],
                                                       "state": "printing"})
                response = await client.fetch(self.base + "/printer/gcode/script",
                                              method="POST", body=json.dumps({"script": "PAUSE"}))
                self.assertEqual(json.loads(response.body)["result"], "ok")
                # The real ordering: the ack first, the state on a
                # later tick.
                await tornado.gen.sleep(0.2)
                self.assertEqual(self.sim.printer.state["print_stats"]["state"], "paused")
                self.sim.printer.scenario(print_stats={**self.sim.printer.state["print_stats"],
                                                       "state": "printing"},
                                          fail_pause_script=True)
                refused = await client.fetch(self.base + "/printer/gcode/script",
                                             method="POST", body=json.dumps({"script": "PAUSE"}))
                self.assertEqual(json.loads(refused.body)["error"]["code"], 400)
                self.assertEqual(self.sim.printer.state["print_stats"]["state"], "printing")
            self.io_loop.run_sync(exercise)

        def test_firmware_restart_broadcasts_klippy_ready(self):
            async def exercise():
                client = AsyncHTTPClient()
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                await client.fetch(self.base + "/printer/firmware_restart", method="POST", body=b"{}")
                frame = json.loads(await conn.read_message())
                self.assertEqual(frame["method"], "notify_klippy_ready")
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_route_delay_holds_only_its_route(self):
            async def exercise():
                client = AsyncHTTPClient()
                self.sim.printer.scenario(route_delay_ms={"server/info": 600.0})
                started = time.monotonic()
                delayed = client.fetch(self.base + "/server/info")
                fast = client.fetch(self.base + "/server/webcams/list")
                # The held route must not stall the other lanes: the
                # fast response lands while server/info still sleeps.
                await fast
                fast_elapsed = time.monotonic() - started
                await delayed
                delayed_elapsed = time.monotonic() - started
                self.assertGreaterEqual(delayed_elapsed, 0.5)
                self.assertLess(fast_elapsed, 0.4)
            self.io_loop.run_sync(exercise)

        def test_corrupt_frame_arm_sends_garbage_on_the_next_push(self):
            async def exercise():
                client = AsyncHTTPClient()
                conn = await tornado.websocket.websocket_connect(
                    f"ws://127.0.0.1:{self.sim.port}/websocket")
                conn.write_message(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                               "params": {"objects": {"print_stats": None}}, "id": 1}))
                await conn.read_message()  # the snapshot reply
                await client.fetch(self.base + "/harness/scenario", method="POST",
                                   body=json.dumps({"corrupt_frame_once": True,
                                                    "print_stats": {"state": "printing"}}))
                frame = await conn.read_message()
                self.assertNotIn("notify_status_update", str(frame))
                conn.close()
            self.io_loop.run_sync(exercise)

        def test_files_metadata_serves_the_listing_entry(self):
            async def exercise():
                client = AsyncHTTPClient()
                response = await client.fetch(
                    self.base + "/server/files/metadata?filename=scenario1.gcode")
                body = json.loads(response.body)["result"]
                self.assertEqual(body["filename"], "scenario1.gcode")
                self.assertIn("estimated_time", body)
                self.assertIn("print_start_time", body)
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
                # The regression the suite i6/h6 calibration exposed:
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

        def test_move_renames_the_file_in_the_listing(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/server/files/move", method="POST",
                                   body=json.dumps({"source": "gcodes/benchy.gcode",
                                                    "dest": "gcodes/benchy-renamed.gcode"}))
                listing = json.loads((await client.fetch(
                    self.base + "/server/files/directory?path=gcodes&extended=true")).body)
                names = [entry["filename"] for entry in listing["result"]["files"]]
                self.assertIn("benchy-renamed.gcode", names)
                self.assertNotIn("benchy.gcode", names)
            self.io_loop.run_sync(exercise)

        def test_gcode_script_echoes_into_the_console(self):
            async def exercise():
                client = AsyncHTTPClient()
                await client.fetch(self.base + "/printer/gcode/script", method="POST",
                                   body=json.dumps({"script": "RENDERED-CONSOLE"}))
                store = json.loads((await client.fetch(
                    self.base + "/server/gcode_store")).body)
                messages = [line.get("message")
                            for line in store["result"]["gcode_store"]]
                self.assertIn("RENDERED-CONSOLE", messages)
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
    simulator's route table — a silent gap would let suite scenarios
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

import unittest

from plugins.MoonrakerProtocol import (
    RemoteFileIdentity,
    download_endpoint,
    live_position_in_gcode_space,
    metadata_endpoint,
    objects_list_endpoint,
    parse_file_identity,
    same_origin,
    server_info_endpoint,
    status_endpoint,
)


class ProtocolTests(unittest.TestCase):
    def test_status_query_requests_motion_report(self):
        url = status_endpoint("http://printer.example.invalid:7125")
        self.assertIn("print_stats", url)
        self.assertIn("gcode_move", url)
        self.assertIn("virtual_sdcard", url)
        self.assertIn("motion_report", url)
        self.assertIn("bed_mesh", url)

    def test_capability_probe_endpoints(self):
        self.assertEqual(server_info_endpoint("http://printer.example.invalid:7125/"), "http://printer.example.invalid:7125/server/info")
        self.assertEqual(objects_list_endpoint("http://printer.example.invalid:7125/"), "http://printer.example.invalid:7125/printer/objects/list")

    def test_filename_paths_are_url_encoded_but_keep_directories(self):
        metadata = metadata_endpoint("http://printer.example.invalid", "folder/My part #1.gcode")
        download = download_endpoint("http://printer.example.invalid", "folder/My part #1.gcode")
        self.assertIn("folder/My%20part%20%231.gcode", metadata)
        self.assertIn("folder/My%20part%20%231.gcode", download)

    def test_metadata_identity_parsing(self):
        payload = {"result": {"filename": "a.gcode", "size": 321, "modified": 12.5, "uuid": "u-1"}}
        identity = parse_file_identity("fallback.gcode", payload, 10)
        self.assertEqual(identity.filename, "a.gcode")
        self.assertEqual(identity.size, 321)
        self.assertEqual(identity.modified, 12.5)
        self.assertEqual(identity.uuid, "u-1")

    def test_metadata_identity_fallback(self):
        identity = parse_file_identity("fallback.gcode", {"result": {}}, 456)
        self.assertEqual(identity.filename, "fallback.gcode")
        self.assertEqual(identity.size, 456)

    def test_live_position_is_converted_to_gcode_space(self):
        motion = {"live_position": [110.0, 220.0, 5.4, 0.0]}
        move = {"homing_origin": [10.0, 20.0, 0.4, 0.0]}
        self.assertEqual(live_position_in_gcode_space(motion, move), (100.0, 200.0, 5.0))

    def test_live_position_honours_axis_map(self):
        motion = {"live_position": [9.0, 30.0, 10.0, 20.0, 0.0]}
        move = {
            "axis_map": {"X": 2, "Y": 3, "Z": 1, "E": 4},
            "homing_origin": [0.0, 1.0, 2.0, 3.0],
        }
        self.assertEqual(live_position_in_gcode_space(motion, move), (8.0, 17.0, 29.0))

    def test_invalid_live_position_returns_none(self):
        self.assertIsNone(live_position_in_gcode_space({}, {}))
        self.assertIsNone(live_position_in_gcode_space({"live_position": [1]}, {}))


class FileIdentityTests(unittest.TestCase):
    def test_remote_identity_prefers_uuid(self):
        a = RemoteFileIdentity("same.gcode", 100, 1.0, "abc")
        b = RemoteFileIdentity("same.gcode", 100, 999.0, "abc")
        self.assertEqual(a.stable_key(), b.stable_key())
        self.assertEqual(a.stable_key(), "uuid:abc")

    def test_remote_identity_fallback_distinguishes_modified(self):
        a = RemoteFileIdentity("same.gcode", 100, 1.0, "")
        b = RemoteFileIdentity("same.gcode", 100, 2.0, "")
        self.assertNotEqual(a.stable_key(), b.stable_key())

    def test_remote_identity_job_match_allows_unknown_size(self):
        identity = RemoteFileIdentity("a.gcode", 100, 1.0, "")
        self.assertTrue(identity.matches_job("a.gcode", 100))
        self.assertTrue(identity.matches_job("a.gcode", 0))
        self.assertFalse(identity.matches_job("a.gcode", 101))
        self.assertFalse(identity.matches_job("b.gcode", 100))


class PrintStartPathTests(unittest.TestCase):
    def test_print_start_path_is_root_exclusive(self):
        # Round-2 D4: print/start wants the SD-card form — never the
        # "gcodes/" root the file endpoints carry.
        from plugins.MoonrakerProtocol import print_start_path, print_start_endpoint
        self.assertEqual(print_start_path("prints/benchy.gcode"), "prints/benchy.gcode")
        self.assertEqual(print_start_path("/prints/benchy.gcode"), "prints/benchy.gcode")
        self.assertEqual(print_start_path("  benchy.gcode  "), "benchy.gcode")
        self.assertEqual(print_start_endpoint("http://printer-a", "prints/a b.gcode"),
                         "http://printer-a/printer/print/start?filename=prints/a%20b.gcode")


class SameOriginTests(unittest.TestCase):
    """The API key rides only the printer's own origin (round-2 F2)."""

    BASE = "http://printer.local:7125"

    def test_identical_origin_is_same(self):
        self.assertTrue(same_origin(self.BASE, "http://printer.local:7125/server/files/list"))
        self.assertTrue(same_origin(self.BASE, "http://printer.local:7125/"))

    def test_default_port_matches_explicit_port(self):
        self.assertTrue(same_origin("http://printer.local", "http://printer.local:80/x"))
        self.assertTrue(same_origin("https://printer.local", "https://printer.local:443/x"))

    def test_different_host_is_foreign(self):
        self.assertFalse(same_origin(self.BASE, "http://cam.local:7125/x"))

    def test_different_port_is_foreign(self):
        self.assertFalse(same_origin(self.BASE, "http://printer.local:8080/webcam/?action=stream"))

    def test_different_scheme_is_foreign(self):
        self.assertFalse(same_origin(self.BASE, "https://printer.local:7125/x"))

    def test_embedded_userinfo_is_foreign(self):
        # http://printer.local:7125@evil.com passes string-prefix
        # checks but its host is evil.com.
        self.assertFalse(same_origin(self.BASE, "http://printer.local:7125@evil.com/x"))

    def test_trailing_dot_is_foreign(self):
        # printer.local. and printer.local are the same host per DNS,
        # but the predicate fails closed rather than guess.
        self.assertFalse(same_origin(self.BASE, "http://printer.local.:7125/x"))

    def test_malformed_inputs_are_foreign(self):
        self.assertFalse(same_origin("", "http://printer.local/x"))
        self.assertFalse(same_origin(self.BASE, ""))
        self.assertFalse(same_origin("not a url", self.BASE))
        self.assertFalse(same_origin(self.BASE, "//printer.local:7125/x"))

    def test_case_variance_is_same_origin(self):
        self.assertTrue(same_origin(self.BASE, "HTTP://PRINTER.LOCAL:7125/x"))


if __name__ == "__main__":
    unittest.main()

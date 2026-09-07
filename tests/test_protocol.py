import unittest

from plugins.MoonrakerProtocol import (
    RemoteFileIdentity,
    download_endpoint,
    live_position_in_gcode_space,
    metadata_endpoint,
    objects_list_endpoint,
    parse_file_identity,
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


if __name__ == "__main__":
    unittest.main()

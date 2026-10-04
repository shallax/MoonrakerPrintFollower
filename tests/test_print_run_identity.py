"""Only the live server history row can attest a durable print identity."""
from copy import deepcopy
import unittest
from mpf.printing.PrintRunIdentity import attest_active_run


class PrintRunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(job_id="000001", start_time=1000.5, filename="part.gcode", status="in_progress", end_time=None)

    def resolve(self, row=None, binding="printer-one"):
        return attest_active_run({"result": {"jobs": [self.row if row is None else row]}}, "part.gcode", binding)

    def test_same_run_is_stable_and_binding_and_server_run_changes_are_distinct(self):
        self.assertEqual(self.resolve(), self.resolve(deepcopy(self.row)))
        self.assertNotEqual(self.resolve(), self.resolve(binding="printer-two"))
        self.assertNotEqual(self.resolve(), self.resolve({**self.row, "job_id": "000002"}))

    def test_no_finished_interrupted_previous_file_or_invalid_time_is_attested(self):
        for patch in ({"status": "interrupted"}, {"status": "completed"}, {"end_time": 2000},
                      {"filename": "old.gcode"}, {"job_id": ""}, {"start_time": True},
                      {"start_time": float('inf')}, {"start_time": -1}):
            with self.subTest(patch=patch):
                self.assertIsNone(self.resolve({**self.row, **patch}))
        for payload in (None, {}, {"result": {"jobs": []}}):
            self.assertIsNone(attest_active_run(payload, "part.gcode", "printer"))

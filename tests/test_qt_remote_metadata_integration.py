"""Executable qt remotefileservicemetadatatests contracts."""
from tests import qt_integration_support as harness

class RemoteFileServiceMetadataTests(harness.RemoteFileServiceMetadataTests):
    def test_metadata_failure_retries_after_backoff_and_completes_on_success(self):
        self.service.METADATA_RETRY_DELAYS_MS = (5,)
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 1)

        # A failure installs a fallback download identity but not completeness.
        self.transport.requests[-1].callback(None, "connection refused")
        self.assertEqual(self.service.identity.filename, "part.gcode")
        self.assertEqual(self.service.identity.size, 1000)
        self.assertFalse(self.service.metadata_complete)

        # Inside the backoff window a retry for the same job is suppressed.
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 1)

        # After the window the same job retries; success completes metadata.
        self._wait_for_the_backoff_to_expire()
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 2)
        self.transport.requests[-1].callback({"result": {"estimated_time": 3600, "uuid": "u-1"}}, None)
        self.assertTrue(self.service.metadata_complete)
        self.assertEqual(self.service.identity.uuid, "u-1")
        self.assertEqual(self.service.metadata["estimated_time"], 3600)

        # Complete metadata is never refetched for the same job.
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 2)

    def test_bind_resets_metadata_state_for_a_new_job(self):
        self.service.request_metadata()
        self.transport.requests[-1].callback({"result": {"uuid": "u-1"}}, None)
        self.assertTrue(self.service.metadata_complete)

        self.service.bind(("part.gcode", 1000, 2))
        self.assertFalse(self.service.metadata_complete)
        self.assertIsNone(self.service.identity)



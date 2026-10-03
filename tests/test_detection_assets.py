"""Pinned and cancellable local inference asset installation."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, sentinel
from urllib.request import Request
import zipfile

from mpf.detection import AssetInstaller, DetectionAssets


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


class CancelAfter:
    def __init__(self, checks):
        self.checks = checks

    def is_set(self):
        self.checks -= 1
        return self.checks <= 0


class DetectionAssetsTests(unittest.TestCase):
    def test_network_requests_use_curas_bundled_certificate_authority(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        url = "https://files.pythonhosted.org/packages/onnxruntime-test.whl"
        payload = json.dumps({"urls": [{"filename": wheel.filename,
                                        "digests": {"sha256": wheel.sha256},
                                        "size": wheel.size, "url": url}]}).encode()
        with patch.dict(sys.modules, {"certifi": SimpleNamespace(where=lambda: "/cura/certifi/cacert.pem")}), \
                patch.object(AssetInstaller.ssl, "create_default_context",
                             return_value=sentinel.trusted_context) as context, \
                patch.object(AssetInstaller, "urlopen",
                             return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)) as open_url:
            self.assertEqual(AssetInstaller.wheel_url(wheel), url)
        context.assert_called_once_with(cafile="/cura/certifi/cacert.pem")
        self.assertIs(open_url.call_args.kwargs["context"], sentinel.trusted_context)

    def test_supported_host_without_numpy_is_ineligible_before_offer(self):
        python = SimpleNamespace(platform="darwin",
                                 version_info=SimpleNamespace(major=3, minor=12))
        with patch.object(DetectionAssets, "sys", python), \
                patch.object(DetectionAssets.platform, "machine", return_value="arm64"), \
                patch.object(DetectionAssets.platform, "mac_ver", return_value=("13.0", "", "")), \
                patch.object(DetectionAssets, "physical_memory", return_value=8 * 1024 ** 3), \
                patch.object(DetectionAssets, "find_spec", return_value=None) as spec:
            with self.assertRaisesRegex(ValueError, "needs NumPy"):
                DetectionAssets.host_wheel()
        spec.assert_called_once_with("numpy")

    def test_runtime_matrix_is_pinned_for_every_supported_cura_abi(self):
        self.assertEqual(len(DetectionAssets._WHEELS), 15)
        for (abi, platform), (digest, size) in DetectionAssets._WHEELS.items():
            self.assertIn(abi, ("310", "311", "312"))
            self.assertEqual(len(digest), 64)
            self.assertGreater(size, 10 * 1024 ** 2)
            self.assertIn(platform.split("_")[0], ("macosx", "manylinux", "win"))

    def test_host_selection_matches_pinned_runtime_on_macos_linux_and_windows(self):
        hosts = (
            ("darwin", "arm64", "macosx_13_0_arm64"),
            ("linux", "x86_64", "manylinux_2_27_x86_64.manylinux_2_28_x86_64"),
            ("win32", "x86_64", "win_amd64"),
        )
        for system, machine, platform_tag in hosts:
            with self.subTest(system=system), \
                    patch.object(DetectionAssets.sys, "platform", system), \
                    patch.object(DetectionAssets.sys, "version_info",
                                 SimpleNamespace(major=3, minor=11)), \
                    patch.object(DetectionAssets.platform, "machine", return_value=machine), \
                    patch.object(DetectionAssets.platform, "mac_ver",
                                 return_value=("13.0", "", "")), \
                    patch.object(DetectionAssets.platform, "libc_ver",
                                 return_value=("glibc", "2.31")), \
                    patch.object(DetectionAssets.sys, "getwindowsversion",
                                 create=True, return_value=SimpleNamespace(major=10)), \
                    patch.object(DetectionAssets, "physical_memory",
                                 return_value=8 * 1024 ** 3), \
                    patch.object(DetectionAssets, "find_spec", return_value=sentinel.numpy):
                wheel = DetectionAssets.host_wheel()
            digest, size = DetectionAssets._WHEELS[("311", platform_tag)]
            self.assertEqual((wheel.sha256, wheel.size), (digest, size))
            self.assertIn(platform_tag, wheel.filename)

    def test_download_checks_size_hash_and_cleans_cancelled_partial_file(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            data = b"pinned camera model"
            digest = hashlib.sha256(data).hexdigest()
            path = os.path.join(directory, "model")
            url = "https://www.obico.io/static_pub/model.onnx"
            cancelled = threading.Event()
            received = []
            with patch.object(AssetInstaller, "_open_download", return_value=Response(data, url)):
                AssetInstaller.download(url, path, len(data), digest, cancelled,
                                        lambda value, total: received.append((value, total)))
            self.assertEqual(Path(path).read_bytes(), data)
            self.assertEqual(received[-1], (len(data), len(data)))
            with patch.object(AssetInstaller, "_open_download", side_effect=AssertionError("cached asset fetched")):
                AssetInstaller.download(url, path, len(data), digest, cancelled,
                                        lambda *_: None)
            other = os.path.join(directory, "cancelled")
            cancelled.set()
            with patch.object(AssetInstaller, "_open_download", return_value=Response(data, url)):
                with self.assertRaises(AssetInstaller.DownloadCancelled):
                    AssetInstaller.download(url, other, len(data), digest, cancelled,
                                            lambda *_: None)
            self.assertFalse(os.path.exists(other))
            self.assertFalse(list(Path(directory).glob("*.part")))

    def test_model_accepts_any_https_cdn_only_if_the_payload_matches_its_pin(self):
        data = b"pinned model"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            target = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=Response(data, "https://regional-cdn.example/model")):
                AssetInstaller.download(DetectionAssets.MODEL_URL, target, len(data), digest,
                                        threading.Event(), lambda *_: None)
            self.assertEqual(Path(target).read_bytes(), data)
            rejected = os.path.join(directory, "rejected")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=Response(b"poison model", "https://other.example/model")):
                with self.assertRaisesRegex(ValueError, "integrity"):
                    AssetInstaller.download(DetectionAssets.MODEL_URL, rejected, len(data), digest,
                                            threading.Event(), lambda *_: None)
            self.assertFalse(os.path.exists(rejected))

    def test_redirects_must_remain_https_at_every_hop(self):
        handler = AssetInstaller._HTTPSRedirectHandler()
        source = Request(DetectionAssets.MODEL_URL)
        self.assertEqual(handler.redirect_request(
            source, None, 302, "Found", {}, "https://regional-cdn.example/model").full_url,
            "https://regional-cdn.example/model")
        with self.assertRaisesRegex(ValueError, "redirect away from HTTPS"):
            handler.redirect_request(source, None, 302, "Found", {}, "http://regional-cdn.example/model")
        with tempfile.TemporaryDirectory(dir=".") as directory:
            target = os.path.join(directory, "rejected")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=Response(b"pinned model", "http://regional-cdn.example/model")):
                with self.assertRaisesRegex(ValueError, "HTTPS after redirects"):
                    AssetInstaller.download(DetectionAssets.MODEL_URL, target,
                                            len(b"pinned model"), hashlib.sha256(b"pinned model").hexdigest(),
                                            threading.Event(), lambda *_: None)
            self.assertFalse(os.path.exists(target))

    def test_untrusted_runtime_index_is_rejected_before_download(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        url = "https://files.pythonhosted.org/packages/onnxruntime-test.whl"
        payload = json.dumps({"urls": [{"filename": wheel.filename,
                                        "digests": {"sha256": wheel.sha256},
                                        "size": wheel.size, "url": url}]}).encode()
        with patch.object(AssetInstaller, "urlopen",
                          return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)):
            self.assertEqual(AssetInstaller.wheel_url(wheel), url)
        with patch.object(AssetInstaller, "urlopen",
                          return_value=Response(payload, "https://attacker.example/index")):
            with self.assertRaisesRegex(ValueError, "index host"):
                AssetInstaller.wheel_url(wheel)

    def test_runtime_extraction_rejects_traversal_and_keeps_destination_absent(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr("onnxruntime/../escape.py", "unsafe")
            destination = os.path.join(directory, "runtime")
            with self.assertRaisesRegex(ValueError, "unsafe entry"):
                AssetInstaller.extract_runtime(archive, destination, threading.Event())
            self.assertFalse(os.path.exists(destination))
            self.assertFalse(os.path.exists(os.path.join(directory, "escape.py")))

    def test_installed_runtime_is_checked_against_the_archive_on_reuse(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            archive = os.path.join(directory, "runtime.whl")
            member = "onnxruntime/capi/onnxruntime_inference_collection.py"
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr(member, "pinned runtime")
            destination = os.path.join(directory, "runtime")
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            self.assertTrue(AssetInstaller.verify_runtime(archive, destination))
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            Path(destination, member).write_text("altered runtime", encoding="utf-8")
            self.assertFalse(AssetInstaller.verify_runtime(archive, destination))
            with self.assertRaisesRegex(ValueError, "integrity"):
                AssetInstaller.extract_runtime(archive, destination, threading.Event())

    def test_extract_rejects_high_decompression_ratio_and_cleans_stage(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
                wheel.writestr("onnxruntime/capi/onnxruntime_inference_collection.py",
                               b"x" * (2 * 1024 * 1024))
            with self.assertRaisesRegex(ValueError, "unsafe entry"):
                AssetInstaller.extract_runtime(archive, os.path.join(directory, "runtime"),
                                               threading.Event())
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_extract_interrupts_large_member_and_cleans_stage(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as wheel:
                wheel.writestr("onnxruntime/capi/onnxruntime_inference_collection.py",
                               b"x" * (2 * 1024 * 1024))
            with self.assertRaises(AssetInstaller.DownloadCancelled):
                AssetInstaller.extract_runtime(archive, os.path.join(directory, "runtime"),
                                               CancelAfter(5))
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_download_cancellation_after_first_chunk_removes_partial(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            data = b"x" * (128 * 1024)
            cancel = threading.Event()
            path = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=Response(data, "https://www.obico.io/model")):
                with self.assertRaises(AssetInstaller.DownloadCancelled):
                    AssetInstaller.download(
                        "https://www.obico.io/model", path, len(data),
                        hashlib.sha256(data).hexdigest(), cancel,
                        lambda *_: cancel.set(),
                    )
            self.assertFalse(os.path.exists(path))
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_cancelled_network_read_reports_cancellation_not_timeout(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            cancel = threading.Event()

            class TimedOut(Response):
                def read(self, _size=-1):
                    cancel.set()
                    raise TimeoutError("socket read timed out")

            path = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=TimedOut(b"", "https://www.obico.io/model")):
                with self.assertRaises(AssetInstaller.DownloadCancelled):
                    AssetInstaller.download("https://www.obico.io/model", path, 5,
                                            "a" * 64, cancel, lambda *_: None)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()

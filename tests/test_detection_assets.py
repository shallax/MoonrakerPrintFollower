"""Pinned and cancellable local inference asset installation."""

import ctypes
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
                patch.object(AssetInstaller, "_open_index",
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

    def test_host_wheel_refuses_platforms_older_than_its_pinned_runtime(self):
        cases = (
            ("darwin", ("12.6", "", ""), "macOS 13"),
            ("darwin", ("", "", ""), "macOS 13"),
            ("freebsd", ("13.0", "", ""), "operating system"),
        )
        for system, mac_ver, message in cases:
            with self.subTest(system=system, mac_ver=mac_ver), \
                    patch.object(DetectionAssets.sys, "platform", system), \
                    patch.object(DetectionAssets.sys, "version_info",
                                 SimpleNamespace(major=3, minor=11)), \
                    patch.object(DetectionAssets.platform, "machine", return_value="arm64"), \
                    patch.object(DetectionAssets.platform, "mac_ver", return_value=mac_ver), \
                    patch.object(DetectionAssets, "physical_memory", return_value=8 * 1024 ** 3), \
                    patch.object(DetectionAssets, "find_spec", return_value=sentinel.numpy):
                with self.assertRaisesRegex(ValueError, message):
                    DetectionAssets.host_wheel()

    def test_linux_host_wheel_needs_a_glibc_two_twenty_seven(self):
        for libc, version in (("glibc", "2.26"), ("musl", "1.2"), ("glibc", "2")):
            with self.subTest(libc=libc, version=version), \
                    patch.object(DetectionAssets.sys, "platform", "linux"), \
                    patch.object(DetectionAssets.sys, "version_info",
                                 SimpleNamespace(major=3, minor=11)), \
                    patch.object(DetectionAssets.platform, "machine", return_value="x86_64"), \
                    patch.object(DetectionAssets.platform, "libc_ver", return_value=(libc, version)), \
                    patch.object(DetectionAssets, "physical_memory", return_value=8 * 1024 ** 3), \
                    patch.object(DetectionAssets, "find_spec", return_value=sentinel.numpy):
                with self.assertRaisesRegex(ValueError, "glibc 2.27"):
                    DetectionAssets.host_wheel()

    def test_windows_host_wheel_needs_windows_ten_and_a_verified_architecture(self):
        for major, machine, message in ((6, "amd64", "Windows 10"), (10, "arm64", "No verified")):
            with self.subTest(major=major, machine=machine), \
                    patch.object(DetectionAssets.sys, "platform", "win32"), \
                    patch.object(DetectionAssets.sys, "version_info",
                                 SimpleNamespace(major=3, minor=11)), \
                    patch.object(DetectionAssets.platform, "machine", return_value=machine), \
                    patch.object(DetectionAssets.sys, "getwindowsversion", create=True,
                                 return_value=SimpleNamespace(major=major)), \
                    patch.object(DetectionAssets, "physical_memory", return_value=8 * 1024 ** 3), \
                    patch.object(DetectionAssets, "find_spec", return_value=sentinel.numpy):
                with self.assertRaisesRegex(ValueError, message):
                    DetectionAssets.host_wheel()

    def test_host_wheel_refuses_low_memory_unpinned_abi_and_a_broken_numpy_probe(self):
        def refusal(message, python=(3, 11), memory=8 * 1024 ** 3, find_spec=None):
            """A host that passes every gate but the one under test."""
            numpy = find_spec if find_spec is not None else patch.object(
                DetectionAssets, "find_spec", return_value=sentinel.numpy)
            with patch.object(DetectionAssets.sys, "platform", "darwin"), \
                    patch.object(DetectionAssets.sys, "version_info",
                                 SimpleNamespace(major=python[0], minor=python[1])), \
                    patch.object(DetectionAssets.platform, "machine", return_value="arm64"), \
                    patch.object(DetectionAssets.platform, "mac_ver",
                                 return_value=("13.0", "", "")), \
                    patch.object(DetectionAssets, "physical_memory", return_value=memory), \
                    numpy:
                with self.assertRaisesRegex(ValueError, message):
                    DetectionAssets.host_wheel()

        refusal("4 GiB", memory=2 * 1024 ** 3)
        refusal("Python 3.13", python=(3, 13))
        refusal("cannot locate NumPy", find_spec=patch.object(
            DetectionAssets, "find_spec", side_effect=ValueError("no sys.path hook")))

    def test_physical_memory_reads_the_windows_status_block(self):
        seen = []

        def status_block(status):
            # byref stands in as itself so the fake can fill the field it reports.
            seen.append((status.length, ctypes.sizeof(status)))
            status.total_physical = 16 * 1024 ** 3
            return 1

        with patch.object(DetectionAssets.sys, "platform", "win32"), \
                patch.object(DetectionAssets.ctypes, "byref", side_effect=lambda value: value), \
                patch.object(DetectionAssets.ctypes, "windll", create=True,
                             new=SimpleNamespace(kernel32=SimpleNamespace(
                                 GlobalMemoryStatusEx=status_block))):
            self.assertEqual(DetectionAssets.physical_memory(), 16 * 1024 ** 3)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], seen[0][1])
        self.assertGreater(seen[0][0], 0)
        with patch.object(DetectionAssets.sys, "platform", "win32"), \
                patch.object(DetectionAssets.ctypes, "windll", create=True,
                             new=SimpleNamespace(kernel32=SimpleNamespace(
                                 GlobalMemoryStatusEx=lambda _status: 0))):
            self.assertEqual(DetectionAssets.physical_memory(), 0)

    def test_physical_memory_falls_back_to_zero_when_the_host_cannot_report(self):
        pages = {"SC_PHYS_PAGES": 512, "SC_PAGE_SIZE": 4096}
        with patch.object(DetectionAssets.sys, "platform", "linux"), \
                patch.object(DetectionAssets.os, "sysconf", create=True,
                             side_effect=lambda name: pages[name]):
            self.assertEqual(DetectionAssets.physical_memory(), 512 * 4096)
        for error in (OSError("no sysconf"), ValueError("unknown name"), AttributeError("no os")):
            with self.subTest(error=error), \
                    patch.object(DetectionAssets.sys, "platform", "linux"), \
                    patch.object(DetectionAssets.os, "sysconf", create=True, side_effect=error):
                self.assertEqual(DetectionAssets.physical_memory(), 0)

    def test_installed_paths_live_under_the_versioned_asset_directory(self):
        runtime, model = DetectionAssets.installed_paths(os.path.join("root", "cura"))
        directory = os.path.join("root", "cura", "detection", DetectionAssets.ASSET_VERSION)
        self.assertEqual(runtime, os.path.join(directory, "runtime"))
        self.assertEqual(model, os.path.join(directory, "model-weights.onnx"))

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
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
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
        with patch.object(AssetInstaller, "_open_index",
                          return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)):
            self.assertEqual(AssetInstaller.wheel_url(wheel), url)
        with patch.object(AssetInstaller, "_open_index",
                          return_value=Response(payload, "https://attacker.example/index")):
            with self.assertRaisesRegex(ValueError, "index host"):
                AssetInstaller.wheel_url(wheel)

    def test_runtime_extraction_rejects_traversal_and_keeps_destination_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr("onnxruntime/../escape.py", "unsafe")
            destination = os.path.join(directory, "runtime")
            with self.assertRaisesRegex(ValueError, "unsafe entry"):
                AssetInstaller.extract_runtime(archive, destination, threading.Event())
            self.assertFalse(os.path.exists(destination))
            self.assertFalse(os.path.exists(os.path.join(directory, "escape.py")))

    def test_installed_runtime_is_checked_against_the_archive_on_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
                wheel.writestr("onnxruntime/capi/onnxruntime_inference_collection.py",
                               b"x" * (2 * 1024 * 1024))
            with self.assertRaisesRegex(ValueError, "unsafe entry"):
                AssetInstaller.extract_runtime(archive, os.path.join(directory, "runtime"),
                                               threading.Event())
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_extract_interrupts_large_member_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as wheel:
                wheel.writestr("onnxruntime/capi/onnxruntime_inference_collection.py",
                               b"x" * (2 * 1024 * 1024))
            with self.assertRaises(AssetInstaller.DownloadCancelled):
                AssetInstaller.extract_runtime(archive, os.path.join(directory, "runtime"),
                                               CancelAfter(5))
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_download_cancellation_after_first_chunk_removes_partial(self):
        with tempfile.TemporaryDirectory() as directory:
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

    def test_open_download_builds_an_https_opener_with_a_three_second_timeout(self):
        opened = []

        def open_request(request, timeout):
            opened.append((request, timeout))
            return sentinel.response

        with patch.object(AssetInstaller, "HTTPSHandler") as handler, \
                patch.object(AssetInstaller, "_trusted_context", return_value=sentinel.context), \
                patch.object(AssetInstaller, "build_opener",
                             return_value=SimpleNamespace(open=open_request)) as builder:
            self.assertIs(AssetInstaller._open_download(
                "https://files.pythonhosted.org/packages/onnxruntime-test.whl"), sentinel.response)
        self.assertIs(builder.call_args.args[0], handler.return_value)
        self.assertIs(handler.call_args.kwargs["context"], sentinel.context)
        self.assertIsInstance(builder.call_args.args[1], AssetInstaller._HTTPSRedirectHandler)
        request, timeout = opened[0]
        self.assertEqual((request.full_url, timeout),
                         ("https://files.pythonhosted.org/packages/onnxruntime-test.whl", 3))

    def test_wheel_url_reports_cancellation_before_and_after_the_index_read(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        payload = json.dumps({"urls": [{"filename": wheel.filename,
                                        "digests": {"sha256": wheel.sha256}, "size": wheel.size,
                                        "url": "https://files.pythonhosted.org/packages/onnxruntime-test.whl"}]}).encode()
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(AssetInstaller, "_open_index",
                          side_effect=AssertionError("index fetched while cancelled")):
            with self.assertRaises(AssetInstaller.DownloadCancelled):
                AssetInstaller.wheel_url(wheel, cancelled)
        with patch.object(AssetInstaller, "_open_index",
                          return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)):
            with self.assertRaises(AssetInstaller.DownloadCancelled):
                AssetInstaller.wheel_url(wheel, CancelAfter(2))

    def test_index_transport_failures_are_failures_unless_the_setup_was_cancelled(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        cancel = threading.Event()
        with patch.object(AssetInstaller, "_open_index", side_effect=TimeoutError("index timed out")):
            with self.assertRaises(TimeoutError):
                AssetInstaller.wheel_url(wheel, cancel)

        def cancel_while_reading(*_args, **_kwargs):
            cancel.set()
            raise TimeoutError("index timed out")

        with patch.object(AssetInstaller, "_open_index", side_effect=cancel_while_reading):
            with self.assertRaises(AssetInstaller.DownloadCancelled) as raised:
                AssetInstaller.wheel_url(wheel, cancel)
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)

    def test_wheel_url_refuses_an_oversized_index_and_unpinned_release_names(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        release = {"filename": wheel.filename, "digests": {"sha256": wheel.sha256},
                   "size": wheel.size,
                   "url": "https://files.pythonhosted.org/packages/onnxruntime-test.whl"}
        cases = (("empty", [], "not available"),
                 ("duplicate", [release, release], "not available"),
                 ("renamed", [{**release, "filename": "onnxruntime-another.whl"}], "not available"))
        for label, urls, message in cases:
            payload = json.dumps({"urls": urls}).encode()
            with self.subTest(case=label), \
                    patch.object(AssetInstaller, "_open_index",
                                 return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)):
                with self.assertRaisesRegex(ValueError, message):
                    AssetInstaller.wheel_url(wheel)
        payload = json.dumps({"urls": [release]}).encode()
        with patch.object(AssetInstaller, "_open_index",
                          return_value=Response(payload, "http://pypi.org/pypi/onnxruntime/1.23.2/json")):
            with self.assertRaisesRegex(ValueError, "index host"):
                AssetInstaller.wheel_url(wheel)
        with patch.object(AssetInstaller, "_open_index",
                          return_value=Response(b"x" * (1024 * 1024 + 1),
                                                DetectionAssets.RUNTIME_INDEX_URL)):
            with self.assertRaisesRegex(ValueError, "size limit"):
                AssetInstaller.wheel_url(wheel)

    def test_wheel_url_refuses_a_release_that_drifted_from_its_pin(self):
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-test.whl", "a" * 64, 100)
        pinned = "https://files.pythonhosted.org/packages/onnxruntime-test.whl"
        release = {"filename": wheel.filename, "digests": {"sha256": wheel.sha256},
                   "size": wheel.size, "url": pinned}
        drifts = (("digest", {**release, "digests": {"sha256": "b" * 64}}),
                  ("size", {**release, "size": wheel.size + 1}),
                  ("no url", {**release, "url": ""}),
                  ("plain http", {**release, "url": pinned.replace("https://", "http://")}),
                  ("foreign host", {**release, "url": "https://attacker.example/onnxruntime-test.whl"}))
        for label, drifted in drifts:
            payload = json.dumps({"urls": [drifted]}).encode()
            with self.subTest(drift=label), \
                    patch.object(AssetInstaller, "_open_index",
                                 return_value=Response(payload, DetectionAssets.RUNTIME_INDEX_URL)):
                with self.assertRaisesRegex(ValueError, "no longer matches"):
                    AssetInstaller.wheel_url(wheel)

    def test_download_requires_https_and_never_reuses_a_corrupt_file(self):
        data = b"pinned camera model"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              side_effect=AssertionError("insecure fetch")):
                with self.assertRaisesRegex(ValueError, "require HTTPS"):
                    AssetInstaller.download("http://cdn.example/model", path, len(data), digest,
                                            threading.Event(), lambda *_: None)
                Path(path).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "integrity"):
                    AssetInstaller.download("https://cdn.example/model", path, len(data), digest,
                                            threading.Event(), lambda *_: None)
            self.assertEqual(Path(path).read_bytes(), b"tampered")

    def test_download_stops_at_the_pinned_size_and_removes_the_partial(self):
        size = 64 * 1024
        data = b"x" * (2 * size)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              return_value=Response(data, "https://cdn.example/model")):
                with self.assertRaisesRegex(ValueError, "exceeds its pinned size"):
                    AssetInstaller.download("https://cdn.example/model", path, size,
                                            hashlib.sha256(data).hexdigest(), threading.Event(),
                                            lambda *_: None)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_download_transport_failure_is_not_reported_as_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model")
            with patch.object(AssetInstaller, "_open_download",
                              side_effect=ConnectionResetError("peer reset")):
                with self.assertRaises(ConnectionResetError):
                    AssetInstaller.download("https://cdn.example/model", path, 5, "a" * 64,
                                            threading.Event(), lambda *_: None)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_extract_refuses_an_archive_over_the_total_size_limit(self):
        # The guard sums declared member sizes, so directory members carry the
        # arithmetic: an archive past 140 MiB of real data would cost far more
        # I/O than the limit it proves.
        members = [zipfile.ZipInfo("onnxruntime/capi/part%d/" % index) for index in range(2)]
        for member in members:
            member.file_size = 75 * 1024 ** 2
            member.compress_size = 75 * 1024 ** 2

        class Wheel:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def infolist(self):
                return list(members)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(AssetInstaller.zipfile, "ZipFile", return_value=Wheel()):
                with self.assertRaisesRegex(ValueError, "size limit"):
                    AssetInstaller.extract_runtime(os.path.join(directory, "runtime.whl"),
                                                   os.path.join(directory, "runtime"),
                                                   threading.Event())
            self.assertFalse(os.path.exists(os.path.join(directory, "runtime")))
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_extract_refuses_symlink_backslash_foreign_and_oversized_members(self):
        # The declared-metadata guards, driven through a fake archive so
        # no member bytes are needed.
        cases = (
            ("symlink member", "onnxruntime/capi/link.py", {"external_attr": (0o120777 << 16)}),
            ("backslash name", "onnxruntime\\capi\\evil.py", {}),
            ("foreign top-level package", "sitecustomize/evil.py", {}),
            ("dot-dot component", "onnxruntime/../../evil.py", {}),
            ("member over its own cap", "onnxruntime/capi/huge.py", {"file_size": 101 * 1024 ** 2}),
        )
        for label, name, attrs in cases:
            with self.subTest(entry=label):
                member = zipfile.ZipInfo(name)
                # Written back after construction, not just passed in:
                # ZipInfo.__init__ rewrites os.sep to "/" — so on
                # Windows the backslash case would arrive at the guard
                # already normalised and prove nothing.
                member.filename = name
                for key, value in attrs.items():
                    setattr(member, key, value)

                class Wheel:
                    members = [member]

                    def __enter__(self):
                        return self

                    def __exit__(self, *_exc):
                        return False

                    def infolist(self):
                        return list(self.members)

                with tempfile.TemporaryDirectory() as directory:
                    with patch.object(AssetInstaller.zipfile, "ZipFile", return_value=Wheel()):
                        with self.assertRaisesRegex(ValueError, "unsafe entry"):
                            AssetInstaller.extract_runtime(
                                os.path.join(directory, "runtime.whl"),
                                os.path.join(directory, "runtime"), threading.Event())

    @unittest.skipIf(os.name == "nt",
                     "POSIX mode bits are synthetic on Windows: ownership is an ACL there, "
                     "and st_mode reports 0o777 for everything")
    def test_an_installed_runtime_is_owner_only(self):
        import stat
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as wheel:
                wheel.writestr(member, "pinned runtime")
            destination = os.path.join(directory, "runtime")
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            self.assertEqual(stat.S_IMODE(os.stat(destination).st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE(os.stat(os.path.join(destination, os.path.dirname(member))).st_mode),
                0o700)

    def test_extract_skips_directory_members_and_verifies_complete_archives(self):
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr("onnxruntime/", "")
                wheel.writestr("onnxruntime/capi/", "")
                wheel.writestr(member, "pinned runtime")
            destination = os.path.join(directory, "runtime")
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            self.assertTrue(os.path.isfile(os.path.join(destination, *member.split("/"))))
            self.assertTrue(AssetInstaller.verify_runtime(archive, destination))

    def test_extract_refuses_a_truncated_member_and_cleans_the_stage(self):
        member = zipfile.ZipInfo("onnxruntime/capi/onnxruntime_inference_collection.py")
        member.file_size = 1024
        member.compress_size = 1024

        class Wheel:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def infolist(self):
                return [member]

            def open(self, _member):
                return io.BytesIO(b"short")

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(AssetInstaller.zipfile, "ZipFile", return_value=Wheel()):
                with self.assertRaisesRegex(ValueError, "truncated"):
                    AssetInstaller.extract_runtime(os.path.join(directory, "runtime.whl"),
                                                   os.path.join(directory, "runtime"),
                                                   threading.Event())
            self.assertFalse(os.path.exists(os.path.join(directory, "runtime")))
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_extract_refuses_an_archive_without_the_inference_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr("onnxruntime/capi/other.py", "not the entry point")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                AssetInstaller.extract_runtime(archive, os.path.join(directory, "runtime"),
                                               threading.Event())
            self.assertFalse(os.path.exists(os.path.join(directory, "runtime")))
            self.assertEqual(list(Path(directory).glob(".runtime-*")), [])

    def test_verify_runtime_rejects_unsafe_member_names(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr("onnxruntime/../escape.py", "unsafe")
            self.assertFalse(AssetInstaller.verify_runtime(archive, directory))

    def test_verify_runtime_rejects_missing_and_altered_installations(self):
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr(member, "pinned runtime")
            self.assertFalse(AssetInstaller.verify_runtime(archive,
                                                           os.path.join(directory, "absent")))
            destination = os.path.join(directory, "runtime")
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            installed = Path(destination, *member.split("/"))
            original = installed.read_bytes()
            installed.write_bytes(b"X" * len(original))
            self.assertFalse(AssetInstaller.verify_runtime(archive, destination))
            installed.write_bytes(original)
            self.assertTrue(AssetInstaller.verify_runtime(archive, destination))

    def test_verify_runtime_rejects_a_linked_installation(self):
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "runtime.whl")
            with zipfile.ZipFile(archive, "w") as wheel:
                wheel.writestr(member, "pinned runtime")
            destination = os.path.join(directory, "runtime")
            AssetInstaller.extract_runtime(archive, destination, threading.Event())
            elsewhere = os.path.join(directory, "elsewhere")
            os.makedirs(elsewhere)
            link = os.path.join(directory, "linked")
            try:
                os.symlink(elsewhere, link)
                installed = Path(destination, *member.split("/"))
                copy = Path(elsewhere, "collection.py")
                copy.write_bytes(installed.read_bytes())
                os.unlink(installed)
                os.symlink(copy, installed)
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links unavailable")
            self.assertFalse(AssetInstaller.verify_runtime(archive, link))
            self.assertFalse(AssetInstaller.verify_runtime(archive, destination))

    def test_install_downloads_the_pinned_wheel_and_model_with_progress(self):
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as wheel_zip:
            wheel_zip.writestr(member, "pinned runtime")
        wheel_bytes = payload.getvalue()
        wheel = DetectionAssets.RuntimeWheel(
            "onnxruntime-1.23.2-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
            hashlib.sha256(wheel_bytes).hexdigest(), len(wheel_bytes))
        index_url = "https://files.pythonhosted.org/packages/" + wheel.filename
        index = json.dumps({"urls": [{"filename": wheel.filename,
                                      "digests": {"sha256": wheel.sha256}, "size": wheel.size,
                                      "url": index_url}]}).encode()
        downloads = []
        events = []

        def fetch(url, path, size, digest, _cancel, progress):
            downloads.append((url, path, size, digest))
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            Path(path).write_bytes(wheel_bytes if path.endswith(".whl") else b"model")
            progress(size, size)

        with tempfile.TemporaryDirectory() as directory:
            runtime, model = DetectionAssets.installed_paths(directory)
            with patch.object(AssetInstaller, "_open_index",
                              return_value=Response(index, DetectionAssets.RUNTIME_INDEX_URL)), \
                    patch.object(AssetInstaller, "download", side_effect=fetch):
                result = AssetInstaller.install(
                    directory, wheel, threading.Event(),
                    lambda phase, received, total: events.append((phase, received, total)))
            self.assertEqual(result, (runtime, model))
            self.assertTrue(os.path.isfile(os.path.join(runtime, *member.split("/"))))
            self.assertEqual(downloads, [
                (index_url, os.path.join(os.path.dirname(model), wheel.filename),
                 wheel.size, wheel.sha256),
                (DetectionAssets.MODEL_URL, model, DetectionAssets.MODEL_SIZE,
                 DetectionAssets.MODEL_SHA256)])
        self.assertEqual(events, [
            ("runtime", 0, wheel.size),
            ("runtime", wheel.size, wheel.size),
            ("model", 0, DetectionAssets.MODEL_SIZE),
            ("model", DetectionAssets.MODEL_SIZE, DetectionAssets.MODEL_SIZE)])

    def test_install_reuses_a_verified_runtime_archive_and_refuses_a_full_disk(self):
        member = "onnxruntime/capi/onnxruntime_inference_collection.py"
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as wheel_zip:
            wheel_zip.writestr(member, "pinned runtime")
        wheel_bytes = payload.getvalue()
        wheel = DetectionAssets.RuntimeWheel("onnxruntime-1.23.2-test.whl",
                                             hashlib.sha256(wheel_bytes).hexdigest(),
                                             len(wheel_bytes))
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = DetectionAssets.installed_paths(directory)
            archive = os.path.join(os.path.dirname(model), wheel.filename)
            os.makedirs(os.path.dirname(archive), mode=0o700)
            Path(archive).write_bytes(wheel_bytes)
            downloads = []
            events = []

            def note_only(url, path, size, digest, *_args):
                downloads.append((url, path, size, digest))

            with patch.object(AssetInstaller, "download", side_effect=note_only):
                AssetInstaller.install(directory, wheel, threading.Event(),
                                       lambda *event: events.append(event))
            self.assertEqual(downloads, [(DetectionAssets.MODEL_URL, model,
                                          DetectionAssets.MODEL_SIZE,
                                          DetectionAssets.MODEL_SHA256)])
            self.assertEqual(events[:2], [("runtime", 0, wheel.size),
                                          ("runtime", wheel.size, wheel.size)])
            self.assertEqual(events[2], ("model", 0, DetectionAssets.MODEL_SIZE))
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "detection-root")
            cramped = SimpleNamespace(total=0, used=0, free=DetectionAssets.MODEL_SIZE)
            with patch.object(AssetInstaller.shutil, "disk_usage", return_value=cramped), \
                    patch.object(AssetInstaller, "download",
                                 side_effect=AssertionError("downloaded")):
                with self.assertRaisesRegex(ValueError, "free disk space"):
                    AssetInstaller.install(root, wheel, threading.Event(), lambda *_: None)
            self.assertTrue(os.path.isdir(root))

    def test_cancelled_network_read_reports_cancellation_not_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
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

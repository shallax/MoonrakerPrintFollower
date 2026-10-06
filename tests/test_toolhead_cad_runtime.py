"""Pinned helper installation, hostile archive rejection and cache integrity."""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request
import zipfile

from mpf.toolhead import CadRuntime


def wheel_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in entries:
            if isinstance(name, str):
                member = zipfile.ZipInfo(name)
                # Keep the hostile bytes even when the writer runs on Windows.
                member.filename = name
            else:
                member = name
            archive.writestr(member, body)
    return output.getvalue()


def tar_member(name, body=b"", kind=tarfile.REGTYPE, link="", mode=0o644):
    member = tarfile.TarInfo(name)
    member.type, member.linkname, member.mode = kind, link, mode
    member.size = len(body) if kind == tarfile.REGTYPE else 0
    return member, body


def helper_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for member, body in entries:
            archive.addfile(member, io.BytesIO(body))
    return output.getvalue()


class ToolheadCadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payloads = {
            "reader.whl": wheel_bytes([
                ("OCP/__init__.py", b"pinned OCP module"),
                ("cadquery_ocp_novtk.libs/libTK.so", b"pinned native library"),
            ]),
            "proxy.whl": wheel_bytes([
                ("cadquery_ocp_proxy/__init__.py", b"pinned proxy module"),
            ]),
            "helper.tar.gz": helper_bytes([
                tar_member("python/bin/python3.12", b"pinned interpreter", mode=0o755),
                tar_member("python/lib/python3.12/os.py", b"pinned standard library"),
                tar_member("python/lib/python3.12/LICENSE.txt", b"original licence"),
                tar_member("python/bin/python3", kind=tarfile.SYMTYPE, link="python3.12"),
                tar_member("python/share/terminfo/1/1178", kind=tarfile.SYMTYPE,
                           link="../a/adm1178"),
                tar_member("python/lib/python3.12/__pycache__/os.pyc", b"unused bytecode"),
            ]),
        }
        self.assets = [(name, {"url": "https://upstream.example/" + name,
                               "sha256": hashlib.sha256(body).hexdigest(),
                               "size": len(body)})
                       for name, body in self.payloads.items()]

    def install(self, cancelled=None, response=None):
        opener = SimpleNamespace(open=lambda url, **_options: io.BytesIO(
            self.payloads[url.rsplit("/", 1)[1]]))
        if response is not None:
            opener.open = response
        with patch.object(CadRuntime, "runtime_assets", return_value=self.assets), \
                patch.object(CadRuntime.urllib.request, "build_opener", return_value=opener):
            return CadRuntime.install_runtime(str(self.root / "cache"),
                                              cancelled or threading.Event(), lambda _text: None)

    def test_five_platforms_always_select_the_independent_cp312_helper(self):
        platforms = (("Darwin", "arm64", "aarch64-apple-darwin"),
                     ("Darwin", "x86_64", "x86_64-apple-darwin"),
                     ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
                     ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
                     ("Windows", "AMD64", "x86_64-pc-windows-msvc"))
        for host_abi in ((3, 10), (3, 14)):
            for system, machine, triple in platforms:
                with self.subTest(host=host_abi, system=system, machine=machine), \
                        patch.object(sys, "version_info", host_abi), \
                        patch.object(CadRuntime.platform, "system", return_value=system), \
                        patch.object(CadRuntime.platform, "machine", return_value=machine), \
                        patch.object(CadRuntime.platform, "mac_ver", return_value=("13.0", "", "")), \
                        patch.object(CadRuntime.platform, "libc_ver", return_value=("glibc", "2.31")):
                    assets = CadRuntime.runtime_assets()
                self.assertEqual(len(assets), 3)
                self.assertIn("-cp312-cp312-", assets[0][0])
                self.assertTrue(assets[1][0].endswith("-py3-none-any.whl"))
                self.assertEqual(assets[2][0], "cpython-3.12.15+20261003-" + triple
                                 + "-install_only_stripped.tar.gz")
                for _name, pin in assets:
                    self.assertTrue(pin["url"].startswith("https://"))
                    self.assertGreater(pin["size"], 0)
                    self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")

    def test_unsupported_hosts_do_not_offer_unmatched_native_binaries(self):
        cases = (("Windows", "arm64", ("glibc", "2.31"), "13.0"),
                 ("Linux", "x86_64", ("musl", "1.2"), "13.0"),
                 ("Linux", "x86_64", ("glibc", "2.30"), "13.0"),
                 ("Darwin", "arm64", ("glibc", "2.31"), "10.15"))
        for system, machine, libc, mac in cases:
            with self.subTest(system=system, machine=machine, libc=libc, mac=mac), \
                    patch.object(CadRuntime.platform, "system", return_value=system), \
                    patch.object(CadRuntime.platform, "machine", return_value=machine), \
                    patch.object(CadRuntime.platform, "mac_ver", return_value=(mac, "", "")), \
                    patch.object(CadRuntime.platform, "libc_ver", return_value=libc):
                with self.assertRaises(ValueError):
                    CadRuntime.runtime_assets()

    def test_install_retains_pinned_archives_and_verifies_all_runtime_files(self):
        target = Path(self.install())
        self.assertEqual((target / "python/lib/python3.12/LICENSE.txt").read_bytes(),
                         b"original licence")
        self.assertFalse((target / "python/bin/python3").exists())
        self.assertFalse((target / "python/share/terminfo/1/1178").exists())
        self.assertFalse((target / "python/lib/python3.12/__pycache__").exists())
        for name, payload in self.payloads.items():
            self.assertEqual((target / ".archives" / name).read_bytes(), payload)
        self.assertEqual(CadRuntime.verify_runtime(str(target), self.assets), str(target))

    def test_valid_cache_needs_no_network_request(self):
        target = self.install()

        def refuse_network(*_args, **_kwargs):
            self.fail("A verified cached runtime must not be downloaded again")

        self.assertEqual(self.install(response=refuse_network), target)

    def test_modified_cached_library_is_rejected_despite_forged_marker(self):
        target = Path(self.install())
        (target / "cadquery_ocp_novtk.libs/libTK.so").write_bytes(b"untrusted native library")
        (target / "verified.json").write_text(json.dumps({"assets": dict(self.assets),
                                                       "verified": True}))
        with self.assertRaisesRegex(ValueError, "integrity"):
            CadRuntime.verify_runtime(str(target), self.assets)
        self.assertEqual(Path(self.install()), target)
        self.assertEqual((target / "cadquery_ocp_novtk.libs/libTK.so").read_bytes(),
                         b"pinned native library")

    def test_modified_retained_archive_cannot_define_new_trusted_hashes(self):
        target = Path(self.install())
        archive = target / ".archives/reader.whl"
        body = bytearray(archive.read_bytes())
        body[-1] ^= 1
        archive.write_bytes(body)  # unchanged length, wrong committed digest
        with self.assertRaisesRegex(ValueError, "archive integrity"):
            CadRuntime.verify_runtime(str(target), self.assets)

    def test_truncated_retained_archive_is_rejected_before_member_verification(self):
        target = Path(self.install())
        archive = target / ".archives/reader.whl"
        archive.write_bytes(archive.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, "archive is damaged"):
            CadRuntime.verify_runtime(str(target), self.assets)

    def test_additional_importable_file_invalidates_the_cache(self):
        target = Path(self.install())
        (target / "OCP/unpinned.py").write_bytes(b"untrusted code")
        with self.assertRaisesRegex(ValueError, "integrity"):
            CadRuntime.verify_runtime(str(target), self.assets)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_cached_library_symlink_is_rejected_even_for_matching_bytes(self):
        target = Path(self.install())
        original = target / "cadquery_ocp_novtk.libs/libTK.so"
        external = self.root / "external.so"
        external.write_bytes(original.read_bytes())
        original.unlink()
        try:
            original.symlink_to(external)
        except OSError as error:
            self.skipTest("Host does not permit symlinks: " + str(error))
        with self.assertRaisesRegex(ValueError, "symlink"):
            CadRuntime.verify_runtime(str(target), self.assets)

    def test_wrong_download_hash_short_body_and_oversized_body_leave_no_cache(self):
        original = self.payloads["reader.whl"]
        for body in (original[:-1], original + b"extra", bytes([original[0] ^ 1]) + original[1:]):
            with self.subTest(size=len(body)):
                self.payloads["reader.whl"] = body
                with self.assertRaisesRegex(ValueError, "integrity|pinned size"):
                    self.install()
                self.assertEqual(list((self.root / "cache").iterdir()), [])
        self.payloads["reader.whl"] = original

    def test_cancellation_during_download_discards_the_partial_stage(self):
        cancelled = threading.Event()

        class CancelResponse(io.BytesIO):
            def read(response, size=-1):
                data = super().read(size)
                cancelled.set()
                return data

        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.install(cancelled, lambda _url, **_options: CancelResponse(b"partial"))
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_wheel_rejects_unsafe_paths_and_member_types_before_any_write(self):
        link = zipfile.ZipInfo("OCP/link.so")
        link.create_system, link.external_attr = 3, (stat.S_IFLNK | 0o777) << 16
        device = zipfile.ZipInfo("OCP/device")
        device.create_system, device.external_attr = 3, (stat.S_IFIFO | 0o600) << 16
        unsafe = ("OCP/../../escaped", "OCP/./alias.py", "/OCP/absolute",
                  "OCP/back\\slash", "OCP/library:stream", "unexpected/module.py", link, device)
        for index, name in enumerate(unsafe):
            for separator in ("/", "\\"):
                with self.subTest(name=str(name), separator=separator):
                    archive = self.root / "invalid.whl"
                    archive.write_bytes(wheel_bytes([("OCP/valid.py", b"safe"), (name, b"unsafe")]))
                    destination = self.root / ("extracted-%d-%d" % (index, ord(separator)))
                    destination.mkdir()
                    # Exercise the ZIP reader's Windows normalisation on every host.
                    with patch.object(zipfile.os, "sep", separator), self.assertRaises(ValueError):
                        CadRuntime.extract_wheel(archive, destination)
                    self.assertEqual(list(destination.iterdir()), [])
                    self.assertFalse((self.root / "escaped").exists())

    def test_helper_rejects_escape_hardlink_and_special_files_before_any_write(self):
        unsafe = (tar_member("python/../../escaped", b"bad"),
                  tar_member("python/binary:stream", b"bad"),
                  tar_member("python/alias", kind=tarfile.LNKTYPE, link="python/bin/python3.12"),
                  tar_member("python/fifo", kind=tarfile.FIFOTYPE))
        for member in unsafe:
            with self.subTest(name=member[0].name):
                archive = self.root / "invalid.tar.gz"
                archive.write_bytes(helper_bytes([tar_member("python/bin/python3.12", b"safe"), member]))
                destination = self.root / "helper-extracted"
                destination.mkdir(exist_ok=True)
                with self.assertRaises(ValueError):
                    CadRuntime.extract_helper(archive, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_helper_declared_member_size_is_bounded_before_extraction(self):
        member = tarfile.TarInfo("python/lib/huge")
        member.size = 400 * 1024 * 1024 + 1
        archive = SimpleNamespace(getmembers=lambda: [member])
        with self.assertRaisesRegex(ValueError, "Invalid STEP helper"):
            list(CadRuntime._tar_members(archive))

    def test_redirect_cannot_downgrade_https(self):
        handler = CadRuntime.HttpsOnly()
        request = Request("https://upstream.example/model.whl")
        with self.assertRaisesRegex(ValueError, "insecure redirect"):
            handler.redirect_request(request, None, 302, "Found", {},
                                     "http://cdn.example/model.whl")

    def test_https_redirect_preserves_a_trusted_scheme_on_another_host(self):
        handler = CadRuntime.HttpsOnly()
        request = Request("https://upstream.example/model.whl")
        redirected = handler.redirect_request(request, None, 302, "Found", {},
                                              "https://cdn.example/model.whl")
        self.assertEqual(redirected.full_url, "https://cdn.example/model.whl")


if __name__ == "__main__":
    unittest.main()

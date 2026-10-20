# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [5a32b35] Prepare 5.4.0 runtime mirror and follower controls
# 2026-10-12 [abf18f3] Mirror optional runtime assets on plugin releases
# 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

"""Pinned optional CAD reader, downloaded from this plugin's release."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import ssl
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..resources.RuntimeMirror import asset_url
from .WindowsRuntimeWithoutRedist import strip_cad_wheel, strip_python_helper

VERSION = "7.9.3.1.1"
WINDOWS_RUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "vcomp140.dll")


def missing_windows_runtime(required=WINDOWS_RUNTIME_DLLS):
    """Check the system copy used by both Cura and the isolated STEP helper."""
    if platform.system() != "Windows": return ()
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    return tuple(name for name in required if not (system32 / name).is_file())


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("CAD reader download refused an insecure redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _trusted_context():
    # Cura ships certifi: frozen macOS builds cannot rely on OpenSSL finding
    # an OS CA file. Retain system/administrator trust and add Cura's bundle.
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context


def runtime_assets():
    abi = "cp312"  # independent helper ABI; never Cura's Python ABI
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    system = platform.system()
    if system == "Darwin":
        suffix = f"macosx_11_0_{machine}"
        triple = ("aarch64" if machine == "arm64" else machine) + "-apple-darwin"
    elif system == "Windows" and machine == "x86_64":
        suffix, triple = "win_amd64", "x86_64-pc-windows-msvc"
    elif system == "Linux":
        arch = "aarch64" if machine == "arm64" else machine
        suffix, triple = "manylinux_2_31_" + arch, arch + "-unknown-linux-gnu"
    else: raise ValueError("No verified CAD reader for this operating system")
    if system == "Darwin" and tuple(int(v) for v in platform.mac_ver()[0].split(".")[:1]) < (11,):
        raise ValueError("STEP import requires macOS 11 or newer")
    if system == "Linux":
        libc, version = platform.libc_ver()
        if libc != "glibc" or tuple(int(v) for v in version.split(".")[:2]) < (2, 31):
            raise ValueError("STEP import requires Linux glibc 2.31 or newer")
    pins = json.loads(Path(__file__).with_name("cad-runtime.json").read_text())
    name = f"cadquery_ocp_novtk-{VERSION}-{abi}-{abi}-{suffix}.whl"
    proxy = f"cadquery_ocp_proxy-{VERSION}-py3-none-any.whl"
    if name not in pins: raise ValueError(f"No verified CAD reader for {abi} on {system} {machine}")
    helper_pins = json.loads(Path(__file__).with_name("helper-runtime.json").read_text())
    helper_suffix = ".tar" if system == "Windows" else ".tar.gz"
    helper = f"cpython-3.12.15+20261003-{triple}-install_only_stripped{helper_suffix}"
    if helper not in helper_pins: raise ValueError("No verified STEP helper for this platform")
    return [(name, pins[name]), (proxy, pins[proxy]), (helper, helper_pins[helper])]


def runtime_directory(root, assets=None):
    assets = assets or runtime_assets()
    # Architecture identity prevents ARM/Rosetta caches from colliding.
    # The old cache key included upstream URLs. Keep that key for verified
    # installations when the bytes and hashes stay unchanged after mirroring.
    identity_assets = {
        name: {"url": pin.get("source_url", pin["url"]), "size": pin["size"],
               "sha256": pin["sha256"]}
        for name, pin in assets
    }
    identity = hashlib.sha256(json.dumps(identity_assets, sort_keys=True).encode()).hexdigest()[:16]
    return os.path.join(root, VERSION + "-" + identity)


def extract_wheel(path, destination):
    with zipfile.ZipFile(path) as wheel:
        total, seen = 0, set()
        if len(wheel.infolist()) > 10000: raise ValueError("CAD runtime archive has too many entries")
        for member in wheel.infolist():
            name = member.filename
            # ZipInfo normalises backslashes on Windows and truncates NULs.
            # Reject altered names before trusting the extraction path.
            if name != member.orig_filename:
                raise ValueError("Invalid CAD runtime archive")
            parts = name.split("/")
            total += member.file_size
            mode = member.external_attr >> 16
            if (name in seen or ":" in name or "\\" in name or name.startswith("/") or any(p in ("..", ".") for p in parts)
                    or stat.S_ISLNK(member.external_attr >> 16) or total > 800 * 1024 * 1024
                    or member.file_size > 400 * 1024 * 1024
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise ValueError("Invalid CAD runtime archive")
            if not (parts[0] in ("OCP", "cadquery_ocp_proxy", "cadquery_ocp_novtk.libs")
                    or parts[0].endswith(".dist-info")):
                raise ValueError("Unexpected CAD runtime archive member")
            seen.add(name)
        wheel.extractall(destination)


def _tar_members(archive):
    total, seen = 0, set()
    for member in archive.getmembers():
        name, parts = member.name, member.name.split("/")
        total += member.size
        if (name in seen or len(seen) >= 10000 or ":" in name or "\\" in name
                or name.startswith("/") or any(p in (".", "..") for p in parts)
                or parts[0] != "python" or total > 800*1024*1024
                or member.size > 400*1024*1024):
            raise ValueError("Invalid STEP helper archive")
        seen.add(name)
        if member.issym():
            # Aliases such as python -> python3.12 are unnecessary for the
            # fixed executable. Ignore internal aliases, never materialise them.
            continue
        if member.isdir() or "__pycache__" in parts: continue
        if not member.isfile(): raise ValueError("STEP helper archive contains a special file")
        yield member


def extract_helper(path, destination):
    with tarfile.open(path, "r:*") as archive:
        members = list(_tar_members(archive))  # validate before writing
        for member in members:
            target = Path(destination, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 128*1024)
            target.chmod(0o700 if member.mode & 0o111 else 0o600)


def verify_runtime(target, assets):
    """Derive trusted member hashes from retained, pinned whole archives."""
    if os.path.islink(target): raise ValueError("CAD runtime directory must not be a symlink")
    files = {}
    for name, pin in assets:
        path = Path(target, ".archives", name)
        if path.is_symlink() or path.stat().st_size != pin["size"]:
            raise ValueError("CAD runtime archive is damaged")
        with path.open("rb") as handle:
            if _digest_file(handle) != pin["sha256"]: raise ValueError("CAD runtime archive integrity check failed")
        if name.endswith(".whl"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir(): continue
                    with archive.open(member) as handle: files[member.filename] = _digest_file(handle)
        else:
            with tarfile.open(path, "r:*") as archive:
                for member in _tar_members(archive):
                    with archive.extractfile(member) as handle: files[member.name] = _digest_file(handle)
    actual, total = {}, 0
    pending = [(str(target), "")]
    while pending:
        directory, prefix = pending.pop()
        # DirEntry retains Windows enumeration metadata. Path.rglob followed
        # by is_symlink/is_file/stat made three filesystem queries per entry.
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink(): raise ValueError("CAD runtime contains a symlink")
                relative = prefix + entry.name
                if entry.is_dir(follow_symlinks=False):
                    pending.append((entry.path, relative + "/"))
                    continue
                if not entry.is_file(follow_symlinks=False): continue
                if relative == "verified.json" or relative.startswith(".archives/"): continue
                total += entry.stat(follow_symlinks=False).st_size
                if total > 800*1024*1024 or len(actual) >= 10000: raise ValueError("CAD runtime exceeded its size limit")
                with open(entry.path, "rb") as handle: actual[relative] = _digest_file(handle)
    if actual != files: raise ValueError("CAD runtime integrity check failed")
    return target


def _digest_file(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(128*1024), b""): digest.update(chunk)
    return digest.hexdigest()


def _fetch_pinned(opener, url, path, size_pin, digest_pin, cancelled, progress, label):
    if not url.startswith("https://"):
        raise ValueError("CAD runtime downloads require HTTPS")
    digest, size, last_progress = hashlib.sha256(), 0, ""
    with opener.open(url, timeout=15) as response, open(path, "wb") as output:
        while True:
            if cancelled.is_set(): raise ValueError("Model import cancelled")
            chunk = response.read(128 * 1024)
            if not chunk: break
            size += len(chunk)
            if size > size_pin: raise ValueError("CAD runtime download exceeded its pinned size")
            digest.update(chunk)
            output.write(chunk)
            message = f"{label}: {size // 1048576} / {size_pin // 1048576} MiB"
            if message != last_progress:
                progress(message)
                last_progress = message
    if size != size_pin or digest.hexdigest() != digest_pin:
        raise ValueError("CAD runtime integrity check failed")


def _download_asset(opener, name, pin, path, cancelled, progress):
    kind = "STEP helper" if name.endswith((".tar.gz", ".tar")) else "CAD reader"
    try:
        _fetch_pinned(opener, asset_url(name), path, pin["size"], pin["sha256"],
                      cancelled, progress, "Downloading " + kind + " from GitHub")
        return
    except (OSError, ValueError) as primary:
        if cancelled.is_set() or "source_url" not in pin:
            raise
        Path(path).unlink(missing_ok=True)
        source = Path(str(path) + ".source")
        try:
            progress("GitHub download failed; trying the original " + kind + " source")
            if pin.get("derivation") == "strip-windows-redist-v1":
                _fetch_pinned(opener, pin["source_url"], source,
                              pin["source_size"], pin["source_sha256"],
                              cancelled, progress, "Downloading original " + kind)
                if name.endswith(".whl"):
                    strip_cad_wheel(source, Path(path))
                else:
                    strip_python_helper(source, Path(path))
                with open(path, "rb") as output:
                    if os.fstat(output.fileno()).st_size != pin["size"] or _digest_file(output) != pin["sha256"]:
                        raise ValueError("Derived CAD runtime integrity check failed")
            else:
                _fetch_pinned(opener, pin["source_url"], path,
                              pin["size"], pin["sha256"], cancelled, progress,
                              "Downloading original " + kind)
        except Exception as secondary:
            Path(path).unlink(missing_ok=True)
            if cancelled.is_set():
                raise ValueError("Model import cancelled") from secondary
            raise ValueError(f"CAD runtime failed from GitHub and its original source: {primary}; {secondary}") from secondary
        finally:
            source.unlink(missing_ok=True)


def install_runtime(root, cancelled, progress, *, assets=None, archive_directory=None):
    assets = runtime_assets() if assets is None else assets
    required = WINDOWS_RUNTIME_DLLS if any(name.endswith(".whl") for name, _pin in assets) else WINDOWS_RUNTIME_DLLS[:2]
    if missing_windows_runtime(required):
        raise ValueError("Python helper needs the Microsoft Visual C++ x64 runtime. Install it from Microsoft, then retry.")
    os.makedirs(root, mode=0o700, exist_ok=True)
    target = runtime_directory(root, assets)
    if os.path.lexists(target):
        try: return verify_runtime(target, assets)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError):
            # Retire an invalid cache before installing a freshly verified one.
            if os.path.islink(target) or not os.path.isdir(target): os.unlink(target)
            else: shutil.rmtree(target)
    stage = tempfile.mkdtemp(dir=root)
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_trusted_context()), HttpsOnly())
        for name, pin in assets:
            path = os.path.join(stage, name)
            retained = Path(archive_directory, name) if archive_directory is not None else None
            reused = False
            if retained is not None and retained.is_file() and not retained.is_symlink():
                with retained.open("rb") as handle:
                    if os.fstat(handle.fileno()).st_size == pin["size"] and _digest_file(handle) == pin["sha256"]:
                        handle.seek(0)
                        with open(path, "wb") as output:
                            shutil.copyfileobj(handle, output, 128*1024)
                        reused = True
            if not reused:
                _download_asset(opener, name, pin, path, cancelled, progress)
            if name.endswith(".whl"): extract_wheel(path, stage)
            else: extract_helper(path, stage)
            archive_dir = os.path.join(stage, ".archives")
            os.makedirs(archive_dir, mode=0o700, exist_ok=True)
            os.replace(path, os.path.join(archive_dir, name))
        Path(stage, "verified.json").write_text(json.dumps({"assets": dict(assets)}))
        if cancelled.is_set(): raise ValueError("Model import cancelled")
        if os.path.exists(target): shutil.rmtree(stage)
        else: os.replace(stage, target)
        return verify_runtime(target, assets)
    finally:
        if os.path.isdir(stage): shutil.rmtree(stage)

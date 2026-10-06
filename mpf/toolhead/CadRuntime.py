"""Pinned optional CAD reader, downloaded from upstream without pip."""
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

VERSION = "7.9.3.1.1"


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
    helper = f"cpython-3.12.15+20261003-{triple}-install_only_stripped.tar.gz"
    if helper not in helper_pins: raise ValueError("No verified STEP helper for this platform")
    return [(name, pins[name]), (proxy, pins[proxy]), (helper, helper_pins[helper])]


def runtime_directory(root, assets=None):
    assets = assets or runtime_assets()
    # Architecture identity prevents ARM/Rosetta caches from colliding.
    identity = hashlib.sha256(json.dumps(dict(assets), sort_keys=True).encode()).hexdigest()[:16]
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
    with tarfile.open(path, "r:gz") as archive:
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
            with tarfile.open(path, "r:gz") as archive:
                for member in _tar_members(archive):
                    with archive.extractfile(member) as handle: files[member.name] = _digest_file(handle)
    actual, total = {}, 0
    for path in Path(target).rglob("*"):
        if path.is_symlink(): raise ValueError("CAD runtime contains a symlink")
        if not path.is_file(): continue
        relative = path.relative_to(target).as_posix()
        if relative == "verified.json" or relative.startswith(".archives/"): continue
        total += path.stat().st_size
        if total > 800*1024*1024 or len(actual) >= 10000: raise ValueError("CAD runtime exceeded its size limit")
        with path.open("rb") as handle: actual[relative] = _digest_file(handle)
    if actual != files: raise ValueError("CAD runtime integrity check failed")
    return target


def _digest_file(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(128*1024), b""): digest.update(chunk)
    return digest.hexdigest()


def install_runtime(root, cancelled, progress):
    assets = runtime_assets()
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
            digest, size = hashlib.sha256(), 0
            last_progress = ""
            with opener.open(pin["url"], timeout=15) as response, open(path, "wb") as output:
                while True:
                    if cancelled.is_set(): raise ValueError("Model import cancelled")
                    chunk = response.read(128 * 1024)
                    if not chunk: break
                    size += len(chunk)
                    if size > pin["size"]: raise ValueError("CAD runtime download exceeded its pinned size")
                    digest.update(chunk)
                    output.write(chunk)
                    message = "Downloading STEP helper" if name.endswith(".tar.gz") else "Downloading CAD reader"
                    message += f": {size // 1048576} / {pin['size'] // 1048576} MiB"
                    if message != last_progress:
                        progress(message)
                        last_progress = message
            if size != pin["size"] or digest.hexdigest() != pin["sha256"]:
                raise ValueError("CAD runtime integrity check failed")
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

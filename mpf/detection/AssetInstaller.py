"""Consent-only, integrity-checked installation of the local CPU assets."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, urlopen
import zipfile

from .DetectionAssets import (
    MODEL_SHA256, MODEL_SIZE, MODEL_URL, RUNTIME_INDEX_URL,
    RuntimeWheel, installed_paths,
)


class DownloadCancelled(Exception):
    pass


def _trusted_context():
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class _HTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Local detection downloads cannot redirect away from HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_download(url):
    opener = build_opener(HTTPSHandler(context=_trusted_context()), _HTTPSRedirectHandler())
    return opener.open(Request(url), timeout=3)


def _check_cancelled(cancel) -> None:
    if cancel.is_set():
        raise DownloadCancelled("Local detection setup cancelled")


def verify_file(path: str, size: int, digest: str) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) != size:
        return False
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest() == digest


def wheel_url(wheel: RuntimeWheel, cancel=None) -> str:
    if cancel is not None:
        _check_cancelled(cancel)
    try:
        with urlopen(Request(RUNTIME_INDEX_URL, headers={"Accept": "application/json"}),
                     timeout=3, context=_trusted_context()) as response:
            if urlsplit(response.geturl()).scheme != "https" \
                    or urlsplit(response.geturl()).hostname != "pypi.org":
                raise ValueError("Unexpected runtime index host")
            raw = response.read(1024 * 1024 + 1)
    except OSError as exc:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("Local detection setup cancelled") from exc
        raise
    if cancel is not None:
        _check_cancelled(cancel)
    if len(raw) > 1024 * 1024:
        raise ValueError("Runtime index exceeds its size limit")
    releases = json.loads(raw)["urls"]
    matches = [item for item in releases if item.get("filename") == wheel.filename]
    if len(matches) != 1:
        raise ValueError("The pinned inference runtime is not available")
    item = matches[0]
    url = str(item.get("url", ""))
    if (item.get("digests", {}).get("sha256") != wheel.sha256
            or item.get("size") != wheel.size
            or urlsplit(url).scheme != "https"
            or urlsplit(url).hostname != "files.pythonhosted.org"):
        raise ValueError("The inference runtime no longer matches its pinned release")
    return url


def download(url: str, path: str, size: int, digest: str, cancel, progress) -> None:
    _check_cancelled(cancel)
    if urlsplit(url).scheme != "https":
        raise ValueError("Local detection downloads require HTTPS")
    if verify_file(path, size, digest):
        progress(size, size)
        return
    if os.path.exists(path):
        raise ValueError("An existing local detection asset failed its integrity check")
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".detection-", suffix=".part",
                                         dir=directory, delete=False) as target:
            temporary = target.name
            received = 0
            sha = hashlib.sha256()
            with _open_download(url) as response:
                if urlsplit(response.geturl()).scheme != "https":
                    raise ValueError("Local detection downloads require HTTPS after redirects")
                while True:
                    _check_cancelled(cancel)
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > size:
                        raise ValueError("Local detection asset exceeds its pinned size")
                    sha.update(block)
                    target.write(block)
                    progress(received, size)
            _check_cancelled(cancel)
            if received != size or sha.hexdigest() != digest:
                raise ValueError("Local detection asset failed its integrity check")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        if cancel.is_set():
            raise DownloadCancelled("Local detection setup cancelled") from exc
        raise
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def extract_runtime(archive: str, destination: str, cancel) -> None:
    _check_cancelled(cancel)
    if os.path.isdir(destination):
        if verify_runtime(archive, destination):
            return
        raise ValueError("The installed inference runtime failed its integrity check")
    parent = os.path.dirname(destination)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".runtime-", dir=parent)
    try:
        with zipfile.ZipFile(archive) as wheel:
            names = set()
            total = 0
            for member in wheel.infolist():
                _check_cancelled(cancel)
                parts = Path(member.filename).parts
                if (member.filename.startswith("/")
                        or "\\" in member.filename
                        or not parts or parts[0] not in ("onnxruntime", "onnxruntime-1.23.2.dist-info")
                        or any(part in ("", ".", "..") for part in parts)
                        or member.filename in names or member.file_size > 100 * 1024 ** 2
                        or (member.file_size > 1024 * 1024 and
                            member.file_size > max(member.compress_size, 1) * 100)
                        or (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Inference runtime archive has an unsafe entry")
                names.add(member.filename)
                total += member.file_size
                if total > 140 * 1024 ** 2:
                    raise ValueError("Inference runtime archive exceeds its size limit")
                if member.is_dir():
                    continue
                target = os.path.join(stage, *parts)
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                with wheel.open(member) as source, open(target, "wb") as output:
                    remaining = member.file_size
                    while remaining:
                        _check_cancelled(cancel)
                        block = source.read(min(64 * 1024, remaining))
                        if not block:
                            raise ValueError("Inference runtime archive is truncated")
                        output.write(block)
                        remaining -= len(block)
        if not os.path.isfile(os.path.join(stage, "onnxruntime", "capi",
                                           "onnxruntime_inference_collection.py")):
            raise ValueError("Inference runtime archive is incomplete")
        _check_cancelled(cancel)
        os.replace(stage, destination)
        stage = None
    finally:
        if stage is not None:
            shutil.rmtree(stage)


def verify_runtime(archive: str, destination: str) -> bool:
    if not os.path.isdir(destination) or os.path.islink(destination):
        return False
    with zipfile.ZipFile(archive) as wheel:
        for member in wheel.infolist():
            parts = Path(member.filename).parts
            if member.filename.startswith("/") or "\\" in member.filename \
                    or not parts or parts[0] not in ("onnxruntime", "onnxruntime-1.23.2.dist-info") \
                    or any(part in ("", ".", "..") for part in parts):
                return False
            if member.is_dir():
                continue
            target = destination
            for part in parts:
                target = os.path.join(target, part)
                if os.path.islink(target):
                    return False
            if not os.path.isfile(target) or os.path.getsize(target) != member.file_size:
                return False
            with wheel.open(member) as source, open(target, "rb") as installed:
                while True:
                    block = source.read(256 * 1024)
                    if not block:
                        break
                    if block != installed.read(len(block)):
                        return False
    return True


def install(root: str, wheel: RuntimeWheel, cancel, progress) -> tuple[str, str]:
    runtime, model = installed_paths(root)
    os.makedirs(root, mode=0o700, exist_ok=True)
    if shutil.disk_usage(root).free < MODEL_SIZE + wheel.size + 180 * 1024 ** 2:
        raise ValueError("Local detection needs at least 400 MiB of free disk space")
    archive = os.path.join(os.path.dirname(model), wheel.filename)
    progress("runtime", 0, wheel.size)
    if verify_file(archive, wheel.size, wheel.sha256):
        progress("runtime", wheel.size, wheel.size)
    else:
        download(wheel_url(wheel, cancel), archive, wheel.size, wheel.sha256, cancel,
                 lambda received, total: progress("runtime", received, total))
    extract_runtime(archive, runtime, cancel)
    progress("model", 0, MODEL_SIZE)
    download(MODEL_URL, model, MODEL_SIZE, MODEL_SHA256, cancel,
             lambda received, total: progress("model", received, total))
    return runtime, model

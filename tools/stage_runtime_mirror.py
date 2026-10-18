# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [5a32b35] Prepare 5.4.0 runtime mirror and follower controls
# 2026-10-12 [abf18f3] Mirror optional runtime assets on plugin releases
# 2026-10-12 [adb8046] Add reproducible staging for selectable runtime assets

"""Stage the exact pinned optional runtimes outside the source tree.

This tool does not publish a release. Publishing also needs the matching
source and licence companion described in docs/RUNTIME_MIRROR.md.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpf.detection.DetectionAssets import (  # noqa: E402
    MODEL_SHA256, MODEL_SIZE, MODEL_SOURCE_URL, RUNTIME_VERSION, _WHEELS,
)


class HTTPSOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Runtime source redirected away from HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    if urlsplit(url).scheme != "https":
        raise ValueError("Runtime source must use HTTPS")
    return build_opener(HTTPSOnly()).open(
        Request(url, headers={"User-Agent": "MoonrakerPrintFollower-runtime-staging"}),
        timeout=60,
    )


def inventory(source_release=None, offline=False):
    assets = [dict(name="model-weights-5a6b1be1fa.onnx", url=MODEL_SOURCE_URL,
                   size=MODEL_SIZE, sha256=MODEL_SHA256)]
    for filename in ("cad-runtime.json", "helper-runtime.json"):
        pins = json.loads((ROOT / "mpf" / "toolhead" / filename).read_text())
        for name, pin in pins.items():
            # The independent STEP helper always runs Python 3.12. The CAD
            # manifest also contains wheels for other ABIs that no production
            # install_runtime() path can select.
            if filename == "cad-runtime.json" and "proxy" not in name \
                    and "-cp312-cp312-" not in name:
                continue
            assets.append(dict(name=name, url=pin.get("source_url", pin["url"]),
                               size=pin["size"], sha256=pin["sha256"],
                               source_size=pin.get("source_size"),
                               source_sha256=pin.get("source_sha256"),
                               derivation=pin.get("derivation")))
    indexed = {}
    if not source_release and not offline:
        with _open(f"https://pypi.org/pypi/onnxruntime/{RUNTIME_VERSION}/json") as response:
            if urlsplit(response.geturl()).hostname != "pypi.org":
                raise ValueError("Unexpected ONNX Runtime index host")
            index = json.load(response)
        indexed = {item["filename"]: item for item in index["urls"]}
    for (abi, platform), (sha256, size) in _WHEELS.items():
        name = f"onnxruntime-{RUNTIME_VERSION}-cp{abi}-cp{abi}-{platform}.whl"
        if indexed:
            item = indexed[name]
            if (item["size"] != size or item["digests"]["sha256"] != sha256
                    or urlsplit(item["url"]).hostname != "files.pythonhosted.org"):
                raise ValueError(f"Upstream index no longer matches the pin for {name}")
            source_url = item["url"]
        else:
            source_url = ""
        assets.append(dict(name=name, url=source_url, size=size, sha256=sha256))
    if source_release:
        from mpf.resources.RuntimeMirror import release_asset_url
        for asset in assets:
            asset["url"] = release_asset_url(source_release, asset["name"])
            asset["derivation"] = None
    names = [asset["name"] for asset in assets]
    if len(assets) != 27 or len(set(names)) != len(names):
        raise ValueError("Unexpected optional runtime inventory")
    return sorted(assets, key=lambda item: item["name"])


def verify(path, asset):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != asset["size"]:
        return False
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest() == asset["sha256"]


def _download(url, path, name, size_pin, digest_pin):
    digest, size = hashlib.sha256(), 0
    with _open(url) as source, path.open("xb") as target:
        while block := source.read(1024 * 1024):
            size += len(block)
            if size > size_pin:
                raise ValueError(f"Source exceeded pinned size: {name}")
            target.write(block)
            digest.update(block)
    if size != size_pin or digest.hexdigest() != digest_pin:
        raise ValueError(f"Source failed pinned integrity check: {name}")


def stage(directory, asset):
    path = directory / asset["name"]
    if verify(path, asset):
        return
    temporary = directory / (asset["name"] + ".part")
    original = directory / (asset["name"] + ".source.part")
    temporary.unlink(missing_ok=True)
    original.unlink(missing_ok=True)
    try:
        if asset.get("derivation") == "strip-windows-redist-v1":
            from tools.windows_runtime_without_redist import strip_cad_wheel, strip_python_helper
            _download(asset["url"], original, asset["name"],
                      asset["source_size"], asset["source_sha256"])
            if asset["name"].endswith(".whl"):
                strip_cad_wheel(original, temporary)
            else:
                strip_python_helper(original, temporary)
            if not verify(temporary, asset):
                raise ValueError(f"Derived archive failed pin: {asset['name']}")
        else:
            _download(asset["url"], temporary, asset["name"],
                      asset["size"], asset["sha256"])
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        original.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="staging directory outside the repository")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--source-release", help="copy exact bytes from an earlier MPF release")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory == ROOT or ROOT in directory.parents:
        parser.error("stage outside the repository so binaries cannot enter Git history")
    directory.mkdir(parents=True, exist_ok=True)
    assets = inventory(args.source_release, offline=args.verify_only)
    if not args.verify_only:
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(stage, directory, asset): asset["name"] for asset in assets}
            for job in as_completed(jobs):
                job.result()
                print(f"verified {jobs[job]}", flush=True)
    failed = [asset["name"] for asset in assets if not verify(directory / asset["name"], asset)]
    if failed:
        raise ValueError("Unverified assets: " + ", ".join(failed))
    if not args.verify_only:
        (directory / "manifest.json").write_text(json.dumps(assets, indent=2) + "\n")
    print(f"Verified {len(assets)} assets, {sum(a['size'] for a in assets):,} bytes")


if __name__ == "__main__":
    main()

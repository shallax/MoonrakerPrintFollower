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
    MODEL_SHA256, MODEL_SIZE, MODEL_URL, RUNTIME_VERSION, _WHEELS,
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


def inventory():
    assets = [dict(name="model-weights-5a6b1be1fa.onnx", url=MODEL_URL,
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
            assets.append(dict(name=name, **pin))
    with _open(f"https://pypi.org/pypi/onnxruntime/{RUNTIME_VERSION}/json") as response:
        if urlsplit(response.geturl()).hostname != "pypi.org":
            raise ValueError("Unexpected ONNX Runtime index host")
        index = json.load(response)
    indexed = {item["filename"]: item for item in index["urls"]}
    for (abi, platform), (sha256, size) in _WHEELS.items():
        name = f"onnxruntime-{RUNTIME_VERSION}-cp{abi}-cp{abi}-{platform}.whl"
        item = indexed[name]
        if (item["size"] != size or item["digests"]["sha256"] != sha256
                or urlsplit(item["url"]).hostname != "files.pythonhosted.org"):
            raise ValueError(f"Upstream index no longer matches the pin for {name}")
        assets.append(dict(name=name, url=item["url"], size=size, sha256=sha256))
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


def stage(directory, asset):
    path = directory / asset["name"]
    if verify(path, asset):
        return
    temporary = directory / (asset["name"] + ".part")
    temporary.unlink(missing_ok=True)
    try:
        digest, size = hashlib.sha256(), 0
        with _open(asset["url"]) as source, temporary.open("xb") as target:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > asset["size"]:
                    raise ValueError(f"Source exceeded pinned size: {asset['name']}")
                target.write(block)
                digest.update(block)
        if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
            raise ValueError(f"Source failed pinned integrity check: {asset['name']}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="staging directory outside the repository")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory == ROOT or ROOT in directory.parents:
        parser.error("stage outside the repository so binaries cannot enter Git history")
    directory.mkdir(parents=True, exist_ok=True)
    assets = inventory()
    if not args.verify_only:
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(stage, directory, asset): asset["name"] for asset in assets}
            for job in as_completed(jobs):
                job.result()
                print(f"verified {jobs[job]}", flush=True)
    failed = [asset["name"] for asset in assets if not verify(directory / asset["name"], asset)]
    if failed:
        raise ValueError("Unverified assets: " + ", ".join(failed))
    (directory / "manifest.json").write_text(json.dumps(assets, indent=2) + "\n")
    print(f"Verified {len(assets)} assets, {sum(a['size'] for a in assets):,} bytes")


if __name__ == "__main__":
    main()

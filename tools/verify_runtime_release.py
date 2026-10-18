# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [abf18f3] Mirror optional runtime assets on plugin releases

"""Check that a GitHub release has every pinned optional runtime asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpf.resources.RuntimeMirror import release_asset_url  # noqa: E402
from tools.stage_runtime_mirror import HTTPSOnly, inventory  # noqa: E402
from urllib.request import Request, build_opener  # noqa: E402


def release_assets(tag):
    output = subprocess.check_output(
        ["gh", "release", "view", tag, "--json", "assets", "--jq", ".assets"],
        text=True,
    )
    return {asset["name"]: asset for asset in json.loads(output)}


def verify_download(tag, asset):
    url = release_asset_url(tag, asset["name"])
    digest, size = hashlib.sha256(), 0
    with build_opener(HTTPSOnly()).open(
        Request(url, headers={"User-Agent": "MoonrakerPrintFollower-release-verifier"}),
        timeout=60,
    ) as response:
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > asset["size"]:
                raise ValueError(f"Oversize direct download: {asset['name']}")
            digest.update(block)
    if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
        raise ValueError(f"Bad direct download: {asset['name']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--public-downloads", action="store_true",
                        help="GET and hash every direct URL after publication")
    parser.add_argument("--require-companion", action="store_true",
                        help="require the corresponding-source and notices archive")
    args = parser.parse_args()
    pinned = inventory(offline=True)
    published = release_assets(args.tag)
    for asset in pinned:
        record = published.get(asset["name"])
        if not record or record["size"] != asset["size"] or \
                record.get("digest") != "sha256:" + asset["sha256"] or \
                record.get("state") != "uploaded":
            raise ValueError(f"Missing or mismatched release asset: {asset['name']}")
        if args.public_downloads:
            verify_download(args.tag, asset)
            print(f"download verified {asset['name']}", flush=True)
    if args.require_companion:
        companion = f"runtime-source-and-notices-{args.tag}.tar.gz"
        if companion not in published or published[companion].get("state") != "uploaded":
            raise ValueError(f"Missing redistribution companion: {companion}")
    print(f"Verified {len(pinned)} pinned runtime assets on {args.tag}")


if __name__ == "__main__":
    main()

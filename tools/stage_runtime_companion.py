# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-20 — Stage the audited runtime source/notices companion for later releases.

"""Copy the audited runtime companion while its covered payload pins match."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpf.resources.RuntimeMirror import release_asset_url  # noqa: E402
from tools.stage_runtime_mirror import inventory, stage, verify  # noqa: E402


SOURCE_TAG = "v5.4.0"
COMPANION_SIZE = 984542522
COMPANION_SHA256 = "fa102dc10f108bb6720a943f4c02a967b14cb943e3e9e5cba8c71d7573fb663f"
# URLs change for each release; the covered names and exact bytes must not.
COVERED_INVENTORY_SHA256 = "83c65ccf46e21c37a855915c3b9fd668c786f1488b19836b764c5c6b91ab1aa1"


def companion_asset(tag):
    name = f"runtime-source-and-notices-{tag}.tar.gz"
    release_asset_url(tag, name)
    covered = [{key: asset[key] for key in ("name", "size", "sha256")}
               for asset in inventory(offline=True)]
    encoded = json.dumps(covered, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != COVERED_INVENTORY_SHA256:
        raise ValueError("Runtime payloads changed: audit and pin a matching source/notices "
                         "companion before releasing")
    return dict(name=name, size=COMPANION_SIZE, sha256=COMPANION_SHA256,
                url=release_asset_url(SOURCE_TAG,
                                      f"runtime-source-and-notices-{SOURCE_TAG}.tar.gz"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("tag")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory == ROOT or ROOT in directory.parents:
        parser.error("stage outside the repository so source archives cannot enter Git history")
    asset = companion_asset(args.tag)
    directory.mkdir(parents=True, exist_ok=True)
    if not args.verify_only:
        stage(directory, asset)
    if not verify(directory / asset["name"], asset):
        raise ValueError("Missing or mismatched runtime source/notices companion")
    print(f"Verified {asset['name']} against the audited companion pin")


if __name__ == "__main__":
    main()

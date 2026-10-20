# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [abf18f3] Mirror optional runtime assets on plugin releases

"""Direct, versioned GitHub Release URLs for optional runtime assets."""

import json
from pathlib import Path
from urllib.parse import quote


_VERSION = json.loads(Path(__file__).resolve().parents[1].joinpath("plugin.json").read_text())["version"]
RELEASE_TAG = "v" + _VERSION
_RELEASES = "https://github.com/shallax/MoonrakerPrintFollower/releases/download/"
RELEASE_BASE = _RELEASES + RELEASE_TAG + "/"


def asset_url(filename: str) -> str:
    return release_asset_url(RELEASE_TAG, filename)


def release_asset_url(tag: str, filename: str) -> str:
    if not tag.startswith("v") or not tag[1:].replace(".", "").isdigit():
        raise ValueError("Invalid runtime release tag")
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
        raise ValueError("Invalid runtime release asset name")
    return _RELEASES + tag + "/" + quote(filename, safe="")

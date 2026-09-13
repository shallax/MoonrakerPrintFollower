#!/usr/bin/env python3
"""Fetch and prepare a pinned Cura version for the scenario harness.

Downloads the official AppImage, extracts it, unpacks the matching
PyQt6 + PyQt6-Qt6 wheels (the driver's QtTest import needs the full
PyPI wheel — Cura's bundle omits QtTest), and records everything in
manifest.json — the version-swap proof the release gates cite.

Layout per version, under /tmp/mpf/cura_versions/<version>/:
    root/           the extracted AppImage (APPDIR for the launch)
    wheels/         the PyQt6 wheel set unpacked (first on PYTHONPATH)
    manifest.json   source URL, sha256, python + PyQt6 pins, date

The AppImage is the source of truth for the pins: the tool reads the
bundled python and PyQt6 versions from the extraction, so the wheels
always match the bundle exactly.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

BASE = pathlib.Path("/tmp/mpf/cura_versions")
CONTAINER = "mpf-cura513"


def sh(*args: str, cwd: pathlib.Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def download(url: str, dest: pathlib.Path) -> None:
    # curl for resume/retry on a ~500 MB artifact.
    sh("curl", "-fL", "--retry", "3", "-C", "-", "-o", str(dest), url)


def find_one(pattern: str) -> str | None:
    hits = sorted(glob.glob(pattern, recursive=True))
    return hits[0] if hits else None


def pick_wheel(files: list[dict], abi3: bool) -> dict | None:
    """The x86_64 manylinux wheel for this package. PyQt6 ships
    cp39-abi3 (one wheel serves every python); PyQt6-Qt6 ships
    py3-none. musllinux and source dists never match."""
    def good(f: dict) -> bool:
        name = f["filename"]
        return (name.endswith(".whl")
                and "x86_64" in name
                and "manylinux" in name
                and "musllinux" not in name
                and (("abi3" in name or "cp3" in name) if abi3
                     else "py3-none" in name))
    wheels = sorted(f for f in files if good(f))
    return wheels[0] if wheels else None


def fetch_wheel(pkg: str, version: str, abi3: bool, dest: pathlib.Path) -> str:
    """Download the pinned wheel straight from the PyPI JSON API —
    no pip, and the tag choice is explicit (pip's --platform flag
    would silently skip PyQt6's manylinux_2_28 wheels)."""
    import urllib.request
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{version}/json") as resp:
        data = json.loads(resp.read())
    wheel = pick_wheel(data["urls"], abi3)
    if not wheel:
        raise RuntimeError(f"no x86_64 manylinux wheel for {pkg}=={version}")
    download(wheel["url"], dest / wheel["filename"])
    return wheel["filename"]


def discover(root: pathlib.Path) -> tuple[str, str]:
    """Read the bundled python and PyQt6 versions from the extraction."""
    python = find_one(str(root / "usr/bin/python3.*"))
    if not python:
        python = find_one(str(root / "bin/python3.*"))
    pyv = re.search(r"python(\d\.\d+)", python).group(1) if python else "3.11"
    dist = find_one(str(root / "**/PyQt6-*.dist-info")) or \
        find_one(str(root / "**/PyQt6_Qt6-*.dist-info"))
    if dist:
        m = re.search(r"PyQt6(?:_Qt6)?-([\d.]+)\.dist-info", dist)
        qt6 = m.group(1) if m else "6.6.0"
    else:
        # The Qt6 library version stands in for the wheel pin (the
        # PyQt6 and PyQt6-Qt6 wheels always share one version).
        lib = find_one(str(root / "**/Qt6/lib/libQt6Core.so.6*"))
        m = re.search(r"libQt6Core\.so\.6\.([\d.]+)", lib) if lib else None
        qt6 = m.group(1) if m else "6.6.0"
    return pyv, qt6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", default="5.13.0",
                    help="Cura version, e.g. 5.13.0 (default) or 5.12.0")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even if a manifest exists")
    args = ap.parse_args()
    version = args.version
    vdir = BASE / version
    if (vdir / "manifest.json").exists() and not args.force:
        print(f"{version}: already prepared at {vdir}")
        return 0
    vdir.mkdir(parents=True, exist_ok=True)

    appimage = vdir / "UltiMaker-Cura.AppImage"
    if not appimage.exists() or args.force:
        url = (f"https://github.com/Ultimaker/Cura/releases/download/"
               f"{version}/UltiMaker-Cura-{version}-linux-X64.AppImage")
        print(f"downloading {url}")
        download(url, appimage)
    sha256 = hashlib.sha256(appimage.read_bytes()).hexdigest()

    root = vdir / "root"
    if not root.exists() or args.force:
        print("extracting (this takes a couple of minutes)...")
        appimage.chmod(0o755)
        sh(str(appimage), "--appimage-extract", cwd=vdir)
        shutil.move(str(vdir / "squashfs-root"), str(root))

    pyv, qt6 = discover(root)
    print(f"bundled python {pyv}, PyQt6 {qt6}")

    wheels = vdir / "wheels"
    wheels.mkdir(exist_ok=True)
    wheels_dl = vdir / "downloads"
    wheels_dl.mkdir(exist_ok=True)
    print(f"downloading PyQt6=={qt6} wheels (the bundled python is {pyv}; "
          "the wheels are abi3)")
    wheel_files = []
    for pkg, abi3 in (("PyQt6", True), ("PyQt6-Qt6", False)):
        wheel_files.append(fetch_wheel(pkg, qt6, abi3, wheels_dl))
    for whl in wheels_dl.glob("*.whl"):
        sh("unzip", "-o", "-q", str(whl), "-d", str(wheels))

    qt_test = wheels / "PyQt6" / "QtTest.abi3.so"
    if not qt_test.exists():
        print(f"error: the wheel set lacks the QtTest binding ({qt_test})")
        return 1

    manifest = {
        "cura_version": version,
        "appimage_url": f"https://github.com/Ultimaker/Cura/releases/download/"
                       f"{version}/UltiMaker-Cura-{version}-linux-X64.AppImage",
        "appimage_sha256": sha256,
        "bundled_python": pyv,
        "bundled_pyqt6": qt6,
        "wheels": [
            {"filename": name,
             "sha256": hashlib.sha256(
                 (wheels_dl / name).read_bytes()).hexdigest()}
            for name in sorted(wheel_files)
        ],
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (vdir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    print(f"prepared: {vdir}")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

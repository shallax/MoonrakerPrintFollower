from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins"
PACKAGE_JSON = ROOT / "package.json"


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])

    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}.curapackage"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    plugin_prefix = pathlib.PurePosixPath("files") / "plugins" / package_id

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(PACKAGE_JSON, "package.json")
        for path in sorted(PLUGIN_ROOT.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            relative = path.relative_to(PLUGIN_ROOT)
            archive.write(path, (plugin_prefix / pathlib.PurePosixPath(relative.as_posix())).as_posix())

    with zipfile.ZipFile(output, "r") as archive:
        names = set(archive.namelist())
        required = {
            "package.json",
            f"files/plugins/{package_id}/plugin.json",
            f"files/plugins/{package_id}/__init__.py",
            f"files/plugins/{package_id}/MoonrakerPrintFollower.py",
            f"files/plugins/{package_id}/MoonrakerOutputDevice.py",
            f"files/plugins/{package_id}/MoonrakerMonitor.qml",
            f"files/plugins/{package_id}/MoonrakerMonitorModel.py",
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError(f"curapackage is missing required entries: {missing}")

        embedded = json.loads(archive.read("package.json").decode("utf-8"))
        if embedded.get("package_id") != package_id or embedded.get("package_version") != version:
            raise RuntimeError("embedded package metadata does not match source package.json")

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Moonraker Print Follower Cura package")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()

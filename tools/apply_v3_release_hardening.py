from __future__ import annotations

import pathlib
import shutil
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected block not found for {label} in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


# ---------------------------------------------------------------------------
# Repository hygiene and release tooling
# ---------------------------------------------------------------------------
write(ROOT / ".gitignore", r'''
__pycache__/
*.py[cod]
*.pyo
*.orig
*.rej
*.swp
*.swo
*.tmp
*.bak
.DS_Store
/dist/
''')

for cache_dir in ROOT.rglob("__pycache__"):
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir, ignore_errors=True)

legacy_dashboard = ROOT / "plugins" / "MoonrakerMonitorEnhanced.qml"
if legacy_dashboard.exists():
    legacy_dashboard.unlink()

write(ROOT / "tools" / "check_qml.py", r'''
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
OPENERS = {"{": "}", "(": ")", "[": "]"}
CLOSERS = {value: key for key, value in OPENERS.items()}


@dataclass
class Frame:
    opener: str
    line: int
    qml_object: bool = False
    properties: dict[str, int] = field(default_factory=dict)
    statement_start: bool = True


def _sanitise(text: str) -> tuple[str, List[str]]:
    out: List[str] = []
    errors: List[str] = []
    state = "code"
    quote = ""
    escaped = False
    line = 1
    string_line = 1
    block_line = 1
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\n":
            line += 1
        if state == "line-comment":
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block-comment":
            if ch == "*" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                state = "code"
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if state == "string":
            out.append("\n" if ch == "\n" else " ")
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                state = "code"
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out.extend((" ", " "))
            i += 2
            state = "line-comment"
            continue
        if ch == "/" and nxt == "*":
            block_line = line
            out.extend((" ", " "))
            i += 2
            state = "block-comment"
            continue
        if ch in {'"', "'"}:
            quote = ch
            string_line = line
            state = "string"
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if state == "string":
        errors.append(f"line {string_line}: unterminated string")
    elif state == "block-comment":
        errors.append(f"line {block_line}: unterminated block comment")
    return "".join(out), errors


def _is_qml_type(identifier: Optional[str]) -> bool:
    if not identifier:
        return False
    tail = identifier.rsplit(".", 1)[-1]
    return bool(tail and tail[0].isupper())


def check_text(text: str, name: str = "<qml>") -> List[str]:
    clean, errors = _sanitise(text)
    stack: List[Frame] = []
    line = 1
    previous_identifier: Optional[str] = None
    i = 0
    while i < len(clean):
        ch = clean[i]
        if ch == "\n":
            line += 1
            if stack and stack[-1].qml_object:
                stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        match = IDENT.match(clean, i)
        if match:
            ident = match.group(0)
            end = match.end()
            j = end
            while j < len(clean) and clean[j] in " \t\r":
                j += 1
            if stack and stack[-1].qml_object and stack[-1].statement_start and j < len(clean) and clean[j] == ":":
                first = stack[-1].properties.get(ident)
                if first is not None:
                    errors.append(f"line {line}: duplicate property '{ident}' (first set on line {first})")
                else:
                    stack[-1].properties[ident] = line
                stack[-1].statement_start = False
            elif stack and stack[-1].qml_object and stack[-1].statement_start:
                # Keep statement_start true for a QML type name immediately
                # followed by an object brace; otherwise this begins an expression.
                k = j
                if k >= len(clean) or clean[k] != "{":
                    stack[-1].statement_start = False
            previous_identifier = ident
            i = end
            continue
        if ch in OPENERS:
            qml_object = ch == "{" and _is_qml_type(previous_identifier)
            stack.append(Frame(ch, line, qml_object=qml_object))
            previous_identifier = None
            i += 1
            continue
        if ch in CLOSERS:
            if not stack or stack[-1].opener != CLOSERS[ch]:
                errors.append(f"line {line}: unmatched '{ch}'")
            else:
                stack.pop()
                if stack and stack[-1].qml_object:
                    stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch == ";":
            if stack and stack[-1].qml_object:
                stack[-1].statement_start = True
            previous_identifier = None
            i += 1
            continue
        if ch == ":":
            previous_identifier = None
            i += 1
            continue
        if stack and stack[-1].qml_object and ch not in ",":
            stack[-1].statement_start = False
        previous_identifier = None
        i += 1

    for frame in reversed(stack):
        errors.append(f"line {frame.line}: unclosed '{frame.opener}'")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first and not first.startswith("import "):
        errors.append("line 1: QML file should begin with imports")
    return [f"{name}: {error}" for error in errors]


def iter_qml(paths: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.qml"))
        elif path.suffix.lower() == ".qml":
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural QML release sanity checker")
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    failures: List[str] = []
    files = list(iter_qml(args.paths))
    if not files:
        print("No QML files found", file=sys.stderr)
        return 2
    for path in files:
        failures.extend(check_text(path.read_text(encoding="utf-8"), str(path)))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"QML sanity check passed for {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')

write(ROOT / "tools" / "build_curapackage.py", r'''
from __future__ import annotations

import argparse
import json
import pathlib
import zipfile
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins"
PACKAGE_JSON = ROOT / "package.json"

FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"}
FORBIDDEN_NAMES = {".DS_Store"}


def is_packaged_source(path: pathlib.Path) -> bool:
    relative = path.relative_to(PLUGIN_ROOT)
    if not path.is_file():
        return False
    if "__pycache__" in relative.parts:
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    return True


def iter_plugin_sources() -> Iterable[pathlib.Path]:
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if is_packaged_source(path):
            yield path


def archive_name(path: pathlib.Path, package_id: str) -> str:
    relative = path.relative_to(PLUGIN_ROOT).as_posix()
    return f"files/plugins/{package_id}/{relative}"


def expected_archive_entries(package_id: str) -> set[str]:
    return {"package.json"} | {archive_name(path, package_id) for path in iter_plugin_sources()}


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}.curapackage"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(PACKAGE_JSON, "package.json")
        for path in iter_plugin_sources():
            archive.write(path, archive_name(path, package_id))

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Moonraker Print Follower Cura package")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
''')

write(ROOT / "tools" / "verify_curapackage.py", r'''
from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

from build_curapackage import PACKAGE_JSON, PLUGIN_ROOT, archive_name, expected_archive_entries, iter_plugin_sources


def verify(path: pathlib.Path) -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("curapackage contains duplicate archive entries")
        actual = set(names)
        expected = expected_archive_entries(package_id)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            raise RuntimeError(f"curapackage file set mismatch; missing={missing}, unexpected={unexpected}")

        embedded_package = json.loads(archive.read("package.json").decode("utf-8"))
        if embedded_package != package:
            raise RuntimeError("embedded package.json differs from source")
        plugin_meta_path = f"files/plugins/{package_id}/plugin.json"
        plugin_meta = json.loads(archive.read(plugin_meta_path).decode("utf-8"))
        if str(plugin_meta.get("version")) != version:
            raise RuntimeError("plugin.json version does not match package version")

        for source in iter_plugin_sources():
            name = archive_name(source, package_id)
            if archive.read(name) != source.read_bytes():
                raise RuntimeError(f"packaged bytes differ from source: {source.relative_to(PLUGIN_ROOT)}")

        forbidden = [
            name for name in names
            if "__pycache__" in name
            or name.endswith((".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"))
            or "/." in name
        ]
        if forbidden:
            raise RuntimeError(f"curapackage contains forbidden build debris: {forbidden}")
    print(f"Verified exact source/package parity for {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Moonraker Print Follower curapackage")
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    verify(args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')

write(ROOT / ".github" / "workflows" / "tests.yml", r'''
name: Tests

on:
  push:
    branches:
      - main
      - v3-unified-moonraker
  pull_request:

jobs:
  python-compatibility:
    name: Python ${{ matrix.python-version }} syntax
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - name: Check out source
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Compile Python sources
        run: python -m compileall -q plugins tools tests

  release-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Check out source
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: QML structural sanity
        run: python tools/check_qml.py plugins
      - name: Run all discovered tests
        run: python -m unittest discover -s tests -p "test_*.py"
      - name: Read package version
        id: package_meta
        run: |
          python - <<'PY'
          import json
          import os
          package = json.load(open("package.json", encoding="utf-8"))
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
              output.write(f"version={package['package_version']}\n")
          PY
      - name: Build Cura package
        run: python tools/build_curapackage.py
      - name: Verify exact Cura package contents
        run: python tools/verify_curapackage.py dist/MoonrakerPrintFollower-v${{ steps.package_meta.outputs.version }}.curapackage
      - name: Upload Cura package artifact
        uses: actions/upload-artifact@v4
        with:
          name: MoonrakerPrintFollower-v${{ steps.package_meta.outputs.version }}-curapackage
          path: dist/MoonrakerPrintFollower-v${{ steps.package_meta.outputs.version }}.curapackage
          if-no-files-found: error
          retention-days: 30
''')

# ---------------------------------------------------------------------------
# Monitor request/session lifecycle hardening and malformed status handling
# ---------------------------------------------------------------------------
monitor = ROOT / "plugins" / "MoonrakerMonitorModel.py"
replace_once(
    monitor,
    '        self._network = QNetworkAccessManager(self)\n        self._requests: Dict[str, QNetworkReply] = {}\n',
    '        self._network = QNetworkAccessManager(self)\n        self._requests: Dict[str, QNetworkReply] = {}\n        self._request_generation = 0\n        self._request_identity: Optional[tuple[str, str]] = None\n        self._monitoring_active = True\n',
    "monitor request session fields",
)
replace_once(
    monitor,
    '        self._aux_timer.start()\n        self._power_timer.start()\n        self._system_timer.start()\n        self._discovery_timer.start()\n        self.refreshAll()\n\n    # ------------------------------------------------------------------\n    # Generic Moonraker HTTP helpers\n',
    '''        self._start_background_timers()\n        self.refreshAll()\n\n    def _start_background_timers(self) -> None:\n        for timer in (self._aux_timer, self._power_timer, self._system_timer, self._discovery_timer):\n            if not timer.isActive():\n                timer.start()\n\n    def _stop_background_timers(self) -> None:\n        for timer in (self._core_timer, self._aux_timer, self._power_timer, self._system_timer, self._discovery_timer):\n            timer.stop()\n\n    def _current_request_identity(self) -> tuple[str, str]:\n        config = self._follower.current_printer_config()\n        return (str(config.url or "").strip().rstrip("/"), str(config.api_key or ""))\n\n    def _invalidate_request_session(self) -> None:\n        self._request_generation += 1\n        for channel in list(self._requests):\n            self._cancel_channel(channel)\n\n    def _ensure_request_session(self) -> None:\n        identity = self._current_request_identity()\n        if identity != self._request_identity:\n            self._request_identity = identity\n            self._invalidate_request_session()\n\n    def setMonitoringActive(self, active: bool) -> None:\n        active = bool(active)\n        if active == self._monitoring_active:\n            if active:\n                self._ensure_request_session()\n            return\n        self._monitoring_active = active\n        if not active:\n            self._stop_background_timers()\n            self._invalidate_request_session()\n            self._action_busy = False\n            self._action_status = ""\n            self.actionChanged.emit()\n            return\n        self._request_identity = None\n        self._start_background_timers()\n        self.refreshAll()\n\n    # ------------------------------------------------------------------\n    # Generic Moonraker HTTP helpers\n''',
    "monitor lifecycle methods",
)
replace_once(
    monitor,
    '    def _json_request(\n        self,\n        channel: str,\n        method: str,\n        path: str,\n        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],\n        *,\n        body: Optional[Dict[str, Any]] = None,\n        replace: bool = False,\n    ) -> bool:\n        if not self._usable_base_url():\n            return False\n',
    '    def _json_request(\n        self,\n        channel: str,\n        method: str,\n        path: str,\n        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],\n        *,\n        body: Optional[Dict[str, Any]] = None,\n        replace: bool = False,\n    ) -> bool:\n        if not self._monitoring_active:\n            return False\n        self._ensure_request_session()\n        if not self._usable_base_url():\n            return False\n',
    "monitor request active guard",
)
replace_once(
    monitor,
    '        self._requests[channel] = reply\n        reply.finished.connect(\n            lambda r=reply, c=channel, cb=callback: self._finish_json_request(c, r, cb)\n        )\n',
    '        self._requests[channel] = reply\n        generation = self._request_generation\n        reply.finished.connect(\n            lambda r=reply, c=channel, cb=callback, g=generation: self._finish_json_request(c, r, cb, g)\n        )\n',
    "monitor generation capture",
)
replace_once(
    monitor,
    '    def _finish_json_request(\n        self,\n        channel: str,\n        reply: QNetworkReply,\n        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],\n    ) -> None:\n        if self._requests.get(channel) is not reply:\n',
    '    def _finish_json_request(\n        self,\n        channel: str,\n        reply: QNetworkReply,\n        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],\n        generation: int,\n    ) -> None:\n        if generation != self._request_generation:\n            if self._requests.get(channel) is reply:\n                self._requests.pop(channel, None)\n            try:\n                reply.deleteLater()\n            except Exception:\n                pass\n            return\n        if self._requests.get(channel) is not reply:\n',
    "monitor generation rejection",
)
replace_once(
    monitor,
    '    @staticmethod\n    def _result(payload: Optional[Dict[str, Any]]) -> Any:\n',
    '    @staticmethod\n    def _status_object(status: Any, name: str) -> Dict[str, Any]:\n        if not isinstance(status, dict):\n            return {}\n        value = status.get(name)\n        return value if isinstance(value, dict) else {}\n\n    @staticmethod\n    def _result(payload: Optional[Dict[str, Any]]) -> Any:\n',
    "status object helper",
)
replace_once(
    monitor,
    '    @pyqtSlot()\n    def refreshAll(self) -> None:\n        self.refreshTransport()\n',
    '    @pyqtSlot()\n    def refreshAll(self) -> None:\n        if not self._monitoring_active:\n            return\n        self.refreshTransport()\n',
    "refresh active guard",
)
replace_once(
    monitor,
    '    @pyqtSlot()\n    def refreshTransport(self) -> None:\n        config = self._follower.current_printer_config()\n',
    '    @pyqtSlot()\n    def refreshTransport(self) -> None:\n        if not self._monitoring_active:\n            self._core_timer.stop()\n            self._cancel_channel("core")\n            return\n        self._ensure_request_session()\n        config = self._follower.current_printer_config()\n',
    "transport active guard",
)
replace_once(
    monitor,
    '    @pyqtSlot(object)\n    def updateMoonrakerStatus(self, status: Any) -> None:\n        if not isinstance(status, dict):\n            return\n\n        print_stats = status.get("print_stats") or {}\n        virtual_sdcard = status.get("virtual_sdcard") or {}\n        gcode_move = status.get("gcode_move") or {}\n        motion_report = status.get("motion_report") or {}\n',
    '    @pyqtSlot(object)\n    def updateMoonrakerStatus(self, status: Any) -> None:\n        if not self._monitoring_active or not isinstance(status, dict):\n            return\n\n        print_stats = self._status_object(status, "print_stats")\n        virtual_sdcard = self._status_object(status, "virtual_sdcard")\n        gcode_move = self._status_object(status, "gcode_move")\n        motion_report = self._status_object(status, "motion_report")\n',
    "malformed core status guard",
)

# Harden the follower-aware layers against malformed nested Moonraker objects too.
for relative in ("MoonrakerMonitorRuntime.py", "MoonrakerMonitorControls.py"):
    path = ROOT / "plugins" / relative
    text = path.read_text(encoding="utf-8")
    text = text.replace('        print_stats = status.get("print_stats") or {}\n        gcode_move = status.get("gcode_move") or {}\n        virtual_sdcard = status.get("virtual_sdcard") or {}\n',
                        '        print_stats = self._status_object(status, "print_stats")\n        gcode_move = self._status_object(status, "gcode_move")\n        virtual_sdcard = self._status_object(status, "virtual_sdcard")\n')
    if relative == "MoonrakerMonitorControls.py":
        text = text.replace('            print_stats = status.get("print_stats") or {}\n            filename = str(print_stats.get("filename") or "")\n',
                            '            print_stats = self._status_object(status, "print_stats")\n            filename = str(print_stats.get("filename") or "")\n', 1)
        text = text.replace('            gcode_move = status.get("gcode_move") or {}\n            try:\n',
                            '            gcode_move = self._status_object(status, "gcode_move")\n            try:\n', 1)
    path.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Only the active Cura printer may poll/update Monitor state.
# ---------------------------------------------------------------------------
output_plugin = ROOT / "plugins" / "MoonrakerOutputDevicePlugin.py"
text = output_plugin.read_text(encoding="utf-8")
text = text.replace('# MoonrakerMonitorEnhanced.qml is retained in the package as the previous dashboard.\n', '')
output_plugin.write_text(text, encoding="utf-8")
replace_once(
    output_plugin,
    '    def stop(self) -> None:\n        if self._current is not None:\n            try:\n                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n            except Exception:\n                pass\n        self._current = None\n\n    @staticmethod\n    def _usable_url(value: str) -> bool:\n',
    '''    def stop(self) -> None:\n        for device in self._devices.values():\n            self._set_monitor_active(device, False)\n        if self._current is not None:\n            try:\n                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n            except Exception:\n                pass\n        self._current = None\n\n    @staticmethod\n    def _set_monitor_active(device: MoonrakerOutputDevice, active: bool) -> None:\n        monitor = getattr(device, "activePrinter", None)\n        setter = getattr(monitor, "setMonitoringActive", None)\n        if callable(setter):\n            try:\n                setter(bool(active))\n            except Exception:\n                pass\n\n    @staticmethod\n    def _usable_url(value: str) -> bool:\n''',
    "output plugin active monitor helper",
)
replace_once(
    output_plugin,
    '            # PrinterOutputDevice exposes activePrinter from this model list.\n            device._printers = [monitor]\n\n        # Bed-mesh wrapper composes',
    '            # PrinterOutputDevice exposes activePrinter from this model list.\n            device._printers = [monitor]\n\n        self._set_monitor_active(device, True)\n\n        # Bed-mesh wrapper composes',
    "activate installed monitor",
)
text = output_plugin.read_text(encoding="utf-8")
text = text.replace('            if self._current is not None and self._current.getId() != MoonrakerOutputDevice.DEVICE_PREFIX + machine_id:\n                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n                self._current = None\n',
                    '            if self._current is not None and self._current.getId() != MoonrakerOutputDevice.DEVICE_PREFIX + machine_id:\n                self._set_monitor_active(self._current, False)\n                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n                self._current = None\n')
text = text.replace('            if not usable:\n                if self._current is not None:\n                    self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n                    self._current = None\n',
                    '            if not usable:\n                if self._current is not None:\n                    self._set_monitor_active(self._current, False)\n                    self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n                    self._current = None\n')
text = text.replace('                if self._current is not None:\n                    try:\n                        self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n',
                    '                if self._current is not None:\n                    self._set_monitor_active(self._current, False)\n                    try:\n                        self.getOutputDeviceManager().removeOutputDevice(self._current.getId())\n', 1)
output_plugin.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Deferred sliders: show intended selection live, apply only on release.
# ---------------------------------------------------------------------------
dashboard = ROOT / "plugins" / "MoonrakerMonitorDashboard.qml"
replace_once(
    dashboard,
    '        function refreshMacroParameters()\n        {\n',
    '        function sliderSelection(slider)\n        {\n            if (slider == null) return 0\n            return Math.round(slider.valueAt(slider.position))\n        }\n\n        function refreshMacroParameters()\n        {\n',
    "slider selection helper",
)
text = dashboard.read_text(encoding="utf-8")
replacements = {
    'Math.round(speedSlider.value) + "%"': 'root.sliderSelection(speedSlider) + "%"',
    'root.printer.setSpeedFactor(Math.round(value))': 'root.printer.setSpeedFactor(root.sliderSelection(speedSlider))',
    'Math.round(flowSlider.value) + "%"': 'root.sliderSelection(flowSlider) + "%"',
    'root.printer.setFlowFactor(Math.round(value))': 'root.printer.setFlowFactor(root.sliderSelection(flowSlider))',
    'Math.round(fanSlider.value) + "%"': 'root.sliderSelection(fanSlider) + "%"',
    'root.printer.setFanSpeed(modelData.object, Math.round(value))': 'root.printer.setFanSpeed(modelData.object, root.sliderSelection(fanSlider))',
    '"Brightness " + Math.round(ledSlider.value) + "%"': '"Brightness " + root.sliderSelection(ledSlider) + "%"',
    'root.printer.setLedBrightness(modelData.object, Math.round(value))': 'root.printer.setLedBrightness(modelData.object, root.sliderSelection(ledSlider))',
    'Math.round(redSlider.value)': 'root.sliderSelection(redSlider)',
    'Math.round(greenSlider.value)': 'root.sliderSelection(greenSlider)',
    'Math.round(blueSlider.value)': 'root.sliderSelection(blueSlider)',
    'Math.round(whiteSlider.value)': 'root.sliderSelection(whiteSlider)',
    'Math.round(ledSlider.value)': 'root.sliderSelection(ledSlider)',
    'Math.round(pwmSlider.value) + "%"': 'root.sliderSelection(pwmSlider) + "%"',
    'root.printer.setPwmOutput(modelData.object, Math.round(value))': 'root.printer.setPwmOutput(modelData.object, root.sliderSelection(pwmSlider))',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"Expected dashboard slider token missing: {old}")
    text = text.replace(old, new)
dashboard.write_text(text, encoding="utf-8")

# Remove a test-only legacy marker comment from runtime code.
lifecycle = ROOT / "plugins" / "MoonrakerOutputDeviceLifecycle.py"
text = lifecycle.read_text(encoding="utf-8")
text = text.replace('    # Legacy hidden-path contract was: return "" if self._is_hidden_remote_path(path) else path\n    # The UI now labels that same logical empty/root path as <root>.\n',
                    '    # The UI labels Moonraker\'s logical empty/root path as <root>.\n')
lifecycle.write_text(text, encoding="utf-8")

hotfix = ROOT / "tests" / "test_hotfix_regressions.py"
text = hotfix.read_text(encoding="utf-8")
text = text.replace('        self.assertIn(\'return "" if self._is_hidden_remote_path(path) else path\', LIFECYCLE)\n',
                    '        self.assertIn("if not path or self._is_hidden_remote_path(path):", LIFECYCLE)\n        self.assertIn("return self.ROOT_UPLOAD_LABEL", LIFECYCLE)\n')
hotfix.write_text(text, encoding="utf-8")

followup = ROOT / "tests" / "test_v3_followup_regressions.py"
text = followup.read_text(encoding="utf-8")
anchor = '        self.assertNotIn("onMoved:", DASHBOARD_QML)\n'
addition = anchor + '''        self.assertIn("function sliderSelection(slider)", DASHBOARD_QML)\n        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_QML)\n        self.assertIn("root.sliderSelection(speedSlider) + \\\"%\\\"", DASHBOARD_QML)\n        self.assertIn("setSpeedFactor(root.sliderSelection(speedSlider))", DASHBOARD_QML)\n        self.assertIn("setFlowFactor(root.sliderSelection(flowSlider))", DASHBOARD_QML)\n'''
if anchor not in text:
    raise SystemExit("followup slider test anchor missing")
text = text.replace(anchor, addition, 1)
followup.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Full miniature G-code fixtures for layer/ETA release tests.
# ---------------------------------------------------------------------------
fixtures = ROOT / "tests" / "fixtures" / "gcode"
fixtures.mkdir(parents=True, exist_ok=True)
write(fixtures / "cura.gcode", '''
;FLAVOR:Marlin
;TIME:25200
;LAYER:0
SET_PRINT_STATS_INFO CURRENT_LAYER=1 TOTAL_LAYER=3
G90
G1 X10 Y10 Z0.20
;TIME_ELAPSED:120.0
;LAYER:1
SET_PRINT_STATS_INFO CURRENT_LAYER=2 TOTAL_LAYER=3
G1 X20 Y10 Z0.40
;TIME_ELAPSED:420.0
;LAYER:2
SET_PRINT_STATS_INFO CURRENT_LAYER=3 TOTAL_LAYER=3
G1 X30 Y10 Z0.60
;TIME_ELAPSED:900.0
''')
write(fixtures / "orca.gcode", '''
; generated by OrcaSlicer
; layer num/total_layer_count: 1/3
G1 X1 Y1 Z0.20
; layer num/total_layer_count: 2/3
G1 X2 Y2 Z0.40
; layer num/total_layer_count: 3/3
G1 X3 Y3 Z0.60
''')
write(fixtures / "prusa.gcode", '''
; generated by PrusaSlicer
;LAYER_CHANGE
;Z:0.20
;HEIGHT:0.20
G1 X1 Y1 Z0.20
;LAYER_CHANGE
;Z:0.35
;HEIGHT:0.15
G1 X2 Y2 Z0.35
;LAYER_CHANGE
;Z:0.60
;HEIGHT:0.25
G1 X3 Y3 Z0.60
''')
write(fixtures / "missing_time.gcode", '''
;LAYER:0
G1 X0 Y0 Z0.20
;LAYER:1
G1 X10 Y0 Z0.40
''')
write(fixtures / "variable_layers.gcode", '''
;LAYER:0
G1 X0 Y0 Z0.20
;TIME_ELAPSED:10
;LAYER:1
G1 X10 Y0 Z0.27
;TIME_ELAPSED:22
;LAYER:2
G1 X20 Y0 Z0.45
;TIME_ELAPSED:45
''')
write(fixtures / "pause.gcode", '''
;LAYER:0
G1 X0 Y0 Z0.20
;TIME_ELAPSED:10
;LAYER:1
G1 X10 Y0 Z0.40
PAUSE
G1 X20 Y0 Z0.40
;TIME_ELAPSED:40
;LAYER:2
G1 X30 Y0 Z0.60
''')
write(fixtures / "resume.gcode", '''
;LAYER:0
SET_PRINT_STATS_INFO CURRENT_LAYER=1 TOTAL_LAYER=4
G1 X0 Y0 Z0.20
;TIME_ELAPSED:60
;LAYER:1
SET_PRINT_STATS_INFO CURRENT_LAYER=2 TOTAL_LAYER=4
G1 X10 Y0 Z0.40
;TIME_ELAPSED:240
;LAYER:2
SET_PRINT_STATS_INFO CURRENT_LAYER=3 TOTAL_LAYER=4
G1 X20 Y0 Z0.60
;TIME_ELAPSED:540
;LAYER:3
SET_PRINT_STATS_INFO CURRENT_LAYER=4 TOTAL_LAYER=4
G1 X30 Y0 Z0.80
;TIME_ELAPSED:900
''')

write(ROOT / "tests" / "test_release_hardening.py", r'''
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
TOOLS = ROOT / "tools"
FIXTURES = ROOT / "tests" / "fixtures" / "gcode"
for path in (PLUGINS, TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from GCodeIndex import build_index_from_file
from check_qml import check_text
from build_curapackage import build
from verify_curapackage import verify

MONITOR_SOURCE = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
OUTPUT_PLUGIN_SOURCE = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
TYPED_SOURCE = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text(encoding="utf-8")
DASHBOARD_SOURCE = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")


class DummySignal:
    def connect(self, *_args, **_kwargs):
        pass
    def emit(self, *_args, **_kwargs):
        pass


class DummyTimer:
    def __init__(self, *_args, **_kwargs):
        self.active = False
        self.timeout = DummySignal()
        self.interval = 0
    def setInterval(self, value):
        self.interval = value
    def setSingleShot(self, *_args):
        pass
    def start(self):
        self.active = True
    def stop(self):
        self.active = False
    def isActive(self):
        return self.active
    @staticmethod
    def singleShot(_delay, callback):
        callback()


class DummyReply:
    def __init__(self):
        self.deleted = False
        self.aborted = False
    def isRunning(self):
        return True
    def abort(self):
        self.aborted = True
    def deleteLater(self):
        self.deleted = True


def _pyqt_signal(*_args, **_kwargs):
    return DummySignal()


def _pyqt_property(*_args, **_kwargs):
    def decorate(function):
        return property(function)
    return decorate


def _pyqt_slot(*_args, **_kwargs):
    def decorate(function):
        return function
    return decorate


def load_monitor_model():
    class QByteArray(bytes):
        pass
    class QUrl:
        def __init__(self, value=""):
            self.value = value
        def isValid(self):
            return True
        def scheme(self):
            return "http"
        def host(self):
            return "host"
    class QVariant:
        def __init__(self, value=None):
            self.value = value
    class QNetworkReply:
        class NetworkError:
            NoError = 0
    class QNetworkRequest:
        class KnownHeaders:
            ContentTypeHeader = 0
    class QNetworkAccessManager:
        def __init__(self, *_args):
            pass
    class QDesktopServices:
        @staticmethod
        def openUrl(*_args):
            pass
    class PrinterOutputModel:
        def __init__(self, *_args, **_kwargs):
            pass
    class Logger:
        @staticmethod
        def log(*_args, **_kwargs):
            pass

    modules = {
        "PyQt6": types.ModuleType("PyQt6"),
        "PyQt6.QtCore": types.ModuleType("PyQt6.QtCore"),
        "PyQt6.QtGui": types.ModuleType("PyQt6.QtGui"),
        "PyQt6.QtNetwork": types.ModuleType("PyQt6.QtNetwork"),
        "cura": types.ModuleType("cura"),
        "cura.PrinterOutput": types.ModuleType("cura.PrinterOutput"),
        "cura.PrinterOutput.Models": types.ModuleType("cura.PrinterOutput.Models"),
        "cura.PrinterOutput.Models.PrinterOutputModel": types.ModuleType("cura.PrinterOutput.Models.PrinterOutputModel"),
        "UM": types.ModuleType("UM"),
        "UM.Logger": types.ModuleType("UM.Logger"),
        "plugins": types.ModuleType("plugins"),
        "plugins.MoonrakerProtocol": types.ModuleType("plugins.MoonrakerProtocol"),
    }
    modules["plugins"].__path__ = []
    core = modules["PyQt6.QtCore"]
    core.QByteArray = QByteArray; core.QTimer = DummyTimer; core.QUrl = QUrl; core.QVariant = QVariant
    core.pyqtProperty = _pyqt_property; core.pyqtSignal = _pyqt_signal; core.pyqtSlot = _pyqt_slot
    modules["PyQt6.QtGui"].QDesktopServices = QDesktopServices
    network = modules["PyQt6.QtNetwork"]
    network.QNetworkAccessManager = QNetworkAccessManager; network.QNetworkReply = QNetworkReply; network.QNetworkRequest = QNetworkRequest
    modules["cura.PrinterOutput.Models.PrinterOutputModel"].PrinterOutputModel = PrinterOutputModel
    modules["UM.Logger"].Logger = Logger
    modules["plugins.MoonrakerProtocol"].status_endpoint = lambda base: base + "/printer/objects/query"

    old = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        namespace = {"__name__": "plugins.MoonrakerMonitorModel", "__package__": "plugins"}
        exec(compile(MONITOR_SOURCE, "MoonrakerMonitorModel.py", "exec"), namespace)
        return namespace["MoonrakerMonitorModel"]
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def load_typed_model():
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")
    class QVariant:
        def __init__(self, value=None): self.value = value
    qtcore.QVariant = QVariant
    qtcore.pyqtProperty = _pyqt_property; qtcore.pyqtSignal = _pyqt_signal; qtcore.pyqtSlot = _pyqt_slot
    package = types.ModuleType("plugins"); package.__path__ = []
    base = types.ModuleType("plugins.MoonrakerMonitorControls")
    base.MoonrakerMonitorModel = type("Base", (), {"_want_aux_object": staticmethod(lambda _name: False)})
    modules = {"PyQt6": pyqt6, "PyQt6.QtCore": qtcore, "plugins": package, "plugins.MoonrakerMonitorControls": base}
    old = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        namespace = {"__name__": "plugins.MoonrakerMonitorTypedControls", "__package__": "plugins"}
        exec(compile(TYPED_SOURCE, "MoonrakerMonitorTypedControls.py", "exec"), namespace)
        return namespace["MoonrakerMonitorModel"]
    finally:
        for name, value in old.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value


class ReleaseHardeningTests(unittest.TestCase):
    def test_all_qml_pass_structural_checker(self):
        failures = []
        for path in sorted(PLUGINS.glob("*.qml")):
            failures.extend(check_text(path.read_text(encoding="utf-8"), path.name))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_qml_checker_rejects_duplicate_property_and_unbalanced_brace(self):
        bad = "import QtQuick 2.15\nItem { width: 1; width: 2\n"
        failures = check_text(bad, "bad.qml")
        self.assertTrue(any("duplicate property 'width'" in item for item in failures))
        self.assertTrue(any("unclosed '{'" in item for item in failures))

    def test_repository_has_no_tracked_python_cache_or_legacy_dashboard(self):
        tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        debris = [name for name in tracked if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
        self.assertEqual(debris, [])
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("__pycache__/", gitignore)
        self.assertIn("*.py[cod]", gitignore)

    def test_package_is_exact_byte_for_byte_source_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "candidate.curapackage"
            build(package)
            verify(package)

    def test_cura_orca_prusa_and_variable_layer_fixtures(self):
        cura = build_index_from_file(str(FIXTURES / "cura.gcode"), compact=False)
        self.assertEqual(cura.layer_count(), 3)
        self.assertEqual(cura.current_layer_map, {1: 0, 2: 1, 3: 2})
        self.assertEqual(cura.layer_elapsed_times, [120.0, 420.0, 900.0])
        orca = build_index_from_file(str(FIXTURES / "orca.gcode"), compact=False)
        self.assertEqual(orca.current_layer_map, {1: 0, 2: 1, 3: 2})
        prusa = build_index_from_file(str(FIXTURES / "prusa.gcode"), compact=False)
        self.assertEqual(prusa.layer_count(), 3)
        variable = build_index_from_file(str(FIXTURES / "variable_layers.gcode"), compact=False)
        self.assertEqual(variable.layer_elapsed_times, [10.0, 22.0, 45.0])

    def test_pause_missing_time_and_resume_fixtures_remain_indexable(self):
        paused = build_index_from_file(str(FIXTURES / "pause.gcode"), compact=False)
        self.assertEqual(paused.layer_count(), 3)
        self.assertEqual(paused.motion_count(1), 2)
        missing = build_index_from_file(str(FIXTURES / "missing_time.gcode"), compact=False)
        self.assertEqual(missing.layer_elapsed_times, [None, None])
        resumed = build_index_from_file(str(FIXTURES / "resume.gcode"), compact=False)
        self.assertEqual(resumed.current_layer_map, {1: 0, 2: 1, 3: 2, 4: 3})

    def test_eta_prefers_slicer_time_for_early_and_resumed_prints(self):
        model = load_monitor_model()
        early = model._estimate_remaining_seconds(3600, 0.02, 7 * 3600, True)
        self.assertAlmostEqual(early, 6 * 3600, delta=1)
        resumed = model._estimate_remaining_seconds(3 * 3600, 0.10, 7 * 3600, True)
        self.assertAlmostEqual(resumed, 4 * 3600, delta=1)
        waiting = model._estimate_remaining_seconds(120, 0.50, None, False)
        self.assertIsNone(waiting)

    def test_malformed_core_status_degrades_without_throwing(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        instance._monitoring_active = True
        instance._monitor_state = "Not connected"; instance._monitor_state_raw = ""
        instance._monitor_filename = ""; instance._monitor_message = ""; instance._monitor_progress = 0
        instance._monitor_progress_fraction = 0.0; instance._monitor_layer = "—"; instance._monitor_elapsed = "00:00:00"
        instance._monitor_eta = "—"; instance._monitor_finish = "—"; instance._monitor_speed = "100%"; instance._monitor_flow = "100%"
        instance._monitor_position = "—"; instance._print_duration = 0.0; instance._metadata_estimated_time = None
        instance._metadata_filename = ""; instance._metadata_lookup_complete = True; instance._power_devices_raw = []
        instance.updateMoonrakerStatus({"print_stats": "bad", "virtual_sdcard": [], "gcode_move": 7, "motion_report": None})
        self.assertEqual(instance.monitorPosition, "—")
        self.assertEqual(instance.monitorProgress, 0)

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        model = load_typed_model()
        self.assertEqual(model._parse_bed_mesh_status(None), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, 1], [2]], "mesh_min": [0, 0], "mesh_max": [1, 1]}), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, float("nan")], [1, 2]], "mesh_min": [0, 0], "mesh_max": [1, 1]}), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, 1], [1, 2]], "mesh_min": [2, 0], "mesh_max": [1, 1]}), {})
        stats = model._parse_mcu_last_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123")
        self.assertEqual(stats, {"mcu_awake": 0.02, "bytes_read": 123.0})

    def test_stale_monitor_request_generation_is_ignored(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        reply = DummyReply()
        instance._requests = {"aux": reply}
        instance._request_generation = 4
        called = []
        instance._finish_json_request("aux", reply, lambda *_args: called.append(True), 3)
        self.assertEqual(called, [])
        self.assertTrue(reply.deleted)
        self.assertNotIn("aux", instance._requests)

    def test_inactive_monitor_ignores_shared_follower_status(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        instance._monitoring_active = False
        instance.updateMoonrakerStatus({"print_stats": {"state": "printing"}})
        self.assertFalse(hasattr(instance, "_monitor_state_raw"))

    def test_monitor_lifecycle_deactivates_old_printer_and_invalidates_requests(self):
        self.assertIn("self._request_generation", MONITOR_SOURCE)
        self.assertIn("generation != self._request_generation", MONITOR_SOURCE)
        self.assertIn("def setMonitoringActive", MONITOR_SOURCE)
        self.assertIn("if not self._monitoring_active", MONITOR_SOURCE)
        self.assertIn("self._set_monitor_active(self._current, False)", OUTPUT_PLUGIN_SOURCE)
        self.assertIn("self._set_monitor_active(device, True)", OUTPUT_PLUGIN_SOURCE)

    def test_deferred_slider_labels_follow_thumb_but_commands_wait_for_release(self):
        self.assertGreaterEqual(DASHBOARD_SOURCE.count("live: false"), 9)
        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_SOURCE)
        self.assertNotIn("onMoved:", DASHBOARD_SOURCE)
        for slider in ("speedSlider", "flowSlider", "fanSlider", "ledSlider", "redSlider", "greenSlider", "blueSlider", "whiteSlider", "pwmSlider"):
            self.assertIn(f"root.sliderSelection({slider})", DASHBOARD_SOURCE)


if __name__ == "__main__":
    unittest.main()
''')

# Source-contract cleanup for the removed dead dashboard file.
source_contracts = ROOT / "tests" / "test_source_contracts.py"
text = source_contracts.read_text(encoding="utf-8")
text = text.replace('            "MoonrakerMonitorDashboard.qml",\n', '            "MoonrakerMonitorDashboard.qml",\n            "MoonrakerMonitorBedMesh.qml",\n')
source_contracts.write_text(text, encoding="utf-8")

# The active Monitor chain is BedMesh -> Dashboard -> Monitor; the old Enhanced
# dashboard was dead code and is intentionally gone.
monitor_upload = ROOT / "tests" / "test_monitor_upload_regressions.py"
text = monitor_upload.read_text(encoding="utf-8")
text = text.replace('    def test_enhanced_monitor_is_packaged_and_selected(self):\n', '    def test_active_monitor_chain_is_packaged_and_selected(self):\n')
text = text.replace('        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)\n        self.assertIn("Printer controls", DASHBOARD_QML)\n',
                    '        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)\n        self.assertIn("Printer controls", DASHBOARD_QML)\n        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())\n')
monitor_upload.write_text(text, encoding="utf-8")

print("Applied v3.0.0 release hardening pass")

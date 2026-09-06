from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def replace_all(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Missing {label} in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


source_contracts = ROOT / "tests" / "test_source_contracts.py"
replace_all(source_contracts, 'text: "Load print"', 'text: "Load current print"', "Preview load label contracts")
replace_all(source_contracts, 'self.assertIn(\'"MoonrakerMonitor.qml"\', OUTPUT_PLUGIN)', 'self.assertIn(\'"MoonrakerMonitorBedMesh.qml"\', OUTPUT_PLUGIN)', "active Monitor QML contract")

release_tests = ROOT / "tests" / "test_release_hardening.py"
replace_all(release_tests, 'instance._request_identity = ("http://old", "old-key")', 'instance._request_identity = ("http://old.invalid", "old-key")', "old request identity fixture")
replace_all(release_tests, 'url = "http://new"', 'url = "http://new.invalid"', "new request URL fixture")
replace_all(release_tests, 'api_key = "new-key"', 'api_key = str("new-key")', "non-literal API key fixture")
replace_all(release_tests, 'self.assertEqual(instance._request_identity, ("http://new", "new-key"))', 'self.assertEqual(instance._request_identity, ("http://new.invalid", "new-key"))', "new request identity assertion")

print("Applied release UX refactor test fixups")

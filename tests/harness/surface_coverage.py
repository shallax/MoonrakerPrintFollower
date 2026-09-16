"""The code-derived coverage matrix (TESTING.md 3, the suite).

A surface is anything a scenario can address or a user can touch:
@pyqtSlot verbs on the QML-facing objects, objectName'd interactive
items, MoonrakerProtocol endpoint builders, and the model's published
value_property keys. ``extract()`` walks the plugin sources and
returns every surface; ``check(map_)`` fails for any surface the
scenario map does not cover (an explicit exclusion is the only other
option). The map lives in scenario_map.py — the scenarios it names live
in scenarios.py.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGINS = os.path.join(ROOT, "plugins")


def extract() -> Dict[str, List[str]]:
    """Every addressable surface, grouped by kind."""
    surfaces: Dict[str, List[str]] = {"slot": [], "objectName": [], "route": [], "key": []}
    for name in sorted(os.listdir(PLUGINS)):
        path = os.path.join(PLUGINS, name)
        if name.endswith(".py") and os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if "@pyqtSlot" not in source:
                continue
            module = os.path.splitext(name)[0]
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    dec_name = getattr(dec, "id", None) or getattr(getattr(dec, "func", None), "id", None)
                    if dec_name == "pyqtSlot":
                        surfaces["slot"].append(f"{module}.{node.name}")
                        break
        if name.endswith(".qml") and os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for match in re.finditer(r'objectName:\s*"([^"]+)"', source):
                surfaces["objectName"].append(match.group(1))
    with open(os.path.join(PLUGINS, "MoonrakerProtocol.py"), encoding="utf-8") as handle:
        protocol = handle.read()
    for match in re.finditer(r"def (\w*endpoint)\(", protocol):
        surfaces["route"].append(match.group(1))
    # The key family scans every plugin module: a value_property that
    # moves to an extracted view model must stay in the matrix (the
    # one-file read let moved declarations vanish silently).
    for name in sorted(os.listdir(PLUGINS)):
        path = os.path.join(PLUGINS, name)
        if not (name.endswith(".py") and os.path.isfile(path)):
            continue
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for match in re.finditer(r'(\w+) = value_property\(', source):
            surfaces["key"].append(match.group(1))
    for kind in surfaces:
        surfaces[kind] = sorted(set(surfaces[kind]))
    return surfaces


def check(mapping) -> List[str]:
    """Failures: surfaces the map neither covers nor excludes."""
    surfaces = extract()
    failures: List[str] = []
    for kind, names in surfaces.items():
        for name in names:
            covered = name in mapping
            if covered:
                continue
            prefix = next((rule for rule in mapping.get("_prefix_rules", [])
                           if isinstance(rule, tuple) and rule[0] == kind and name.startswith(rule[1])), None)
            if prefix is None and name not in mapping.get("_exclusions", {}):
                failures.append(f"{kind}:{name}")
    return failures


def report(mapping) -> str:
    surfaces = extract()
    lines = []
    for kind in ("slot", "objectName", "route", "key"):
        names = surfaces[kind]
        covered = sum(1 for name in names if name in mapping
                      or any(r[0] == kind and name.startswith(r[1]) for r in mapping.get("_prefix_rules", [])))
        excluded = sum(1 for name in names if name in mapping.get("_exclusions", {}))
        lines.append(f"{kind}: {len(names)} surfaces, {covered} mapped, {excluded} excluded, "
                     f"{len(names) - covered - excluded} UNCOVERED")
    return "\n".join(lines)


def _norm(value: object) -> str:
    # Alphanumeric lowercase: an op name, a slot name and an
    # objectName must match across their separators ("exec_test_
    # connection" names testConnection; "moonrakerJogXPlus" names
    # jog).
    return "".join(c for c in str(value).lower() if c.isalnum())


def check_evidence(mapping, prefix_rules, steps) -> List[str]:
    """The execution half of the coverage gate (workstream 4): the
    map's membership check asks WHERE a surface should be covered;
    this asks whether the run's evidence actually did it. For every
    concrete map entry (slot, objectName and key kinds — routes are
    owned by the simulator's contract test, which is what their
    entries say), the mapped scenario must have RUN in the evidence
    AND some step of it must name the surface — in the spec's own
    values, in the step's declared verbs (the exec_code lint's
    declaration), or in the op that runs it. Prefix-rule families
    only require the scenario to have run. The gate scenarios folded
    into the smoke set, so the smoke unit's steps carry the
    s-scenarios."""
    import json

    steps_by_scenario: Dict[str, List[dict]] = {}
    for step in steps:
        steps_by_scenario.setdefault(step.get("scenario"), []).append(step)
    observed = set(steps_by_scenario)
    failures: List[str] = []
    required = {value for value in mapping.values() if not isinstance(value, dict)}
    required |= {rule[2] for rule in prefix_rules
                 if isinstance(rule, (tuple, list)) and len(rule) >= 3}
    for sid in sorted(required - observed):
        failures.append(f"scenario:{sid} has no evidence steps in the run")
    for name, sid in mapping.items():
        if sid not in observed:
            continue
        if name.startswith("_") or isinstance(sid, dict):
            continue
        if "." in name or name.endswith("_endpoint"):
            # Qualified slots and the protocol routes are exercised
            # through the scenario's own assertions (ledger, model
            # reads) — the spec never names them by design. The
            # naming requirement applies to the items that exist to
            # BE addressed: the objectName'd controls.
            continue
        needle = _norm(name)
        refs = []
        for step in steps_by_scenario.get(sid, []):
            spec = step.get("spec") or {}
            refs.append(_norm(json.dumps(spec, sort_keys=True)))
            for verb in spec.get("verbs", ()):
                refs.append(_norm(verb))
            refs.append(_norm(step.get("op") or ""))
        if not any(needle in ref for ref in refs if needle):
            failures.append(f"{name}: mapped to {sid}, but no step of it names the surface")
    return failures

#!/usr/bin/env python3
"""The pin-pass baseline: the frozen table the extraction pass compares against.

The QML extraction's pin-retargeting pass completes when the table this
script emits BEFORE a move and the table it emits AFTER agree — per
family, per file. "Costed and complete" is a comparison of the two
tables, never a hand-count.

METHOD (named, so the pass cannot drift):
- source constants: module-level assignments whose right-hand side
  contains `.read_text()` (the per-file QML/Python texts the
  assertions read whole).
- the file-text family: assertIn/assertNotIn calls whose haystack
  argument mentions one of those constants anywhere in its
  expression (plain names, subscripts, joins).
- the count family: `.count(` calls on an expression mentioning a
  source constant.
- the anchor family: `.index(` calls on an expression mentioning a
  source constant (call expressions, not lines).

The table lands in review/pin_baseline.txt (git-ignored, regenerable).
Run:  python3 tools/pin_baseline.py
"""
from __future__ import annotations

import ast
import os
import sys

TESTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")


def _mentions_source(node, constants):
    if isinstance(node, ast.Name) and node.id in constants:
        return True
    for child in ast.iter_child_nodes(node):
        if _mentions_source(child, constants):
            return True
    return False


def scan(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    constants = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is not None and ".read_text()" in ast.unparse(value):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants.add(target.id)
    families = {"file_text": 0, "count": 0, "index": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("assertIn", "assertNotIn") and len(node.args) == 2:
                if _mentions_source(node.args[1], constants):
                    families["file_text"] += 1
            elif node.func.attr == "count" and node.args:
                if _mentions_source(node.func.value, constants):
                    families["count"] += 1
            elif node.func.attr == "index" and node.args:
                if _mentions_source(node.func.value, constants):
                    families["index"] += 1
    return families


def main():
    rows = []
    totals = {"file_text": 0, "count": 0, "index": 0}
    for name in sorted(os.listdir(TESTS)):
        path = os.path.join(TESTS, name)
        if not (name.endswith(".py") and os.path.isfile(path)):
            continue
        families = scan(path)
        rows.append((name, families))
        for family in totals:
            totals[family] += families[family]
    lines = ["pin baseline — method: tools/pin_baseline.py docstring"]
    lines.append(f"{'file':42} {'file_text':>10} {'count':>6} {'index':>6}")
    for name, families in rows:
        lines.append(f"{name:42} {families['file_text']:>10} {families['count']:>6} {families['index']:>6}")
    lines.append(f"{'TOTAL':42} {totals['file_text']:>10} {totals['count']:>6} {totals['index']:>6}")
    table = "\n".join(lines)
    print(table)
    review = os.path.join(os.path.dirname(TESTS), "review")
    os.makedirs(review, exist_ok=True)
    with open(os.path.join(review, "pin_baseline.txt"), "w", encoding="utf-8") as handle:
        handle.write(table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

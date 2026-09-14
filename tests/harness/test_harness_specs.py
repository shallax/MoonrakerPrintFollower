"""Structural checks on the suite specs — the host-side guard the
probe-code-in-string-constants pattern lacked: a bad escape or a
typo'd op used to surface only inside the container, forty minutes
into a gate (the engineering panel's finding)."""

import ast
import os
import sys
import unittest

# The harness modules live beside this file; the host discovery
# starts elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scenarios as _scenarios
import runner as _runner


def _inline_code_values(spec):
    for step in spec.get("steps", ()):
        code = step.get("code")
        if isinstance(code, str):
            yield code


class HarnessSpecTests(unittest.TestCase):
    def test_spec_ids_are_unique(self):
        ids = [spec["id"] for spec in _scenarios.SCENARIOS]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        self.assertEqual(duplicates, [])

    def test_every_probe_constant_compiles(self):
        broken = []
        for name in dir(_scenarios):
            if name.isupper() and isinstance(getattr(_scenarios, name), str):
                try:
                    compile(getattr(_scenarios, name), f"<{name}>", "exec")
                except SyntaxError as exc:
                    broken.append((name, str(exc)))
        self.assertEqual(broken, [])

    def test_every_inline_code_step_compiles(self):
        broken = []
        for spec in _scenarios.SCENARIOS:
            for code in _inline_code_values(spec):
                try:
                    compile(code, f"<{spec['id']}>", "exec")
                except SyntaxError as exc:
                    broken.append((spec["id"], str(exc)[:80]))
        self.assertEqual(broken, [])

    def test_every_exec_code_step_declares_its_verbs(self):
        # The exec_code closure (the round-2 HIGH-1): inline code is
        # the hiding place for direct invocations — E_STOP_SEQUENCE
        # called printer.emergencyStopClick() invisibly. Every
        # exec_code step now declares what it invokes, and each
        # declaration must name something the code actually contains.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") != "exec_code":
                    continue
                verbs = step.get("verbs")
                if verbs is None:
                    offenders.append(f"{spec['id']}: exec_code step without verbs")
                    continue
                code = step.get("code") or ""
                # Template-based steps store the CONSTANT name as
                # code — resolve it so the verb is checked against
                # the code text, not the name.
                resolved = getattr(_scenarios, code, code) if code.isupper() else code
                for verb in verbs:
                    if verb not in resolved:
                        offenders.append(f"{spec['id']}: verbs declares {verb!r}, not in the code")
        self.assertEqual(offenders, [])

    def test_every_spec_op_exists_in_the_runner(self):
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        ops = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                left, right = node.test.left, node.test.comparators[0]
                if (isinstance(left, ast.Name) and left.id == "op"
                        and isinstance(right, ast.Constant) and isinstance(right.value, str)):
                    ops.add(right.value)
        missing = {}
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                op = step.get("op")
                if op and op not in ops:
                    missing.setdefault(op, []).append(spec["id"])
        self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main()

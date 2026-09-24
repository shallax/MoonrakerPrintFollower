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
    def test_ui_test_holds_one_run_per_container(self):
        # The 2026-09-16 census loss: two ui_test runs sharing one
        # container restage the workdir and kill each other's
        # simulator mid-scenario. The script must carry the
        # per-container lock so a second run refuses up front.
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(".ui_test-lock-$CONTAINER", source)
        self.assertIn("one run per container at a time", source)
        self.assertIn("trap 'rm -rf \"$LOCK_DIR\"' EXIT", source)

    def test_every_run_scans_the_cura_log_for_plugin_noise(self):
        # The 2026-09-16 log-scan ruling: every run reads the Cura log
        # and fails on plugin-originated noise regardless of scenario
        # — the polish loop slipped through because no scenario
        # asserted it. The scan, its match set and the runner-verdict
        # propagation must all be pinned.
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("scan_cura_log", source)
        self.assertIn("grep -nE 'MoonrakerPrintFollower|/Moonraker[A-Za-z]+\\.qml'", source)
        self.assertIn("CURA LOG NOISE (the log-scan ruling)", source)
        self.assertIn("|| RUNNER_RC=$?", source)
        self.assertIn('if [ "${RUNNER_RC:-0}" -ne 0 ]; then', source)

    def test_no_resize_crosses_the_application_window_floor(self):
        # The application's own floor is the bottom: a target below it
        # is a size Cura refuses, a user cannot drag there, so the step
        # would rest on a geometry nobody can reach (and on Windows the
        # refused request silently measured a different size than the
        # spec asked for). The "min" token asks the driver for the
        # platform's own floor; a literal has to clear the largest
        # floor, since one spec runs on every platform.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") != "resize_window":
                    continue
                if "force" in step:
                    offenders.append(f"{spec['id']}: forces past the window floor")
                for axis, floor in (("w", _scenarios.WINDOW_FLOOR_W),
                                    ("h", _scenarios.WINDOW_FLOOR_H)):
                    value = step.get(axis)
                    if value == "min":
                        continue
                    if not isinstance(value, int) or value < floor:
                        offenders.append(
                            f"{spec['id']}: {axis}={value!r} below the floor {floor}")
        self.assertEqual(offenders, [])

    def test_the_driver_cannot_relax_the_window_floor(self):
        # The floor may not be dropped to build a geometry a user
        # cannot reach: the driver's resize verb takes the minimum from
        # the live window and refuses a target below it, and nothing in
        # the harness overrides the window's own minimum.
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("setMinimumSize", source)
        self.assertIn("below the window minimum", source)

    def test_a_frame_is_captured_only_when_cura_holds_the_display(self):
        # The evidence records the DISPLAY: a step that hands it to
        # another process — a link press opening a browser — makes
        # every frame after it evidence of that process instead (the
        # owner watched Edge take the screen mid-suite-probe). The
        # guard must run BEFORE the capture, its verdict must be able
        # to fail the step, and the driver's answer must read the OS
        # where the OS can be asked rather than assuming Qt's word.
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"cmd": "foreground"', source)
        self.assertIn("def foreground_guard(", source)
        self.assertIn("def _foreground_verdict(", source)
        guard = source.index("foreground = foreground_guard()")
        capture = source.index("capture = shot(name)", guard)
        self.assertLess(guard, capture, "the guard must run before the capture")
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            driver_source = handle.read()
        self.assertIn('if cmd == "foreground":', driver_source)
        self.assertIn("GetWindowThreadProcessId", driver_source)
        self.assertIn("def _raise_main_window(", driver_source)
        # A platform with no foreground authority must be recorded,
        # never failed: the calibration is the observed activation.
        self.assertIn("_FOREGROUND_SEEN", driver_source)
        self.assertIn('"none"', driver_source)

    def test_a_press_aims_at_the_body_as_drawn(self):
        # A rotated control (the collapsed rails run at -90°) must be
        # aimed at its own centre. Adding w/2, h/2 to the mapped ORIGIN
        # mixed item axes into scene axes: the aim landed beside the
        # rail, so every press at one hit empty space and every rail
        # rect read "NOT in view" while the rail rendered.
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("def _aim_point(", source)
        self.assertNotIn("scene.x() + target.width() / 2", source)
        self.assertNotIn("scene.x() + item.width() / 2", source)
        self.assertIn("item.mapToScene(QPointF(float(item.width()) / 2.0",
                      source)

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

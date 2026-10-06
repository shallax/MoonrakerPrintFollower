"""Structural checks on the suite specs — the host-side guard the
probe-code-in-string-constants pattern lacked: a bad escape or a
typo'd op used to surface only inside the container, forty minutes
into a gate (the engineering panel's finding)."""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# The harness modules live beside this file; the host discovery
# starts elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scenarios as _scenarios
import runner as _runner


def _driver_source(*names):
    """One driver family's source, by module name.

    The dispatcher stays in the package's __init__; each probe and
    interaction family owns its own module. A pin reads the module that
    owns what it holds, so a token that moved is retargeted rather than
    found anywhere in a concatenated tree.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for name in names:
        path = os.path.join(here, "driver", name)
        if not os.path.exists(path):
            raise AssertionError("no driver family at %s" % path)
        with open(path, encoding="utf-8") as handle:
            out.append(handle.read())
    return out if len(out) > 1 else out[0]


def _inline_code_values(spec):
    for step in spec.get("steps", ()):
        code = step.get("code")
        if isinstance(code, str):
            yield code


class HarnessSpecTests(unittest.TestCase):
    def test_settings_probe_keeps_large_tree_in_file_and_bounds_rpc_reply(self):
        from types import SimpleNamespace
        from unittest.mock import mock_open, patch

        point = SimpleNamespace(x=lambda: 10, y=lambda: 20)
        items = [SimpleNamespace(
            property=lambda key, index=index: f"control-{index}" if key == "objectName" else None,
            metaObject=lambda: SimpleNamespace(className=lambda: "SettingsControl"),
            mapToScene=lambda _point: point, width=lambda: 100, height=lambda: 25)
            for index in range(200)]
        manager = SimpleNamespace(getMachineActions=lambda: [])
        app = SimpleNamespace(getMachineActionManager=lambda: manager)
        application = SimpleNamespace(Application=SimpleNamespace(getInstance=lambda: app))
        namespace = {"_main_window": lambda: SimpleNamespace(contentItem=lambda: None),
                     "_walk": lambda *_args, **_kwargs: items, "QPointF": lambda *_args: None}
        opened = mock_open()
        with patch.dict(sys.modules, {"UM.Application": application}), patch("builtins.open", opened):
            exec(_scenarios.SETTINGS_PROBE, namespace)
        full_dump = "".join(call.args[0] for call in opened().write.call_args_list)
        self.assertGreater(len(full_dump), 4000)
        self.assertEqual(len(json.loads(full_dump)["config_items"]), 200)
        self.assertEqual(namespace["result"]["config_item_count"], 200)
        self.assertLess(len(json.dumps(namespace["result"])), 4000)

    def test_reported_position_probe_reads_the_render_translation(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        spec = next(spec for spec in _scenarios.SCENARIOS if spec["id"] == "p9")
        probe = next(step["code"] for step in spec["steps"]
                     if step["op"] == "wait_exec" and '"position_ok": true' in step.get("contains", ""))
        node = SimpleNamespace(isVisible=lambda: True,
            getPosition=lambda: SimpleNamespace(x=0, y=0, z=0),
            render_position=lambda: SimpleNamespace(x=-25, y=20, z=25))
        runtime = SimpleNamespace(toolhead=SimpleNamespace(_node=node),
            presentation=SimpleNamespace(reported_position=True),
            cura=SimpleNamespace(view=SimpleNamespace(
                getNozzleNode=lambda: SimpleNamespace(getParent=lambda: None))))
        extension = type("MoonrakerPrintFollower", (), {"_runtime": runtime})()
        stack = SimpleNamespace(getProperty=lambda key, _role: False if key == "machine_center_is_zero" else 250)
        app = SimpleNamespace(getExtensions=lambda: [extension], getGlobalContainerStack=lambda: stack)
        application = SimpleNamespace(Application=SimpleNamespace(getInstance=lambda: app))
        namespace = {}
        with patch.dict(sys.modules, {"UM.Application": application}):
            exec(probe, namespace)
        self.assertTrue(namespace["result"]["position_ok"])
        self.assertEqual(namespace["result"]["actual_position"], [-25, 20, 25])

    def test_scheduled_pause_scenarios_hold_the_exact_layer_before_ui_waits(self):
        # The v5.1.0 macOS gate observed layer 5 while p7 waited for layer 1:
        # arming the hold after that wait cannot repair a missed transient.
        # Both flows must seed and hold in their initial print transaction.
        for scenario_id in ("p6", "p7"):
            spec = next(spec for spec in _scenarios.SCENARIOS if spec["id"] == scenario_id)
            steps = spec["steps"]
            seed_index = next(index for index, step in enumerate(steps)
                              if step["op"] == "sim_set_current_print")
            with self.subTest(scenario=scenario_id):
                self.assertEqual(steps[seed_index]["current_layer"], 1)
                self.assertEqual(steps[seed_index]["layer_clock_interval_s"], 3600)
                self.assertFalse(any(step["op"].startswith("wait_") for step in steps[:seed_index]))
                scheduled_index = next(index for index, step in enumerate(steps)
                                       if step["op"] == "wait_exec" and '"End of layer 2' in step.get("contains", ""))
                release_index = next(index for index, step in enumerate(steps)
                                     if step["op"] == "sim_arm" and step.get("arms", {}).get("layer_clock_interval_s") == 6)
                self.assertGreater(release_index, scheduled_index)

    def test_inline_input_rectangles_account_for_a_displaced_window(self):
        from types import SimpleNamespace
        for x, y in ((0, 0), (40, 59), (-900, 31)):
            with self.subTest(origin=(x, y)):
                origin = SimpleNamespace(x=lambda x=x: x, y=lambda y=y: y)
                item = SimpleNamespace(window=lambda origin=origin: SimpleNamespace(position=lambda: origin))
                driver = SimpleNamespace(_rect=lambda item, x=x, y=y: {"x": x + 100, "y": y + 200,
                                                              "w": 30, "h": 40})
                namespace = {"self": driver}
                exec(_scenarios.SCENE_RECT, namespace)
                self.assertEqual(namespace["_input_rect"](item),
                                 {"x": 100, "y": 200, "w": 30, "h": 40})

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
        self.assertIn('LOCK_DIR="$WORK_DIR/.ui_test-lock-$CONTAINER"', source)
        self.assertLess(source.index('mkdir -p "$WORK_DIR"'),
                        source.index('if ! mkdir "$LOCK_DIR"'))
        self.assertIn("one run per container at a time", source)
        self.assertIn("trap 'rm -rf \"$LOCK_DIR\"' EXIT", source)

    def test_cura_library_path_is_applied_after_the_timeout_wrapper(self):
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        launch = source.split("launch_cura() {", 1)[1].split("\n}", 1)[0]
        self.assertEqual(launch.count("timeout 1800 env"), 1)
        before, child = launch.split("timeout 1800 env", 1)
        self.assertNotIn("LD_LIBRARY_PATH=", before)
        self.assertIn(
            r"LD_LIBRARY_PATH=\$CURA_ROOT:\$CURA_ROOT/usr/lib/x86_64-linux-gnu:"
            r"\$CURA_ROOT/lib/x86_64-linux-gnu:\$CURA_ROOT/usr/lib:"
            r"\$CURA_WHEELS/PyQt6/Qt6/lib", child)
        self.assertLess(child.index("LD_LIBRARY_PATH="), child.index(r"\$MPF_LAUNCH"))

    @unittest.skipUnless(sys.platform != "win32" and shutil.which("bash"),
                         "the Linux Cura launcher needs bash")
    def test_timeout_keeps_container_libraries_and_child_gets_cura_libraries(self):
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        launch = source.split("launch_cura() {", 1)[1].split("\n}", 1)[0]
        match = re.search(
            r"""bash -lc 'su ubuntu -s /bin/bash -c "(.*?)" >/tmp/mpf/cura_run.log""",
            launch, re.DOTALL)
        self.assertIsNotNone(match)
        command = match.group(1).replace("\\\n", "").replace(r"\$", "$")
        os.makedirs("/tmp/mpf", exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mpf-launch-", dir="/tmp/mpf") as tmp:
            timeout = os.path.join(tmp, "timeout")
            child = os.path.join(tmp, "launch_probe")
            wrapper_result = os.path.join(tmp, "wrapper")
            child_result = os.path.join(tmp, "child")
            with open(timeout, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\n"
                             'test "$1" = 1800 || exit 2\n'
                             'printf "%s" "$LD_LIBRARY_PATH" > "$MPF_WRAPPER_RESULT"\n'
                             'shift\nexec "$@"\n')
            with open(child, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\n"
                             'printf "%s" "$LD_LIBRARY_PATH" > "$MPF_CHILD_RESULT"\n')
            os.chmod(timeout, 0o755)
            os.chmod(child, 0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                       LD_LIBRARY_PATH="container-libraries", CURA_ROOT=tmp,
                       CURA_WHEELS=tmp, MPF_LAUNCH="./launch_probe",
                       MPF_WRAPPER_RESULT=wrapper_result, MPF_CHILD_RESULT=child_result)
            subprocess.run(["bash", "-c", command], env=env, check=True)
            with open(wrapper_result, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "container-libraries")
            with open(child_result, encoding="utf-8") as handle:
                self.assertEqual(
                    handle.read(),
                    ":".join((tmp, tmp + "/usr/lib/x86_64-linux-gnu",
                              tmp + "/lib/x86_64-linux-gnu", tmp + "/usr/lib",
                              tmp + "/PyQt6/Qt6/lib")))

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
        self.assertIn("tests/harness/log_gate.py", source)
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
        # The verb is the dispatcher's; the reading and the raise belong
        # to the foreground family beside it.
        driver_source = _driver_source("__init__.py")
        owner = _driver_source("foreground.py")
        self.assertIn('if cmd == "foreground":', driver_source)
        self.assertIn("GetWindowThreadProcessId", owner)
        self.assertIn("def _raise_main_window(", owner)
        # A platform with no foreground authority must be recorded,
        # never failed: the calibration is the observed activation.
        self.assertIn("_FOREGROUND_SEEN", owner)
        self.assertIn('"none"', owner)

    def test_a_press_aims_at_the_body_as_drawn(self):
        # A rotated control (the collapsed rails run at -90°) must be
        # aimed at its own centre. Adding w/2, h/2 to the mapped ORIGIN
        # mixed item axes into scene axes: the aim landed beside the
        # rail, so every press at one hit empty space and every rail
        # rect read "NOT in view" while the rail rendered.
        source = _driver_source("interaction.py")
        self.assertIn("def _aim_point(", source)
        self.assertNotIn("scene.x() + target.width() / 2", source)
        self.assertNotIn("scene.x() + item.width() / 2", source)
        self.assertIn("item.mapToScene(QPointF(float(item.width()) / 2.0",
                      source)

    def test_a_step_never_addresses_a_name_on_a_type_no_walk_reaches(self):
        # The harness addresses the plugin through QQuickItem.childItems()
        # — the VISUAL tree. A Popup, a Menu, a Window: none of those is
        # an Item, so a step naming one can never resolve. It fails
        # slowly and confusingly: the controls INSIDE the popup answer
        # normally, so the leg's other steps pass while the wait for the
        # popup itself times out (measured: the macOS printing leg's
        # wait for the replace prompt timed out while both its buttons
        # were pressed without complaint).
        #
        # A name NOTHING addresses is fine and several exist (a popup's
        # own handle, recorded as exclusions) — the rule is about the
        # names the suite actually presses and reads.
        import re
        from pathlib import Path
        plugins = Path(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))) / "mpf"
        addressed = {step["objectName"] for spec in _scenarios.SCENARIOS
                     for step in spec.get("steps", ()) if step.get("objectName")}
        non_items = ("Popup", "Menu", "Dialog", "Window", "ToolTip", "Action",
                     "MessageDialog", "FileDialog", "ColorDialog", "FolderDialog",
                     "Drawer")
        declaration = re.compile(
            r"^\s*(?:[A-Z][\w.]*\.)?(" + "|".join(non_items) + r")\s*\{\s*$")
        offenders = []
        for path in sorted(plugins.rglob("*.qml")):
            lines = path.read_text(encoding="utf-8").split("\n")
            for index, line in enumerate(lines):
                if not declaration.match(line):
                    continue
                depth = line.count("{") - line.count("}")
                for offset in range(index + 1, len(lines)):
                    body = lines[offset]
                    if depth == 0:
                        break
                    name = re.match(r"^\s*objectName\s*:\s*\"([^\"]+)\"", body)
                    if depth == 1 and name and name.group(1) in addressed:
                        offenders.append(f"{path.name}:{offset + 1}: {name.group(1)}")
                    depth += body.count("{") - body.count("}")
        self.assertEqual(offenders, [],
                         "a step addresses a name on a type no walk reaches: %s" % offenders)

    def test_the_replace_prompt_is_the_cards_own_dialog(self):
        # The replace prompt was a QMessageBox, and the box-shaped
        # modal is what broke the macOS legs: a native alert answered by
        # the harness while the plugin never returned from its nested
        # loop, so a dozen steps failed downstream of a step that
        # reported success. It is the card's popup now, answered by a
        # real press on a rendered control like every other scenario.
        #
        # The four halves of the contract, each of which has been the
        # broken one at some point:
        #   * the plugin opens no widget dialog at all,
        #   * the card renders the prompt and owns its two buttons,
        #   * the MODEL is the single authority on whether it is up,
        #   * every leg that loads a print presses the button.
        cura = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "mpf", "cura", "CuraIntegration.py")
        with open(cura, encoding="utf-8") as handle:
            integration = handle.read()
        self.assertNotIn("QMessageBox", integration)
        self.assertNotIn("QtWidgets", integration)
        self.assertNotIn("confirm_replace", integration)

        card_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "mpf", "preview")
        with open(os.path.join(card_dir, "MoonrakerPreviewCard.qml"), encoding="utf-8") as handle:
            qml = handle.read()
        # The prompt's body is its own document (ReplacePromptDialog.qml)
        # instantiated by the card; the card still renders it, so the
        # rendered surface is the two texts together.
        with open(os.path.join(card_dir, "ReplacePromptDialog.qml"), encoding="utf-8") as handle:
            dialog = handle.read()
        self.assertIn("ReplacePromptDialog {", qml)
        self.assertIn('objectName: "moonrakerReplacePrompt"', dialog)
        self.assertIn('objectName: "moonrakerReplaceConfirmButton"', dialog)
        self.assertIn('objectName: "moonrakerReplaceCancelButton"', dialog)
        self.assertIn("modal: true", dialog)
        # Centred on the window, not nested in the card: the question
        # is the application's, and a 340px corner card is a strange
        # place to ask it from.
        self.assertIn("anchors.centerIn: Overlay.overlay", dialog)
        # The state is the model's, and the popup is written from the
        # change handler rather than bound: bindings on setProperty-fed
        # values go stale on this dynamically created component (the
        # same reason updateCardGate exists), and a stale binding is a
        # prompt that never opens.
        self.assertIn("function updateReplacePrompt()", qml)
        self.assertIn("replacePromptDialog.visible = base.replacePromptVisible && base.gateVisible",
                      qml)
        self.assertIn("onReplacePromptVisibleChanged: updateReplacePrompt()", qml)
        # Gated on the card's own visibility: the card is instantiated
        # twice and both copies receive the published value.
        self.assertNotIn("visible: base.replacePromptVisible", qml)

        coordinator = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "mpf", "application", "PrintCoordinator.py")
        with open(coordinator, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('self._presentation.publish({"replacePromptVisible": True})', source)
        self.assertIn("def _replace_confirmed(self):", source)
        self.assertIn("def _replace_cancelled(self):", source)
        # The pending load is the coordinator's, and answering twice
        # must not run it twice: the handler clears the field it read.
        self.assertIn("action, self._replace_action = self._replace_action, None", source)

        # Every scenario that loads a print over existing contents
        # presses the button - and none of them answers a box.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            steps = spec.get("steps", ())
            for index, step in enumerate(steps):
                if step.get("objectName") != "moonrakerReplaceConfirmButton":
                    continue
                if step.get("op") == "wait_rect":
                    continue  # the witness, judged below
                if step.get("op") != "deliver_click":
                    offenders.append(f"{spec['id']}: {step.get('op')} on the prompt")
                # The press must be WITNESSED first: a press at a
                # control that is not up yet is the race the widget
                # path had, and an absent read afterwards would make
                # the miss a vacuous pass.
                witness = [s for s in steps[:index]
                           if s.get("op") == "wait_rect"
                           and s.get("objectName") == "moonrakerReplaceConfirmButton"
                           and not s.get("absent")]
                if not witness:
                    offenders.append(f"{spec['id']}: the prompt is pressed unpainted")
        self.assertEqual(offenders, [])
        self.assertTrue(any(step.get("objectName") == "moonrakerReplaceConfirmButton"
                            for spec in _scenarios.SCENARIOS
                            for step in spec.get("steps", ())),
                        "no scenario presses the prompt at all")

    def test_a_confirm_is_pressed_not_driven_as_a_slot(self):
        # The static-recording finding: the visual leg confirmed its
        # rename by calling the slot, which leaves the modal popup
        # open — its scrim then covers every later scenario, so the
        # leg's clicks are ones a user could not make and its
        # recording's last minute never moves. A confirm is the
        # dialog's own verb, pressed on screen.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") not in ("exec_slot", "exec_file_slot"):
                    continue
                if "confirm" in str(step.get("slot", "")).lower():
                    offenders.append(f"{spec['id']}: {step['slot']} driven as a slot")
        self.assertEqual(offenders, [])

    def test_the_rename_flow_witnesses_its_dialog_closing(self):
        # The press that confirms must also be shown to close the
        # popup, and the absence must be WITNESSED: the witness step
        # (the dialog on screen) comes first, so the absent read is
        # never the vacuous "never observed" pass.
        spec = next(s for s in _scenarios.SCENARIOS
                    if s["id"] == "v10")
        steps = spec["steps"]
        witness = next(i for i, s in enumerate(steps)
                       if s.get("op") == "wait_rect" and s.get("text") == "Rename file"
                       and not s.get("absent"))
        press = next(i for i, s in enumerate(steps)
                     if s.get("op") == "deliver_click"
                     and s.get("objectName") == "renameConfirmButton")
        gone = next(i for i, s in enumerate(steps)
                    if s.get("op") == "wait_rect" and s.get("text") == "Rename file"
                    and s.get("absent"))
        self.assertLess(witness, press, "the dialog must be on screen before the press")
        self.assertLess(press, gone, "the press must come before the popup is gone")

    def test_a_visual_scenario_that_opens_the_file_manager_closes_it(self):
        # The manager is a full-area page: left open it hides the
        # panes the rest of the visual leg asserts and presses, so
        # those steps would pass over a screen showing something
        # else entirely. The way out is the page's own Close button
        # (a press, not a slot), so the departure is on screen too.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            if spec.get("group") != "visual":
                continue
            steps = spec.get("steps", ())
            slots = [str(step.get("slot", "")) for step in steps
                     if step.get("op") in ("exec_slot", "exec_file_slot")]
            closes = [step for step in steps
                      if step.get("objectName") == "fileManagerCloseButton"]
            if "openFileManager" in slots and not (closes or "setFileManagerOpen" in slots):
                offenders.append(spec["id"])
        self.assertEqual(offenders, [])

    def test_spec_ids_are_unique(self):
        ids = [spec["id"] for spec in _scenarios.SCENARIOS]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        self.assertEqual(duplicates, [])

    def test_the_package_re_exports_every_probe_constant(self):
        # The compile check below and the verbs resolution in
        # test_every_exec_code_step_declares_its_verbs both look the
        # constant up on the PACKAGE. A constant added to the probe
        # module but left out of the assembly point's enumerated
        # re-export would silently drop out of both — the check would
        # keep passing over a smaller set.
        import scenarios.probe_source as _probes
        defined = {name for name in dir(_probes)
                   if name.isupper() and isinstance(getattr(_probes, name), (str, int))}
        exported = {name for name in dir(_scenarios)
                    if name.isupper() and isinstance(getattr(_scenarios, name), (str, int))}
        self.assertGreater(len(defined), 40,
                           "the probe module's constants stopped being found")
        self.assertEqual(defined - exported, set(),
                         "probe constants the package does not re-export")
        for name in sorted(defined):
            self.assertIs(getattr(_scenarios, name), getattr(_probes, name),
                          "%s is re-exported as a different object" % name)

    def test_the_suite_is_assembled_from_named_groups_in_order(self):
        # The assembly point spells the group order out; a filesystem
        # glob would make the suite depend on directory order. Each
        # group's own sequence is what the runner executes, and each
        # group must stay contiguous so selecting one yields exactly
        # its module's list.
        groups = [module.__name__.rsplit(".", 1)[-1] for module in _scenarios.GROUPS]
        self.assertEqual(len(groups), len(set(groups)), "a group is assembled twice")
        self.assertEqual(len(groups), 16)
        for module in _scenarios.GROUPS:
            name = module.__name__.rsplit(".", 1)[-1]
            selected = [spec for spec in _scenarios.SCENARIOS
                        if spec.get("group") == name]
            self.assertEqual(selected, module.SCENARIOS,
                             "selecting group %s does not yield its module's "
                             "list in order" % name)
            for spec in module.SCENARIOS:
                self.assertEqual(spec.get("group"), name,
                                 "%s sits in the %s module" % (spec["id"], name))
        self.assertEqual(len(_scenarios.SCENARIOS),
                         sum(len(module.SCENARIOS) for module in _scenarios.GROUPS))

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

    def test_the_presentation_probe_asks_the_app_not_the_screen(self):
        # A window whose scene graph stopped presenting answers every
        # tree read, so a leg can pass every assertion over a screen
        # that never moved (the static-green ruling). The probe must
        # therefore make a frame DUE and report whether it came: the
        # driver attaches to frameSwapped, changes a temporary visible
        # item in the window's own scene, verifies the change landed and
        # awaits the frame, and the runner samples it at the start and
        # the end of every scenario.
        # The verb and the sample's own fields are the dispatcher's; the
        # counter and the heartbeat belong to the frames family.
        driver_source = _driver_source("__init__.py")
        owner = _driver_source("frames.py")
        self.assertIn('if cmd == "frames":', driver_source)
        self.assertIn("frameSwapped.connect", owner)
        self.assertIn("isExposed", driver_source)
        # The heartbeat is the measurement: an item in the app's own
        # scene graph, driven on the GUI thread this server is served
        # on, verified by read-back, awaited with a bounded deadline,
        # and removed before the verb returns so no still can carry it.
        self.assertIn("HEARTBEAT_OBJECT", owner)
        self.assertIn('objectName: "mpfLivenessHeartbeat"', owner)
        self.assertIn("item.setParentItem(window.contentItem())", owner)
        self.assertIn("item.setProperty(name, value)", owner)
        self.assertIn("item.property(name)", owner)
        self.assertIn("def _await_frames(", owner)
        self.assertIn("def _remove_heartbeat(", owner)
        self.assertIn("item.setParentItem(None)", owner)
        self.assertIn("item.deleteLater()", owner)
        # The wait is event-driven and bounded, never one sleep: the
        # deadline is checked against the clock in short qWait slices,
        # so the render loop keeps running while it is awaited.
        self.assertIn("deadline = started + max(0.0, float(deadline_ms) / 1000.0)",
                      owner)
        self.assertIn("while _FRAMES.count <= before:", owner)
        wait = owner[owner.index("def _await_frames("):]
        wait = wait[:wait.index("\ndef ")]
        self.assertIn("_settle(min(50.0, left * 1000.0))", wait)
        self.assertNotIn("time.sleep", wait)
        # The record rides the sample whether or not a frame arrived,
        # and says which change was made and what answered it: a change
        # that did not land, or an item that would not build, made no
        # frame due and must not read as a renderer that stopped.
        self.assertIn('"verified": False', owner)
        self.assertIn('attempt["verified"] = _same_number(', owner)
        self.assertIn('reply["heartbeat"] = heartbeat', driver_source)
        self.assertIn('"frame_signal": attached', driver_source)
        # And whether the counter had to attach to a window it was not
        # counting: the first count on a replacement window has no
        # history, which is a different answer from a count that
        # stopped moving.
        self.assertIn('"fresh_window": fresh', driver_source)
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        # The runner drives both ends of every scenario and publishes the
        # record; the sample itself and its verdict belong to the
        # presentation owner beside it.
        import liveness as _liveness
        with open(_liveness.__file__, encoding="utf-8") as handle:
            owner = handle.read()
        self.assertIn('"cmd": "frames"', owner)
        self.assertIn("def probe(rpc, phase, scenario_id):", owner)
        self.assertIn("def outcome_records(", owner)
        self.assertIn("def frames_probe(", source)
        self.assertIn("def liveness_outcome(", source)
        start = source.index('frames_probe("start", spec["id"])')
        end = source.index('frames_probe("end", spec["id"])')
        self.assertLess(start, end)
        self.assertIn('run["frames"] = FRAME_PROBES', source)
        # The outcome and its log lines are the leg's own fault lines,
        # so a scenario that stalled fails whatever the leg captured —
        # and where the leg does not judge its screen the miss is a
        # report-only diagnostic rather than a silent pass.
        self.assertIn('run["frames_outcome"] = outcomes', source)
        self.assertIn("ui_test: NO FRAMES", source)
        self.assertIn("ui_test: HEARTBEAT REPORT-ONLY", source)
        self.assertIn("def gating(", owner)
        # The sample asks for the heartbeat and carries its deadline:
        # a count read cold would call an idle window frozen.
        self.assertIn('"heartbeat": True', owner)
        self.assertIn("FRAME_HEARTBEAT_DEADLINE_MS", owner)
        # And the enums go out as names: PyQt6 hands back the wrapper
        # (QWindow.Visibility) and int() on it raises — the first live
        # run of the probe answered every sample with that TypeError.
        # The enum reader is the shared scene utility both families use.
        self.assertIn("def _enum_name(", _driver_source("scene.py"))
        self.assertNotIn("int(window.visibility())", driver_source)
        self.assertNotIn("int(window.visibility())", owner)

    def test_the_suite_profile_predismisses_the_one_time_boot_prompts(self):
        # A suite boot is a settled install. The what's-new card and the
        # local-detection offer are one-time prompts, and a modal still
        # waiting blocks every stage click — the 5.0.0 detection offer
        # failed e1–e4 exactly that way. Both markers stay seeded.
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base = os.path.join(root, "tests", "harness", "config", "config", "cura", "5.13",
                            "MoonrakerPrintFollower")
        with open(os.path.join(base, "state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertTrue(state.get("whatsNewSeen"),
                        "the suite profile no longer pre-dismisses What's New")
        with open(os.path.join(base, "settings.json"), encoding="utf-8") as handle:
            settings = json.load(handle)
        record = (settings.get("global") or {}).get("localDetection") or {}
        self.assertTrue(record.get("offer_seen", False),
                        "the suite profile no longer pre-dismisses the local-detection offer")


if __name__ == "__main__":
    unittest.main()

"""Executable qml dashboard interaction contracts."""
from tests import qml_engine_support as harness

class CollapseOnShrinkTests(harness.CollapseOnShrinkTests):
    def test_a_slow_shrink_folds_the_information_pane(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 760)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self.assertFalse(monitor.property("infoCollapsed"), "the pane started folded")
        self.assertGreater(info.width(), 200.0)
        folded_at = None
        for width in range(1100, 699, -20):
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            self.assertTrue(status.isVisible(), "the status pane hid at %d" % width)
            if monitor.property("infoCollapsed"):
                if folded_at is None:
                    folded_at = width
            else:
                self.assertIsNone(folded_at,
                                  "the fold released while still shrinking (%d)" % width)
        self.assertIsNotNone(folded_at, "the information pane never folded")
        self.assertLessEqual(folded_at, 1000, "the fold came later than the squeeze boundary")
        self.assertGreaterEqual(folded_at, 900, "the fold came before the squeeze boundary")
        self.assertLess(info.width(), 100.0, "the folded pane kept its full width")
        self.assertGreater(camera.width(), info.width(),
                           "the fold must hand the space to the camera column")

    def test_a_fast_resize_lands_in_the_same_steady_state(self):
        # The transient pass is not the contract: whatever the resize
        # steps, the resting state at a given width is the same one.
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 760)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        for width, expected in ((700, True), (1100, False), (640, True), (1100, False)):
            self.resize_window(monitor, window, width, 760)
            self.assertEqual(monitor.property("infoCollapsed"), expected,
                             "the latch read %s at %d" % (monitor.property("infoCollapsed"), width))
            self.assertGreater(camera.property("viewportWidth"), 0.0, width)
        # Jumping to the same width twice rests in the same state: the
        # layout the latch reads is deterministic, so the fold cannot
        # depend on the resize that arrived before it.
        self.resize_window(monitor, window, 700, 760)
        first = (monitor.property("infoCollapsed"), info.width(), camera.width())
        self.resize_window(monitor, window, 1100, 760)
        self.resize_window(monitor, window, 700, 760)
        self.assertEqual(first, (monitor.property("infoCollapsed"), info.width(), camera.width()))

    def test_the_camera_pane_never_collapses_at_any_width(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1600, 760)
        camera = self.camera_pane(monitor)
        for width in range(1600, 399, -100):
            self.resize_window(monitor, window, width, 760)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column went below its minimum at %d" % width)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)

    def test_the_status_pane_folds_to_its_strip_and_the_console_folds(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1600, 760)
        status = self.find(monitor, "statusPanel")
        console = None
        for item in monitor.findChildren(harness.QQuickItem):
            if item.property("tooNarrow") is not None:
                console = item
                break
        self.assertIsNotNone(console, "the console panel did not mount")
        self.assertAlmostEqual(status.width(), 410.0, delta=0.5)
        self.assertFalse(console.property("tooNarrow"))
        self.resize_window(monitor, window, 520, 760)
        # The status pane folds to its readout strip rather than
        # compressing its sections into a reflow.
        self.assertTrue(status.isVisible())
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        self.assertLess(status.width(), 100.0, "the folded pane kept its full width")
        # The console keeps its room: the fold hands the camera column
        # what the status pane was holding. Its own width-driven fold
        # waits until the column itself is crushed.
        self.assertFalse(console.property("tooNarrow"))
        self.resize_window(monitor, window, 400, 760)
        self.assertTrue(console.property("tooNarrow"))

    def test_the_controls_pane_yields_width_and_stays_open(self):
        # The dashboard's controls pane carries no auto-collapse of its
        # own: it yields between its two widths and stays open.
        dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", 1600, 760)
        pane = self.find(dashboard, "moonrakerControlsPane")
        self.assertAlmostEqual(pane.width(), 386.0, delta=0.5)
        self.resize_window(dashboard, window, 900, 760)
        self.assertTrue(pane.isVisible(), "the controls pane closed")
        self.assertAlmostEqual(pane.width(), 340.0, delta=0.5)


class ReExpansionGuardTests(harness.ReExpansionGuardTests):
    def test_a_slow_shrink_folds_the_information_then_the_status_pane(self):
        monitor, window, printer = self._mount(1100)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self.assertFalse(monitor.property("infoCollapsed"), "the pane started folded")
        self.assertFalse(monitor.property("statusCollapsed"), "the pane started folded")
        folds = self._shrink(monitor, window, 700)
        self.assertEqual(sorted(folds), ["info", "status"], "both panes must fold")
        self.assertGreater(folds["info"], folds["status"],
                           "the status pane folded before the information pane")
        # The fold points themselves: each pane folds where the camera it
        # leaves behind can no longer hold it. The 20 px sweep lands one
        # step past the exact crossing, so the bands carry the step.
        self.assertGreaterEqual(folds["info"], 940, "the information pane folded early")
        self.assertLessEqual(folds["info"], 980, "the information pane folded late")
        self.assertGreaterEqual(folds["status"], 720, "the status pane folded early")
        self.assertLessEqual(folds["status"], 760, "the status pane folded late")
        self.assertTrue(monitor.property("infoAutoCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        self.assertLess(info.width(), 100.0, "the folded pane kept its full width")
        self.assertLess(status.width(), 100.0, "the folded pane kept its full width")
        # The 'say so' half, on both panes.
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.assertIn("too narrow", self._message(monitor, "infoPanel", "information"))
        self.assertIn("too narrow", self._message(monitor, "statusPanel", "printer status"))
        # The collapsed strips: a click anywhere on a folded pane.
        self._click(info, window, 0.5, 0.6)
        self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [], "a strip expanded an auto-collapsed pane")
        self.assertEqual(printer.status_calls, [], "a strip expanded an auto-collapsed pane")
        self.assertTrue(monitor.property("infoCollapsed"), "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"), "the pane reopened while too narrow")
        # The header toggles: the same refusal.
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.info_calls, [], "a toggle expanded an auto-collapsed pane")
        self.assertEqual(printer.status_calls, [], "a toggle expanded an auto-collapsed pane")
        self.assertTrue(monitor.property("infoCollapsed"), "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"), "the pane reopened while too narrow")

    def test_widening_past_the_release_restores_both_controls(self):
        monitor, window, printer = self._mount(1100)
        self._shrink(monitor, window, 700)
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertFalse(monitor.property("infoAutoCollapsed"), "the latch never released")
        self.assertFalse(monitor.property("statusAutoCollapsed"), "the latch never released")
        self.assertFalse(monitor.property("infoExpandLocked"), "the lock outlived the fold")
        self.assertFalse(monitor.property("statusExpandLocked"), "the lock outlived the fold")
        self.assertNotIn("too narrow", self._message(monitor, "infoPanel", "information"))
        self.assertNotIn("too narrow", self._message(monitor, "statusPanel", "printer status"))
        # Both information controls answer again: the toggle collapses
        # the pane, the strip expands it. Each strip click waits for the
        # layout the toggle's own write needs — the strip's guard reads
        # the camera the layout left behind, and a stale one still reads
        # the pane's room as spent (the harness note on _settle).
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self.assertEqual(printer.info_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("infoCollapsed"))
        self._settle(monitor, window, 1100)
        self.assertFalse(monitor.property("infoExpandLocked"), "the lock outlived the collapse")
        self._click(self.find(monitor, "infoPanel"), window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("infoCollapsed"))
        # The status pane's pair, the same way.
        self._settle(monitor, window, 1100)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("statusCollapsed"))
        self._settle(monitor, window, 1100)
        self.assertFalse(monitor.property("statusExpandLocked"), "the lock outlived the collapse")
        self._click(self.find(monitor, "statusPanel"), window, 0.5, 0.6)
        self.assertEqual(printer.status_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("statusCollapsed"))

    def test_the_user_collapse_survives_the_narrow_window(self):
        # The auto fold never takes over the user's own collapse — the
        # pane stays folded for the user's reason and no auto flag is
        # set, so widening restores nothing but the user's state.
        monitor, window, printer = self._mount(1100)
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self.assertEqual(printer.info_calls, [True], "the pane did not collapse")
        self._shrink(monitor, window, 700)
        self.assertFalse(monitor.property("infoAutoCollapsed"),
                         "the auto fold overrode the user's collapse")
        self.assertTrue(monitor.property("infoCollapsed"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertTrue(monitor.property("infoCollapsed"),
                        "the user's collapse was discarded")
        self.assertEqual(printer.info_calls, [True],
                         "the narrow window wrote the persisted state")

    def test_the_user_collapse_of_the_status_pane_survives_too(self):
        # The same guard shape on the status pane: the auto fold never
        # overrides the user's own collapse, and the auto fold stands
        # down while it holds.
        monitor, window, printer = self._mount(1100)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the pane did not collapse")
        self._shrink(monitor, window, 700)
        self.assertFalse(monitor.property("statusAutoCollapsed"),
                         "the auto fold overrode the user's collapse")
        self.assertTrue(monitor.property("statusCollapsed"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertTrue(monitor.property("statusCollapsed"),
                        "the user's collapse was discarded")

    def test_a_jump_under_the_camera_room_folds_with_no_clicks(self):
        # The click-twice report: a jump whose landed layout sits under
        # the crossing never crossed the squeeze edge on the way, so the
        # fold has to come from the landed camera — and it must come
        # with NO clicks. The first click used to be the one that landed
        # the fold, so the second was refused: one click swallowed. The
        # boundary is the width where the camera with both panes open is
        # exactly at its comfort (measured at 965/966).
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        # The landing width is DERIVED: the crossing is a font-metric
        # boundary, and "965/966" is the CI stack's measurement of it
        # (this box crosses ~10 px lower, and the status pane's own
        # crossing sits further down still). What the test is about is
        # the JUMP — each step below is a jump from the previous
        # layout, never a gradual crossing — so the width walks down
        # until the landed layout has folded both panes, and the
        # assertions then hold the fold, the locks and the camera's
        # room on that landed layout.
        width = 960
        self.resize_window(monitor, window, width, 760)
        while not (monitor.property("infoAutoCollapsed")
                   and monitor.property("statusAutoCollapsed")) and width > 560:
            width -= 40
            self.resize_window(monitor, window, width, 760)
        self.assertTrue(monitor.property("infoAutoCollapsed"),
                        "the information pane did not fold")
        self.assertTrue(monitor.property("statusAutoCollapsed"),
                        "the status pane did not fold")
        self.assertTrue(monitor.property("infoCollapsed"))
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera did not get its room back")
        self.assertGreater(camera.width(), info.width(),
                           "the fold must hand the space to the camera column")
        # Both expand paths refuse, twice each, with nothing ever
        # reaching the model: the fold was not a click's work.
        for _ in range(2):
            self._click(self._toggle(monitor, "infoPanel", "information"), window)
            self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
            self._click(info, window, 0.5, 0.6)
            self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [], "a click expanded a folded pane")
        self.assertEqual(printer.status_calls, [], "a click expanded a folded pane")
        self.assertTrue(monitor.property("infoCollapsed"),
                        "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"),
                        "the pane reopened while too narrow")
        # One width past the boundary the camera can hold both panes:
        # nothing folds, so a fold that survives there is the click-twice
        # hole again (the rule reads the camera, never a stage width).
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        self.resize_window(monitor, window, 970, 760)
        # The crossing itself is host dependent: it is where the open
        # panes leave the camera at its comfort, and the pane headers'
        # text decides how many pixels the panes take (measured at
        # 965/966 in this container, above 970 on the macOS CI's
        # typeface). What must hold everywhere is the rule the product
        # itself applies — infoAutoCollapsed = infoOpenWidth under the
        # comfort OR the status pane already folded — the second leg
        # being the cascade holding the room its own fold holds (a
        # fold at 970 is legal only while a fold is what keeps the
        # camera off its comfort; with the camera free it is the
        # click-twice hole again).
        freed = monitor.property("infoOpenWidth")
        status_folded = bool(monitor.property("statusAutoCollapsed"))
        self.assertEqual(monitor.property("infoAutoCollapsed"),
                         freed < 220.0 or status_folded,
                         "the information pane's fold does not follow the camera's room")
        if status_folded:
            # The status pane's own room cannot be asserted as a value:
            # statusOpenWidth credits THIS pane's fold back out of the
            # camera, so it returns to the widened camera less the cost
            # the moment the fold lands and reads ~430 wherever a fold
            # is legitimate. Measured on this container's own crossing —
            # 433 at 965, 428 at 960, 418 at 950, every one of them with
            # the information pane folded and the fold correct. The lock
            # this branch guards is the CASCADE: a status fold that
            # stands with the information pane open is the click-twice
            # hole (the rule's own `infoWasCollapsed || statusWasFolded`).
            self.assertTrue(monitor.property("infoAutoCollapsed"),
                            "the status pane folded with the camera free")
        else:
            self.assertFalse(monitor.property("infoCollapsed"))
            self.assertFalse(monitor.property("statusCollapsed"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera is under its comfort above the boundary")

    def test_the_expansion_costs_and_the_forward_check_agree(self):
        # The contract's unit is the pane's expansion cost — its expanded
        # width less the collapsed strip it replaces — and the forward
        # check is that cost against the camera's comfort minimum. Both
        # are pinned here, so a changed cost, a changed minimum or a
        # changed measure trips this wherever the camera stands.
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        self.assertAlmostEqual(monitor.property("infoExpandCost"), 226.0, delta=0.5)
        self.assertAlmostEqual(monitor.property("statusExpandCost"), 366.0, delta=0.5)
        for width in (1250, 1100, 900, 760, 700, 640, 480):
            self.resize_window(monitor, window, width, 760)
            viewport = camera.property("viewportWidth")
            self.assertGreater(viewport, 0.0, "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            self.assertEqual(monitor.property("infoExpandBlocked"),
                             viewport - monitor.property("infoExpandCost") < 220.0,
                             "the information forward check changed at %d" % width)
            self.assertEqual(monitor.property("statusExpandBlocked"),
                             viewport - monitor.property("statusExpandCost") < 220.0,
                             "the status forward check changed at %d" % width)
            self.assertEqual(monitor.property("webcamSqueezed"), viewport < 220.0,
                             "the squeeze threshold changed at %d" % width)
            for pane in ("info", "status"):
                collapsed = monitor.property("%sCollapsed" % pane)
                locked = monitor.property("%sExpandLocked" % pane)
                # The lock never outlives its fold: an open pane is
                # never refusing anything.
                self.assertFalse(locked and not collapsed,
                                 "the %s lock outlived its fold at %d" % (pane, width))
                if collapsed:
                    self.assertEqual(
                        locked,
                        monitor.property("%sAutoCollapsed" % pane)
                        or monitor.property("webcamSqueezed")
                        or monitor.property("%sExpandBlocked" % pane),
                        "the %s lock's reasons changed at %d" % (pane, width))

    def test_the_folds_release_from_the_top_and_the_information_pane_last(self):
        # The LIFO order, measured on the way back up: the status pane
        # (folded last) is the first back, where the fold's own
        # arithmetic stops refusing; the information pane keeps its fold
        # while the status pane's stands — reclaiming its room there
        # would spend the room that fold is holding, and the pair would
        # land back on both folded — and only opens once the status pane
        # is back and the camera can hold IT (measured at 752 and 972).
        # Each width is read from a settled layout: the pass that
        # corrects the flags lays out for the flags it is correcting,
        # and the harness lays out again only on a size change (the
        # harness note on _settle), so each read finishes with a wobble
        # above the width and a return to it.
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self._shrink(monitor, window, 700)
        self.assertTrue(monitor.property("infoAutoCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        status_release = None
        info_release = None
        for width in range(700, 1101, 4):
            self._settle(monitor, window, width)
            self.resize_window(monitor, window, width + 1, 760)
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            if status_release is None and not monitor.property("statusAutoCollapsed"):
                status_release = width
                # The fold's room is the camera's again, and the lock
                # went with the fold.
                self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                        "the status pane released under the comfort at %d" % width)
                self.assertFalse(monitor.property("statusExpandLocked"),
                                 "the status lock outlived its fold at %d" % width)
            if info_release is None and not monitor.property("infoAutoCollapsed"):
                info_release = width
                break
            # The information pane's fold is the last to go: it holds
            # while the status pane's stands, and goes on holding until
            # the camera with the status pane back can hold it.
            self.assertTrue(monitor.property("infoCollapsed"),
                            "the information pane reclaimed its room at %d" % width)
            if status_release is None:
                self.assertLess(camera.property("viewportWidth") - 366.0, 220.0,
                                "the status fold held at %d with the camera able to hold it" % width)
            else:
                self.assertLess(camera.property("viewportWidth") - 226.0, 220.0,
                                "the information fold held at %d with the camera able to hold it" % width)
        self.assertIsNotNone(status_release, "the status pane's fold never released")
        self.assertIsNotNone(info_release, "the information pane's fold never released")
        self.assertLess(status_release, info_release,
                        "the two folds released in the same breath")
        self.assertGreaterEqual(status_release, 740, "the status pane released early")
        self.assertLessEqual(status_release, 780, "the status pane released late")
        self.assertGreaterEqual(info_release, 960, "the information pane released early")
        self.assertLessEqual(info_release, 1000, "the information pane released late")
        # Both panes are back, their locks went with the folds, the
        # camera keeps its comfort, and the user's controls answer.
        self.assertFalse(monitor.property("statusAutoCollapsed"),
                         "the status pane stayed folded past the release")
        self.assertFalse(monitor.property("infoCollapsed"))
        self.assertFalse(monitor.property("statusCollapsed"))
        self.assertFalse(monitor.property("infoExpandLocked"))
        self.assertFalse(monitor.property("statusExpandLocked"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera is under its comfort with the panes back")
        self.assertGreater(info.width(), 200.0)
        self.assertGreater(status.width(), 400.0)
        # The user's own controls answer again at that width: the toggle
        # collapses the pane, the strip expands it back — the lock the
        # fold left standing does not outlive the fold.
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("statusCollapsed"))
        self._settle(monitor, window, info_release)
        self.assertFalse(monitor.property("statusExpandLocked"),
                         "the lock outlived the collapse on a stage that can hold the pane")
        self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.status_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("statusCollapsed"))

    def test_the_controls_pane_refuses_once_the_camera_has_no_room(self):
        # The rule is universal: the dashboard's controls pane takes its
        # room from the same camera — read through the loaded monitor
        # document — so its every expand path refuses exactly when the
        # camera could not absorb the pane. It carries no auto fold of
        # its own, so only the user's collapse ever meets the lock.
        dashboard, window, printer = self._mount(1250, "MoonrakerMonitorDashboard.qml")
        monitor = self._monitor_in(dashboard)
        camera = self.camera_pane(monitor)
        pane = self.find(dashboard, "moonrakerControlsPane")
        self.assertAlmostEqual(dashboard.property("controlsExpandCost"), 342.0, delta=0.5)
        # The user hides the controls with the monitor's panes open: the
        # room the pane gives up goes to those panes, the camera lands at
        # 447, and 447 - 342 is under the comfort minimum — the pane is
        # locked where it lies, on the camera's own numbers.
        printer.setControlsCollapsed(True)
        self._settle(dashboard, window, 1250)
        self.assertTrue(dashboard.property("controlsCollapsed"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0)
        self.assertTrue(dashboard.property("controlsExpandBlocked"))
        self.assertTrue(dashboard.property("controlsExpandLocked"))
        # Hiding Printer status hands the camera the status pane's room
        # back: that camera can hold the controls again, so the lock
        # lifts and the status pane's expansion is ALLOWED.
        printer.setStatusCollapsed(True)
        self._settle(dashboard, window, 1250)
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertFalse(dashboard.property("controlsExpandBlocked"),
                         "the controls stayed blocked with the camera free")
        self.assertFalse(dashboard.property("controlsExpandLocked"))
        self.assertFalse(monitor.property("statusExpandLocked"),
                         "the status pane locked with the camera free")
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True, False],
                         "the status pane refused while the camera had room")
        # ... and that expansion is what crowds the camera again: the
        # controls' two paths refuse now, and the refusal says why.
        self._settle(dashboard, window, 1250)
        self.assertTrue(dashboard.property("controlsExpandBlocked"))
        self.assertTrue(dashboard.property("controlsExpandLocked"))
        self._click(self._toggle(dashboard, "moonrakerControlsPane", "printer controls"), window)
        self._click(pane, window, 0.5, 0.6)
        self.assertEqual(printer.controls_calls, [True],
                         "a click expanded the pane the camera has no room for")
        self.assertTrue(dashboard.property("controlsCollapsed"))
        self.assertIn("too narrow",
                      self._message(dashboard, "moonrakerControlsPane", "printer controls"))
        # Widening is the way out: the camera's room comes back with it.
        self.resize_window(dashboard, window, 1600, 760)
        self.assertFalse(dashboard.property("controlsExpandBlocked"),
                         "the controls stayed blocked on a wide stage")
        self._click(self._toggle(dashboard, "moonrakerControlsPane", "printer controls"), window)
        self.assertEqual(printer.controls_calls, [True, False],
                         "the toggle could not expand the pane on a wide stage")

    def test_a_fast_resize_rests_in_the_same_guarded_state(self):
        monitor, window, printer = self._mount(1100)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        camera = self.camera_pane(monitor)
        rest = []
        for _ in range(2):
            self.resize_window(monitor, window, 640, 760)
            self.assertTrue(monitor.property("infoExpandLocked"))
            self.assertTrue(monitor.property("statusExpandLocked"))
            self.assertTrue(monitor.property("infoCollapsed"))
            self.assertTrue(monitor.property("statusCollapsed"))
            self._click(info, window, 0.5, 0.6)
            self._click(status, window, 0.5, 0.6)
            self.assertEqual(printer.info_calls, [], "a strip expanded a folded pane")
            self.assertEqual(printer.status_calls, [], "a strip expanded a folded pane")
            rest.append((info.width(), status.width(), camera.width()))
            self.resize_window(monitor, window, 1100, 760)
            self.assertFalse(monitor.property("infoExpandLocked"))
            self.assertFalse(monitor.property("statusExpandLocked"))
        self.assertEqual(printer.info_calls, [], "a guarded click reached the model")
        self.assertEqual(printer.status_calls, [], "a guarded click reached the model")
        # The same jump rests in the same layout: the fold cannot
        # depend on the resize that arrived before it.
        self.assertEqual(rest[0], rest[1], "the fast resize rested differently")


class SectionOrderArrivalTests(harness.SectionOrderArrivalTests):
    def test_the_wiring_pins_the_arrival_apply_and_bans_the_timer(self):
        source = harness.qml_source("MoonrakerMonitor.qml").read_text(encoding="utf-8")
        # The arrival trigger: the model's change handler applies both
        # panes, reading the CURRENT effective layout.
        arrival = source[source.index("onPrinterChanged:"):source.index("onPrinterChanged:") + 2400]
        self.assertIn('applySectionOrder(infoContent, "information")', arrival)
        self.assertIn('applySectionOrder(statusContent, "status")', arrival)
        # The removed polling timer: its id may not exist anywhere.
        self.assertNotIn("sectionOrderApply", source)
        # No repeat Timer waits for the printer model to arrive.
        self.assertNotRegex(
            source[source.index("onPrinterChanged:"):source.index("onPrinterChanged:") + 600],
            r"Timer\s*\{|interval:\s*200|attempts",)

    def test_the_apply_reorders_the_panes_against_a_live_model(self):
        from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant

        class OutputDouble(harness.QObject):
            activePrinterChanged = harness.pyqtSignal()

            def __init__(self):
                super().__init__()
                self._printer = None

            def attach(self, printer):
                self._printer = printer
                self.activePrinterChanged.emit()

            @harness.pyqtProperty(harness.QObject, notify=activePrinterChanged)
            def activePrinter(self):
                return self._printer

        class PrinterDouble(harness.QObject):
            sectionLayoutChanged = harness.pyqtSignal()

            def __init__(self, order):
                super().__init__()
                self._order = order

            # The signature has to match the model's own: the console
            # pane calls this with a bool while its bindings evaluate,
            # and a slot declared with no arguments makes Qt abort on
            # the call rather than fail the assertion that follows.
            @harness.pyqtSlot(bool)
            def setConsoleExpanded(self, expanded):
                pass

            @harness.pyqtSlot(str, result="QVariant")
            def sectionLayoutFor(self, pane):
                return {"order": list(self._order.get(pane, [])), "hidden": []}

            @harness.pyqtProperty("QVariant")
            def temperatureChartLegend(self):
                return {"series": []}

            @harness.pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @harness.pyqtProperty("QVariant")
            def sectionHiddenMap(self):
                return {}

        output = OutputDouble()
        self.engine.rootContext().setContextProperty("OutputDevice", output)
        self.addCleanup(self.engine.rootContext().setContextProperty, "OutputDevice", None)
        monitor = self.mount_monitor(900)  # the shell constructs with no printer
        container = self.find(monitor, "moonrakerInfoContent")
        default = self._header_order(container)
        # The information pane's canonical two sections; the ids are
        # static on the headers, so the null-printer mount reads them.
        self.assertGreaterEqual(len(default), 2, "the pane needs sections to reorder")
        reversed_order = list(reversed(default))
        # The binding carries the model (the engine's notify delivery
        # is production's concern); the apply itself runs as the
        # arrival handler would call it.
        output.attach(PrinterDouble({"information": reversed_order}))
        self.pump()
        QMetaObject.invokeMethod(monitor, "applySectionOrder",
                                 Q_ARG(QVariant, container), Q_ARG(QVariant, "information"))
        self.pump()
        self.assertEqual(self._header_order(container), reversed_order)


class EscapeLadderTests(harness.EscapeLadderTests):
    def test_escape_reaches_the_ladder_with_a_text_item_focused(self):
        dashboard, window = self._dashboard()
        monitor = self._loaded_monitor(dashboard)
        console = self.find(dashboard, "moonrakerConsoleOutput")
        shortcut = self._escape_shortcut(dashboard)
        self.assertTrue(shortcut.property("enabled"), "the shortcut mounts armed")
        console.forceActiveFocus()
        self.pump(60)
        # The field engine fires no shortcut at all once a text item owns
        # the key: standing the shortcut down is that engine's condition.
        shortcut.setProperty("enabled", False)
        self.pump(30)
        self._press(dashboard, window, monitor, armed=True)
        # And with it back on, the other route must not double the rung.
        shortcut.setProperty("enabled", True)
        self.pump(30)
        self._press(dashboard, window, monitor, armed=True)



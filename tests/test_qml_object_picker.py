"""Executable qml object picker contracts."""
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    class PlateJobBoundaryTests(harness.PlateJobBoundaryTests):
        def test_a_new_jobs_geometry_replaces_the_finished_prints(self):
            # Old and new jobs both carry PART_A; the new job's core
            # report moves it from [10, 10] to [100, 100].
            model = self._model(
                core={"print_stats": {"filename": "next.gcode", "state": "printing"},
                      "exclude_object": self.MOVED},
                auxiliary={"exclude_object": self.OLD})
            rows = self._rows(model)
            self.assertEqual([100.0, 100.0], list(rows["PART_A"]["center"]),
                             "the picker plotted the finished print's geometry")
            self.assertTrue(rows["PART_A"]["current"],
                            "the new job's current object never landed")

        def test_a_new_print_with_no_objects_yet_shows_no_plate(self):
            # The job boundary with nothing defined yet: the core report
            # carries an empty object list, and the finished print's
            # polygons must not stand in for it.
            model = self._model(
                core={"print_stats": {"filename": "next.gcode"},
                      "exclude_object": {"objects": [], "excluded_objects": [],
                                         "current_object": None}},
                auxiliary={"exclude_object": self.OLD})
            self.assertEqual({}, self._rows(model),
                             "the finished print's objects stayed on the plate")

        def test_a_stale_auxiliary_copy_never_crosses_a_job_boundary(self):
            # Only the auxiliary copy has landed, and the job has moved
            # since it was read: the plate is honestly empty until the
            # core lane reports its own, never the finished print under
            # the new job's flags.
            model = self._model(core={"print_stats": {"filename": "old.gcode"}},
                                auxiliary={"exclude_object": self.OLD})
            self.assertEqual(["PART_A"], sorted(self._rows(model)),
                             "the fixture no longer projects the first landing")
            model._data.snapshot.core = {"print_stats": {"filename": "next.gcode"}}
            self.assertEqual({}, self._rows(model),
                             "the finished print's geometry survived the job boundary")

        def test_the_core_report_leads_even_when_it_names_no_objects(self):
            # The lane's own answer for the current job — an empty plate
            # — outranks a populated copy from the other lane.
            model = self._model(
                core={"exclude_object": {"objects": [], "excluded_objects": [],
                                         "current_object": None}},
                auxiliary={"exclude_object": self.MOVED})
            self.assertEqual({}, self._rows(model),
                             "an empty core report fell back to the other lane")



class PlateCanvasHitTests(harness.PlateCanvasHitTests):
    def test_axis_arrows_follow_the_pickers_bed_corner(self):
        window, face, canvas = self._picker(self._polygon_bed())
        self.assertIsNone(canvas.findChild(harness.QQuickItem, "moonrakerPlateAxisArrows"))
        bed = canvas.property("_plot").property("bed")
        origin = canvas.mapToItem(window.contentItem(), harness.QPointF())
        right = origin.x() + bed.property("offsetX").toNumber() + bed.property("plotWidth").toNumber()
        top = origin.y() + bed.property("offsetY").toNumber()
        width = bed.property("plotWidth").toNumber()
        height = bed.property("plotHeight").toNumber()
        image = window.grabWindow()
        def ink(image, colour, bounds):
            x0, y0, x1, y1 = (int(value) for value in bounds)
            result = set()
            for y in range(y0, y1):
                for x in range(x0, x1):
                    pixel = image.pixelColor(x, y)
                    if max(abs(pixel.red() - colour.red()),
                           abs(pixel.green() - colour.green()),
                           abs(pixel.blue() - colour.blue())) < 20:
                        result.add((x, y))
            return result
        red = ink(image, harness.QColor("#ef5350"), (right - .18 * width, top, right, top + 12))
        green = ink(image, harness.QColor("#66bb6a"), (right - 12, top, right, top + .18 * height))
        self.assertTrue(red and green, "the picker lost a painted border arrow")
        self.assertAlmostEqual(right - min(x for x, _ in red), .15 * width, delta=4)
        self.assertAlmostEqual(max(y for _, y in green) - top, .15 * height, delta=4)
        self.assertFalse(ink(image, harness.QColor("#ef5350"), (right - .18 * width, top - 3, right, top)))
        self.assertFalse(ink(image, harness.QColor("#66bb6a"), (right, top, right + 3, top + .18 * height)))
        canvas.setProperty("showAxisArrows", False)
        image = self._wait_until(
            window, lambda frame: not ink(frame, harness.QColor("#ef5350"),
                                          (right - .18 * width, top, right, top + 12))
            and not ink(frame, harness.QColor("#66bb6a"),
                        (right - 12, top, right, top + .18 * height)))
        self.assertFalse(ink(image, harness.QColor("#ef5350"), (right - .18 * width, top, right, top + 12)))
        self.assertFalse(ink(image, harness.QColor("#66bb6a"), (right - 12, top, right, top + .18 * height)))
        self.assertTrue(canvas.property("showGrid"))
        canvas.setProperty("showAxisArrows", True)
        image = self._wait_until(
            window, lambda frame: bool(ink(frame, harness.QColor("#ef5350"),
                                           (right - .18 * width, top, right, top + 12)))
            and bool(ink(frame, harness.QColor("#66bb6a"),
                         (right - 12, top, right, top + .18 * height))))
        self.assertTrue(ink(image, harness.QColor("#ef5350"), (right - .18 * width, top, right, top + 12)))
        self.assertTrue(ink(image, harness.QColor("#66bb6a"), (right - 12, top, right, top + .18 * height)))

    def test_the_picker_discloses_omitted_objects_without_an_extra_row(self):
        window, face, canvas = self._picker(self._polygon_bed())
        before = (face.width(), face.height())
        self._printer._plate = dict(self._printer._plate, truncated=17)
        self._printer.plateObjectsChanged.emit()
        self._pump_ms(80)
        pending = [window.contentItem()]
        texts = []
        while pending:
            item = pending.pop()
            if item.metaObject().indexOfProperty("text") >= 0:
                texts.append(str(item.property("text")))
            pending.extend(item.childItems())
        self.assertIn("17 objects omitted from this map (256-object limit)", texts)
        self.assertEqual(before, (face.width(), face.height()))


    def test_a_click_near_the_polygon_edge_selects_the_object(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 8 mm inside the bar's right end, 5 mm off its bottom edge.
        point = (232.0, 50.0)
        self.assertEqual(self._containing(rows, *point), ["Long_Bracket"],
                         "the fixture no longer places this click inside the bar")
        bounds = harness.polygon_bounds(rows[0]["polygon"])
        self.assertLessEqual(min(point[0] - bounds[0], bounds[2] - point[0],
                                 point[1] - bounds[1], bounds[3] - point[1]), 8.0,
                             "the click is not near the polygon's edge")
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Long_Bracket")
        self.assertGreater(distance, self._radius_px(canvas),
                           "the click sits inside the degraded radius after all")
        self.assertEqual(self._hover(window, canvas, face, *point), "Long_Bracket")

    def test_a_click_inside_one_of_two_close_objects_selects_that_object(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 2 mm inside the left object's right edge, 4 mm off its
        # neighbour — and 14.6 mm from the NEIGHBOUR's centre, inside the
        # degraded radius: the rule the picker replaced answered
        # Right_Block here, a different object from the one the user
        # highlighted.
        point = (188.0, 226.0)
        self.assertEqual(self._containing(rows, *point), ["Left_Block"])
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Right_Block")
        self.assertLessEqual(distance, self._radius_px(canvas),
                             "the fixture no longer pins the wrong-centre case")
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block",
                         self._last_hover_evidence)
        # The neighbour answers on its own geometry, not on proximity.
        inside = (202.0, 232.0)
        self.assertEqual(self._containing(rows, *inside), ["Right_Block"])
        self.assertEqual(self._hover(window, canvas, face, *inside), "Right_Block")

    def test_a_click_outside_every_polygon_selects_nothing(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (150.0, 100.0)
        self.assertEqual(self._containing(rows, *point), [],
                         "the fixture no longer leaves this point in the open")
        _name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertGreater(distance, self._radius_px(canvas),
                           "the point is within the degraded radius of some centre")
        self.assertEqual(self._hover(window, canvas, face, *point), "")

    def test_a_polygon_object_is_never_matched_by_its_centre(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 5 mm below the right object's bottom edge and 15 mm from its
        # centre: inside the degraded radius, outside the geometry, with
        # no other object in reach.
        point = (202.0, 245.0)
        self.assertEqual(self._containing(rows, *point), [])
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Right_Block")
        self.assertLessEqual(distance, self._radius_px(canvas),
                             "the fixture no longer pins the near-centre case")
        self.assertEqual(self._hover(window, canvas, face, *point), "",
                         "a centre the object does not own answered for it")

    def test_an_overlap_resolves_to_the_first_object_in_the_payload(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (95.0, 130.0)
        self.assertEqual(self._containing(rows, *point), ["Over_A", "Over_B"],
                         "the fixture no longer overlaps at this point")
        # The centre rule named the second one, so the assertion below
        # pins the documented rule and not the old answer.
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Over_B")
        self.assertLessEqual(distance, self._radius_px(canvas))
        self.assertEqual(self._hover(window, canvas, face, *point), "Over_A")

    def test_a_centre_only_object_still_hits_through_the_degraded_radius(self):
        rows = [self._row("Sparse_Centre", [30.0, 120.0])]
        window, face, canvas = self._picker(rows)
        self.assertEqual(self._hover(window, canvas, face, 30.0, 122.0), "Sparse_Centre")
        # The radius still bounds the fallback.
        self.assertEqual(self._hover(window, canvas, face, 30.0, 150.0), "")

    def test_a_triple_click_excludes_and_restores_the_object_under_the_pointer(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # The point where the centre rule named the neighbour: the
        # destructive gesture must act on the object the hover
        # highlighted, at every step of the gesture.
        point = (188.0, 226.0)
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block",
                         self._last_hover_evidence)
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual([call for call in self._printer.calls if call[0] == "exclude"],
                         [("exclude", "Left_Block")])
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block",
                         "the excluded object left the hit test")
        # The verdict reached the canvas with the republished payload:
        # the same gesture now restores the object it just excluded.
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual([call for call in self._printer.calls if call[0] == "restore"],
                         [("restore", "Left_Block")])

    def test_the_gesture_fixes_its_action_on_the_first_click(self):
        """The action is the plate the user saw when the gesture
        armed: the first click publishes it (the host's counter line
        reads it), and clicks one and two never act."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 1)
        self.assertEqual(face.property("pendingAction"), "exclude",
                         "the arming click did not fix its action")
        self.assertEqual(self._acts(), [], "one click acted")
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 2)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self.assertEqual(self._acts(), [], "two clicks acted")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block")])
        self.assertEqual(face.property("clickProgress"), 0,
                         "the completed gesture stayed armed")
        self.assertEqual(face.property("pendingAction"), "")
        # The exclusion reached the payload, so the object's own next
        # gesture arms the other way and fires once.
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore")
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block"),
                                        ("restore", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_another_clients_exclusion_mid_gesture_cancels_the_gesture(self):
        """The finding: an exclude armed against an included object
        must never land as a restore because a second client got
        there first. The state that invalidates the armed action
        cancels the gesture instead."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude",
                         "the mid-gesture status re-decided the action")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture ran the opposite of the action it armed")
        self.assertEqual(face.property("clickProgress"), 0,
                         "the cancelled gesture stayed armed")
        self.assertEqual(face.property("pendingAction"), "")
        self._park_pointer(window, canvas, face)

    def test_another_clients_restore_mid_gesture_cancels_the_gesture(self):
        """The mirror: a restore armed against an excluded object must
        not land as an exclusion once a second client restored it."""
        rows = self._polygon_bed()
        rows[1]["excluded"] = True  # Left_Block
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore")
        self._status_moves("Left_Block", False)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the mid-gesture status re-decided the action")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture excluded the object it had armed to restore")
        self.assertEqual(face.property("clickProgress"), 0)
        self._park_pointer(window, canvas, face)

    def test_a_state_that_changed_and_came_back_still_acts(self):
        """Permission, not history: a plate that permits the armed
        action again executes it, however it got there."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._status_moves("Left_Block", True)
        self._status_moves("Left_Block", False)
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_the_object_leaving_the_plate_mid_gesture_cancels_the_gesture(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        # A re-index, a new print: the armed target is off the plate,
        # and the ground it stood on hits nothing at all.
        self._printer._plate["objects"] = [row for row in self._printer._plate["objects"]
                                           if row["name"] != "Left_Block"]
        self._printer.plateObjectsChanged.emit()
        self.pump(20)
        for _ in range(2):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture acted on an object that left the plate")
        # The vanished gesture left nothing armed: a fresh one on
        # another object arms and completes on its own.
        for _ in range(3):
            self._click_bed(window, canvas, face, 202.0, 232.0)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Right_Block")],
                         "the vanished object's gesture leaked into the new one")
        self._park_pointer(window, canvas, face)

    def test_a_gesture_moved_to_another_object_arms_that_objects_action(self):
        """The counter restarts on the object the pointer moved to,
        and the action it arms is that object's own."""
        rows = self._polygon_bed()
        rows[2]["excluded"] = True  # Right_Block
        window, face, canvas = self._picker(rows)
        self._click_bed(window, canvas, face, 188.0, 226.0)  # Left_Block
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._click_bed(window, canvas, face, 202.0, 232.0)  # Right_Block
        self.assertEqual(face.property("clickProgress"), 1,
                         "the new object continued the old gesture")
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the new object inherited the other object's action")
        self._click_bed(window, canvas, face, 202.0, 232.0)
        self._click_bed(window, canvas, face, 202.0, 232.0)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Right_Block")],
                         "the gesture acted on the object the pointer left")
        self._park_pointer(window, canvas, face)

    def test_an_expired_gesture_rearms_from_the_current_plate(self):
        class GestureClock(harness.QObject):
            def __init__(self):
                super().__init__()
                self.value = 1000.0

            @harness.pyqtSlot(result=float)
            def now(self):
                return self.value

        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        clock = GestureClock()
        self.assertTrue(face.setProperty("gestureClock", clock))
        window_ms = face.property("tripleClickWindowMs")
        self.assertGreater(window_ms, 0, "the gesture carries no window")
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        clock.value += window_ms + 1
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 1,
                         "a click past the window continued the old gesture")
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the re-armed gesture kept the expired click's action")
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_a_cancelled_gesture_is_followed_by_a_clean_one(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [])
        # The cancelled gesture leaves nothing armed: the next three
        # clicks are a new gesture over the plate as it now stands.
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Left_Block")],
                         "the cancelled gesture's clicks leaked into the new one")
        self._park_pointer(window, canvas, face)

    def test_the_hover_label_wears_the_object_states_ink(self):
        """The live request: the picker's hover label wears the SAME
        colour the map's outline uses for the object's state —
        excluded, current, passed and plain rows each carry their own
        ink, and a lost hover falls back to the inactive text."""
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        from PyQt6.QtGui import QColor
        rows = self._polygon_bed()
        rows[0]["excluded"] = True  # Long_Bracket
        rows[1]["current"] = True  # Left_Block
        rows[2]["passed"] = True  # Right_Block
        window, face, canvas = self._picker(rows)

        def ink():
            value = QMetaObject.invokeMethod(face, "hoverInk",
                                             Q_RETURN_ARG(QVariant))
            if hasattr(value, "toVariant"):
                value = value.toVariant()
            return QColor(value).name()

        bare = ink()
        states = {}
        for bed_x, bed_y, expect in ((107.0, 37.5, "Long_Bracket"),
                                     (115.0, 215.0, "Left_Block"),
                                     (202.0, 230.0, "Right_Block"),
                                     (90.0, 110.0, "Over_A")):
            self.assertEqual(self._hover(window, canvas, face, bed_x, bed_y),
                             expect, "the hover missed its object")
            states[expect] = ink()
        # Each state's ink is its own — the label and the outline
        # never disagree on the colour.
        self.assertEqual(4, len(set(states.values())),
                         "the states' ink collapsed: %r" % states)
        self.assertNotIn(bare, set(states.values()),
                         "a hovered state fell back to the inactive ink")
        # The label's binding wears the live ink, not just the helper:
        # the hover row is the face's own sibling in the picker's
        # layout, so the pin reads its live text and colour.
        self._hover(window, canvas, face, 107.0, 37.5)
        label = [sibling for sibling in face.parentItem().childItems()
                 if sibling.property("text") == "Long_Bracket — excluded"]
        self.assertEqual(1, len(label), "the hover label never rendered the detail")
        self.assertEqual(QColor(label[0].property("color")).name(),
                         states["Long_Bracket"],
                         "the label's colour disagrees with the state ink")

    def test_the_map_follows_a_late_attach_and_a_bed_switch(self):
        """The mapping is computed from its own inputs, never from a
        resize: a printer attached after the mount, dimensions that
        arrive late and a bed switch all re-map the canvas where it
        stands. The map that only rebuilt on completion and on resize
        stayed blank — or on the previous machine's coordinate system —
        until the user happened to resize the popover."""
        document, window = self.mount_window("PlateCanvas.qml", 400, 400)
        canvas = document
        self.assertEqual(canvas.property("objectName"), "moonrakerPlateCanvas")
        self.assertIsNone(canvas.property("_plot"),
                          "the map plotted with no printer attached")

        # Wait for the threaded raster's delivered ink, not a fixed delay.
        printer = harness.LateBedDouble()
        self._bed_printer = printer
        printer.setBed(250.0, 250.0)
        document.setProperty("printerModel", printer)
        self.pump(10)
        plot = canvas.property("_plot")
        self.assertIsNotNone(plot, "the late attach never built the mapping")
        self.assertEqual(self._bed_rect(canvas), (0.0, 250.0, 0.0, 250.0))
        plotted = self._wait_until(window, lambda image: not image.isNull()
                                   and self._ink_count(image) > 0, timeout=3.0)
        self.assertGreater(self._ink_count(plotted), 0,
                           "the late attach never reached the canvas")

        # A bed switch: the same canvas, the new machine's rectangle,
        # and the raster repainted with it.
        printer.setBed(300.0, 200.0)
        self.pump(10)
        plot = canvas.property("_plot")
        self.assertIsNotNone(plot, "the bed switch dropped the mapping")
        self.assertEqual(self._bed_rect(canvas), (0.0, 300.0, 0.0, 200.0))
        switched = self._wait_until(window, lambda image: not image.isNull()
                                    and image != plotted, timeout=3.0)
        self.assertEqual(switched.size(), plotted.size())
        self.assertNotEqual(switched, plotted,
                            "the map never repainted for the bed switch")

        # Zero dimensions are unknown geometry; the real ones that
        # follow are the same no-resize path.
        printer.setBed(0.0, 0.0)
        self.pump(30)
        self.assertIsNone(canvas.property("_plot"), "a 0 mm bed still plotted")
        printer.setBed(220.0, 180.0)
        self.pump(30)
        self.assertIsNotNone(canvas.property("_plot"),
                             "the late dimensions never built the mapping")

        # The centre convention is the third bed input, on its own
        # signal: the rectangle reflects about zero.
        printer.setCentreIsZero(True)
        self.pump(30)
        self.assertEqual(self._bed_rect(canvas), (-110.0, 110.0, -90.0, 90.0))


class PlateDownloadActionTests(harness.PlateDownloadActionTests):
    def test_the_instruction_row_anchors_nothing_managed_by_a_layout(self):
        self.pump(10)  # flush anything queued by an earlier mount
        before = len(harness._APPLICATION["messages"])
        document, window, printer = self._mount_action()
        warned = [message for message in harness._APPLICATION["messages"][before:]
                  if "PlateDownloadAction.qml" in message or "managed by a layout" in message]
        self.assertEqual(warned, [], "the row's anchors warn on the real engine again")
        label, area = self._instruction_row(document)
        self.assertNotIn("Layout", area.parentItem().metaObject().className(),
                         "the MouseArea hangs off a layout item again")
        # Centre the icon and text together, rather than centring the
        # text inside a full-width row with the icon left at the edge.
        document.setProperty("idleInstruction", "Download this print")
        self.pump(30)
        row = label.parentItem()
        self.assertLess(row.width(), document.width())
        self.assertAlmostEqual(row.x() + row.width() / 2, document.width() / 2, delta=0.5)
        self.assertLess(label.x(), 30)
        progress = self.find(document, "plateDownloadProgressRow")
        self.assertAlmostEqual(progress.x() + progress.width() / 2, document.width() / 2, delta=0.5)

    def test_the_whole_instruction_row_stays_one_click_target(self):
        document, window, printer = self._mount_action()
        label, area = self._instruction_row(document)
        base = window.contentItem()
        area_rect = self.rect(area, base)
        label_rect = self.rect(label, base)
        self.assertTrue(area_rect.contains(label_rect),
                        "the label sits outside the row's MouseArea")
        # The row is the full width the card gives the action: the
        # blank space beside the text is the same offer.
        self.assertAlmostEqual(area_rect.width(), document.width(), delta=0.5)
        self._click_centre(label, window)
        self.assertEqual(printer.improve_eta_calls, 1,
                         "clicking the label no longer downloads the index")

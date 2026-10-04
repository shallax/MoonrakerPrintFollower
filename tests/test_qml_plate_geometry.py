"""Executable qml plate geometry contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_information_mini_beds_keep_a_rectangular_bed_in_proportion(self):
        previous = harness.PlatePrinterDouble.BED
        harness.PlatePrinterDouble.BED = (250.0, 400.0)
        self.addCleanup(setattr, harness.PlatePrinterDouble, "BED", previous)
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 1000)
        self._open(monitor, "")
        faces = (
            next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerBedMeshMap")
                 if item.property("compact")),
            next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerPlateProgressFace")
                 if item.property("compact")),
            next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerPlateCanvas")
                 if item.property("compact") and item.parentItem().objectName() != "moonrakerPlateProgressFace"),
        )
        self._wait_until(window, lambda _image: all(
            face.width() > 170 and abs(face.height() / face.width() - 1.6) < .02
            for face in faces), timeout=5.0)
        for face in faces:
            with self.subTest(face=face.objectName()):
                self.assertGreater(face.width(), 170)
                self.assertAlmostEqual(face.height() / face.width(), 1.6, delta=.02,
                                       msg=f"{face.objectName()}: {face.width()}x{face.height()}")

    def test_information_mini_beds_fill_the_column(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 760)
        self._open(monitor, "")
        mesh = next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerBedMeshMap")
                    if item.property("compact"))
        follower = next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerPlateProgressFace")
                        if item.property("compact"))
        picker = next(item for item in monitor.findChildren(harness.QQuickItem, "moonrakerPlateCanvas")
                      if item.property("compact") and item.parentItem().objectName() != "moonrakerPlateProgressFace")
        faces = (mesh, follower, picker)
        self._wait_until(window, lambda _image: all(
            face.width() > 170 and abs(face.height() - face.width()) <= 2
            for face in faces), timeout=5.0)
        for face in faces:
            with self.subTest(face=face.objectName()):
                self.assertGreater(face.width(), 170)
                self.assertAlmostEqual(face.height(), face.width(), delta=2,
                                       msg=f"{face.objectName()}: {face.width()}x{face.height()}")
        self.assertAlmostEqual(mesh.width(), follower.width(), delta=2)
        self.assertAlmostEqual(mesh.width(), picker.width(), delta=2)

    def test_enlarged_plate_popovers_leave_room_for_square_beds(self):
        widths = []
        for popover, name in (("plate", "moonrakerPlateExcludeFace"),
                              ("plateprogress", "moonrakerPlateProgressFace")):
            with self.subTest(popover=popover):
                monitor, _window = self.mount_window("MoonrakerMonitor.qml", 1100, 1100)
                self._open(monitor, popover)
                face = self._popover_faces(monitor, name)[0]
                widths.append(face.width())
                self.assertGreater(face.width(), 300)
                self.assertGreaterEqual(face.height(), face.width() - 2,
                                        "the plate is flattened by the popover's height")
        self.assertAlmostEqual(widths[0], widths[1], delta=2,
                               msg="the same bed uses different widths across popovers")

    def test_clear_pause_action_fits_and_stays_beside_pause_during_zoom(self):
        for panel_width in (800, 900, 1100):
            with self.subTest(panel_width=panel_width):
                monitor, window, face = self._follower_popover(width=panel_width)
                pause = monitor.findChild(harness.QQuickItem, "moonrakerFollowerPauseButton")
                self.assertIsNotNone(pause)
                clear = next(item for item in pause.parentItem().childItems()
                             if item.property("text") == "Clear")
                column = pause.parentItem().parentItem()
                self._printer.setPauseClearAvailable(True)
                # The model binding turns true before QtQuick.Layouts sizes the
                # button. While still height 0 it is vertically centred 15 px
                # below its eventual position, so it is not a zoom baseline.
                self._wait_until(
                    window,
                    lambda _image, column=column, pause=pause, clear=clear:
                    column.property("clearAvailable") is True
                    and pause.height() > 0
                    and clear.height() == pause.height()
                    and clear.width() >= column.property("clearButtonWidth"),
                    timeout=2.0)
                self.assertTrue(column.property("clearAvailable"))
                self.assertEqual(clear.height(), pause.height(),
                                 "Clear never entered the foot row at full height")
                self.assertGreaterEqual(clear.width(), column.property("clearButtonWidth"))

                def positions(pause=pause, clear=clear, window=window):
                    return (pause.mapToItem(window.contentItem(), harness.QPointF()).x(),
                            clear.mapToItem(window.contentItem(), harness.QPointF()).x(),
                            clear.mapToItem(window.contentItem(), harness.QPointF()).y())

                initial_y = positions()[2]
                for zoom in (1.0, 1.5, 2.0, 1.0):
                    face.setProperty("viewScale", zoom)
                    self._pump_ms(80)
                    pause_x, clear_x, clear_y = positions()
                    self.assertGreaterEqual(clear.width(), column.property("clearButtonWidth"))
                    self.assertGreater(pause.width(), 0)
                    self.assertLessEqual(pause_x + pause.width(), clear_x)
                    self.assertLessEqual(clear_x + clear.width(),
                                         column.mapToItem(window.contentItem(), harness.QPointF()).x()
                                         + column.width() + 1)
                    self.assertEqual(clear_y, initial_y)
                self._printer.setPauseClearAvailable(False)
                self._wait_until(
                    window,
                    lambda _image, column=column, clear=clear:
                    column.property("clearAvailable") is False
                    and clear.width() == 0,
                    timeout=2.0)
                self.assertFalse(column.property("clearAvailable"))
                self.assertEqual(clear.width(), 0)
                del self._printer

    def test_zoom_and_axis_toggle_do_not_reflow_follower_options(self):
        monitor, window, face = self._follower_popover()
        names = ("Travels", "Retractions", "Unretractions", "Axis arrows",
                 "Antialiasing")
        def controls():
            return {name: next(item for item in monitor.findChildren(harness.QQuickItem)
                               if item.property("text") == name) for name in names}
        def rows():
            return {name: round(item.mapToItem(window.contentItem(),
                                                harness.QPointF()).y(), 1)
                    for name, item in controls().items()}
        def flow_width():
            return round(controls()["Travels"].parentItem().width(), 1)

        self._pump_ms(250)
        initial = rows()
        initial_width = flow_width()
        ordered = [item.property("text") for item in
                   controls()["Travels"].parentItem().childItems()
                   if item.property("text") in names]
        self.assertEqual(ordered, list(names))
        self.assertTrue(controls()["Axis arrows"].property("checked"))
        reset = next(item for item in monitor.findChildren(harness.QQuickItem)
                     if item.property("text") == "Reset view")
        self.assertEqual(reset.property("opacity"), 0.0)
        self.assertFalse(reset.property("enabled"))
        face.setProperty("viewScale", 1.5)
        self._pump_ms(250)
        self.assertEqual(reset.property("opacity"), 1.0)
        self.assertTrue(reset.property("enabled"))
        self.assertEqual(flow_width(), initial_width, "zoom stole width from the checkbox Flow")
        self.assertEqual(rows(), initial, "zoom moved checkbox rows")
        self._printer.setFollowerShowAxisArrows(False)
        self._pump_ms(100)
        self.assertFalse(controls()["Axis arrows"].property("checked"))
        self.assertEqual(rows(), initial, "disabling arrows moved checkbox rows")
        face.setProperty("viewScale", 1.0)
        self._pump_ms(250)
        self.assertEqual(rows(), initial, "resetting zoom moved checkbox rows")
        self._printer.setFollowerShowAxisArrows(True)
        self._pump_ms(100)
        self.assertTrue(controls()["Axis arrows"].property("checked"))
        self.assertEqual(rows(), initial, "restoring arrows moved checkbox rows")

    def test_axis_arrows_track_the_follower_bed_and_clear_the_zoom_scope(self):
        face, window, _mapping, _baseline = self._painted(
            self._layer_payload(self.HORIZONTAL_RUNS))
        grid = face.findChild(harness.QQuickItem, "moonrakerPlateCanvas")
        self.assertIsNone(face.findChild(harness.QQuickItem, "moonrakerPlateAxisArrows"))
        bed = grid.property("_plot").property("bed")
        origin = face.mapToItem(window.contentItem(), harness.QPointF())
        right = origin.x() + bed.property("offsetX").toNumber() + bed.property("plotWidth").toNumber()
        top = origin.y() + bed.property("offsetY").toNumber()
        width = bed.property("plotWidth").toNumber()
        height = bed.property("plotHeight").toNumber()
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
        red_box = (right - .18 * width, top, right, top + 12)
        green_box = (right - 12, top, right, top + .18 * height)
        red_colour = harness.QColor("#ef5350")
        green_colour = harness.QColor("#66bb6a")
        def arrow_ink(image):
            return ink(image, red_colour, red_box), ink(image, green_colour, green_box)

        image = self._wait_until(window, lambda frame: all(arrow_ink(frame)))
        red, green = arrow_ink(image)
        self.assertTrue(red and green, "the follower lost a painted border arrow")
        self.assertAlmostEqual(right - min(x for x, _ in red), .15 * width, delta=4)
        self.assertAlmostEqual(max(y for _, y in green) - top, .15 * height, delta=4)
        self.assertFalse(ink(image, red_colour, (right - .18 * width, top - 3, right, top)))
        self.assertFalse(ink(image, green_colour, (right, top, right + 3, top + .18 * height)))
        face.setProperty("showAxisArrows", False)
        image = self._wait_until(window, lambda frame: not any(arrow_ink(frame)))
        red, green = arrow_ink(image)
        self.assertFalse(red or green, "the painted arrows remained after disabling them")
        self.assertTrue(grid.property("showGrid"))
        face.setProperty("showAxisArrows", True)
        image = self._wait_until(window, lambda frame: all(arrow_ink(frame)))
        red, green = arrow_ink(image)
        self.assertTrue(red and green, "the painted arrows did not return")

    def test_the_painted_horizontal_runs_stay_horizontal_and_unbridged(self):
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.HORIZONTAL_RUNS))
        start = self._scene(mapping, 30.0, 203.0)
        gap = self._scene(mapping, 90.0, 203.0)[0]
        end = self._scene(mapping, 130.0, 203.0)[0]
        last = self._scene(mapping, 190.0, 203.0)[0]
        left, row, right = int(start[0]), int(start[1]), int(last)
        box = (left - 6, row - 8, right + 6, row + 8)
        added = self._printed(3, window, face, baseline, box, (left + 4, right - 4))
        self.assertTrue(added, "the printed runs were not painted")
        self.assertLessEqual(max(abs(pixel_row - row) for _col, pixel_row in added), 3,
                             "a horizontal run did not stay horizontal")
        bridged = [col for col, _pixel_row in added if gap + 4 < col < end - 4]
        self.assertEqual(bridged, [],
                         "the painter bridged the gap between two prepared runs")
        columns = [col for col, _pixel_row in added]
        self.assertLessEqual(min(columns), left + 4)
        self.assertGreaterEqual(max(columns), right - 4)

    def test_the_painted_parallel_diagonals_stay_parallel(self):
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.PARALLEL_DIAGONALS))
        high = self._scene(mapping, 30.0, 140.0)
        low = self._scene(mapping, 90.0, 30.0)
        box = (int(high[0]) - 6, int(high[1]) - 6, int(low[0]) + 6, int(low[1]) + 6)
        added = self._printed(3, window, face, baseline, box,
                              (int(high[0]) + 4, int(low[0]) - 4))
        self.assertTrue(added, "the printed diagonals were not painted")
        bands = {}
        for col in range(box[0], box[2] + 1):
            rows = sorted(pixel_row for pixel_col, pixel_row in added if pixel_col == col)
            centres = []
            run = []
            for pixel_row in rows:
                if run and pixel_row > run[-1] + 2:
                    centres.append(sum(run) / len(run))
                    run = []
                run.append(pixel_row)
            if run:
                centres.append(sum(run) / len(run))
            bands[col] = centres
        paired = {col: centres for col, centres in bands.items() if len(centres) == 2}
        span = box[2] - box[0]
        self.assertGreaterEqual(len(paired), 0.6 * span,
                                "both diagonals must paint in every column (%d of %d)"
                                % (len(paired), span))
        separations = [centres[1] - centres[0] for centres in paired.values()]
        self.assertLessEqual(max(separations) - min(separations), 2,
                             "the parallel diagonals converge or fan on screen")
        # Each band rides its own straight line at the bed's own slope:
        # a fanned or chording painter breaks the constant per-column
        # rise.
        expected = mapping["sy"] / mapping["sx"]
        columns = sorted(paired)
        for index in (0, 1):
            rise = paired[columns[0]][index] - paired[columns[-1]][index]
            self.assertAlmostEqual(rise / (columns[-1] - columns[0]), expected,
                                   delta=0.3, msg="a diagonal's slope is not the bed's")

    def test_the_split_paints_only_the_motions_it_counted(self):
        # Split 2 counts motions 0 and 1: the first run's edge (motion
        # 1) is printed, the second run's (motion 2) is not yet — an
        # inclusive reading of the same number paints one edge ahead.
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.HORIZONTAL_RUNS))
        left = int(self._scene(mapping, 30.0, 203.0)[0])
        row = int(self._scene(mapping, 30.0, 203.0)[1])
        gap = int(self._scene(mapping, 90.0, 203.0)[0])
        end = int(self._scene(mapping, 130.0, 203.0)[0])
        right = int(self._scene(mapping, 190.0, 203.0)[0])
        box = (left - 6, row - 8, right + 6, row + 8)
        added = self._printed(2, window, face, baseline, box, (left + 4, gap - 4))
        self.assertTrue(added, "the printed run was not painted")
        ahead = [col for col, _pixel_row in added if col > gap + 4]
        self.assertEqual(ahead, [],
                         "the painter drew motions past the split it was given")
        # The next poll's delta completes the layer: the accumulation
        # adds the second run without repainting the first.
        added = self._printed(3, window, face, baseline, box, (end + 4, right - 4))
        columns = [col for col, _pixel_row in added]
        self.assertLessEqual(min(columns), left + 4, "the delta lost the first run")
        self.assertGreaterEqual(max(columns), right - 4, "the delta never reached the second run")

    def test_a_painted_arc_is_curved_and_not_its_chord(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_CCW))
        apex = self._scene(mapping, 125.0, 185.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)[0]
        right = self._scene(mapping, 185.0, 125.0)[0]
        radius = abs(chord - apex[1])
        box = (int(left) - 8, int(chord - radius) - 8, int(right) + 8, int(chord + radius) + 8)
        added = self._printed(2, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arc was not painted")
        centres = self._centres(added)
        self.assertGreater(len(centres), 30, "the arc arrived as a handful of chords")
        for col, bands in centres.items():
            self.assertEqual(len(bands), 1, "column %d carries two ink bands" % col)
        rows = [bands[0] for bands in centres.values()]
        self.assertGreater(max(abs(row - chord) for row in rows), 20.0,
                           "the painted arc never left its own chord")
        self.assertGreaterEqual(min(rows), chord - radius - 5, "the ink rose past the arc's apex")
        self.assertEqual([row for row in rows if row > chord + 6], [],
                         "the counter-clockwise arc dipped below its own chord")
        apex_band = [bands[0] for col, bands in centres.items() if abs(col - apex[0]) <= 2]
        self.assertTrue(apex_band, "the arc's apex column carries no ink")
        self.assertLessEqual(min(apex_band), apex[1] + 6, "the arc's apex is missing")

    def test_a_clockwise_arc_bulges_to_its_own_side_of_the_chord(self):
        # The same endpoints as the counter-clockwise case and the same
        # chord: only the command word moved, so the ink must too.
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_CW))
        apex = self._scene(mapping, 125.0, 65.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)[0]
        right = self._scene(mapping, 185.0, 125.0)[0]
        radius = abs(apex[1] - chord)
        box = (int(left) - 8, int(chord - radius) - 8, int(right) + 8, int(chord + radius) + 8)
        added = self._printed(2, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arc was not painted")
        centres = self._centres(added)
        rows = [bands[0] for bands in centres.values()]
        # A semicircle meets its chord perpendicular, so the endpoint
        # column's ink run spans about the square root of twice the
        # on-screen radius and its band centre sits that far below the
        # chord; the margin grows with the arc's pixel radius.
        self.assertLess(abs(min(rows) - chord), 9, "the clockwise arc did not start on its chord")
        self.assertGreater(max(rows) - chord, 20.0, "the painted arc never left its own chord")
        self.assertEqual([row for row in rows if row < chord - 6], [],
                         "the clockwise arc bulged to the counter-clockwise side")
        apex_band = [row for col, bands in centres.items() for row in bands
                     if abs(col - apex[0]) <= 2]
        self.assertTrue(apex_band and min(apex_band) >= apex[1] - 6,
                        "the clockwise arc's own apex band is missing")

    def test_a_quarter_circle_paints_as_multiple_screen_segments(self):
        gcode = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                 "G0 X185 Y125\n"
                 "G3 X125 Y185 I-60 J0 E1\n")
        face, window, mapping, baseline = self._painted(self._arc_payload(gcode))
        start = self._scene(mapping, 185.0, 125.0)
        end = self._scene(mapping, 125.0, 185.0)
        box = (int(end[0]) - 8, int(end[1]) - 8, int(start[0]) + 8, int(start[1]) + 8)
        added = self._printed(2, window, face, baseline, box, (int(end[0]) + 4, int(start[0]) - 4))
        self.assertTrue(added, "the printed quarter circle was not painted")
        centres = self._centres(added)
        self.assertGreater(len(centres), 15, "the quarter circle arrived as a few chords")
        rows = [centres[col][0] for col in sorted(centres)]
        # The straight line joining the two painted ends, at the middle
        # column: a curve whose midpoint sits on it drew as its chord.
        straight = rows[0] + (rows[-1] - rows[0]) * 0.5
        self.assertGreater(abs(rows[len(rows) // 2] - straight), 8.0,
                           "the quarter circle painted as one straight segment")

    def test_the_arc_and_the_line_after_it_join_without_a_gap(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_THEN_LINE))
        junction = self._scene(mapping, 65.0, 125.0)
        far = self._scene(mapping, 185.0, 60.0)
        apex = self._scene(mapping, 125.0, 185.0)
        box = (int(junction[0]) - 8, int(apex[1]) - 8, int(far[0]) + 8, int(far[1]) + 8)
        added = self._printed(3, window, face, baseline, box,
                              (int(junction[0]) + 4, int(far[0]) - 4))
        self.assertTrue(added, "the printed arc and line were not painted")
        self.assertTrue(self._near(added, junction, 3), "the join vertex carries no ink")
        self.assertTrue(self._near(added, far, 3), "the move after the arc was not painted")
        self.assertEqual(self._components(added), 1,
                         "the arc and the line after it painted as separate runs")
        # Every column between them carries ink on the line's own screen
        # path: the arc's tessellation ends exactly where the line starts,
        # rather than short of it or past it.
        centres = self._centres(added)
        span = far[0] - junction[0]
        for col in range(int(junction[0]) + 3, int(far[0]) - 2):
            bands = centres.get(col)
            self.assertTrue(bands, "the stroke left a gap at column %d" % col)
            expected = junction[1] + (col - junction[0]) * (far[1] - junction[1]) / span
            self.assertLessEqual(min(abs(row - expected) for row in bands), 3,
                                 "the stroke left the line at column %d" % col)

    def test_the_split_paints_the_arc_by_its_original_motion_index(self):
        # Split 2 counts motions 0 and 1 — the travel and the arc — and
        # the arc's whole curve belongs to motion 1. A split read as a
        # vertex count instead paints part of the curve at 1, and an
        # inclusive reading of 2 paints the line after it.
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_THEN_LINE))
        apex = self._scene(mapping, 125.0, 185.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)
        right = self._scene(mapping, 185.0, 125.0)
        far = self._scene(mapping, 185.0, 60.0)
        span = (int(left[0]) + 4, int(right[0]) - 4)
        box = (int(left[0]) - 8, int(apex[1]) - 8, int(right[0]) + 8, int(far[1]) + 8)
        self._printer.setSplit(1)
        # The absence below is read off a frame the face has PAINTED at
        # split 1: a grab taken first holds the previous split's picture,
        # and the wait has to be on the paint rather than on ink — this
        # claim is that the split paints NOTHING, so a wait for ink can
        # only burn its budget and hand back whatever the threaded
        # painter had committed. The dump that lived here is gone with
        # the cause it was hunting: the baseline was a picture of the
        # previous state, which `_painted` no longer hands back.
        self._await_split(face, 1)
        added = self._added(self._settled(window), baseline, face, window, box)
        self.assertEqual(added, set(), "the split painted the arc before its own motion")
        added = self._printed(2, window, face, baseline, box, span)
        self.assertTrue([row for _col, row in added if abs(row - chord) < 8],
                        "the arc was never painted")
        self.assertEqual([row for _col, row in added if row > chord + 8], [],
                         "the split painted the motion after the arc")
        line_box = (int(left[0]) - 8, int(chord) + 8, int(right[0]) + 8, int(far[1]) + 8)
        self._printer.setSplit(3)
        _image, line_ink = self._await_ink(
            window, face, baseline, line_box,
            (int((left[0] + right[0]) / 2), int(right[0]) - 4))
        self.assertTrue(line_ink, "the motion after the arc was never painted")

    def test_disconnected_arc_runs_are_not_bridged(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_RUNS))
        first_end = self._scene(mapping, 105.0, 125.0)[0]
        second_start = self._scene(mapping, 145.0, 125.0)[0]
        left = self._scene(mapping, 15.0, 125.0)[0]
        right = self._scene(mapping, 235.0, 125.0)[0]
        apex = self._scene(mapping, 60.0, 170.0)[1]
        chord = self._scene(mapping, 60.0, 125.0)[1]
        box = (int(left) - 8, int(apex) - 8, int(right) + 8, int(chord) + 8)
        added = self._printed(4, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arcs were not painted")
        columns = [col for col, _row in added]
        self.assertLessEqual(min(columns), left + 4, "the first arc is missing")
        self.assertGreaterEqual(max(columns), right - 4, "the second arc is missing")
        self.assertEqual([col for col in columns if first_end + 4 < col < second_start - 4], [],
                         "the painter bridged two arc runs a travel apart")
        self.assertEqual(self._components(added), 2, "the two arc runs painted as one")

    def test_the_follower_fills_its_plot_edge_to_edge(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        face = faces[0]
        # No dot for this measurement: the scene-graph dot inks
        # instantly and would satisfy the wait before the threaded
        # paint lands — the stroke is this test's subject.
        face.setProperty("dot", None)
        plot = face.findChild(harness.QQuickItem, "moonrakerPlateCanvas").property("_plot")
        bed = plot.property("bed")
        top = bed.property("offsetY").toNumber()
        bottom = top + bed.property("plotHeight").toNumber()
        rows = self._ink_rows(self._grab_when_inked(window, face, top=top), face, window)
        if not rows or min(rows) > top + 12 or max(rows) < bottom - 12:
            window.grabWindow().save("/tmp/mpf/follower_fail.png")
        self.assertTrue(rows, "the follower painted nothing")
        # The corner-pinned stroke runs along the plot's extreme
        # edges: any truncated or offset mapping leaves a band empty.
        self.assertLessEqual(min(rows), top + 12, "no ink at the plot's top edge")
        self.assertGreaterEqual(max(rows), bottom - 12, "no ink at the plot's bottom edge")

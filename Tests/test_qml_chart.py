"""Executable qml chart contracts."""
from tests import qml_engine_support as harness

class ChartSurfaceTests(harness.ChartSurfaceTests):
    def test_the_data_canvas_requests_the_threaded_image_strategy(self):
        chart, _ = self.mount_chart()
        canvas = self.find(chart, "temperatureDataCanvas")
        threaded = self.enum_value(canvas, "RenderStrategy", "Threaded")
        image = self.enum_value(canvas, "RenderTarget", "Image")
        self.assertEqual(self.read_js(self.engine, canvas, "renderStrategy"),
                         (threaded, False), "the canvas must request Canvas.Threaded")
        self.assertEqual(self.read_js(self.engine, canvas, "renderTarget"),
                         (image, False), "the canvas must request Canvas.Image")
        self.assertEqual(self.canvas_messages(), [],
                         "the runtime complained about the chart canvas")

    def test_the_paint_input_is_the_main_thread_plain_data_snapshot(self):
        # The threaded paint must never reach a theme singleton or
        # another QML item: its complete input is the snapshot the
        # document builds on the main thread, and it must exist — with
        # the series, mapping and labels resolved — before any paint.
        chart, _ = self.mount_chart()
        canvas = self.find(chart, "temperatureDataCanvas")
        job = canvas.property("paintJob").toVariant()
        self.assertIsNotNone(job, "the paint job was never snapshotted")
        self.assertEqual(len(job["series"]), 1)
        self.assertEqual(len(job["series"][0]["points"]), 40)
        self.assertEqual(len(job["grid"]), 5)
        self.assertGreaterEqual(len(job["ticks"]), 5)
        self.assertIn("actual", job["series"][0])
        # A trackless payload (the mini's shape) snapshots without
        # touching targets/powers at all.
        mini, _ = self.mount_chart(compact=True, with_tracks=False)
        mini_job = self.find(mini, "temperatureDataCanvas").property("paintJob").toVariant()
        self.assertEqual(len(mini_job["series"]), 1)
        self.assertEqual(len(mini_job["grid"]), 3)
        self.assertEqual(self.canvas_messages(), [])

    def test_the_hover_surface_is_scene_graph_and_publishes_values(self):
        chart, _ = self.mount_chart()
        before = len(harness._APPLICATION["messages"])
        chart.setProperty("hoverX", 400.0)
        self.pump(30)
        cursor = self.find(chart, "temperatureHoverCursor")
        self.assertTrue(cursor.property("visible"))
        self.assertGreater(cursor.property("x"), 0)
        self.assertNotEqual(chart.property("hoverClock"), "")
        values = chart.property("hoverValues").toVariant()
        self.assertIn("extruder", values)
        self.assertTrue(str(values["extruder"]).endswith("°C"))
        markers = self.find(chart, "temperatureHoverMarkers")
        self.assertEqual(markers.property("count"), 1)
        # The hover published scalars and moved items — the data
        # canvas was never asked to repaint (a repaint would show up
        # as engine noise, and there is no overlay Canvas left to
        # receive one).
        self.assertEqual(harness._APPLICATION["messages"][before:], [])
        # Leaving clears the hover state.
        chart.setProperty("hoverX", -1.0)
        self.pump(30)
        self.assertFalse(cursor.property("visible"))
        self.assertEqual(markers.property("count"), 0)

    def test_the_compact_chart_never_shows_the_hover_surface(self):
        chart, _ = self.mount_chart(compact=True)
        chart.setProperty("hoverX", 400.0)
        self.pump(30)
        cursor = self.find(chart, "temperatureHoverCursor")
        self.assertFalse(cursor.property("visible"))


class ChartColourPickerTests(harness.ChartColourPickerTests):
    def test_the_monitor_loads_without_building_a_colour_dialog(self):
        monitor, _window, _printer = self._mount(1100)
        built = self.colour_pickers(monitor)
        self.assertEqual(len(built), 0,
                         "the monitor built a colour dialog at load: %r" % (built,))

    def test_the_first_click_builds_the_picker_and_applies_the_colour(self):
        monitor, _window, printer = self._mount(1100)
        monitor.setProperty("selectedChartSensor", "heater_bed")
        harness.QMetaObject.invokeMethod(self.chart_card(monitor), "openChartColorDialog")
        self.pump(10)
        pickers = self.colour_pickers(monitor)
        self.assertEqual(len(pickers), 1, "the picker did not build on the click")
        pickers[0].setProperty("selectedColor", harness.QColor("#123456"))
        # The dialog's own accept signal is the wiring under test: the
        # card's handler must run from the signal, not from a call.
        harness.QMetaObject.invokeMethod(pickers[0], "accepted")
        self.pump(10)
        self.assertEqual(printer.sensor_colors, [("heater_bed", "#123456")],
                         "the picked colour never reached the printer")

    def test_a_second_click_reuses_the_built_picker(self):
        monitor, _window, printer = self._mount(1100)
        monitor.setProperty("selectedChartSensor", "extruder")
        card = self.chart_card(monitor)
        harness.QMetaObject.invokeMethod(card, "openChartColorDialog")
        self.pump(10)
        harness.QMetaObject.invokeMethod(card, "openChartColorDialog")
        self.pump(10)
        self.assertEqual(len(self.colour_pickers(monitor)), 1,
                         "the click rebuilt the picker instead of reusing it")
        pickers = self.colour_pickers(monitor)
        pickers[0].setProperty("selectedColor", harness.QColor("#0a0b0c"))
        harness.QMetaObject.invokeMethod(pickers[0], "accepted")
        self.pump(10)
        self.assertEqual(printer.sensor_colors, [("extruder", "#0a0b0c")],
                         "the reused picker did not reach the printer")



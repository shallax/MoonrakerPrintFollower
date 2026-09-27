"""Executable monitor files runtime contracts."""
from tests import monitor_test_support as harness

class MonitorQtTests(harness.MonitorQtTests):
    def test_panel_state_migrates_the_legacy_flat_section_file(self):
        # The first shipped format stored the bare section map; it must
        # still hydrate into sections with default panel toggles.
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"setup": False, "toolhead": True}, handle)
        model = self.monitor()
        self.assertEqual(model._sections, {"setup": False, "toolhead": True})
        self.assertFalse(model.controlsCollapsed)
        self.assertFalse(model.controlsLocked)

    def test_corrupt_panel_state_file_degrades_to_defaults(self):
        # A truncated or hand-edited file must never raise or hydrate
        # inverted: unreadable JSON yields defaults, and string flags like
        # 'false' must collapse (bool('false') is True).
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        model = self.monitor()
        self.assertEqual(model._sections, {})
        self.assertFalse(model.controlsCollapsed)
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"sections": {"setup": "false", "toolhead": 0},
                       "controlsLocked": "true"}, handle)
        model = self.monitor()
        self.assertIs(model._sections["setup"], False)
        self.assertIs(model._sections["toolhead"], False)
        self.assertIs(model.controlsLocked, True)
        # The console height goes through the same discipline: a missing
        # key is the unset default, junk never raises, and a negative or
        # non-numeric height can never hydrate.
        self.assertEqual(model.consoleHeight, 0)
        for stored in ("-120", "", "tall", None, [], float("inf"), float("nan")):
            with open(section_path, "w", encoding="utf-8") as handle:
                harness.json.dump({"consoleHeight": stored}, handle)
            model = self.monitor()
            self.assertEqual(model.consoleHeight, 0, stored)
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"consoleHeight": "412"}, handle)
        model = self.monitor()
        self.assertEqual(model.consoleHeight, 412)

    def test_files_view_model_keeps_stable_identities_behind_the_list(self):
        # The view model (4.3.0): the QAbstractListModel behind the
        # list-valued projection — the relpath is the row identity,
        # so a rebuild re-anchors delegates by identity, never by
        # position.
        from plugins.FilesViewModel import FilesViewModel
        view = FilesViewModel()
        rows = [{"relpath": "a.gcode", "name": "a"},
                {"relpath": "b.gcode", "name": "b"},
                {"relpath": "c.gcode", "name": "c"}]
        view.set_rows(rows)
        self.assertEqual(view.rowCount(), 3)
        self.assertEqual(view.data(view.index(1)), "b.gcode")
        # A reordered rebuild keeps each row's identity attached to
        # its file.
        view.set_rows([rows[2], rows[0], rows[1]])
        self.assertEqual(view.data(view.index(0)), "c.gcode")
        self.assertEqual(view.data(view.index(2)), "b.gcode")

    def test_moonraker_file_metadata_populates_layer_height_and_estimate(self):
        # Klipper never reports a per-layer thickness. Moonraker's file
        # metadata parses the slicer header SERVER-SIDE (a tiny JSON
        # query, not a gcode download), so the layer-height readout and
        # the slicer estimate populate even for prints the user never
        # loaded — the "no proactive downloads" ruling is
        # untouched.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if "server/files/metadata" in request.path]
        self.assertTrue(requests)
        requests[-1].callback({"result": {"layer_height": 0.2, "first_layer_height": 0.3, "estimated_time": 3600}},
                              None)
        # The coordinator re-publishes its snapshot on the callback; the
        # model re-reads it on its own publish (its poll timers are far
        # too slow for a test event-loop spin).
        model._data.changed.emit()
        self.qt.events()  # the publish coalescer flushes on the next turn
        # current_layer=2 (one-based) -> layer index 1 -> step 0.2 mm.
        self.assertEqual(model.monitorLayerHeight, "0.200 mm")
        # 3600 s slicer estimate - 30 s elapsed.
        self.assertEqual(model.monitorEta, "00:59:30")
        self.assertEqual(model.monitorEtaBasis, "blend")



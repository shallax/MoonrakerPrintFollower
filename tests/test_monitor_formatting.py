"""Executable monitor formatting contracts."""
from tests import monitor_test_support as harness

class MonitorFormattingTests(harness.MonitorFormattingTests):
    def test_preview_temperature_pair_renders_the_fixed_pair(self):
        # The strip's fixed pair: hotend and bed with the
        # current→target form. target 0.0 = no setpoint — the arrow
        # is omitted; a 0.0 reading on a heater with no target is
        # "—" (Klipper's not-measured convention); missing objects
        # render "—".
        hotend, bed = harness.preview_temperature_pair({
            "extruder": {"temperature": 205.2, "target": 210.0},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
        })
        self.assertEqual(hotend, "205.2 → 210.0 °C")
        self.assertEqual(bed, "60.0 → 60.0 °C")
        hotend, bed = harness.preview_temperature_pair({
            "extruder": {"temperature": 23.4, "target": 0.0},
            "heater_bed": {"temperature": 0.0, "target": 0.0},
        })
        self.assertEqual(hotend, "23.4 °C")
        self.assertEqual(bed, "—")
        hotend, bed = harness.preview_temperature_pair({"extruder": {"temperature": None}})
        self.assertEqual(hotend, "—")
        self.assertEqual(bed, "—")

    def test_layer_and_height_readouts_render_the_printer_side(self):
        # The status bar's cells: the HUMAN layer number and the
        # absolute Z with its unit — '—' while the resolver has
        # nothing. Zero-based indexes shift; None cells read absent.
        layer = harness.PhysicalLayer()
        self.assertEqual(harness.layer_readout(layer), "—")
        self.assertEqual(harness.height_readout(layer), "—")
        layer = harness.replace(layer, index=11, height=12.34)
        self.assertEqual(harness.layer_readout(layer), "12")
        self.assertEqual(harness.height_readout(layer), "12.34 mm")
        layer = harness.replace(layer, total=345)
        self.assertEqual(harness.layer_readout(layer), "12/345")
        layer = harness.replace(layer, index=0, height=0.0, total=None)
        self.assertEqual(harness.layer_readout(layer), "1")
        self.assertEqual(harness.height_readout(layer), "0.00 mm")

    def test_print_job_caption_names_every_state(self):
        # The caption's vocabulary (F18): disconnected and unknown
        # name themselves (never "Idle" while the socket is down), the
        # controls lock names itself so the dead action band keeps its
        # context, and the job state word maps once.
        self.assertEqual(harness.print_job_caption(None), "")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="no", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Disconnected")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="unknown", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Printer state unknown")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="yes", state="printing",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=True, busy=False)),
                         "Locked")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="yes", state="printing",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Printing")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="yes", state="paused",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Paused")
        self.assertEqual(harness.print_job_caption(harness.Observation(active=True, connection="yes", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Idle")

    def test_preview_block_carries_the_verdicts_and_the_sentinel(self):
        # The block rides the aux clock: the stamp passes through
        # untouched, the verdicts come from the same policy rows the
        # Dashboard reads (one derivation — the surfaces cannot
        # disagree), and absence is an explicit shape.
        observation = harness.Observation(active=True, connection="yes", state="printing",
                                  homed_axes="xyz", assumed_stopped=False,
                                  save_config_pending=False, controls_locked=False, busy=False,
                                  pause_resume_supported=True)
        block = harness.preview_block({"extruder": {"temperature": 205.2, "target": 210.0}},
                              observation, stamp=12.5)
        self.assertEqual(block["stamp"], 12.5)
        self.assertEqual(block["state"], "printing")
        self.assertTrue(block["canPause"])
        self.assertFalse(block["canResume"])
        self.assertEqual(block["pauseReason"], "")
        self.assertEqual(block["resumeReason"], "Print is not paused")
        self.assertIn("Resume applies to a paused print", block["resumeReasonDetail"])
        self.assertFalse(block["inactive"])
        # The sentinel shape: no observation and no aux — everything
        # reads absent, nothing is omitted.
        block = harness.preview_block({}, None, stamp=0.0, inactive=True)
        self.assertTrue(block["inactive"])
        self.assertFalse(block["canPause"])
        self.assertEqual(block["hotend"], "—")
        self.assertEqual(block["bed"], "—")

    def test_macro_parameter_inference_types_defaults(self):
        definitions = harness.infer_macro_parameters("""
            {% set enabled = params.ENABLED|default(True) %}
            {% set count = params.COUNT|default(5)|int %}
            {% set scale = params.SCALE|default(0.25)|float %}
            {% set label = params.LABEL|default('test') %}
            {% set required = params.REQUIRED|int %}
        """)
        by_name = {item["name"]: item for item in definitions}
        self.assertEqual(by_name["ENABLED"]["type"], "bool")
        self.assertEqual(by_name["ENABLED"]["default"], "True")
        self.assertEqual(by_name["COUNT"]["type"], "int")
        self.assertEqual(by_name["COUNT"]["default"], "5")
        self.assertEqual(by_name["SCALE"]["type"], "float")
        self.assertEqual(by_name["LABEL"]["type"], "string")
        self.assertTrue(by_name["REQUIRED"]["required"])

    def test_monitor_layer_height_reports_current_thickness(self):
        config = harness.SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        layer = harness.LayerResolver().resolve(
            {"print_stats": {"info": {"current_layer": 2, "total_layer": 3}}},
            config,
            metadata={"first_layer_height": 0.2, "layer_height": 0.2},
            heights=(0.2, 0.35, 0.55),
        )
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = harness.SimpleNamespace(
            layer=layer,
            estimated_time=None,
            metadata_complete=False,
        )
        self.assertEqual(harness.core_values(snapshot, physical, True)["monitorLayerHeight"], "0.150 mm")

    def test_monitor_progress_reports_two_decimals(self):
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.655766},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        self.assertEqual(harness.core_values(snapshot, physical, True)["monitorProgress"], 65.58)

    def test_speed_factor_rename_is_mechanically_pinned(self):
        # The 4.2.0 rename: the multiplier row reads "Speed factor"
        # and no plain "Speed" caption survives in the Monitor card
        # (the UX re-review's ask for a mechanical pin).
        self.assertIn('text: "Speed factor"', harness.JOB_SECTION_QML)
        self.assertNotIn('text: "Speed"', harness.MONITOR_QML)

    def test_motion_rows_report_the_live_values(self):
        # The motion cluster (4.2.0): Velocity is Klipper's scalar
        # speed magnitude; Flow rate is the commanded volumetric flow
        # — live_extruder_velocity × π·(d/2)²; Accel limit is the
        # configured ceiling from the aux poll's toolhead.
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": 0.34},
        }, auxiliary={
            "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
        })
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = harness.core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "20.0 mm/s")
        self.assertEqual(values["monitorFlowRate"], "0.8 mm³/s")
        self.assertEqual(values["monitorAccelLimit"], "5000 mm/s²")
        self.assertEqual(values["monitorFlowDiameter"], "1.75 mm")

    def test_motion_rows_read_dashes_without_the_objects(self):
        # "—" only when the printer reports no motion object — the
        # existing core-only snapshots (no auxiliary) must not break.
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "idle", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = harness.core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "—")
        self.assertEqual(values["monitorFlowRate"], "—")
        self.assertEqual(values["monitorAccelLimit"], "—")

    def test_flow_rate_keeps_the_retraction_sign_and_clamps_epsilon(self):
        # A retraction reads negative; a cancellation artifact
        # (-3.6e-15 was caught live) clamps to zero before the sign
        # decision, never "-0.00 mm³/s".
        def values_with(ev):
            snapshot = harness.SimpleNamespace(core={
                "print_stats": {"state": "printing", "print_duration": 30},
                "virtual_sdcard": {"progress": 0.5},
                "gcode_move": {},
                "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": ev},
            }, auxiliary={
                "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
                "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
            })
            physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                       estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
            return harness.core_values(snapshot, physical, True)
        self.assertEqual(values_with(-23.6)["monitorFlowRate"], "-56.8 mm³/s")
        # The display threshold (the re-review): a cancellation
        # artifact (-3.6e-15 was caught live) AND any magnitude that
        # would round to "-0.0" at %.1f clamp to zero.
        self.assertEqual(values_with(-3.552713678800501e-15)["monitorFlowRate"], "0.0 mm³/s")
        self.assertEqual(values_with(-0.01)["monitorFlowRate"], "0.0 mm³/s")

    def test_motion_rows_read_zero_when_idle_and_connected(self):
        # The idle state (the re-review's pin): a connected printer
        # with motion_report present reads 0, never "—".
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "standby", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {"live_velocity": 0.0, "live_extruder_velocity": 0.0},
        }, auxiliary={
            "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
        })
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = harness.core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "0.0 mm/s")
        self.assertEqual(values["monitorFlowRate"], "0.0 mm³/s")
        self.assertEqual(values["monitorAccelLimit"], "5000 mm/s²")

    def test_flow_rate_uses_the_active_tools_diameter_only(self):
        # Per-tool: the ACTIVE tool's section supplies the diameter;
        # [extruder_stepper] sections never match (the prefix-sweep
        # hazard), and the tool name is validated before the lookup.
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": 0.5},
        }, auxiliary={
            "toolhead": {"extruder": "extruder1", "max_accel": 5000.0},
            "configfile": {"settings": {
                "extruder": {"filament_diameter": 1.75},
                "extruder1": {"filament_diameter": 2.85},
                "extruder_stepper main": {"foo": 1},
            }},
        })
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = harness.core_values(snapshot, physical, True)
        # 0.5 × π × (2.85/2)² = 3.19 mm³/s, from extruder1's diameter.
        self.assertEqual(values["monitorFlowDiameter"], "2.85 mm")
        self.assertEqual(values["monitorFlowRate"], "3.2 mm³/s")

    def test_layer_progress_comes_from_the_snapshot_byte_fraction(self):
        # The within-layer fraction comes from the index's byte ranges
        # (the nozzle's Z never moves within a layer, so Z cannot
        # express it); without an index the UI hides the bar.
        from mpf.printer.PrintState import PhysicalLayer
        snapshot = harness.SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = harness.SimpleNamespace(layer=PhysicalLayer(1, 10), estimated_time=None,
                                   metadata_complete=False, layer_eta=None, layer_progress=0.5)
        self.assertAlmostEqual(harness.core_values(snapshot, physical, True)["monitorLayerProgress"], 0.5, places=6)
        physical = harness.SimpleNamespace(layer=PhysicalLayer(1, 10), estimated_time=None,
                                   metadata_complete=False, layer_eta=None, layer_progress=None)
        self.assertEqual(harness.core_values(snapshot, physical, True)["monitorLayerProgress"], -1.0)

    def test_eta_prefers_slicer_time_for_early_and_resumed_prints(self):
        self.assertAlmostEqual(harness.estimate_remaining(3600, 0.02, 7 * 3600, True), 6 * 3600, delta=1)
        self.assertAlmostEqual(harness.estimate_remaining(3 * 3600, 0.10, 7 * 3600, True), 4 * 3600, delta=1)
        self.assertIsNone(harness.estimate_remaining(120, 0.50, None, False))

    def test_eta_appears_as_soon_as_moonraker_reports_a_little_progress(self):
        # The connect-time expectation: the unoptimised blend
        # shows up with only a small progress signal, not a minute into
        # the print (the old 60 s / 2% floor left the readout empty).
        self.assertAlmostEqual(harness.estimate_remaining(15, 0.01, None, True), 1485, delta=1)
        self.assertIsNone(harness.estimate_remaining(5, 0.01, None, True))       # too early
        self.assertIsNone(harness.estimate_remaining(15, 0.001, None, True))    # no progress signal

    def test_layer_resolver_bounds_the_estimate_by_the_total_print_height(self):
        # An underestimated step (noise, a mis-cancelled hop) must not
        # claim a layer above the object's own height.
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        resolver = harness.LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5}}
        for z, e in ((9.8, 1.0), (9.8, 2.0), (9.9, 3.0), (9.9, 4.0)):
            status["gcode_move"] = {"gcode_position": [100, 100, z, e]}
            resolver.resolve(status, config, metadata={"object_height": 10})
        # step measured as 0.1 (the only ascent) -> round((9.9-0.1)/0.1)
        # = 98 layers, inside the 100-layer object-height bound.
        layer = resolver.resolve(status, config, metadata={"object_height": 10})
        self.assertEqual(layer.index, 98)

    def test_layer_resolver_measures_the_layer_height_when_the_header_never_says(self):
        # Some slicer headers declare no layer height at all; the
        # resolver measures it from the observed Z increments (layer
        # changes dominate positive deltas while extrusion advances).
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        resolver = harness.LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5}}
        for z, e in ((0.3, 1.0), (0.3, 2.0), (0.5, 3.0), (0.5, 4.0), (0.7, 5.0), (0.7, 6.0)):
            status["gcode_move"] = {"gcode_position": [100, 100, z, e]}
            resolver.resolve(status, config, metadata={})
        layer = resolver.resolve(status, config, metadata={})
        self.assertEqual(layer.index, 2)  # step measured as 0.2, first assumed 0.2
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_seeds_a_mid_print_connect_and_heals_a_wipe_seed(self):
        # The trace: a mid-print connect at z=9.75 and 65%
        # progress reported no current_layer. At significant progress
        # the position IS the real print height — seed freely — and a
        # wipe above the print self-heals when the nozzle descends
        # (printing never descends, so a lower candidate is the
        # correction; three consecutive observations avoid boundary
        # jitter oscillation).
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        metadata = {"layer_height": 0.2, "first_layer_height": 0.3}
        resolver = harness.LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5},
                  "gcode_move": {"gcode_position": [100, 100, 10, 1.0]}}
        self.assertIsNone(resolver.resolve(status, config, metadata=metadata).index)  # baseline only
        status["gcode_move"] = {"gcode_position": [100, 100, 9.75, 2.0]}
        self.assertEqual(resolver.resolve(status, config, metadata=metadata).index, 47)  # mid-print height seeds
        for e in (3.0, 4.0, 5.0):
            status["gcode_move"] = {"gcode_position": [100, 100, 0.3, e]}
            resolver.resolve(status, config, metadata=metadata)
        layer = resolver.resolve(status, config, metadata=metadata)
        self.assertEqual(layer.index, 0)  # the descent corrected the wipe seed
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_ignores_the_parked_z_before_the_file_starts(self):
        # The Z estimate must not seed from the parked height (z=10
        # while heating used to read as "Layer 50" for the whole
        # print); it engages only once the virtual SD has started.
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        metadata = {"layer_height": 0.2, "first_layer_height": 0.3}
        resolver = harness.LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.0},
                  "gcode_move": {"gcode_position": [100, 100, 10, 0.0]}}
        self.assertEqual(resolver.resolve(status, config, metadata=metadata).source, "print start")
        # The file starts at the first-layer height and extrusion
        # advances: the estimate tracks from there.
        status["virtual_sdcard"] = {"progress": 0.05}
        status["gcode_move"] = {"gcode_position": [100, 100, 0.3, 0.5]}
        layer = resolver.resolve(status, config, metadata=metadata)
        self.assertEqual(layer.index, 0)
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_does_not_claim_layer_1_for_a_stuck_current_layer(self):
        # A printer whose G-code never emits per-layer stats reports
        # current_layer=0 forever; past ~3% progress claiming "Layer 1"
        # would be a stuck lie — the honest value is None.
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=False, z_tolerance=0.05)
        stuck = harness.LayerResolver().resolve(
            {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
             "virtual_sdcard": {"progress": 0.5}}, config)
        self.assertIsNone(stuck.index)

    def test_layer_resolver_shows_layer_1_at_print_start(self):
        # An ACTIVE print at current_layer=0 (before the first
        # SET_PRINT_STATS_INFO) reads as layer 1 — values must show
        # as soon as Moonraker reports them, not "—" until the
        # print advances a layer.
        config = harness.SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=False, z_tolerance=0.05)
        active = harness.LayerResolver().resolve(
            {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}}}, config)
        self.assertEqual(active.index, 0)
        self.assertEqual(active.source, "print start")
        idle = harness.LayerResolver().resolve(
            {"print_stats": {"state": "standby", "info": {"current_layer": 0, "total_layer": 12}}}, config)
        self.assertIsNone(idle.index)  # pre-print suppression stays

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        self.assertEqual(harness.parse_bed_mesh(None), {})
        for matrix, bounds in (([[0, 1], [2]], [0, 0]), ([[0, float("nan")], [1, 2]], [0, 0]), ([[0, 1], [1, 2]], [2, 0])):
            self.assertEqual(harness.parse_bed_mesh({"mesh_matrix": matrix, "mesh_min": bounds, "mesh_max": [1, 1]}), {})
        self.assertEqual(harness.parse_mcu_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123"), {"mcu_awake": 0.02, "bytes_read": 123.0})

    def test_file_row_payload_carries_the_folder_breadcrumb(self):
        # The search face shows the folder under the name (the
        # live request: same-named files in different
        # folders must be tellable); root-level files carry "".
        row = harness.SimpleNamespace(filename="a.gcode", relpath="prints/sub/a.gcode", modified=None,
                              size=None, attempts=None, last_status=None, print_start_time=None,
                              object_height=None, layer_height=None, estimated_time=None,
                              last_print=None, slicer=None, extruder=None, bed=None, filament=None)
        self.assertEqual(harness.file_row_payload(row, 0)["folder"], "prints/sub")
        row.relpath = "a.gcode"
        self.assertEqual(harness.file_row_payload(row, 0)["folder"], "")



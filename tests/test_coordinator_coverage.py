"""Executable coordinator coverage contracts."""
from tests import control_owner_support as harness

class CoordinatorCoverageTests(harness.CoordinatorCoverageTests):
    def test_both_views_receive_the_same_once_matched_motion_observation(self):
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        parts.index.plate_split = 27
        parts.client.connected = True
        calls = []
        original = parts.index.observe_motion

        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            return result

        parts.index.observe_motion = observe
        parts.coordinator.refresh()
        self.assertEqual(len(calls), 1)
        snapshot = parts.coordinator.snapshot
        self.assertIs(snapshot.motion_progress, calls[0])
        self.assertIs(parts.preview.observed[-1][0].motion_progress, calls[0])
        self.assertEqual(snapshot.plate_progress["split"], calls[0].split)
        self.assertEqual(snapshot.layer_progress, calls[0].fraction)

    def test_travel_velocity_roundoff_cannot_confirm_layer_entry(self):
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        calls = []
        real = parts.index.observe_motion

        def record(anchor, file_position=None, live_position=None, paused=False, extruding=None):
            calls.append(extruding)
            return real(anchor, file_position, live_position, paused=paused, extruding=extruding)

        parts.index.observe_motion = record
        # The connected printer reports tiny positive floating-point residue
        # while travelling: it must not open the new layer's extrusion gate.
        for velocity, expected in ((None, None), (0.0, False),
                                   (3.552713678800501e-15, False), (5e-7, False),
                                   (-0.03, False), (0.00001, True), (0.037, True)):
            with self.subTest(velocity=velocity):
                calls.clear()
                self._printing(parts, motion_report={
                    "live_position": [10., 10., 1.2, 0.],
                    "live_extruder_velocity": velocity})
                parts.coordinator.refresh()
                self.assertTrue(calls)
                self.assertIs(calls[-1], expected)

    def test_every_collaborator_signal_reaches_its_handler(self):
        parts = self._make()
        coordinator = parts.coordinator
        before = len(parts.presentation.published)
        parts.files.changed.emit()
        self.assertGreater(len(parts.presentation.published), before)
        parts.presentation.loadRequested.emit()
        self.assertIn({"replacePromptVisible": True}, parts.presentation.published)
        parts.presentation.replaceConfirmed.emit()
        parts.cura.has_toolpath = True
        parts.presentation.attachmentRequested.emit()
        self.assertEqual(parts.preview.attach_calls, [True])
        parts.presentation.clearPausesRequested.emit()
        self.assertEqual(parts.pauses.cleared, 1)
        parts.presentation.pauseAtLayerRequested.emit(4)
        self.assertEqual(parts.pauses.toggles[-1], (3, None, None))
        parts.presentation.removePauseRequested.emit(4)
        self.assertEqual(parts.pauses.removed, [3])
        started = parts.index.requests
        parts.presentation.improveEtaRequested.emit()
        self.assertEqual(parts.index.requests, started + 1)
        parts.preview.controlsChanged.emit()
        parts.pauses.changed.emit()
        parts.pauses.message.emit("note from pauses")
        self.assertEqual(coordinator._detail, "note from pauses")
        parts.client.connectionChanged.emit(False, "Disconnected")
        self.assertEqual(coordinator._detail, "Disconnected")
        parts.client.sessionInvalidated.emit()
        self.assertEqual(coordinator._detail, "Not connected")
        # A service failure is logged, never raised into the caller.
        parts.files.failed.emit("boom")
        parts.index.failed.emit("boom")

    def test_a_printing_observation_binds_every_service_to_the_run(self):
        parts = self._printing(self._make())
        coordinator, files = parts.coordinator, parts.files
        self.assertTrue(coordinator.snapshot.active)
        self.assertEqual(coordinator.snapshot.layer.index, 4)
        self.assertEqual(coordinator.snapshot.layer.total, 100)
        self.assertEqual(coordinator.snapshot.layer.source, "Moonraker current_layer")
        self.assertEqual(files.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.index.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.pauses.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.pauses.observed_layers, [4])
        resets = parts.preview.reset_prints
        # The next poll of the SAME run is not a new job: no reset.
        self._printing(parts)
        self.assertEqual(parts.preview.reset_prints, resets)
        # A new filename re-starts the run and clears the preview state.
        parts.client.statusReceived.emit(harness._status("printing", filename="other.gcode"))
        self.assertEqual(parts.preview.reset_prints, resets + 1)

    def test_a_standby_frame_drops_the_run_identity(self):
        parts = self._printing(self._make())
        parts.client.statusReceived.emit(harness._status("standby"))
        self.assertFalse(parts.coordinator.snapshot.active)
        self.assertIsNone(parts.index.bound)
        self.assertIsNone(parts.files.bound)
        self.assertIsNone(parts.pauses.bound)

    def test_observe_ignores_non_mappings_and_a_closed_coordinator(self):
        coordinator = self._make().coordinator
        coordinator.observe("not a status")
        coordinator.observe(None)
        self.assertFalse(coordinator.snapshot.active)
        coordinator.close()
        coordinator.observe(harness._status())
        self.assertFalse(coordinator.snapshot.active)

    def test_an_unconfigured_load_request_only_explains_itself(self):
        coordinator = self._make(configured=False).coordinator
        coordinator.request_load()
        self.assertEqual(coordinator._detail,
                         "Set a Moonraker URL before loading the current print")
        self.assertFalse(coordinator._loads.load_requested)
        coordinator.download_for_monitor()
        self.assertEqual(coordinator._detail,
                         "Set a Moonraker URL before improving the monitor estimate")
        self.assertFalse(coordinator._loads.monitor_requested)

    def test_a_load_request_ages_out_against_a_standby_printer(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.request_load()
        self.assertTrue(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "Resolving current print…")
        self.assertEqual(parts.client.forced, 1)
        coordinator._loads._load_requested_at = harness.time.monotonic() - 10.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_a_monitor_request_ages_out_against_a_standby_printer(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.download_for_monitor()
        self.assertTrue(coordinator._loads.monitor_requested)
        self.assertEqual(coordinator._detail,
                         "Downloading and indexing the print for the monitor…")
        self.assertEqual(parts.index.requests, 1)
        coordinator._loads._monitor_requested_at = harness.time.monotonic() - 10.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_an_active_print_gets_the_longer_request_window(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads.request_monitor()
        coordinator._loads._monitor_requested_at = harness.time.monotonic() - 3.0
        coordinator.refresh()
        self.assertTrue(coordinator._loads.monitor_requested)  # the 5 s window holds
        coordinator._loads._monitor_requested_at = harness.time.monotonic() - 6.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertNotEqual(coordinator._detail, "No active Moonraker print to load")

    def test_the_toolpath_arrival_nudges_cura_once_and_attaches(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.refresh()
        self.assertEqual((cura.nudges, cura.layer_view_nudges), (1, 1))
        self.assertEqual(parts.preview.attach_calls, [True])
        coordinator.refresh()
        self.assertEqual((cura.nudges, cura.layer_view_nudges), (1, 1))
        # The toolpath going away detaches for real (the 2026-09-17 ruling).
        cura.has_toolpath = False
        coordinator.refresh()
        self.assertEqual(parts.preview.attach_calls, [True, False])
        self.assertFalse(coordinator._had_toolpath)

    def test_a_toolpath_flap_never_overrides_a_deliberate_detach(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.refresh()
        coordinator.toggle_attachment()
        self.assertFalse(parts.preview.state.attached)
        self.assertTrue(coordinator._user_detached)
        for toolpath in (False, True):
            cura.has_toolpath = toolpath
            coordinator.refresh()
        self.assertEqual(parts.preview.attach_calls, [True, False])

    def test_attachment_is_refused_without_a_toolpath(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.toggle_attachment()
        self.assertEqual(parts.preview.attach_calls, [])
        self.assertFalse(coordinator._user_detached)
        # Detaching stays available: the control pins to detached.
        parts.preview.state.attached = True
        coordinator.toggle_attachment()
        self.assertEqual(parts.preview.attach_calls, [False])
        self.assertEqual(parts.client.forced, 0)

    def test_attaching_forces_a_refresh_and_detaching_does_not(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.toggle_attachment()
        self.assertTrue(parts.preview.state.attached)
        self.assertFalse(coordinator._user_detached)
        self.assertEqual(parts.client.forced, 1)

    def test_the_downloaded_header_supplies_the_filament_total(self):
        directory = harness.tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = harness.os.path.join(directory.name, "cube.gcode")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(";Filament used: 1.5m\nG1 X0\n")
        parts = self._printing(self._make())
        parts.files.path = path
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.filament_total, 1500.0)
        # A path yielding no header hands the scrub back to the metadata.
        parts.files.path = harness.os.path.join(directory.name, "missing.gcode")
        parts.files.metadata = {"filament_total": 250.0}
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.filament_total, 250.0)
        # And an unparsable metadata value leaves the total absent.
        parts.files.metadata = {"filament_total": "not-a-number"}
        parts.coordinator.refresh()
        self.assertIsNone(parts.coordinator.snapshot.filament_total)

    def test_a_matching_index_view_arms_the_layer_progress(self):
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        parts.index.plate_split = 50
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertTrue(snapshot.index_ready)
        self.assertEqual(snapshot.layer_progress, 0.5)
        # The readout receives the service's new accepted boundary.
        parts.index.plate_split = 100
        parts.client.statusReceived.emit(
            harness._status("printing", virtual_sdcard={"file_position": 99999}))
        self.assertEqual(parts.coordinator.snapshot.layer_progress, 1.0)
        # A non-numeric position reads as no progress at all.
        parts.client.statusReceived.emit(
            harness._status("printing", virtual_sdcard={"file_position": "many"}))
        self.assertIsNone(parts.coordinator.snapshot.layer_progress)

    def test_an_index_view_for_another_file_is_not_this_print_evidence(self):
        parts = self._printing(self._make())
        parts.client.connected = True
        parts.index.view = harness._view(job_key=("stale.gcode", 100000, 7))
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertFalse(snapshot.index_ready)
        self.assertIsNone(snapshot.layer_progress)
        self.assertIsNone(parts.preview.observed[-1][3])

    def test_the_monitor_download_retires_on_index_error(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.index.phase = "error"
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_the_monitor_download_retires_on_download_error(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.files.phase = "error"
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_the_monitor_download_retires_once_the_index_lands(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.index.view = harness._view()
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_a_pending_load_hands_the_file_lease_to_cura(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files.path = "/downloads/cube.gcode"
        parts.files._lease = "LEASE-1"
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(parts.cura.loads, ["LEASE-1"])
        # A lease that vanished between the read and the load loads nothing.
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files._lease = None
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(parts.cura.loads, ["LEASE-1"])

    def test_a_print_that_changed_mid_load_reports_it(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = ("gone.gcode", 1, 1)
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(coordinator._detail, "Print changed before it could be loaded")

    def test_a_load_waits_while_cura_is_still_busy(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files.path = "/downloads/cube.gcode"
        parts.files._lease = "LEASE-1"
        parts.cura.loading = True
        coordinator.refresh()
        self.assertEqual(coordinator._loads._load_job, coordinator.snapshot.job_key)
        self.assertEqual(parts.cura.loads, [])

    def test_the_trace_line_names_the_resolution_source(self):
        parts = self._make(config=harness.PrinterConfig(trace_layer=True))
        from mpf.application import PrintCoordinator as coordinator_module
        with harness.patch.object(coordinator_module, "Logger") as logger:
            self._printing(parts)
        trace = [call for call in logger.log.call_args_list if "layer trace" in str(call)]
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0].args[-1], "Moonraker current_layer")
        # The 5 s throttle keeps the next poll from repeating it.
        first_at = parts.coordinator._layer_trace_at
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator.refresh()
        self.assertEqual(logger.log.call_args_list, [])
        parts.coordinator._layer_trace_at = first_at - 10.0
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator.refresh()
        self.assertTrue(any("layer trace" in str(call) for call in logger.log.call_args_list))

    def test_a_disconnected_client_invalidates_the_preview_view(self):
        parts = self._printing(self._make())
        before = parts.preview.invalidations
        parts.client.connected = False
        parts.coordinator.refresh()
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertEqual(parts.index.followed, [])

    def test_the_plate_payload_uses_the_fresh_physical_layer(self):
        # The green-printed fix's second half: plate_progress and
        # plate_visited read the FRESH physical layer, never the
        # previous snapshot's — the old code kept the passed set on
        # the outgoing layer across a transition.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        status = harness._status()
        status["print_stats"]["info"]["current_layer"] = 5
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        status["print_stats"]["info"]["current_layer"] = 6
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_anchors, [4, 4, 5, 5],
                         "the plate payload anchored on the previous snapshot's layer")

    def test_a_detach_frozen_on_the_live_layer_carries_no_file_position(self):
        # The live report: detaching froze the CURRENT layer, and the
        # anchor-equality test kept feeding the payload the live file
        # position — the split never stopped, so the detach read as
        # dead. Attached-ness is the test: a frozen anchor is frozen
        # even while the print is still on that layer.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        # The serving gate (the reviewer's C): the frozen payload
        # serves while the popover is open.
        parts.coordinator.set_popover_open(True)
        parts.coordinator.set_plate_anchor(4)  # == the live layer
        parts.coordinator.refresh()
        # TWO payloads per refresh while detached and the popover is
        # open: the live one for the mini, the frozen one for the
        # popover (the live request). The detach's own refresh plus
        # the explicit one: four calls.
        self.assertEqual(parts.index.plate_anchors, [4, 4, 4, 4])
        self.assertIsNone(parts.index.plate_positions[-1],
                          "the frozen layer was still handed the live position")
        self.assertEqual(parts.index.manual_anchor, 4)
        # The scrub rides the same seam.
        parts.coordinator.set_plate_split(37)
        self.assertEqual(parts.index.manual_split, 37)
        # Re-attaching restores the live position flow (one payload).
        parts.coordinator.set_plate_anchor(None)
        parts.coordinator.refresh()
        self.assertIsNone(parts.index.manual_anchor)
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_the_plate_payload_shares_the_resolved_file_position(self):
        # The offset feeds the shared service, whose accepted fraction
        # drives the readout as well as the plate's physical boundary.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        parts.index.plate_split = 50
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertTrue(snapshot.index_ready)
        self.assertEqual(snapshot.layer_progress, 0.5)
        self.assertEqual(parts.index.plate_anchors, [4])
        self.assertEqual(parts.index.plate_positions, [4500])

    def test_a_monitor_only_index_builds_the_plate_payload_without_a_view(self):
        # The Improve-ETA download leaves the index's job key UNRESOLVED
        # (it names the active print, never a loaded file), so the
        # identity gate refuses the Preview view, but the shared physical
        # observation and Monitor readout still use the print's own index.
        parts = self._printing(self._make())
        parts.index.view = harness._view(job_key=())
        parts.index.plate_split = 37
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertFalse(snapshot.index_ready)
        self.assertEqual(snapshot.layer_progress, 0.37)
        self.assertEqual(parts.index.plate_anchors, [4])
        self.assertEqual(parts.index.plate_positions, [4500])
        self.assertIsNotNone(snapshot.plate_progress)
        self.assertEqual(snapshot.plate_progress["anchor"], 4)
        self.assertEqual(snapshot.plate_layer_count, len(parts.index.view.ranges),
                         "monitor-only plate rendered with a zero layer-slider range")

    def test_monitor_only_spiral_uses_the_print_index_to_hold_the_tail(self):
        parts = self._printing(self._make())
        parts.cura.heights = []  # no Preview toolpath for this printer file
        view = harness._view(job_key=(), ranges=((0, 1000), (1000, 2000), (2000, 3000)))
        view.continuous_z_boundary = lambda layer: 0.6 if layer == 2 else None
        parts.index.view = view
        status = harness._status(
            "printing",
            virtual_sdcard={"file_position": 2200, "file_size": 100000, "progress": 0.5},
            gcode_move={"gcode_position": [9.0, 0.0, 0.6, 12.0],
                        "absolute_coordinates": True},
            motion_report={"live_position": [9.0, 0.0, 0.52, 0.0]})
        status["print_stats"]["info"] = {"current_layer": 3, "total_layer": 3}
        parts.client.statusReceived.emit(status)
        self.assertEqual(parts.coordinator.snapshot.layer.index, 1)
        self.assertEqual(parts.index.plate_anchors[-1], 1)
        status["motion_report"]["live_position"][2] = 0.6
        parts.client.statusReceived.emit(status)
        self.assertEqual(parts.coordinator.snapshot.layer.index, 2)
        self.assertEqual(parts.index.plate_anchors[-1], 2)

    def test_an_unresolved_physical_layer_builds_no_plate_payload(self):
        # The print's own layer never resolved while the index exists:
        # there is no anchor, so the plate APIs are never asked — and
        # the position's presence on the status is not one.
        parts = self._make()
        view = harness._view()
        view.layer_at = lambda position: None  # the file position maps to no layer
        parts.index.view = view
        status = harness._status()
        status["print_stats"]["info"] = {}
        parts.client.statusReceived.emit(status)
        snapshot = parts.coordinator.snapshot
        self.assertIsNone(snapshot.layer.index)
        self.assertIsNone(snapshot.plate_progress)
        self.assertEqual(parts.index.plate_anchors, [])
        self.assertEqual(parts.index.plate_positions, [])

    def test_manual_plate_is_served_before_the_live_layer_resolves(self):
        parts = self._make()
        view = harness._view()
        view.layer_at = lambda position: None
        parts.index.view = view
        status = harness._status()
        status["print_stats"]["info"] = {}
        parts.client.statusReceived.emit(status)
        parts.coordinator.set_popover_open(True)
        parts.coordinator.set_plate_anchor(0)
        snapshot = parts.coordinator.snapshot
        self.assertTrue(snapshot.index_ready)
        self.assertIsNone(snapshot.layer.index)
        self.assertIsNone(snapshot.plate_progress,
                          "the live plate invented a physical layer")
        self.assertIsNotNone(snapshot.plate_manual_progress,
                             "manual viewing waited for a physical layer")
        self.assertEqual(snapshot.plate_manual_progress["anchor"], 0)
        self.assertEqual(parts.index.plate_anchors[-1], 0)
        self.assertIsNone(parts.index.plate_positions[-1])
        self.assertEqual(parts.index.manual_anchor, 0)
        parts.coordinator.set_plate_anchor(2)
        self.assertEqual(parts.coordinator.snapshot.plate_manual_progress["anchor"], 2,
                         "manual layer browsing stopped before the live layer resolved")
        self.assertIsNone(parts.coordinator.snapshot.plate_progress)

    def test_an_unchanged_plate_definition_is_not_re_walked_by_a_position_poll(self):
        # The coordinator's plate projection is per-vertex Python work
        # (coordinate validation plus ring decimation) and it ran on
        # EVERY core poll — 80 objects x 300 vertices is ~14 ms a poll,
        # 128 x 1,000 ~80 ms, against a 750 ms cadence. The definition
        # did not change between those polls; only the position and the
        # clock did, and neither is the projection's input.
        parts = self._plate_parts()
        geometry = harness._plate_geometry(80, 300)
        calls, spy = harness._normalisation_spy()
        with spy:
            self._plate_poll(parts, geometry)
            cold = len(calls)
            self.assertEqual(cold, 80, "the cold poll did not walk every ring")
            for position in (4600.0, 4700.0, 4800.0):
                self._plate_poll(parts, geometry, position=position, duration=300.0)
            # Even a re-parsed payload (fresh containers and fresh
            # floats) is the same definition: the judgement reads
            # values, never identity.
            self._plate_poll(parts, geometry, position=4900.0, duration=300.0,
                             reparse=True)
        self.assertEqual(len(calls), cold,
                         "an unchanged definition was walked again by a later poll")
        self.assertEqual(len(parts.index.plate_visited_rows), 5,
                         "a poll served no plate walk at all")
        self.assertEqual(parts.index.plate_visited_rows[-1][1], 500)
        self.assertEqual(len(parts.index.plate_visited_rows[-1][2]), 80,
                         "the walk was handed a shorter row set than the plate holds")

    def test_a_late_define_arrival_is_walked_and_served(self):
        # EXCLUDE_OBJECT_DEFINE runs mid-print on some machines, so a
        # name the projection has never seen can arrive on any poll: a
        # changed definition re-walks the whole payload (the natural
        # sort and the cap are the projection's, not one row's), and
        # the rows the printed-object walk is handed must carry the new
        # object — a projection kept past its definition would leave it
        # unpainted for the rest of the print.
        parts = self._plate_parts()
        geometry = harness._plate_geometry(3, 300)
        calls, spy = harness._normalisation_spy()
        with spy:
            self._plate_poll(parts, geometry)
            cold = len(calls)
            self._plate_poll(parts, geometry)
            self.assertEqual(len(calls), cold)
            geometry["objects"].append(harness._plate_ring("LATE_OBJECT", 300, 40.0))
            self._plate_poll(parts, geometry)
            self.assertEqual(len(calls), cold + 4,
                             "a late DEFINE did not re-walk the definition")
        names = {row["name"] for row in parts.index.plate_visited_rows[-1][2]}
        self.assertIn("LATE_OBJECT", names)
        self.assertEqual(len(names), 4)

    def test_a_changed_ring_is_walked_and_served_fresh(self):
        # The same names with a moved silhouette (a re-define, a
        # re-slice): the walk must judge the NEW coordinates, never the
        # memo's old projection.
        parts = self._plate_parts()
        geometry = harness._plate_geometry(2, 300)
        calls, spy = harness._normalisation_spy()
        with spy:
            self._plate_poll(parts, geometry)
            cold = len(calls)
            self._plate_poll(parts, geometry)
            self.assertEqual(len(calls), cold)
            geometry["objects"][0]["polygon"][0] += 5.0
            self._plate_poll(parts, geometry)
            self.assertEqual(len(calls), cold + 2,
                             "a changed ring did not re-walk the definition")
        rows = {row["name"]: row for row in parts.index.plate_visited_rows[-1][2]}
        self.assertEqual(rows["OBJ_0"]["polygon"][0], [35.0, 20.0])

    def test_a_same_file_restart_drops_the_previous_definition(self):
        # A restart of the SAME file re-defines the same objects, so
        # the geometry alone cannot separate the two runs: the job
        # key's own serial is the signal (a file position that went
        # backwards), and the memo drops the finished print's rows with
        # it rather than carrying them — and their memory — into the
        # next run.
        parts = self._plate_parts()
        geometry = harness._plate_geometry(4, 300)
        calls, spy = harness._normalisation_spy()
        with spy:
            self._plate_poll(parts, geometry, position=4500.0)
            cold = len(calls)
            self._plate_poll(parts, geometry, position=4600.0)
            self.assertEqual(len(calls), cold)
            self._plate_poll(parts, geometry, position=120.0)
            self.assertEqual(len(calls), cold + 4,
                             "the restarted job reused the previous definition")

    def test_the_memoised_refresh_costs_a_fraction_of_the_re_walk(self):
        # The same refresh path both ways: with the pre-fix call site (a
        # stand-in that normalises every poll) and with the memo. The
        # structural assertion lives in the ring-walk spy above; this
        # one pins the ORDER of the win, with a margin wide enough that
        # no plausible machine inverts it. The pre-fix cost is the
        # review's own: ~14 ms a poll at this geometry against a 750 ms
        # cadence, paid on the owner thread.
        geometry = harness._plate_geometry(80, 300)
        raw_cold, raw_steady = self._refresh_cost(geometry, passthrough=True)
        memo_cold, memo_steady = self._refresh_cost(geometry)
        print("plate projection per refresh (80 x 300): "
              "re-walk cold %.2f ms steady %.2f ms; "
              "memoised cold %.2f ms steady %.2f ms"
              % (raw_cold, raw_steady, memo_cold, memo_steady))
        self.assertLess(memo_steady, raw_steady * 0.25,
                        "the memoised refresh cost %.2f ms against the re-walk's %.2f ms"
                        % (memo_steady, raw_steady))

    def test_a_missing_or_invalid_file_position_resolves_to_none(self):
        # Missing, null and non-numeric fields all resolve to None —
        # never to byte 0, which would read as real progress at the
        # head of the layer — and neither consumer acts on it.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        for sdcard in ({}, {"file_position": None}, {"file_position": "many"},
                       {"file_position": [1]}):
            with self.subTest(sdcard=sdcard):
                parts.client.statusReceived.emit(
                    harness._status("printing", virtual_sdcard=sdcard))
                self.assertIsNone(parts.coordinator.snapshot.layer_progress)
                self.assertEqual(parts.index.plate_anchors[-1], 4)
                self.assertIsNone(parts.index.plate_positions[-1])

    def test_the_plate_split_reads_the_toolheads_own_position(self):
        # The painted boundary follows the NOZZLE, so the head's own
        # position must reach the service beside the dispatcher's — read
        # from the same status the Preview's follower reads it from, in
        # the G-code's own coordinate space.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_lives[-1], (10.0, 10.0, 1.2))

    def test_the_live_position_survives_a_pause(self):
        # A pause is where the refinement matters most: the dispatcher
        # sits where it stopped, the pause macro parks the head away
        # from the path. The status keeps flowing, so the position keeps
        # flowing with it and the service can hold its boundary.
        parts = self._make()
        parts.index.view = harness._view()
        status = harness._status("paused")
        status["motion_report"]["live_position"] = [140.0, 140.0, 10.0, 0.0]
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_lives[-1], (140.0, 140.0, 10.0))
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_a_missing_motion_report_hands_no_live_position(self):
        # No telemetry is None, never a fabricated origin: the service
        # then keeps the coarse boundary, exactly as it always did.
        parts = self._printing(self._make(), motion_report={})
        parts.index.view = harness._view()
        parts.coordinator.refresh()
        self.assertIsNone(parts.index.plate_lives[-1])
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_the_plate_anchor_waits_for_the_nozzle_at_a_layer_change(self):
        # The live transition, end to end: the printer reports the layer it
        # has just READ (the parser runs ahead of the move queue, and
        # SET_PRINT_STATS_INFO executes as it is read), the byte offset
        # past the marker, and its own commanded Z — while the nozzle is
        # still physically finishing the PREVIOUS layer. Anchoring the
        # plate to the claimed layer refines the new layer's boundary
        # against the nozzle's place on the old one: a fraction of a layer
        # with nothing printed, held by the service's monotonic floor. The
        # anchor follows the nozzle's own Z instead.
        parts = self._printing(self._make())
        parts.cura.heights = [0.2, 0.4]
        parts.index.view = harness._view(ranges=((0, 1000), (1000, 2000)))
        transition = harness._status(
            "printing",
            virtual_sdcard={"file_position": 1000, "file_size": 100000, "progress": 0.5},
            # The parser's own gcode position is already on layer 1's
            # plane (0.4); only motion_report cannot lead.
            gcode_move={"gcode_position": [9.0, 0.0, 0.4, 12.0],
                        "absolute_coordinates": True},
            motion_report={"live_position": [9.0, 0.0, 0.2, 0.0]})
        transition["print_stats"]["info"] = {"current_layer": 2, "total_layer": 6}
        parts.client.statusReceived.emit(transition)
        self.assertEqual(parts.coordinator.snapshot.layer.index, 0)
        self.assertEqual(parts.index.plate_anchors[-1], 0)
        self.assertEqual(parts.index.plate_lives[-1], (9.0, 0.0, 0.2))
        # The nozzle rises to the new layer's plane: the same claim now
        # anchors, with no extra poll and nothing latched per layer.
        arrived = harness._status(
            "printing",
            virtual_sdcard={"file_position": 1100, "file_size": 100000, "progress": 0.55},
            gcode_move={"gcode_position": [1.0, 0.0, 0.4, 13.0],
                        "absolute_coordinates": True},
            motion_report={"live_position": [1.0, 0.0, 0.4, 0.0]})
        arrived["print_stats"]["info"] = {"current_layer": 2, "total_layer": 6}
        parts.client.statusReceived.emit(arrived)
        self.assertEqual(parts.coordinator.snapshot.layer.index, 1)
        self.assertEqual(parts.index.plate_anchors[-1], 1)

    def test_a_frame_arriving_mid_refresh_cannot_split_the_snapshot(self):
        # A frame really can land while refresh() is mid-flight: observe()
        # is called re-entrantly from the client's signal, and the pass it
        # then starts is not the guarded one (it clears the flag its own
        # nested call set, not the outer refresh's). The emit below
        # therefore arrives between the plate's call and the snapshot it
        # belongs to, and the pass it starts runs to completion inside the
        # one in flight. Before the fix every read after that point — the
        # observation, the mesh, the Preview's frame, the published text —
        # came from the NEW frame while the anchor, the file position and
        # the nozzle place came from the old one: one snapshot of two
        # instants, whose layer and position were never true together, and
        # a published ETA describing a frame the snapshot did not come
        # from. Each pass now resolves from the frame it started on.
        parts = self._printing(self._make())
        parts.index.view = harness._view()
        late = harness._status("paused", filename="cube.gcode", print_duration=200.0,
                       virtual_sdcard={"file_position": 90000, "file_size": 100000,
                                       "progress": 0.9},
                       gcode_move={"gcode_position": [140.0, 140.0, 9.0, 500.0],
                                   "absolute_coordinates": True},
                       motion_report={"live_position": [140.0, 140.0, 9.0, 0.0]})
        late["print_stats"]["info"] = {"current_layer": 60, "total_layer": 100}
        calls = []
        real = parts.index.plate_progress

        def reentrant(anchor, file_position=None, live_position=None, paused=False, extruding=None, *, motion=...):
            calls.append((anchor, file_position, live_position, paused))
            if len(calls) == 1:
                parts.client.statusReceived.emit(late)
            return real(anchor, file_position, live_position, paused=paused, extruding=extruding, motion=motion)

        parts.index.plate_progress = reentrant
        parts.coordinator.refresh()
        # Two passes: the one in flight, then the one the late frame
        # started. Each is anchored, positioned and refined from ONE
        # frame, including its pause bit. The late pause preserves the
        # accepted layer despite its parser's lookahead claim of 59.
        self.assertEqual(calls, [(4, 4500, (10.0, 10.0, 1.2), False),
                                 (4, 90000, (140.0, 140.0, 9.0), True)])
        # And the refresh that was in flight lands on its OWN frame's
        # observation rather than the late one's, so the snapshot's layer
        # and its byte offset describe the same instant.
        snapshot = parts.coordinator.snapshot
        self.assertEqual(snapshot.layer.index, 4)
        self.assertEqual(snapshot.observation.file_position, 4500)
        self.assertEqual(snapshot.observation.state, "printing")
        self.assertNotEqual(parts.presentation.published[-1]["previewEtaText"], "Paused",
                            "the published text describes the frame the snapshot came from")

    def test_no_plate_path_raises_across_the_position_variants(self):
        # Every combination the unbound-position defect could reach,
        # replayed on one coordinator: each poll completes and
        # publishes. The view-absent variant raised UnboundLocalError
        # before the position was resolved ahead of both consumers.
        parts = self._make()

        def layerless_status():
            status = harness._status()
            status["print_stats"]["info"] = {}
            return status

        def unmapped_view():
            view = harness._view()
            view.layer_at = lambda position: None
            return view

        variants = (
            ("view and position", harness._view(), harness._status()),
            ("monitor-only index", harness._view(job_key=()), harness._status()),
            ("no physical layer", unmapped_view(), layerless_status()),
            ("missing position", harness._view(), harness._status("printing", virtual_sdcard={})),
            ("null position", harness._view(),
             harness._status("printing", virtual_sdcard={"file_position": None})),
            ("non-numeric position", harness._view(),
             harness._status("printing", virtual_sdcard={"file_position": "many"})),
            ("no index at all", None, harness._status()),
        )
        for label, view, status in variants:
            with self.subTest(label):
                parts.index.view = view
                parts.client.statusReceived.emit(status)
                parts.coordinator.refresh()
                self.assertIsNotNone(parts.coordinator.snapshot)
                self.assertTrue(parts.presentation.published)

    def test_a_connected_client_publishes_the_followed_layer_and_hydration(self):
        parts = self._printing(self._make())
        parts.client.connected = True
        parts.preview.observe_result = ("Following", (3, 5))
        parts.coordinator.refresh()
        self.assertEqual(parts.index.followed, [4])
        self.assertEqual(parts.index.hydration, [3, 5])
        self.assertEqual(parts.coordinator._detail, "Following")

    def test_the_pause_rows_are_built_once_and_reused_by_a_bare_publish(self):
        parts = self._printing(self._make())
        parts.pauses.layers = {7}
        parts.pauses.states = {7: "scheduled"}
        parts.coordinator.refresh()
        built = len(parts.preview.remaining_calls)
        self.assertGreater(built, 0)
        parts.coordinator._publish()
        self.assertEqual(len(parts.preview.remaining_calls), built)
        # The states join the cache key: a fired pause rebuilds the rows.
        parts.pauses.states = {7: "fired"}
        parts.coordinator._publish()
        self.assertGreater(len(parts.preview.remaining_calls), built)

    def test_the_published_load_phase_tracks_each_terminal_phase(self):
        parts = self._make()
        coordinator = parts.coordinator

        def published():
            coordinator.refresh()
            return parts.presentation.published[-1]

        parts.files.phase = "downloading"
        parts.files.download_fraction = 0.4
        self.assertEqual(published()["loadPhase"], "Downloading…")
        self.assertEqual(published()["loadProgress"], 0.4)
        parts.files.phase = "resolving"
        self.assertEqual(published()["loadPhase"], "Resolving…")
        parts.files.phase = ""
        parts.files.download_fraction = None
        parts.index.phase = "indexing"
        parts.index.progress = 0.25
        result = published()
        self.assertEqual(result["loadPhase"], "Indexing…")
        self.assertEqual(result["loadProgress"], 0.25)
        parts.index.phase = ""
        parts.cura.loading = True
        self.assertEqual(published()["loadPhase"], "Rendering…")
        parts.cura.loading = False
        coordinator._loads.request_load()
        self.assertEqual(published()["loadPhase"], "Resolving current print…")
        coordinator._loads.reset()
        self.assertEqual(published()["loadProgress"], -1.0)

    def test_a_baked_pause_blocks_the_manual_toggle_for_that_layer(self):
        parts = self._make()
        parts.index.view = harness._view(pause_layers=(5,))
        parts.cura.selected_layer = 5
        parts.coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertFalse(block["pauseAtLayerCanToggle"])
        self.assertEqual(block["pauseAtLayerUnavailableText"],
                         "a pause is baked into the gcode at this layer")
        self.assertTrue(block["pauseAtLayerHasBaked"])
        # The backstop: the request is refused however it arrived.
        parts.coordinator.toggle_pause(6)
        self.assertEqual(parts.pauses.toggles, [])

    def test_the_pause_total_falls_back_to_cura_max_layer(self):
        parts = self._make()
        parts.cura.max_layer = 99
        parts.coordinator.toggle_pause(50)
        self.assertEqual(parts.pauses.toggles, [(49, None, 100)])
        parts.coordinator._publish()
        self.assertEqual(parts.presentation.published[-1]["pauseAtLayerCandidate"], 0)

    def test_a_scene_invalidation_does_not_abort_the_load(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.request_load()
        before = parts.preview.invalidations
        parts.cura.invalidated.emit("stage swapped")
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertTrue(coordinator._loads.load_requested)

    def test_a_view_swap_restores_attachment_only_with_a_toolpath(self):
        parts = self._make()
        parts.preview.state.attached = True
        parts.cura.has_toolpath = True
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [True])
        # Without a toolpath to drive, a swap leaves the follower detached.
        parts.preview.attach_calls.clear()
        parts.cura.has_toolpath = False
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [])
        # A swap while detached is navigation, never a re-attach.
        parts.preview.state.attached = False
        parts.cura.has_toolpath = True
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [])

    def test_an_installed_index_resets_tracking_while_it_builds(self):
        parts = self._make()
        parts.index.phase = "indexing"
        parts.index.changed.emit()
        self.assertEqual(parts.preview.reset_trackings, 1)
        parts.index.phase = "ready"
        parts.index.changed.emit()
        self.assertEqual(parts.preview.reset_trackings, 1)

    def test_position_changes_are_throttled_and_reanchor_the_eta(self):
        parts = self._make()
        coordinator = parts.coordinator
        parts.preview.override = True
        parts.cura.positionChanged.emit()
        self.assertEqual(coordinator._detail, "Detached")
        published = len(parts.presentation.published)
        coordinator._publish_at = harness.time.monotonic() + 10.0
        parts.cura.positionChanged.emit()
        self.assertEqual(len(parts.presentation.published), published)
        # Past the throttle with a matching view: the ETA is re-anchored.
        coordinator._publish_at = 0.0
        parts.index.view = harness._view()
        parts.files.job_key = ("cube.gcode", 100000, 1)
        parts.preview.remaining_end_value = 600.0
        parts.cura.positionChanged.emit()
        self.assertEqual(coordinator.snapshot.layer_eta, 600.0)
        self.assertEqual(parts.preview.eta_updates[-1][1], parts.index.view)

    def test_a_position_change_ignores_a_view_for_another_file(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator._publish_at = 0.0
        parts.index.view = harness._view(job_key=("other.gcode", 1, 1))
        parts.files.job_key = ("cube.gcode", 100000, 1)
        parts.preview.remaining_end_value = 600.0
        parts.cura.positionChanged.emit()
        self.assertIsNone(coordinator.snapshot.layer_eta)
        self.assertEqual(parts.presentation.published[-1]["selectedLayerEtaText"], "")

    def test_position_changes_name_no_override_with_the_gate_off(self):
        parts = self._make(config=harness.PrinterConfig(enabled=False))
        parts.preview.override = True
        parts.cura.positionChanged.emit()
        self.assertNotEqual(parts.coordinator._detail, "Detached")

    def test_a_loaded_file_invalidates_and_a_failed_load_explains_itself(self):
        parts = self._make()
        coordinator = parts.coordinator
        before = parts.preview.invalidations
        parts.cura.fileLoaded.emit("/downloads/cube.gcode")
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertEqual(parts.client.forced, 1)
        coordinator._loads._load_job = ("cube.gcode", 1, 1)
        parts.cura.loadFailed.emit("no disk space")
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(coordinator._detail, "Could not load current print: no disk space")

    def test_confirm_load_switches_the_stage_and_asks_in_the_card(self):
        parts = self._make()
        parts.coordinator.confirm_load()
        self.assertEqual(parts.cura.stages, ["PreviewStage"])
        self.assertIn({"replacePromptVisible": True}, parts.presentation.published)
        self.assertFalse(parts.coordinator._loads.load_requested,
                         "the load waits for the user's answer")

    def test_the_prompt_runs_the_load_the_user_agreed_to(self):
        parts = self._make()
        parts.coordinator.confirm_load()
        parts.presentation.replaceConfirmed.emit()
        self.assertTrue(self._accept(lambda: parts.coordinator._loads.load_requested))

    def test_the_prompt_is_dismissed_by_either_answer(self):
        parts = self._make()
        parts.coordinator.confirm_load()
        parts.presentation.replaceCancelled.emit()
        parts.presentation.replaceConfirmed.emit()
        self.assertEqual(parts.presentation.published[-2:],
                         [{"replacePromptVisible": False},
                          {"replacePromptVisible": False}])
        self.assertFalse(parts.coordinator._loads.load_requested,
                         "Cancel is not an answer to load")
        self.assertIsNone(parts.coordinator._replace_action,
                          "the pending load is released, not left armed")

    def test_an_answered_prompt_cannot_run_its_load_twice(self):
        # The popup's buttons are live until the publish lands, and a
        # double press used to be two loads.
        parts = self._make()
        parts.coordinator.confirm_load()
        parts.presentation.replaceConfirmed.emit()
        self.assertTrue(self._accept(lambda: parts.coordinator._loads.load_requested))
        before = parts.client.forced
        parts.presentation.replaceConfirmed.emit()
        self._pump(0.05)
        self.assertEqual(parts.client.forced, before,
                         "the second press ran the load again")

    def test_an_answered_prompt_does_not_load_into_a_closed_plugin(self):
        # The load is deferred one turn, and shutdown wins that race:
        # a plugin closed while the answer was in flight must not
        # start a load it will not finish.
        parts = self._make()
        parts.coordinator.confirm_load()
        parts.coordinator.close()
        parts.presentation.replaceConfirmed.emit()
        self._pump(0.1)
        self.assertFalse(parts.coordinator._loads.load_requested)

    def test_the_preview_block_keeps_the_newest_stamp(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator.receive_preview_block("not a mapping")
        self.assertIsNone(coordinator._preview_block)
        coordinator.receive_preview_block({"stamp": 10.0, "state": "old"})
        coordinator.receive_preview_block({"stamp": 4.0, "state": "stale"})
        self.assertEqual(coordinator._preview_block[0]["state"], "old")
        coordinator.receive_preview_block({"stamp": 12.0, "state": "new"})
        self.assertEqual(coordinator._preview_block[0]["state"], "new")
        coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertEqual(block["previewBlock"]["state"], "new")
        self.assertTrue(block["previewBlockStale"])  # the feed is down
        parts.client.connected = True
        coordinator._preview_block = ({"state": "new"}, harness.time.monotonic())
        coordinator._publish()
        self.assertFalse(parts.presentation.published[-1]["previewBlockStale"])
        # An aged block is stale even on a live connection.
        coordinator._preview_block = ({"state": "new"},
                                      harness.time.monotonic() - coordinator.PREVIEW_BLOCK_STALE_S - 1)
        coordinator._publish()
        self.assertTrue(parts.presentation.published[-1]["previewBlockStale"])

    def test_a_bare_publish_still_reports_the_stage_and_the_readouts(self):
        parts = self._make()
        parts.cura.preview_active = True
        parts.cura.has_toolpath = True
        parts.cura.scene_has_objects = True
        parts.binding.identity = ("http://printer", "Voron")
        parts.index.view = harness._view(pause_layers=(9,))
        parts.coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertTrue(block["previewStageActive"])
        self.assertEqual(block["activePrinterName"], "Voron")
        self.assertTrue(block["hasToolpath"])
        self.assertTrue(block["sceneHasObjects"])
        self.assertEqual(block["pauseAtLayerSummary"], "End-of-layer PAUSE: 10")
        self.assertTrue(block["pauseAtLayerHasBaked"])
        self.assertFalse(block["pauseAtLayerHasClearable"])
        self.assertFalse(block["layerReadoutAvailable"])
        self.assertEqual(block["layerReadoutText"], "—")
        self.assertFalse(block["heightReadoutAvailable"])
        self.assertEqual(block["heightReadoutText"], "—")

    def test_the_gate_diagnostics_log_only_on_change(self):
        parts = self._make()
        from mpf.application import PrintCoordinator as coordinator_module
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator._publish()
            messages = [str(call) for call in logger.log.call_args_list]
            # Two diagnostics lines: the card's gates and the phases.
            self.assertEqual(len(messages), 2)
            self.assertTrue(any("preview card gates" in message
                                for message in messages))
            self.assertTrue(any("preview card phases" in message
                                for message in messages))
            parts.coordinator._publish()
        self.assertEqual(len(logger.log.call_args_list), 2)
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.cura.has_toolpath = True
            parts.coordinator._publish()
        self.assertTrue(any("preview card gates" in str(call)
                            for call in logger.log.call_args_list))
        # The phase line answers to a phase, not to a gate: the camera's
        # own summary is a timestamp with no idea which phase it fell in.
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.index.phase = "indexing"
            parts.coordinator._publish()
        messages = [str(call) for call in logger.log.call_args_list]
        self.assertTrue(any("preview card phases" in message
                            for message in messages))
        self.assertFalse(any("preview card gates" in message
                             for message in messages))
        args = logger.log.call_args_list[-1][0]
        self.assertIn("index=indexing", args[1] % tuple(args[2:]))

    def test_close_silences_every_later_publication(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.close()
        published = len(parts.presentation.published)
        coordinator.refresh()
        coordinator._publish()
        parts.pauses.changed.emit()
        self.assertEqual(len(parts.presentation.published), published)
        self.assertFalse(coordinator._processing)

    def test_reset_binding_clears_the_whole_session(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = ("x", 1, 1)
        coordinator._loads.request_load()
        coordinator._loads.request_monitor()
        coordinator._header_total_mm = 100.0
        coordinator._next_pause._anchor_elapsed = 30.0
        coordinator._next_pause._anchor_job = coordinator.snapshot.job_key
        coordinator._next_pause._last_index = 4
        coordinator._user_detached = True
        coordinator._mr_meta = {"layer_height": 0.2}
        coordinator._mr_meta_key = ("cube.gcode", coordinator.snapshot.job_key)
        coordinator._mr_meta_checks = 2
        coordinator._next_pause._prev_state = "printing"
        parts.cura.has_toolpath = True
        coordinator.reset_binding()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertFalse(coordinator._loads.load_requested)
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertIsNone(coordinator._header_total_mm)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)
        self.assertIsNone(coordinator._next_pause._anchor_job)
        self.assertIsNone(coordinator._next_pause._prev_state)
        self.assertIsNone(coordinator._next_pause._last_index)
        self.assertFalse(coordinator._user_detached)
        self.assertEqual(coordinator._mr_meta, {})
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(coordinator._detail, "Not connected")
        self.assertFalse(coordinator.snapshot.active)
        self.assertIsNone(parts.pauses.bound)
        self.assertIsNone(parts.index.bound)
        self.assertIsNone(parts.files.bound)
        self.assertEqual(parts.bed_mesh.clears, 1)
        self.assertEqual(parts.cura.invalidated_reasons, ["active printer binding changed"])
        # A fresh binding with a toolpath already in Cura keeps following.
        self.assertEqual(parts.preview.attach_calls[-1], True)

    def test_a_dropped_metadata_send_leaves_no_identity_behind(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        files.metadata_only_started = False
        coordinator._maybe_fetch_mr_metadata("cube.gcode", ("cube.gcode", 100000, 1))
        self.assertEqual(coordinator._mr_meta_asked, ("", ""))
        self.assertEqual(coordinator._mr_meta_at, 0.0)
        self.assertFalse(coordinator._mr_meta_pending)

    def test_the_metadata_fetch_is_single_flight_and_throttled(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)
        self.assertTrue(coordinator._mr_meta_pending)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)  # one request at a time
        coordinator._mr_meta_pending = False
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)  # inside the 30 s window
        coordinator._mr_meta_at = harness.time.monotonic() - 31.0
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 2)
        # A new key re-arms the give-up counter and skips the window.
        coordinator._mr_meta_pending = False
        coordinator._mr_meta_checks = 2
        coordinator._maybe_fetch_mr_metadata("other.gcode", job)
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(len(files.metadata_only), 3)

    def test_a_latched_payload_retires_the_whole_ladder(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"result": {"job_id": None, "layer_height": 0.2,
                                            "filament_total": 100.0}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertEqual(coordinator._mr_metadata_for("cube.gcode", job)["layer_height"], 0.2)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)

    def test_a_named_job_id_rides_the_history_cross_check(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        self.assertEqual(len(parts.client.sent_json), 1)
        self.assertEqual(parts.client.sent_json[0][1], "mr-history")
        self.assertEqual(parts.client.sent_json[0][3],
                         "server/history/list?limit=1&order=desc")
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        parts.client.reply({"result": {"jobs": [{"job_id": 77}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_a_mismatched_job_id_is_never_latched(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        from mpf.application import PrintCoordinator as coordinator_module
        with harness.patch.object(coordinator_module, "Logger") as logger:
            parts.client.reply({"result": {"jobs": [{"job_id": 99}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertTrue(any("refused" in str(call) for call in logger.log.call_args_list))

    def test_an_unattestable_check_gives_up_after_the_limit(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        key = ("cube.gcode", job)
        limit = coordinator.MR_META_CHECK_LIMIT
        for step in range(limit):
            coordinator._mr_meta_at = 0.0
            coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
            files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
            parts.client.reply({"result": {"jobs": []}}, None)
            if step < limit - 1:
                self.assertEqual(coordinator._mr_meta_key, ("", ""))
                self.assertEqual(coordinator._mr_meta_checks, step + 1)
        self.assertEqual(coordinator._mr_meta_key, key)
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(coordinator._mr_metadata_for(*key)["layer_height"], 0.2)

    def test_a_failed_history_request_also_gives_up_after_the_limit(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        for _ in range(coordinator.MR_META_CHECK_LIMIT):
            coordinator._mr_meta_at = 0.0
            coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
            files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
            parts.client.reply(None, "connection lost")
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertFalse(coordinator._mr_meta_pending)

    def test_a_failed_metadata_fetch_never_latches(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"error": "boom"}, "boom")
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertFalse(coordinator._mr_meta_pending)
        # A payload that is not a mapping is refused the same way.
        coordinator._mr_meta_at = 0.0
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"result": ["not", "a", "mapping"]}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_asked, ("cube.gcode", job))

    def test_a_superseded_history_reply_never_latches(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        stale = parts.client.reply
        coordinator.reset_binding()
        stale({"result": {"jobs": [{"job_id": 77}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_the_next_pause_target_and_fraction_follow_the_deadlines(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        parts.index.view = harness._view()
        parts.pauses.layers = {9}
        parts.preview.remaining_value = 300.0
        coordinator.refresh()
        snapshot = coordinator.snapshot
        self.assertEqual(snapshot.next_pause_layer, 10)
        self.assertAlmostEqual(snapshot.next_pause_fraction, 120.0 / 420.0, places=4)
        # The compute can build its own rows when the caller has none.
        self.assertEqual(coordinator._next_pause.compute(snapshot.layer, 120.0)[0], 10)
        # An exhausted remaining with time left short-circuits to a full bar.
        parts.preview.remaining_value = -500.0
        coordinator.refresh()
        self.assertEqual(coordinator.snapshot.next_pause_fraction, 1.0)
        # A row BEHIND the current layer is never the target.
        parts.pauses.layers = {2}
        parts.preview.remaining_value = 300.0
        coordinator.refresh()
        self.assertIsNone(coordinator.snapshot.next_pause_layer)
        self.assertEqual(coordinator.snapshot.next_pause_eta, "")

    def test_a_superseded_metadata_reply_is_dropped(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        pending = files.metadata_only[-1]
        coordinator.reset_binding()
        pending({"result": {"job_id": None}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_a_closed_coordinator_drops_a_late_metadata_reply(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        pending = files.metadata_only[-1]
        coordinator.close()
        pending({"result": {"job_id": None}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))

    def test_the_bed_mesh_observation_reaches_the_presenter(self):
        parts = self._printing(self._make(), bed_mesh={"mesh_min": [0, 0], "mesh_max": [10, 10],
                                                       "probed_matrix": [[0.1]]})
        self.assertEqual(len(parts.bed_mesh.updates), 1)
        self.assertEqual(parts.cura.watched, [True])

    def test_the_metadata_and_index_pull_start_only_with_a_toolpath(self):
        parts = self._printing(self._make())
        parts.coordinator.refresh()
        self.assertEqual((parts.files.metadata_requests, parts.index.requests), (0, 0))
        parts.cura.has_toolpath = True
        parts.coordinator.refresh()
        self.assertGreaterEqual(parts.files.metadata_requests, 1)
        self.assertGreaterEqual(parts.index.requests, 1)

    def test_a_disabled_follower_never_requests_the_index(self):
        parts = self._printing(self._make(config=harness.PrinterConfig(path_follow=False)))
        parts.cura.has_toolpath = True
        parts.coordinator.refresh()
        self.assertEqual(parts.index.requests, 0)

    def test_a_load_request_with_an_active_print_waits_for_the_file(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator.request_load()
        self.assertTrue(coordinator._loads.load_requested)
        coordinator.observe(harness._status("printing"))
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._loads._load_job, coordinator.snapshot.job_key)
        self.assertEqual(parts.files.file_requests, [True])

    def test_a_load_request_with_no_active_print_explains_itself(self):
        coordinator = self._make().coordinator
        coordinator.request_load()
        coordinator.observe(harness._status("standby"))
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_a_non_numeric_metadata_estimate_is_ignored(self):
        parts = self._printing(self._make())
        parts.files.metadata = {"estimated_time": "not-a-number"}
        parts.coordinator.refresh()
        self.assertIsNone(parts.coordinator.snapshot.estimated_time)
        parts.files.metadata = {"estimated_time": 3600.0}
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.estimated_time, 3600.0)

    def test_a_non_numeric_print_duration_reads_as_zero(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        parts.client.statusReceived.emit(harness._status("paused"))
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 120.0)
        before = coordinator.snapshot.job_key
        parts.client.statusReceived.emit(harness._status("printing", print_stats={
            "state": "printing", "filename": "cube.gcode", "print_duration": "ages",
            "info": {"current_layer": 5, "total_layer": 100}}))
        # The bad duration was swallowed as 0.0, and a rewind to zero is
        # a restart: the run identity advances and the anchor clears.
        self.assertNotEqual(coordinator.snapshot.job_key, before)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)

    def test_the_pause_anchor_holds_the_last_pause_and_clears_on_a_new_job(self):
        coordinator = self._make().coordinator
        coordinator._next_pause.update_anchor("cube.gcode", "printing", 100.0)
        coordinator._next_pause.update_anchor("cube.gcode", "paused", 140.0)
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 140.0)
        # Still paused: a later poll never re-stamps the anchor.
        coordinator._next_pause.update_anchor("cube.gcode", "paused", 200.0)
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 140.0)
        # A new job clears the anchor AND the last known index.
        coordinator._next_pause._last_index = 12
        coordinator._next_pause.update_anchor("other.gcode", "printing", 5.0)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)
        self.assertIsNone(coordinator._next_pause._last_index)

    def test_a_resolver_that_drops_to_none_keeps_the_last_index(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        # The frame that OPENS a run clears the remembered index with the
        # anchor; a later frame of the same run is what records it.
        self.assertIsNone(coordinator._next_pause._last_index)
        self._printing(parts)
        self.assertEqual(coordinator._next_pause._last_index, 4)
        parts.client.statusReceived.emit(harness._status("printing", print_stats={
            "state": "printing", "filename": "cube.gcode", "print_duration": 130.0,
            "info": {"current_layer": 0, "total_layer": 100}}))
        self.assertIsNone(coordinator.snapshot.layer.index)
        self.assertEqual(coordinator._next_pause._last_index, 4)

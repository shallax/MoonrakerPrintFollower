"""The 4.5.0 multi-printer ownership battery (the pre-release review): a
cached monitor must never reactivate after a shared-client reconnect, and
the Preview presentation routes through the current machine's monitor
only."""

from unittest.mock import Mock, patch

from test_output_device_coverage import OutputDeviceTestCase


class MonitorOwnershipTests(OutputDeviceTestCase):
    """The review's shared-client sequence, verbatim, plus the routing
    and cycle proofs. The fixture's ScriptedTransport client exposes the
    real pyqtSignals, so connectionChanged/sessionInvalidated are
    emitted directly, exactly as the shared client would."""

    def _install(self):
        app = self.qt.Application()
        client = self.client()
        follower = self.follower(client, self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.refresh()
        return app, client, follower, plugin

    def _switch(self, app, follower, plugin, machine_id):
        app.stack = self.qt.Machine(machine_id)
        follower._identity = lambda: (machine_id, "Printer " + machine_id)
        plugin.refresh()
        return plugin._current.activePrinter

    def test_a_shared_reconnect_never_reactivates_a_deposed_monitor(self):
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter

        monitor_b = self._switch(app, follower, plugin, "B")
        self.assertFalse(monitor_a._data._active)
        self.assertTrue(monitor_b._data._active)

        # The shared client reconnects to B: the deposed A receives the
        # same connectionChanged(True) and must stay inactive.
        client.connectionChanged.emit(True, "connected over http")
        self.qt.events(10)
        self.assertFalse(monitor_a._data._active)
        self.assertTrue(monitor_b._data._active)
        # Ownership is the gate, not the runtime state.
        self.assertTrue(monitor_b._data._owner_active)
        self.assertFalse(monitor_a._data._owner_active)

    def test_only_the_current_monitor_routes_the_preview_intents(self):
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter
        monitor_b = self._switch(app, follower, plugin, "B")

        a_thresholds = Mock()
        b_thresholds = Mock()
        monitor_a.setBedMeshThresholds = a_thresholds
        monitor_b.setBedMeshThresholds = b_thresholds
        follower.presentation.bedMeshThresholdsRequested.emit(0.2, 0.8)
        self.assertEqual(b_thresholds.call_args, ((0.2, 0.8),))
        self.assertFalse(a_thresholds.called)

        a_pause = Mock()
        b_pause = Mock()
        monitor_a.stripPausePrint = a_pause
        monitor_b.stripPausePrint = b_pause
        follower.presentation.printPauseRequested.emit()
        self.assertTrue(b_pause.called)
        self.assertFalse(a_pause.called)

        # A's verdict change after the switch cannot overwrite the
        # Preview pause/resume verdicts; B's can.
        pushes = []
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor_a.actionChanged.emit()
        self.assertEqual(pushes, [])
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor_b.actionChanged.emit()
        self.assertEqual(len(pushes), 1)

        # A's preview block after the switch cannot overwrite the
        # current printer's block; B's can.
        monitor_a.previewBlockChanged.emit({"remainder": 99})
        self.assertEqual(follower.blocks, [])
        monitor_b.previewBlockChanged.emit({"remainder": 12})
        self.assertEqual(follower.blocks, [{"remainder": 12}])

    def test_switching_back_routes_the_cached_monitor_again(self):
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter
        monitor_b = self._switch(app, follower, plugin, "B")

        monitor_a_again = self._switch(app, follower, plugin, "A")
        self.assertIs(monitor_a_again, monitor_a)  # the cached monitor
        self.assertTrue(monitor_a._data._active)
        self.assertFalse(monitor_b._data._active)

        pushes = []
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor_b.actionChanged.emit()
        self.assertEqual(pushes, [])
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor_a.actionChanged.emit()
        self.assertEqual(len(pushes), 1)

        monitor_b.previewBlockChanged.emit({"remainder": 98})
        self.assertEqual(follower.blocks, [])
        monitor_a.previewBlockChanged.emit({"remainder": 11})
        self.assertEqual(follower.blocks, [{"remainder": 11}])

    def test_a_deposed_monitor_retires_its_open_chart(self):
        # A(open chart) -> B -> A(cached reuse) must not silently
        # resume full-history construction: the pop-over's hydration
        # state is transient UI and retires with the ownership.
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter

        def chart_of(monitor):
            chart = monitor.temperatureChartFull
            return chart if isinstance(chart, dict) else chart.value()

        # Enough history for a non-empty chart, then open it.
        monitor_a._history.observe(
            {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}},
            1000.0, 1000000.0)
        monitor_a._history.observe(
            {"extruder": {"temperature": 200.5, "target": 210.0, "power": 0.5}},
            1002.5, 1000002.5)
        monitor_a.setChartOpen(True)
        self.assertTrue(chart_of(monitor_a)["series"], "the open chart hydrates")

        self._switch(app, follower, plugin, "B")
        self.assertFalse(monitor_a._data._active)
        self.assertFalse(monitor_a._chart_open,
                         "ownership loss must retire the open chart")
        self.assertEqual(chart_of(monitor_a)["series"], [],
                         "the deposed monitor's full payload must be dormant")

        monitor_a_again = self._switch(app, follower, plugin, "A")
        self.assertIs(monitor_a_again, monitor_a)  # the cached monitor
        self.assertEqual(chart_of(monitor_a_again)["series"], [],
                         "the cached monitor must not rehydrate on reactivation")
        # Further feeds keep it dormant.
        monitor_a._history.observe(
            {"extruder": {"temperature": 201.0, "target": 210.0, "power": 0.5}},
            1005.0, 1000005.0)
        monitor_a._schedule_publish()
        self.qt.events(10)
        self.assertEqual(chart_of(monitor_a)["series"], [],
                         "feeds must not rebuild the full payload while no chart is open")
        # An explicit open hydrates; closing returns to dormancy.
        monitor_a.setChartOpen(True)
        self.assertTrue(chart_of(monitor_a)["series"])
        monitor_a.setChartOpen(False)
        self.assertEqual(chart_of(monitor_a)["series"], [])

    def test_an_owned_open_chart_survives_a_session_reset(self):
        # Owned + open chart + session reset -> reconnect: the chart
        # must repopulate WITHOUT a second open gesture. A session
        # invalidation clears the stale history but must not retire
        # the pop-over's hydration state — that retires only with
        # ownership (the deposed-monitor regression above).
        app, client, follower, plugin = self._install()
        monitor = plugin._current.activePrinter

        def chart_of(model):
            chart = model.temperatureChartFull
            return chart if isinstance(chart, dict) else chart.value()

        monitor._history.observe(
            {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}},
            1000.0, 1000000.0)
        monitor._history.observe(
            {"extruder": {"temperature": 200.5, "target": 210.0, "power": 0.5}},
            1002.5, 1000002.5)
        monitor.setChartOpen(True)
        self.assertTrue(chart_of(monitor)["series"], "the open chart hydrates")

        # The transport/session invalidation: ownership stays, the
        # runtime suspends, the stale history clears.
        client.sessionInvalidated.emit()
        self.qt.events(10)
        self.assertTrue(monitor._data._owner_active,
                        "the session reset must not revoke ownership")
        self.assertTrue(monitor._chart_open,
                        "a session reset must not close a visibly open chart")
        self.assertEqual(chart_of(monitor)["series"], [],
                         "the session reset cleared the stale history")

        # The reconnect re-arms the same owned monitor.
        client.connectionChanged.emit(True, "connected over http")
        self.qt.events(10)
        # Fresh samples repopulate the open chart automatically — no
        # second setChartOpen(True) anywhere in this test.
        monitor._history.observe(
            {"extruder": {"temperature": 202.0, "target": 210.0, "power": 0.5}},
            1005.0, 1000005.0)
        monitor._history.observe(
            {"extruder": {"temperature": 202.5, "target": 210.0, "power": 0.5}},
            1007.5, 1000007.5)
        monitor._schedule_publish()
        self.qt.events(10)
        self.assertTrue(chart_of(monitor)["series"],
                        "the reconnect feed must repopulate the open chart on its own")

    def test_repeated_switches_accumulate_no_handlers(self):
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter

        for machine_id in ("B", "A", "B"):
            self._switch(app, follower, plugin, machine_id)
            client.connectionChanged.emit(True, "reconnected")
            self.qt.events(10)
            if machine_id == "A":
                self.assertTrue(monitor_a._data._active)
            else:
                self.assertFalse(monitor_a._data._active)

        monitor_b = plugin._current.activePrinter
        self.assertTrue(monitor_b._data._active)
        self.assertFalse(monitor_a._data._active)

        # One emission from the current monitor: exactly ONE dispatch,
        # not one per switch cycle.
        pushes = []
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor_b.actionChanged.emit()
        self.assertEqual(len(pushes), 1)
        follower.blocks.clear()
        monitor_b.previewBlockChanged.emit({"remainder": 12})
        self.assertEqual(follower.blocks, [{"remainder": 12}])

    def test_the_current_monitor_reams_after_a_session_invalidation(self):
        app, client, follower, plugin = self._install()
        monitor = plugin._current.activePrinter
        self.assertTrue(monitor._data._active)

        # A transport/session invalidation suspends the RUNTIME only —
        # ownership stays, so the reconnect re-arms the same monitor.
        client.sessionInvalidated.emit()
        self.qt.events(10)
        self.assertFalse(monitor._data._active)
        self.assertTrue(monitor._data._owner_active)

        client.connectionChanged.emit(True, "connected over http")
        self.qt.events(10)
        self.assertTrue(monitor._data._active)

    def test_a_deposed_monitor_cannot_regrant_ownership_through_reconnect(self):
        # The ownership close-out: ONLY the plugin grants ownership.
        # A cached stale monitor's manual Reconnect must be a no-op —
        # it used to self-grant through set_active(True) and re-claim
        # the shared client.
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter
        self._switch(app, follower, plugin, "B")
        monitor_b = plugin._current.activePrinter
        self.assertFalse(monitor_a._data._owner_active)

        monitor_a._data.reconnect()
        self.qt.events(10)
        self.assertFalse(monitor_a._data._owner_active)
        self.assertFalse(monitor_a._data._active)
        self.assertTrue(monitor_b._data._owner_active)
        self.assertTrue(monitor_b._data._active)
        self.assertIs(plugin._current.activePrinter, monitor_b)

    def test_a_deposed_monitor_cannot_recover_through_the_emergency_path(self):
        # The emergency cycle must not grant ownership either: a
        # deposed monitor is inactive and unowned, and stays both.
        app, client, follower, plugin = self._install()
        monitor_a = plugin._current.activePrinter
        self._switch(app, follower, plugin, "B")
        monitor_b = plugin._current.activePrinter

        monitor_a._data.reconnect_after_emergency()
        self.qt.events(10)
        self.assertFalse(monitor_a._data._owner_active)
        self.assertFalse(monitor_a._data._active)
        self.assertTrue(monitor_b._data._owner_active)
        self.assertTrue(monitor_b._data._active)

"""Executable console coverage contracts."""
from tests import control_owner_support as harness

class ConsoleCoverageTests(harness.ConsoleCoverageTests):
    def test_a_response_burst_coalesces_into_one_shard_write(self):
        # J (the 2026-09-19 review): ten response batches inside one
        # debounce window produce ONE shard write, and the final
        # transcript carries every surviving line.
        persistence = harness._FakePersistence()
        controller = self._make(identity=("A", "Printer A"), persistence=persistence)
        for i in range(10):
            controller.append_responses([{"text": "line %d" % i, "error": False,
                                          "success": False, "time": 1000.0 + i}])
        self.assertEqual(controller._transcript[-1]["text"], "line 9")
        self.assertEqual(persistence.writes, {}, "nothing persists inside the window")
        self.events(450)  # the debounce fires
        stored = persistence.writes.get("A", {}).get("consoleTranscript") or []
        self.assertEqual([entry["text"] for entry in stored[-10:]], ["line %d" % i for i in range(10)])
        self.assertEqual(list(persistence.writes), ["A"], "one write for the whole burst")

    def test_a_machine_switch_retires_the_dirty_debounce_window(self):
        identity = {"value": ("A", "Printer A")}
        persistence = harness._FakePersistence()
        controller = self._make(identity=lambda: identity["value"], persistence=persistence)
        controller.append_responses([{"text": "dirty A line", "error": False,
                                      "success": False, "time": 10.0}])
        identity["value"] = ("B", "Printer B")
        controller._session_invalidated()
        self.events(450)  # the debounce would have fired
        self.assertNotIn("A", persistence.writes, "A's dirty lines must never persist after the switch")
        self.assertNotIn("B", persistence.writes, "the retired window must not leak into B")

    def test_an_empty_line_is_nothing_but_an_oversized_one_explains_itself(self):
        controller = self._make()
        self.assertFalse(controller.send(""))
        self.assertFalse(controller.send("   "))
        self.assertEqual(controller._transcript, [])
        self.assertFalse(controller.send("x" * (harness.MAX_LINE + 1)))
        self.assertEqual([entry["text"] for entry in controller._transcript],
                         ["Command too long — ignored."])
        # A multiline paste is folded to one line, not refused.
        self.assertTrue(controller.send("G28\nM18"))
        self.assertEqual(controller.values["consoleHistory"][-1], "G28M18")

    def test_a_full_lane_refuses_the_command(self):
        controller = self._make()
        controller._in_flight = set(range(harness.MAX_PENDING))
        self.assertFalse(controller.send("G28"))
        self.assertEqual(controller._transcript[-1]["text"],
                         "Too many commands waiting — try again in a moment.")

    def test_an_undeliverable_command_keeps_the_draft(self):
        controller = self._make()
        self.data.started = False
        self.assertFalse(controller.send("G28"))
        self.assertEqual(controller._transcript[-1]["text"],
                         "Command queue full or Moonraker unavailable — try again.")
        self.assertEqual(self.data.requests[-1].timeout_ms, 30000)
        self.assertEqual(self.data.requests[-1].channel, "console")
        self.assertEqual(self.data.requests[-1].path, "printer/gcode/script")

    def test_a_send_lands_and_its_verdict_colours_the_line(self):
        controller = self._make()
        self.assertTrue(controller.send("G28"))
        entry = controller._transcript[-1]
        self.assertEqual(entry, {"kind": "command", "text": "G28", "error": False,
                                 "success": False, "restored": False, "saved": False})
        self.assertEqual(controller.values["consolePending"], 1)
        revisions = controller.values["consoleRevisions"]
        self.data.callback({"result": "ok"}, None)
        self.assertTrue(entry["success"])
        self.assertEqual(controller.values["consoleRevisions"], revisions + 1)
        self.assertEqual(controller.values["consolePending"], 0)

    def test_a_server_refusal_marks_the_line_and_a_timeout_does_not(self):
        controller = self._make()
        controller.send("G28")
        entry = controller._transcript[-1]
        self.data.callback({"error": {"message": "Extrude below minimum temp"}},
                           "Extrude below minimum temp")
        self.assertTrue(entry["error"])
        self.assertFalse(entry["success"])
        controller.send("M18")
        second = controller._transcript[-1]
        self.data.callback(None, "timed out")
        self.assertFalse(second["error"])
        self.assertFalse(second["success"])
        self.assertEqual(controller._transcript[-1]["text"],
                         "No response from the printer — the command may still be running.")

    def test_a_late_completion_from_a_dead_session_drains_nothing(self):
        controller = self._make()
        controller.send("G28")
        entry = controller._transcript[-1]
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._in_flight, set())
        self.data.callback({"result": "ok"}, None)
        self.assertTrue(entry["success"])
        self.assertEqual(controller.values["consolePending"], 0)

    def test_responses_are_appended_and_blank_ones_are_skipped(self):
        controller = self._make()
        controller.append_responses([])
        # Only genuinely empty text is skipped; whitespace is a line.
        controller.append_responses([{"text": ""}, {"text": None}])
        self.assertEqual(controller._transcript, [])
        controller.append_responses([{"text": "ok", "error": False, "success": True, "time": 4.0},
                                     {"text": "x" * (harness.MAX_LINE + 5), "time": 6.0}])
        self.assertEqual([entry["kind"] for entry in controller._transcript],
                         ["response", "response"])
        self.assertEqual(len(controller._transcript[-1]["text"]), harness.MAX_LINE)
        self.assertEqual(controller._store_time, 6.0)
        # The stamp is monotonic: an older response never rewinds it.
        controller.append_responses([{"text": "again", "time": 1.0}])
        self.assertEqual(controller._store_time, 6.0)
        self.assertEqual(controller._transcript[-1]["kind"], "response")

    def test_the_ring_rotates_and_pins_the_newest_commands(self):
        controller = self._make()
        command = {"kind": "command", "text": "G28", "error": False, "success": False,
                   "restored": False}
        controller._append_entries([command] + [self._response(f"line {n}")
                                                for n in range(harness.MAX_HISTORY - 1)])
        self.assertEqual(len(controller._transcript), harness.MAX_HISTORY)
        controller._append_entries([self._response("next")])
        # The command is pinned through the rotation; nothing was dropped
        # here because the rotation took exactly the one pinned command.
        self.assertEqual(controller._transcript[0]["text"], "G28")
        self.assertEqual(controller.values["consoleDropped"], 0)
        controller._append_entries([self._response("again")])
        self.assertEqual(controller.values["consoleDropped"], 1)
        self.assertEqual(controller._transcript[0]["text"], "G28")

    def test_a_note_is_never_persisted(self):
        controller = self._make()
        controller.note("a local explanation")
        entry = controller._transcript[-1]
        self.assertEqual(entry["kind"], "note")
        self.assertNotIn(entry, controller._persist_window())
        controller.mark_saved()
        self.assertNotIn("saved", entry)

    def test_the_legacy_history_migrates_when_the_transcript_is_absent(self):
        config = harness.SimpleNamespace(console_transcript=None, console_history=["G28", "M18"],
                                 console_store_time=0.0)
        controller = self._make(config=config)
        self.assertEqual([entry["text"] for entry in controller._transcript], ["G28", "M18"])
        self.assertTrue(all(entry["restored"] for entry in controller._transcript))
        self.assertEqual(controller.values["consoleHistory"], ["G28", "M18"])

    def test_an_empty_stored_list_is_a_genuine_clear(self):
        # The Clear-doesn't-stick report: an empty record must not fall
        # through to the legacy history re-migration.
        controller = self._make(config=harness.PrinterConfig(console_transcript=[],
                                                     console_history=["G28"]))
        self.assertEqual(controller._transcript, [])

    def test_a_stored_transcript_keeps_the_newest_commands(self):
        stored = ([{"kind": "command", "text": "oldest-command", "error": False,
                    "success": False}]
                  + [{"kind": "response", "text": f"line {n}", "error": False, "success": False}
                     for n in range(harness.MAX_TRANSCRIPT + 20)]
                  + [{"kind": "command", "text": "newest-command", "error": False,
                      "success": False}])
        controller = self._make(config=harness.PrinterConfig(console_transcript=stored,
                                                     console_store_time=9.0))
        texts = [entry["text"] for entry in controller._transcript]
        self.assertIn("newest-command", texts)
        self.assertNotIn("oldest-command", texts)
        self.assertEqual(len(controller._transcript),
                         harness.MAX_TRANSCRIPT + controller.MAX_PERSIST_COMMANDS)
        self.assertEqual(controller._store_time, 9.0)
        self.assertTrue(all(entry["restored"] for entry in controller._transcript))

    def test_clear_stops_an_empty_transcript_and_empties_a_full_one(self):
        controller = self._make()
        controller.clear()
        self.assertEqual(self.applied, [])
        controller.send("G28")
        controller.clear()
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller.values["consoleHistory"], [])

    def test_the_clear_does_not_reload_the_legacy_history(self):
        persistence = harness._FakePersistence(shards={"A": {"consoleTranscript": [
            {"kind": "command", "text": "G28", "error": False, "success": False}]}})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual([entry["text"] for entry in controller._transcript], ["G28"])
        controller.clear()
        self.assertEqual(persistence.writes["A"], {"consoleTranscript": []})
        # The cleared shard blocks the reload: Clear sticks.
        controller.reload_if_empty()
        self.assertEqual(controller._transcript, [])

    def test_reload_keeps_a_live_session_on_the_same_machine(self):
        controller = self._make()
        controller.send("G28")
        controller._transcript_identity = "A"
        revisions = controller.values["consoleRevisions"]
        controller.reload_if_empty()
        self.assertEqual(controller.values["consoleRevisions"], revisions)
        self.assertEqual(controller._transcript[-1]["text"], "G28")

    def test_reload_holds_off_until_cura_knows_the_machine(self):
        for identity in (None, ("unknown", "Unknown Cura printer")):
            controller = self._make(identity=identity)
            controller.reload_if_empty()
            self.assertIsNone(controller._transcript_identity)

    def test_a_machine_switch_swaps_the_transcript(self):
        # A's lines must never bleed into B's pane and B's record.
        persistence = harness._FakePersistence(shards={
            "A": {"consoleTranscript": [{"kind": "command", "text": "A-only",
                                         "error": False, "success": False}]},
            "B": {"consoleTranscript": []}})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual([entry["text"] for entry in controller._transcript], ["A-only"])
        self.assertEqual(controller._transcript_identity, "A")
        controller._identity = lambda: ("B", "Printer B")
        controller.reload_if_empty()
        self.assertEqual(controller._transcript_identity, "B")
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller._dropped, 0)

    def test_an_empty_pane_on_the_same_machine_stays_quiet(self):
        controller = self._make()
        controller._transcript_identity = "A"
        revisions = controller.values["consoleRevisions"]
        controller.reload_if_empty()
        self.assertEqual(controller.values["consoleRevisions"], revisions)

    def test_the_shard_reader_distinguishes_absent_from_empty(self):
        controller = self._make()
        self.assertIsNone(controller._shard_transcript("A"))
        self.assertIsNone(controller._shard_transcript(""))
        # A shard that is not a mapping at all is ABSENT (the config
        # record still carries the transcript)...
        controller._persistence = harness._FakePersistence(shard="not a dict")
        self.assertIsNone(controller._shard_transcript("A"))
        # ...but a shard whose transcript is unusable reads as a Clear,
        # which is authoritative: an empty pane, not the config record.
        controller._persistence = harness._FakePersistence(shard={"consoleTranscript": "nope"})
        self.assertEqual(controller._shard_transcript("A"), [])
        controller._persistence = harness._FakePersistence(
            shard={"consoleTranscript": [{"kind": "command", "text": "shard"}]})
        self.assertEqual(controller._shard_transcript("A")[0]["text"], "shard")

    def test_a_shard_load_carries_its_own_store_stamp(self):
        persistence = harness._FakePersistence(shard={
            "consoleTranscript": [{"kind": "command", "text": "shard", "error": False,
                                   "success": False}],
            "consoleStoreTime": 12.0})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual(controller._transcript[0]["text"], "shard")
        self.assertEqual(controller._store_time, 12.0)
        self.assertEqual(controller._transcript_identity, "A")

    def test_a_missing_shard_falls_back_to_the_config_record(self):
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(
            config=harness.PrinterConfig(console_transcript=[{"kind": "command", "text": "config-side",
                                                      "error": False, "success": False}],
                                 console_store_time=3.0),
            persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual(controller._transcript[0]["text"], "config-side")
        self.assertEqual(controller._store_time, 3.0)

    def test_an_empty_shard_never_reloads_the_config_record(self):
        # The constructor seeds from the config record; once the machine
        # resolves, its shard is authoritative — an EMPTY shard (a
        # genuine Clear) must not fall back to that record.
        persistence = harness._FakePersistence(shards={"A": {"consoleTranscript": []}})
        controller = self._make(
            config=harness.PrinterConfig(console_transcript=[{"kind": "command", "text": "config-side",
                                                      "error": False, "success": False}]),
            persistence=persistence)
        self.assertEqual([entry["text"] for entry in controller._transcript], ["config-side"])
        # The pane currently belongs to another machine, so the next
        # reload reads A's record — and the EMPTY shard wins.
        controller._transcript_identity = "previous-machine"
        controller.reload_if_empty()
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller._transcript_identity, "A")

    def test_persisting_rides_the_shard_and_settles_the_colour(self):
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        stored = persistence.writes["A"]["consoleTranscript"][0]
        self.assertEqual(stored, {"kind": "command", "text": "G28", "error": False,
                                  "success": False})
        self.assertEqual(self.applied, [])  # no legacy write beside the shard
        entry = controller._transcript[-1]
        self.assertFalse(entry["saved"])
        controller.mark_saved()
        self.assertTrue(entry["saved"])

    def test_an_unresolved_identity_skips_the_persist_entirely(self):
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(identity=("unknown", "Unknown Cura printer"),
                                persistence=persistence)
        controller.send("G28")
        self.assertEqual(persistence.writes, {})
        self.assertEqual(self.applied, [])

    def test_a_rejected_shard_write_notes_the_console(self):
        persistence = harness._FakePersistence(shard=None, ok=False)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        self.assertTrue(any("could not be saved" in entry["text"]
                            for entry in controller._transcript))

    def test_the_config_only_double_still_writes_the_legacy_record(self):
        controller = self._make()
        controller.send("G28")
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[-1].console_transcript[0]["text"], "G28")
        self.assertEqual(self.applied[-1].console_store_time, controller._store_time)
        # The guard: an unchanged record is never rewritten.
        controller._persist_legacy()
        self.assertEqual(len(self.applied), 1)

    def test_the_persist_window_pins_the_newest_commands_in_order(self):
        # The persisted record is the last MAX_TRANSCRIPT lines PLUS the
        # newest commands, so a typed request survives being rotated out
        # of the kept tail — up to MAX_PERSIST_COMMANDS of them.
        controller = self._make()
        def command(text):
            return {"kind": "command", "text": text, "error": False, "success": False,
                    "restored": False, "saved": False}
        controller._transcript = (
            [command(f"pin {n}") for n in range(5)]
            + [self._response(f"mid {n}") for n in range(40)]
            + [command(f"tail {n}") for n in range(7)]
            + [self._response(f"end {n}") for n in range(43)])
        window = controller._persist_window()
        texts = [entry["text"] for entry in window]
        self.assertEqual(texts[:3], ["pin 2", "pin 3", "pin 4"])
        self.assertNotIn("pin 0", texts)
        self.assertNotIn("pin 1", texts)
        self.assertEqual(len(window), harness.MAX_TRANSCRIPT + 3)
        self.assertEqual(sum(1 for entry in window if entry["kind"] == "command"),
                         controller.MAX_PERSIST_COMMANDS)

    def test_mark_saved_only_touches_the_persisted_window(self):
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        old = [{"kind": "command", "text": f"old {n}", "error": False, "success": False,
                "restored": False, "saved": False} for n in range(12)]
        oldest = old[0]
        restored = {"kind": "command", "text": "restored", "error": False, "success": False,
                    "restored": True, "saved": False}
        old[1] = restored
        tail = ([{"kind": "command", "text": f"new {n}", "error": False, "success": False,
                  "restored": False, "saved": False} for n in range(10)]
                + [self._response(f"line {n}") for n in range(harness.MAX_TRANSCRIPT - 10)])
        controller._transcript = old + tail
        # The settle only claims what a SUCCESSFUL write stored, so the
        # write has to happen first (it is what arms the colour flip).
        controller._persist()
        controller.mark_saved()
        self.assertFalse(oldest["saved"])       # pushed out of the window
        self.assertFalse(restored["saved"])     # restored lines never claim disk
        self.assertTrue(tail[0]["saved"])       # a retained command inside it
        self.assertNotIn("saved", tail[-1])     # responses are left alone

    def test_a_write_failing_after_a_success_never_colours_its_unwritten_lines(self):
        # The review's catch: the settle used to colour "on disk" from
        # the transcript window alone, so a shard write that failed
        # after an earlier success painted lines it never stored.
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        stored = controller._transcript[-1]
        persistence.ok = False
        controller.send("M18")
        # The failed write leaves its own note after the command.
        unwritten = next(entry for entry in controller._transcript if entry["text"] == "M18")
        controller.mark_saved()  # the settle the successful write armed
        self.assertTrue(stored["saved"])
        self.assertFalse(unwritten["saved"])

    def test_a_claim_holds_while_its_line_is_still_in_the_pane(self):
        # The claim names the entries a success stored, so a line the
        # pane still shows keeps its "on disk" verdict after newer
        # traffic has pushed it out of the persisted window — it WAS
        # written, and only the pane dropping it retires the claim.
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        self.assertTrue(controller.send("G28"))
        written = controller._transcript[-1]
        controller._transcript.extend(
            [{"kind": "command", "text": f"M{n}", "error": False, "success": False,
              "restored": False, "saved": False} for n in range(controller.MAX_PERSIST_COMMANDS)]
            + [self._response(f"line {n}") for n in range(harness.MAX_TRANSCRIPT)])
        self.assertFalse(any(entry is written for entry in controller._persist_window()))
        # A later success settles ITS window; the earlier claim has to
        # survive that write's bookkeeping.
        controller._persist()
        controller.mark_saved()
        self.assertTrue(written["saved"])

    def test_the_settle_is_not_restarted_by_continuous_traffic(self):
        # A chatty feed writes the shard every few hundred ms; a settle
        # restarted by every success would push the colour flip out
        # forever, so a pending settle is left to fire.
        persistence = harness._FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller._saved_timer.setInterval(200)
        controller.send("G28")
        first = controller._transcript[-1]
        self.events(150)
        controller.send("M18")
        self.events(150)
        self.assertTrue(first["saved"])

    def test_the_connection_note_prints_transitions_only(self):
        controller = self._make()
        self.data.connectionStateChanged.emit("unknown")
        self.assertEqual(controller._transcript[-1]["text"], "Disconnected from Moonraker.")
        self.assertEqual(controller._transcript[-1]["kind"], "note")
        self.data.connectionStateChanged.emit("yes")
        self.assertEqual(controller._transcript[-1]["text"], "Connected to Moonraker.")
        # The same state again writes nothing: a flapping link must not
        # fill the feed with repeats.
        self.data.connectionStateChanged.emit("yes")
        self.assertEqual(len(controller._transcript), 2)
        self.data.connectionStateChanged.emit("no")
        self.assertEqual(controller._transcript[-1]["text"], "Disconnected from Moonraker.")

    def test_a_session_invalidation_drops_pending_sends(self):
        controller = self._make()
        controller.send("G28")
        self.assertEqual(controller.values["consolePending"], 1)
        self.data.invalidated.emit()
        self.assertEqual(controller.values["consolePending"], 0)
        self.assertEqual(controller._in_flight, set())
        # The live pane is left alone: a same-machine reconnect must not
        # re-stamp a working session.
        self.assertEqual(controller._transcript[-1]["text"], "G28")
        self.data.invalidated.emit()
        self.assertEqual(controller.values["consolePending"], 0)

    def test_the_emergency_stop_note_only_prints_with_pending_sends(self):
        controller = self._make()
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._transcript, [])
        controller.send("G28")
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._transcript[-1]["text"],
                         "Pending console commands dropped by the emergency stop.")



"""Executable index hydration contracts."""
from tests import index_plate_support as harness

class HydrationWindowTests(harness.HydrationWindowTests):
    def test_a_hydration_request_covers_the_layers_around_the_layer(self):
        self._bind()
        self.service.request_hydration(2)
        # The face reads the previous layer's ghost, the current layer and
        # the look-ahead; a request that asked for the current layer alone
        # left the ghost empty for the whole print.
        self.assertEqual(self.service._hydrate, {1, 2, 3})
        self.service._hydrate.clear()
        self.service.request_hydration(0)
        self.assertEqual(self.service._hydrate, {0, 1})

    def test_a_request_for_a_hydrated_layer_still_demands_its_presentation(self):
        self._bind(hydrated=(2,))
        self.service.request_hydration(2)
        # Hydration is a SOURCE state, not presentation readiness. The
        # current layer remains demanded until the decoded cache holds it,
        # alongside the two ghosts.
        self.assertEqual(self.service._hydrate, {1, 2, 3})

    def test_a_request_outside_the_index_is_ignored(self):
        self._bind()
        self.service.request_hydration(9)
        self.service.request_hydration(-1)
        self.assertEqual(self.service._hydrate, set())

    def test_a_refused_layer_is_not_asked_for_again(self):
        # The latch outranks every presentation source. In particular an
        # already-hydrated layer must not bypass it and spin forever after
        # a decode/prepare failure.
        self._bind(hydrated=(2,))
        self.service._failed_hydrate.add(2)
        self.assertEqual(self.service._presentation_source(2), "failed")
        self.service.request_hydration(2)
        # The other two layers remain useful and are still demanded.
        self.assertEqual(self.service._hydrate, {1, 3})

    def test_a_refused_layer_names_itself_in_the_payload(self):
        # The silent latch: the layer is refused, the face shows
        # "Loading layer…" for as long as it is on screen and nothing
        # says why. The payload carries the verdict so the face can
        # name it.
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service._failed_hydrate.add(5)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertIsNone(payload["layers"]["current"])
        self.assertEqual(payload["refusal"], "failed",
                         "the refused layer must name itself")
        # A neighbour's refusal is not this anchor's: the face names
        # the layer it is showing.
        self.assertEqual(self.service.plate_progress(4, file_position=None)["refusal"], "")

    def test_an_anchor_outside_the_file_names_itself_too(self):
        # The other no-arrival state: a shrunken file leaves the frozen
        # anchor past the last layer. Nothing will ever arrive for it
        # either, so the face must not promise a load.
        self._bind(layers=8)
        self.service._manual_anchor = 11
        payload = self.service.plate_progress(11, file_position=None)
        self.assertIsNone(payload["layers"]["current"])
        self.assertEqual(payload["refusal"], "outside")
        # A healthy layer of the same file claims nothing.
        self.assertEqual(self.service.plate_progress(3, file_position=None)["refusal"], "")

    def test_an_explicit_seek_clears_the_refusal_latch(self):
        # The trap: the latch outranks every demand, so seeking away
        # and back to a refused layer was refused again with nothing
        # said — the user had no way to ask. A NEW anchor is a fresh
        # attempt; the poll's re-assert of the SAME anchor is not
        # (that is the per-poll re-read the latch exists to stop).
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service._failed_hydrate.add(5)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertIn(5, self.service._failed_hydrate,
                      "the poll's own re-assert must not clear the latch")
        self.assertNotIn(5, self.service._hydrate)
        self.service.set_manual_anchor(1)
        self.service.set_manual_anchor(5)
        self.assertNotIn(5, self.service._failed_hydrate,
                         "an explicit re-seek is a fresh attempt")
        self.assertIn(5, self.service._hydrate,
                      "the re-seek's layer was not demanded again")

    def test_the_polls_re_assert_keeps_the_frozen_demand_standing(self):
        # The coordinator re-asserts the frozen anchor every poll so a
        # demand dropped while the worker was busy is raised again
        # (its own comment says the request has to stand). The
        # idempotency guard swallowed the whole call, so a manual
        # window that lost its demand never came back.
        self._bind(layers=8)
        index = self.service._view._index
        index.followed_layer = 1
        self.service.set_manual_anchor(5)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertEqual(self.service._hydrate, {4, 5, 6},
                         "the standing demand was not re-raised")
        # A ready layer is never re-demanded: decoded is readiness, and
        # re-raising it would submit the window every poll.
        self.service._decoded_lru.set(5, {}, 0)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertEqual(self.service._hydrate, {4, 6})

    def test_a_moved_anchor_retops_the_window(self):
        index = self._bind(hydrated=(4,))
        self.service._hydrate = {0, 1, 2}
        self.service.set_followed_layer(3)
        self.assertEqual(index.followed_layer, 3)
        # The anchor is pruned to its own window, then topped back up:
        # the layer the print has just left is exactly the ghost the face
        # wants, and it is not in the request any more.
        self.assertEqual(self.service._hydrate, {2, 3, 4})

    def test_a_manual_anchor_demands_its_window_beside_the_live_one(self):
        index = self._bind(layers=8)
        index.followed_layer = 1
        self.service.set_manual_anchor(5)
        # The frozen layer's window is asked for on its own demand path:
        # re-anchoring the live window would evict the live layer the
        # dot, the split and the printed fill all read.
        self.assertEqual(self.service._hydrate, {4, 5, 6})
        self.assertEqual(index.manual_anchor, 5)
        self.service._hydrate.clear()
        self.service.request_hydration(1)
        self.assertEqual(self.service._hydrate, {0, 1, 2})
        # Rejoining the print drops the frozen demand.
        self.service.set_manual_anchor(None)
        self.assertIsNone(index.manual_anchor)
        self.assertEqual(self.service._hydrate, {0, 1, 2})

    def test_a_manual_anchor_outside_the_index_is_never_asked_for(self):
        index = self._bind(layers=8)
        self.service.set_manual_anchor(11)
        # A layer outside the file is no anchor: nothing is demanded and
        # the retention bound keeps the live window alone.
        self.assertEqual(self.service._hydrate, set())
        self.assertIsNone(index.manual_anchor)

    def test_the_frozen_anchor_reaches_an_index_built_after_the_detach(self):
        self._bind(layers=8)
        # The detach can land before the build, with no index to carry
        # the anchor yet.
        self.service._view = None
        self.service.set_manual_anchor(6)
        index = self._bind(layers=8)
        # The poll's advance hands the held anchor to the new index.
        self.service._apply_manual_anchor()
        self.assertEqual(index.manual_anchor, 6)

    def test_the_full_marker_draws_every_motion_of_the_frozen_layer(self):
        # A seek lands the layer at 100% (the live request): the FULL
        # marker resolves to the layer's own motion count, whatever
        # layer it points at.
        index = self._bind(layers=8)
        index.motion_offsets = [list(range(3)) for _ in range(8)]
        index.motion_offsets[5] = list(range(11))
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(-1)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertEqual(payload["split"], 11)

    def test_the_manual_split_is_the_frozen_layers_boundary(self):
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(37)
        # A frozen layer carries no file position; the scrub is what
        # the payload reads for the boundary.
        payload = self.service.plate_progress(5, file_position=None)
        self.assertEqual(payload["split"], 37)
        # A live poll of the print's own layer is untouched.
        payload = self.service.plate_progress(2, file_position=42)
        self.assertIsNone(payload["split"])

    def test_rejoining_the_print_abandons_the_scrub(self):
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(37)
        self.service.set_manual_anchor(None)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertIsNone(payload["split"])

    def test_the_progress_payload_carries_the_layers_motion_count(self):
        # motionTotal is the slider's range: the layer's own edge count.
        index = self._bind(layers=8)
        index.motion_offsets = [list(range(10)) for _ in range(8)]
        payload = self.service.plate_progress(3, file_position=None)
        self.assertEqual(payload["motionTotal"], 10)

    def test_the_full_cache_answers_after_the_window_evicts(self):
        # The full prepared cache's promise: a layer the window's
        # store no longer holds is served from the compact form —
        # decoded by the WORKER into the hot cache (never on the UI
        # thread), and the bundle's first display
        # reuses the worker's own payload instead of decoding a second
        # object .
        index = self._bind(layers=8, hydrated=(5,))
        from mpf.gcode.PlateProgress import encode_layer, prepare_layer
        payload = prepare_layer(index, 5)
        self.assertIsNotNone(payload)
        self.service._full_cache[5] = encode_layer(payload)
        from mpf.gcode.PlateProgress import _prepared_layers
        _prepared_layers.pop((id(index), 5), None)
        # The worker's commit lands the decoded payload in the hot
        # presentation cache.
        self.service._decoded_lru[5] = payload
        bundle = self.service.plate_layers(5)
        self.assertIs(bundle["current"], payload,
                      "the worker's payload was decoded again for the first display")
        self.assertEqual(bundle["current"]["motions"], payload["motions"])

    def test_alternating_anchors_keep_both_bundles(self):
        # The live payload and the frozen one alternate every poll
        # while detached; one memo slot thrashed — each ask evicted
        # the other's bundle and the identities churned per poll (the
        # live report: the whole plugin went awful while detached).
        self._bind(layers=8, hydrated=(3, 4, 5))
        live = self.service.plate_layers(4)
        frozen = self.service.plate_layers(9)
        self.assertIsNot(frozen, live)
        self.assertIs(self.service.plate_layers(4), live,
                      "the live bundle rebuilt on the alternating ask")
        self.assertIs(self.service.plate_layers(9), frozen,
                      "the frozen bundle rebuilt on the alternating ask")

    def test_the_layers_memo_rebuilds_when_the_hydration_fill_lands(self):
        # The live report: a far seek's current stayed blank forever —
        # the bundle was memoised while the anchor's layer was still
        # loading, and the fill state never entered the key. The fill
        # is the worker's decoded store now: hydration alone serves
        # nothing (the UI thread never prepares or decodes).
        index = self._bind(layers=8, hydrated=(4, 6))
        self.service.set_manual_anchor(5)
        first = self.service.plate_layers(5)
        self.assertIsNone(first["current"])
        from mpf.gcode.PlateProgress import prepare_layer
        index.hydrated_layers.add(5)
        self.service._decoded_lru[5] = prepare_layer(index, 5)
        second = self.service.plate_layers(5)
        self.assertIsNotNone(second["current"])

    def test_a_request_outside_the_followed_window_is_not_queued(self):
        index = self._bind()
        index.followed_layer = 0
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, set())
        index.followed_layer = None
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, {3, 4})



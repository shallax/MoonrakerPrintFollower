"""Executable index object visits contracts."""
from tests import index_plate_support as harness

class PlateVisitedTests(harness.PlateVisitedTests):
    def test_a_walk_without_an_index_or_a_boundary_reads_empty(self):
        # No index, no boundary, no anchor: nothing has been judged, and
        # the empty set is the truth about the edges walked so far.
        self.service._view = None
        self.assertEqual(self.service.plate_visited(0, 5, self.ROWS), frozenset())
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n")
        self.assertEqual(self.service.plate_visited(0, None, self.ROWS), frozenset())
        self.assertEqual(self.service.plate_visited(None, 5, self.ROWS), frozenset())

    def test_a_travel_across_a_polygon_deposits_nothing(self):
        # Motions: 1 the prime that starts the print, 2 and 3 the travels
        # (the second crossing the polygon), 4 the extrusion that finally
        # reaches it.
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                   b"G0 X0 Y25\nG0 X80 Y25\nG1 X40 Y25 E2\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset(),
                         "a travel crossing the polygon marked it printed")
        self.assertEqual(self.service.plate_visited(0, 5, self.ROWS), frozenset({"Widget"}),
                         "the extrusion that reached the polygon did not mark it")

    def test_an_extrusion_that_only_clips_a_corner_marks_it(self):
        # Both endpoints outside, the segment crossing the rectangle: the
        # endpoint-only reading would call this a miss.
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\nG0 X5 Y5\nG1 X75 Y45 E2\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset({"Widget"}))

    def test_a_later_travel_never_unmarks_a_printed_object(self):
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                   b"G0 X5 Y5\nG1 X75 Y45 E2\nG0 X0 Y0\nG1 X80 Y25 E3\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset({"Widget"}))
        # The next poll's travel crosses everything and changes nothing;
        # a backwards split (a restart) keeps the verdict too, because
        # the walk's cursor only ever advances.
        self.assertEqual(self.service.plate_visited(0, 6, self.ROWS), frozenset({"Widget"}))
        self.assertEqual(self.service.plate_visited(0, 2, self.ROWS), frozenset({"Widget"}))

    def test_a_compact_layer_is_walked_like_a_full_one(self):
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n;LAYER:1\n"
                b"G0 X5 Y5\nG1 X75 Y45 E2\n")
        # Layer 1 is the one that prints the object, and a compact index
        # carries its motions only once the worker has hydrated it.
        self._bind(data, hydration=(1,))
        self.assertEqual(self.service.plate_visited(1, 2, self.ROWS), frozenset({"Widget"}))

    def test_a_moved_anchor_restarts_the_walk(self):
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n;LAYER:1\n"
                b"G0 X5 Y5\nG1 X75 Y45 E2\n")
        self._bind(data, hydration=(0, 1))
        self.assertEqual(self.service.plate_visited(0, 2, self.ROWS), frozenset())
        self.assertEqual(self.service.plate_visited(1, 2, self.ROWS), frozenset({"Widget"}),
                         "the anchor change did not restart the walk at the layer's start")

    def test_rows_without_a_usable_polygon_are_ignored(self):
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X75 Y45 E1\n")
        rows = [{"name": "Widget", "polygon": None},
                {"name": "", "polygon": self.POLYGON},
                {"name": "Box", "polygon": [[70.0, 40.0], [80.0, 40.0], [80.0, 50.0], [70.0, 50.0]]}]
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset({"Box"}))

    def test_a_polygon_arriving_after_its_extrusion_marks_it(self):
        # The late-DEFINE sequence: the walk advances with no geometry
        # at all, then EXCLUDE_OBJECT_DEFINE executes and the polygon
        # arrives covering an extrusion already consumed. A bare cursor
        # never looks back, so the object stayed grey until a later
        # layer.
        self._bind(self.LATE)
        self.assertEqual(self.service.plate_visited(0, 8, []), frozenset(),
                         "the geometry-free poll marked something")
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX))),
            frozenset({"Widget", "Box"}),
            "the late polygon was not replayed against the consumed extrusion")

    def test_a_settled_poll_never_rescans_prior_motion(self):
        self._bind(self.LATE)
        walked = self._counting_walk()
        # The opening poll with no geometry draws nothing at all: with
        # no polygon to mark, the layer is never walked. The cursor
        # advances regardless (the geometry that arrives next replays
        # from the layer's start), which the poll below proves.
        self.service.plate_visited(0, 8, [])
        self.assertEqual(walked, [], "the geometry-free poll walked the layer")
        walked.clear()
        # The geometry arriving costs ONE replay of the consumed range.
        self.assertEqual(self.service.plate_visited(
            0, 8, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
            frozenset({"Widget", "Box"}))
        self.assertEqual(len(walked), 8, "the replay did not cover the consumed range")
        walked.clear()
        # Rebuilt-but-equal rows are the same geometry: no walk at all.
        for _ in range(3):
            self.assertEqual(self.service.plate_visited(
                0, 8, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
                frozenset({"Widget", "Box"}))
        self.assertEqual(walked, [], "a settled poll re-walked the consumed motion")
        # An advanced split walks the new edge alone, never the range —
        # and with every object already printed there is nothing left
        # to decide, so it draws no edge either (the all-printed fast
        # path). The cursor still advances.
        self.assertEqual(self.service.plate_visited(
            0, 9, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
            frozenset({"Widget", "Box"}))
        self.assertEqual(walked, [], "the all-printed advance drew edges")
        self.assertEqual(self.service._visited_upto, 9,
                         "the all-printed advance did not advance the cursor")
        # A later object turns the walk back on: it is judged against
        # the consumed range — for its own geometry alone, and the
        # verdict keeps the objects already printed.
        self.assertEqual(self.service.plate_visited(
            0, 9, self._rows(("Widget", self.POLYGON), ("Box", self.BOX),
                             ("Spoon", self.SPOON))), frozenset({"Widget", "Box"}))
        self.assertEqual([edge[0] for edge in walked], list(range(9)),
                         "the new object's replay did not cover the consumed range")

    def test_another_object_arriving_later_is_judged_on_the_consumed_range(self):
        self._bind(self.LATE)
        self.assertEqual(self.service.plate_visited(0, 4, self._rows(("Widget", self.POLYGON))),
                         frozenset({"Widget"}))
        # Box arrives at the SAME split: only the consumed extrusion 3
        # covers it, so the verdict turns on the replay alone.
        self.assertEqual(
            self.service.plate_visited(0, 4, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON))),
            frozenset({"Widget", "Box"}),
            "the incrementally-defined object was not judged on the consumed range")
        # Fork arrives with new motion instead: the two halves compose
        # into one verdict, and the untouched object stays unvisited.
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON),
                                                        ("Fork", self.FORK))),
            frozenset({"Widget", "Box", "Fork"}),
            "the same poll's replay and delta did not compose")
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON),
                                                        ("Fork", self.FORK))),
            frozenset({"Widget", "Box", "Fork"}),
            "a later poll changed the verdicts")

    def test_an_extrusion_inside_overlapping_polygons_marks_both(self):
        # The release-candidate overlap question: an edge inside the
        # hulls of TWO objects has met both — the walk must not stop
        # at the first matching hull, or the later-defined object
        # never reads passed.
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\nG0 X6 Y4\n"
                b"G1 X8 Y14 E2\n")  # motion 3: inside BOX and OVER both
        self._bind(data)
        over = [[5.0, 8.0], [15.0, 8.0], [15.0, 28.0], [5.0, 28.0]]
        rows = self._rows(("Box", self.BOX), ("Over", over))
        self.assertEqual(self.service.plate_visited(0, 4, rows),
                         frozenset({"Box", "Over"}),
                         "the overlap's second object was never marked")

    def test_a_changed_polygon_replays_the_consumed_range(self):
        # The same name with moved vertices is new geometry: the cache
        # keys on content, so the consumed range is judged again.
        self._bind(self.LATE)
        away = [[100.0, 130.0], [120.0, 130.0], [120.0, 140.0], [100.0, 140.0]]
        self.assertEqual(self.service.plate_visited(0, 8, self._rows(("Widget", away))),
                         frozenset(), "the far polygon marked something")
        self.assertEqual(self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON))),
                         frozenset({"Widget"}),
                         "the moved polygon did not replay the consumed extrusion")

    def test_a_new_layer_rewalks_its_own_motion(self):
        # Layer 0 settles the geometry without printing the object; the
        # anchor move must not read that settled geometry as covering
        # layer 1's own motions, and must reanchor in both directions.
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                b";LAYER:1\nG0 X5 Y5\nG1 X75 Y45 E2\n")
        self._bind(data, hydration=(0, 1))
        rows = self._rows(("Widget", self.POLYGON))
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset(),
                         "layer 0 printed the object")
        self.assertEqual(self.service.plate_visited(1, 2, rows), frozenset({"Widget"}),
                         "the settled geometry suppressed layer 1's own walk")
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset(),
                         "layer 0's verdict survived the anchor move")

    def test_a_dense_layer_without_geometry_is_never_walked(self):
        # The first fast path: with no polygon to mark, no edge of the
        # consumed range can change a verdict, so a dense layer is
        # never drawn at all. The cursor still advances with it — the
        # geometry that arrives later is judged against the WHOLE
        # consumed range (the late-DEFINE ruling), merely over polls.
        self._bind_dense(self.DENSE_MOTIONS)
        walked = self._counting_walk()
        self.assertEqual(self.service.plate_visited(0, 100000, []), frozenset())
        self.assertEqual(walked, [], "the geometry-free poll walked the dense layer")
        self.assertEqual(self.service._visited_upto, 100000,
                         "the cursor did not advance over the consumed range")
        polls, verdict = self._drive(0, 100000, [("Part", self._strip(5000))])
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the late polygon was not replayed against the consumed range")
        self.assertGreater(polls, 1, "the dense replay was not spread over polls")

    def test_a_dense_layer_of_printed_objects_draws_no_edge(self):
        # The second fast path: every known polygon already visited —
        # nothing left to decide, so the advancing poll draws no edge
        # and still moves the cursor over the range.
        self._bind_dense(self.DENSE_MOTIONS)
        near = ("Near", self._strip(5000))
        far = ("Far", self._strip(50000))
        self._drive(0, 100000, [near, far])
        walked = self._counting_walk()
        self.assertEqual(self.service.plate_visited(0, self.DENSE_MOTIONS,
                                                    self._rows(near, far)),
                         frozenset({"Near", "Far"}),
                         "the all-printed advance changed a verdict")
        self.assertEqual(walked, [], "the all-printed advance drew the dense layer")
        self.assertEqual(self.service._visited_upto, self.DENSE_MOTIONS)

    def test_a_dense_replay_is_spread_over_bounded_polls(self):
        # The cut walk: each poll draws at most one check step, and the
        # chunks compose into one pass — no cut edge is drawn twice.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts, edges = [], []
        for _ in range(500):
            walked.clear()
            verdict = self.service.plate_visited(0, 5000, self._rows(part))
            counts.append(len(walked))
            edges.extend(edge[0] for edge in walked)
            if (self.service._visited_upto >= 5000
                    and self.service._visited_replay_upto >= self.service._visited_upto):
                break
        else:
            self.fail("the dense walk never settled")
        self.assertEqual(verdict, frozenset({"Part"}))
        self.assertGreater(len(counts), 1, "the walk was not cut")
        self.assertTrue(all(count <= self.service._VISITED_WALK_STEP for count in counts),
                        "a poll drew more than the check step: %s" % counts)
        self.assertTrue(all(edge < 5000 for edge in edges),
                        "the walk drew past its own range")
        self.assertEqual(edges, sorted(set(edges)),
                         "a cut walk drew an edge twice")

    def test_a_late_polygon_replays_a_dense_consumed_range(self):
        # The late-DEFINE ruling under the budget: the polygon arrives
        # after the layer was consumed with no geometry at all, so only
        # the frontier's replay can find it. The verdict proves the
        # replay covered the consumed range, and the cut chunks prove
        # it did so without re-walking an edge.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        self.assertEqual(self.service.plate_visited(0, 5000, []), frozenset())
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts, edges = [], []
        for _ in range(500):
            walked.clear()
            verdict = self.service.plate_visited(0, 5000, self._rows(part))
            counts.append(len(walked))
            edges.extend(edge[0] for edge in walked)
            if self.service._visited_replay_upto >= self.service._visited_upto:
                break
        else:
            self.fail("the late polygon was never judged against the consumed range")
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the late polygon was not marked")
        self.assertGreater(len(counts), 1, "the replay was not spread over polls")
        self.assertIn(4000, edges,
                      "the replay did not reach the extrusion it had to judge")
        self.assertEqual(edges, sorted(set(edges)),
                         "the frontier replay re-walked a consumed edge")
        self.assertEqual(self.service._visited_replay_upto, 5000,
                         "the replay did not finish on the consumed range")

    def test_a_changed_polygon_on_a_printed_object_costs_no_walk(self):
        # An object already marked printed cannot change its verdict,
        # whatever its geometry does: no walk, no vertex test, and the
        # verdict holds.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        self._drive(0, 5000, [("Part", self._strip(4000))])
        walked = self._counting_walk()
        calls = self._count_segment_tests()
        self.assertEqual(self.service.plate_visited(0, 5000,
                                                    self._rows(("Part", self._strip(1000)))),
                         frozenset({"Part"}))
        self.assertEqual(walked, [], "the printed object was judged again")
        self.assertEqual(calls, [], "the printed object's vertices were tested")

    def test_a_settled_dense_poll_tests_no_vertex(self):
        # The settled poll (unchanged geometry, unchanged split): no
        # edge and no vertex test, however dense the layer.
        self._bind_dense(self.DENSE_MOTIONS)
        objects = [("Obj%02d" % i, self._strip(2000 + 7000 * i)) for i in range(4)]
        self._drive(0, 20000, objects)
        walked = self._counting_walk()
        calls = self._count_segment_tests()
        for _ in range(3):
            self.assertEqual(self.service.plate_visited(0, 20000, self._rows(*objects)),
                             frozenset({"Obj00", "Obj01", "Obj02"}))
        self.assertEqual(walked, [], "a settled dense poll re-walked the layer")
        self.assertEqual(calls, [], "a settled dense poll tested vertices")

    def test_an_advancing_split_walks_only_the_new_range(self):
        # The cursor's own rule, with geometry still outstanding: the
        # delta draws the range since the last poll, never the layer.
        # The first range is DRIVEN to its cursor instead of assumed to
        # fit one poll: the walk's budget is wall-clock, so where a
        # platform cuts it (Windows cut it at the very first check step
        # — _VISITED_WALK_STEP — and the delta then resumed from there)
        # is a scheduling detail of the machine, never of the rule
        # under test. The edges the second range walks are counted
        # whole, however many polls they take.
        self._bind_dense(5000)
        walked = self._counting_walk()
        part = ("Part", self._strip(4800))
        self._drive(0, 1000, [part])
        walked.clear()
        self._drive(0, 2000, [part])
        self.assertEqual([edge[0] for edge in walked], list(range(1000, 2000)),
                         "the delta re-walked the consumed range")

    def test_a_bounded_walk_agrees_with_an_unbounded_one(self):
        # The cut is a scheduling detail, never a semantic one: the
        # chunked verdict equals the one-pass verdict.
        module = self.qt.load("GCodeIndexService")
        objects = [("Obj%02d" % i, self._strip(700 + 900 * i)) for i in range(4)]
        self._bind_dense(8000)
        self._pin_budget(0.0)
        _polls, bounded = self._drive(0, 8000, objects)
        other = module.GCodeIndexService(self.files, object())
        self.addCleanup(other.close)
        other.bind(self.job)
        other._view = module.IndexView(self.job, harness.make_index(layers=1, motions=8000))
        other._VISITED_WALK_BUDGET_S = 60.0
        unbounded = other.plate_visited(0, 8000, self._rows(*objects))
        self.assertEqual(bounded, unbounded,
                         "the chunked walk read differently from the one-pass walk")
        self.assertEqual(bounded, frozenset(name for name, _polygon in objects))

    def test_rapid_anchor_changes_stay_bounded(self):
        # The anchor flips with the print (and with a manual detach):
        # every poll stays inside the budget, and a fresh anchor never
        # inherits the other layer's work.
        self._bind_dense(5000, layers=2)
        self._pin_budget(0.0)
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts = []
        for anchor in (0, 1) * 6:
            walked.clear()
            self.assertEqual(self.service.plate_visited(anchor, 5000, self._rows(part)),
                             frozenset(),
                             "an anchor flip claimed a verdict it never walked")
            counts.append(len(walked))
        self.assertTrue(all(0 < count <= self.service._VISITED_WALK_STEP for count in counts),
                        "an anchor flip overspent its poll: %s" % counts)
        _polls, verdict = self._drive(0, 5000, [part])
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the settled anchor never reached its own layer's verdict")

    def test_a_dense_travel_never_marks_an_object(self):
        # The E rule holds on a dense walk: the layer's travel run
        # crosses the object and deposits nothing there.
        index = self._bind_dense(5000)
        index.travel_starts[0] = [1000]
        index.travel_ends[0] = [2000]
        self._pin_budget(0.0)
        _polls, verdict = self._drive(0, 5000, [("Part", self._strip(1500))])
        self.assertEqual(verdict, frozenset(), "the travel marked the object")
        self.assertEqual(self.service._visited_upto, 5000,
                         "the travel run was not walked")
        # The control: an object where the extrusion resumes is marked.
        _polls, after = self._drive(0, 5000, [("Part", self._strip(2500))])
        self.assertEqual(after, frozenset({"Part"}),
                         "the extrusion after the travel did not mark the object")

    def test_overlapping_objects_on_a_dense_layer_both_mark(self):
        # One extruding edge visits every hull it meets: the dense walk
        # must never stop at the first match.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        _polls, verdict = self._drive(0, 5000, [("Box", self._strip(4000, 200.0)),
                                                ("Over", self._strip(4000, 100.0))])
        self.assertEqual(verdict, frozenset({"Box", "Over"}),
                         "the overlap's second object was never marked")

    def test_the_owner_thread_bound_holds_on_a_dense_layer(self):
        # The reviewer's repro, measured on the owner thread: a dense
        # layer attached part-way through with every object defined.
        # Each poll is bounded (the one-pass walk costs ~839 ms), the
        # verdict is never provisional, and the walk still converges on
        # the whole truth. The bound is the walk's own budget plus an
        # order of magnitude of headroom for a loaded machine.
        self._bind_dense(self.DENSE_MOTIONS)
        objects = [("Obj%02d" % i, self._strip(5000 + 9000 * i)) for i in range(20)]
        worst, polls, verdict = 0.0, 0, frozenset()
        for _ in range(2000):
            start = harness.time.monotonic()
            verdict = self.service.plate_visited(0, 100000, self._rows(*objects))
            worst = max(worst, harness.time.monotonic() - start)
            polls += 1
            if (self.service._visited_upto >= 100000
                    and self.service._visited_replay_upto >= self.service._visited_upto):
                break
        else:
            self.fail("the dense walk never settled")
        self.assertLess(worst * 1000.0, 100.0,
                        "one poll spent %.1f ms on the owner thread" % (worst * 1000.0))
        self.assertLess(polls, 500, "the dense walk took %d polls to settle" % polls)
        self.assertEqual(verdict,
                         frozenset(name for name, strip in objects if strip[2][0] < 100000),
                         "the bounded walk did not reach the consumed range's truth")



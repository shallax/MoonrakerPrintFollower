"""Executable qml plate paint delivery contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_the_bounded_walk_leaves_the_full_repaint_s_picture(self):
        # The walk skips the runs whose last motion is below the paint's
        # own start — a bound that is only sound while the geometry it
        # skips really holds nothing the paint could draw. Reach the
        # SAME printed split twice and compare: once as a delta over an
        # already-printed canvas (the bounded path — the canvas holds
        # the history below the start, so only the tail is stroked) and
        # once as a cleared repaint of that split from the layer's start
        # (the unbounded path — every run is read). A bound that is off
        # by a run, or that trusts a run whose motions do not ascend,
        # leaves geometry missing from the delta picture.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        # 60 short runs, nine vertices each, motions ascending across
        # the whole layer: the bound has to land on a run boundary, and
        # a run read from its wrong end draws the wrong edges.
        runs = []
        motion = 0
        for run in range(60):
            points = []
            for vertex in range(9):
                points.append([20.0 + vertex * 12.0, 40.0 + run * 3.0,
                               float(motion)])
                motion += 1
            runs.append(points)
        target, history = 480, 200
        payload = {
            "classes": {"WALL-OUTER": runs},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": motion,
        }
        layer = self._native_layer(payload, face)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})

        def settled_grab():
            self._pump_ms(350)  # past the view settle and the paints
            return window.grabWindow()

        def seek_to(split):
            # One paint per split: a split that lands before the canvas
            # has painted the previous one is coalesced, and the paint
            # that follows starts from the split before last.
            self._printer.setSplit(split)
            self._pump_ms(120)

        seek_to(0)        # the layer's first paint, from its start
        seek_to(history)  # a forward paint over the printed history
        seek_to(target)   # the delta: from the history's split forward
        delta = settled_grab()
        delta_ink = self._red_pixels(delta, face, window)
        self.assertGreater(delta_ink, 0, "the delta path never drew")
        # The comparison means nothing unless the delta grab followed a
        # paint that EXTENDED the canvas: a reset clears it and reads
        # the whole layer, which is the very path being compared.
        self.assertGreater(
            face.property("_paintsSinceReset"), 0,
            "the delta grab followed a reset — the bound was never "
            "exercised, so this comparison proved nothing")
        # The same split, reached backward: the reset clears the canvas,
        # so `from` is -1 and the walk reads the layer from its start.
        seek_to(motion - 1)
        seek_to(target)
        full = settled_grab()
        full_ink = self._red_pixels(full, face, window)
        self.assertGreater(full_ink, 0, "the cleared repaint never drew")
        # The core ink is a census, not a sampled grid: a run the bound
        # skipped drops hundreds of its pixels, while the seam the
        # delta's own composition creates can drop one — this container
        # drops exactly one on the correct walk, and 532 on a bound
        # that skipped a run straddling the boundary.
        print("bounded-walk delta vs full repaint ink: %d vs %d"
              % (delta_ink, full_ink))
        self.assertLessEqual(abs(delta_ink - full_ink), max(8, full_ink // 500),
                             "the bounded walk's delta carried %d core-ink "
                             "pixels, the full repaint %d"
                             % (delta_ink, full_ink))
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        rows = range(0, int(face.height()), 4)
        cols = range(0, int(face.width()), 4)
        strict, loose = 0, 0
        for row in rows:
            for col in cols:
                left = delta.pixel(int(origin.x()) + col, int(origin.y()) + row)
                right = full.pixel(int(origin.x()) + col, int(origin.y()) + row)
                if left == right:
                    continue
                strict += 1
                if any(abs(((left >> shift) & 0xFF) - ((right >> shift) & 0xFF)) > 60
                       for shift in (0, 8, 16)):
                    loose += 1
        sampled = len(rows) * len(cols)
        print("bounded-walk delta vs full repaint: %d exact, %d past the "
              "antialias tolerance, of %d sampled" % (strict, loose, sampled))
        # The delta and the repaint stroke the SAME edges of the same
        # layer at the same alpha over the same canvas, and this
        # container renders them identically — 0 of the samples differ
        # exactly — so the allowance below is the platform rasteriser's
        # fringe shading and nothing this test is measuring. It must
        # never become a hiding place: a bound that skipped a run
        # straddling the boundary put 44 samples past it here (and cost
        # the ink census 532 pixels), both well outside these bounds.
        self.assertLessEqual(loose, max(8, sampled // 500),
                             "the bounded walk's delta settled to a different "
                             "picture (%d of %d sampled pixels differ beyond "
                             "the antialias tolerance, %d exactly)"
                             % (loose, sampled, strict))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_reverse_scrub_frames_never_show_a_hybrid_composition(self):
        # The compositor contract: every frame displayed while the
        # split moves presents ONE COMPLETE composition — the
        # standing committed picture or the replacement's — judged by
        # a whole-path probe signature (the early printed history,
        # both sides of the prefix/tail seam, the Canvas tail's
        # middle, the final printed edge, and clean space beyond it),
        # never a rightmost-column guess. Zero blank frames, zero
        # hybrid frames, zero exemptions. An intermediate split that
        # never presented may not appear once the demand moved on:
        # the allowed set is the LAST PRESENTED composition plus the
        # current demand, derived from actual presentation.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("attached", False)
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        row = int(round(float(bed["offsetY"])
                        + (float(bed["bedYMax"]) - 125.0)
                        * float(plot_value["sy"])))
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))

        def column_for(bed_x):
            return int(round(float(bed["offsetX"])
                             + (bed_x - float(bed["bedXMin"]))
                             * float(plot_value["sx"])))

        def ink_at(image, bed_x, slack=3):
            # Any red ink in the path's row band within `slack`
            # columns of the bed-x position.
            col = column_for(bed_x)
            return any(
                self._matches(image.pixel(int(origin.x()) + c,
                                          int(origin.y()) + r),
                              (0xD3, 0x2F, 0x2F))
                for c in range(col - slack, col + slack + 1)
                for r in range(row - 2, row + 3))

        def clean_beyond(image, split):
            # No printed ink past the final edge: the stroke's round
            # cap ends within a few pixels of the last point, and the
            # next motion (10 mm further) must never bleed through —
            # stale geometry beyond the requested split reads here.
            edge = column_for(20.0 + 10.0 * (split - 1))
            return not any(
                self._matches(image.pixel(int(origin.x()) + c,
                                          int(origin.y()) + r),
                              (0xD3, 0x2F, 0x2F))
                for c in range(edge + 6, edge + 18)
                for r in range(row - 2, row + 3))

        def complete_signature(image, split):
            # The WHOLE composition's probe signature for `split`: the
            # early printed history, both sides of the prefix/tail
            # seam (a hole at the handoff is a hybrid), the Canvas
            # tail's middle, the final printed edge — and clean space
            # beyond it.
            probes = [25.0]  # the early printed history
            if split > 10:
                probes += [105.0, 115.0]  # both sides of the seam
                probes.append((110.0 + 20.0 + 10.0 * (split - 1)) / 2.0)
            probes.append(20.0 + 10.0 * (split - 1))  # the final edge
            return (all(ink_at(image, bx) for bx in probes)
                    and clean_beyond(image, split))

        def classify(image, candidates):
            # The frame's complete composition, by whole-signature
            # match — the candidate whose signature holds, else None
            # (an invalid frame).
            for split in candidates:
                if complete_signature(image, split):
                    return split
            return None

        failures = []

        def leg(name, prev_split, new_split, invalidate=False, beats=25):
            # One transition: every frame must present the standing
            # committed composition or the requested one. Zero blanks
            # and zero invalid frames — no exemptions.
            layer.set_expected_key("fixture-key")
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            self._printer.setSplit(prev_split)
            self._pump_ms(300)  # the leg's own standing composition
            self._printer.setSplit(new_split)
            if invalidate:
                layer.set_expected_key("invalidated")
            allowed = [prev_split, new_split]
            presented = prev_split
            for beat in range(beats):
                self._pump_ms(20)
                image = window.grabWindow()
                shown = classify(image, allowed)
                if shown is None:
                    # The whole signature, per candidate, so a flake's
                    # failing probes are visible in the report.
                    details = []
                    for cand in allowed:
                        probes = [25.0]
                        if cand > 10:
                            probes += [105.0, 115.0,
                                       (110.0 + 20.0 + 10.0 * (cand - 1)) / 2.0]
                        probes.append(20.0 + 10.0 * (cand - 1))
                        details.append("split %d probes %s clean=%s"
                                       % (cand, [ink_at(image, bx) for bx in probes],
                                          clean_beyond(image, cand)))
                    progress_now = face.property("progress")
                    if progress_now is not None and hasattr(progress_now, "toVariant"):
                        progress_now = progress_now.toVariant()
                    details.append(
                        "face: wasShown=%s hold=%s texture=%s last=%s covers=%s "
                        "prefixValid=%s prefixSplit=%s split=%s"
                        % (face.property("_prefixWasShown"),
                           face.property("_prefixHold"),
                           face.property("_textureReady"),
                           face.property("_lastSplit"),
                           face.property("_vectorCoversFrom"),
                           layer.prefixValid, layer.prefixSplit,
                           progress_now.get("split") if progress_now else "?"))
                    details.append("owners=%s receipt=%s" % (
                        face.property("_presentation").toVariant(),
                        face.property("_deliveredComposition").toVariant()))
                    full_image = self._image_with_source(face, layer.rasterData)
                    details.append("held=%r fullImage=%s" % (
                        face.property("_heldFullSource"),
                        (full_image.isVisible(), full_image.property("opacity"),
                         full_image.property("status")) if full_image is not None else None))
                    failures.append("%s: beat %d presented no complete "
                                    "composition (%s)"
                                    % (name, beat, " | ".join(details)))
                    continue
                if shown not in allowed:
                    failures.append("%s: beat %d presented split %d (allowed %s)"
                                    % (name, beat, shown, allowed))
                else:
                    presented = shown
            return presented

        for name, prev_split, new_split, invalidate in [
                ("100% -> partial", 21, 18, False),
                ("80% -> 60%", 18, 15, False),
                ("80% -> 40%", 18, 8, False),
                ("60% -> 70%", 15, 16, False),
                ("delayed prefix", 18, 15, True)]:
            leg(name, prev_split, new_split, invalidate)
        # The rapid alternation: the demand never rests, and the
        # standing-composition policy applies — the allowed set is the
        # LAST PRESENTED complete composition plus the current demand.
        # An intermediate split that never presented may not appear
        # later; one that did becomes the standing picture.
        layer.set_expected_key("fixture-key")
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        self._pump_ms(300)
        standing = 18
        rapid_trace = []
        for split in [15, 8, 12, 6, 16, 10, 14, 9]:
            self._printer.setSplit(split)
            allowed = [standing, split]
            for beat in range(3):
                self._pump_ms(20)
                image = window.grabWindow()
                shown = classify(image, allowed)
                receipt = face.property("_deliveredComposition").toVariant()
                receipt_split = receipt.get("split") if isinstance(receipt, dict) else None
                rapid_trace.append((split, beat, shown, receipt_split))
                if shown is None:
                    ink = sum(
                        self._matches(image.pixel(int(origin.x()) + c,
                                                  int(origin.y()) + r),
                                      (0xD3, 0x2F, 0x2F))
                        for c in range(column_for(20.0), column_for(220.0) + 1)
                        for r in range(row - 2, row + 3))
                    signatures = [
                        (candidate,
                         [ink_at(image, x) for x in
                          ([25.0, 105.0, 115.0,
                            (110.0 + 20.0 + 10.0 * (candidate - 1)) / 2.0]
                           if candidate > 10 else [25.0])
                          + [20.0 + 10.0 * (candidate - 1)]],
                         clean_beyond(image, candidate))
                        for candidate in allowed]
                    failures.append("rapid %d -> %d: beat %d presented no "
                                    "complete composition (image=%dx%d null=%s ink=%d "
                                    "signatures=%s receiptComplete=%s trace=%s owners=%s receipt=%s)"
                                    % (standing, split, beat, image.width(), image.height(),
                                       image.isNull(), ink, signatures,
                                       complete_signature(image, receipt_split)
                                       if isinstance(receipt_split, int) and 0 < receipt_split <= 21
                                       else None, rapid_trace[-8:],
                                       face.property("_presentation").toVariant(),
                                       receipt))
                    continue
                if shown not in allowed:
                    failures.append("rapid %d -> %d: beat %d presented split %d "
                                    "(allowed %s)" % (standing, split, beat, shown, allowed))
                else:
                    standing = shown
        self.assertEqual(failures, [],
                         "hybrid, blank or stale-composition frames "
                         "during the scrub: %s" % failures)
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_stale_canvas_delivery_never_readies_the_partial_prefix(self):
        # F4's focused regression, the review's exact state:
        # textureReady true, the coverage compatible, but the
        # canvas's LAST PAINTED split behind the current demand —
        # _partialPrefixReady must stay false until the CURRENT
        # split's delivery lands. The predicate is read through the
        # real engine's face (the front gates — the prefix model and
        # the uploaded image — genuinely pass). The stale record is
        # forced while the demand itself is never moved, so no paint
        # races the probe.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        # Settle on a partial split so the prefix model publishes and
        # the prefix Image uploads — the predicate's own front gates.
        # The settle's landmark is the face's own shown record, not a
        # fixed beat: the prefix's upload is off-thread, so any beat
        # that fits one host reads the front gates still shut on another
        # and the assertion below fails on the state it was waiting for.
        # The timeout is a hang guard; this assertion is what fails when
        # the prefix never shows.
        self._printer.setSplit(18)
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline and \
                not face.property("_prefixWasShown"):
            self._pump_ms(10)
        self.assertTrue(face.property("_prefixWasShown"),
                        "the settled prefix never showed")

        def predicate():
            result = QMetaObject.invokeMethod(
                face, "_partialPrefixReady", Q_RETURN_ARG(QVariant))
            self.assertIsInstance(result, bool, "the predicate never invoked")
            return result

        # The stale exact state: a delivered canvas whose recorded
        # painted split trails the standing demand (split 18). Both
        # coverage records are forced — the ownership gates read the
        # DELIVERED one (the committed record runs a beat ahead of
        # the scene), and a fixture that forced only the committed
        # record would leave the delivered record claiming a canvas
        # state the test does not mean.
        face.setProperty("_vectorCoversFrom", 0)
        receipt = {"epoch": face.property("_progressWorldEpoch"),
                   "from": 0, "split": 5, "prefixSource": ""}
        face.setProperty("_deliveredComposition", receipt)
        face.setProperty("_lastSplit", 5)
        self.assertFalse(predicate(),
                         "an older split's delivered frame readied the prefix")
        # The painter may have already advanced to 18; only the
        # DELIVERED receipt can allow the prefix to stand on that bitmap.
        face.setProperty("_lastSplit", 18)
        self.assertFalse(predicate(),
                         "the painter's split impersonated delivered pixels")
        receipt["split"] = 18
        face.setProperty("_deliveredComposition", receipt)
        self.assertFalse(predicate(), "a full Canvas admitted overlapping prefix ink")
        receipt["from"] = 10
        receipt["prefixSource"] = layer.prefixData
        face.setProperty("_deliveredComposition", receipt)
        self.assertTrue(predicate(), "the matching tail delivery never readied the prefix")

    def test_a_stale_scene_or_ambiguous_canvas_delivery_cannot_admit_a_prefix(self):
        # A pure delivery-controller probe through the REAL QML engine.
        # The backend may publish a new layer while a previous paint
        # is still in the render thread. Neither the old paint nor
        # multiple paints collapsed into one delivery is evidence
        # that the new layer's Canvas texture is presentation-ready.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline and not face.property("_prefixWasShown"):
            self._pump_ms(10)
        self.assertTrue(face.property("_prefixWasShown"),
                        "the baseline prefix was never presented")
        epoch = face.property("_progressWorldEpoch")
        world = face.property("_progressWorldKey")
        self.assertGreater(epoch, 0)
        self.assertTrue(world)

        def deliver(receipt, paints=1):
            face.setProperty("_canvasTransaction", {
                "epoch": face.property("_progressWorldEpoch"),
                "world": face.property("_progressWorldKey"),
                "inFlight": True, "pending": False, "count": paints,
                "first": receipt, "last": receipt, "consensus": paints == 1})
            result = QMetaObject.invokeMethod(
                face, "_deliverProgressPaint", Q_RETURN_ARG(QVariant))
            self.assertIsInstance(result, bool)
            return result

        correct = {"epoch": epoch, "world": world, "valid": True,
                   "from": 10, "split": 18}
        face.setProperty("_deliveredComposition", None)
        self.assertFalse(deliver({**correct, "epoch": epoch - 1}),
                         "an earlier layer's callback certified this one")
        self.assertNotEqual(face.property("_vectorCoversShown"), 10)

        self.assertFalse(deliver(correct, paints=2),
                         "two paints collapsed into one ambiguous texture")
        self.assertNotEqual(face.property("_vectorCoversShown"), 10)

        self.assertTrue(deliver(correct), "a matching delivery was refused")
        self.assertEqual(face.property("_vectorCoversShown"), 10)
        self.assertEqual(face.property("_vectorSplitShown"), 18)
        self.assertEqual(face.property("_vectorWorldShown"), epoch)

        # Advancing the print or layer incarnation invalidates the
        # previously delivered bitmap without changing its motion
        # count or accidentally reusing a previous anchor's bytes.
        face.setProperty("_progressWorldEpoch", epoch + 1)
        face.setProperty("_deliveredComposition", None)
        self.assertFalse(deliver(correct),
                         "the new generation accepted stale Canvas pixels")
        self.assertEqual(face.property("_vectorCoversShown"), -2)

    def test_return_to_standing_split_queues_after_intervening_upload(self):
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        self.pump(10)
        epoch = face.property("_progressWorldEpoch")
        world = face.property("_progressWorldKey")
        old = {"epoch": epoch, "world": world, "valid": True,
               "from": 0, "split": 18}
        face.setProperty("_deliveredComposition", old)
        intervening = dict(old, split=8)
        face.setProperty("_canvasTransaction", {
            "epoch": epoch, "world": world, "inFlight": True,
            "pending": False, "count": 1, "first": intervening,
            "last": intervening, "consensus": True})
        self.assertFalse(QMetaObject.invokeMethod(
            face, "_progressPaintSatisfied", Q_RETURN_ARG(QVariant)))
        self.assertTrue(QMetaObject.invokeMethod(
            face, "_deliverProgressPaint", Q_RETURN_ARG(QVariant)))
        self.assertTrue(face.property("_canvasTransaction").toVariant()["pending"])

    def test_equivalent_extra_canvas_paints_share_one_valid_delivery(self):
        # Qt can coalesce multiple identical onPaint events into one
        # painted() signal. The delivery is not ambiguous when both
        # bitmaps have the same world, boundary and coverage.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        self.pump(10)
        epoch = face.property("_progressWorldEpoch")
        world = face.property("_progressWorldKey")
        receipt = {"epoch": epoch, "world": world, "valid": True,
                   "from": 0, "split": 8}
        face.setProperty("_canvasTransaction", {
            "epoch": epoch, "world": world, "inFlight": True,
            "pending": False, "count": 2, "first": receipt,
            "last": dict(receipt), "consensus": True})
        self.assertTrue(QMetaObject.invokeMethod(
            face, "_deliverProgressPaint", Q_RETURN_ARG(QVariant)))
        self.assertEqual(face.property("_vectorCoversShown"), 0)
        self.assertEqual(face.property("_vectorSplitShown"), 8)

    def test_mixed_canvas_paints_cannot_certify_the_last_boundary(self):
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        self.pump(10)
        epoch = face.property("_progressWorldEpoch")
        world = face.property("_progressWorldKey")
        old = {"epoch": epoch, "world": world, "valid": True,
               "from": 0, "split": 8}
        current = dict(old, **{"from": 5, "split": 10})
        face.setProperty("_canvasTransaction", {
            "epoch": epoch, "world": world, "inFlight": True,
            "pending": False, "count": 2, "first": old,
            "last": current, "consensus": False})
        self.assertFalse(QMetaObject.invokeMethod(
            face, "_deliverProgressPaint", Q_RETURN_ARG(QVariant)))
        state = face.property("_canvasTransaction")
        if hasattr(state, "toVariant"):
            state = state.toVariant()
        self.assertTrue(state["pending"])

    def test_exact_canvas_coalesces_progress_while_one_paint_is_in_flight(self):
        from PyQt6.QtCore import QMetaObject
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("_deliveredComposition", None)
        face.setProperty("_canvasTransaction", {
            "epoch": face.property("_progressWorldEpoch"),
            "world": face.property("_progressWorldKey"),
            "inFlight": True, "pending": False, "count": 0,
            "first": None, "last": None, "consensus": False})
        QMetaObject.invokeMethod(face, "_requestProgressPaint")
        QMetaObject.invokeMethod(face, "_requestProgressPaint")
        state = face.property("_canvasTransaction")
        if hasattr(state, "toVariant"):
            state = state.toVariant()
        self.assertTrue(state["pending"], "rapid live polls did not coalesce")
        self.assertTrue(state["inFlight"])

    def test_a_delivery_does_not_reenter_canvas_paint_in_its_own_turn(self):
        # Qt may render extra canvas windows during a resize. An
        # ambiguous single-flight receipt must schedule one later
        # retry, not spin requestPaint() synchronously from onPainted
        # and starve the GUI/camera under repeated invalidation.
        from PyQt6.QtCore import QMetaObject
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("_canvasTransaction", {
            "epoch": face.property("_progressWorldEpoch"),
            "world": face.property("_progressWorldKey"),
            "inFlight": False, "pending": True, "count": 0,
            "first": None, "last": None, "consensus": False})
        QMetaObject.invokeMethod(face, "_flushProgressPaint")
        state = face.property("_canvasTransaction")
        if hasattr(state, "toVariant"):
            state = state.toVariant()
        self.assertFalse(state["inFlight"],
                         "onPainted synchronously initiated its replacement paint")
        self.assertTrue(state["pending"],
                        "the retry was consumed before the next event turn")

    def test_obsolete_staging_upload_cannot_replace_committed_texture(self):
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, Q_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        self._pump_ms(100)
        epoch, world = face.property("_progressWorldEpoch"), face.property("_progressWorldKey")
        front = face.property("_frontBuffer")
        stage = 1 - front
        standing = {"epoch": epoch, "world": world, "valid": True,
                    "from": 0, "split": 6, "paintKey": "standing"}
        face.setProperty("_deliveredComposition", standing)

        def deliver(receipt):
            face.setProperty("_canvasTransaction", {
                "epoch": epoch, "world": world, "inFlight": True,
                "pending": False, "count": 1, "first": receipt,
                "last": receipt, "consensus": True})
            face.setProperty("_paintBuffer", stage)
            return QMetaObject.invokeMethod(face, "_deliverStagedPaint",
                Q_RETURN_ARG(QVariant), Q_ARG(QVariant, stage))

        obsolete = dict(standing, split=16, paintKey="superseded-demand")
        self.assertFalse(deliver(obsolete))
        self.assertEqual(face.property("_frontBuffer"), front,
                         "the superseded upload exposed intermediate pixels")
        receipt = face.property("_deliveredComposition")
        self.assertEqual(receipt.toVariant() if hasattr(receipt, "toVariant") else receipt, standing)
        self.assertTrue(face.property("_canvasTransaction").toVariant()["pending"])
        key = QMetaObject.invokeMethod(face, "_progressKeyOf", Q_RETURN_ARG(QVariant))
        current = dict(standing, split=10, paintKey=key)
        self.assertTrue(deliver(current))
        self.assertEqual(face.property("_frontBuffer"), stage)
        receipt = face.property("_deliveredComposition")
        self.assertEqual(receipt.toVariant() if hasattr(receipt, "toVariant") else receipt, current)

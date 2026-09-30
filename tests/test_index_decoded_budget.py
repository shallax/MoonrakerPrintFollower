"""Executable index decoded budget contracts."""
from mpf.GCode import LayerCache as layer_cache
from tests import index_plate_support as harness

class DecodedBudgetTests(harness.DecodedBudgetTests):
    def test_gpu_budget_survives_one_consumer_closing_and_reverts_for_software(self):
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("mini", True)
            self.service.set_gpu_rendering("popover", True)
            self.assertEqual(self.service._decoded_lru.max_bytes, self.module._GPU_DECODED_LRU_MAX_BYTES)
            self.service.set_gpu_rendering("popover", False)
            self.assertEqual(self.service._decoded_lru.max_bytes, self.module._GPU_DECODED_LRU_MAX_BYTES)
            self.service._reset_print_state()
            self.assertEqual(self.service._decoded_lru.max_bytes, self.module._GPU_DECODED_LRU_MAX_BYTES)
            # Populate more than the software allowance without actually allocating it.
            self.service._decoded_lru.set(1, object(), 80 * 1024 * 1024)
            self.service._decoded_lru.set(2, object(), 80 * 1024 * 1024)
            self.service.set_gpu_rendering("mini", False)
            self.assertEqual(self.service._decoded_lru.max_bytes, self.module._DECODED_LRU_MAX_BYTES)
            self.assertLessEqual(self.service.decoded_resident_bytes(), self.module._DECODED_LRU_MAX_BYTES)

    def test_a_pin_keeps_evicted_bytes_charged(self):
        # The mirror seeds the way the commit does in production:
        # every charged layer's size stays known past its eviction.
        def charge(layer, size):
            self.service._decoded_lru.set(layer, object(), size)
            self.service._decoded_sizes[layer] = size

        lru = self.service._decoded_lru
        charge(1, 100)
        self.service.pin_decoded(1)
        charge(2, 100)
        charge(3, 100)  # 300 > 200: the LRU evicts 1 on its own
        self.assertNotIn(1, lru)
        self.assertEqual(self.service.pinned_decoded_bytes(), 100,
                         "the evicted pin's bytes were uncharged")
        self.service._reconcile_decoded()
        self.assertEqual(self.service.decoded_resident_bytes(), 200,
                         "the combined bound did not yield to the pin")
        self.service.unpin_decoded(1)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)
        self.assertEqual(self.service.decoded_resident_bytes(), 100,
                         "the unpin never released the charge")
        # An unknown layer pins nothing.
        self.service.pin_decoded(99)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    def test_an_evicted_memo_payload_still_pins(self):
        # The frozen window's memoised payload can outlive its LRU
        # entry: the pin must charge it from the retained size, not
        # from the LRU's live table.
        self.service._decoded_sizes[9] = 700
        self.service._decoded_lru.set(9, object(), 700)
        self.service._decoded_lru.clear()  # the LRU dropped it
        self.service.pin_decoded(9)
        self.assertEqual(self.service.pinned_decoded_bytes(), 700,
                         "the memoised payload's pin never charged")
        self.service.unpin_decoded(9)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    def test_the_service_floor_is_one_entry(self):
        # The windows' protection moved to the pins: the floor keeps
        # only the just-committed layer alive under a pathological
        # byte bound.
        self.assertEqual(self.service._decoded_lru.min_entries, 1)
        lru = self.service._decoded_lru
        lru.set(7, object(), 100)
        lru.max_bytes = 1
        lru.set(8, object(), 100)
        self.assertEqual(len(lru), 1, "the one-entry floor did not hold")

    def test_the_pin_reads_the_charged_size_without_rewalking(self):
        # The worker measured the payload once; the pin must reuse
        # that size (O(1)), never re-walk the geometry on the UI
        # thread — the exact bytes must survive the eviction through
        # the mirror.
        payload = {"classes": {"FILL": [
            [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(20000)]]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 20000}
        start = harness.time.monotonic()
        size = layer_cache._deep_size(payload)
        walked = harness.time.monotonic() - start
        print("deep_size: %d bytes walked in %.1f ms" % (size, walked * 1000.0))
        lru = self.service._decoded_lru
        lru.set(4, payload, size)
        self.service._decoded_sizes[4] = size
        self.service.pin_decoded(4)
        lru.set(5, object(), 120)
        lru.set(6, object(), 120)
        self.assertNotIn(4, lru)
        self.assertEqual(self.service.pinned_decoded_bytes(), size,
                         "the pin did not retain the worker's measured size")
        self.service.unpin_decoded(4)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    @harness.unittest.skipUnless(harness.os.path.isfile("/proc/self/status"),
                         "the RSS budget reads procfs: macOS and Windows have none")
    def test_the_decoded_tier_plateaus_under_churn(self):
        # Seek-style churn must not climb: fresh payloads per cycle,
        # the byte bound evicting under pressure, and the process
        # RSS within a slack of the early cycle (a leak would climb
        # past it — the live-print confirmation rides the next pass).
        import gc

        def rss_kb():
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
            return 0

        lru = self.service._decoded_lru
        lru.max_bytes = 8 * 1024 * 1024

        def cycle_payloads():
            for _layer in range(12):
                yield {"classes": {"FILL": [
                    [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(8000)]]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 8000}

        for cycle in range(6):
            for layer, payload in enumerate(cycle_payloads()):
                lru.set(cycle * 100 + layer, payload,
                        layer_cache._deep_size(payload))
            lru.clear()
            gc.collect()
            if cycle == 1:
                early = rss_kb()
        late = rss_kb()
        print("decoded-tier RSS: early %d KB, late %d KB" % (early, late))
        self.assertLess(late, early + 30000,
                        "the decoded tier's RSS climbed across the cycles")

    def test_an_encoding_failure_still_charges_the_decoded_tier(self):
        # A layer whose encode failed has no bytes to measure: the charge
        # comes from the payload's own motion count, so the byte budget
        # stays bounded without a second geometry walk.
        charge = layer_cache._decoded_charge
        self.assertEqual(charge(payload={"motions": 100}), 100 * 256)
        # A count that is absent, unusable or negative is no count at all:
        # the floor is what keeps the accounting honest.
        self.assertEqual(charge(payload={"motions": 0}), layer_cache._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload={"motions": -5}), layer_cache._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload={"motions": "many"}), layer_cache._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload=[1, 2]), layer_cache._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(), layer_cache._DECODED_CHARGE_FLOOR)
        # A packed payload is charged by its expansion, not by a walk of
        # the decoded points it stands for.
        self.assertEqual(charge(raw=b"x" * 1000),
                         1000 * layer_cache._DECODED_PACKED_EXPANSION)

    def test_a_value_that_refuses_its_size_leaves_the_walk(self):
        # A host object may refuse the size probe; the walk drops that one
        # value and keeps accounting the rest rather than fail the charge.
        class Unmeasurable:
            def __sizeof__(self):
                raise TypeError("no size")

        lru = self.module._ByteBoundedLru(max_bytes=100000)
        lru[0] = {"payload": {"motions": 3}, "extra": Unmeasurable()}
        self.assertGreater(lru.total_bytes(), 0)

    def test_the_combined_bound_never_evicts_a_protected_window(self):
        # The demanded windows are protected: when the pins alone put the
        # tier over budget, the reconcile yields to that floor rather than
        # evict a layer the poll is about to read.
        lru = self.service._decoded_lru
        lru.set(0, object(), 90)
        lru.set(1, object(), 90)
        lru.protected = {0, 1}
        self.service._decoded_sizes[2] = 5000
        self.service.pin_decoded(2)
        self.assertEqual(self.service.pinned_decoded_bytes(), 5000)
        self.assertEqual(len(lru), 2, "a protected window was evicted")
        self.assertIn(0, lru)
        self.assertIn(1, lru)

    def test_the_protection_set_needs_an_index(self):
        # No index names the followed layers, so there is no window to
        # protect and the set is left exactly as it was.
        self.service._decoded_lru.protected = {7}
        self.service._view = None
        self.service._update_decoded_protection()
        self.assertEqual(self.service._decoded_lru.protected, {7})



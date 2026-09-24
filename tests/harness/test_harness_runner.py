"""Host-side pins for the runner's evidence spine.

runner.py runs inside the harness container, but its imports are
host-safe, so the pure pieces — the PNG dimension check behind
shot()'s self-defence and the evidence record — are pinned here,
outside the slow loop. Run directly (like test_harness_specs.py):
`python3 tests/harness/test_harness_runner.py`.
"""

import json
import os
import re
import shutil
import sys
import time
import tempfile
import unittest
from pathlib import Path

import runner

ROOT = Path(__file__).resolve().parent.parent.parent
# A per-run scratch, never a fixed path: a fixed dir under the shared
# /tmp/mpf collects files owned by the harness container's root user,
# which the host-side legs cannot then overwrite (the live gate error).
SCRATCH = tempfile.mkdtemp(prefix="test-harness-runner-", dir="/tmp/mpf")


def _png_bytes(width, height):
    # A structurally valid PNG header: signature + IHDR chunk carrying
    # the declared dimensions. shot() reads only these fields, so the
    # file body is irrelevant.
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = (b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00")  # bit depth 8, RGBA, no interlace
    length = len(ihdr).to_bytes(4, "big")
    return signature + length + ihdr + b"\x00" * 4  # CRC not read by the parser


class PngSizeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH, exist_ok=True)

    def test_declared_dimensions_parse(self):
        path = os.path.join(SCRATCH, "full.png")
        with open(path, "wb") as handle:
            handle.write(_png_bytes(1600, 1000))
        self.assertEqual(runner._png_size(path), (1600, 1000))

    def test_truncated_frame_is_not_a_png(self):
        path = os.path.join(SCRATCH, "truncated.png")
        with open(path, "wb") as handle:
            handle.write(_png_bytes(1600, 1000)[:20])
        self.assertIsNone(runner._png_size(path))

    def test_non_png_is_none(self):
        path = os.path.join(SCRATCH, "text.png")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not a png")
        self.assertIsNone(runner._png_size(path))


class ClassificationRatchetTests(unittest.TestCase):
    def test_real_input_can_only_grow(self):
        # The F08 acceptance, pinned statically: real-input steps may
        # only increase as the conversion proceeds. Today's census
        # (2026-09-15, the what's-new overlay's z16 — the pin includes
        # key_press, which the classifier counts as real input).
        # The floor is the CURRENT census (2026-09-18, the panel's
        # re-census — the old 93 let 46 real steps convert to probes
        # before the pin fired): 139.
        text = (ROOT / "tests/harness/scenarios.py").read_text(encoding="utf-8")
        real = len(re.findall(
            r'"op": "(deliver_click|click_stage|click_text|key_press)"', text))
        self.assertGreaterEqual(real, 139)

    def test_direct_invocation_can_only_shrink(self):
        # And the other half: direct-invocation steps may only
        # shrink. Today's census (2026-09-15): the what's-new
        # overlay's z16 raised the ceiling once, deliberately — its
        # popup has no real-input trigger (the offer runs at boot;
        # the re-shows exist so each real dismissal can be tested),
        # so its six exec_slot re-shows and one marker-clear are the
        # floor for the feature's coverage (DECISIONS 4.1.0). The pin
        # now counts exec_code and confirm_box, which the classifier
        # itself calls direct invocation, so they can never hide
        # outside the ratchet. One more deliberate raise (4.2.0,
        # DECISIONS): h6c's setBedMeshThresholds exec — the range
        # filter's window cannot change through a single click (the
        # handle-click no-op ruling makes a full-range window inert to
        # clicks, and the harness has no drag op), so the slot is the
        # only real path. And a9's reconnect exec — the group shares
        # one boot, so the never-connected premise needs a real
        # session cycle, and no real-input path exists for a
        # reconnection:
        # 47 exec_slot + 12 exec_file_slot + 6 emit_click +
        # 3 confirm_box + 6 exec_mode + 3 exec_validator +
        # 1 exec_console + 1 exec_extrude + 1 exec_test_connection
        # + 23 exec_code.
        # One more deliberate raise (4.3.0, DECISIONS): g6's refused
        # Resume exec — the lane's revalidation witness must dispatch
        # through the real slot while the state forbids it, and no
        # real-input path exists for a click on a refused control.
        # And s8's track-click probe — the slider's click path has no
        # real-input op (the driver clicks by objectName/text only,
        # and the probe clicks BY GEOMETRY on the track), so the
        # witness is the inline QTest click.
        text = (ROOT / "tests/harness/scenarios.py").read_text(encoding="utf-8")
        direct = len(re.findall(
            r'"op": "(exec_slot|exec_file_slot|emit_click|confirm_box|exec_mode'
            r'|exec_validator|exec_console|exec_extrude|exec_test_connection|exec_code)"', text))
        # A deliberate raise (4.4.0, DECISIONS): the configure group's
        # nine slots — the reorder witness must dispatch through the
        # real slot (the g6 precedent's reasoning), the accessor gets
        # one exercising call, and the readout collapse/restore rides
        # the chrome slots exactly as v7 does. The drag GESTURE stays
        # out of the gate (no button-state primitive yet).
        # One more deliberate raise (4.4.0, DECISIONS): the configure
        # group's three inline probes — the info-readout centring gate
        # (a geometry proof no verb expresses), and the file-manager
        # popup's band click + row-width gate (the band has no
        # objectName and the ⇄ text collides with the configure
        # triggers, so the click rides the s8 track-click precedent).
        # And the drag gate (x6): a real press/move/release on the
        # handle via QTest — the driver has no drag verb, and the
        # probe proves the plain release commits (the live report's
        # last open item). Its restore slot, x5's file-manager close
        # and outside-press probe ride the same paths with it.
        # And the controls-field gate (x4): the three fixed-width
        # readout fields must stay separate on the strip's line — a
        # geometry proof no verb expresses (the live report: the
        # fields must never overlap).
        # And the availability gate (x8): the collapse and restore
        # slots — the live-untestable ruling rides the
        # slots exactly as the other readout scenarios do.
        # And the cross-talk gate (x9): the info drag rides the x6
        # probe again, and the crosstalk probe runs twice — after
        # the drag and after the controls reset — raising when the
        # reset touched the information pane (the live report).
        # And the next-pause fill (x10): the improve-Eta exec —
        # the fill appears only once the index lands, and no real
        # input path exists for the monitor-side download (the h8
        # precedent's reasoning).
        # And x10's load flow: the fill's ETA needs the follower's
        # observed layer, so the scenario rides the h2 load (its
        # confirm_box counts, the h2 precedent).
        # And p6/p7's determinism pin: the wait_sim on the layer
        # clock's first tick (the click's target must resolve from
        # the mapped layer, never the file-position fallback that
        # lands inside the baked pause — the 2026-09-18 flake).
        # One more deliberate raise (4.5.0, DECISIONS): the popover
        # rounds' six exec_codes — the dismissal probe's geometry
        # clicks (no real-input op clicks by coordinates, the s8
        # precedent) and the selector's five tri-state reads (the
        # bespoke glyph used to assert by text; the native checkbox
        # carries its state in checkState, which no declarative op
        # reads yet).
        # And one more (4.5.0, the hardening pass): a7's exec_mode —
        # the klippy-ready scenario must return to websocket mode
        # before the re-arm contract holds, and the only real-input
        # path to a transport mode is the same exec_mode a2/a3/a10
        # use (there is no clickable transport-mode control).
        # Two more (4.6.0, the cleanup pass): i3's validCacheMax
        # execs — the cache-size validator's accepted and refused
        # bounds ride the same direct path as its sibling validators
        # (typing into the pane's text field has no real-input op,
        # the i3 precedent).
        self.assertLessEqual(direct, 153)

    def test_classification_derives_from_the_mechanism(self):
        # A step's class comes from its op and the delivery record —
        # never from a declared label.
        self.assertEqual(
            runner._classify("deliver_click", {"accepted": True}), "ui-interaction")
        self.assertEqual(
            runner._classify("deliver_click", {"accepted": False}),
            "application-integration")
        self.assertEqual(runner._classify("exec_slot", None),
                         "application-integration")
        self.assertEqual(runner._classify("wait_model", None),
                         "diagnostic-probe")


class RealModeAllowlistTests(unittest.TestCase):
    def test_real_mode_allowlists_are_pinned_exactly(self):
        # The ratcheting shape: these allowlists are the only thing
        # between a scenario and a live printer. They may only
        # shrink; any change moves this pin AND the decision record
        # in the same commit.
        self.assertEqual(sorted(runner.REAL_SAFE_SLOTS), sorted([
            "reconnect", "refreshAll", "refreshWebcams", "selectWebcam",
            "setShowProbePoints", "setBedMeshPreviewVisible",
            "setSectionExpanded", "setConsoleExpanded", "setStatusCollapsed",
            "setInfoCollapsed", "setControlsCollapsed", "setConsoleHeight",
            "clearConsoleHistory"]))
        self.assertEqual(runner.REAL_SAFE_OPS, {
            "click_stage", "click_text", "model_read",
            "wait_model", "assert_model", "exec_slot", "dwell",
            "rect_of", "assert_aligned", "assert_rendered",
            "wait_rendered", "wait_rect", "dump_visible",
            "resize_window", "census"})

    def test_input_verbs_are_denied_in_real_mode(self):
        # Deny-by-default: every input verb is refused in real mode
        # unless deliberately allowlisted (none are).
        for op in ("deliver_click", "key_press", "click_jog", "emit_click",
                   "exec_file_slot", "exec_code"):
            self.assertNotIn(op, runner.REAL_SAFE_OPS)


class EvidenceRecordTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH, exist_ok=True)
        runner.EVIDENCE[:] = []
        self._old_run_dir = runner.RUN_DIR
        runner.RUN_DIR = SCRATCH

    def tearDown(self):
        runner.RUN_DIR = self._old_run_dir
        runner.EVIDENCE[:] = []

    def test_entry_shape_matches_schema_1(self):
        entry = runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "click_text", "text": "Pause"}, "f1-00",
            True, "a real click", "it landed", (SCRATCH + "/f1-00.png", None),
            time.monotonic())
        self.assertEqual(entry["schema"], 1)
        self.assertEqual(entry["scenario"], "f1")
        self.assertEqual(entry["step"], 0)
        self.assertEqual(entry["op"], "click_text")
        self.assertEqual(entry["spec"], {"op": "click_text", "text": "Pause"})
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["capture"], "f1-00.png")
        self.assertIsNone(entry["capture_error"])
        self.assertIsNone(entry["delivery"])
        self.assertIsNone(entry["geometry"])
        self.assertIsNone(entry["walk"])
        self.assertGreaterEqual(entry["duration_ms"], 0)

    def test_entry_carries_the_evidence_geometry_and_walk(self):
        # The evidence-visibility fields (C8/C9): each resolving step
        # records the element's scene rect and the walk that resolved
        # it, on their own channels — never folded into delivery.
        entry = runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "scroll_into_view", "text": "Turn off"}, "f1-01",
            True, "scrolled", "contained", (SCRATCH + "/f1-01.png", None),
            time.monotonic(), delivery={"accepted": True},
            geometry=[640, 312, 104, 36], walk={"mode": "click", "depth": 96,
                                                "items": 41, "windows": 3})
        self.assertEqual(entry["geometry"], [640, 312, 104, 36])
        self.assertEqual(entry["walk"], {"mode": "click", "depth": 96,
                                         "items": 41, "windows": 3})
        self.assertEqual(entry["delivery"], {"accepted": True})

    def test_capture_error_splits_from_the_path(self):
        entry = runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "sim_set"}, "f1-00", False, "state", "refused",
            ("/none/f1-00.png", "capture failed: empty frame"), time.monotonic())
        self.assertEqual(entry["capture"], "f1-00.png")
        self.assertEqual(entry["capture_error"], "capture failed: empty frame")
        self.assertFalse(entry["ok"])

    def test_write_evidence_lands_parseable_json(self):
        runner.EVIDENCE.append(runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "sim_arm"}, "f1-00", True, "armed", "applied",
            ("/none/f1-00.png", None), time.monotonic()))
        runner.write_evidence("test run")
        with open(os.path.join(SCRATCH, "evidence.json"), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["schema"], 1)
        self.assertEqual(record["title"], "test run")
        self.assertEqual(record["steps"][0]["scenario"], "f1")


class _FakeSubprocess:
    """subprocess.call/Popen, recorded. The second boot's gallery is
    written by whichever call the test says writes it."""

    DEVNULL = -3

    def __init__(self, log, return_codes, galley_dir=None):
        self.log = log
        self.return_codes = list(return_codes)
        self.gallery_dir = galley_dir

    def call(self, argv, env=None, **kwargs):
        self.log.append(("call", list(argv), dict(env or {})))
        if argv[0] != sys.executable:
            # The kill between the boots: no exit code of its own to
            # spend, and no gallery of its own to write.
            return 0
        if self.gallery_dir and argv[2].endswith("2"):
            os.makedirs(self.gallery_dir, exist_ok=True)
            with open(os.path.join(self.gallery_dir, "index.html"), "w",
                      encoding="utf-8") as handle:
                handle.write("<html></html>")
        return self.return_codes.pop(0) if self.return_codes else 0

    def Popen(self, argv, cwd=None, **kwargs):
        self.log.append(("popen", list(argv), cwd))
        return object()


class _FakeHost:
    """The dispatch's two calls, named so the test can see them."""

    def kill_command(self, system, **kwargs):
        return ["kill", system]

    def launch_command(self, system, binary, working_dir=None, **kwargs):
        return {"argv": ["launch", binary], "cwd": working_dir}


class TwoBootRunTests(unittest.TestCase):
    """The two-boot legs' orchestration, pinned without an app.

    On a native host this is the whole pair — each boot is the same
    runner entry point the container leg calls, and the work between
    them (stop the first app, clear the rendezvous, launch the second)
    has no shell to live in — so what each boot sees is pinned here.
    """

    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH, exist_ok=True)
        self.log = []
        self._old = {name: getattr(runner, name)
                     for name in ("RUN_DIR", "RPC_DIR", "subprocess",
                                  "native_host", "wait_for_driver")}
        self._old_env = os.environ.get("HARNESS_CURA_BIN")
        os.environ["HARNESS_CURA_BIN"] = "/Applications/Cura.app/Contents/MacOS/UltiMaker-Cura"
        os.environ["HARNESS_CURA_CWD"] = "/Applications/Cura.app/Contents/MacOS"
        runner.RUN_DIR = SCRATCH
        runner.RPC_DIR = SCRATCH
        runner.native_host = _FakeHost()
        runner.wait_for_driver = lambda deadline_s: {"ok": True}

    def tearDown(self):
        for name, value in self._old.items():
            setattr(runner, name, value)
        os.environ.pop("HARNESS_CURA_CWD", None)
        if self._old_env is None:
            os.environ.pop("HARNESS_CURA_BIN", None)
        else:
            os.environ["HARNESS_CURA_BIN"] = self._old_env
        shutil.rmtree(SCRATCH, ignore_errors=True)

    def _set_subprocess(self, return_codes=(0, 0), gallery=True):
        runner.subprocess = _FakeSubprocess(
            self.log, return_codes,
            galley_dir=os.path.join(SCRATCH, "boot2") if gallery else None)

    def _boot_calls(self):
        """The boots alone — the kill between them is a call too, and
        it must not be what a test reads as boot 1."""
        return [entry for entry in self.log
                if entry[0] == "call" and entry[1][0] == sys.executable]

    def test_boot_two_gets_its_own_gallery_and_the_same_handoff_document(self):
        self._set_subprocess()
        self.assertEqual(runner.two_boot_run("firstinstall"), 0)
        boots = self._boot_calls()
        self.assertEqual(len(boots), 2)
        self.assertEqual([entry[1][2] for entry in boots],
                         ["firstinstall1", "firstinstall2"])
        first, second = (entry[2] for entry in boots)
        self.assertEqual(first["HARNESS_RUN_DIR"], SCRATCH)
        # Boot 2's gallery is its own directory (the container leg's
        # layout), and the document it diffs against is boot 1's.
        self.assertEqual(second["HARNESS_RUN_DIR"], os.path.join(SCRATCH, "boot2"))
        document = os.path.join(SCRATCH, "boot1-document.json")
        self.assertEqual(first["HARNESS_BOOT1_DOC"], document)
        self.assertEqual(second["HARNESS_BOOT1_DOC"], document)

    def test_the_first_boot_is_stopped_and_the_rendezvous_cleared_before_the_second(self):
        self._set_subprocess()
        for name in ("harness_port.txt", "harness_token.txt"):
            with open(os.path.join(SCRATCH, name), "w", encoding="utf-8") as handle:
                handle.write("stale")
        self.assertEqual(runner.two_boot_run("migration"), 0)
        launches = [entry for entry in self.log if entry[0] == "popen"]
        self.assertEqual(len(launches), 1)
        # The app is launched with the binary and directory the setup
        # script exported, and the stale rendezvous is gone — a stale
        # port would send boot 2 straight to a dead socket.
        self.assertEqual(launches[0][1], ["launch", os.environ["HARNESS_CURA_BIN"]])
        self.assertEqual(launches[0][2], os.environ["HARNESS_CURA_CWD"])
        self.assertFalse(os.path.exists(os.path.join(SCRATCH, "harness_port.txt")))
        self.assertFalse(os.path.exists(os.path.join(SCRATCH, "harness_token.txt")))

    def test_the_modes_are_the_two_boot_pairs(self):
        self.assertEqual(runner.TWO_BOOT_MODES["firstinstall"],
                         ("firstinstall1", "firstinstall2"))
        self.assertEqual(runner.TWO_BOOT_MODES["migration"], ("migration1", "migration2"))

    def test_the_first_failing_boot_decides_the_verdict(self):
        self._set_subprocess(return_codes=(3, 0))
        self.assertEqual(runner.two_boot_run("firstinstall"), 3)
        self._set_subprocess(return_codes=(0, 4))
        self.assertEqual(runner.two_boot_run("firstinstall"), 4)

    def test_a_second_boot_without_a_gallery_fails_the_leg(self):
        # The workflow's EVIDENCE MISSING guard watches the first
        # boot's directory only, so the second boot's gallery has to be
        # defended here.
        self._set_subprocess(gallery=False)
        self.assertEqual(runner.two_boot_run("firstinstall"), 1)

    def test_a_host_that_exports_no_binary_cannot_relaunch(self):
        # An older setup script leaves the leg unable to boot twice:
        # that is a failure with a name, not a silent second boot of
        # the same app.
        self._set_subprocess(return_codes=(0, 0))
        os.environ.pop("HARNESS_CURA_BIN")
        self.assertEqual(runner.two_boot_run("firstinstall"), 1)
        self.assertEqual([entry for entry in self.log if entry[0] == "popen"], [])

    def test_a_driver_that_never_answers_stops_the_leg(self):
        # Without an answer there is nothing for boot 2 to drive: the
        # leg fails here rather than in a traceback five minutes later.
        self._set_subprocess()
        runner.wait_for_driver = lambda deadline_s: None
        self.assertEqual(runner.two_boot_run("firstinstall"), 1)
        self.assertEqual(len(self._boot_calls()), 1)

    def test_boot_ones_log_is_lifted_before_the_second_launch(self):
        # The relaunch truncates cura.log, so boot 1's half of the
        # evidence has to be taken between the boots, not at the end.
        self._set_subprocess()
        config = Path(tempfile.mkdtemp(prefix="two-boot-config-", dir="/tmp/mpf"))
        (config / "cura.log").write_text("boot one", encoding="utf-8")
        os.environ["HARNESS_CURA_CONFIG"] = str(config)
        try:
            self.assertEqual(runner.two_boot_run("firstinstall"), 0)
        finally:
            os.environ.pop("HARNESS_CURA_CONFIG", None)
            shutil.rmtree(config, ignore_errors=True)
        self.assertTrue(os.path.isfile(os.path.join(SCRATCH, "cura.log-boot1")))


class HarvestCuraLogTests(unittest.TestCase):
    """Cura's own log is kept beside the evidence.

    A native host has no ui_test.sh to harvest it, and that file is
    what says why a driver never answered — so it is copied out of the
    config directory the setup script exported, rotated files included.
    """

    def setUp(self):
        self.src = Path(tempfile.mkdtemp(prefix="harvest-config-", dir="/tmp/mpf"))
        self.dest = Path(tempfile.mkdtemp(prefix="harvest-dest-", dir="/tmp/mpf"))
        self._old = os.environ.get("HARNESS_CURA_CONFIG")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HARNESS_CURA_CONFIG", None)
        else:
            os.environ["HARNESS_CURA_CONFIG"] = self._old
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_the_log_and_its_rotated_neighbour_are_both_kept(self):
        (self.src / "cura.log").write_text("newest", encoding="utf-8")
        (self.src / "cura.log.1").write_text("older", encoding="utf-8")
        (self.src / "cura.cfg").write_text("not a log", encoding="utf-8")
        os.environ["HARNESS_CURA_CONFIG"] = str(self.src)
        self.assertEqual(runner.harvest_cura_log(str(self.dest)), 2)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()),
                         ["cura.log", "cura.log.1"])

    def test_the_boot_a_log_came_from_is_in_its_name(self):
        (self.src / "cura.log").write_text("boot one", encoding="utf-8")
        os.environ["HARNESS_CURA_CONFIG"] = str(self.src)
        self.assertEqual(runner.harvest_cura_log(str(self.dest), suffix="-boot1"), 1)
        self.assertEqual((self.dest / "cura.log-boot1").read_text(encoding="utf-8"),
                         "boot one")

    def test_a_host_without_a_config_dir_is_not_an_error(self):
        # The container leg exports no HARNESS_CURA_CONFIG: ui_test.sh
        # harvests the seeded roots itself.
        os.environ.pop("HARNESS_CURA_CONFIG", None)
        self.assertEqual(runner.harvest_cura_log(str(self.dest)), 0)
        os.environ["HARNESS_CURA_CONFIG"] = str(self.src / "absent")
        self.assertEqual(runner.harvest_cura_log(str(self.dest)), 0)
        self.assertEqual(list(self.dest.iterdir()), [])


class StaticLegTests(unittest.TestCase):
    """The static-green class: a leg whose screen does not move.

    A green verdict over a frozen screen is not a success — the
    macOS motion leg passed 73 of 73 steps while its 216 s recording
    showed no visible change for about 173 s. The thresholds are
    measured across the 51 galleries, and the frame comparison has to
    tolerate the encoder's own dither, which is what made an exact-hash
    reading miss a 394 s frozen stretch by two orders of magnitude."""

    def _sequence(self, plan):
        # plan: a list of (count, brightness) — the frames of each move.
        frames = []
        for count, level in plan:
            frames.extend([bytes([level]) * 8] * count)
        return frames

    def test_a_leg_frozen_for_most_of_its_length_fails(self):
        # 20 s of motion then 80 s of one picture: the macOS motion
        # leg's shape, in miniature.
        frames = self._sequence([(20, 10), (80, 90)])
        verdict = runner.static_verdict(frames)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["span_s"], 80)
        self.assertEqual(verdict["share"], 0.8)

    def test_the_encoders_own_dither_still_reads_as_still(self):
        # The measured encoder behaviour: 254 of 393 frozen pairs
        # differ by exactly one grey level. An exact comparison sees
        # 70 distinct frames and calls this leg busy; the frozen leg it
        # really is has to read as frozen.
        frames = [bytes([10]) * 8] * 30
        frames += [bytes([40 if index % 2 else 41]) * 8 for index in range(70)]
        self.assertEqual(len(set(frames)), 3)
        verdict = runner.static_verdict(frames)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["span_s"], 70)

    def test_a_moving_leg_passes(self):
        frames = [bytes([(index * 7) % 250]) * 8 for index in range(200)]
        verdict = runner.static_verdict(frames)
        self.assertTrue(verdict["ok"])
        self.assertLess(verdict["span_s"], runner.STATIC_LEG_SECONDS)

    def test_a_still_span_under_the_absolute_floor_passes(self):
        # Legitimate idle: one step waiting on a model, a budget of
        # 15-30 s. The longest span on a leg that is not one of the
        # frozen macOS ones is 59 s, and no budget reaches the floor.
        frames = self._sequence([(30, 10), (45, 90), (30, 200)])
        self.assertTrue(runner.static_verdict(frames)["ok"])

    def test_a_leg_too_short_to_judge_is_not_judged(self):
        self.assertIsNone(runner.static_verdict([]))
        self.assertIsNone(runner.static_verdict([b"\x00" * 8]))

    def test_the_frame_length_is_the_duration(self):
        # The decode is one frame per second, so a run's length IS its
        # seconds and no frame rate has to be carried alongside.
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn("fps=1,scale=", source)
        self.assertIn("static_verdict(frames)", source)

    def test_the_exit_path_judges_every_recording(self):
        # The check must cover every mode — the suite groups, the
        # boot-only legs and their second boots — so it runs in the
        # runner's own exit path over the run directory, after the
        # recorders are closed, and it lands in evidence.json as well
        # as the gallery.
        source = Path(runner.__file__).read_text(encoding="utf-8")
        harvest = source.index("harvest_cura_log(RUN_DIR)")
        judge = source.index("static_leg_report(RUN_DIR)", harvest)
        self.assertLess(harvest, judge, "every recording must be closed first")
        self.assertIn('"static_leg"', source)
        self.assertIn("for root, _dirs, names in os.walk(run_dir)", source)

    def test_the_thresholds_are_the_measured_ones(self):
        # 60 s clears every legitimate idle measured across the 51
        # galleries (the longest non-frozen span is 59 s) and the
        # share separates a leg that froze (39-92% of its duration)
        # from one that spent a step waiting.
        self.assertEqual(runner.STATIC_LEG_SECONDS, 60.0)
        self.assertEqual(runner.STATIC_LEG_SHARE, 0.35)
        self.assertEqual(runner.STATIC_FRAME_MAD, 1.0)


if __name__ == "__main__":
    unittest.main()

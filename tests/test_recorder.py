"""Artifact schema, still cadence, and the coverage map.

Image writing is injected, so this runs with no OpenCV.
"""

import json
import tempfile
import unittest
from pathlib import Path

from chiphealth.config import RunConfig
from chiphealth.detector import Detector, Event
from chiphealth.config import DetectorConfig
from chiphealth.recorder import (CoverageMap, DEGRADED, FAIL, PASS, RunRecorder,
                                 UNKNOWN)
from chiphealth.sweep import AXIS_COL, KIND_TRAVEL, Step


def a_step(idx=0, row=10, col=10):
    return Step(idx=idx, row=row, col=col, h=20, w=20, axis=AXIS_COL,
                direction=+1, kind=KIND_TRAVEL, band=0)


class Result:
    def __init__(self, blocks=(), lag=0.0, clean=True):
        self.tested_blocks = set(blocks)
        self.lag = lag
        self.primary_area = 400.0
        self.clean = clean


class TestCoverageMap(unittest.TestCase):

    def test_shape(self):
        cm = CoverageMap(128, 128, 4)
        self.assertEqual((cm.rows, cm.cols), (32, 32))
        self.assertEqual(cm.counts()[UNKNOWN], 1024)

    def test_starts_unknown_not_pass(self):
        """Untested must never read as healthy."""
        self.assertEqual(CoverageMap(128, 128, 4).get(0, 0), UNKNOWN)

    def test_worst_verdict_wins(self):
        cm = CoverageMap(128, 128, 4)
        cm.mark(1, 1, PASS)
        cm.mark(1, 1, FAIL)
        cm.mark(1, 1, PASS)  # a later clean pass must not erase a fault
        self.assertEqual(cm.get(1, 1), FAIL)

    def test_degraded_does_not_downgrade_fail(self):
        cm = CoverageMap(128, 128, 4)
        cm.mark(2, 2, FAIL)
        cm.mark(2, 2, DEGRADED)
        self.assertEqual(cm.get(2, 2), FAIL)

    def test_event_kinds_map_to_verdicts(self):
        cm = CoverageMap(128, 128, 4)
        cm.mark_event("drag", 0, 0)
        cm.mark_event("no_movement", 0, 1)
        cm.mark_event("residue", 0, 2)
        cm.mark_event("unreachable", 0, 3)
        self.assertEqual(cm.get(0, 0), DEGRADED)
        self.assertEqual(cm.get(0, 1), FAIL)
        self.assertEqual(cm.get(0, 2), DEGRADED)
        self.assertEqual(cm.get(0, 3), FAIL)

    def test_suspicious_blocks_feed_the_fine_pass(self):
        cm = CoverageMap(128, 128, 4)
        cm.mark_tested([(0, 0), (0, 1)])
        cm.mark_event("drag", 5, 5)
        cm.mark_event("no_movement", 9, 9)
        self.assertEqual(sorted(cm.suspicious_blocks()), [(5, 5), (9, 9)])

    def test_out_of_range_marks_ignored(self):
        cm = CoverageMap(128, 128, 4)
        cm.mark(999, 999, FAIL)  # must not raise
        self.assertEqual(cm.counts()[FAIL], 0)


class RecorderCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = RunConfig()
        self.cfg.runs_root = Path(self.tmp.name)
        self.written: list[str] = []
        self.rec = RunRecorder(self.cfg, "20260807T120000Z", "chip-A",
                               image_writer=lambda p, f: self.written.append(p))
        self.rec.start()

    def tearDown(self):
        self.tmp.cleanup()

    def read_jsonl(self, path):
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestStillCadence(RecorderCase):

    def test_first_still_always_taken(self):
        self.assertTrue(self.rec.should_capture_still(0.0))

    def test_five_second_cadence(self):
        self.rec.capture_still(0.0, "frame")
        self.assertFalse(self.rec.should_capture_still(2.0))
        self.assertFalse(self.rec.should_capture_still(4.9))
        self.assertTrue(self.rec.should_capture_still(5.0))
        self.assertTrue(self.rec.should_capture_still(12.0))

    def test_routine_and_flagged_are_kept_apart(self):
        self.rec.capture_still(0.0, "f", flagged=False)
        self.rec.capture_still(5.0, "f", flagged=True)
        self.assertEqual(self.rec.n_routine_stills, 1)
        self.assertEqual(self.rec.n_flagged_stills, 1)
        self.assertTrue(any("routine" in p for p in self.written))
        self.assertTrue(any("flagged" in p for p in self.written))

    def test_missing_writer_counts_rather_than_silently_dropping(self):
        rec = RunRecorder(self.cfg, "r2", "chip-A", image_writer=None)
        rec.start()
        rec.capture_still(0.0, "frame")
        self.assertEqual(rec.images_skipped, 1)


class TestEvents(RecorderCase):

    def _event(self, kind="drag", br=5, bc=5):
        return Event(kind=kind, step_idx=42, frame_index=100, t=21.0,
                     row=float(br * 4 + 1), col=float(bc * 4 + 1),
                     block_row=br, block_col=bc, severity=3.5,
                     detail="test event")

    def test_event_row_carries_the_dataset_fields(self):
        self.rec.log_event(self._event(), full_frame="F", roi="R")
        rows = self.read_jsonl(self.rec.paths.events_jsonl)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        for key in ("event_id", "run_id", "chip_id", "schema_version",
                    "detector_version", "label_source", "kind", "block_row",
                    "block_col", "severity", "voltage", "step_delay_s",
                    "autofocus", "stage", "sample"):
            self.assertIn(key, r, f"missing {key}")
        self.assertEqual(r["label_source"], "auto")
        self.assertEqual(r["voltage"], [45, 45, 45, 0, 0, 0, 0, 0, 0])

    def test_event_saves_roi_and_full_frame(self):
        self.rec.log_event(self._event(), full_frame="F", roi="R")
        self.assertTrue(any(p.endswith("_full.jpg") for p in self.written))
        self.assertTrue(any(p.endswith("_roi.jpg") for p in self.written))

    def test_event_ids_are_unique(self):
        ids = {self.rec.log_event(self._event(bc=i)) for i in range(5)}
        self.assertEqual(len(ids), 5)

    def test_event_updates_coverage(self):
        self.rec.log_event(self._event(kind="no_movement", br=7, bc=8))
        self.assertEqual(self.rec.coverage.get(7, 8), FAIL)


class TestNegatives(RecorderCase):

    def test_sampling_is_seeded_and_reproducible(self):
        a = RunRecorder(self.cfg, "ra", "chip", image_writer=lambda p, f: None,
                        rng_seed=7)
        b = RunRecorder(self.cfg, "rb", "chip", image_writer=lambda p, f: None,
                        rng_seed=7)
        a.start(); b.start()
        a.cfg.capture.negative_sample_rate = 0.5
        hits_a = [bool(a.maybe_sample_negative(a_step(i), "f")) for i in range(40)]
        hits_b = [bool(b.maybe_sample_negative(a_step(i), "f")) for i in range(40)]
        self.assertEqual(hits_a, hits_b)

    def test_negatives_are_written_as_records_too(self):
        self.rec.cfg.capture.negative_sample_rate = 1.0
        self.rec.maybe_sample_negative(a_step(3), "frame")
        rows = self.read_jsonl(self.rec.paths.events_jsonl)
        self.assertEqual(rows[0]["sample"], "negative")
        self.assertEqual(rows[0]["kind"], "clean")

    def test_no_frame_no_sample(self):
        self.rec.cfg.capture.negative_sample_rate = 1.0
        self.assertIsNone(self.rec.maybe_sample_negative(a_step(), None))


class TestTimelineAndSummary(RecorderCase):

    def test_steps_append_and_mark_coverage(self):
        for i in range(3):
            self.rec.log_step(a_step(i), Result(blocks=[(0, i)], lag=0.5))
        rows = self.read_jsonl(self.rec.paths.timeline)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["leading_edge"], 29)
        self.assertEqual(self.rec.coverage.counts()[PASS], 3)

    def test_prompts_are_recorded_with_what_was_asked(self):
        self.rec.log_prompt("Load 20x20 droplet at row 5, col 10", "loaded", 0.0)
        self.rec.finalize()
        meta = json.loads(self.rec.paths.run_json.read_text())
        self.assertEqual(len(meta["prompts"]), 1)
        self.assertIn("20x20", meta["prompts"][0]["asked"])

    def test_notes_surface_in_the_summary(self):
        self.rec.note("3 fine-pass targets dropped by the cap")
        self.rec.finalize()
        text = self.rec.paths.summary.read_text()
        self.assertIn("dropped by the cap", text)

    def test_summary_says_unknown_is_not_healthy(self):
        self.rec.finalize()
        self.assertIn("not healthy", self.rec.paths.summary.read_text())

    def test_finalize_writes_everything(self):
        self.rec.log_step(a_step(0), Result(blocks=[(0, 0)]))
        stats = self.rec.finalize()
        self.assertTrue(self.rec.paths.coverage.exists())
        self.assertTrue(self.rec.paths.summary.exists())
        self.assertTrue(self.rec.paths.run_json.exists())
        self.assertEqual(stats["steps"], 1)
        cov = json.loads(self.rec.paths.coverage.read_text())
        self.assertEqual(cov["rows"], 32)
        self.assertEqual(len(cov["grid"]), 32)

    def test_run_json_pins_versions_and_config(self):
        meta = json.loads(self.rec.paths.run_json.read_text())
        self.assertIn("schema_version", meta)
        self.assertIn("detector_version", meta)
        self.assertEqual(meta["chip_id"], "chip-A")
        self.assertEqual(meta["config"]["chip"]["rows"], 128)


class TestChipIdGuard(unittest.TestCase):

    def test_missing_chip_id_is_refused(self):
        """Without it, longitudinal history silently mixes chips."""
        cfg = RunConfig()
        with self.assertRaises(ValueError):
            cfg.require_chip_id()
        cfg.chip_id = "  "
        with self.assertRaises(ValueError):
            cfg.require_chip_id()
        cfg.chip_id = " chip-B "
        self.assertEqual(cfg.require_chip_id(), "chip-B")


if __name__ == "__main__":
    unittest.main()


class TestCodeVersion(unittest.TestCase):
    """run.json has to say which code produced it.

    schema_version and detector_version only change when someone remembers to
    change them; the commit is exact. This artifact is meant to be read months
    later, when there is no other way to answer the question.
    """

    def test_it_reports_a_commit_and_a_dirty_flag(self):
        from chiphealth.recorder import code_version
        v = code_version()
        self.assertIn("commit", v)
        self.assertIn("dirty", v)
        if v["commit"] is not None:
            self.assertRegex(v["commit"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(v["dirty"], bool)

    def test_a_dirty_tree_says_so_loudly(self):
        """A hash from a dirty tree names code the run did NOT use. Recording
        it without the warning would be worse than recording nothing."""
        from chiphealth.recorder import code_version
        v = code_version()
        if v.get("dirty"):
            self.assertIn("UNCOMMITTED", v["note"])
            self.assertIn("does NOT identify", v["note"])
            self.assertTrue(v["dirty_files"])

    def test_dirty_paths_are_not_mangled(self):
        """Regression: a bare .strip() on the porcelain output ate the leading
        space of the FIRST line only, so `[3:]` chopped a character off that one
        path -- 'chiphealth/__init__.py' came back as 'hiphealth/__init__.py'.
        Every entry keeps its two-column status field, so a reader can also tell
        a modified tracked file from untracked noise."""
        import subprocess
        from chiphealth import recorder

        class Out:
            returncode = 0
            stdout = (" M chiphealth/__init__.py\n"
                      " M chiphealth/config.py\n"
                      "?? .scratch/\n")

        class Sha(Out):
            stdout = "a" * 40 + "\n"

        calls = []
        real = subprocess.run

        def fake(args, **kw):
            calls.append(args)
            return Sha() if "rev-parse" in args else Out()

        subprocess.run = fake
        try:
            v = recorder.code_version()
        finally:
            subprocess.run = real
        self.assertEqual(v["commit"], "a" * 40)
        self.assertTrue(v["dirty"])
        self.assertEqual(v["dirty_files"],
                         [" M chiphealth/__init__.py",
                          " M chiphealth/config.py",
                          "?? .scratch/"])
        # the path that used to lose its first character
        self.assertTrue(any(l.endswith("chiphealth/__init__.py")
                            for l in v["dirty_files"]))

    def test_a_clean_tree_carries_no_warning(self):
        import subprocess
        from chiphealth import recorder

        class Clean:
            returncode = 0
            stdout = ""

        class Sha:
            returncode = 0
            stdout = "b" * 40 + "\n"

        real = subprocess.run
        subprocess.run = lambda args, **kw: (Sha() if "rev-parse" in args
                                             else Clean())
        try:
            v = recorder.code_version()
        finally:
            subprocess.run = real
        self.assertEqual(v["commit"], "b" * 40)
        self.assertFalse(v["dirty"])
        self.assertNotIn("note", v)
        self.assertNotIn("dirty_files", v)

    def test_it_never_raises_when_git_is_unavailable(self):
        """Not a repo, no git installed, or a timeout must all degrade to an
        explicit 'unknown', not a crash and not a missing key."""
        import subprocess
        from chiphealth import recorder

        def boom(*a, **k):
            raise FileNotFoundError("no git here")

        real = subprocess.run
        subprocess.run = boom
        try:
            v = recorder.code_version()
        finally:
            subprocess.run = real
        self.assertIsNone(v["commit"])
        self.assertIsNone(v["dirty"])
        self.assertIn("no git commit available", v["note"])

    def test_a_nonzero_git_exit_is_treated_as_unknown(self):
        import subprocess
        from chiphealth import recorder

        class Fail:
            returncode = 128
            stdout = ""

        real = subprocess.run
        subprocess.run = lambda *a, **k: Fail()
        try:
            v = recorder.code_version()
        finally:
            subprocess.run = real
        self.assertIsNone(v["commit"])
        self.assertIn("not a", v["note"])

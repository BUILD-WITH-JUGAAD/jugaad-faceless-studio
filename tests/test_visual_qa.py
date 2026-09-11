#!/usr/bin/env python3
"""Phase 8 — visual QA diagnostics (deterministic fixtures)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from visual_beat import make_visual_beat  # noqa: E402
from visual_qa import (  # noqa: E402
    analyze_visual_plan,
    density_metrics,
    format_visual_report,
    reuse_metrics,
    semantic_flags,
    validate_visual_beats,
)
from stock_adapter import visual_planner_enabled  # noqa: E402


def _beats(spans):
    out = []
    for i, (s, e) in enumerate(spans):
        out.append(make_visual_beat(s, e, narration="Beat {0}".format(i), beat_id="b{0}".format(i)))
    return out


def _assets(rows):
    """rows: (path, start, end, **extra)"""
    out = []
    for row in rows:
        path, start, end = row[0], row[1], row[2]
        extra = row[3] if len(row) > 3 else {}
        item = {"path": path, "start": start, "end": end}
        item.update(extra)
        out.append(item)
    return out


class TimelineValidationTests(unittest.TestCase):
    def test_valid_sequential_beats(self):
        beats = _beats([(0, 4), (4, 8), (8, 12)])
        report = validate_visual_beats(beats, 12.0)
        self.assertTrue(report["valid"])
        self.assertEqual(report["beat_count"], 3)
        self.assertAlmostEqual(report["coverage_seconds"], 12.0, places=2)
        self.assertEqual(report["gap_seconds"], 0.0)
        self.assertEqual(report["overlap_seconds"], 0.0)

    def test_gaps(self):
        beats = _beats([(0, 3), (5, 8)])
        report = validate_visual_beats(beats, 8.0)
        self.assertFalse(report["valid"])
        self.assertGreater(report["gap_seconds"], 1.5)
        self.assertTrue(any("gap" in i for i in report["issues"]))

    def test_overlaps(self):
        beats = _beats([(0, 5), (4, 8)])
        report = validate_visual_beats(beats, 8.0)
        self.assertFalse(report["valid"])
        self.assertGreater(report["overlap_seconds"], 0.5)

    def test_zero_and_negative_duration(self):
        # Invalid spans as plain timed dicts (VisualBeat ctor rejects these).
        beats = [
            {"id": "z", "start": 0.0, "end": 0.0},
            {"id": "n", "start": 2.0, "end": 1.0},
        ]
        report = validate_visual_beats(beats, 5.0)
        self.assertFalse(report["valid"])
        self.assertTrue(any("duration" in i or "end" in i for i in report["issues"]))

    def test_beyond_narration(self):
        beats = _beats([(0, 4), (4, 12)])
        report = validate_visual_beats(beats, 10.0)
        self.assertFalse(report["valid"])
        self.assertTrue(any("exceeds narration" in i for i in report["issues"]))


class DensityTests(unittest.TestCase):
    def test_short_narration_scaling(self):
        beats = _beats([(i * 4, i * 4 + 4) for i in range(5)])
        dens = density_metrics(beats, 20.0)
        self.assertEqual(dens["beat_count"], 5)
        self.assertIn(dens["density_status"], {"ok", "low"})
        # Must not require ~24–50 absolute beats on a 20s clip.
        self.assertNotEqual(dens["density_status"], "sparse")

    def test_180_second_density(self):
        # ~4s holds → 45 beats
        beats = _beats([(i * 4.0, min(180.0, i * 4.0 + 4.0)) for i in range(45)])
        dens = density_metrics(beats, 180.0)
        self.assertEqual(dens["beat_count"], 45)
        self.assertEqual(dens["density_status"], "ok")
        self.assertGreater(dens["pct_beats_le_6s"], 90)

    def test_sparse_180(self):
        beats = _beats([(0, 60), (60, 120), (120, 180)])
        dens = density_metrics(beats, 180.0)
        self.assertEqual(dens["density_status"], "sparse")


class ReuseTests(unittest.TestCase):
    def test_duplicate_and_adjacent(self):
        assets = _assets([
            ("/a.mp4", 0, 3),
            ("/b.mp4", 3, 6),
            ("/b.mp4", 6, 9),
            ("/a.mp4", 9, 12),
        ])
        reuse = reuse_metrics(assets)
        self.assertEqual(reuse["asset_references"], 4)
        self.assertEqual(reuse["unique_assets"], 2)
        self.assertEqual(reuse["reused_references"], 2)
        self.assertEqual(reuse["adjacent_duplicates"], 1)
        self.assertEqual(reuse["max_consecutive_reuse"], 2)

    def test_non_adjacent_reuse_ok_shape(self):
        assets = _assets([
            ("/a.mp4", 0, 3),
            ("/b.mp4", 3, 6),
            ("/a.mp4", 6, 9),
        ])
        reuse = reuse_metrics(assets)
        self.assertEqual(reuse["adjacent_duplicates"], 0)
        self.assertEqual(reuse["unique_assets"], 2)
        self.assertEqual(reuse["reused_references"], 1)

    def test_asset_reuse_counting_empty(self):
        reuse = reuse_metrics([])
        self.assertEqual(reuse["unique_assets"], 0)
        self.assertEqual(reuse["failed_or_missing"], 0)


class SemanticTests(unittest.TestCase):
    def test_fallback_and_low_relevance(self):
        beats = [
            make_visual_beat(
                0, 4,
                narration="Firefighter running.",
                visual_intent="firefighter running into fire",
                queries=["firefighter running"],
                relevance_score=0.9,
                selected_asset={"id": 1, "score": 0.9},
            ),
            make_visual_beat(
                4, 8,
                narration="Dark room.",
                visual_intent="",
                queries=[],
                relevance_score=0.2,
                selected_asset={
                    "id": 2,
                    "score": 0.2,
                    "used_fallback_query": True,
                    "low_confidence": True,
                },
            ),
        ]
        flags = semantic_flags(beats, min_relevance=0.6)
        self.assertEqual(flags["fallback_queries_used"], 1)
        self.assertGreaterEqual(flags["low_relevance_beats"], 1)
        self.assertGreaterEqual(flags["missing_visual_intent"], 1)


class AnalyzeReportTests(unittest.TestCase):
    def test_full_analyze_and_format(self):
        beats = _beats([(0, 4), (4, 8), (8, 12)])
        for b in beats:
            b.visual_intent = "scene"
            b.queries = ["scene query"]
            b.selected_asset = {"id": b.id, "score": 0.8}
            b.relevance_score = 0.8
        assets = _assets([
            ("/1.mp4", 0, 4),
            ("/2.mp4", 4, 8),
            ("/3.mp4", 8, 12),
        ])
        report = analyze_visual_plan(beats, assets, 12.0, provider="pexels")
        self.assertEqual(report["planned_beats"], 3)
        self.assertEqual(report["successful_assets"], 3)
        self.assertEqual(report["status"], "PASS")
        text = format_visual_report(report)
        self.assertIn("VISUAL QA", text)
        self.assertIn("Status: PASS", text)

    def test_empty_failed_assets(self):
        beats = _beats([(0, 5), (5, 10)])
        assets = [
            {"path": "/ok.mp4", "start": 0, "end": 5},
            {"start": 5, "end": 10, "miss": True},
        ]
        report = analyze_visual_plan(beats, assets, 10.0)
        self.assertEqual(report["successful_assets"], 1)
        self.assertEqual(report["failed_assets"], 1)
        self.assertIn(report["status"], {"PASS WITH WARNINGS", "FAIL"})

    def test_planner_disabled_compatibility(self):
        """QA helpers stay inert to rendering; flag gate for emit remains planner-only."""
        with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", False):
            self.assertFalse(visual_planner_enabled())
        # analyze still works as a pure function without planner.
        report = analyze_visual_plan([], [], 30.0)
        self.assertEqual(report["status"], "EMPTY")


if __name__ == "__main__":
    unittest.main()

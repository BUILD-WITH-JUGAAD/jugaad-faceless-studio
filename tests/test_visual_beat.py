#!/usr/bin/env python3
"""VisualBeat data model — Phase 2 only (no planner / pipeline wiring)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visual_beat import VisualBeat, make_visual_beat  # noqa: E402


class VisualBeatTests(unittest.TestCase):
    def test_create_valid_beat(self):
        beat = make_visual_beat(
            start=0.0,
            end=4.5,
            narration="The fisherman returned to the village before dawn.",
            beat_id="vb_001",
            subject=["fisherman"],
            action=["walking"],
            location=["coastal village"],
            time_context="dawn",
            mood="uneasy",
            objects=["strange object"],
            visual_intent="establishing shot",
            queries=["fisherman walking beach dawn"],
            fallback_queries=["coastal village sunrise"],
            relevance_score=7.5,
        )
        self.assertEqual(beat.id, "vb_001")
        self.assertEqual(beat.start, 0.0)
        self.assertEqual(beat.end, 4.5)
        self.assertAlmostEqual(beat.duration, 4.5)
        self.assertIn("fisherman", beat.narration.lower())
        self.assertEqual(beat.subject, ["fisherman"])
        self.assertEqual(beat.action, ["walking"])
        self.assertEqual(beat.location, ["coastal village"])
        self.assertEqual(beat.time_context, "dawn")
        self.assertEqual(beat.mood, "uneasy")
        self.assertEqual(beat.objects, ["strange object"])
        self.assertEqual(beat.visual_intent, "establishing shot")
        self.assertEqual(beat.asset_type_preference, ["video", "image"])
        self.assertEqual(beat.queries, ["fisherman walking beach dawn"])
        self.assertEqual(beat.fallback_queries, ["coastal village sunrise"])
        self.assertEqual(beat.candidate_assets, [])
        self.assertIsNone(beat.selected_asset)
        self.assertEqual(beat.relevance_score, 7.5)

    def test_defaults_and_optional_fields(self):
        beat = make_visual_beat(start=1.0, end=3.0)
        self.assertTrue(beat.id)
        self.assertEqual(beat.narration, "")
        self.assertEqual(beat.subject, [])
        self.assertEqual(beat.action, [])
        self.assertEqual(beat.location, [])
        self.assertIsNone(beat.time_context)
        self.assertIsNone(beat.mood)
        self.assertEqual(beat.objects, [])
        self.assertEqual(beat.visual_intent, "")
        self.assertEqual(beat.asset_type_preference, ["video", "image"])
        self.assertEqual(beat.queries, [])
        self.assertEqual(beat.fallback_queries, [])
        self.assertEqual(beat.candidate_assets, [])
        self.assertIsNone(beat.selected_asset)
        self.assertEqual(beat.relevance_score, 0.0)
        self.assertAlmostEqual(beat.duration, 2.0)

    def test_to_dict_and_from_dict_roundtrip(self):
        original = make_visual_beat(
            start=2.0,
            end=6.0,
            narration="Empty street before sunrise.",
            beat_id="roundtrip",
            subject=["street"],
            mood="quiet",
            queries=["empty coastal street fog"],
            candidate_assets=[{"id": "pex_1", "score": 4.2}],
            selected_asset={"id": "pex_1"},
            relevance_score=4.2,
        )
        payload = original.to_dict()
        self.assertEqual(payload["id"], "roundtrip")
        self.assertEqual(payload["start"], 2.0)
        self.assertEqual(payload["end"], 6.0)
        self.assertEqual(payload["queries"], ["empty coastal street fog"])
        restored = VisualBeat.from_dict(payload)
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_from_dict_infers_duration(self):
        beat = VisualBeat.from_dict({
            "id": "infer",
            "start": 10.0,
            "end": 14.0,
            "narration": "Wet boots on sand.",
        })
        self.assertAlmostEqual(beat.duration, 4.0)

    def test_invalid_timing_end_before_start(self):
        with self.assertRaises(ValueError):
            make_visual_beat(start=5.0, end=2.0)

    def test_invalid_timing_equal_span(self):
        with self.assertRaises(ValueError):
            make_visual_beat(start=3.0, end=3.0)

    def test_invalid_timing_negative_start(self):
        with self.assertRaises(ValueError):
            make_visual_beat(start=-0.1, end=2.0)

    def test_invalid_duration_mismatch(self):
        with self.assertRaises(ValueError):
            VisualBeat(
                id="bad_dur",
                start=0.0,
                end=4.0,
                duration=9.0,
            )

    def test_string_fields_normalized_to_lists(self):
        beat = make_visual_beat(
            start=0.0,
            end=2.0,
            subject="fisherman",
            action="carrying object",
            location="shore",
            objects="boots",
            asset_type_preference="image",
        )
        self.assertEqual(beat.subject, ["fisherman"])
        self.assertEqual(beat.action, ["carrying object"])
        self.assertEqual(beat.location, ["shore"])
        self.assertEqual(beat.objects, ["boots"])
        self.assertEqual(beat.asset_type_preference, ["image"])


if __name__ == "__main__":
    unittest.main()

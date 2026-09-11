#!/usr/bin/env python3
"""Asset ranking + dedupe — Phase 5 (mocked candidates only)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visual_beat import make_visual_beat  # noqa: E402
from visual_rank import (  # noqa: E402
    min_relevance_threshold,
    normalize_candidate,
    rank_candidates,
    score_candidate,
    select_assets_for_beats,
    select_for_beat,
    selection_summary,
    semantic_relevance,
)


def _firefighter_beat():
    return make_visual_beat(
        0, 4,
        narration="A firefighter ran into the burning apartment building.",
        subject=["firefighter"],
        action=["running"],
        location=["apartment", "building"],
        objects=[],
        visual_intent="firefighter running into burning apartment building",
        queries=["firefighter running into burning apartment building"],
        fallback_queries=["firefighter burning building", "firefighter fire rescue"],
    )


class RelevanceTests(unittest.TestCase):
    def test_highly_relevant_beats_generic(self):
        beat = _firefighter_beat()
        generic = {"id": "A", "tags": "fire flames smoke orange"}
        standing = {
            "id": "B",
            "tags": "firefighter standing near burning building fire",
        }
        running = {
            "id": "C",
            "tags": "firefighter running into burning building apartment fire",
        }
        ranked = rank_candidates(beat, [generic, standing, running])
        self.assertEqual([r["id"] for r in ranked[:3]], ["C", "B", "A"])
        self.assertGreater(ranked[0]["semantic_score"], ranked[2]["semantic_score"])

    def test_subject_match_affects_ranking(self):
        beat = _firefighter_beat()
        with_subject = {
            "id": "s",
            "tags": "firefighter near burning apartment building",
        }
        without = {
            "id": "n",
            "tags": "burning apartment building smoke flames",
        }
        self.assertGreater(
            semantic_relevance(beat, normalize_candidate(with_subject)),
            semantic_relevance(beat, normalize_candidate(without)),
        )

    def test_action_match_affects_ranking(self):
        beat = _firefighter_beat()
        running = {"id": "r", "tags": "firefighter running burning building"}
        standing = {"id": "s", "tags": "firefighter standing burning building"}
        self.assertGreater(
            score_candidate(beat, running),
            score_candidate(beat, standing),
        )

    def test_location_match_affects_ranking(self):
        beat = make_visual_beat(
            0, 3,
            narration="He walked through the coastal village at dawn.",
            subject=["fisherman"],
            action=["walking"],
            location=["coastal village"],
            time_context="dawn",
            visual_intent="fisherman walking coastal village dawn",
            queries=["fisherman walking coastal village dawn"],
        )
        village = {"id": "v", "tags": "fisherman walking coastal village dawn"}
        city = {"id": "c", "tags": "fisherman walking tokyo city street night"}
        self.assertGreater(score_candidate(beat, village), score_candidate(beat, city))

    def test_object_match_affects_ranking(self):
        beat = make_visual_beat(
            0, 3,
            narration="She held the strange object in her hands.",
            subject=[],
            action=["holding"],
            objects=["strange object"],
            visual_intent="close-up of strange object held in hands",
            queries=["strange object held in hands"],
        )
        obj = {"id": "o", "tags": "hands holding mysterious object close up"}
        empty = {"id": "e", "tags": "person standing in room"}
        self.assertGreater(score_candidate(beat, obj), score_candidate(beat, empty))

    def test_incomplete_metadata_tolerated(self):
        beat = _firefighter_beat()
        sparse = {"id": "sparse", "title": "firefighter running"}
        ranked = rank_candidates(beat, [sparse])
        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0]["score"], 0)

    def test_ranking_is_deterministic(self):
        beat = _firefighter_beat()
        pool = [
            {"id": "A", "tags": "fire flames"},
            {"id": "B", "tags": "firefighter standing burning building"},
            {"id": "C", "tags": "firefighter running into burning building"},
        ]
        a = [r["id"] for r in rank_candidates(beat, pool)]
        b = [r["id"] for r in rank_candidates(beat, list(reversed(pool)))]
        self.assertEqual(a, b)


class DedupePenaltyTests(unittest.TestCase):
    def test_previously_used_receives_penalty(self):
        beat = _firefighter_beat()
        asset = {
            "id": "same",
            "tags": "firefighter running into burning building",
        }
        fresh = score_candidate(beat, asset)
        used = rank_candidates(beat, [asset], used_ids={"same"})[0]["score"]
        self.assertLess(used, fresh)

    def test_adjacent_duplicate_strongly_penalized(self):
        beat = _firefighter_beat()
        asset = {
            "id": "adj",
            "tags": "firefighter running into burning building",
        }
        used_only = rank_candidates(beat, [asset], used_ids={"adj"})[0]["score"]
        adjacent = rank_candidates(
            beat, [asset], used_ids={"adj"}, recent_ids=["adj"],
        )[0]["score"]
        self.assertLess(adjacent, used_only)

    def test_unused_preferred_when_similar(self):
        beat = _firefighter_beat()
        used = {
            "id": "used",
            "tags": "firefighter running into burning apartment building",
        }
        unused = {
            "id": "fresh",
            "tags": "firefighter running into burning apartment building fire",
        }
        chosen = select_for_beat(
            beat, [used, unused], used_ids={"used"}, recent_ids=["used"],
        )
        self.assertEqual(chosen.selected_asset["id"], "fresh")

    def test_only_viable_candidate_can_be_reused(self):
        beat = _firefighter_beat()
        only = {
            "id": "only",
            "tags": "firefighter running into burning building",
        }
        chosen = select_for_beat(
            beat, [only], used_ids={"only"}, recent_ids=["only"],
        )
        self.assertIsNotNone(chosen.selected_asset)
        self.assertEqual(chosen.selected_asset["id"], "only")
        self.assertTrue(chosen.selected_asset.get("reused"))
        self.assertTrue(chosen.selected_asset.get("adjacent_reuse"))

    def test_adjacent_beats_prefer_different_ids(self):
        beats = [
            make_visual_beat(
                0, 4,
                narration="Fisherman walks to village.",
                subject=["fisherman"],
                action=["walking"],
                location=["village"],
                queries=["fisherman walking village dawn"],
                visual_intent="fisherman walking toward village",
            ),
            make_visual_beat(
                4, 8,
                narration="Wet boots on sand.",
                objects=["boots"],
                location=["sand"],
                queries=["wet boots on sand"],
                visual_intent="close-up of wet boots on sand",
            ),
        ]
        shared = [
            {"id": "shared", "tags": "fisherman village dawn boots sand"},
            {"id": "boots", "tags": "wet boots sand close up"},
            {"id": "village", "tags": "fisherman walking coastal village"},
        ]
        out = select_assets_for_beats(beats, candidates_by_beat=[shared, shared])
        self.assertNotEqual(out[0].selected_asset["id"], out[1].selected_asset["id"])
        self.assertEqual(selection_summary(out)["duplicate_ids"], 0)


class LowConfidenceTests(unittest.TestCase):
    def test_low_relevance_identified(self):
        beat = _firefighter_beat()
        weak = {"id": "weak", "tags": "orange glow distant smoke"}
        chosen = select_for_beat(beat, [weak])
        self.assertIsNotNone(chosen.selected_asset)
        self.assertTrue(chosen.selected_asset.get("low_confidence"))
        self.assertLess(chosen.relevance_score, min_relevance_threshold())

    def test_empty_pool_is_miss_not_crash(self):
        beat = _firefighter_beat()
        chosen = select_for_beat(beat, [])
        self.assertIsNone(chosen.selected_asset)
        self.assertEqual(chosen.relevance_score, 0.0)

    def test_does_not_pick_first_api_order(self):
        beat = _firefighter_beat()
        pool = [
            {"id": "A", "tags": "generic fire flames"},
            {"id": "B", "tags": "firefighter standing near burning building"},
            {"id": "C", "tags": "firefighter running into burning building"},
        ]
        chosen = select_for_beat(beat, pool)
        self.assertEqual(chosen.selected_asset["id"], "C")


if __name__ == "__main__":
    unittest.main()

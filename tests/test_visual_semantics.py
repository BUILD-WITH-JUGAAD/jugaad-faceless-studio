#!/usr/bin/env python3
"""Semantic visual query generation — Phase 4 only."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visual_beat import make_visual_beat  # noqa: E402
from visual_semantics import (  # noqa: E402
    analyze_narration,
    enrich_beat,
    enrich_visual_beats,
)


def _beat(text: str, start: float = 0.0, end: float = 4.0):
    return make_visual_beat(start=start, end=end, narration=text)


class SemanticAnalysisTests(unittest.TestCase):
    def test_simple_narration(self):
        beat = enrich_beat(
            _beat("The hacker noticed something strange on the screen.")
        )
        self.assertTrue(beat.visual_intent)
        blob = " ".join(
            [beat.visual_intent]
            + beat.queries
            + beat.fallback_queries
            + beat.subject
            + beat.action
            + beat.objects
        ).lower()
        self.assertIn("hacker", blob)
        self.assertTrue(
            "screen" in blob or "notic" in blob or "warning" in blob or "strange" in blob
        )
        self.assertFalse(any(q.strip().lower() in {"technology", "horror"} for q in beat.queries))

    def test_person_location_object(self):
        text = (
            "The fisherman returned to the village just before dawn, "
            "carrying the strange object he had found in the sea."
        )
        beat = enrich_beat(_beat(text))
        self.assertIn("fisherman", beat.subject)
        self.assertTrue(any("carry" in a for a in beat.action))
        self.assertTrue(
            any("village" in loc for loc in beat.location)
            or "village" in beat.visual_intent.lower()
        )
        self.assertEqual(beat.time_context, "before dawn")
        self.assertTrue(any("object" in o for o in beat.objects))
        primary = (beat.queries[0] if beat.queries else beat.visual_intent).lower()
        self.assertNotEqual(primary.strip(), "ocean")
        self.assertIn("fisherman", primary)
        # Should combine multiple facets, not a single generic noun.
        self.assertGreaterEqual(len(primary.split()), 3)

    def test_action_heavy_narration(self):
        beat = enrich_beat(
            _beat("A firefighter ran into the burning apartment building.")
        )
        self.assertTrue(any("firefighter" in s for s in beat.subject))
        self.assertTrue(any("run" in a or "enter" in a for a in beat.action) or "running" in beat.visual_intent)
        blob = " ".join(beat.queries + beat.fallback_queries + [beat.visual_intent]).lower()
        self.assertTrue("fire" in blob or "burn" in blob or "apartment" in blob or "building" in blob)
        self.assertTrue(beat.fallback_queries)
        # Fallbacks should stay related (still mention fire/rescue/building/person role)
        for fb in beat.fallback_queries:
            fl = fb.lower()
            self.assertTrue(
                any(k in fl for k in ("fire", "burn", "building", "apartment", "firefighter", "running")),
                msg="unrelated fallback: {0}".format(fb),
            )

    def test_historical_time_context(self):
        beat = enrich_beat(
            _beat("In 1995, families across Zanzibar started sleeping outside.")
        )
        self.assertEqual(beat.time_context, "1995")
        blob = " ".join(beat.subject + beat.action + beat.location + [beat.visual_intent]).lower()
        self.assertTrue("famil" in blob or "sleep" in blob or "zanzibar" in blob)

    def test_mood_emotional_narration(self):
        beat = enrich_beat(
            _beat("She waited alone in the silent courtyard, nervous and afraid.")
        )
        self.assertIn(beat.mood, {"uneasy", "fearful", "lonely", "quiet"})
        self.assertTrue(
            any("courtyard" in loc for loc in beat.location)
            or "courtyard" in beat.visual_intent.lower()
        )

    def test_generic_narration_no_fake_specificity(self):
        beat = enrich_beat(_beat("It happened again."))
        # Should not invent people/places that aren't present.
        self.assertEqual(beat.subject, [])
        self.assertEqual(beat.location, [])
        self.assertTrue(beat.visual_intent or beat.queries)
        for q in beat.queries:
            self.assertFalse(q.strip().lower() in {"horror", "scary", "history", "ocean"})


class SemanticPlanTests(unittest.TestCase):
    def test_multiple_sentences_as_beats(self):
        beats = [
            _beat("Parents sat in courtyards with lanterns.", 0, 4),
            _beat("Children slept in piles on mats.", 4, 8),
            _beat("Newspapers printed the name.", 8, 12),
        ]
        out = enrich_visual_beats(beats)
        self.assertEqual(len(out), 3)
        for beat in out:
            self.assertTrue(beat.visual_intent)
            self.assertTrue(beat.queries or beat.fallback_queries)

    def test_adjacent_beats_different_queries(self):
        beats = [
            _beat("The fisherman walked toward the coastal village at dawn.", 0, 4),
            _beat("Wet boots pressed into the cold sand.", 4, 8),
            _beat("A strange object dripped seawater in his hands.", 8, 12),
        ]
        out = enrich_visual_beats(beats)
        primaries = [b.queries[0].lower() for b in out if b.queries]
        self.assertGreaterEqual(len(primaries), 2)
        # Not the same query for every beat.
        self.assertGreater(len(set(primaries)), 1)
        joined = " | ".join(primaries)
        self.assertTrue("fisherman" in joined or "village" in joined)
        self.assertTrue("boot" in joined or "sand" in joined or "object" in joined or "hand" in joined)

    def test_explicit_broll_query_preserved(self):
        beats = [
            _beat("Families slept outside with the lights left on.", 0, 5),
            _beat("Something came through the bedroom wall.", 5, 10),
        ]
        part = {
            "broll_query": "zanzibar night village",
            "broll_queries": [
                "family sleeping outdoors night",
                "dark bedroom wall night",
            ],
        }
        out = enrich_visual_beats(beats, part=part)
        # Exact setting preserved on each beat's query stack.
        for beat in out:
            stack = [q.lower() for q in (beat.queries + beat.fallback_queries)]
            self.assertTrue(
                any("zanzibar night village" == q for q in stack),
                msg="missing broll_query in {0}".format(stack),
            )
        # Per-beat explicit keys preserved as leading queries.
        self.assertTrue(
            any("family sleeping outdoors night" == q.lower() for q in out[0].queries)
        )
        self.assertTrue(
            any("dark bedroom wall night" == q.lower() for q in out[1].queries)
        )

    def test_fallback_query_generation(self):
        beat = enrich_beat(
            _beat("A firefighter ran into the burning apartment building at night.")
        )
        self.assertTrue(1 <= len(beat.fallback_queries) <= 3)
        primary = (beat.queries[0] if beat.queries else "").lower()
        for fb in beat.fallback_queries:
            self.assertNotEqual(fb.lower(), "horror")
            self.assertNotEqual(fb.lower(), "city")
            # Fallbacks should generally be shorter or equal specificity, still overlapping.
            if primary:
                pset = set(primary.split())
                fset = set(fb.lower().split())
                self.assertTrue(
                    pset & fset or any(
                        k in fb.lower()
                        for k in ("fire", "burn", "building", "apartment", "firefighter", "night")
                    )
                )

    def test_analyze_does_not_require_beat_object(self):
        data = analyze_narration(
            "The sailor watched the northern lights over the arctic ice."
        )
        self.assertTrue(data["visual_intent"])
        self.assertTrue(
            data["time_context"] is None
            or data["location"]
            or data["subject"]
            or data["action"]
        )


if __name__ == "__main__":
    unittest.main()

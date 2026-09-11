#!/usr/bin/env python3
"""Visual beat planner — Phase 3 timing only."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visual_planner import (  # noqa: E402
    plan_summary,
    plan_visual_beats,
    target_beat_count,
)


def _words_for_text(text: str, duration: float, pause_after=None) -> list:
    """Synthetic Whisper-like word timings spread across duration."""
    pause_after = set(pause_after or ())
    tokens = [t for t in text.replace("\n", " ").split() if t.strip()]
    if not tokens:
        return []
    # Leave small gaps; insert longer pauses after selected token indices.
    usable = max(duration - 0.15 * len(pause_after), duration * 0.85)
    unit = usable / len(tokens)
    words = []
    t = 0.0
    for i, tok in enumerate(tokens):
        clean = tok.strip(".,!?;:\"'")
        start = t
        end = start + max(0.08, unit * 0.85)
        words.append({"word": clean or tok, "start": start, "end": end})
        t = end + 0.04
        if i in pause_after:
            t += 0.45
    # Stretch so last word ends near duration.
    if words:
        scale = duration / max(words[-1]["end"], 0.01)
        for w in words:
            w["start"] = round(w["start"] * scale, 4)
            w["end"] = round(min(duration, w["end"] * scale), 4)
        words[-1]["end"] = duration
    return words


SHORT = (
    "The door creaked open. Something waited in the dark."
)

MEDIUM_30 = (
    "In 1995, families across Zanzibar started sleeping outside. "
    "Lights on. Doors open. Not because of a storm. "
    "Because something was coming through the walls. "
    "They called it Popobawa. Bat wing. One eye. "
    "Parents sat in courtyards with lanterns. "
    "Children slept in piles on mats. Never alone."
)

MEDIUM_60 = (
    MEDIUM_30
    + " Ali woke with bruises he could not explain. "
    "Marks across his arms, like something had pinned him down. "
    "His neighbors did not laugh. They nodded, because it had happened to them too. "
    "By morning the whole village was talking. Then the next village. Then the news. "
    "Nobody explained how a rumor could leave real bruises."
)

LONG_180 = (
    MEDIUM_60
    + " Hospitals saw the panic. Police took statements. Newspapers printed the name. "
    "On Pemba, people moved mattresses into the street. Some slept in mosques. "
    "Some slept in the road with car headlights on. Radio reports spread faster "
    "than any elder's warning. If you were alone, you were prey. "
    "That was the rule for two weeks. An entire population slept outdoors rather "
    "than risk being alone behind a locked door. "
    "Here is what actually happened. The attacks always followed unrest. "
    "Elections. Land disputes. This was not a story historians dug out of a book. "
    "This was 1995. Then 2007. Then 2013. Real mass panic, recorded by real newspapers, "
    "across real islands. Every time the island shook politically, the same nights "
    "came back. Same bruises. Same smell people swore they could taste. "
    "Same decision: better the mosquitoes outside than whatever came through the wall. "
    "Some called it a demon. Some called it mass hysteria. The people who slept outside "
    "did not care what you called it. They only knew they would not lock that door "
    "and wait in the dark. Search the old headlines. Pemba. Unguja. Popobawa. "
    "The pattern is still there. When the island gets tense, the night gets worse."
)

LONG_SENTENCE = (
    "The fisherman returned to the village just before dawn, carrying the strange "
    "object he had found in the sea, and as the empty coastal streets waited in fog, "
    "he felt the wet boots drag on sand while the mysterious weight shifted in his hands."
)

PAUSED = (
    "The courtyard went silent. "
    "Then footsteps crossed the wet stone. "
    "A lantern swayed once, and went out."
)


class PlannerTargetTests(unittest.TestCase):
    def test_target_ranges(self):
        self.assertTrue(6 <= target_beat_count(30) <= 10)
        self.assertTrue(10 <= target_beat_count(60) <= 18)
        self.assertTrue(24 <= target_beat_count(180) <= 50)


class PlannerBehaviorTests(unittest.TestCase):
    def _assert_timeline(self, beats, duration):
        self.assertGreaterEqual(len(beats), 1)
        self.assertAlmostEqual(beats[0].start, 0.0, places=3)
        self.assertAlmostEqual(beats[-1].end, float(duration), places=2)
        for i, beat in enumerate(beats):
            self.assertGreater(beat.duration, 0.0)
            self.assertGreater(beat.end, beat.start)
            self.assertAlmostEqual(beat.duration, beat.end - beat.start, places=3)
            if i:
                self.assertAlmostEqual(beat.start, beats[i - 1].end, places=3)
            # Chronological
            if i:
                self.assertGreaterEqual(beat.start, beats[i - 1].start)

    def test_30_second_narration(self):
        duration = 30.0
        words = _words_for_text(MEDIUM_30, duration)
        beats = plan_visual_beats(MEDIUM_30, duration, words)
        self._assert_timeline(beats, duration)
        count = len(beats)
        self.assertTrue(6 <= count <= 10, "30s beat count={0}".format(count))
        summary = plan_summary(beats)
        self.assertLessEqual(summary["max_hold"], 10.0)

    def test_60_second_narration(self):
        duration = 60.0
        words = _words_for_text(MEDIUM_60, duration)
        beats = plan_visual_beats(MEDIUM_60, duration, words)
        self._assert_timeline(beats, duration)
        count = len(beats)
        self.assertTrue(10 <= count <= 18, "60s beat count={0}".format(count))

    def test_180_second_narration(self):
        duration = 180.0
        words = _words_for_text(LONG_180, duration)
        beats = plan_visual_beats(LONG_180, duration, words)
        self._assert_timeline(beats, duration)
        count = len(beats)
        self.assertTrue(24 <= count <= 50, "180s beat count={0}".format(count))
        # Not PART-sized sparsity
        self.assertGreaterEqual(count, 24)

    def test_short_narration(self):
        duration = 8.0
        words = _words_for_text(SHORT, duration)
        beats = plan_visual_beats(SHORT, duration, words)
        self._assert_timeline(beats, duration)
        self.assertTrue(1 <= len(beats) <= 4)

    def test_long_sentence(self):
        duration = 20.0
        words = _words_for_text(LONG_SENTENCE, duration)
        beats = plan_visual_beats(LONG_SENTENCE, duration, words)
        self._assert_timeline(beats, duration)
        # One PART / one sentence must not become a single long hold.
        self.assertGreaterEqual(len(beats), 3)
        self.assertLessEqual(plan_summary(beats)["max_hold"], 10.0)

    def test_multiple_sentences_with_pauses(self):
        duration = 18.0
        # Pause after each sentence end token index approx.
        tokens = PAUSED.replace("\n", " ").split()
        pause_at = []
        for i, tok in enumerate(tokens):
            if tok.endswith("."):
                pause_at.append(i)
        words = _words_for_text(PAUSED, duration, pause_after=pause_at)
        beats = plan_visual_beats(PAUSED, duration, words)
        self._assert_timeline(beats, duration)
        self.assertGreaterEqual(len(beats), 3)
        # Narration slices should reflect distinct sentences when possible.
        joined = " ".join(b.narration for b in beats)
        self.assertIn("courtyard", joined.lower())
        self.assertIn("footsteps", joined.lower())

    def test_missing_whisper_fallback(self):
        duration = 60.0
        beats = plan_visual_beats(MEDIUM_60, duration, words=None)
        self._assert_timeline(beats, duration)
        count = len(beats)
        self.assertTrue(10 <= count <= 18, "fallback 60s count={0}".format(count))

    def test_beats_are_chronological(self):
        duration = 45.0
        beats = plan_visual_beats(MEDIUM_60, duration, _words_for_text(MEDIUM_60, duration))
        starts = [b.start for b in beats]
        self.assertEqual(starts, sorted(starts))

    def test_no_negative_or_zero_durations(self):
        for duration in (5.0, 30.0, 60.0, 180.0):
            text = LONG_180 if duration >= 120 else MEDIUM_60 if duration >= 40 else SHORT
            beats = plan_visual_beats(text, duration, _words_for_text(text, duration))
            for beat in beats:
                self.assertGreater(beat.duration, 0.0)
                self.assertGreaterEqual(beat.start, 0.0)
                self.assertGreater(beat.end, beat.start)

    def test_beats_cover_narration(self):
        duration = 90.0
        beats = plan_visual_beats(MEDIUM_60, duration, _words_for_text(MEDIUM_60, duration))
        covered = sum(b.duration for b in beats)
        self.assertAlmostEqual(covered, duration, places=2)
        # Most beats should carry narration text from the story.
        with_text = sum(1 for b in beats if b.narration.strip())
        self.assertGreaterEqual(with_text, max(1, len(beats) - 1))


if __name__ == "__main__":
    unittest.main()

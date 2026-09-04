#!/usr/bin/env python3
"""Story gender and ghost-reference scoping."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_engine import beat_gender, beat_is_creature, story_gender  # noqa: E402


class CastTests(unittest.TestCase):
    def test_male_story(self):
        text = "The next morning the boy was never seen again. He left no trace. His laughter was gone."
        self.assertEqual(story_gender(text), "male")

    def test_female_story(self):
        text = "She walked home alone. The girl heard the scrape behind her."
        self.assertEqual(story_gender(text), "female")

    def test_beat_overrides_story(self):
        self.assertEqual(beat_gender("Japanese schoolgirl walking alone at night", "The boy ran."), "female")
        self.assertEqual(beat_gender("Japanese schoolboy walking home night", "She waited."), "male")

    def test_creature_beats(self):
        self.assertTrue(beat_is_creature("Teke Teke crawling ghost"))
        self.assertFalse(beat_is_creature("Tokyo Japan empty street night rain"))


if __name__ == "__main__":
    unittest.main()

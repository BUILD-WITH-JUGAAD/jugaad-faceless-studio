#!/usr/bin/env python3
"""Spoken-text cleanup — quotes must not reach Coqui as asterisks."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tts_engine import for_speech, split_narration  # noqa: E402


class SpeechCleanTests(unittest.TestCase):
    def test_straight_quotes(self):
        self.assertEqual(for_speech('They called him "Amit".'), "They called him Amit.")

    def test_smart_quotes(self):
        self.assertEqual(for_speech("They called him \u201cAmit\u201d."), "They called him Amit.")

    def test_markdown_stars(self):
        self.assertEqual(for_speech("They called him *Amit*."), "They called him Amit.")
        self.assertEqual(for_speech("They called him **Amit**."), "They called him Amit.")

    def test_keeps_apostrophes(self):
        self.assertEqual(for_speech("Amit's house wasn't empty."), "Amit's house wasn't empty.")

    def test_split_uses_cleaned_text(self):
        chunks = split_narration('They called him "Amit".')
        self.assertEqual(chunks, ["They called him Amit."])
        self.assertNotIn('"', "".join(chunks))
        self.assertNotIn("*", "".join(chunks))


if __name__ == "__main__":
    unittest.main()

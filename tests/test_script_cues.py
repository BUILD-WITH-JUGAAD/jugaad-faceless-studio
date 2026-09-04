#!/usr/bin/env python3
"""Stage directions must not be spoken or captioned."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script_text import parse_script, spoken_text  # noqa: E402
from tts_engine import _speech_jobs, for_speech  # noqa: E402


SAMPLE = """
(A brief, unsettling silence, then a low, drawn-out “teke…” followed by a child’s giggle)

(Tempo increases slightly, music becomes more suspenseful)

(Final, drawn-out “teke…” fades to silence)

Narrator: The next morning, the boy was never seen again. No trace of him, no explanation. Just an unsettling silence where his laughter used to be.
"""


class ScriptCueTests(unittest.TestCase):
    def test_spoken_drops_cues_and_narrator(self):
        spoken = spoken_text(SAMPLE)
        self.assertIn("The next morning, the boy was never seen again", spoken)
        self.assertNotIn("Narrator", spoken)
        self.assertNotIn("Tempo increases", spoken)
        self.assertNotIn("child’s giggle", spoken)
        self.assertNotIn("child's giggle", spoken)

    def test_for_speech_matches_captions(self):
        spoken = for_speech(SAMPLE)
        self.assertTrue(spoken.startswith("The next morning"))
        self.assertNotIn("asterisk", spoken.lower())
        self.assertNotIn("teke", spoken.lower())

    def test_cues_classified(self):
        pack = parse_script(SAMPLE)
        kinds = [c["kind"] for c in pack["cues"]]
        self.assertIn("silence", kinds)
        self.assertIn("sfx", kinds)
        self.assertIn("music", kinds)
        queries = {c["query"] for c in pack["cues"] if c["kind"] == "sfx"}
        self.assertIn("child giggle", queries)
        self.assertIn("metal scrape", queries)

    def test_speech_jobs_pause_then_speak(self):
        jobs = _speech_jobs(SAMPLE)
        kinds = [k for k, _ in jobs]
        self.assertEqual(kinds[0], "silence")
        self.assertIn("speech", kinds)
        spoken = " ".join(payload for kind, payload in jobs if kind == "speech")
        self.assertIn("next morning", spoken)
        self.assertNotIn("Tempo", spoken)

    def test_keeps_normal_asides(self):
        text = "In 1995 (the first wave) families slept outside."
        self.assertIn("the first wave", spoken_text(text))


if __name__ == "__main__":
    unittest.main()

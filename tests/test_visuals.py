#!/usr/bin/env python3
"""Visual search key parsing — ChatGPT paste + VISUAL: lines."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from story_engine import (  # noqa: E402
    _parse_pack,
    parse_visuals,
    visual_to_image_beat,
)


CHATGPT = """
🎬 Visual Search Keys

1. Opening
Tokyo Japan empty street night rain
Japanese street night cinematic horror
Tokyo alley rain night

2. Girl walking alone
Japanese schoolgirl walking alone at night
anime girl school uniform night street
Japanese student walking home night

3. Hearing the sound
dark empty street horror POV
person looking behind at night horror
dark alley suspense cinematic

4. Teke-Teke appearance
Teke Teke Japanese urban legend
Japanese ghost Teke Teke horror
Teke Teke crawling ghost
Japanese urban legend ghost silhouette

5. Final chase
ghost chasing person dark street
Japanese horror chase night
horror POV running dark street

6. Ending
empty Japanese railway tracks night
Japanese train station abandoned night
dark railway tunnel horror
"""


class VisualParseTests(unittest.TestCase):
    def test_chatgpt_groups_six_scenes(self):
        keys = parse_visuals(CHATGPT)
        self.assertEqual(len(keys), 6)
        self.assertTrue(keys[0].startswith("Tokyo Japan empty street night rain"))
        self.assertIn("Tokyo alley rain night", keys[0])
        self.assertIn(" | ", keys[0])
        self.assertIn("Teke Teke crawling ghost", keys[3])
        self.assertTrue(keys[5].endswith("dark railway tunnel horror"))

    def test_bare_list_stays_one_per_line(self):
        keys = parse_visuals(
            "Tokyo Japan empty street night rain\n"
            "Japanese schoolgirl walking alone at night\n"
        )
        self.assertEqual(
            keys,
            [
                "Tokyo Japan empty street night rain",
                "Japanese schoolgirl walking alone at night",
            ],
        )

    def test_numbered_long_keys_stay_keys(self):
        keys = parse_visuals(
            "1. Tokyo Japan empty street night rain\n"
            "2. Japanese schoolgirl walking alone at night\n"
        )
        self.assertEqual(
            keys,
            [
                "Tokyo Japan empty street night rain",
                "Japanese schoolgirl walking alone at night",
            ],
        )

    def test_visual_prefix(self):
        keys = parse_visuals("VISUAL: Tokyo alley rain night\nKEY: Teke Teke crawling ghost")
        self.assertEqual(keys, ["Tokyo alley rain night", "Teke Teke crawling ghost"])

    def test_skips_story_sentences(self):
        keys = parse_visuals(
            "In 1995 families slept outside. They called it Popobawa."
        )
        self.assertEqual(keys, [])

    def test_image_beat_joins_alternates(self):
        beat = visual_to_image_beat(
            "Tokyo Japan empty street night rain | Japanese street night cinematic horror"
        )
        self.assertEqual(
            beat,
            "Tokyo Japan empty street night rain, Japanese street night cinematic horror",
        )

    def test_parse_pack_strips_visuals_from_speech(self):
        pack = _parse_pack(
            "She walked the empty Tokyo street at night and heard the scrape.\n"
            "TITLE: Teke Teke\n"
            "SETTING: Tokyo alley rain night\n"
            "VISUAL: Tokyo Japan empty street night rain\n"
            "VISUAL: Teke Teke crawling ghost\n"
        )
        self.assertNotIn("VISUAL", pack["text"])
        self.assertEqual(pack["title"], "Teke Teke")
        self.assertEqual(pack["setting"], "Tokyo alley rain night")
        self.assertEqual(
            pack["visuals"],
            [
                "Tokyo Japan empty street night rain",
                "Teke Teke crawling ghost",
            ],
        )


if __name__ == "__main__":
    unittest.main()

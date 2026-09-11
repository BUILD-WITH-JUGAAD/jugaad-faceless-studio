#!/usr/bin/env python3
"""Visual search key parsing — ChatGPT paste + VISUAL: lines."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from story_engine import (  # noqa: E402
    _finish_story,
    _parse_pack,
    clean_story,
    derive_visuals,
    filter_stock_visuals,
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

    def test_rejects_chat_preamble_as_visual_key(self):
        preamble = (
            "Here's a short, unnerving story designed for a YouTube Short, "
            "aiming for a 'dread' effect:"
        )
        self.assertEqual(parse_visuals(preamble), [])
        self.assertEqual(filter_stock_visuals([preamble]), [])

    def test_finish_story_strips_preamble_and_derives_keys(self):
        raw = (
            "Here's a short, unnerving story designed for a YouTube Short, "
            "aiming for a 'dread' effect:\n"
            "The rain started subtly. Just a whisper against the windows. "
            "Then it intensified against the cellar door.\n"
            "TITLE: The Unopened Box\n"
            "SETTING: Dark cellar rainstorm night\n"
        )
        pack = _finish_story(raw)
        self.assertNotIn("Here's a short", pack["text"])
        self.assertTrue(pack["text"].startswith("The rain started"))
        self.assertEqual(pack["title"], "The Unopened Box")
        self.assertTrue(len(pack["visuals"]) >= 1)
        for key in pack["visuals"]:
            self.assertNotIn("Here's a short", key)
            self.assertFalse(key.rstrip().endswith(":"))

    def test_clean_story_drops_designed_for_preamble(self):
        text = clean_story(
            "Here's a short, unnerving story designed for a YouTube Short:\n"
            "The box sat unopened in the dark cellar."
        )
        self.assertNotIn("Here's a short", text)
        self.assertIn("box sat unopened", text)

    def test_derive_visuals_ignores_preamble_only(self):
        keys = derive_visuals(
            "Here's a short, unnerving story designed for a YouTube Short, "
            "aiming for a dread effect:\n"
            "Rain drummed on the cellar windows. An unopened box waited in the dark.",
            "Dark cellar rainstorm night",
        )
        self.assertTrue(keys)
        joined = " ".join(keys).lower()
        self.assertNotIn("here's a short", joined)
        self.assertTrue("cellar" in joined or "rain" in joined or "box" in joined)


if __name__ == "__main__":
    unittest.main()

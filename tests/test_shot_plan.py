#!/usr/bin/env python3
"""Shot-plan JSON story packs + Pixazo per-shot prompts."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generative_adapter import build_planned_pixazo_storyboard  # noqa: E402
from story_engine import (  # noqa: E402
    _finish_story,
    normalize_shots,
    target_shot_count,
)


# Compact local-model style (action + camera only — app builds Pixazo prompts).
SAMPLE_JSON = """
{
  "title": "Hospital Shadow",
  "story": "He walked the empty hospital halls alone. Then something followed him through the dark.",
  "setting": "abandoned hospital night",
  "setting_search_keys": ["abandoned hospital corridor night", "empty hospital hallway dark"],
  "visual_search_keys": [
    "man walking hospital corridor night",
    "empty hospital hallway horror",
    "ghost far end hospital corridor",
    "man running hospital hallway"
  ],
  "characters": [
    {"name": "Man", "look": "man in dark jacket"},
    {"name": "Ghost", "look": "pale translucent figure"}
  ],
  "shots": [
    {
      "shot_number": 1,
      "duration": 5,
      "action": "Man walks through hospital corridor.",
      "camera": "Camera follows behind him."
    },
    {
      "shot_number": 2,
      "duration": 5,
      "action": "Man hears a sound and stops. He slowly turns around.",
      "camera": "Camera pushes toward his face."
    },
    {
      "shot_number": 3,
      "duration": 5,
      "action": "Empty corridor. A ghost appears at the far end.",
      "camera": "Camera slowly moves toward the ghost."
    },
    {
      "shot_number": 4,
      "duration": 5,
      "action": "Man sees the ghost. He takes several steps backward.",
      "camera": "Camera tracks backward with him."
    },
    {
      "shot_number": 5,
      "duration": 5,
      "action": "Ghost rapidly moves closer. Man turns and runs.",
      "camera": "Camera follows the chase."
    }
  ]
}
"""


class ShotPlanParseTests(unittest.TestCase):
    def test_finish_story_json_pack(self):
        pack = _finish_story(SAMPLE_JSON)
        self.assertEqual(pack["title"], "Hospital Shadow")
        self.assertIn("hospital", pack["text"].lower())
        self.assertTrue(pack["setting"])
        self.assertGreaterEqual(len(pack["visuals"]), 2)
        self.assertEqual(len(pack["shots"]), 5)
        first = pack["shots"][0]
        self.assertEqual(first["action"], "Man walks through hospital corridor.")
        self.assertIn("follows behind", first["camera"].lower())
        # App composes compact Pixazo prompt from short fields.
        prompt = first["visual_prompt"].lower()
        self.assertIn("hospital", prompt)
        self.assertIn("walks", prompt)
        self.assertIn("follows", prompt)
        self.assertLess(len(first["visual_prompt"].split()), 60)
        self.assertEqual(pack["characters"][0]["name"], "Man")
        self.assertTrue(pack["setting_search_keys"])

    def test_legacy_text_still_works(self):
        raw = (
            "The rain started hard against the cellar door and would not stop.\n"
            "TITLE: The Unopened Box\n"
            "SETTING: Dark cellar rainstorm night\n"
            "VISUAL: dark cellar rain night\n"
            "VISUAL: unopened wooden box cellar\n"
        )
        pack = _finish_story(raw)
        self.assertEqual(pack["title"], "The Unopened Box")
        self.assertEqual(pack["shots"], [])
        self.assertTrue(pack["visuals"])

    def test_normalize_composes_missing_prompt(self):
        shots = normalize_shots(
            [{
                "duration": 5,
                "action": "Woman steps backward into the hall.",
                "camera": "Camera pulls back with her.",
            }],
            setting="old house night",
            characters=[{"look": "young woman in red coat"}],
        )
        self.assertEqual(len(shots), 1)
        prompt = shots[0]["visual_prompt"].lower()
        self.assertIn("steps backward", prompt)
        self.assertTrue("pulls" in prompt or "camera" in prompt)
        self.assertIn("old house", prompt)

    def test_target_shot_count_bands(self):
        self.assertGreaterEqual(target_shot_count(30), 5)
        self.assertLessEqual(target_shot_count(30), 8)
        self.assertGreaterEqual(target_shot_count(60), 10)
        self.assertLessEqual(target_shot_count(60), 14)


class ShotPixazoTests(unittest.TestCase):
    def test_planned_pixazo_uses_shot_prompts(self):
        pack = _finish_story(SAMPLE_JSON)
        part = {
            "text": pack["text"],
            "broll_query": pack["setting"],
            "shots": pack["shots"],
            "characters": pack["characters"],
            "broll_queries": ["should not be used as primary"],
        }
        prompts = []

        def fake_fetch(prompt, dest, **kw):
            prompts.append(prompt)
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * 9000)
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pixazo_engine._require_key", return_value="k"):
                with mock.patch("pixazo_engine._load_job", return_value=None):
                    clips = build_planned_pixazo_storyboard(
                        part,
                        Path(tmp),
                        duration=25.0,
                        text=pack["text"],
                        fetch_fn=fake_fetch,
                        clip_budget=5,
                    )
        self.assertEqual(len(clips), 5)
        self.assertEqual(len(prompts), 5)
        joined = " ".join(prompts).lower()
        self.assertIn("hospital", joined)
        self.assertIn("ghost", joined)
        self.assertNotIn("should not be used", joined)
        # Timeline is contiguous 5+5+5+5+5 covering narration.
        self.assertAlmostEqual(clips[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(clips[-1]["end"], 25.0, places=2)
        for i in range(len(clips) - 1):
            self.assertAlmostEqual(clips[i]["end"], clips[i + 1]["start"], places=2)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Phase 7 — illustrated / generative VisualBeat adapters (mocked)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from visual_beat import make_visual_beat  # noqa: E402
from visual_semantics import enrich_beat  # noqa: E402
from visual_prompt import allocate_budgeted_beats, scene_text_from_beat  # noqa: E402
from illustrated_adapter import (  # noqa: E402
    beat_generation_prompt,
    build_planned_illustrated_storyboard,
    illustrated_panel_budget,
)
from generative_adapter import (  # noqa: E402
    build_planned_ai_video_storyboard,
    build_planned_pixazo_storyboard,
)
from stock_adapter import visual_planner_enabled  # noqa: E402


SHORT = (
    "The fisherman returned to the village before dawn. "
    "He carried a strange wet object from the sea. "
    "Wet boots left prints on the sand."
)


def _enriched(text, **kwargs):
    return enrich_beat(make_visual_beat(0, 4, narration=text, **kwargs))


class ScenePromptTests(unittest.TestCase):
    def test_ai_image_semantic_prompt_not_generic(self):
        beat = _enriched(
            "A frightened fisherman holding a strange wet object outside a coastal village before dawn."
        )
        scene = scene_text_from_beat(beat)
        self.assertNotEqual(scene.lower().strip(), "dark")
        self.assertNotIn("dark horror style", scene.lower())
        self.assertTrue(
            any(tok in scene.lower() for tok in ("fisherman", "object", "village", "dawn"))
        )

    def test_explicit_image_prompt_preserved(self):
        beat = enrich_beat(
            make_visual_beat(0, 3, narration="He walked home."),
            setting="coastal fog",
            explicit_queries=["ancient roman soldiers marching through rain"],
        )
        scene = scene_text_from_beat(beat, setting="coastal fog")
        self.assertIn("ancient roman soldiers", scene.lower())

    def test_comic_cartoon_anime_intent(self):
        beat = _enriched("The astronaut stared at the flashing warning lights.")
        for style in ("comic", "cartoon", "anime"):
            prompt = beat_generation_prompt(beat, style=style, story=SHORT)
            self.assertTrue(len(prompt) > 20)
            # Style medium is applied by _panel_prompt; scene must be specific.
            self.assertNotEqual(prompt.lower()[:20], "dark horror")

    def test_budget_caps_dense_planner(self):
        beats = [
            make_visual_beat(i * 2, i * 2 + 2, narration="Beat {0}.".format(i))
            for i in range(20)
        ]
        capped = allocate_budgeted_beats(beats, 5, duration=40.0)
        self.assertEqual(len(capped), 5)
        self.assertEqual(capped[0].start, 0.0)
        self.assertAlmostEqual(capped[-1].end, 40.0, places=1)


class IllustratedAdapterTests(unittest.TestCase):
    def test_comic_build_uses_semantic_prompts_and_seed(self):
        part = {
            "text": SHORT,
            "broll_query": "coastal village dawn",
            "image_prompts": [
                "fisherman carrying wet object dawn village",
            ],
        }
        prompts = []
        seeds = []

        def fake_fetch(prompt, dest, seed=None, style="comic", use_ref=False, beat="", **kw):
            prompts.append(prompt)
            seeds.append(seed)
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\xff\xd8" + b"x" * 3000)
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "COMIC_PANELS", 4):
                with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                    panels = build_planned_illustrated_storyboard(
                        part,
                        Path(tmp),
                        duration=24.0,
                        style="comic",
                        character_lock="scarred bat-winged shadow",
                        character_seed=42,
                        text=SHORT,
                        fetch_fn=fake_fetch,
                        panel_budget=4,
                    )
        self.assertEqual(len(panels), 4)
        self.assertTrue(all(p.get("prompt") for p in panels))
        self.assertTrue(any("fisherman" in (p.get("beat") or "").lower() or
                            "fisherman" in (p.get("prompt") or "").lower()
                            for p in panels))
        self.assertEqual(seeds[0], 42)
        self.assertEqual(seeds[1], 42 + 17)
        # Character lock must appear in at least one panel prompt.
        self.assertTrue(any("scarred bat-winged shadow" in (p.get("prompt") or "") for p in panels))

    def test_cartoon_and_anime_styles(self):
        part = {"text": SHORT, "broll_query": "village night"}

        def fake_fetch(prompt, dest, seed=None, style="comic", **kw):
            dest = Path(dest)
            dest.write_bytes(b"\xff\xd8" + b"y" * 3000)
            return dest

        for style in ("cartoon", "anime"):
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                    panels = build_planned_illustrated_storyboard(
                        part,
                        Path(tmp),
                        duration=12.0,
                        style=style,
                        character_seed=1,
                        text=SHORT,
                        fetch_fn=fake_fetch,
                        panel_budget=3,
                    )
                self.assertEqual(len(panels), 3)
                self.assertTrue(all(Path(p["path"]).exists() for p in panels))

    def test_reference_image_flag_passed(self):
        part = {"text": "A ghost crawled across the ceiling.", "broll_query": "dark attic"}
        seen = []

        def fake_fetch(prompt, dest, seed=None, style="comic", use_ref=False, beat="", **kw):
            seen.append({"use_ref": use_ref, "beat": beat, "prompt": prompt})
            dest = Path(dest)
            dest.write_bytes(b"\xff\xd8" + b"z" * 3000)
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("image_engine.use_ref_for_beat", return_value=True):
                with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                    build_planned_illustrated_storyboard(
                        part,
                        Path(tmp),
                        duration=8.0,
                        style="comic",
                        character_seed=7,
                        text=part["text"],
                        fetch_fn=fake_fetch,
                        panel_budget=2,
                    )
        self.assertTrue(seen)
        self.assertTrue(any(row["use_ref"] for row in seen))

    def test_panel_failure_reuses_previous(self):
        part = {"text": SHORT, "broll_query": "coast"}
        calls = {"n": 0}

        def fake_fetch(prompt, dest, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                dest = Path(dest)
                dest.write_bytes(b"\xff\xd8" + b"a" * 3000)
                return dest
            raise RuntimeError("provider down")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                panels = build_planned_illustrated_storyboard(
                    part,
                    Path(tmp),
                    duration=12.0,
                    text=SHORT,
                    fetch_fn=fake_fetch,
                    panel_budget=3,
                )
        self.assertEqual(len(panels), 3)
        self.assertEqual(panels[0]["path"], panels[1]["path"])

    def test_reuse_images_cache(self):
        part = {"text": SHORT, "broll_query": "coast"}

        def boom(*a, **k):
            raise AssertionError("fetch should not be called when REUSE_IMAGES=1")

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for i in range(1, 3):
                p = tmp / "panel_{0}.jpg".format(i)
                p.write_bytes(b"\xff\xd8" + b"c" * 3000)
            with mock.patch.dict(os.environ, {"REUSE_IMAGES": "1"}):
                with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                    panels = build_planned_illustrated_storyboard(
                        part,
                        tmp,
                        duration=10.0,
                        text=SHORT,
                        fetch_fn=boom,
                        panel_budget=2,
                    )
        self.assertEqual(len(panels), 2)


class GenerativeAdapterTests(unittest.TestCase):
    def test_pixazo_beat_intent(self):
        part = {"text": SHORT, "broll_query": "coastal village dawn"}
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
                        duration=30.0,
                        text=SHORT,
                        fetch_fn=fake_fetch,
                        clip_budget=3,
                    )
        self.assertEqual(len(clips), 3)
        self.assertTrue(prompts)
        self.assertTrue(
            any(any(t in p.lower() for t in ("fisherman", "village", "boots", "sea", "object"))
                for p in prompts)
        )

    def test_pixazo_failure_fallback(self):
        part = {"text": SHORT, "broll_query": "coast"}
        n = {"i": 0}

        def fake_fetch(prompt, dest, **kw):
            n["i"] += 1
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if n["i"] == 1:
                dest.write_bytes(b"x" * 9000)
                return dest
            raise RuntimeError("queue timeout")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pixazo_engine._require_key", return_value="k"):
                with mock.patch("pixazo_engine._load_job", return_value=None):
                    clips = build_planned_pixazo_storyboard(
                        part,
                        Path(tmp),
                        duration=20.0,
                        text=SHORT,
                        fetch_fn=fake_fetch,
                        clip_budget=3,
                    )
        self.assertEqual(len(clips), 3)
        self.assertEqual(clips[0]["path"], clips[1]["path"])

    def test_ai_video_preserves_character_seed_path(self):
        part = {"text": SHORT, "broll_query": "coast"}
        still_seeds = []

        def fake_still(prompt, dest, seed=None, **kw):
            still_seeds.append(seed)
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\xff\xd8" + b"v" * 3000)
            return dest

        def fake_video(prompt, dest, image_url=None, **kw):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * 9000)
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with mock.patch("video_engine._require_video_key", return_value="k"):
                with mock.patch.object(config, "REUSE_BOARD_DIR", ""):
                    clips = build_planned_ai_video_storyboard(
                        part,
                        tmp / "stills",
                        tmp / "clips",
                        duration=18.0,
                        style="comic",
                        character_lock="scarred shadow",
                        character_seed=99,
                        text=SHORT,
                        fetch_still_fn=fake_still,
                        fetch_video_fn=fake_video,
                        panel_budget=3,
                    )
        self.assertEqual(len(clips), 3)
        self.assertEqual(still_seeds[0], 99)
        self.assertEqual(still_seeds[1], 99 + 17)

    def test_feature_flag_gate(self):
        with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", True):
            self.assertTrue(visual_planner_enabled())
        with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", False):
            self.assertFalse(visual_planner_enabled())

    def test_comic_budget_uses_comic_panels(self):
        with mock.patch.object(config, "COMIC_PANELS", 8):
            self.assertEqual(illustrated_panel_budget(), 8)


if __name__ == "__main__":
    unittest.main()

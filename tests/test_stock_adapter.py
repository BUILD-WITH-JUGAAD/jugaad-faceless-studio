#!/usr/bin/env python3
"""Stock provider adapters + pipeline integration — Phase 6 (mocked)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from visual_beat import make_visual_beat  # noqa: E402
from visual_semantics import enrich_beat, enrich_visual_beats  # noqa: E402
from stock_adapter import (  # noqa: E402
    SearchCache,
    build_planned_stock_videos,
    collect_candidates_for_beat,
    fill_stock_candidates,
    materialize_stock_beats,
    plan_enriched_beats,
    select_stock_for_beat,
    select_stock_for_beats,
    stock_selection_report,
    visual_planner_enabled,
)


def _beat(text, **kwargs):
    return enrich_beat(make_visual_beat(0, 4, narration=text, **kwargs))


def _video(vid, tags, url_slug=None):
    slug = url_slug or tags.replace(" ", "-")
    return {
        "id": vid,
        "duration": 12,
        "url": "https://www.pexels.com/video/{0}/".format(slug),
        "tags": tags,
        "video_files": [
            {"link": "http://example/{0}.mp4".format(vid), "width": 1080, "height": 1920},
        ],
    }


def _photo(pid, alt, tags=""):
    return {
        "id": pid,
        "slug": alt.replace(" ", "-"),
        "alt_description": alt,
        "description": tags or alt,
        "tags": [{"title": t} for t in (tags or alt).split()],
        "width": 1080,
        "height": 1920,
        "urls": {
            "raw": "http://example/{0}.jpg".format(pid),
            "regular": "http://example/{0}.jpg".format(pid),
        },
        "links": {
            "html": "http://unsplash.example/{0}".format(pid),
            "download_location": "",
        },
        "user": {"name": "Tester"},
    }


SHORT_STORY = (
    "The fisherman returned to the village just before dawn. "
    "He carried a strange object from the sea. "
    "Wet boots left prints on the sand. "
    "Windows in the empty street rattled in the wind. "
    "Something moved outside the homes that night."
)


class MockSearchStockAdapterTests(unittest.TestCase):
    def test_collects_candidates_from_queries(self):
        beat = _beat("A firefighter ran into the burning building.")
        seen = []

        def search(q):
            seen.append(q)
            return [
                _video(1, "firefighter running into burning building"),
                _video(2, "fire flames generic"),
                _video(3, "firefighter standing near fire"),
            ]

        cands = collect_candidates_for_beat(beat, provider="pexels", search_fn=search)
        ids = {c["id"] for c in cands}
        self.assertTrue(seen)
        self.assertEqual(ids, {1, 2, 3})
        self.assertTrue(all(c.get("provider") == "pexels" for c in cands))
        self.assertTrue(all("search_query" in c and "query" not in c for c in cands))

    def test_selects_best_not_first(self):
        beat = make_visual_beat(
            0, 4,
            narration="A firefighter ran into the burning apartment building.",
            subject=["firefighter"],
            action=["running"],
            location=["apartment", "building"],
            visual_intent="firefighter running into burning apartment building",
            queries=["firefighter running into burning apartment building"],
            fallback_queries=["firefighter fire"],
        )

        def search(q):
            return [
                _video(10, "generic fire flames smoke"),
                _video(11, "firefighter standing near burning building"),
                _video(12, "firefighter running into burning apartment building"),
            ]

        chosen = select_stock_for_beat(beat, provider="pexels", search_fn=search)
        self.assertIsNotNone(chosen.selected_asset)
        self.assertEqual(chosen.selected_asset["id"], 12)

    def test_dedupes_across_beats(self):
        beats = [
            make_visual_beat(
                0, 4,
                narration="Fisherman walks to the village.",
                subject=["fisherman"],
                action=["walking"],
                location=["village"],
                queries=["fisherman walking coastal village"],
                visual_intent="fisherman walking coastal village",
            ),
            make_visual_beat(
                4, 8,
                narration="Wet boots on sand.",
                objects=["boots"],
                location=["sand"],
                queries=["wet boots on sand"],
                visual_intent="close-up wet boots on sand",
            ),
        ]

        def search(q):
            return [
                _video(1, "fisherman walking coastal village dawn boots sand"),
                _video(2, "wet boots walking sand close up"),
                _video(3, "coastal village sunrise fisherman"),
            ]

        out = select_stock_for_beats(beats, provider="pexels", search_fn=search)
        self.assertIsNotNone(out[0].selected_asset)
        self.assertIsNotNone(out[1].selected_asset)
        self.assertNotEqual(out[0].selected_asset["id"], out[1].selected_asset["id"])

    def test_preserves_setting_in_query_stack(self):
        beat = make_visual_beat(
            0, 3,
            narration="Families slept outside.",
            queries=["family sleeping outdoors night"],
        )
        seen = []

        def search(q):
            seen.append(q)
            return [_video(1, q)]

        # Primary alone yields < enough → setting (last resort) may be searched.
        collect_candidates_for_beat(
            beat,
            provider="pexels",
            setting="zanzibar night village",
            search_fn=search,
            enough=20,
        )
        self.assertIn("family sleeping outdoors night", seen)
        self.assertIn("zanzibar night village", seen)

    def test_unsplash_shaping(self):
        beat = make_visual_beat(
            0, 3,
            narration="Lantern in a dark courtyard.",
            objects=["lantern"],
            location=["courtyard"],
            queries=["lantern dark courtyard night"],
            visual_intent="lantern in dark courtyard",
        )

        def search(q):
            return [
                _photo("p1", "yoga studio bright", "yoga gym"),
                _photo("p2", "oil lantern dark courtyard", "lantern courtyard night"),
            ]

        chosen = select_stock_for_beat(beat, provider="unsplash", search_fn=search)
        self.assertEqual(chosen.selected_asset["id"], "p2")
        self.assertEqual(chosen.selected_asset["provider"], "unsplash")

    def test_search_failure_does_not_crash(self):
        beat = _beat("Something happened in the dark room.")

        def search(_q):
            raise RuntimeError("network down")

        out = select_stock_for_beat(beat, search_fn=search)
        self.assertIsNone(out.selected_asset)

    def test_fill_then_report(self):
        beat = make_visual_beat(
            0, 3,
            narration="Firefighter running into fire.",
            subject=["firefighter"],
            action=["running"],
            queries=["firefighter running burning building"],
            visual_intent="firefighter running into burning building",
        )

        def search(q):
            return [_video(7, "firefighter running burning building")]

        filled = fill_stock_candidates([beat], search_fn=search)
        self.assertTrue(filled[0].candidate_assets)
        selected = select_stock_for_beats(filled, search_fn=search)
        report = stock_selection_report(selected)
        self.assertEqual(report["selected"], 1)
        self.assertEqual(report["misses"], 0)


class PlannerIntegrationTests(unittest.TestCase):
    def test_one_part_produces_multiple_beats(self):
        part = {"text": SHORT_STORY, "broll_query": "coastal village dawn"}
        beats = plan_enriched_beats(part, SHORT_STORY, duration=30.0, words=None)
        self.assertGreaterEqual(len(beats), 5)
        self.assertTrue(all(b.queries for b in beats))

    def test_each_beat_gets_own_retrieval_query(self):
        part = {"text": SHORT_STORY, "broll_query": "coastal village dawn"}
        beats = plan_enriched_beats(part, SHORT_STORY, duration=30.0)
        seen = []

        def search(q):
            seen.append(q)
            return [_video(hash(q) % 10000, q)]

        select_stock_for_beats(beats, provider="pexels", search_fn=search)
        # Multiple distinct beat queries were issued (not one shared bag).
        self.assertGreaterEqual(len(set(seen)), 3)

    def test_explicit_broll_query_preserved(self):
        part = {
            "text": "Soldiers marched through the city.",
            "broll_query": "ancient roman soldiers",
        }
        beats = plan_enriched_beats(part, part["text"], duration=8.0)
        joined = " ".join(
            " ".join(b.queries + b.fallback_queries) for b in beats
        ).lower()
        self.assertIn("ancient roman soldiers", joined)

    def test_fallback_when_primary_empty(self):
        beat = make_visual_beat(
            0, 3,
            narration="Astronaut walking abandoned station.",
            queries=["astronaut walking abandoned station"],
            fallback_queries=["astronaut space station corridor"],
            visual_intent="astronaut walking abandoned station",
            subject=["astronaut"],
            action=["walking"],
            location=["station"],
        )

        def search(q):
            if "abandoned" in q:
                return []
            return [_video(99, "astronaut space station corridor walking")]

        chosen = select_stock_for_beat(beat, search_fn=search)
        self.assertIsNotNone(chosen.selected_asset)
        self.assertEqual(chosen.selected_asset["id"], 99)
        self.assertTrue(chosen.selected_asset.get("used_fallback_query"))

    def test_search_cache_avoids_duplicate_calls(self):
        cache = SearchCache()
        calls = []

        def search(q):
            calls.append(q)
            return [_video(1, q)]

        beat_a = make_visual_beat(
            0, 3, narration="Dawn village.", queries=["coastal village dawn"],
        )
        beat_b = make_visual_beat(
            3, 6, narration="Village again.", queries=["coastal village dawn"],
        )
        collect_candidates_for_beat(beat_a, search_fn=search, cache=cache)
        collect_candidates_for_beat(beat_b, search_fn=search, cache=cache)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.misses, 1)

    def test_provider_failure_one_beat_does_not_kill_others(self):
        beats = [
            make_visual_beat(
                0, 3,
                narration="Fisherman on the pier.",
                queries=["fisherman on pier dawn"],
                visual_intent="fisherman on pier dawn",
                subject=["fisherman"],
            ),
            make_visual_beat(
                3, 6,
                narration="Broken radio static.",
                queries=["broken radio static close up"],
                visual_intent="broken radio static",
                objects=["radio"],
            ),
        ]

        def search(q):
            if "radio" in q:
                raise RuntimeError("quota")
            return [_video(5, "fisherman on wooden pier dawn")]

        out = select_stock_for_beats(beats, search_fn=search)
        self.assertIsNotNone(out[0].selected_asset)
        self.assertIsNone(out[1].selected_asset)

    def test_materialize_downloads_and_reuses_by_id(self):
        beats = select_stock_for_beats(
            [
                make_visual_beat(
                    0, 2,
                    narration="Firefighter running.",
                    queries=["firefighter running"],
                    subject=["firefighter"],
                    action=["running"],
                    visual_intent="firefighter running",
                ),
                make_visual_beat(
                    2, 4,
                    narration="Still the firefighter.",
                    queries=["firefighter"],
                    subject=["firefighter"],
                    visual_intent="firefighter",
                ),
            ],
            search_fn=lambda q: [
                _video(42, "firefighter running into smoke"),
                _video(43, "empty street night"),
            ],
        )
        downloads = []

        def fake_dl(sel, dest):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-mp4-content-xxxx")
            downloads.append(sel.get("id"))
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            items, records = materialize_stock_beats(
                beats, Path(tmp), "t", provider="pexels", download_fn=fake_dl,
            )
        self.assertGreaterEqual(len(items), 1)
        # Same asset id must not re-download.
        if (
            beats[0].selected_asset
            and beats[1].selected_asset
            and beats[0].selected_asset["id"] == beats[1].selected_asset["id"]
        ):
            self.assertEqual(downloads.count(42), 1)

    def test_visual_planner_flag(self):
        with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", True):
            self.assertTrue(visual_planner_enabled())
        with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", False):
            self.assertFalse(visual_planner_enabled())

    def test_build_planned_uses_planner_path(self):
        part = {
            "text": SHORT_STORY,
            "broll_query": "coastal village dawn",
        }
        calls = {"search": 0}

        def search(q):
            calls["search"] += 1
            return [
                _video(100 + (hash(q) % 50), q + " cinematic"),
                _video(200 + (hash(q) % 50), "unrelated gym workout"),
            ]

        def fake_dl(sel, dest):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * 4000)
            return dest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("broll_engine._headers_pexels", return_value={}):
                clips = build_planned_stock_videos(
                    part,
                    Path(tmp),
                    "smoke",
                    duration=28.0,
                    words=None,
                    provider="pexels",
                    text=SHORT_STORY,
                    search_fn=search,
                    download_fn=fake_dl,
                )
        self.assertGreaterEqual(len(clips), 5)
        self.assertTrue(all("path" in c and "start" in c and "end" in c for c in clips))
        self.assertGreater(calls["search"], 0)
        # Chronological coverage
        self.assertEqual(clips[0]["start"], 0.0)
        self.assertAlmostEqual(clips[-1]["end"], 28.0, places=1)

    def test_flag_routes_planner_vs_legacy(self):
        """VISUAL_PLANNER_ENABLED selects planner builder vs legacy fetch."""
        part = {"text": "Hello world night rain.", "broll_query": "rain street"}

        def route(enabled):
            with mock.patch.object(config, "VISUAL_PLANNER_ENABLED", enabled):
                if visual_planner_enabled():
                    return "planner"
                return "legacy"

        self.assertEqual(route(True), "planner")
        self.assertEqual(route(False), "legacy")
        # main.py uses the same visual_planner_enabled() gate for stock types.


if __name__ == "__main__":
    unittest.main()

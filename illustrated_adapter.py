"""
Illustrated / AI-image adapters for VisualBeat plans (Phase 7).

Wraps image_engine.fetch_ai_image + _panel_prompt. Does not rewrite the engine.
Does not touch Phase 6 stock adapters.
"""

from __future__ import annotations

import os
from pathlib import Path

import config
from stock_adapter import plan_enriched_beats
from visual_prompt import allocate_budgeted_beats, scene_text_from_beat


def illustrated_panel_budget(duration: float = None) -> int:
    """Respect COMIC_PANELS — do not emit one AI still per dense planner beat."""
    return max(1, int(getattr(config, "COMIC_PANELS", 10) or 10))


def beat_generation_prompt(
    beat,
    *,
    setting: str = "",
    character_lock: str = "",
    index: int = 0,
    style: str = "comic",
    story: str = "",
) -> str:
    """VisualBeat → existing _panel_prompt input (semantic scene, not generic style)."""
    from image_engine import _panel_prompt

    scene = scene_text_from_beat(beat, setting=setting)
    return _panel_prompt(
        scene,
        setting,
        character_lock,
        index=index,
        style=style,
        story=story,
    )


def build_planned_illustrated_storyboard(
    part: dict,
    out_dir: Path,
    duration: float,
    words: list = None,
    *,
    style: str = "comic",
    character_lock: str = "",
    character_seed: int = None,
    text: str = None,
    fetch_fn=None,
    panel_budget: int = None,
) -> list:
    """
    VisualBeat timeline → budgeted panels → existing fetch_ai_image.

    Returns [{path, start, end, beat, prompt, seed}, ...] for assemble_from_images.
    Preserves REFERENCE_IMAGE / CHARACTER_SEED / REUSE_BOARD_DIR / REUSE_IMAGES.
    """
    from image_engine import (
        _reuse_board_files,
        _reuse_board_panels,
        fetch_ai_image,
        use_ref_for_beat,
        visual_beat_times,
    )
    from tts_engine import for_speech

    spoken = text if text is not None else for_speech(part.get("text") or "")
    setting = (part.get("broll_query") or part.get("setting") or "").strip()
    story = part.get("text") or spoken
    budget = int(panel_budget if panel_budget is not None else illustrated_panel_budget(duration))

    planned = plan_enriched_beats(part, spoken, duration, words=words)
    beats = allocate_budgeted_beats(planned, budget, duration=duration)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Existing reuse-board path: same behavior as generate_storyboard.
    reuse_files = _reuse_board_files()
    if reuse_files:
        times = visual_beat_times(spoken, len(reuse_files), words or [], duration)
        scene_beats = [scene_text_from_beat(b, setting=setting) for b in beats]
        while len(scene_beats) < len(reuse_files):
            scene_beats.append(scene_beats[-1] if scene_beats else spoken[:80])
        reused = _reuse_board_panels(out_dir, scene_beats, times)
        if reused:
            print(
                "[illustrated] reusing board ({0} panel(s)); planner budget was {1}".format(
                    len(reused), budget,
                )
            )
            return reused

    fetch = fetch_fn or fetch_ai_image
    panels = []
    last_path = None
    for i, beat in enumerate(beats):
        scene = scene_text_from_beat(beat, setting=setting)
        prompt = beat_generation_prompt(
            beat,
            setting=setting,
            character_lock=character_lock or "",
            index=i,
            style=style,
            story=story,
        )
        dest = out_dir / "panel_{0}.jpg".format(i + 1)
        seed = None if character_seed is None else int(character_seed) + i * 17
        reuse = dest.exists() and dest.stat().st_size > 2000 and os.getenv("REUSE_IMAGES") == "1"
        if reuse:
            last_path = dest
            print("[illustrated] reusing {0}".format(dest.name))
        else:
            try:
                last_path = fetch(
                    prompt,
                    dest,
                    seed=seed,
                    style=style,
                    use_ref=use_ref_for_beat(scene),
                    beat=scene,
                )
            except Exception as exc:
                print(
                    "[illustrated] panel {0} failed ({1}); reusing previous".format(
                        i + 1, exc,
                    )
                )
                if last_path is None:
                    raise
        panels.append({
            "path": last_path,
            "start": float(beat.start),
            "end": float(beat.end),
            "beat": scene,
            "prompt": prompt,
            "seed": seed,
            "beat_id": beat.id,
        })
        print(
            "[illustrated] panel {0}/{1} [{2}] {3}".format(
                i + 1, len(beats), style, scene[:80],
            )
        )
        if i < len(beats) - 1 and fetch_fn is None and not reuse:
            import time
            time.sleep(2)

    if panels:
        panels[-1]["end"] = float(duration)
    return panels

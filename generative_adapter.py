"""
Pixazo + Pollinations AI-video adapters for VisualBeat plans (Phase 7).

Wraps existing generators. Does not rewrite pixazo_engine / video_engine.
Does not touch Phase 6 stock adapters.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import config
from illustrated_adapter import build_planned_illustrated_storyboard, illustrated_panel_budget
from stock_adapter import plan_enriched_beats
from visual_prompt import allocate_budgeted_beats, scene_text_from_beat


def pixazo_clip_budget(duration: float) -> int:
    from pixazo_engine import _clip_budget
    return int(_clip_budget(duration))


def build_planned_pixazo_storyboard(
    part: dict,
    clips_dir: Path,
    duration: float,
    words: list = None,
    *,
    text: str = None,
    fetch_fn=None,
    clip_budget: int = None,
) -> list:
    """
    Shot-plan visual_prompts (preferred) or VisualBeat timeline → Pixazo clips.

    Never sends the whole story as one generation prompt.
    Respects PIXAZO_MAX_CLIPS / _clip_budget. Returns [{path, start, end, beat}].
    """
    from pixazo_engine import _load_job, _require_key, fetch_pixazo_video
    from story_engine import normalize_shots
    from tts_engine import for_speech

    _require_key()
    spoken = text if text is not None else for_speech(part.get("text") or "")
    setting = (part.get("broll_query") or part.get("setting") or "").strip()
    budget = int(clip_budget if clip_budget is not None else pixazo_clip_budget(duration))

    shots = normalize_shots(part.get("shots") or [])
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    fetch = fetch_fn or fetch_pixazo_video

    if shots:
        selected = shots[:budget]
        print(
            "[pixazo-plan] {0} shot visual_prompt(s) for {1:.0f}s (budget {2})".format(
                len(selected), float(duration), budget,
            ),
            flush=True,
        )
        # Scale shot durations to cover the narration timeline.
        total_w = sum(max(1.0, float(s.get("duration") or 5)) for s in selected) or 1.0
        cursor = 0.0
        timeline = []
        for i, shot in enumerate(selected):
            weight = max(1.0, float(shot.get("duration") or 5))
            if i == len(selected) - 1:
                end = float(duration)
            else:
                end = min(float(duration), cursor + (weight / total_w) * float(duration))
            timeline.append((cursor, max(cursor + 0.1, end), shot))
            cursor = end

        clips = []
        last = None
        for i, (start, end, shot) in enumerate(timeline):
            scene = str(shot.get("visual_prompt") or "").strip()
            dest = clips_dir / "clip_{0}.mp4".format(i + 1)
            reuse = dest.exists() and dest.stat().st_size > 8000 and not _load_job(dest)
            if reuse:
                print("[pixazo-plan] reusing {0}".format(dest.name), flush=True)
                path = dest
            else:
                try:
                    path = fetch(scene, dest)
                except Exception as exc:
                    print(
                        "[pixazo-plan] shot {0} failed ({1}); reusing previous".format(
                            i + 1, exc,
                        ),
                        flush=True,
                    )
                    if last is None:
                        raise
                    path = last
            last = path
            clips.append({
                "path": path,
                "start": float(start),
                "end": float(end),
                "beat": scene,
                "shot_number": shot.get("shot_number"),
            })
            if i < len(timeline) - 1 and fetch_fn is None and not reuse:
                time.sleep(2)
        if clips:
            clips[-1]["end"] = float(duration)
        return clips

    planned = plan_enriched_beats(part, spoken, duration, words=words)
    beats = allocate_budgeted_beats(planned, budget, duration=duration)

    print(
        "[pixazo-plan] {0} clip(s) from {1} planner beat(s) for {2:.0f}s".format(
            len(beats), len(planned), float(duration),
        ),
        flush=True,
    )

    clips = []
    last = None
    for i, beat in enumerate(beats):
        scene = scene_text_from_beat(beat, setting=setting)
        dest = clips_dir / "clip_{0}.mp4".format(i + 1)
        start, end = float(beat.start), float(beat.end)
        reuse = dest.exists() and dest.stat().st_size > 8000 and not _load_job(dest)
        if reuse:
            print("[pixazo-plan] reusing {0}".format(dest.name), flush=True)
            path = dest
        else:
            try:
                path = fetch(scene, dest)
            except Exception as exc:
                print(
                    "[pixazo-plan] beat {0} failed ({1}); reusing previous".format(
                        i + 1, exc,
                    ),
                    flush=True,
                )
                if last is None:
                    raise
                path = last
        last = path
        clips.append({
            "path": path,
            "start": start,
            "end": end,
            "beat": scene,
            "beat_id": beat.id,
        })
        if i < len(beats) - 1 and fetch_fn is None and not reuse:
            time.sleep(2)

    if clips:
        clips[-1]["end"] = float(duration)
    return clips


def build_planned_ai_video_storyboard(
    part: dict,
    stills_dir: Path,
    clips_dir: Path,
    duration: float,
    words: list = None,
    *,
    style: str = "comic",
    character_lock: str = "",
    character_seed: int = None,
    text: str = None,
    fetch_still_fn=None,
    fetch_video_fn=None,
    panel_budget: int = None,
) -> list:
    """
    Budgeted illustrated stills (VisualBeat prompts) → existing fetch_ai_video.

    Panel count follows COMIC_PANELS (same as legacy AI-video still board).
    Does not spawn one paid clip per dense planner beat.
    """
    from video_engine import _require_video_key, _still_url, fetch_ai_video

    _require_video_key()
    budget = int(panel_budget if panel_budget is not None else illustrated_panel_budget(duration))

    panels = build_planned_illustrated_storyboard(
        part,
        stills_dir,
        duration,
        words=words,
        style=style,
        character_lock=character_lock,
        character_seed=character_seed,
        text=text,
        fetch_fn=fetch_still_fn,
        panel_budget=budget,
    )

    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    fetch_vid = fetch_video_fn or fetch_ai_video

    clips = []
    last = None
    for i, panel in enumerate(panels):
        dest = clips_dir / "clip_{0}.mp4".format(i + 1)
        reuse = dest.exists() and dest.stat().st_size > 8000 and os.getenv("REUSE_VIDEOS") == "1"
        if reuse:
            print("[ai-video-plan] reusing {0}".format(dest.name))
            path = dest
        else:
            try:
                path = fetch_vid(
                    panel.get("beat") or "",
                    dest,
                    image_url=_still_url(panel),
                )
            except Exception as exc:
                print(
                    "[ai-video-plan] clip {0} failed ({1}); reusing previous".format(
                        i + 1, exc,
                    )
                )
                if last is None:
                    raise
                path = last
        last = path
        clips.append({
            "path": path,
            "start": panel["start"],
            "end": panel["end"],
            "beat": panel.get("beat") or "",
        })
        if i < len(panels) - 1 and fetch_video_fn is None and not reuse:
            time.sleep(2)
    return clips

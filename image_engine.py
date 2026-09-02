"""
Free, keyless AI stills via Pollinations.ai.

Each part is a storyboard: several panels timed to the narration, not one
still held for 25 seconds. Prompts are built from the spoken sentences so
the pictures track the story.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

import config

_STYLE_SUFFIX = {
    "comic": config.COMIC_STYLE_SUFFIX,
    "cartoon": config.CARTOON_STYLE_SUFFIX,
    "anime": config.ANIME_STYLE_SUFFIX,
}


def fetch_ai_image(
    prompt: str,
    out_path: Path,
    width: int = None,
    height: int = None,
    seed: int = None,
    style: str = "comic",
    retries: int = 3,
) -> Path:
    width = width or max(64, int(round(config.VIDEO_WIDTH * 768 / max(config.VIDEO_WIDTH, 1))))
    height = height or max(64, int(round(config.VIDEO_HEIGHT * 768 / max(config.VIDEO_WIDTH, 1))))
    # Pollinations likes multiples of 8
    width = max(64, (width // 8) * 8)
    height = max(64, (height // 8) * 8)
    suffix = _STYLE_SUFFIX.get(style, config.COMIC_STYLE_SUFFIX)
    full_prompt = f"{prompt}{suffix}"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = None
    style_models = ("flux-anime", "turbo") if style in ("comic", "cartoon", "anime") else ("flux", "turbo")
    configured = getattr(config, "IMAGE_MODEL", "") or ""
    if configured.strip():
        style_models = (configured.strip(), "turbo")
    for attempt in range(1, retries + 1):
        params = {
            "width": width,
            "height": height,
            "nologo": "true",
            "model": style_models[0] if attempt == 1 else style_models[-1],
        }
        if seed is not None:
            params["seed"] = int(seed) + (attempt - 1)
        if config.POLLINATIONS_API_KEY:
            params["key"] = config.POLLINATIONS_API_KEY
        encoded = full_prompt if attempt == 1 else prompt + ", 2D illustration, bold outlines, not a photo"
        url = config.POLLINATIONS_BASE_URL + quote(encoded, safe="")
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=120,
                headers={"User-Agent": "faceless-pipeline/1.0"},
            )
            resp.raise_for_status()
            if not resp.content.startswith(b"\xff\xd8") and "image" not in resp.headers.get("content-type", ""):
                raise RuntimeError(f"Pollinations returned {resp.headers.get('content-type')}")
            out_path.write_bytes(resp.content)
            with Image.open(out_path) as im:
                im.verify()
            print(f"[image] generated '{prompt[:70]}...' -> {out_path.name}")
            return out_path
        except Exception as exc:
            last_error = exc
            print(f"[image] attempt {attempt}/{retries} failed: {exc}")
            time.sleep(2 * attempt)

    raise RuntimeError(f"Failed to generate image for prompt {prompt!r}: {last_error}")


def _sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def pack_beats(text: str, target: int = None) -> list:
    """Merge narration sentences into ~target visual beats."""
    target = target or getattr(config, "COMIC_PANELS", 5)
    sentences = _sentences(text)
    if not sentences:
        return [text.strip()] if text.strip() else []
    merged = []
    for s in sentences:
        if merged and len(s) < 28:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    if len(merged) <= target:
        return merged
    groups = []
    n = len(merged)
    for i in range(target):
        a = round(i * n / target)
        b = round((i + 1) * n / target)
        groups.append(" ".join(merged[a:b]))
    return [g for g in groups if g.strip()]


def _even_times(n: int, duration: float, words: list) -> list:
    """Equal-ish panel holds, snapped to nearby word starts so cuts land on speech."""
    n = max(int(n), 1)
    if n == 1:
        return [(0.0, duration)]
    targets = [duration * i / n for i in range(n + 1)]
    if words:
        anchors = [0.0] + [float(w["start"]) for w in words] + [duration]
        min_hold = max(0.6, duration / n * 0.4)
        snapped = [0.0]
        for idx, c in enumerate(targets[1:-1], start=1):
            remaining = n - idx
            nearest = min(anchors, key=lambda s: abs(s - c))
            latest = duration - min_hold * remaining
            nearest = min(max(nearest, snapped[-1] + min_hold), latest)
            snapped.append(nearest)
        snapped.append(duration)
        targets = snapped
    return [(targets[i], targets[i + 1] if i + 1 < n else duration) for i in range(n)]


def _times_look_ok(times: list, duration: float) -> bool:
    if len(times) < 2:
        return True
    holds = [end - start for start, end in times]
    if any(h < 0.45 for h in holds):
        return False
    if holds[0] > duration * 0.5:
        return False
    return True


def _beat_times(beats: list, words: list, duration: float) -> list:
    """Contiguous (start, end) covering 0..duration, aligned to Whisper when possible."""
    n = len(beats)
    if n == 1:
        return [(0.0, duration)]

    if not words:
        return _even_times(n, duration, [])

    cursor = 0
    starts = []
    for beat in beats:
        tokens = [tok for tok in re.findall(r"[a-z0-9']+", beat.lower()) if len(tok) > 2]
        if cursor >= len(words):
            starts.append(words[-1]["start"])
            continue
        starts.append(words[cursor]["start"])
        matched = 0
        i = cursor
        # Don't scan the whole transcript looking for visual-prompt words.
        search_limit = min(len(words), cursor + max(len(tokens) * 3, 8))
        while i < search_limit and matched < max(len(tokens) - 1, 1):
            ww = re.sub(r"[^a-z0-9']", "", words[i]["word"].lower())
            if ww and matched < len(tokens) and (ww == tokens[matched] or ww in tokens[matched] or tokens[matched] in ww):
                matched += 1
            i += 1
        cursor = max(i if matched else cursor + 1, cursor + 1)

    times = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < n else duration
        if end <= start:
            end = min(duration, start + duration / n)
        times.append((max(0.0, start), min(duration, end)))
    times[0] = (0.0, times[0][1])
    cleaned = [times[0]]
    for start, end in times[1:]:
        prev_s, _prev_e = cleaned[-1]
        cleaned[-1] = (prev_s, start)
        cleaned.append((start, end))
    cleaned[-1] = (cleaned[-1][0], duration)
    if not _times_look_ok(cleaned, duration):
        return _even_times(n, duration, words)
    return cleaned


_CAMERA = (
    "extreme close-up, subject filling the frame",
    "dutch angle, camera tilted, chaotic energy",
    "worm's-eye low angle looking up",
    "overhead bird's-eye looking down",
    "over-the-shoulder cinematic comic shot",
    "silhouette against a hard backlight",
    "smash-zoom composition, subject lunging at camera",
)


def _panel_prompt(beat: str, setting: str, character_lock: str, index: int = 0) -> str:
    angle = _CAMERA[index % len(_CAMERA)]
    bits = [
        f"2D animation keyframe of this exact story moment: {beat}",
        f"Camera: {angle}",
        "Action pose, implied motion, speed lines, saturated cel color",
    ]
    if setting:
        bits.append(f"Location: {setting}")
    if character_lock:
        bits.append(f"If the creature appears, draw: {character_lock}")
    bits.append(
        "Full bleed vertical frame, one scene only, not a split comic page, "
        "no letterbox, no black bars, no panel border. "
        "Depict the action in the sentence, not a random portrait. "
        "No text, no speech bubbles, no captions."
    )
    return ". ".join(bits)


def visual_beat_times(text: str, n: int, words: list, duration: float) -> list:
    """n contiguous (start, end) spans covering the full voiceover."""
    n = max(int(n), 1)
    spoken = pack_beats(text, target=n)
    times = _beat_times(spoken, words or [], duration)
    if len(times) != n or not _times_look_ok(times, duration):
        times = _even_times(n, duration, words or [])
    return times


def generate_storyboard(
    part: dict,
    out_dir: Path,
    duration: float,
    words: list,
    style: str = "comic",
    character_lock: str = "",
    character_seed: int = None,
) -> list:
    """
    Returns [{path, start, end, beat}, ...] covering the full narration.
    Uses part['image_prompts'] when provided, otherwise splits part['text'].
    """
    explicit = part.get("image_prompts")
    if explicit:
        beats = [str(p).strip() for p in explicit if str(p).strip()]
    else:
        beats = pack_beats(part["text"])
    if not beats:
        beats = [part.get("image_prompt") or part.get("broll_query") or part["text"][:160]]

    # Time against the spoken narration, never against image-prompt wording.
    # Matching visual prompts to Whisper words collapsed every panel onto the last second.
    spoken = pack_beats(part["text"], target=len(beats))
    times = _beat_times(spoken if len(spoken) == len(beats) else beats, words or [], duration)
    if len(times) != len(beats) or not _times_look_ok(times, duration):
        times = _even_times(len(beats), duration, words or [])
    setting = part.get("broll_query") or ""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    panels = []
    last_path = None
    for i, beat in enumerate(beats):
        prompt = _panel_prompt(beat, setting, character_lock, index=i)
        dest = out_dir / f"panel_{i + 1}.jpg"
        seed = None if character_seed is None else int(character_seed) + i * 17
        reuse = dest.exists() and dest.stat().st_size > 2000 and os.getenv("REUSE_IMAGES") == "1"
        if reuse:
            last_path = dest
            print(f"[image] reusing {dest.name}")
        else:
            try:
                last_path = fetch_ai_image(prompt, dest, seed=seed, style=style)
            except RuntimeError as exc:
                print(f"[image] panel {i + 1} failed ({exc}); reusing previous panel")
                if last_path is None:
                    raise
        start, end = times[i]
        panels.append({
            "path": last_path,
            "start": start,
            "end": end,
            "beat": beat,
            "prompt": prompt,
            "seed": seed,
        })
        print(f"[image] panel {i + 1}/{len(beats)} {start:.1f}s–{end:.1f}s: {beat[:80]}")
        if i < len(beats) - 1:
            time.sleep(2)
    return panels

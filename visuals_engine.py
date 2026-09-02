"""
Background visuals for a part.

video_type:
  live / real / stock     — Pexels stock video (photoreal)
  comic / 2D comic        — generated graphic-novel stills, Ken-Burns pan
  cartoon / 2D / 2d       — generated 2D cartoon stills, Ken-Burns pan
  anime                   — generated anime stills, Ken-Burns pan

Illustrated styles use the free Pollinations image API (no key required).
"""

from __future__ import annotations

import hashlib
import re
import time
import wave
from pathlib import Path
from urllib.parse import quote

import numpy as np
import requests
from PIL import Image

from broll_engine import fetch_broll
import config

# MoviePy 1.0.3 still uses Image.ANTIALIAS, removed in Pillow 10.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from moviepy.editor import CompositeVideoClip, ImageClip, concatenate_videoclips


ILLUSTRATED_TYPES = {"comic", "cartoon", "anime"}

_ALIASES = {
    "live": "live",
    "real": "live",
    "stock": "live",
    "pexels": "live",
    "photoreal": "live",
    "realistic_broll": "live",
    "realistic broll": "live",
    "realistic": "live",
    "broll": "live",
    "stock video": "live",
    "comic": "comic",
    "2d comic": "comic",
    "2d-comic": "comic",
    "2d_comic": "comic",
    "graphic novel": "comic",
    "cartoon": "cartoon",
    "2d": "cartoon",
    "2d cartoon": "cartoon",
    "2d-cartoon": "cartoon",
    "anime": "anime",
    "ai video": "ai_video",
    "2d video": "ai_video",
    "2d-video": "ai_video",
    "animated": "ai_video",
    "motion": "ai_video",
    "i2v": "ai_video",
}

_STYLE_PROMPTS = {
    "comic": (
        "2D horror comic book illustration, hand-inked bold black outlines, "
        "flat cel-shaded colors, graphic novel panel, printed halftone dots, "
        "simplified shapes, sequential art, NOT photorealistic, NOT 3D, "
        "NOT cinematic realism, no photograph, no text, no watermark, vertical 9:16"
    ),
    "cartoon": (
        "2D cartoon still, thick ink outlines, flat color fills, cel shading, "
        "stylized horror cartoon, limited palette, NOT photorealistic, NOT 3D, "
        "no photograph, no text, no watermark, vertical 9:16"
    ),
    "anime": (
        "2D anime illustration, clean line art, cel shading, horror atmosphere, "
        "NOT photorealistic, NOT 3D render, no photograph, no text, no watermark, "
        "vertical 9:16"
    ),
}


def normalize_video_type(raw) -> str:
    if not raw:
        return normalize_video_type(config.VIDEO_TYPE)
    key = str(raw).strip().lower().replace("_", " ")
    if key not in _ALIASES:
        known = ", ".join(sorted(set(_ALIASES)))
        raise ValueError(f"Unknown video_type={raw!r}. Use one of: {known}")
    return _ALIASES[key]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def trim_wav_to(path: Path, max_seconds: float) -> float:
    """Cut a wav in place to max_seconds. Returns the resulting duration."""
    path = Path(path)
    max_seconds = float(max_seconds)
    with wave.open(str(path), "rb") as src:
        params = src.getparams()
        rate = src.getframerate()
        total = src.getnframes()
        keep = int(rate * max_seconds)
        if keep >= total or keep <= 0:
            return total / float(rate)
        frames = src.readframes(keep)
    tmp = path.with_suffix(".trim.wav")
    with wave.open(str(tmp), "wb") as dest:
        dest.setparams(params)
        dest.writeframes(frames)
    tmp.replace(path)
    return keep / float(rate)


def prepare_background(
    query: str,
    out_path: Path,
    video_type: str,
    text: str,
    duration: float,
) -> Path:
    """Always writes an mp4 to out_path for assemble_video to consume."""
    style = normalize_video_type(video_type)
    print(f"[visuals] video_type={style}")
    if style == "live":
        path = fetch_broll(query, out_path)
        if path is None:
            raise RuntimeError(f"No Pexels clip found for {query!r}")
        return path
    return _illustrated_broll(query, text, out_path, style, duration)


def _scene_beats(text: str, query: str, count: int = 3) -> list:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks = [c.strip() for c in chunks if len(c.strip()) > 24]
    if not chunks:
        return [query]
    if len(chunks) <= count:
        return chunks
    last = len(chunks) - 1
    idxs = [round(i * last / (count - 1)) for i in range(count)]
    # keep unique, stable order
    seen = []
    for i in idxs:
        if i not in seen:
            seen.append(i)
    return [chunks[i] for i in seen]


def _prompt_for(style: str, query: str, beat: str, short: bool = False) -> str:
    if short:
        return f"2D {style} illustration of {query}, bold black outlines, flat cel shading, not a photo"
    return (
        f"2D {style} illustration of {query}: {beat}. "
        f"{_STYLE_PROMPTS[style]}"
    )


def _download_still(prompt: str, dest: Path, seed: int, fallback_prompt: str = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    fallback = fallback_prompt or prompt
    attempts = [
        {"model": "flux", "prompt": prompt, "seed": seed},
        {"model": "flux", "prompt": fallback, "seed": seed + 11},
        {"model": "turbo", "prompt": fallback, "seed": seed + 23},
    ]
    for i, attempt in enumerate(attempts, start=1):
        try:
            params = {
                "width": 768,
                "height": 1344,
                "model": attempt["model"],
                "nologo": "true",
                "seed": attempt["seed"],
            }
            if config.POLLINATIONS_API_KEY:
                params["key"] = config.POLLINATIONS_API_KEY
            resp = requests.get(
                "https://image.pollinations.ai/prompt/" + quote(attempt["prompt"], safe=""),
                params=params,
                timeout=120,
                headers={"User-Agent": "faceless-pipeline/1.0"},
            )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "image" not in ctype and not resp.content.startswith(b"\xff\xd8"):
                raise RuntimeError(f"Pollinations returned {ctype}, not an image")
            dest.write_bytes(resp.content)
            with Image.open(dest) as im:
                im.verify()
            print(f"[visuals] still -> {dest.name} ({attempt['model']})")
            return dest
        except Exception as exc:
            last_err = exc
            print(f"[visuals] still attempt {i} failed: {exc}")
            time.sleep(3 * i)
    raise RuntimeError(f"Could not generate illustrated still: {last_err}")


def _cover_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / img.width, height / img.height)
    new_w = max(int(img.width * scale), width)
    new_h = max(int(img.height * scale), height)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def _ken_burns_clip(image_path: Path, duration: float, zoom_in: bool):
    extra = 1.16
    frame_w = config.VIDEO_WIDTH
    frame_h = config.VIDEO_HEIGHT
    src = Image.open(image_path).convert("RGB")
    oversized = _cover_resize(src, int(frame_w * extra), int(frame_h * extra))
    arr = np.array(oversized)
    clip = ImageClip(arr).set_duration(duration)

    max_dx = oversized.width - frame_w
    max_dy = oversized.height - frame_h

    def pos(t):
        p = t / max(duration, 0.001)
        if not zoom_in:
            p = 1.0 - p
        return (-int(max_dx * p), -int(max_dy * p))

    moving = clip.set_position(pos)
    return CompositeVideoClip([moving], size=(frame_w, frame_h)).set_duration(duration)


def _illustrated_broll(query: str, text: str, out_path: Path, style: str, duration: float) -> Path:
    beats = _scene_beats(text, query, count=3)
    stills_dir = config.STILLS_DIR / out_path.stem
    stills_dir.mkdir(parents=True, exist_ok=True)

    stills = []
    for i, beat in enumerate(beats):
        seed_src = f"{style}|{query}|{beat}|{i}"
        seed = int(hashlib.md5(seed_src.encode()).hexdigest()[:8], 16)
        dest = stills_dir / f"panel_{i + 1}.jpg"
        prompt = _prompt_for(style, query, beat)
        try:
            _download_still(
                prompt,
                dest,
                seed,
                fallback_prompt=_prompt_for(style, query, query, short=True),
            )
            stills.append(dest)
        except RuntimeError as exc:
            print(f"[visuals] skipping panel {i + 1}: {exc}")
        if i < len(beats) - 1:
            time.sleep(3)
    if not stills:
        dest = stills_dir / "panel_1.jpg"
        _download_still(_prompt_for(style, query, query, short=True), dest, 1)
        stills.append(dest)

    panel_dur = max(duration / len(stills), 0.4)
    clips = [
        _ken_burns_clip(path, panel_dur, zoom_in=(i % 2 == 0))
        for i, path in enumerate(stills)
    ]
    bg = concatenate_videoclips(clips, method="compose").set_duration(duration)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bg.write_videofile(
            str(out_path),
            fps=config.FPS,
            codec="libx264",
            audio=False,
            threads=4,
            logger="bar",
        )
    finally:
        bg.close()
        for c in clips:
            c.close()
    print(f"[visuals] illustrated b-roll -> {out_path}")
    return out_path

"""
AI motion clips via Pollinations video (image-to-video).

Stills cannot become animation by zooming them. Each storyboard frame is sent
to a video model as the first frame; the model generates ~5s of real motion.
Those clips are then cut to the narration.

Video models are paid. Put POLLINATIONS_API_KEY in .env
(https://enter.pollinations.ai/keys). wan-fast is the cheap draft model.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

import config
from image_engine import generate_storyboard


def _require_video_key() -> str:
    key = (config.POLLINATIONS_API_KEY or "").strip()
    if not key:
        raise RuntimeError(
            "AI video needs POLLINATIONS_API_KEY in .env. "
            "Create one at https://enter.pollinations.ai/keys "
            "(video models are paid; wan-fast is about 0.05 Pollen per 5s clip)."
        )
    return key


def fetch_ai_video(
    prompt: str,
    out_path: Path,
    image_url: str = None,
    duration: int = None,
    retries: int = 3,
) -> Path:
    """
    Text-to-video, or image-to-video when image_url is a public still.
    Writes an mp4.
    """
    key = _require_video_key()
    duration = int(duration or getattr(config, "AI_VIDEO_SECONDS", 5))
    model = getattr(config, "AI_VIDEO_MODEL", "wan-fast")
    encoded = quote(
        f"{prompt}. 2D animated shot, camera moving, flickering light, "
        "subtle character motion, not a still photo, no text, no watermark",
        safe="",
    )
    url = getattr(config, "POLLINATIONS_VIDEO_URL", "https://gen.pollinations.ai/video/") + encoded
    params = {
        "model": model,
        "duration": duration,
        "aspectRatio": "9:16",
        "audio": "false",
        "nologo": "true",
    }
    if image_url:
        params["image"] = image_url
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "faceless-pipeline/1.0",
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"[video] {model} {duration}s '{prompt[:60]}...' (attempt {attempt})")
            resp = requests.get(
                url,
                params=params,
                timeout=300,
                headers=headers,
            )
            if resp.status_code == 402:
                raise RuntimeError(
                    "Payment required: video models spend Paid Pollen, not Quest Pollen. "
                    f"Dashboard said: {(resp.text or '')[:240]}"
                )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if b"ftyp" not in resp.content[:64]:
                raise RuntimeError(f"not a video ({ctype}): {resp.text[:200]}")
            out_path.write_bytes(resp.content)
            if out_path.stat().st_size < 8000:
                raise RuntimeError("video file too small")
            print(f"[video] -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")
            return out_path
        except Exception as exc:
            last_error = exc
            print(f"[video] attempt {attempt}/{retries} failed: {exc}")
            time.sleep(4 * attempt)
    raise RuntimeError(f"Failed to generate video for {prompt!r}: {last_error}")


def _still_url(panel: dict) -> str:
    """Public Pollinations URL for the same still (seeded), used as the I2V first frame."""
    prompt = panel.get("prompt") or panel["beat"]
    seed = panel.get("seed") or 1
    return (
        config.POLLINATIONS_BASE_URL
        + quote(str(prompt), safe="")
        + f"?width=768&height=1344&nologo=true&seed={int(seed)}"
    )


def generate_video_storyboard(
    part: dict,
    stills_dir: Path,
    clips_dir: Path,
    duration: float,
    words: list,
    style: str = "comic",
    character_lock: str = "",
    character_seed: int = None,
) -> list:
    """
    Still per beat, then a motion clip per still.
    Returns [{path, start, end, beat}, ...] where path is an mp4.
    """
    _require_video_key()
    panels = generate_storyboard(
        part=part,
        out_dir=stills_dir,
        duration=duration,
        words=words,
        style=style,
        character_lock=character_lock,
        character_seed=character_seed,
    )
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, panel in enumerate(panels):
        dest = clips_dir / f"clip_{i + 1}.mp4"
        reuse = dest.exists() and dest.stat().st_size > 8000 and os.getenv("REUSE_VIDEOS") == "1"
        if reuse:
            print(f"[video] reusing {dest.name}")
        else:
            fetch_ai_video(panel["beat"], dest, image_url=_still_url(panel))
        clips.append({
            "path": dest,
            "start": panel["start"],
            "end": panel["end"],
            "beat": panel["beat"],
        })
        if i < len(panels) - 1:
            time.sleep(2)
    return clips

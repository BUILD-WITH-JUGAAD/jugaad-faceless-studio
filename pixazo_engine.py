"""
Free Pixazo AI video (LTX 2.3) — fair-use free tier, no credit card.

Docs: https://www.pixazo.ai/models/ltx
Auth header: Ocp-Apim-Subscription-Key

Flow: POST /ltx-video/v1/text-to-video → poll /v2/requests/status/{id} → download mp4.

Free queue is slow (often 5–15+ min per clip). We poll one job to completion
instead of abandoning it and starting retries that stack more queue work.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

import config
from image_engine import pack_beats, visual_beat_times
from tts_engine import for_speech

# Free LTX jobs routinely sit in PROCESSING for many minutes.
_POLL_TIMEOUT_S = int(os.getenv("PIXAZO_POLL_TIMEOUT", "1200") or 1200)
_POLL_SLEEP_S = 10
# Cap clips so a short never queues dozens of free LTX jobs.
_MAX_CLIPS = int(os.getenv("PIXAZO_MAX_CLIPS", "12") or 12)
# Target ~one visual cut every N seconds when no explicit shot plan exists.
_SECONDS_PER_CLIP = float(os.getenv("PIXAZO_SECONDS_PER_CLIP", "5") or 5)
# Free LTX clip length. 49 frames @ 24fps ≈ 2s (API-safe default).
# Override with PIXAZO_NUM_FRAMES if your tier allows longer clips.
_NUM_FRAMES = int(os.getenv("PIXAZO_NUM_FRAMES", "49") or 49)
_FRAME_RATE = int(os.getenv("PIXAZO_FRAME_RATE", "24") or 24)


def _require_key() -> str:
    key = (getattr(config, "PIXAZO_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError(
            "Pixazo AI video needs PIXAZO_API_KEY in .env. "
            "Email signup (no card) at https://www.pixazo.ai/api/free"
        )
    return key


def _gateway() -> str:
    return (getattr(config, "PIXAZO_GATEWAY", "") or "https://gateway.pixazo.ai").rstrip("/")


def _headers(key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": key,
        "User-Agent": "faceless-pipeline/1.0",
    }


def _extract_url(payload) -> str:
    if isinstance(payload, str) and payload.startswith("http"):
        return payload
    if not isinstance(payload, dict):
        return ""
    for key in ("url", "video_url", "videoUrl", "media_url", "output", "result", "data"):
        val = payload.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, list):
            for item in val:
                nested = _extract_url(item) if isinstance(item, dict) else (
                    item if isinstance(item, str) and item.startswith("http") else ""
                )
                if nested:
                    return nested
        if isinstance(val, dict):
            nested = _extract_url(val)
            if nested:
                return nested
    urls = payload.get("urls")
    if isinstance(urls, list) and urls:
        first = urls[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
    return ""


def _download_url(url: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                fh.write(chunk)
    if out_path.stat().st_size < 8000:
        raise RuntimeError("Pixazo video file too small")
    return out_path


def _job_sidecar(out_path: Path) -> Path:
    return Path(out_path).with_suffix(".pixazo.json")


def _save_job(out_path: Path, request_id: str, polling_url: str, prompt: str) -> None:
    _job_sidecar(out_path).write_text(json.dumps({
        "request_id": request_id,
        "polling_url": polling_url,
        "prompt": prompt,
        "started": time.time(),
    }))


def _load_job(out_path: Path) -> dict:
    path = _job_sidecar(out_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clear_job(out_path: Path) -> None:
    path = _job_sidecar(out_path)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _poll_result(
    key: str,
    request_id: str,
    polling_url: str = "",
    timeout_s: int = None,
) -> str:
    """Poll until COMPLETED; return media URL. Free queue can take 10–20 min."""
    timeout_s = int(timeout_s if timeout_s is not None else _POLL_TIMEOUT_S)
    status_url = (polling_url or "").strip()
    if not status_url:
        status_url = "{0}/v2/requests/status/{1}".format(_gateway(), request_id)
    headers = {"Ocp-Apim-Subscription-Key": key, "User-Agent": "faceless-pipeline/1.0"}
    deadline = time.time() + max(120, timeout_s)
    last = {}
    started = time.time()
    last_print = 0.0
    while time.time() < deadline:
        resp = requests.get(status_url, headers=headers, timeout=60)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                "Pixazo rejected the API key ({0}): {1}".format(
                    resp.status_code, (resp.text or "")[:200],
                )
            )
        resp.raise_for_status()
        try:
            last = resp.json()
        except Exception:
            raise RuntimeError("Pixazo status was not JSON: {0}".format((resp.text or "")[:200]))
        status = str(last.get("status") or "").upper()
        elapsed = int(time.time() - started)
        if status == "COMPLETED":
            media = _extract_url(last.get("output") or last)
            if not media:
                raise RuntimeError("Pixazo COMPLETED with no media URL: {0}".format(str(last)[:240]))
            print("[pixazo] COMPLETED in {0}s".format(elapsed), flush=True)
            return media
        if status in ("FAILED", "ERROR"):
            raise RuntimeError(
                "Pixazo {0}: {1}".format(status, last.get("error") or str(last)[:240])
            )
        # Print at most every ~30s so the studio log does not flood.
        if time.time() - last_print >= 30:
            print(
                "[pixazo] status={0} ({1}s / {2}s max) id={3}".format(
                    status or "?", elapsed, timeout_s, (request_id or "")[:36],
                ),
                flush=True,
            )
            last_print = time.time()
        time.sleep(_POLL_SLEEP_S)
    raise RuntimeError(
        "Pixazo still {0} after {1}s — free queue is slow. "
        "Wait and re-run with REUSE_VIDEOS=1, or switch to Live b-roll / comic. "
        "Last: {2}".format(
            str(last.get("status") or "unknown"),
            timeout_s,
            str(last)[:180],
        )
    )


def _submit(key: str, prompt: str) -> tuple:
    path = getattr(config, "PIXAZO_VIDEO_PATH", "") or "/ltx-video/v1/text-to-video"
    url = _gateway() + path
    frames = max(25, min(121, int(getattr(config, "PIXAZO_NUM_FRAMES", _NUM_FRAMES) or _NUM_FRAMES)))
    fps = max(8, min(30, int(getattr(config, "PIXAZO_FRAME_RATE", _FRAME_RATE) or _FRAME_RATE)))
    body = {
        "prompt": (
            "{0}. cinematic vertical 9:16 shot, clear physical motion through the clip, "
            "atmospheric lighting, no text, no watermark, no freeze-frame".format(prompt)
        ),
        "aspect": "9:16",
        # Keep within Pixazo free-tier duration limits; one action per clip.
        "num_frames": frames,
        "frame_rate": fps,
    }
    resp = requests.post(url, headers=_headers(key), json=body, timeout=120)
    if resp.status_code in (401, 403):
        raise RuntimeError(
            "Pixazo rejected the API key ({0}): {1}".format(
                resp.status_code, (resp.text or "")[:200],
            )
        )
    if resp.status_code == 402:
        raise RuntimeError(
            "Pixazo wallet balance too low: {0}".format((resp.text or "")[:200])
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Pixazo endpoint not found ({0}). Check PIXAZO_VIDEO_PATH / API docs.".format(url)
        )
    if resp.status_code not in (200, 202):
        resp.raise_for_status()

    ctype = (resp.headers.get("content-type") or "").lower()
    raw = resp.content
    if "video" in ctype or (raw[:64] and b"ftyp" in raw[:64]):
        return ("bytes", raw)

    try:
        payload = resp.json()
    except Exception:
        raise RuntimeError(
            "unexpected Pixazo response ({0}): {1}".format(ctype, (resp.text or "")[:200])
        )

    request_id = str(payload.get("request_id") or "").strip()
    polling_url = str(payload.get("polling_url") or "").strip()
    if request_id or polling_url:
        return ("job", {"request_id": request_id, "polling_url": polling_url})

    media = _extract_url(payload)
    if media:
        return ("url", media)
    raise RuntimeError(
        "Pixazo JSON had no request_id or media URL: {0}".format(str(payload)[:240])
    )


def fetch_pixazo_video(prompt: str, out_path: Path, retries: int = 2) -> Path:
    """Text-to-video via free LTX 2.3. Writes an mp4. Resumes in-flight jobs."""
    key = _require_key()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume a previous queue job for this clip instead of POSTing again.
    pending = _load_job(out_path)
    if pending.get("request_id") or pending.get("polling_url"):
        print(
            "[pixazo] resuming {0}…".format((pending.get("request_id") or "job")[:40]),
            flush=True,
        )
        try:
            media = _poll_result(
                key,
                pending.get("request_id") or "",
                polling_url=pending.get("polling_url") or "",
            )
            if media.startswith("/"):
                media = _gateway() + media
            _download_url(media, out_path)
            _clear_job(out_path)
            print("[pixazo] -> {0} ({1} KB)".format(
                out_path.name, out_path.stat().st_size // 1024,
            ), flush=True)
            return out_path
        except Exception as exc:
            msg = str(exc)
            # Still processing / soft timeout → keep sidecar and fail soft for storyboard reuse.
            if "still" in msg.lower() or "timed out" in msg.lower() or "PROCESSING" in msg:
                raise
            print("[pixazo] resume failed ({0}); submitting new job".format(exc), flush=True)
            _clear_job(out_path)

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(
                "[pixazo] LTX '{0}...' (attempt {1}/{2}, poll up to {3}s)".format(
                    prompt[:60], attempt, retries, _POLL_TIMEOUT_S,
                ),
                flush=True,
            )
            kind, payload = _submit(key, prompt)
            if kind == "bytes":
                out_path.write_bytes(payload)
                if out_path.stat().st_size < 8000:
                    raise RuntimeError("Pixazo returned a tiny video body")
                _clear_job(out_path)
                print("[pixazo] -> {0} ({1} KB)".format(
                    out_path.name, out_path.stat().st_size // 1024,
                ), flush=True)
                return out_path

            if kind == "url":
                media = payload
            else:
                request_id = payload["request_id"]
                polling_url = payload["polling_url"]
                _save_job(out_path, request_id, polling_url, prompt)
                media = _poll_result(key, request_id, polling_url=polling_url)

            if media.startswith("/"):
                media = _gateway() + media
            parsed = urlparse(media)
            if parsed.scheme not in ("http", "https"):
                raise RuntimeError("bad Pixazo media URL: {0}".format(media[:120]))
            _download_url(media, out_path)
            _clear_job(out_path)
            print("[pixazo] -> {0} ({1} KB)".format(
                out_path.name, out_path.stat().st_size // 1024,
            ), flush=True)
            return out_path
        except Exception as exc:
            last_error = exc
            print("[pixazo] attempt {0}/{1} failed: {2}".format(attempt, retries, exc), flush=True)
            # Do not stack new free-queue jobs if this one is still processing.
            if "still" in str(exc).lower() or "PROCESSING" in str(exc):
                break
            time.sleep(4 * attempt)
    raise RuntimeError("Failed to generate Pixazo video for {0!r}: {1}".format(
        prompt, last_error,
    ))


def _clip_budget(duration: float) -> int:
    hard_max = max(1, min(_MAX_CLIPS, int(getattr(config, "PIXAZO_MAX_CLIPS", _MAX_CLIPS) or _MAX_CLIPS)))
    seconds = max(1.0, float(duration or 1.0))
    # Prefer ~4–6s holds when no explicit shot list is present.
    by_time = int(round(seconds / max(4.0, min(6.0, _SECONDS_PER_CLIP))))
    if seconds >= 60:
        by_time = max(10, by_time)
    elif seconds >= 30:
        by_time = max(5, by_time)
    else:
        by_time = max(3, by_time)
    return max(1, min(hard_max, by_time))


def _shot_prompts(part: dict) -> list:
    """Prefer structured shot visual_prompts over whole-story / stock keys."""
    try:
        from story_engine import normalize_shots
        shots = normalize_shots(part.get("shots") or [])
    except Exception:
        shots = [
            s for s in (part.get("shots") or [])
            if isinstance(s, dict) and str(s.get("visual_prompt") or "").strip()
        ]
    prompts = []
    for shot in shots:
        prompt = str(shot.get("visual_prompt") or "").strip()
        if prompt:
            prompts.append(prompt)
    return prompts


def generate_pixazo_storyboard(
    part: dict,
    clips_dir: Path,
    duration: float,
    words: list,
) -> list:
    """One LTX clip per shot visual_prompt (or legacy visual key), budgeted by duration."""
    _require_key()
    spoken = for_speech(part["text"])
    max_clips = _clip_budget(duration)
    shot_prompts = _shot_prompts(part)
    if shot_prompts:
        beats = shot_prompts[:max_clips]
        print(
            "[pixazo] using {0} shot visual_prompt(s) (capped at {1})".format(
                len(beats), max_clips,
            ),
            flush=True,
        )
    else:
        keys = part.get("broll_queries") or part.get("image_prompts") or []
        keys = [str(k).strip() for k in keys if str(k).strip()]
        if keys:
            beats = keys[:max_clips]
            while len(beats) < max_clips and keys:
                beats.append(keys[len(beats) % len(keys)])
        else:
            beats = pack_beats(spoken, target=max_clips) or [spoken[:160]]
            beats = beats[:max_clips]
            # Never send the entire narration as one long generation prompt.
            if len(beats) == 1 and len(str(beats[0]).split()) > 40:
                beats = pack_beats(spoken, target=max(3, max_clips))[:max_clips] or beats
    print(
        "[pixazo] storyboard: {0} clip(s) for {1:.0f}s "
        "(~1 cut / {2:.0f}s; free queue is slow)".format(
            len(beats), float(duration), max(4.0, _SECONDS_PER_CLIP),
        ),
        flush=True,
    )
    times = visual_beat_times(spoken, len(beats), words or [], duration)
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    last = None
    for i, beat in enumerate(beats):
        dest = clips_dir / "clip_{0}.mp4".format(i + 1)
        start, end = times[i]
        reuse = dest.exists() and dest.stat().st_size > 8000 and not _load_job(dest)
        if reuse:
            print("[pixazo] reusing {0}".format(dest.name), flush=True)
            path = dest
        else:
            try:
                path = fetch_pixazo_video(beat, dest)
            except Exception as exc:
                print("[pixazo] beat {0} failed ({1}); reusing previous".format(i + 1, exc), flush=True)
                if last is None:
                    raise
                path = last
        last = path
        clips.append({"path": path, "start": start, "end": end, "beat": beat})
        if i < len(beats) - 1:
            time.sleep(2)
    if clips:
        clips[-1]["end"] = times[-1][1]
    return clips

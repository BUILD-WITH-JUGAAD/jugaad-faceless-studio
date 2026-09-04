"""
Free, keyless AI stills via Pollinations.ai.

Each part is a storyboard: several panels timed to the narration, not one
still held for 25 seconds. Prompts are built from the spoken sentences so
the pictures track the story.
"""

from __future__ import annotations

import os
import re
import shutil
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


_MALE = re.compile(
    r"\b(he|him|his|himself|boy|man|male|schoolboy|brother|son|lad|guy)\b",
    re.I,
)
_FEMALE = re.compile(
    r"\b(she|her|hers|herself|girl|woman|female|schoolgirl|sister|daughter|lady)\b",
    re.I,
)
_CREATURE = re.compile(
    r"\b(ghost|yokai|spirit|teke|demon|monster|shetani|creature|apparition|"
    r"haunting|undead|ghoul|wraith|silhouette|popobawa|qallupilluk|yurei|"
    r"crawling ghost)\b",
    re.I,
)


def story_gender(text: str) -> str:
    raw = text or ""
    male = len(_MALE.findall(raw))
    female = len(_FEMALE.findall(raw))
    if male >= female + 2:
        return "male"
    if female >= male + 2:
        return "female"
    return ""


def beat_gender(beat: str, story: str = "") -> str:
    raw = beat or ""
    if re.search(r"\b(schoolgirl|girl|woman)\b", raw, re.I) and not re.search(
        r"\b(schoolboy|boy|man)\b", raw, re.I
    ):
        return "female"
    if re.search(r"\b(schoolboy|boy|man)\b", raw, re.I):
        return "male"
    return story_gender(story)


def beat_is_creature(beat: str) -> bool:
    return bool(_CREATURE.search(beat or ""))


def _ref_role() -> str:
    role = (getattr(config, "REFERENCE_ROLE", "") or "creature").strip().lower()
    if role in {"ghost", "monster", "creature"}:
        return "creature"
    if role in {"character", "face", "person", "human"}:
        return "character"
    if role in {"style", "mood"}:
        return "style"
    if role in {"off", "none", "0"}:
        return "off"
    return "creature"


def use_ref_for_beat(beat: str) -> bool:
    if not _ref_path().is_file():
        return False
    role = _ref_role()
    if role == "off":
        return False
    if role == "style":
        return False
    if role == "character":
        return True
    return beat_is_creature(beat)


def fetch_ai_image(
    prompt: str,
    out_path: Path,
    width: int = None,
    height: int = None,
    seed: int = None,
    style: str = "comic",
    retries: int = 3,
    use_ref: bool = None,
    beat: str = "",
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
    ref = _ref_path()
    if use_ref is None:
        use_ref = use_ref_for_beat(beat or prompt)
    if ref.is_file() and use_ref:
        try:
            return _fetch_with_reference(prompt, out_path, ref, width, height, style)
        except Exception as exc:
            print("[image] reference failed ({0}); drawing without it".format(exc))
            last_error = exc
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


def _ref_path(explicit=None) -> Path:
    raw = explicit or getattr(config, "REFERENCE_IMAGE", "") or ""
    path = Path(str(raw).strip()) if raw else Path()
    return path if path.is_file() else Path()


def _fetch_with_reference(prompt: str, out_path: Path, ref: Path, width: int, height: int, style: str) -> Path:
    """Lock the still to an uploaded photo via Pollinations image edit (kontext)."""
    suffix = _STYLE_SUFFIX.get(style, config.COMIC_STYLE_SUFFIX)
    role = _ref_role()
    if role == "creature":
        lock = (
            "The reference image is a ghost or creature only. Match that creature "
            "when it appears. Do not put that face on a living person. "
        )
    elif role == "style":
        lock = "Match only the art style and palette of the reference. Do not copy the face. "
    else:
        lock = "Keep the same human character, face, and outfit as the reference image. "
    full_prompt = lock + "New scene: {0}{1}".format(prompt, suffix)
    key = (config.POLLINATIONS_API_KEY or "").strip()
    headers = {"User-Agent": "faceless-pipeline/1.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    mime = "image/jpeg"
    if ref.suffix.lower() == ".png":
        mime = "image/png"
    elif ref.suffix.lower() in {".webp"}:
        mime = "image/webp"
    files = {"image": (ref.name, ref.read_bytes(), mime)}
    data = {
        "prompt": full_prompt[:1800],
        "model": "kontext",
        "size": "{0}x{1}".format(width, height),
        "nologo": "true",
    }
    resp = requests.post(
        "https://gen.pollinations.ai/v1/images/edits",
        headers=headers,
        files=files,
        data=data,
        timeout=180,
    )
    if resp.status_code >= 400:
        raise RuntimeError("reference edit HTTP {0}: {1}".format(resp.status_code, (resp.text or "")[:200]))
    ctype = resp.headers.get("content-type", "")
    body = resp.content
    if body.startswith(b"\xff\xd8") or "image/" in ctype:
        out_path.write_bytes(body)
    else:
        payload = resp.json()
        item = ((payload.get("data") or [{}])[0]) if isinstance(payload, dict) else {}
        b64 = item.get("b64_json") or ""
        url = item.get("url") or ""
        if b64:
            import base64
            out_path.write_bytes(base64.b64decode(b64))
        elif url:
            got = requests.get(url, timeout=120, headers={"User-Agent": "faceless-pipeline/1.0"})
            got.raise_for_status()
            out_path.write_bytes(got.content)
        else:
            raise RuntimeError("reference edit returned no image")
    with Image.open(out_path) as im:
        im.verify()
    print("[image] referenced '{0}...' -> {1}".format(prompt[:70], out_path.name))
    return out_path


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


_MEDIUM = {
    "anime": (
        "2D anime still, Japanese animation, clean line art, cel shaded, "
        "not a photograph, not live action, not 3D"
    ),
    "cartoon": (
        "2D cartoon still, thick ink outlines, flat saturated color, "
        "not a photograph, not 3D"
    ),
    "comic": (
        "2D comic keyframe, graphic novel, cel shaded, "
        "not a photograph, not 3D"
    ),
}


def _panel_prompt(
    beat: str,
    setting: str,
    character_lock: str,
    index: int = 0,
    style: str = "comic",
    story: str = "",
) -> str:
    angle = _CAMERA[index % len(_CAMERA)]
    visual = (beat or "").strip()
    medium = _MEDIUM.get(style, _MEDIUM["comic"])
    bits = [
        visual,
        medium,
        f"Camera: {angle}",
        "One clear scene, action in frame, saturated color",
    ]
    if setting:
        bits.append(f"Location: {setting}")
    gender = beat_gender(beat, story)
    if gender == "male" and not beat_is_creature(beat):
        bits.append("The living human is male, a boy or man, he/him. Do not draw a girl")
    elif gender == "female" and not beat_is_creature(beat):
        bits.append("The living human is female, a girl or woman, she/her. Do not draw a boy")
    if character_lock:
        bits.append(f"If the creature appears, draw: {character_lock}")
    role = _ref_role()
    if _ref_path().is_file() and role == "character":
        bits.append("Same human as the reference photo, consistent face and clothes")
    elif _ref_path().is_file() and role == "creature":
        bits.append(
            "Reference photo is the ghost or creature only. "
            "Do not put that face on the living narrator"
        )
    elif _ref_path().is_file() and role == "style":
        bits.append("Match the reference art style only, not the face")
    bits.append(
        "Full bleed vertical frame, one scene only, not a split comic page, "
        "no letterbox, no black bars, no panel border. "
        "Draw this visual search exactly, not a random portrait. "
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


def _beats_for(part: dict, panel_count: int = None) -> list:
    explicit = part.get("image_prompts")
    if explicit:
        beats = [str(p).strip() for p in explicit if str(p).strip()]
    else:
        from tts_engine import for_speech
        beats = pack_beats(for_speech(part["text"]), target=panel_count)
    if not beats:
        beats = [part.get("image_prompt") or part.get("broll_query") or (part.get("text") or "")[:160]]
    return [b for b in beats if str(b).strip()]


def generate_board(
    part: dict,
    out_dir: Path,
    style: str = "comic",
    character_lock: str = "",
    character_seed: int = None,
    width: int = None,
    height: int = None,
    panel_count: int = None,
    on_panel=None,
    story: str = "",
) -> list:
    """
    Stills only — no narration timing. Used by the Studio Images tab.
    Returns [{path, beat, prompt, seed}, ...].
    """
    beats = _beats_for(part, panel_count=panel_count)
    if not beats:
        raise ValueError("Write a story before drawing panels.")
    setting = part.get("broll_query") or ""
    story = story or part.get("text") or ""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    panels = []
    last_path = None
    for i, beat in enumerate(beats):
        prompt = _panel_prompt(
            beat, setting, character_lock, index=i, style=style, story=story,
        )
        dest = out_dir / f"panel_{i + 1}.jpg"
        seed = None if character_seed is None else int(character_seed) + i * 17
        try:
            last_path = fetch_ai_image(
                prompt, dest, seed=seed, style=style, width=width, height=height,
                use_ref=use_ref_for_beat(beat), beat=beat,
            )
        except RuntimeError as exc:
            print(f"[image] panel {i + 1} failed ({exc}); reusing previous panel")
            if last_path is None:
                raise
        panels.append({
            "path": last_path,
            "beat": beat,
            "prompt": prompt,
            "seed": seed,
        })
        print(f"[image] panel {i + 1}/{len(beats)}: {beat[:80]}")
        if on_panel:
            on_panel(i + 1, len(beats), panels[-1])
        if i < len(beats) - 1:
            time.sleep(2)
    return panels


def _reuse_board_files() -> list:
    raw = (getattr(config, "REUSE_BOARD_DIR", "") or "").strip()
    if not raw:
        return []
    src = Path(raw)
    if not src.is_dir():
        return []
    return sorted(src.glob("panel_*.jpg"))


def _reuse_board_panels(out_dir: Path, beats: list, times: list) -> list:
    files = _reuse_board_files()
    if not files:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panels = []
    for i, src_path in enumerate(files):
        dest = out_dir / "panel_{0}.jpg".format(i + 1)
        if dest.resolve() != src_path.resolve():
            shutil.copy2(src_path, dest)
        start, end = times[i] if i < len(times) else (0.0, 1.0)
        beat = beats[i] if i < len(beats) else src_path.stem
        panels.append({
            "path": dest,
            "start": start,
            "end": end,
            "beat": beat,
            "prompt": "",
            "seed": None,
        })
        print("[image] reusing board {0} -> {1}".format(src_path.name, dest.name))
    if times and panels:
        panels[-1]["end"] = times[-1][1]
    return panels


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
    beats = _beats_for(part)
    if not beats:
        beats = [part.get("image_prompt") or part.get("broll_query") or part["text"][:160]]

    # Time against the spoken narration, never against image-prompt wording.
    # Matching visual prompts to Whisper words collapsed every panel onto the last second.
    from tts_engine import for_speech
    spoken = pack_beats(for_speech(part["text"]), target=len(beats))
    times = _beat_times(spoken if len(spoken) == len(beats) else beats, words or [], duration)
    if len(times) != len(beats) or not _times_look_ok(times, duration):
        times = _even_times(len(beats), duration, words or [])
    setting = part.get("broll_query") or ""
    story = part.get("text") or ""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reuse_files = _reuse_board_files()
    if reuse_files:
        times = visual_beat_times(for_speech(part["text"]), len(reuse_files), words or [], duration)
        reused = _reuse_board_panels(out_dir, beats, times)
        if reused:
            return reused

    panels = []
    last_path = None
    for i, beat in enumerate(beats):
        prompt = _panel_prompt(
            beat, setting, character_lock, index=i, style=style, story=story,
        )
        dest = out_dir / f"panel_{i + 1}.jpg"
        seed = None if character_seed is None else int(character_seed) + i * 17
        reuse = dest.exists() and dest.stat().st_size > 2000 and os.getenv("REUSE_IMAGES") == "1"
        if reuse:
            last_path = dest
            print(f"[image] reusing {dest.name}")
        else:
            try:
                last_path = fetch_ai_image(
                    prompt, dest, seed=seed, style=style,
                    use_ref=use_ref_for_beat(beat), beat=beat,
                )
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

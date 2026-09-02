"""
Combines background footage + narration audio + burned-in word-by-word captions
into a final vertical (1080x1920) video, ready to upload as a Short/Reel.
"""

from __future__ import annotations

import math
from pathlib import Path

# MoviePy 1.0.3 still uses Image.ANTIALIAS, removed in Pillow 10.
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import numpy as np
from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    VideoFileClip,
    concatenate_videoclips,
)
from moviepy.audio.fx.audio_loop import audio_loop
import config
from music_engine import pick_background_music

# ImageMagick-style names -> macOS font files (MoviePy TextClip needs ImageMagick
# and often can't resolve Arial-Bold on a fresh Mac install).
_FONT_FILES = {
    "Arial-Bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "Arial": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "Arial-Black": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "Impact": "/System/Library/Fonts/Supplemental/Impact.ttf",
    "Helvetica": "/System/Library/Fonts/Helvetica.ttc",
}

_W = config.VIDEO_WIDTH
_H = config.VIDEO_HEIGHT
_SRC_SCALE = 1.72


def _sync_canvas():
    """Pick up size= / studio aspect-ratio changes from config."""
    global _W, _H
    _W = int(config.VIDEO_WIDTH)
    _H = int(config.VIDEO_HEIGHT)


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = _FONT_FILES.get(name, name)
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        fallback = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        return ImageFont.truetype(fallback, size=size)


def _abs_pos(pos):
    """Turn ('center', 0.82) into pixel coords. Bare floats are pixels in MoviePy."""
    x, y = pos
    if isinstance(x, float) and 0.0 <= x <= 1.0:
        x = int(_W * x)
    if isinstance(y, float) and 0.0 <= y <= 1.0:
        y = int(_H * y)
    return (x, y)


def _render_text_rgba(
    text: str,
    fontsize: int,
    color: str,
    stroke_color: str,
    stroke_width: int,
    max_width: int = None,
    badge: bool = False,
) -> np.ndarray:
    font = _load_font(config.CAPTION_FONT, fontsize)

    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)

    def measure(s: str):
        bbox = draw.textbbox((0, 0), s, font=font, stroke_width=stroke_width)
        return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox

    lines = [text]
    if max_width:
        words = text.split()
        lines = []
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            w, _, _ = measure(trial)
            if w <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

    line_sizes = [measure(line) for line in lines]
    pad = max(stroke_width * 2, 8)
    if badge:
        pad = max(pad, 16)
    img_w = max(s[0] for s in line_sizes) + pad * 2
    line_h = max(s[1] for s in line_sizes)
    img_h = line_h * len(lines) + pad * 2

    img = Image.new("RGBA", (max(img_w, 1), max(img_h, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if badge:
        try:
            draw.rounded_rectangle(
                [0, 0, img_w - 1, img_h - 1],
                radius=18,
                fill=(0, 0, 0, 200),
            )
        except Exception:
            draw.rectangle([0, 0, img_w - 1, img_h - 1], fill=(0, 0, 0, 200))
    y = pad
    for line, (lw, lh, bbox) in zip(lines, line_sizes):
        x = (img_w - lw) // 2 - bbox[0]
        draw.text(
            (x, y - bbox[1]),
            line,
            font=font,
            fill=color,
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
        )
        y += line_h
    return np.array(img)


def _text_clip(
    text: str,
    fontsize: int,
    color: str,
    stroke_color: str,
    stroke_width: int,
    start: float,
    end: float,
    position,
    max_width: int = None,
    badge: bool = False,
) -> ImageClip:
    arr = _render_text_rgba(
        text, fontsize, color, stroke_color, stroke_width,
        max_width=max_width, badge=badge,
    )
    clip = ImageClip(arr, transparent=True).set_start(start).set_end(end)
    return clip.set_position(_abs_pos(position))


def _make_caption_clip(chunk: dict, video_size: tuple) -> ImageClip:
    return _text_clip(
        chunk["text"],
        fontsize=config.CAPTION_FONTSIZE,
        color=config.CAPTION_COLOR,
        stroke_color=config.CAPTION_STROKE_COLOR,
        stroke_width=config.CAPTION_STROKE_WIDTH,
        start=chunk["start"],
        end=chunk["end"],
        position=config.CAPTION_POSITION,
        max_width=int(video_size[0] * 0.9),
    )


def _bottom_scrim(duration: float) -> ImageClip:
    """Dark gradient behind captions so white type stays readable."""
    h = int(_H * 0.28)
    alpha = np.linspace(0, 170, h, dtype=np.uint8)
    layer = np.zeros((h, _W, 4), dtype=np.uint8)
    layer[:, :, 3] = alpha[:, None]
    return (
        ImageClip(layer, transparent=True)
        .set_duration(duration)
        .set_position(("center", _H - h))
    )


def _mix_audio(narration, duration, extras):
    """Narration on top of a quiet looping background track, if one exists."""
    track = pick_background_music()
    if track is None:
        return narration
    music = AudioFileClip(str(track))
    extras.append(music)
    vol = float(getattr(config, "MUSIC_VOLUME", 0.12))
    if hasattr(music, "volumex"):
        music = music.volumex(vol)
    if music.duration < duration:
        music = audio_loop(music, duration=duration)
    else:
        music = music.subclip(0, duration)
    if hasattr(music, "audio_fadein"):
        music = music.audio_fadein(0.4).audio_fadeout(1.2)
    extras.append(music)
    mixed = CompositeAudioClip([narration, music]).set_duration(duration)
    extras.append(mixed)
    return mixed


def _overlay_and_write(
    background,
    audio,
    duration,
    caption_chunks,
    out_path,
    part_label,
    extras_to_close,
):
    _sync_canvas()
    layers = [background, _bottom_scrim(duration)]
    if part_label:
        label_end = min(getattr(config, "PART_LABEL_DURATION", 1.4), duration)
        layers.append(_text_clip(
            part_label,
            fontsize=getattr(config, "PART_LABEL_FONTSIZE", 48),
            color="white",
            stroke_color="black",
            stroke_width=2,
            start=0,
            end=label_end,
            position=getattr(config, "PART_LABEL_POSITION", ("center", 0.055)),
            badge=True,
        ))
    for chunk in caption_chunks:
        layers.append(_make_caption_clip(chunk, (_W, _H)))

    extras_to_close = list(extras_to_close or [])
    mixed = _mix_audio(audio, duration, extras_to_close)
    final = CompositeVideoClip(layers, size=(_W, _H))
    final = final.set_duration(duration).set_audio(mixed)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        final.write_videofile(
            str(out_path),
            fps=config.FPS,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            logger="bar",
        )
    finally:
        audio.close()
        final.close()
        for clip in extras_to_close:
            try:
                clip.close()
            except Exception:
                pass
    print(f"[assemble] final video -> {out_path}")
    return out_path


def _mute(clip):
    if hasattr(clip, "without_audio"):
        return clip.without_audio()
    return clip.set_audio(None)


def _fit_vertical_video(clip):
    """Cover-crop any clip onto the current canvas (9:16, 16:9, 1:1, …)."""
    _sync_canvas()
    scale = max(_W / float(clip.w), _H / float(clip.h))
    clip = clip.resize(width=max(int(round(clip.w * scale)), _W))
    return clip.crop(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=_W,
        height=_H,
    )


def assemble(
    broll_path,
    narration_path: Path,
    caption_chunks: list,
    out_path: Path,
    part_label: str = None,
) -> Path:
    """
    Stitch real Pexels clips from assets/broll under the voiceover.
    broll_path may be one file or a list of files.
    """
    _sync_canvas()
    audio = AudioFileClip(str(narration_path))
    duration = audio.duration
    paths = list(broll_path) if isinstance(broll_path, (list, tuple)) else [broll_path]
    extras = []
    pieces = []
    for path in paths:
        clip = _mute(_fit_vertical_video(VideoFileClip(str(path))))
        extras.append(clip)
        pieces.append(clip)
    video = pieces[0] if len(pieces) == 1 else concatenate_videoclips(pieces, method="compose")
    if len(pieces) > 1:
        extras.append(video)

    if video.duration < duration:
        video = video.loop(duration=duration)
        extras.append(video)
    else:
        video = video.subclip(0, duration)
    video = _mute(video)

    return _overlay_and_write(
        video, audio, duration, caption_chunks, out_path, part_label, extras + [video]
    )


def _ease_out_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1.0 - (1.0 - p) ** 3


def _crop_letterbox(img: Image.Image, thresh: int = 16) -> Image.Image:
    arr = np.asarray(img)
    lum = arr.mean(axis=2)
    rows = np.where(lum.mean(axis=1) > thresh)[0]
    cols = np.where(lum.mean(axis=0) > thresh)[0]
    if len(rows) < 20 or len(cols) < 20:
        return img
    top, bot = int(rows[0]), int(rows[-1])
    left, right = int(cols[0]), int(cols[-1])
    bar_top = top / img.height
    bar_bot = 1.0 - bot / img.height
    if bar_top < 0.03 and bar_bot < 0.03:
        return img
    return img.crop((left, top, right + 1, bot + 1))


def _cover_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / img.width, height / img.height)
    new_w = max(int(img.width * scale), width)
    new_h = max(int(img.height * scale), height)
    img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def _grade_still(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(1.42)
    img = ImageEnhance.Contrast(img).enhance(1.28)
    img = ImageEnhance.Brightness(img).enhance(1.06)
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    return img


def _prep_source(image_path) -> np.ndarray:
    img = Image.open(str(image_path)).convert("RGB")
    img = _crop_letterbox(img)
    img = _grade_still(img)
    img = _cover_resize(img, int(_W * _SRC_SCALE), int(_H * _SRC_SCALE))
    return np.asarray(img)


def _window(src: np.ndarray, zoom: float, pan_x: float, pan_y: float, sx: float, sy: float) -> np.ndarray:
    src_h, src_w = src.shape[:2]
    zoom = min(max(zoom, 1.02), _SRC_SCALE - 0.02)
    cw = int(_W * _SRC_SCALE / zoom)
    ch = int(_H * _SRC_SCALE / zoom)
    cw = min(max(cw, 8), src_w)
    ch = min(max(ch, 8), src_h)
    max_x = src_w - cw
    max_y = src_h - ch
    x = int(max_x * pan_x + sx)
    y = int(max_y * pan_y + sy)
    x = 0 if max_x <= 0 else min(max(x, 0), max_x)
    y = 0 if max_y <= 0 else min(max(y, 0), max_y)
    crop = src[y:y + ch, x:x + cw]
    return np.asarray(
        Image.fromarray(crop).resize((_W, _H), Image.Resampling.BILINEAR)
    )


def _motion_at(t: float, duration: float, kind: int):
    """Return zoom, pan_x, pan_y for a punchy comic-camera move."""
    p = min(max(t / max(duration, 0.001), 0.0), 1.0)
    smash_t = min(t / 0.42, 1.0)
    smash = _ease_out_cubic(smash_t)
    z0 = getattr(config, "SMASH_ZOOM_START", 1.55)
    z1 = getattr(config, "SMASH_ZOOM_END", 1.12)

    if kind % 6 == 0:  # smash in from tight
        zoom = z0 + (z1 - z0) * smash
        pan_x, pan_y = 0.50 + 0.08 * p, 0.42 + 0.12 * p
    elif kind % 6 == 1:  # whip-pan right while easing out
        zoom = 1.28 - 0.10 * p
        pan_x, pan_y = 0.18 + 0.64 * _ease_out_cubic(p), 0.45
    elif kind % 6 == 2:  # smash + rise
        zoom = z0 + (z1 - z0) * smash
        pan_x, pan_y = 0.52, 0.62 - 0.28 * smash
    elif kind % 6 == 3:  # pan left, slight zoom in
        zoom = 1.18 + 0.12 * p
        pan_x, pan_y = 0.82 - 0.64 * _ease_out_cubic(p), 0.50
    elif kind % 6 == 4:  # crash zoom (keep punching in)
        zoom = 1.18 + 0.32 * _ease_out_cubic(p)
        pan_x, pan_y = 0.48 + 0.06 * p, 0.40 + 0.18 * p
    else:  # drop down onto the subject
        zoom = 1.22 + 0.10 * p
        pan_x, pan_y = 0.50, 0.22 + 0.50 * _ease_out_cubic(p)
    return zoom, pan_x, pan_y


def _shake(t: float) -> tuple:
    intensity = getattr(config, "CAMERA_SHAKE", 14)
    decay = max(0.0, 1.0 - t / 0.32)
    if decay <= 0:
        return 0.0, 0.0
    return (
        intensity * decay * math.sin(t * 58.0),
        intensity * 0.7 * decay * math.cos(t * 51.0),
    )


def _whip_shift(frame: np.ndarray, t: float, direction: int) -> np.ndarray:
    whip = getattr(config, "WHIP_DURATION", 0.16)
    if t >= whip or direction == 0:
        return frame
    p = 1.0 - _ease_out_cubic(t / whip)
    dx = int(direction * p * _W)
    if dx == 0:
        return frame
    canvas = np.zeros_like(frame)
    if dx > 0:
        canvas[:, dx:] = frame[:, : _W - dx]
    else:
        canvas[:, : _W + dx] = frame[:, -dx:]
    return canvas


def _comic_panel_clip(image_path, duration: float, kind: int, whip_dir: int) -> VideoClip:
    src = _prep_source(image_path)
    duration = max(float(duration), 0.25)
    grain = [
        np.random.RandomState(i).randint(0, 16, (_H, _W, 1), dtype=np.int16) - 8
        for i in range(4)
    ]

    def make_frame(t):
        zoom, pan_x, pan_y = _motion_at(t, duration, kind)
        sx, sy = _shake(t)
        frame = _window(src, zoom, pan_x, pan_y, sx, sy)
        frame = _whip_shift(frame, t, whip_dir)
        g = grain[int(t * 24) % 4]
        return np.clip(frame.astype(np.int16) + g, 0, 255).astype(np.uint8)

    return VideoClip(make_frame, duration=duration).set_fps(config.FPS)


def _impact_flash(start: float, warm: bool = False) -> ColorClip:
    color = (255, 210, 190) if warm else (255, 255, 255)
    dur = getattr(config, "IMPACT_FLASH", 0.08)
    return (
        ColorClip(size=(_W, _H), color=color)
        .set_start(start)
        .set_duration(dur)
        .set_opacity(0.62)
    )


def _speed_lines_clip(start: float) -> VideoClip:
    rng = np.random.RandomState(7)
    img = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = _W // 2, _H // 2
    for _ in range(90):
        ang = rng.random() * math.tau
        inner = 180 + rng.random() * 220
        outer = 980 + rng.random() * 420
        x0 = cx + math.cos(ang) * inner
        y0 = cy + math.sin(ang) * inner
        x1 = cx + math.cos(ang) * outer
        y1 = cy + math.sin(ang) * outer
        w = 2 + int(rng.random() * 4)
        a = 70 + int(rng.random() * 80)
        draw.line([(x0, y0), (x1, y1)], fill=(255, 255, 255, a), width=w)
    arr = np.array(img)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3].astype(np.float32) / 255.0
    dur = 0.38

    def make_frame(t):
        return rgb

    def make_mask(t):
        fade = max(0.0, 1.0 - t / dur) * 0.55
        return alpha * fade

    clip = VideoClip(make_frame, duration=dur).set_start(start)
    clip.mask = VideoClip(make_mask, ismask=True, duration=dur).set_start(start)
    return clip


def _ken_burns_still(image_path, duration, zoom_in=True):
    """Legacy single-still path — still used by assemble_from_image."""
    return _comic_panel_clip(image_path, duration, kind=0 if zoom_in else 4, whip_dir=0)


def assemble_from_image(
    image_path: Path,
    narration_path: Path,
    caption_chunks: list,
    out_path: Path,
    part_label: str = None,
) -> Path:
    return assemble_from_images(
        [{"path": image_path, "start": 0, "end": None}],
        narration_path,
        caption_chunks,
        out_path,
        part_label,
    )


def assemble_from_images(
    panels: list,
    narration_path: Path,
    caption_chunks: list,
    out_path: Path,
    part_label: str = None,
) -> Path:
    """
    Comic animatic: smash-zoom + pan + shake per panel, whip-in and impact
    flash on every cut, timed to the narration.
    """
    _sync_canvas()
    audio = AudioFileClip(str(narration_path))
    duration = max(audio.duration, 0.1)
    fade = getattr(config, "COMIC_CROSSFADE", 0.0)

    motion = []
    extras = []
    fx = []
    for i, panel in enumerate(panels):
        start = float(panel.get("start") or 0.0)
        end = panel.get("end")
        end = duration if end is None else float(end)
        hold = max(end - start, 0.35)
        whip_dir = 0 if i == 0 else (1 if i % 2 else -1)
        clip = _comic_panel_clip(panel["path"], hold + fade, kind=i, whip_dir=whip_dir)
        clip = clip.set_start(max(0.0, start - (fade if i else 0.0)))
        if i and fade:
            clip = clip.crossfadein(fade)
        motion.append(clip)
        extras.append(clip)
        fx.append(_impact_flash(start, warm=(i % 2 == 1)))
        if i:
            fx.append(_speed_lines_clip(start))

    background = CompositeVideoClip(
        motion + fx, size=(_W, _H)
    ).set_duration(duration).set_audio(None)

    return _overlay_and_write(
        background, audio, duration, caption_chunks, out_path, part_label, extras + [background]
    )


def assemble_from_videos(
    clips: list,
    narration_path: Path,
    caption_chunks: list,
    out_path: Path,
    part_label: str = None,
) -> Path:
    """
    Real motion clips (AI video or stock), timed to the narration, plus captions.
    Each item is {path, start, end}. Short clips loop to fill their beat.
    """
    _sync_canvas()
    audio = AudioFileClip(str(narration_path))
    duration = max(audio.duration, 0.1)
    layers = []
    extras = []
    for item in clips:
        start = float(item.get("start") or 0.0)
        end = item.get("end")
        end = duration if end is None else float(end)
        hold = max(end - start, 0.35)
        video = VideoFileClip(str(item["path"]))
        video = _fit_vertical_video(video)
        if video.duration < hold:
            video = video.loop(duration=hold)
        else:
            video = video.subclip(0, min(hold, video.duration))
        video = video.set_start(start).set_duration(hold).set_audio(None)
        layers.append(video)
        extras.append(video)

    background = CompositeVideoClip(
        layers, size=(_W, _H)
    ).set_duration(duration).set_audio(None)

    return _overlay_and_write(
        background, audio, duration, caption_chunks, out_path, part_label, extras + [background]
    )

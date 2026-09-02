"""
End-to-end pipeline runner.

For each part of a script:
  1. Generate narration (Coqui TTS, local, free)
  2. Transcribe it for word-level captions (Whisper, local, free)
  3. Build background (Pexels b-roll, illustrated stills, or paid AI video)
  4. Assemble the final vertical video (moviepy)

Usage:
    python main.py scripts/popobawa.py
    python main.py scripts/popobawa.py model=live
    python main.py scripts/popobawa.py model=comic image_model=flux-anime
    python main.py scripts/popobawa.py model=live music=off
    python main.py scripts/popobawa.py music=horror_piano
"""

import os
import sys

# OpenBLAS on Apple Silicon can SIGSEGV (exit -11) during import if it
# spawns many gemm threads. Pin this before numpy / TTS / Whisper load.
for _k, _v in (
    ("OPENBLAS_NUM_THREADS", "1"),
    ("OMP_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("VECLIB_MAXIMUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("TOKENIZERS_PARALLELISM", "false"),
):
    os.environ[_k] = _v

print("[run] starting pipeline", flush=True)

import importlib.util
import json
from pathlib import Path

print("[run] loading config", flush=True)
import config
print("[run] loading tts", flush=True)
from tts_engine import narrate
print("[run] loading captions", flush=True)
from captions_engine import transcribe_with_word_timestamps, captions_from_script
print("[run] loading broll", flush=True)
from broll_engine import fetch_story_broll
print("[run] loading images", flush=True)
from image_engine import generate_storyboard, visual_beat_times
from video_engine import generate_video_storyboard
from visuals_engine import normalize_video_type, trim_wav_to, wav_duration
from assemble_video import assemble_from_images, assemble_from_videos
print("[run] libraries ready", flush=True)


def load_script_module(path: str):
    spec = importlib.util.spec_from_file_location("script_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words_for(audio_path: Path) -> list:
    cache = config.CAPTIONS_DIR / f"{audio_path.stem}_words.json"
    if cache.exists() and cache.stat().st_mtime >= audio_path.stat().st_mtime:
        print(f"[captions] reusing {cache.name}")
        return json.loads(cache.read_text())
    words = transcribe_with_word_timestamps(audio_path)
    cache.write_text(json.dumps(words))
    return words


def _clip_stem(series_name: str, index: int, total: int) -> str:
    if total == 1:
        return series_name
    return f"{series_name}_part{index}"


_MUSIC_OFF = {"0", "false", "off", "no", "none"}
_MUSIC_RANDOM = {"1", "true", "on", "yes", "random", ""}


def _apply_music_choice(value: str) -> None:
    low = str(value).strip().lower()
    if low in _MUSIC_OFF:
        config.MUSIC_ENABLED = False
        config.MUSIC_TRACK = ""
        return
    config.MUSIC_ENABLED = True
    config.MUSIC_TRACK = "" if low in _MUSIC_RANDOM else value.strip()


def parse_cli(argv):
    """
    python main.py scripts/foo.py [series_name] model=live image_model=flux music=off
    Values with '=' override config. A bare token is the series name.
    """
    if len(argv) < 2:
        print(
            "Usage: python main.py scripts/popobawa.py [series_name] "
            "model=live|comic|cartoon|anime|ai_video "
            "[image_model=flux] [video_model=wan-fast] "
            "[music=on|off|random|<track name>] [size=9:16|1:1|4:5|16:9|4:3] "
            "[max=180|0]"
        )
        sys.exit(1)

    script_path = argv[1]
    series_name = Path(script_path).stem
    overrides = {}
    for arg in argv[2:]:
        if "=" not in arg:
            series_name = arg
            continue
        key, value = arg.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if key in {"model", "video_type", "type"}:
            overrides["video_type"] = normalize_video_type(value)
        elif key in {"image_model", "img_model"}:
            config.IMAGE_MODEL = value
            overrides["image_model"] = value
        elif key in {"video_model", "ai_video_model"}:
            config.AI_VIDEO_MODEL = value
            overrides["video_model"] = value
        elif key in {"music", "music_track"}:
            _apply_music_choice(value)
            overrides["music"] = value
        elif key in {"size", "aspect", "aspect_ratio"}:
            w, h = config.apply_aspect_ratio(value)
            overrides["size"] = value
            print("[run] canvas {0}x{1} ({2})".format(w, h, value))
        elif key in {"max", "length", "duration", "seconds", "max_seconds"}:
            seconds = config.apply_max_seconds(value)
            overrides["max_seconds"] = seconds
            if seconds:
                print("[run] max length {0}s".format(seconds))
            else:
                print("[run] max length off (follow the story)")
        elif key == "music_volume":
            config.MUSIC_VOLUME = float(value)
        else:
            print(f"[cli] ignoring unknown option {arg}")
    return script_path, series_name, overrides


def _audio_is_current(audio_path: Path, text: str) -> bool:
    sidecar = audio_path.with_suffix(".script.txt")
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        return False
    if not sidecar.exists():
        return False
    return sidecar.read_text() == text


def run_pipeline(script_path: str, series_name: str = "series", overrides=None):
    overrides = overrides or {}
    module = load_script_module(script_path)
    parts = module.PARTS
    series_type = overrides.get("video_type") or getattr(module, "VIDEO_TYPE", config.VIDEO_TYPE)
    if "music" not in overrides:
        script_music = getattr(module, "BACKGROUND_MUSIC", None) or getattr(module, "MUSIC_TRACK", None)
        if script_music:
            _apply_music_choice(str(script_music))
    character_lock = getattr(module, "CHARACTER_LOCK", "")
    character_seed = getattr(module, "CHARACTER_SEED", config.CHARACTER_SEED)
    shorts_max = overrides.get("max_seconds")
    if shorts_max is None:
        shorts_max = getattr(config, "SHORTS_MAX_SECONDS", 180)
    shorts_max = int(shorts_max or 0)
    single = len(parts) == 1
    print(f"[run] video_type={normalize_video_type(series_type)}")

    for i, part in enumerate(parts, start=1):
        stem = _clip_stem(series_name, i, len(parts))
        label = None if single else part.get("label")
        title = series_name if single else f"{series_name} - {part.get('label', f'part {i}')}"
        print(f"\n=== {title} ===")

        # 1. narration — regenerate if the script text changed
        audio_path = config.AUDIO_DIR / f"{stem}.wav"
        if _audio_is_current(audio_path, part["text"]):
            print(f"[tts] reusing {audio_path.name}")
        else:
            narrate(part["text"], audio_path)
            audio_path.with_suffix(".script.txt").write_text(part["text"])

        duration = wav_duration(audio_path)
        if shorts_max:
            print(f"[tts] duration {duration:.1f}s (cap {shorts_max}s)")
            if duration > shorts_max:
                duration = trim_wav_to(audio_path, shorts_max)
                print(f"[tts] trimmed narration to {duration:.1f}s")
                cache = config.CAPTIONS_DIR / f"{audio_path.stem}_words.json"
                if cache.exists():
                    kept = []
                    for word in json.loads(cache.read_text()):
                        if float(word.get("start") or 0) >= duration:
                            continue
                        word["end"] = min(float(word.get("end") or duration), duration)
                        kept.append(word)
                    cache.write_text(json.dumps(kept))
        else:
            print(f"[tts] duration {duration:.1f}s (no length cap)")

        # 2. captions from the written story, timed to the full voiceover
        words = _words_for(audio_path)
        chunks = captions_from_script(part["text"], duration, words, words_per_chunk=2)

        # 3–4. visuals + assembly
        out_path = config.OUTPUT_DIR / f"{stem}.mp4"
        video_type = normalize_video_type(
            overrides.get("video_type") or part.get("video_type") or series_type
        )

        if video_type == "live":
            clip_paths = fetch_story_broll(
                part,
                config.BROLL_DIR,
                stem,
                duration,
            )
            if not clip_paths:
                raise RuntimeError(
                    f"No Pexels clips found. Check PEXELS_API_KEY and broll_query for {stem}"
                )
            times = visual_beat_times(part["text"], len(clip_paths), words, duration)
            timed_clips = [
                {"path": path, "start": start, "end": end}
                for path, (start, end) in zip(clip_paths, times)
            ]
            assemble_from_videos(
                clips=timed_clips,
                narration_path=audio_path,
                caption_chunks=chunks,
                out_path=out_path,
                part_label=label,
            )
        elif video_type == "ai_video":
            clips = generate_video_storyboard(
                part=part,
                stills_dir=config.AI_IMAGE_DIR / stem,
                clips_dir=config.AI_VIDEO_DIR / stem,
                duration=duration,
                words=words,
                style="comic",
                character_lock=character_lock,
                character_seed=character_seed,
            )
            assemble_from_videos(
                clips=clips,
                narration_path=audio_path,
                caption_chunks=chunks,
                out_path=out_path,
                part_label=label,
            )
        else:
            story_dir = config.AI_IMAGE_DIR / stem
            panels = generate_storyboard(
                part=part,
                out_dir=story_dir,
                duration=duration,
                words=words,
                style=video_type,
                character_lock=character_lock,
                character_seed=character_seed,
            )
            assemble_from_images(
                panels=panels,
                narration_path=audio_path,
                caption_chunks=chunks,
                out_path=out_path,
                part_label=label,
            )

    print(f"\nDone. {len(parts)} video(s) in {config.OUTPUT_DIR}/")


if __name__ == "__main__":
    script_arg, name_arg, cli_overrides = parse_cli(sys.argv)
    run_pipeline(script_arg, name_arg, cli_overrides)

"""
Local, free auto-captioning using OpenAI's Whisper (open-source, self-hosted).
No API key. Runs entirely on your machine.

Produces word-level timestamps so captions can be burned in
one-or-two-words-at-a-time (the fast-paced caption style most
horror/faceless reels use for retention).
"""

from __future__ import annotations

from pathlib import Path
import re
import whisper
import config

_model = None


def _get_model():
    global _model
    if _model is None:
        print(f"[captions] loading whisper model '{config.WHISPER_MODEL}' (first run downloads it)...")
        _model = whisper.load_model(config.WHISPER_MODEL)
    return _model


def transcribe_with_word_timestamps(audio_path: Path) -> list[dict]:
    """
    Returns a flat list of word-level caption chunks:
    [{"word": "In", "start": 0.0, "end": 0.32}, ...]
    """
    model = _get_model()
    result = model.transcribe(str(audio_path), word_timestamps=True)

    words = []
    for segment in result["segments"]:
        for w in segment.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })
    print(f"[captions] transcribed {len(words)} words from {audio_path.name}")
    return words


def script_words(text: str) -> list:
    return re.findall(r"[A-Za-z0-9']+|[0-9]+", text or "")


def captions_from_script(
    text: str,
    duration: float,
    whisper_words: list = None,
    words_per_chunk: int = 2,
) -> list:
    """
    Burn the written script on screen, timed to the voiceover.

    Whisper is only used for timing. Using Whisper's guessed words put
    'Papa Ball' on screen and dropped the ending when TTS/Whisper truncated.
    """
    from tts_engine import for_speech
    tokens = script_words(for_speech(text))
    duration = max(float(duration), 0.1)
    if not tokens:
        return group_words_for_display(whisper_words or [], words_per_chunk)

    n = len(tokens)
    whisper_words = whisper_words or []
    timed = []
    close = bool(
        whisper_words
        and abs(len(whisper_words) - n) <= max(6, int(n * 0.12))
    )
    for i, token in enumerate(tokens):
        if close and i < len(whisper_words):
            start = float(whisper_words[i]["start"])
            end = float(whisper_words[i]["end"])
        else:
            start = duration * i / n
            end = duration * (i + 1) / n
        timed.append({"word": token, "start": start, "end": max(end, start + 0.05)})

    timed[0]["start"] = 0.0
    timed[-1]["end"] = duration
    for i in range(1, n):
        if timed[i]["start"] < timed[i - 1]["end"]:
            mid = (timed[i - 1]["start"] + timed[i]["end"]) / 2.0
            timed[i - 1]["end"] = mid
            timed[i]["start"] = mid
    print(f"[captions] {n} script words covering 0.0–{duration:.1f}s")
    return group_words_for_display(timed, words_per_chunk)


def group_words_for_display(words: list, words_per_chunk: int = 2) -> list:
    """
    Groups single words into small on-screen chunks (default: 2 words at a time),
    which is the fast, punchy caption style that performs well on Shorts.
    """
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        group = words[i:i + words_per_chunk]
        chunks.append({
            "text": " ".join(w["word"] for w in group),
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })
    return chunks


if __name__ == "__main__":
    test_audio = config.AUDIO_DIR / "test_part1.wav"
    if test_audio.exists():
        words = transcribe_with_word_timestamps(test_audio)
        for c in group_words_for_display(words):
            print(f"{c['start']:.2f}-{c['end']:.2f}: {c['text']}")
    else:
        print("Run tts_engine.py first to generate test audio.")

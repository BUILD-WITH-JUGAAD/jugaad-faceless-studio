"""
Local, free text-to-speech using Coqui TTS.
No API key. No usage cap. Runs on CPU (slower) or GPU (fast) on your own machine.

VITS silently truncates long paragraphs, so we speak in short chunks and
stitch the wavs. That keeps the full story on the audio timeline.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

from TTS.api import TTS
import config

_tts_instance = None
# VITS decoder drops the tail of long strings. Stay well under that limit.
_MAX_CHUNK_CHARS = 180
_GAP_MS = 220


def _get_tts():
    """Lazy-load the model once and reuse it across calls (loading is the slow part)."""
    global _tts_instance
    if _tts_instance is None:
        print(f"[tts] loading model {config.TTS_MODEL} (first run downloads it)...")
        _tts_instance = TTS(model_name=config.TTS_MODEL)
    return _tts_instance


def split_narration(text: str, max_chars: int = None) -> list:
    """Split on sentence boundaries, then pack until max_chars."""
    max_chars = int(max_chars or _MAX_CHUNK_CHARS)
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    buf = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long_sentence(sentence, max_chars))
            continue
        trial = f"{buf} {sentence}".strip() if buf else sentence
        if len(trial) > max_chars and buf:
            chunks.append(buf)
            buf = sentence
        else:
            buf = trial
    if buf:
        chunks.append(buf)
    return chunks


def _split_long_sentence(sentence: str, max_chars: int) -> list:
    words = sentence.split()
    pieces, buf = [], ""
    for word in words:
        trial = f"{buf} {word}".strip() if buf else word
        if len(trial) > max_chars and buf:
            pieces.append(buf)
            buf = word
        else:
            buf = trial
    if buf:
        pieces.append(buf)
    return pieces


def _concat_wavs(paths: list, out_path: Path, gap_ms: int = _GAP_MS) -> None:
    params = None
    frames = []
    for i, path in enumerate(paths):
        with wave.open(str(path), "rb") as src:
            if params is None:
                params = src.getparams()
            elif src.getparams()[:3] != params[:3]:
                raise RuntimeError(f"WAV format mismatch in {path}")
            frames.append(src.readframes(src.getnframes()))
        if i < len(paths) - 1 and gap_ms > 0:
            n = int(params.framerate * gap_ms / 1000.0)
            frames.append(b"\x00" * (n * params.sampwidth * params.nchannels))
    with wave.open(str(out_path), "wb") as dest:
        dest.setparams(params)
        dest.writeframes(b"".join(frames))


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as src:
        return src.getnframes() / float(src.getframerate())


def narrate(text: str, out_path: Path, speaker: str = None) -> Path:
    """
    Convert text to a narrated .wav file.

    text: the script line(s) to speak
    out_path: where to save the audio, e.g. assets/audio/part1.wav
    speaker: override the default speaker/voice id (see config.TTS_SPEAKER)
    """
    tts = _get_tts()
    speaker = speaker or config.TTS_SPEAKER

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = split_narration(text)
    if not chunks:
        raise ValueError("narrate() got empty text")

    if len(chunks) == 1:
        tts.tts_to_file(text=chunks[0], speaker=speaker, file_path=str(out_path))
        print(f"[tts] saved narration -> {out_path} ({_wav_seconds(out_path):.1f}s)")
        return out_path

    piece_paths = []
    for i, chunk in enumerate(chunks, start=1):
        piece = out_path.parent / f"{out_path.stem}_chunk{i:02d}.wav"
        print(f"[tts] chunk {i}/{len(chunks)} ({len(chunk)} chars)")
        tts.tts_to_file(text=chunk, speaker=speaker, file_path=str(piece))
        piece_paths.append(piece)

    _concat_wavs(piece_paths, out_path)
    for piece in piece_paths:
        try:
            piece.unlink()
        except OSError:
            pass
    seconds = _wav_seconds(out_path)
    words = len(text.split())
    print(f"[tts] saved narration -> {out_path} ({seconds:.1f}s, {words} words, {len(chunks)} chunks)")
    return out_path


if __name__ == "__main__":
    sample = (
        "In 1995, families across Zanzibar started sleeping outside — together, "
        "in groups, lights on, doors open."
    )
    narrate(sample, config.AUDIO_DIR / "test_part1.wav")

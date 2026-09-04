"""
Local, free text-to-speech using Coqui TTS.
No API key. No usage cap. Runs on CPU (slower) or GPU (fast) on your own machine.

VITS silently truncates long paragraphs, so we speak in short chunks and
stitch the wavs. That keeps the full story on the audio timeline.
"""

from __future__ import annotations

import ast
import re
import wave
from pathlib import Path

import config
from script_text import parse_script

_tts_instance = None
# VITS decoder drops the tail of long strings. Stay well under that limit.
_MAX_CHUNK_CHARS = 180
_GAP_MS = 220
_VCTK_SPEAKERS = frozenset({
    "p225", "p226", "p227", "p228", "p229", "p230", "p231", "p232", "p233",
    "p234", "p236", "p237", "p238", "p239", "p240", "p241", "p243", "p244",
    "p245", "p246", "p247", "p248", "p249", "p250", "p251", "p252", "p253",
    "p254", "p255", "p256", "p257", "p258", "p259", "p260", "p261", "p262",
    "p263", "p264", "p265", "p266", "p267", "p268", "p269", "p270", "p271",
    "p272", "p273", "p274", "p275", "p276", "p277", "p278", "p279", "p280",
    "p281", "p282", "p283", "p284", "p285", "p286", "p287", "p288", "p292",
    "p293", "p294", "p295", "p297", "p298", "p299", "p300", "p301", "p302",
    "p303", "p304", "p305", "p306", "p307", "p308", "p310", "p311", "p312",
    "p313", "p314", "p316", "p317", "p318", "p323", "p326", "p329", "p330",
    "p333", "p334", "p335", "p336", "p339", "p340", "p341", "p343", "p345",
    "p347", "p351", "p360", "p361", "p362", "p363", "p364", "p374", "p376",
})


def list_voices() -> list:
    """Voice cards. Re-reads config.py so label edits show without a full restart."""
    voices = _voices_from_disk()
    if voices:
        return voices
    return [dict(item) for item in getattr(config, "TTS_VOICES", ())]


def _voices_from_disk() -> list:
    path = Path(__file__).resolve().parent / "config.py"
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "TTS_VOICES" not in names:
            continue
        try:
            raw = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return []
        return [dict(item) for item in raw]
    return []


def resolve_voice(value: str = None) -> str:
    """Map a studio/CLI choice to a VCTK speaker id."""
    fallback = (getattr(config, "TTS_SPEAKER", "") or "p326").strip() or "p326"
    raw = (value or "").strip() or fallback
    low = raw.lower()
    for voice in list_voices():
        if (voice.get("id") or "").lower() == low or (voice.get("label") or "").lower() == low:
            return voice["id"]
    if low in _VCTK_SPEAKERS:
        return low
    print("[tts] unknown voice {0}, using {1}".format(raw, fallback))
    return fallback if fallback in _VCTK_SPEAKERS else "p326"


def _get_tts():
    """Lazy-load the model once and reuse it across calls (loading is the slow part)."""
    global _tts_instance
    if _tts_instance is None:
        from TTS.api import TTS
        print(f"[tts] loading model {config.TTS_MODEL} (first run downloads it)...")
        _tts_instance = TTS(model_name=config.TTS_MODEL)
    return _tts_instance


def for_speech(text: str) -> str:
    """Spoken words only. Drops stage cues and marks VITS reads as 'asterisk'."""
    raw = parse_script(text)["spoken"] or (text or "")
    raw = (
        raw.replace("\u201c", '"').replace("\u201d", '"')
        .replace("\u00ab", '"').replace("\u00bb", '"')
        .replace("\u201e", '"').replace("\u201f", '"')
        .replace("\u2018", "'").replace("\u2019", "'")
        .replace("\u300c", "").replace("\u300d", "")
        .replace("\u300e", "").replace("\u300f", "")
    )
    raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
    raw = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", raw)
    raw = re.sub(r'"([^"\n]*)"', r"\1", raw)
    raw = raw.replace("*", " ").replace('"', "")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r" *\n *", "\n", raw)
    return raw.strip()


def split_narration(text: str, max_chars: int = None) -> list:
    """Split on sentence boundaries, then pack until max_chars."""
    max_chars = int(max_chars or _MAX_CHUNK_CHARS)
    text = re.sub(r"\s+", " ", for_speech(text))
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


def _speech_jobs(text: str) -> list:
    """('speech', chunk) and ('silence', ms) in script order."""
    pack = parse_script(text)
    jobs = []
    for seg in pack["segments"]:
        if seg["kind"] == "speech":
            for chunk in split_narration(seg["text"]):
                jobs.append(("speech", chunk))
        elif seg["kind"] == "silence":
            jobs.append(("silence", int(seg.get("ms") or 1200)))
        elif seg["kind"] == "sfx":
            jobs.append(("silence", 550))
    if jobs:
        return jobs
    return [("speech", chunk) for chunk in split_narration(text)]


def _write_silence(path: Path, params, ms: int) -> None:
    n = max(1, int(params.framerate * max(int(ms), 1) / 1000.0))
    frames = b"\x00" * (n * params.sampwidth * params.nchannels)
    with wave.open(str(path), "wb") as dest:
        dest.setparams(params)
        dest.writeframes(frames)


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
    speaker = resolve_voice(speaker or config.TTS_SPEAKER)
    print("[tts] voice {0}".format(speaker))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    jobs = _speech_jobs(text)
    if not jobs:
        raise ValueError("narrate() got empty text")

    speech_jobs = [j for j in jobs if j[0] == "speech"]
    if len(jobs) == 1 and jobs[0][0] == "speech":
        tts.tts_to_file(text=jobs[0][1], speaker=speaker, file_path=str(out_path))
        print(f"[tts] saved narration -> {out_path} ({_wav_seconds(out_path):.1f}s)")
        return out_path

    speech_files = {}
    params = None
    speech_n = 0
    for i, (kind, payload) in enumerate(jobs, start=1):
        if kind != "speech":
            continue
        piece = out_path.parent / f"{out_path.stem}_chunk{i:02d}.wav"
        speech_n += 1
        print(f"[tts] chunk {speech_n}/{len(speech_jobs)} ({len(payload)} chars)")
        tts.tts_to_file(text=payload, speaker=speaker, file_path=str(piece))
        speech_files[i] = piece
        if params is None:
            with wave.open(str(piece), "rb") as src:
                params = src.getparams()

    piece_paths = []
    for i, (kind, payload) in enumerate(jobs, start=1):
        if kind == "speech":
            piece_paths.append(speech_files[i])
            continue
        if params is None:
            continue
        piece = out_path.parent / f"{out_path.stem}_chunk{i:02d}.wav"
        _write_silence(piece, params, int(payload))
        print(f"[tts] pause {int(payload)}ms")
        piece_paths.append(piece)

    if not piece_paths:
        raise ValueError("narrate() got empty text")
    if len(piece_paths) == 1:
        Path(piece_paths[0]).replace(out_path)
        print(f"[tts] saved narration -> {out_path} ({_wav_seconds(out_path):.1f}s)")
        return out_path

    _concat_wavs(piece_paths, out_path, gap_ms=0)
    for piece in piece_paths:
        try:
            piece.unlink()
        except OSError:
            pass
    seconds = _wav_seconds(out_path)
    words = len(for_speech(text).split())
    print(f"[tts] saved narration -> {out_path} ({seconds:.1f}s, {words} words, {len(speech_jobs)} chunks)")
    return out_path


VOICE_PREVIEW_TEXT = (
    "The house went quiet. Then I heard it. A whisper, right behind me."
)


def voice_preview_dir() -> Path:
    folder = config.AUDIO_DIR / "voice_previews"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def voice_preview_path(speaker: str) -> Path:
    return voice_preview_dir() / "{0}.wav".format(resolve_voice(speaker))


def ensure_voice_preview(speaker: str) -> Path:
    """Build a short cached sample for the studio Voice picker."""
    speaker = resolve_voice(speaker)
    path = voice_preview_path(speaker)
    if path.exists() and path.stat().st_size > 2000:
        return path
    tmp = path.with_suffix(".tmp.wav")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    narrate(VOICE_PREVIEW_TEXT, tmp, speaker=speaker)
    tmp.replace(path)
    return path


if __name__ == "__main__":
    sample = (
        "In 1995, families across Zanzibar started sleeping outside — together, "
        "in groups, lights on, doors open."
    )
    narrate(sample, config.AUDIO_DIR / "test_part1.wav")

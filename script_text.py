"""
Split a pasted script into spoken narration vs stage cues.

ChatGPT often writes:

    (A brief silence, then a child's giggle)
    Narrator: The next morning the boy was gone.

Parentheticals and speaker labels are not spoken and must not be captioned.
Silence / SFX / music cues are returned so the pipeline can pause or overlay.
"""

from __future__ import annotations

import re

_SPEAKER = re.compile(
    r"^(?:Narrator|Narration|VO|V\.O\.|Voice[- ]?over|Host|Announcer|Speaker)\s*:\s*",
    re.I,
)
_CUE_LINE = re.compile(r"^\s*[\(\[](.+)[\)\]]\s*$")
_INLINE_CUE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

_SILENCE = re.compile(
    r"\b(silence|silent|pause|a beat|room tone|holds?)\b",
    re.I,
)
_MUSIC = re.compile(
    r"\b(music|tempo|underscore|score|sting|suspenseful|swell)\b",
    re.I,
)
_SFX_HINT = re.compile(
    r"\b(sfx|sound effect|giggle|laugh|teke|whisper|scream|shriek|creak|"
    r"knock|footstep|thunder|heartbeat|whoosh|scrape|howl|cry)\b",
    re.I,
)
_STAGE = re.compile(
    r"\b(increases|decreases|becomes|followed by|drawn-out|unsettling|"
    r"fades? to|slightly)\b",
    re.I,
)
_LABELED = re.compile(r"^(sfx|sound|music|audio|fx|note)\s*:", re.I)

_SFX_RULES = (
    (r"giggle|child(?:'s)? laugh|children laugh", "child giggle"),
    (r"\blaugh", "laugh"),
    (r"teke|scrape|dragging|metal drag", "metal scrape"),
    (r"whisper", "whisper"),
    (r"scream|shriek", "scream"),
    (r"knock", "knock door"),
    (r"footstep", "footsteps night"),
    (r"creak", "wood creak"),
    (r"heartbeat", "heartbeat"),
    (r"thunder", "thunder"),
    (r"howl|wind", "wind night"),
    (r"cry|crying", "child crying"),
)


def spoken_text(text: str) -> str:
    return parse_script(text)["spoken"]


def parse_script(text: str) -> dict:
    """Return spoken narration plus ordered segments and cues."""
    segments = []
    spoken_bits = []

    def add_speech(raw: str) -> None:
        cleaned = _SPEAKER.sub("", (raw or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            segments.append({"kind": "speech", "text": cleaned, "query": "", "ms": 0})
            spoken_bits.append(cleaned)

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        whole = _CUE_LINE.match(line)
        if whole and _is_cue_text(whole.group(1)):
            segments.extend(_cues_from_inner(whole.group(1)))
            continue
        line = _SPEAKER.sub("", line)
        parts = re.split(r"([\[\(][^\]\)]+[\]\)])", line)
        buf = []
        for part in parts:
            inner = _INLINE_CUE.match(part.strip())
            if inner and _is_cue_text(inner.group(1)):
                if buf:
                    add_speech(" ".join(buf))
                    buf = []
                segments.extend(_cues_from_inner(inner.group(1)))
            elif part.strip():
                buf.append(part.strip())
        if buf:
            add_speech(" ".join(buf))

    spoken = re.sub(r"\s+", " ", " ".join(spoken_bits)).strip()
    cues = [s for s in segments if s["kind"] != "speech"]
    return {"spoken": spoken, "segments": segments, "cues": cues}


def _is_cue_text(inner: str) -> bool:
    inner = (inner or "").strip()
    if not inner:
        return False
    if _LABELED.match(inner):
        return True
    return bool(
        _SILENCE.search(inner)
        or _MUSIC.search(inner)
        or _SFX_HINT.search(inner)
        or _STAGE.search(inner)
    )


def _cues_from_inner(inner: str) -> list:
    inner = (inner or "").strip()
    out = []
    if _SILENCE.search(inner):
        ms = 1200
        if re.search(r"brief|short|a beat", inner, re.I):
            ms = 900
        elif re.search(r"long|drawn|final", inner, re.I):
            ms = 1600
        out.append({"kind": "silence", "text": inner, "query": "", "ms": ms})
    queries = []
    for pattern, query in _SFX_RULES:
        if re.search(pattern, inner, re.I) and query not in queries:
            queries.append(query)
    for query in queries:
        out.append({"kind": "sfx", "text": inner, "query": query, "ms": 0})
    if _MUSIC.search(inner) and not queries:
        out.append({"kind": "music", "text": inner, "query": "suspense", "ms": 0})
    if not out:
        out.append({"kind": "music", "text": inner, "query": "", "ms": 0})
    return out

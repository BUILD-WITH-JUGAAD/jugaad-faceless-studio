"""
Shared VisualBeat → scene text for generative / illustrated adapters (Phase 7).

Stock adapters (Phase 6) keep using search queries; this module is for
AI/comic/Pixazo/AI-video prompt construction only.
"""

from __future__ import annotations

from dataclasses import replace

from visual_beat import VisualBeat


def scene_text_from_beat(beat: VisualBeat, setting: str = "") -> str:
    """
    Build a generation-oriented scene description from one VisualBeat.

    Prefer explicit leading queries (from image_prompts / broll_queries),
    then visual_intent, then composed attributes, then narration.
    Never returns a bare style tag like "dark horror".
    """
    primary = (beat.queries[0] if beat.queries else "").strip()
    intent = (beat.visual_intent or "").strip()
    narration = (beat.narration or "").strip()

    if primary and len(primary.split()) >= 5:
        text = primary
    elif intent and len(intent.split()) >= 4:
        text = intent
    elif primary:
        text = primary
    elif intent:
        text = intent
    else:
        bits = []
        for xs in (beat.subject, beat.action, beat.objects, beat.location):
            bits.extend([str(x).strip() for x in (xs or []) if str(x).strip()])
        if beat.time_context:
            bits.append(str(beat.time_context).strip())
        if beat.mood:
            bits.append(str(beat.mood).strip())
        text = " ".join(bits).strip() or narration[:160]

    setting = (setting or "").strip()
    if setting and setting.lower() not in text.lower() and len(text.split()) < 10:
        text = "{0}, {1}".format(text, setting).strip(", ")

    return re_space(text)[:220]


def re_space(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def allocate_budgeted_beats(beats: list, max_count: int, duration: float = None) -> list:
    """
    Cap VisualBeats to a provider budget without inventing fake density.

    Evenly samples beats when over budget, then retimes them to cover
    [0, duration] so the renderer still gets full timeline coverage.
    """
    beats = list(beats or [])
    if not beats:
        return []
    max_count = max(1, int(max_count or 1))
    duration = float(
        duration
        if duration is not None
        else (beats[-1].end if beats else 1.0)
    )
    duration = max(duration, 0.1)

    if len(beats) <= max_count:
        selected = beats
    else:
        n = max_count
        if n == 1:
            idxs = [0]
        else:
            idxs = [
                int(round(i * (len(beats) - 1) / float(n - 1)))
                for i in range(n)
            ]
        # de-dupe while preserving order
        seen = set()
        ordered = []
        for i in idxs:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        while len(ordered) < n:
            for i in range(len(beats)):
                if i not in seen:
                    seen.add(i)
                    ordered.append(i)
                if len(ordered) >= n:
                    break
        selected = [beats[i] for i in ordered[:n]]

    n = len(selected)
    out = []
    for i, beat in enumerate(selected):
        start = 0.0 if i == 0 else duration * i / float(n)
        end = duration if i == n - 1 else duration * (i + 1) / float(n)
        if end <= start:
            end = min(duration, start + max(0.05, duration / float(n)))
        out.append(
            replace(
                beat,
                start=float(start),
                end=float(end),
                duration=float(end) - float(start),
            )
        )
    if out:
        out[0] = replace(
            out[0],
            start=0.0,
            duration=float(out[0].end) - 0.0,
        )
        last_start = float(out[-1].start)
        out[-1] = replace(
            out[-1],
            end=duration,
            duration=duration - last_start,
        )
    return out

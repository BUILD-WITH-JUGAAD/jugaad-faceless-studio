"""
Timing / visual-beat planner.

Phase 3: narration + Whisper (or fallback) → chronologically ordered VisualBeat[].

Does not fetch assets, generate queries, or touch run_pipeline / providers.

Timing helpers below mirror image_engine._sentences / _even_times / _beat_times
so this module stays importable without PIL (image_engine pulls Pillow at import).
visual_beat_times() in image_engine is intentionally left untouched.
"""

from __future__ import annotations

import math
import re

import config
from visual_beat import make_visual_beat

# Clause / topic cues inside a long sentence (not a full semantic NLP pass).
_CLAUSE_SPLIT = re.compile(
    r"(?<=[;:—–])\s+"
    r"|(?<=,)\s+(?=(?:and|but|while|when|as|before|after|then|until|because)\b)",
    re.I,
)


def target_beat_count(duration: float) -> int:
    """Duration-scaled beat target. Soft AGENTS ranges when duration allows."""
    duration = max(float(duration or 0.0), 0.1)
    min_s = float(getattr(config, "VISUAL_BEAT_MIN_SECONDS", 2.0) or 2.0)
    pref_s = float(getattr(config, "VISUAL_BEAT_PREFERRED_SECONDS", 4.0) or 4.0)
    max_s = float(getattr(config, "VISUAL_BEAT_MAX_SECONDS", 6.0) or 6.0)
    min_s = max(0.8, min_s)
    pref_s = max(min_s, pref_s)
    max_s = max(pref_s, max_s)

    ideal = duration / pref_s
    lo = max(1, int(math.ceil(duration / max_s)))
    hi = max(lo, int(math.floor(duration / min_s)))

    # Soft floors/ceilings from AGENTS.md density guidance.
    if duration >= 150:
        lo = max(lo, 24)
        hi = min(max(hi, lo), 50)
    elif duration >= 50:
        lo = max(lo, 10)
        hi = min(max(hi, lo), 18)
    elif duration >= 25:
        lo = max(lo, 6)
        hi = min(max(hi, lo), 10)

    if lo > hi:
        lo, hi = hi, lo
    return int(max(lo, min(hi, round(ideal))))


def _spoken(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        from tts_engine import for_speech
        return (for_speech(raw) or raw).strip()
    except Exception:
        return raw


def _sentences(text: str) -> list:
    """Mirror of image_engine._sentences."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _even_times(n: int, duration: float, words: list) -> list:
    """Mirror of image_engine._even_times — equal-ish holds, word-snapped."""
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
    return [
        (targets[i], targets[i + 1] if i + 1 < n else duration)
        for i in range(n)
    ]


def _times_look_ok(times: list, duration: float) -> bool:
    """Mirror of image_engine._times_look_ok."""
    if len(times) < 2:
        return True
    holds = [end - start for start, end in times]
    if any(h < 0.45 for h in holds):
        return False
    if holds[0] > duration * 0.5:
        return False
    return True


def _beat_times(beats: list, words: list, duration: float) -> list:
    """Mirror of image_engine._beat_times — Whisper-aligned contiguous spans."""
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
        search_limit = min(len(words), cursor + max(len(tokens) * 3, 8))
        while i < search_limit and matched < max(len(tokens) - 1, 1):
            ww = re.sub(r"[^a-z0-9']", "", words[i]["word"].lower())
            if ww and matched < len(tokens) and (
                ww == tokens[matched]
                or ww in tokens[matched]
                or tokens[matched] in ww
            ):
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


def _split_clauses(sentence: str) -> list:
    sentence = (sentence or "").strip()
    if not sentence:
        return []
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(sentence) if p and p.strip()]
    return parts if parts else [sentence]


def _semantic_units(text: str) -> list:
    """Sentence units; long sentences further split on light clause markers."""
    spoken = _spoken(text)
    if not spoken:
        return []
    units = []
    for sentence in _sentences(spoken):
        words = sentence.split()
        if len(words) >= 18 or len(sentence) >= 110:
            clauses = _split_clauses(sentence)
            if len(clauses) > 1:
                units.extend(clauses)
                continue
        units.append(sentence)
    return units or ([spoken] if spoken else [])


def _pause_cut_indices(words: list, min_gap: float = 0.35) -> set:
    """Word indices where the next word starts after a pause (good cut points)."""
    cuts = set()
    if not words or len(words) < 2:
        return cuts
    for i in range(len(words) - 1):
        end = float(words[i].get("end") or words[i].get("start") or 0.0)
        nxt = float(words[i + 1].get("start") or end)
        if nxt - end >= min_gap:
            cuts.add(i + 1)
    return cuts


def _span(item: dict) -> float:
    return max(0.0, float(item["end"]) - float(item["start"]))


def _merge_pair(a: dict, b: dict) -> dict:
    return {
        "text": (a["text"] + " " + b["text"]).strip(),
        "start": float(a["start"]),
        "end": float(b["end"]),
    }


def _split_item(item: dict, words: list) -> tuple:
    """Split one long unit near its midpoint, preferring pause / word anchors."""
    start = float(item["start"])
    end = float(item["end"])
    mid = (start + end) / 2.0
    text = item["text"]
    tokens = text.split()

    cut_t = mid
    left_text = text
    right_text = ""

    if words:
        in_span = [
            w for w in words
            if float(w.get("start") or 0) >= start - 0.05
            and float(w.get("start") or 0) < end
        ]
        pause_cuts = _pause_cut_indices(in_span)
        anchors = []
        for i, w in enumerate(in_span):
            t = float(w.get("start") or start)
            if start + 0.4 < t < end - 0.4:
                weight = abs(t - mid) - (0.35 if i in pause_cuts else 0.0)
                anchors.append((weight, t, i))
        if anchors:
            anchors.sort()
            _, cut_t, idx = anchors[0]
            if tokens and len(in_span) >= 2:
                ratio = max(
                    1,
                    min(len(tokens) - 1, int(round(idx * len(tokens) / len(in_span)))),
                )
                left_text = " ".join(tokens[:ratio]).strip() or text
                right_text = " ".join(tokens[ratio:]).strip()
    elif len(tokens) >= 4:
        half = len(tokens) // 2
        left_text = " ".join(tokens[:half]).strip()
        right_text = " ".join(tokens[half:]).strip()
        cut_t = start + (end - start) * (half / float(len(tokens)))

    if not right_text:
        cut_t = mid
        right_text = left_text

    cut_t = min(max(cut_t, start + 0.4), end - 0.4)
    left = {"text": left_text, "start": start, "end": cut_t}
    right = {"text": right_text, "start": cut_t, "end": end}
    return left, right


def _align_units_to_words(units: list, words: list, duration: float) -> list:
    """Map narration units to timeline spans."""
    duration = float(duration)
    if not units:
        return [{"text": "", "start": 0.0, "end": duration}]
    if not words:
        weights = [max(len(u), 1) for u in units]
        total = float(sum(weights))
        cursor = 0.0
        out = []
        for i, unit in enumerate(units):
            if i == len(units) - 1:
                end = duration
            else:
                end = cursor + duration * (weights[i] / total)
            out.append({"text": unit, "start": cursor, "end": max(cursor + 0.05, end)})
            cursor = out[-1]["end"]
        out[0]["start"] = 0.0
        out[-1]["end"] = duration
        return out

    times = _beat_times(units, words, duration)
    if len(times) != len(units):
        times = _even_times(len(units), duration, words)
    out = []
    for unit, (start, end) in zip(units, times):
        out.append({"text": unit, "start": float(start), "end": float(end)})
    out[0]["start"] = 0.0
    out[-1]["end"] = duration
    return out


def _adjust_density(items: list, duration: float, words: list, target: int) -> list:
    """Merge short neighbors / split long spans toward the duration-scaled target."""
    min_s = float(getattr(config, "VISUAL_BEAT_MIN_SECONDS", 2.0) or 2.0)
    max_s = float(getattr(config, "VISUAL_BEAT_MAX_SECONDS", 6.0) or 6.0)
    hard_max = float(getattr(config, "VISUAL_BEAT_HARD_MAX_SECONDS", 8.0) or 8.0)
    hard_max = max(max_s, hard_max)

    items = [dict(x) for x in items]
    if not items:
        return [{"text": "", "start": 0.0, "end": float(duration)}]

    changed = True
    guard = 0
    while changed and guard < 200:
        guard += 1
        changed = False
        nxt = []
        for item in items:
            if _span(item) > hard_max + 0.05 and len((item.get("text") or "").split()) >= 2:
                left, right = _split_item(item, words or [])
                if _span(left) >= 0.4 and _span(right) >= 0.4:
                    nxt.extend([left, right])
                    changed = True
                    continue
            nxt.append(item)
        items = nxt

    changed = True
    guard = 0
    while changed and guard < 200:
        guard += 1
        changed = False
        if len(items) <= 1:
            break
        best_i = None
        best_span = None
        for i in range(len(items) - 1):
            a, b = items[i], items[i + 1]
            combined = _span(a) + _span(b)
            if min(_span(a), _span(b)) < min_s and combined <= max_s + 0.25:
                if best_span is None or combined < best_span:
                    best_span = combined
                    best_i = i
        if best_i is not None:
            items = (
                items[:best_i]
                + [_merge_pair(items[best_i], items[best_i + 1])]
                + items[best_i + 2:]
            )
            changed = True

    guard = 0
    while len(items) < target and guard < 200:
        guard += 1
        longest_i = max(range(len(items)), key=lambda i: _span(items[i]))
        if _span(items[longest_i]) < (min_s * 2) - 0.05:
            break
        left, right = _split_item(items[longest_i], words or [])
        if _span(left) < 0.4 or _span(right) < 0.4:
            break
        items = items[:longest_i] + [left, right] + items[longest_i + 1:]

    guard = 0
    while len(items) > target and guard < 200:
        guard += 1
        if len(items) <= 1:
            break
        best_i = 0
        best_span = _span(items[0]) + _span(items[1])
        for i in range(len(items) - 1):
            combined = _span(items[i]) + _span(items[i + 1])
            if combined < best_span:
                best_span = combined
                best_i = i
        if best_span > hard_max + 1.0 and len(items) <= target + 2:
            break
        items = (
            items[:best_i]
            + [_merge_pair(items[best_i], items[best_i + 1])]
            + items[best_i + 2:]
        )

    if items:
        items[0]["start"] = 0.0
        for i in range(1, len(items)):
            items[i]["start"] = float(items[i - 1]["end"])
        items[-1]["end"] = float(duration)
        for i, item in enumerate(items):
            if item["end"] <= item["start"]:
                nxt_end = items[i + 1]["start"] if i + 1 < len(items) else duration
                item["end"] = max(item["start"] + 0.05, float(nxt_end))
        items[-1]["end"] = float(duration)
    return items


def plan_visual_beats(
    text: str,
    duration: float,
    words: list = None,
) -> list:
    """
    Build chronological VisualBeat objects covering the narration.

    Parameters
    ----------
    text : spoken narration (script text; stage cues stripped when possible)
    duration : voiceover length in seconds
    words : optional Whisper word dicts [{word, start, end}, ...]
    """
    duration = max(float(duration or 0.0), 0.1)
    words = list(words or [])
    spoken = _spoken(text)
    if not spoken:
        return [
            make_visual_beat(start=0.0, end=duration, narration="", beat_id="vb_0001")
        ]

    units = _semantic_units(spoken)
    timed = _align_units_to_words(units, words, duration)
    target = target_beat_count(duration)
    adjusted = _adjust_density(timed, duration, words, target)

    snippets = [item["text"] for item in adjusted]
    hard_max = float(getattr(config, "VISUAL_BEAT_HARD_MAX_SECONDS", 8.0) or 8.0)

    if words and len(snippets) > 1:
        snapped = _beat_times(snippets, words, duration)
        if len(snapped) != len(snippets):
            snapped = _even_times(len(snippets), duration, words)
        for item, (start, end) in zip(adjusted, snapped):
            item["start"] = float(start)
            item["end"] = float(end)
        adjusted[0]["start"] = 0.0
        adjusted[-1]["end"] = duration
    elif len(snippets) > 1 and not words:
        holds = [_span(x) for x in adjusted]
        if holds and (max(holds) > hard_max or min(holds) < 0.4):
            snapped = _even_times(len(snippets), duration, [])
            for item, (start, end) in zip(adjusted, snapped):
                item["start"] = float(start)
                item["end"] = float(end)

    beats = []
    cursor = 0.0
    for i, item in enumerate(adjusted):
        start = 0.0 if i == 0 else cursor
        end = float(item["end"])
        if i == len(adjusted) - 1:
            end = duration
        if end <= start:
            end = min(duration, start + max(0.05, duration / max(len(adjusted), 1)))
        beats.append(
            make_visual_beat(
                start=start,
                end=end,
                narration=str(item.get("text") or "").strip(),
                beat_id="vb_{0:04d}".format(i + 1),
            )
        )
        cursor = end
    return beats


def plan_summary(beats: list) -> dict:
    """Small QA helper for tests / logging (not used by providers yet)."""
    if not beats:
        return {"count": 0, "duration": 0.0, "min_hold": 0.0, "max_hold": 0.0}
    holds = [float(b.duration) for b in beats]
    return {
        "count": len(beats),
        "duration": float(beats[-1].end),
        "min_hold": min(holds),
        "max_hold": max(holds),
        "avg_hold": sum(holds) / len(holds),
    }

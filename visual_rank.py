"""
Common asset ranking + deduplication for VisualBeat candidates.

Phase 5: VisualBeat + candidate assets → best selection.

Does not search providers, download files, or touch run_pipeline.
Reuses broll_engine.score_video as an optional provider-quality signal when the
payload looks like stock video — never as the sole decision.

Ranking priority: semantic narration match ≫ provider/order ≫ diversity.
"""

from __future__ import annotations

import re
from dataclasses import replace

import config
from visual_beat import VisualBeat

_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "with",
    "from", "into", "over", "by", "it", "its", "is", "was", "were", "be", "been",
    "this", "that", "they", "them", "their", "you", "your", "we", "our", "not",
}


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(getattr(config, name, default) or default)
    except (TypeError, ValueError):
        return float(default)


def min_relevance_threshold() -> float:
    return max(0.0, min(1.0, _cfg_float("VISUAL_MIN_RELEVANCE_SCORE", 0.60)))


def _tokens(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
        if len(w) > 2 and w not in _STOP
    }


def _field_tokens(values) -> set:
    out = set()
    if values is None:
        return out
    if isinstance(values, str):
        return _tokens(values)
    for item in values:
        out |= _tokens(str(item or ""))
    return out


def _overlap_ratio(need: set, hay: set) -> float:
    if not need or not hay:
        return 0.0
    return len(need & hay) / float(len(need))


def asset_identity(asset: dict):
    """Strongest available id for dedupe tracking."""
    if not isinstance(asset, dict):
        return None
    for key in (
        "id", "asset_id", "pexels_id", "pixabay_id", "unsplash_id", "provider_id",
    ):
        if asset.get(key) is not None and str(asset.get(key)).strip() != "":
            return asset.get(key)
    for key in ("canonical_url", "url", "page", "pageURL", "path", "link"):
        val = asset.get(key)
        if val:
            return str(val)
    file_info = asset.get("file")
    if isinstance(file_info, dict) and file_info.get("link"):
        return str(file_info.get("link"))
    return None


def _join_meta(*parts) -> str:
    bits = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple)):
            bits.append(" ".join(str(x) for x in part if x))
        else:
            bits.append(str(part))
    return " ".join(bits)


def normalize_candidate(raw: dict) -> dict:
    """Common lightweight candidate shape. Missing metadata is allowed."""
    if not isinstance(raw, dict):
        return {
            "id": None, "provider": "", "url": "", "path": "", "title": "",
            "description": "", "tags": "", "slug": "", "duration": 0,
            "width": 0, "height": 0, "shot_type": "", "provider_score": None,
            "haystack": "", "raw": {},
        }

    # Already normalized?
    if raw.get("haystack") is not None and "raw" in raw and "semantic_score" not in raw:
        # Ranked rows also have haystack; prefer re-normalize from embedded raw
        # only when this looks like a prior ranked row.
        pass

    tags = raw.get("tags")
    if isinstance(tags, (list, tuple)):
        tags_text = " ".join(str(t) for t in tags if t)
    else:
        tags_text = str(tags or "")

    url = (
        raw.get("canonical_url")
        or raw.get("url")
        or raw.get("page")
        or raw.get("pageURL")
        or ""
    )
    path = str(raw.get("path") or "")
    slug = str(raw.get("slug") or "")
    if not slug and "/video/" in str(url):
        slug = str(url).split("/video/")[-1].rstrip("/")

    title = str(raw.get("title") or raw.get("alt") or raw.get("alt_description") or "")
    description = str(raw.get("description") or raw.get("caption") or "")
    source = raw.get("raw") if isinstance(raw.get("raw"), dict) else raw
    haystack = _join_meta(
        slug, tags_text, title, description, raw.get("query"),
        raw.get("haystack"), url, path,
    )

    width = int(raw.get("width") or 0)
    height = int(raw.get("height") or 0)
    file_info = raw.get("file") if isinstance(raw.get("file"), dict) else {}
    if not width:
        width = int(file_info.get("width") or 0)
    if not height:
        height = int(file_info.get("height") or 0)

    shot = str(raw.get("shot_type") or raw.get("shot") or "").strip().lower()
    if not shot:
        low = haystack.lower()
        for label, hints in (
            ("close-up", ("close-up", "closeup", "close up", "macro", "detail")),
            ("wide", ("wide", "aerial", "establishing", "landscape", "panorama")),
            ("medium", ("medium", "mid-shot", "mid shot", "portrait")),
            ("action", ("running", "walking", "moving", "chase", "action")),
            ("environment", ("exterior", "interior", "street", "room", "building")),
            ("person", ("firefighter", "fisherman", "hacker", "man", "woman", "people")),
            ("object", ("hands", "holding", "boots", "lantern", "screen")),
        ):
            if any(h in low for h in hints):
                shot = label
                break

    provider_score = raw.get("provider_score")
    if provider_score is None and "score" in raw and "semantic_score" not in raw:
        provider_score = raw.get("score")
    try:
        provider_score = float(provider_score) if provider_score is not None else None
    except (TypeError, ValueError):
        provider_score = None

    return {
        "id": asset_identity(raw) or asset_identity(source),
        "provider": str(raw.get("provider") or source.get("provider") or ""),
        "url": str(url or ""),
        "path": path,
        "title": title,
        "description": description,
        "tags": tags_text,
        "slug": slug,
        "duration": float(raw.get("duration") or source.get("duration") or 0),
        "width": width,
        "height": height,
        "shot_type": shot,
        "provider_score": provider_score,
        "haystack": haystack,
        "raw": source if isinstance(source, dict) else raw,
    }


def _provider_signal(beat: VisualBeat, cand: dict, setting: str) -> float:
    """Optional 0–1 signal from embedded provider_score or score_video."""
    if cand.get("provider_score") is not None:
        return max(0.0, min(1.0, float(cand["provider_score"]) / 15.0))

    raw = cand.get("raw") if isinstance(cand.get("raw"), dict) else {}
    looks_video = bool(raw.get("video_files")) or "/video/" in str(raw.get("url") or "")
    if not looks_video:
        return 0.0
    try:
        from broll_engine import score_video
        queries = list(beat.queries or []) or [beat.visual_intent or beat.narration or ""]
        need = set()
        for group in (beat.subject, beat.action, beat.location, beat.objects):
            need |= _field_tokens(group)
        best = -50.0
        for q in queries:
            best = max(best, float(score_video(raw, q, need, setting or "")))
        if best <= 0:
            return 0.0
        return max(0.0, min(1.0, best / 15.0))
    except Exception:
        return 0.0


def _quality_signal(cand: dict) -> float:
    score = 0.5
    duration = float(cand.get("duration") or 0)
    if duration:
        if 6 <= duration <= 50:
            score += 0.2
        elif duration < 4:
            score -= 0.15
    short_side = min(int(cand.get("width") or 0), int(cand.get("height") or 0))
    if short_side >= 1080:
        score += 0.2
    elif short_side >= 720:
        score += 0.1
    elif 0 < short_side < 480:
        score -= 0.15
    return max(0.0, min(1.0, score))


def _diversity_signal(cand: dict, recent_shot_types) -> float:
    shot = (cand.get("shot_type") or "").strip().lower()
    if not shot:
        return 0.5
    recent = [str(s).lower() for s in (recent_shot_types or []) if s]
    if not recent:
        return 0.55
    if shot == recent[-1]:
        return 0.25
    if shot in recent:
        return 0.4
    return 0.7


def semantic_relevance(beat: VisualBeat, cand: dict) -> float:
    """0–1: does this asset represent what is being narrated?"""
    if "haystack" not in cand:
        cand = normalize_candidate(cand)
    hay = _tokens(cand.get("haystack") or "")

    subject = _field_tokens(beat.subject)
    action = _field_tokens(beat.action)
    location = _field_tokens(beat.location)
    objects = _field_tokens(beat.objects)
    time_toks = _tokens(beat.time_context or "")
    mood_toks = _tokens(beat.mood or "")
    intent = _tokens(beat.visual_intent or "")
    query_toks = set()
    for q in list(beat.queries or [])[:3]:
        query_toks |= _tokens(q)
    narr = _tokens(beat.narration or "")

    parts = []
    weights = []

    def add(need, weight):
        if need:
            parts.append(_overlap_ratio(need, hay))
            weights.append(weight)

    add(subject, 0.22)
    add(action, 0.20)
    add(location, 0.16)
    add(objects, 0.16)
    add(time_toks, 0.08)
    add(mood_toks, 0.04)
    add(intent or query_toks or narr, 0.14)

    if not weights:
        return _overlap_ratio(narr, hay) if narr else 0.0

    score = sum(p * w for p, w in zip(parts, weights)) / float(sum(weights))
    if intent:
        score = min(1.0, score + 0.05 * min(3, len(intent & hay)))
    return max(0.0, min(1.0, score))


def rank_score(
    beat: VisualBeat,
    candidate: dict,
    *,
    setting: str = "",
    used_ids=None,
    recent_ids=None,
    recent_shot_types=None,
) -> dict:
    """
    Ranking breakdown for one candidate.

    final ≈ semantic*0.62 + provider*0.15 + quality*0.08 + diversity*0.05
            − used_penalty − adjacent_penalty
    """
    cand = normalize_candidate(candidate)
    used_ids = set(used_ids or ())
    recent_ids = list(recent_ids or [])
    aid = cand.get("id")

    semantic = semantic_relevance(beat, cand)
    provider = _provider_signal(beat, cand, setting)
    quality = _quality_signal(cand)
    diversity = _diversity_signal(cand, recent_shot_types)

    used_pen = 0.0
    adjacent_pen = 0.0
    if aid is not None and aid in used_ids:
        used_pen = _cfg_float("VISUAL_USED_ASSET_PENALTY", 0.15)
    if aid is not None and recent_ids and aid == recent_ids[-1]:
        adjacent_pen = _cfg_float("VISUAL_ADJACENT_ASSET_PENALTY", 0.35)
    elif aid is not None and aid in recent_ids:
        adjacent_pen = max(adjacent_pen, _cfg_float("VISUAL_USED_ASSET_PENALTY", 0.15) * 0.5)

    final = (
        0.62 * semantic
        + 0.15 * provider
        + 0.08 * quality
        + 0.05 * diversity
        - used_pen
        - adjacent_pen
    )
    final = max(0.0, min(1.0, final))
    return {
        "id": aid,
        "final": final,
        "semantic": semantic,
        "provider": provider,
        "quality": quality,
        "diversity": diversity,
        "used_penalty": used_pen,
        "adjacent_penalty": adjacent_pen,
        "candidate": cand,
    }


def score_candidate(beat, asset, *, query=None, setting=""):
    """Public 0–1 score helper (tests / callers)."""
    working = beat
    if query:
        working = replace(beat, queries=[query] + list(beat.queries or []))
    return float(rank_score(working, asset, setting=setting)["final"])


def rank_candidates(
    beat: VisualBeat,
    candidates: list,
    *,
    setting: str = "",
    used_ids=None,
    recent_ids=None,
    recent_shot_types=None,
    exclude_used: bool = False,
) -> list:
    """Ranked candidates, best first. Deterministic for equal inputs."""
    used_ids = set(used_ids or ())
    scored = []
    seen = set()
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        cand = normalize_candidate(raw)
        aid = cand.get("id")
        if aid is not None and aid in seen:
            continue
        if aid is not None:
            seen.add(aid)
        if exclude_used and aid is not None and aid in used_ids:
            continue
        detail = rank_score(
            beat,
            cand,
            setting=setting,
            used_ids=used_ids,
            recent_ids=recent_ids,
            recent_shot_types=recent_shot_types,
        )
        if (
            detail["semantic"] <= 0
            and detail["provider"] <= 0
            and not (cand.get("haystack") or "").strip()
        ):
            continue
        row = dict(cand)
        row.update({
            "score": detail["final"],
            "semantic_score": detail["semantic"],
            "provider_signal": detail["provider"],
            "quality_signal": detail["quality"],
            "diversity_signal": detail["diversity"],
            "used_penalty": detail["used_penalty"],
            "adjacent_penalty": detail["adjacent_penalty"],
            "rank_detail": detail,
            "raw": cand.get("raw") or raw,
        })
        scored.append(row)

    scored.sort(
        key=lambda r: (
            -float(r.get("score") or 0),
            -float(r.get("semantic_score") or 0),
            str(r.get("id") or ""),
        )
    )
    return scored


def select_for_beat(
    beat: VisualBeat,
    candidates: list = None,
    *,
    setting: str = "",
    used_ids=None,
    recent_ids=None,
    recent_shot_types=None,
    min_score: float = None,
) -> VisualBeat:
    """
    Select the best candidate for one beat.

    Previously used assets are penalized, not hard-removed, so the only viable
    asset can still be chosen and marked reused.
    """
    pool = list(candidates if candidates is not None else (beat.candidate_assets or []))
    threshold = min_relevance_threshold() if min_score is None else float(min_score)
    ranked = rank_candidates(
        beat,
        pool,
        setting=setting,
        used_ids=used_ids,
        recent_ids=recent_ids,
        recent_shot_types=recent_shot_types,
    )
    if not ranked:
        return replace(beat, candidate_assets=[], selected_asset=None, relevance_score=0.0)

    best = ranked[0]
    used_set = set(used_ids or ())
    # Prefer an unused/non-adjacent candidate when nearly as good.
    for row in ranked:
        aid = row.get("id")
        reused = aid is not None and aid in used_set
        adjacent = bool(recent_ids) and aid == recent_ids[-1]
        if not reused and not adjacent:
            if float(row["score"]) + 0.05 >= float(best["score"]):
                best = row
            break

    aid = best.get("id")
    reused = aid is not None and aid in used_set
    adjacent = bool(recent_ids) and aid == (recent_ids[-1] if recent_ids else None)
    low = float(best["score"]) < threshold

    selected = dict(best)
    selected["low_confidence"] = bool(low)
    selected["reused"] = bool(reused or adjacent)
    selected["adjacent_reuse"] = bool(adjacent)

    return replace(
        beat,
        candidate_assets=ranked,
        selected_asset=selected,
        relevance_score=float(best["score"]),
    )


def select_assets_for_beats(
    beats: list,
    *,
    setting: str = "",
    min_score: float = None,
    candidates_by_beat=None,
    used_assets=None,
) -> list:
    """Select across beats with running used/recent tracking."""
    used = set(used_assets or ())
    recent_ids = []
    recent_shots = []
    out = []
    for i, beat in enumerate(beats or []):
        pool = None
        if candidates_by_beat is not None:
            if isinstance(candidates_by_beat, dict):
                pool = candidates_by_beat.get(beat.id)
            elif i < len(candidates_by_beat):
                pool = candidates_by_beat[i]
        chosen = select_for_beat(
            beat,
            candidates=pool,
            setting=setting,
            used_ids=used,
            recent_ids=recent_ids,
            recent_shot_types=recent_shots,
            min_score=min_score,
        )
        selected = chosen.selected_asset
        if selected:
            aid = selected.get("id")
            if aid is not None:
                used.add(aid)
                recent_ids.append(aid)
                recent_ids = recent_ids[-8:]
            shot = selected.get("shot_type") or ""
            if shot:
                recent_shots.append(shot)
                recent_shots = recent_shots[-8:]
        out.append(chosen)
    return out


def selection_summary(beats: list) -> dict:
    beats = list(beats or [])
    selected = [b for b in beats if b.selected_asset]
    ids = [b.selected_asset.get("id") for b in selected if b.selected_asset]
    present = [i for i in ids if i is not None]
    return {
        "beats": len(beats),
        "selected": len(selected),
        "misses": len(beats) - len(selected),
        "unique_ids": len(set(present)),
        "duplicate_ids": len(present) - len(set(present)),
        "low_confidence": sum(
            1 for b in selected if (b.selected_asset or {}).get("low_confidence")
        ),
        "reused": sum(1 for b in selected if (b.selected_asset or {}).get("reused")),
        "avg_score": (
            sum(float(b.relevance_score or 0) for b in selected) / len(selected)
            if selected else 0.0
        ),
        "min_threshold": min_relevance_threshold(),
    }

"""
Stock provider adapters for VisualBeat plans (Phase 6).

Wraps existing Pexels / Pixabay / Unsplash search + download helpers.
Does not rewrite provider engines.

Flow:
  plan_visual_beats → enrich_visual_beats → per-beat search (cached)
  → visual_rank → download via existing helpers → renderer payloads

When VISUAL_PLANNER_ENABLED is False, callers keep using fetch_story_broll /
generate_photo_storyboard unchanged.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import config
from visual_beat import VisualBeat
from visual_rank import select_assets_for_beats, select_for_beat

PLANNER_ALGO = "visual_planner_v1"
# Stop escalating fallbacks once this many unique candidates exist.
_ENOUGH_CANDIDATES = 6


def visual_planner_enabled() -> bool:
    return bool(getattr(config, "VISUAL_PLANNER_ENABLED", False))


def _beat_query_list(beat: VisualBeat, setting: str = "") -> list:
    """Primary + fallback queries; setting appended as last-resort signal."""
    out = []
    for q in list(beat.queries or []) + list(beat.fallback_queries or []):
        s = str(q or "").strip()
        if s and s not in out:
            out.append(s)
    if not out and beat.visual_intent:
        out.append(str(beat.visual_intent).strip())
    if not out and beat.narration:
        out.append(str(beat.narration).strip()[:120])
    setting = (setting or "").strip()
    if setting and setting not in out:
        out.append(setting)
    return out[:8]


def _normalize_provider(provider: str) -> str:
    raw = (provider or "pexels").strip().lower()
    if raw in {"pixabay", "px"}:
        return "pixabay"
    if raw in {"photos", "photo", "unsplash", "image", "images"}:
        return "unsplash"
    if raw in {"live", "stock", "real", "pexels", "video"}:
        return "pexels"
    return raw


def _shape_video_hit(video: dict, provider: str, query: str = "") -> dict:
    """Map a Pexels/Pixabay-shaped video dict into a ranking candidate."""
    from broll_engine import _pick_file, _slug

    tags = video.get("tags") or ""
    slug = _slug(video) if video else ""
    file_info = None
    try:
        file_info = _pick_file(video)
    except Exception:
        file_info = None
    width = int((file_info or {}).get("width") or 0)
    height = int((file_info or {}).get("height") or 0)
    return {
        "id": video.get("id"),
        "provider": provider,
        "url": video.get("url") or "",
        "tags": tags,
        "slug": slug,
        "title": slug.replace("-", " "),
        "description": tags,
        "duration": video.get("duration") or 0,
        "width": width,
        "height": height,
        # Do not set "query" — visual_rank folds that into haystack.
        "search_query": query,
        "file": file_info,
        "video_files": video.get("video_files") or [],
        "raw_video": video,
    }


def _shape_unsplash_hit(photo: dict, query: str = "") -> dict:
    """Map an Unsplash photo into a ranking candidate."""
    from photo_engine import _as_video_shape, _pick_url

    shaped = _as_video_shape(photo)
    user = (photo.get("user") or {})
    urls = photo.get("urls") or {}
    return {
        "id": photo.get("id"),
        "provider": "unsplash",
        "url": (photo.get("links") or {}).get("html") or "",
        "tags": shaped.get("tags") or "",
        "slug": (photo.get("slug") or "").replace("-", " "),
        "title": photo.get("alt_description") or photo.get("description") or "",
        "description": photo.get("description") or photo.get("alt_description") or "",
        "duration": 0,
        "width": int(photo.get("width") or 0),
        "height": int(photo.get("height") or 0),
        "search_query": query,
        "download_url": _pick_url(photo) or urls.get("regular") or "",
        "credit": "{0}".format(user.get("name") or user.get("username") or "Unsplash"),
        "photo": photo,
    }


class SearchCache:
    """In-run cache: identical (provider, query) → same raw hits; still re-ranked later."""

    def __init__(self):
        self._store = {}
        self.hits = 0
        self.misses = 0

    def get_or_search(self, query: str, provider: str, per_page: int, search_fn):
        key = (
            _normalize_provider(provider),
            str(query or "").strip().lower(),
            int(per_page),
        )
        if key in self._store:
            self.hits += 1
            return list(self._store[key])
        self.misses += 1
        try:
            hits = list(search_fn(query) or [])
        except Exception:
            raise
        self._store[key] = hits
        return list(hits)


def default_search(query: str, provider: str = "pexels", per_page: int = 15) -> list:
    """
    Call existing provider search helpers.

    Returns raw provider hits (not yet ranked). Injectable in tests via search_fn.
    """
    provider = _normalize_provider(provider)
    if provider == "unsplash":
        from photo_engine import _search as unsplash_search
        return list(unsplash_search(query, per_page=per_page) or [])
    from broll_engine import _search as stock_search
    return list(stock_search(query, provider=provider, per_page=per_page) or [])


def collect_candidates_for_beat(
    beat: VisualBeat,
    *,
    provider: str = "pexels",
    setting: str = "",
    per_page: int = 12,
    search_fn=None,
    max_queries: int = 4,
    cache: SearchCache = None,
    enough: int = None,
) -> list:
    """
    Search primary, then escalate fallbacks only until enough candidates exist.

    Marks each candidate with search_query / query_index for fallback tracing.
    """
    provider = _normalize_provider(provider)
    raw_search = search_fn or (
        lambda q, p=provider, n=per_page: default_search(q, provider=p, per_page=n)
    )
    cache = cache or SearchCache()
    target = int(enough if enough is not None else _ENOUGH_CANDIDATES)

    queries = _beat_query_list(beat, setting=setting)[:max_queries]
    if not queries:
        return []

    seen = set()
    out = []
    for qi, query in enumerate(queries):
        try:
            hits = cache.get_or_search(query, provider, per_page, raw_search)
        except Exception as exc:
            print("[stock] search failed '{0}' ({1}): {2}".format(query, provider, exc))
            continue
        added = 0
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            if provider == "unsplash":
                cand = _shape_unsplash_hit(hit, query=query)
            else:
                cand = _shape_video_hit(hit, provider=provider, query=query)
            aid = cand.get("id")
            if aid is None or aid in seen:
                continue
            seen.add(aid)
            cand["query_index"] = qi
            cand["used_fallback_query"] = qi > 0
            out.append(cand)
            added += 1
        # Primary empty → keep escalating. Primary thin → keep escalating until enough.
        if out and len(out) >= target:
            break
        if out and qi == 0 and added == 0:
            continue
    return out


def fill_stock_candidates(
    beats: list,
    *,
    provider: str = "pexels",
    setting: str = "",
    search_fn=None,
    per_page: int = 12,
    cache: SearchCache = None,
) -> list:
    """Attach candidate_assets to each beat (no selection yet)."""
    provider = _normalize_provider(provider)
    cache = cache or SearchCache()
    filled = []
    for beat in beats or []:
        cands = collect_candidates_for_beat(
            beat,
            provider=provider,
            setting=setting,
            search_fn=search_fn,
            per_page=per_page,
            cache=cache,
        )
        filled.append(replace(beat, candidate_assets=cands))
    return filled


def _attach_search_meta(beat: VisualBeat) -> VisualBeat:
    """Copy adapter search/download fields from raw onto ranked selected_asset."""
    sel = beat.selected_asset
    if not sel:
        return beat
    raw = sel.get("raw") if isinstance(sel.get("raw"), dict) else {}
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    out = dict(sel)
    for key in (
        "search_query", "used_fallback_query", "query_index",
        "file", "photo", "download_url", "credit", "raw_video",
    ):
        if out.get(key) not in (None, ""):
            continue
        if key in raw:
            out[key] = raw[key]
        elif key in nested:
            out[key] = nested[key]
    return replace(beat, selected_asset=out)


def select_stock_for_beats(
    beats: list,
    *,
    provider: str = "pexels",
    setting: str = "",
    search_fn=None,
    per_page: int = 12,
    min_score: float = None,
    cache: SearchCache = None,
) -> list:
    """
    Search stock providers per beat, then rank/dedupe via visual_rank.

    Returns VisualBeat[] with candidate_assets + selected_asset filled.
    Does not download files.
    """
    provider = _normalize_provider(provider)
    cache = cache or SearchCache()
    filled = fill_stock_candidates(
        beats,
        provider=provider,
        setting=setting,
        search_fn=search_fn,
        per_page=per_page,
        cache=cache,
    )
    pools = [list(b.candidate_assets or []) for b in filled]
    selected = select_assets_for_beats(
        filled,
        setting=setting,
        min_score=min_score,
        candidates_by_beat=pools,
    )
    return [_attach_search_meta(b) for b in selected]


def select_stock_for_beat(
    beat: VisualBeat,
    *,
    provider: str = "pexels",
    setting: str = "",
    search_fn=None,
    used_ids=None,
    recent_ids=None,
    per_page: int = 12,
    min_score: float = None,
    cache: SearchCache = None,
) -> VisualBeat:
    """Single-beat convenience wrapper."""
    cands = collect_candidates_for_beat(
        beat,
        provider=provider,
        setting=setting,
        search_fn=search_fn,
        per_page=per_page,
        cache=cache,
    )
    chosen = select_for_beat(
        beat,
        candidates=cands,
        setting=setting,
        used_ids=used_ids,
        recent_ids=recent_ids,
        min_score=min_score,
    )
    return _attach_search_meta(chosen)


def stock_selection_report(beats: list) -> dict:
    """Compact coverage stats for tests / later logging."""
    from visual_rank import selection_summary
    base = selection_summary(beats)
    providers = {}
    fallbacks = 0
    for beat in beats or []:
        sel = beat.selected_asset or {}
        p = sel.get("provider") or "none"
        providers[p] = providers.get(p, 0) + (1 if beat.selected_asset else 0)
        if sel.get("used_fallback_query"):
            fallbacks += 1
    base["providers"] = providers
    base["fallback_queries_used"] = fallbacks
    return base


def plan_enriched_beats(part: dict, text: str, duration: float, words: list = None) -> list:
    """Timing planner + semantic enrichment (stock path entry)."""
    from visual_planner import plan_visual_beats
    from visual_semantics import enrich_visual_beats

    beats = plan_visual_beats(text, duration, words=words)
    return enrich_visual_beats(beats, part=part or {})


def _beat_fingerprint(beats: list) -> list:
    out = []
    for b in beats or []:
        out.append({
            "id": b.id,
            "start": round(float(b.start), 3),
            "end": round(float(b.end), 3),
            "narration": (b.narration or "")[:160],
            "queries": list(b.queries or [])[:4],
        })
    return out


def _download_video_asset(selected: dict, dest: Path) -> Path:
    from broll_engine import _download

    file_info = selected.get("file")
    if not file_info and selected.get("raw_video"):
        from broll_engine import _pick_file
        file_info = _pick_file(selected["raw_video"])
    if not file_info or not file_info.get("link"):
        raise RuntimeError("selected video has no downloadable file")
    if dest.exists() and dest.stat().st_size > 2000:
        return dest
    return _download(file_info, dest)


def _download_photo_asset(selected: dict, dest: Path) -> Path:
    from photo_engine import _download

    photo = selected.get("photo")
    if not photo:
        raise RuntimeError("selected photo missing raw Unsplash payload")
    if dest.exists() and dest.stat().st_size > 2000:
        return dest
    return _download(photo, dest)


def materialize_stock_beats(
    beats: list,
    dest_dir: Path,
    stem: str,
    *,
    provider: str = "pexels",
    download_fn=None,
) -> list:
    """
    Download selected assets (or reuse by asset id). Returns renderer items:
      videos: {path, start, end, beat_id, asset_id}
      photos: same shape (jpg paths)
    Misses reuse the previous path when available (timeline continuity).
    """
    provider = _normalize_provider(provider)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    is_photo = provider == "unsplash"
    ext = "jpg" if is_photo else "mp4"
    prefix = "photo" if is_photo else "vb"

    by_id = {}  # asset id -> local path (avoid re-download)
    items = []
    last_path = None
    records = []

    for i, beat in enumerate(beats or [], start=1):
        dest = dest_dir / "{0}_{1}_{2}.{3}".format(stem, prefix, i, ext)
        sel = beat.selected_asset
        start, end = float(beat.start), float(beat.end)
        narration = (beat.narration or "")[:120]

        if not sel:
            print("[stock] beat {0} miss — no ranked candidate".format(i))
            if last_path is None:
                records.append({
                    "beat_id": beat.id,
                    "miss": True,
                    "narration": narration,
                })
                continue
            items.append({
                "path": last_path,
                "start": start,
                "end": end,
                "beat_id": beat.id,
                "asset_id": None,
                "reused_previous": True,
                "beat": narration,
            })
            records.append({
                "beat_id": beat.id,
                "reused_previous": True,
                "narration": narration,
            })
            continue

        aid = sel.get("id")
        try:
            if aid is not None and aid in by_id:
                path = by_id[aid]
            else:
                if download_fn is not None:
                    path = Path(download_fn(sel, dest))
                elif is_photo:
                    path = _download_photo_asset(sel, dest)
                else:
                    path = _download_video_asset(sel, dest)
                if aid is not None:
                    by_id[aid] = path
        except Exception as exc:
            print("[stock] beat {0} download failed: {1}".format(i, exc))
            if last_path is None:
                records.append({
                    "beat_id": beat.id,
                    "miss": True,
                    "error": str(exc),
                    "narration": narration,
                })
                continue
            path = last_path
            items.append({
                "path": path,
                "start": start,
                "end": end,
                "beat_id": beat.id,
                "asset_id": aid,
                "reused_previous": True,
                "beat": narration,
            })
            records.append({
                "beat_id": beat.id,
                "reused_previous": True,
                "error": str(exc),
                "narration": narration,
            })
            continue

        last_path = path
        items.append({
            "path": path,
            "start": start,
            "end": end,
            "beat_id": beat.id,
            "asset_id": aid,
            "beat": narration,
        })
        records.append({
            "beat_id": beat.id,
            "file": Path(path).name,
            "asset_id": aid,
            "score": round(float(beat.relevance_score or 0), 3),
            "search_query": sel.get("search_query"),
            "used_fallback_query": bool(sel.get("used_fallback_query")),
            "slug": sel.get("slug") or sel.get("title") or "",
            "provider": sel.get("provider") or provider,
            "low_confidence": bool(sel.get("low_confidence")),
            "reused": bool(sel.get("reused")),
            "narration": narration,
        })
        print(
            "[stock] beat {0}/{1} [{2}] score={3:.2f} q={4!r} -> {5}".format(
                i,
                len(beats),
                provider,
                float(beat.relevance_score or 0),
                sel.get("search_query") or "",
                (sel.get("slug") or sel.get("title") or str(aid))[:70],
            )
        )

    return items, records


def _try_reuse_manifest(
    dest_dir: Path,
    stem: str,
    provider: str,
    fingerprint: list,
    is_photo: bool,
) -> list:
    dest_dir = Path(dest_dir)
    manifest_path = dest_dir / (
        "{0}_photos_plan.json".format(stem) if is_photo else "{0}_broll_plan.json".format(stem)
    )
    pattern = (
        "{0}_photo_*.jpg".format(stem) if is_photo else "{0}_vb_*.mp4".format(stem)
    )
    existing = sorted(dest_dir.glob(pattern))
    if not existing or not manifest_path.exists():
        return None
    try:
        saved = json.loads(manifest_path.read_text())
        if (
            saved.get("algo") == PLANNER_ALGO
            and saved.get("provider") == provider
            and saved.get("fingerprint") == fingerprint
            and len(existing) >= len(fingerprint)
        ):
            items = []
            for i, row in enumerate(fingerprint):
                items.append({
                    "path": existing[i],
                    "start": row["start"],
                    "end": row["end"],
                    "beat_id": row.get("id"),
                    "beat": row.get("narration") or "",
                })
            print(
                "[stock] reusing {0} planned asset(s) for {1} ({2})".format(
                    len(items), stem, provider,
                )
            )
            return items
    except Exception:
        return None
    return None


def _write_manifest(
    dest_dir: Path,
    stem: str,
    provider: str,
    fingerprint: list,
    records: list,
    setting: str,
    is_photo: bool,
    report: dict,
) -> None:
    dest_dir = Path(dest_dir)
    path = dest_dir / (
        "{0}_photos_plan.json".format(stem) if is_photo else "{0}_broll_plan.json".format(stem)
    )
    path.write_text(json.dumps({
        "algo": PLANNER_ALGO,
        "provider": provider,
        "setting": setting,
        "fingerprint": fingerprint,
        "assets": records,
        "report": report,
    }, indent=2))


def build_planned_stock_videos(
    part: dict,
    dest_dir: Path,
    stem: str,
    duration: float,
    words: list = None,
    *,
    provider: str = "pexels",
    text: str = None,
    search_fn=None,
    download_fn=None,
) -> list:
    """
    Full stock-video VisualBeat path for assemble_from_videos.

    Returns [{path, start, end}, ...]. Does not call the renderer.
    """
    from tts_engine import for_speech

    provider = _normalize_provider(provider)
    if provider == "pixabay":
        from broll_engine import _require_pixabay
        _require_pixabay()
    else:
        from broll_engine import _headers_pexels
        _headers_pexels()

    spoken = text if text is not None else for_speech(part.get("text") or "")
    setting = (part.get("broll_query") or part.get("setting") or "").strip()
    beats = plan_enriched_beats(part, spoken, duration, words=words)
    fingerprint = _beat_fingerprint(beats)

    reused = _try_reuse_manifest(dest_dir, stem, provider, fingerprint, is_photo=False)
    if reused is not None:
        return reused

    cache = SearchCache()
    selected = select_stock_for_beats(
        beats,
        provider=provider,
        setting=setting,
        search_fn=search_fn,
        cache=cache,
    )
    report = stock_selection_report(selected)
    report["search_cache_hits"] = cache.hits
    report["search_cache_misses"] = cache.misses
    print(
        "[stock] planned {0} beats → selected {1}, misses {2}, "
        "unique {3}, cache hits/misses {4}/{5}".format(
            report["beats"],
            report["selected"],
            report["misses"],
            report["unique_ids"],
            cache.hits,
            cache.misses,
        )
    )

    items, records = materialize_stock_beats(
        selected,
        dest_dir,
        stem,
        provider=provider,
        download_fn=download_fn,
    )
    if not items:
        return []
    _write_manifest(
        dest_dir, stem, provider, fingerprint, records, setting,
        is_photo=False, report=report,
    )
    return [
        {"path": it["path"], "start": it["start"], "end": it["end"]}
        for it in items
    ]


def build_planned_stock_photos(
    part: dict,
    dest_dir: Path,
    stem: str,
    duration: float,
    words: list = None,
    *,
    text: str = None,
    search_fn=None,
    download_fn=None,
) -> list:
    """
    Full Unsplash VisualBeat path for assemble_from_images.

    Returns [{path, start, end, beat}, ...].
    """
    from photo_engine import _require_key
    from tts_engine import for_speech

    _require_key()
    provider = "unsplash"
    spoken = text if text is not None else for_speech(part.get("text") or "")
    setting = (part.get("broll_query") or part.get("setting") or "").strip()
    beats = plan_enriched_beats(part, spoken, duration, words=words)
    fingerprint = _beat_fingerprint(beats)

    reused = _try_reuse_manifest(dest_dir, stem, provider, fingerprint, is_photo=True)
    if reused is not None:
        return [
            {
                "path": it["path"],
                "start": it["start"],
                "end": it["end"],
                "beat": it.get("beat") or "",
            }
            for it in reused
        ]

    cache = SearchCache()
    selected = select_stock_for_beats(
        beats,
        provider=provider,
        setting=setting,
        search_fn=search_fn,
        cache=cache,
    )
    report = stock_selection_report(selected)
    report["search_cache_hits"] = cache.hits
    report["search_cache_misses"] = cache.misses
    print(
        "[stock] planned {0} photo beats → selected {1}, misses {2}, "
        "cache hits/misses {3}/{4}".format(
            report["beats"],
            report["selected"],
            report["misses"],
            cache.hits,
            cache.misses,
        )
    )

    items, records = materialize_stock_beats(
        selected,
        dest_dir,
        stem,
        provider=provider,
        download_fn=download_fn,
    )
    if not items:
        return []
    _write_manifest(
        dest_dir, stem, provider, fingerprint, records, setting,
        is_photo=True, report=report,
    )
    panels = [
        {
            "path": it["path"],
            "start": it["start"],
            "end": it["end"],
            "beat": it.get("beat") or "",
        }
        for it in items
    ]
    if panels:
        panels[-1]["end"] = float(duration)
    return panels

"""
Unsplash stock photos timed to narration, then Ken-Burns in assemble_from_images.

Free Access Key: https://unsplash.com/developers
Trigger the download endpoint so photographers get credit stats.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

import config
from broll_engine import (
    ALGO_VERSION,
    _beat_list,
    _tokens,
    _visual_keys,
    queries_for_beat,
    score_video,
)

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


def _require_key() -> str:
    key = (getattr(config, "UNSPLASH_ACCESS_KEY", "") or "").strip()
    if not key:
        raise RuntimeError(
            "No UNSPLASH_ACCESS_KEY set. Create a free app at "
            "https://unsplash.com/developers and put the Access Key in .env "
            "as UNSPLASH_ACCESS_KEY=your_key_here"
        )
    return key


def _headers() -> dict:
    return {
        "Authorization": "Client-ID {0}".format(_require_key()),
        "Accept-Version": "v1",
        "User-Agent": "faceless-pipeline/1.0",
    }


def _as_video_shape(photo: dict) -> dict:
    """Reuse broll scoring against Unsplash description/tags/slug."""
    tags = " ".join(
        t.get("title") or ""
        for t in (photo.get("tags") or [])
        if isinstance(t, dict)
    )
    alt = photo.get("alt_description") or photo.get("description") or ""
    slug = (photo.get("slug") or "").replace("-", " ")
    return {
        "id": photo.get("id"),
        "duration": 12,
        "url": photo.get("links", {}).get("html") or "",
        "tags": " ".join(x for x in (tags, alt, slug) if x),
        "video_files": [{"link": "photo", "width": 1080, "height": 1920}],
    }


def _search(query: str, per_page: int = 20) -> list:
    params = {
        "query": query,
        "per_page": max(1, min(int(per_page), 30)),
        "orientation": "portrait",
        "content_filter": "high",
    }
    resp = requests.get(
        UNSPLASH_SEARCH_URL, headers=_headers(), params=params, timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("results") or []


def _pick_url(photo: dict) -> str:
    urls = photo.get("urls") or {}
    raw = urls.get("raw") or urls.get("full") or urls.get("regular")
    if not raw:
        return ""
    # Request a vertical-friendly size for 9:16 Ken Burns.
    sep = "&" if "?" in raw else "?"
    return "{0}{1}w=1080&h=1920&fit=crop&crop=entropy".format(raw, sep)


def _trigger_download(photo: dict) -> None:
    loc = (photo.get("links") or {}).get("download_location") or ""
    if not loc:
        return
    try:
        requests.get(loc, headers=_headers(), timeout=15).raise_for_status()
    except Exception as exc:
        print("[photos] download ping failed: {0}".format(exc))


def _download(photo: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = _pick_url(photo)
    if not url:
        raise RuntimeError("Unsplash photo has no download URL")
    _trigger_download(photo)
    with requests.get(
        url,
        stream=True,
        timeout=90,
        headers={"User-Agent": "faceless-pipeline/1.0"},
    ) as r:
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                fh.write(chunk)
    if out_path.stat().st_size < 2000:
        raise RuntimeError("downloaded photo too small")
    return out_path


def choose_photo(queries, need, setting, used_ids) -> dict:
    candidates = {}
    for query in queries:
        try:
            photos = _search(query)
        except Exception as exc:
            print("[photos] search failed '{0}': {1}".format(query, exc))
            continue
        for photo in photos:
            pid = photo.get("id")
            if not pid or pid in used_ids:
                continue
            shaped = _as_video_shape(photo)
            sc = score_video(shaped, query, need, setting)
            if sc <= 0 or not _pick_url(photo):
                continue
            user = (photo.get("user") or {})
            row = {
                "id": pid,
                "score": sc,
                "query": query,
                "slug": shaped["tags"][:80],
                "photo": photo,
                "credit": "{0}".format(user.get("name") or user.get("username") or "Unsplash"),
                "page": (photo.get("links") or {}).get("html") or "",
            }
            prev = candidates.get(pid)
            if prev is None or sc > prev["score"]:
                candidates[pid] = row
    if not candidates:
        return None
    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)
    kept = [r for r in ranked if r["score"] >= 3.0]
    return (kept or ranked)[0]


def generate_photo_storyboard(
    part: dict,
    out_dir: Path,
    duration: float,
    words: list,
    stem: str = "photos",
) -> list:
    """
    One Unsplash still per beat. Returns [{path, start, end, beat}, ...]
    for assemble_from_images.
    """
    from image_engine import visual_beat_times
    from tts_engine import for_speech

    _require_key()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    setting = (part.get("broll_query") or "").strip()
    keys = _visual_keys(part)
    cap = int(getattr(config, "BROLL_CLIP_COUNT", 12) or 12)
    beats = keys[:cap] if keys else _beat_list(part, duration)
    plan = []
    for beat in beats:
        queries, need = queries_for_beat(beat, setting)
        extras = [q.strip() for q in str(beat).split("|") if q.strip()] if keys else []
        if extras:
            queries = extras + [q for q in queries if q not in extras]
            for extra in extras:
                need |= _tokens(extra)
        plan.append({"beat": beat, "queries": queries[:6], "need": sorted(need)})

    spoken = for_speech(part["text"])
    times = visual_beat_times(spoken, len(plan), words or [], duration)
    manifest_path = out_dir / "{0}_photos.json".format(stem)
    existing = sorted(out_dir.glob("{0}_photo_*.jpg".format(stem)))
    if existing and manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text())
            if (
                saved.get("algo") == ALGO_VERSION
                and saved.get("provider") == "unsplash"
                and saved.get("beats") == [p["beat"] for p in plan]
                and len(existing) >= len(plan)
            ):
                print("[photos] reusing {0} still(s) for {1}".format(len(plan), stem))
                panels = []
                for i, item in enumerate(plan):
                    start, end = times[i]
                    panels.append({
                        "path": existing[i],
                        "start": start,
                        "end": end,
                        "beat": item["beat"],
                    })
                if panels:
                    panels[-1]["end"] = times[-1][1]
                return panels
        except Exception:
            pass

    for old in existing:
        try:
            old.unlink()
        except OSError:
            pass

    used = set()
    panels = []
    records = []
    last = None
    for i, item in enumerate(plan, start=1):
        dest = out_dir / "{0}_photo_{1}.jpg".format(stem, i)
        start, end = times[i - 1]
        picked = choose_photo(item["queries"], set(item["need"]), setting, used)
        if picked is None:
            print("[photos] beat {0} no match ({1!r})".format(i, item["beat"][:60]))
            if last is None:
                continue
            panels.append({
                "path": last,
                "start": start,
                "end": end,
                "beat": item["beat"],
            })
            records.append({"beat": item["beat"], "reused_previous": True})
            continue
        reuse = dest.exists() and dest.stat().st_size > 2000 and os.getenv("REUSE_IMAGES") == "1"
        if reuse:
            print("[photos] reusing {0}".format(dest.name))
        else:
            _download(picked["photo"], dest)
        used.add(picked["id"])
        last = dest
        panels.append({
            "path": dest,
            "start": start,
            "end": end,
            "beat": item["beat"],
        })
        records.append({
            "file": dest.name,
            "beat": item["beat"],
            "query": picked["query"],
            "slug": picked["slug"],
            "score": round(picked["score"], 2),
            "unsplash_id": picked["id"],
            "credit": picked["credit"],
            "page": picked["page"],
        })
        print(
            "[photos] beat {0}/{1} score={2:.1f} q={3!r} — {4}".format(
                i, len(plan), picked["score"], picked["query"], picked["credit"],
            )
        )

    if panels:
        panels[-1]["end"] = times[-1][1]
    manifest_path.write_text(json.dumps({
        "algo": ALGO_VERSION,
        "provider": "unsplash",
        "setting": setting,
        "beats": [p["beat"] for p in plan],
        "photos": records,
        "note": "Credit photographers when you publish (Unsplash guidelines).",
    }, indent=2))
    return panels

"""
Turn parsed stage cues into short Epidemic SFX overlays.

Without EPIDEMIC_API_KEY the cues still leave the narration (pauses only).
"""

from __future__ import annotations

import re
from pathlib import Path

import config
from epidemic_engine import EpidemicError, configured, download_to, list_tracks
from script_text import parse_script


def overlays_for_script(text: str, duration: float, dest_dir: Path = None) -> list:
    pack = parse_script(text)
    spoken = pack["spoken"] or "x"
    total = max(len(spoken), 1)
    chars = 0
    planned = []
    for seg in pack["segments"]:
        if seg["kind"] == "speech":
            chars += len(seg.get("text") or "")
            continue
        if seg["kind"] != "sfx" or not seg.get("query"):
            continue
        start = max(0.0, float(duration) * chars / total)
        planned.append((start, seg["query"]))
    if not planned:
        return []
    if not configured():
        queries = ", ".join(sorted({q for _, q in planned}))
        print("[sfx] cues need EPIDEMIC_API_KEY ({0}) — pauses only".format(queries))
        return []

    dest_dir = Path(dest_dir or getattr(config, "SFX_DIR", config.ASSETS / "sfx"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for start, query in planned:
        path = fetch_sfx(query, dest_dir)
        if path is None:
            continue
        overlays.append({"path": path, "start": start, "volume": 0.34})
        print("[sfx] {0} @ {1:.1f}s".format(path.name, start))
    return overlays


def fetch_sfx(query: str, dest_dir: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", (query or "").lower()).strip("_") or "sfx"
    dest = Path(dest_dir) / (slug + ".mp3")
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    try:
        data = list_tracks(query, limit=5, kind="sfx")
    except EpidemicError as exc:
        print("[sfx] search failed {0!r}: {1}".format(query, exc))
        return None
    tracks = data.get("tracks") or []
    if not tracks:
        print("[sfx] no Epidemic hit for {0!r}".format(query))
        return None
    try:
        return download_to(tracks[0]["id"], dest, kind="sfx")
    except EpidemicError as exc:
        print("[sfx] download failed {0!r}: {1}".format(query, exc))
        return None

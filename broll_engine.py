"""
Pexels stock video, ranked against the spoken story.

The old path searched portrait-only and took the first hit, so
'zanzibar aerial' became a kayak vlog. This version:

  1. Builds short stock queries from each narration beat
  2. Searches without an orientation filter (we crop to 9:16 later)
  3. Scores every candidate by how well its Pexels slug matches the beat
  4. Drops clips whose slugs are tourism, gym, subway, Tokyo, etc.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests

import config

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
ALGO_VERSION = 3

_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "with",
    "from", "into", "over", "by", "it", "its", "is", "was", "were", "be", "been",
    "this", "that", "they", "them", "their", "you", "your", "we", "our", "not",
    "do", "does", "did", "doesn", "don", "didn", "would", "could", "should",
    "because", "then", "than", "some", "any", "all", "just", "only", "very",
    "called", "call", "here", "what", "when", "if", "so", "as", "up", "out",
}

# Slug substrings that mean the clip is the wrong world for these stories.
_REJECT = (
    "kayak", "yoga", "gym", "workout", "fitness", "subway", "metro-train",
    "tokyo", "shibuya", "hawaii", "hawaiian", "maldives", "bali", "santorini",
    "wedding", "bride", "cooking", "restaurant", "burger", "pizza", "office",
    "laptop", "airplane", "airport", "highway", "traffic", "concert", "nightclub",
    "christmas", "snowy-village", "skiing", "russian-girl", "cliff-jumping",
    "cancer", "room-service", "trolley", "fashion", "makeup", "shopping",
    "cruise", "yacht", "surfing", "snorkeling", "diving-underwater",
    "catwalk", "studio", "timelapse-city", "new-york", "times-square",
    "sky-lantern", "chinese-lantern", "floating-lantern", "lantern-festival",
    "lantern-release", "homeless", "sidewalk-with-a-cup",
    "woman-exploring", "beautiful-woman", "beautiful-russian",
    "party", "nightlife-on-vibrant", "street-party",
    "night-drive", "dashboard", "car-interior", "curvy-road",
)

# Map spoken story moments to queries Pexels actually has, plus tokens we
# require/boost in the result slug. Never search for the monster's name.
_BEAT_RULES = (
    (r"zanzibar|pemba|unguja|tanzania",
     ["zanzibar aerial village", "tanzania coastal village", "aerial african village"],
     {"zanzibar", "tanzania", "africa", "village", "aerial", "coast", "island"}),
    (r"sleeping outside|slept outdoors|sleep outdoors|mattresses|streets are full|sleep alone",
     ["african village huts", "rural african village aerial", "people sitting village africa"],
     {"africa", "village", "rural", "huts", "community"}),
    (r"lantern",
     ["oil lantern darkness", "rustic lantern dark", "candle lantern dark"],
     {"lantern", "oil", "candle", "dark", "darkness"}),
    (r"knock|locked door|lock that door|doorway|does not need to",
     ["dark wooden door", "old doorway night", "closing door dark"],
     {"door", "doorway", "wooden", "dark"}),
    (r"bedroom|through the walls|coming through|empty room|wait in the dark",
     ["dark bedroom night", "empty dark room", "man sitting on bed night"],
     {"bedroom", "bed", "room", "dark", "night"}),
    (r"bat wing|leathery|popobawa|demon|burning metal|one eye",
     ["full moon night clouds", "dark storm clouds night", "candle flame dark"],
     {"moon", "night", "cloud", "dark", "candle", "storm"}),
    (r"newspaper|headlines|printed the name|the news|real newspapers",
     ["old newspaper closeup", "hands reading newspaper", "printing newspaper"],
     {"newspaper", "paper", "print", "headline"}),
    (r"election|unrest|political|mass panic|mass hysteria|land disputes",
     ["african village gathering", "rural africa crowd", "people gathering village"],
     {"africa", "village", "crowd", "gathering", "people"}),
    (r"hospital|police",
     ["dark empty hallway", "dim corridor night"],
     {"corridor", "hallway", "dark", "dim"}),
    (r"woke with bruises|marks across his|pinned him down|bruises he could",
     ["man sitting on bed night", "person awake in bed dark", "insomnia night bedroom"],
     {"bed", "night", "man", "dark", "bedroom"}),
    (r"children|mats|parents sat",
     ["african village courtyard", "people sitting africa village", "rural africa family"],
     {"village", "africa", "people", "family"}),
    (r"alone|prey|never in a locked",
     ["dark empty road night", "dark village path night", "oil lantern darkness"],
     {"night", "dark", "road", "path", "lantern"}),
    (r"midnight|after midnight|night gets worse|lights left on",
     ["african village night", "rural night africa", "fire village night africa"],
     {"night", "village", "africa", "dark"}),
    (r"island became a crowd|whole village|next village",
     ["african village aerial", "rural african village", "tanzania village"],
     {"village", "africa", "aerial", "rural"}),
    # Arctic / Qallupilluk
    (r"arctic|sea ice|qallupilluk|iglu",
     ["arctic sea ice aerial", "frozen ocean ice", "polar ice aerial"],
     {"arctic", "ice", "frozen", "ocean", "polar"}),
    (r"footprint|crack in the ice|open leads|tide crack",
     ["cracked sea ice", "ice breaking ocean", "frozen ice crack"],
     {"ice", "crack", "frozen", "ocean"}),
    (r"humming|shoreline|open water",
     ["arctic shoreline", "polar coast ice", "frozen ocean dusk"],
     {"arctic", "ice", "ocean", "coast", "shore"}),
    (r"dogs refuse|sled",
     ["sled dogs arctic", "huskies snow"],
     {"dog", "sled", "husky", "snow", "arctic"}),
    (r"northern|aurora",
     ["northern lights ice", "aurora snow"],
     {"aurora", "northern", "lights", "snow"}),
    (r"child.*ice|boy near|wander too close",
     ["child walking snow", "person walking frozen lake"],
     {"snow", "ice", "walking", "winter"}),
)


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _slug(video: dict) -> str:
    url = video.get("url") or ""
    return url.split("/video/")[-1].rstrip("/")


def _headers() -> dict:
    if not config.PEXELS_API_KEY:
        raise RuntimeError(
            "No PEXELS_API_KEY set. Get a free key at https://www.pexels.com/api/ "
            "and add it to a .env file as PEXELS_API_KEY=your_key_here"
        )
    return {"Authorization": config.PEXELS_API_KEY}


def queries_for_beat(beat: str, setting: str = "") -> tuple:
    """Return (queries, need_tokens) for one spoken beat.

    Scene rules (lantern, door, newspaper) outrank place-name rules so
    'Pemba + lanterns' searches for lanterns, not another aerial village.
    """
    scene_q, place_q = [], []
    need = set()
    place_pat = r"zanzibar|pemba|unguja|tanzania|arctic|sea ice|qallupilluk|iglu"
    for pattern, qs, tokens in _BEAT_RULES:
        if not re.search(pattern, beat, flags=re.I):
            continue
        bucket = place_q if re.search(place_pat, pattern, flags=re.I) else scene_q
        for q in qs:
            if q not in bucket:
                bucket.append(q)
        need |= tokens
    queries = (scene_q + place_q)[:4]
    if setting:
        setting = setting.strip()
        need |= _tokens(setting)
        if setting and setting not in queries:
            queries.append(setting)
    if not queries:
        queries = [setting or "dark rural night"]
        need |= {"night", "dark"}
    return queries[:5], need


def _rejected(slug: str, allow: set = None) -> str:
    hay = slug.lower()
    allow = {str(a).lower() for a in (allow or set()) if a}
    for bad in _REJECT:
        if bad in hay and bad not in allow:
            return bad
    return ""


def score_video(video: dict, query: str, need: set, setting: str) -> float:
    slug = _slug(video)
    hay = _tokens(slug.replace("-", " "))
    if not hay:
        return -50.0
    allow = _tokens(query) | set(need or ()) | _tokens(setting)
    banned = _rejected(slug, allow)
    if banned:
        return -100.0

    qtoks = _tokens(query)
    stoks = _tokens(setting)
    score = 0.0
    score += 4.0 * len(qtoks & hay)
    score += 3.0 * len(need & hay)
    score += 2.0 * len(stoks & hay)
    if stoks and not (stoks & hay) and not (need & hay):
        score -= 1.5

    nightish = bool(need & {"night", "dark", "darkness", "midnight", "lantern", "candle"})
    if nightish:
        if hay & {"night", "dark", "darkness", "candle", "lantern", "moon", "midnight"}:
            score += 2.0
        if hay & {"sunset", "sunrise", "beach", "sunny", "daytime"}:
            score -= 2.0

    duration = float(video.get("duration") or 0)
    if duration < 6:
        score -= 3.0
    elif 8 <= duration <= 50:
        score += 1.0
    return score


def _search(query: str, per_page: int = 30) -> list:
    params = {"query": query, "per_page": per_page}
    resp = requests.get(PEXELS_SEARCH_URL, headers=_headers(), params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("videos") or []


def _pick_file(video: dict):
    files = [f for f in (video.get("video_files") or []) if f.get("link")]
    if not files:
        return None
    ranked = []
    for f in files:
        h = int(f.get("height") or 0)
        w = int(f.get("width") or 0)
        if min(w, h) < 480:
            continue
        # Prefer ~1080 on the short side so we crop cleanly without 4K downloads.
        ranked.append((abs(min(w, h) - 1080), -max(w, h), f))
    ranked.sort()
    return ranked[0][2] if ranked else files[0]


def choose_clip(queries, need, setting, used_ids) -> dict:
    """Search every query, score all unique videos, return the best."""
    candidates = {}
    for query in queries:
        try:
            videos = _search(query)
        except Exception as exc:
            print(f"[broll] search failed '{query}': {exc}")
            continue
        for video in videos:
            vid = video.get("id")
            if vid in used_ids:
                continue
            sc = score_video(video, query, need, setting)
            file_info = _pick_file(video)
            if file_info is None or sc <= 0:
                continue
            row = {
                "id": vid,
                "score": sc,
                "query": query,
                "slug": _slug(video),
                "duration": video.get("duration"),
                "file": file_info,
            }
            prev = candidates.get(vid)
            if prev is None or sc > prev["score"]:
                candidates[vid] = row
    if not candidates:
        return None
    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)
    kept = [r for r in ranked if r["score"] >= 3.0]
    return (kept or ranked)[0]


def _download(file_info, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(file_info["link"], stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                fh.write(chunk)
    return out_path


def _visual_keys(part: dict) -> list:
    keys = part.get("broll_queries") or part.get("image_prompts") or []
    return [str(k).strip() for k in keys if str(k).strip()]


def _beat_list(part: dict, duration: float) -> list:
    from image_engine import pack_beats
    from tts_engine import for_speech
    target = int(max(6, min(12, round(float(duration) / 14.0))))
    text = for_speech(part["text"])
    beats = pack_beats(text, target=target)
    return beats or [text[:180]]


def fetch_story_broll(part: dict, dest_dir: Path, stem: str, duration: float) -> list:
    """
    One scored Pexels clip per spoken beat. Reuses files only when the
    algorithm version and beat list still match.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
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

    manifest_path = dest_dir / f"{stem}_broll.json"
    existing = sorted(dest_dir.glob(f"{stem}_broll_*.mp4"))
    if existing and manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text())
            if (
                saved.get("algo") == ALGO_VERSION
                and saved.get("beats") == [p["beat"] for p in plan]
                and len(existing) >= len(plan)
            ):
                print(f"[broll] reusing {len(plan)} scored clip(s) for {stem}")
                return existing[: len(plan)]
        except Exception:
            pass

    for old in existing:
        try:
            old.unlink()
        except OSError:
            pass

    used = set()
    clips = []
    records = []
    last = None
    for i, item in enumerate(plan, start=1):
        dest = dest_dir / f"{stem}_broll_{i}.mp4"
        picked = choose_clip(item["queries"], set(item["need"]), setting, used)
        if picked is None:
            print(f"[broll] beat {i} no scored match ({item['beat'][:60]!r})")
            if last is not None:
                clips.append(last)
                records.append({"beat": item["beat"], "reused_previous": True})
            continue
        _download(picked["file"], dest)
        used.add(picked["id"])
        last = dest
        clips.append(dest)
        records.append({
            "file": dest.name,
            "beat": item["beat"],
            "query": picked["query"],
            "slug": picked["slug"],
            "score": round(picked["score"], 2),
            "pexels_id": picked["id"],
        })
        print(
            f"[broll] beat {i}/{len(plan)} score={picked['score']:.1f} "
            f"q={picked['query']!r} -> {picked['slug'][:70]}"
        )

    manifest_path.write_text(json.dumps({
        "algo": ALGO_VERSION,
        "setting": setting,
        "beats": [p["beat"] for p in plan],
        "clips": records,
    }, indent=2))
    return clips


def fetch_broll(query: str, out_path: Path, min_duration: int = 8, used_ids=None) -> Path:
    """Back-compat single search (scored, no portrait filter)."""
    used_ids = used_ids if used_ids is not None else set()
    picked = choose_clip([query], _tokens(query), query, used_ids)
    if picked is None:
        print(f"[broll] no suitable clip found for '{query}'")
        return None
    used_ids.add(picked["id"])
    path = _download(picked["file"], out_path)
    print(f"[broll] downloaded '{query}' -> {Path(path).name} ({picked['slug'][:60]})")
    return path


def fetch_broll_clips(queries, dest_dir: Path, stem: str, count: int = None) -> list:
    """Back-compat: treat the query list as the beat plan with no story text."""
    dummy = {
        "text": " ".join(
            (q if isinstance(q, str) else " ".join(q)) for q in (queries or [])
        ) or "dark night",
        "broll_query": "",
        "broll_queries": queries or [],
    }
    return fetch_story_broll(dummy, dest_dir, stem, duration=60.0)

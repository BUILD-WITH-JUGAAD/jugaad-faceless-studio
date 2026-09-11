"""
Semantic visual query generation for VisualBeat objects.

Phase 4: narration → subject/action/location/mood/objects + visual_intent + queries.

Does not fetch assets, call providers, or touch run_pipeline.
No external LLM — lightweight heuristics over the beat narration.
"""

from __future__ import annotations

import re
from dataclasses import replace

from visual_beat import VisualBeat

_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "with",
    "from", "into", "over", "by", "it", "its", "is", "was", "were", "be", "been",
    "being", "this", "that", "they", "them", "their", "he", "him", "his", "she",
    "her", "hers", "you", "your", "we", "our", "not", "no", "do", "does", "did",
    "would", "could", "should", "because", "then", "than", "some", "any", "all",
    "just", "only", "very", "called", "here", "what", "when", "where", "who",
    "if", "so", "as", "up", "out", "about", "across", "after", "before", "while",
    "there", "had", "has", "have", "but", "also", "even", "still", "again",
    "something", "someone", "anything", "everything", "nothing", "himself",
    "herself", "itself", "themselves", "through", "during", "without", "within",
}

# Single-token queries that are almost never useful alone.
_GENERIC_ONLY = {
    "horror", "history", "scary", "ocean", "person", "city", "technology",
    "people", "dark", "night", "light", "room", "house", "man", "woman",
    "thing", "things", "world", "time", "way", "place", "area", "scene",
}

_ROLES = (
    "fisherman", "hacker", "firefighter", "fireman", "soldier", "doctor",
    "nurse", "police", "officer", "detective", "farmer", "villager", "villagers",
    "parent", "parents", "child", "children", "boy", "girl", "man", "woman",
    "stranger", "sailor", "captain", "pilot", "driver", "worker", "teacher",
    "student", "monk", "priest", "king", "queen", "soldier", "guard", "thief",
    "family", "families", "crowd", "neighbor", "neighbors", "narrator",
)

_ACTION_STEMS = (
    "walk", "walking", "ran", "run", "running", "carry", "carrying", "carried",
    "notice", "noticed", "noticing", "look", "looking", "looked", "stare",
    "staring", "enter", "entering", "entered", "open", "opening", "opened",
    "close", "closing", "closed", "return", "returned", "returning", "hold",
    "holding", "held", "find", "found", "finding", "search", "searching",
    "flee", "fleeing", "escape", "escaping", "chase", "chasing", "hide",
    "hiding", "sleep", "sleeping", "slept", "wake", "waking", "woke", "sit",
    "sitting", "stand", "standing", "wait", "waiting", "watch", "watching",
    "read", "reading", "write", "writing", "type", "typing", "burn", "burning",
    "fight", "fighting", "scream", "screaming", "whisper", "whispering",
    "knock", "knocking", "crawl", "crawling", "drag", "dragging", "point",
    "pointing", "reach", "reaching", "grab", "grabbing", "drop", "dropping",
    "examine", "examining", "inspect", "inspecting", "approach", "approaching",
    "leave", "leaving", "arrive", "arriving", "arrived", "gather", "gathering",
    "print", "printed", "printing", "spread", "spreading",
)

_LOCATIONS = (
    "village", "villages", "town", "city", "street", "streets", "road", "alley",
    "beach", "shore", "coast", "coastal", "ocean", "sea", "island", "room",
    "bedroom", "house", "home", "building", "apartment", "courtyard", "forest",
    "woods", "cave", "tunnel", "bridge", "harbor", "harbour", "dock", "boat",
    "ship", "mosque", "hospital", "hallway", "corridor", "doorway", "door",
    "screen", "monitor", "office", "lab", "laboratory", "kitchen", "basement",
    "attic", "rooftop", "market", "temple", "church", "school", "station",
    "railway", "tracks", "ice", "arctic", "desert", "mountain", "river", "lake",
    "field", "farm", "harbor", "port", "wall", "walls", "mat", "mats", "sand",
)

_OBJECTS = (
    "object", "artifact", "lantern", "candle", "door", "screen", "monitor",
    "computer", "laptop", "phone", "knife", "key", "keys", "letter", "newspaper",
    "book", "bag", "box", "boat", "net", "rope", "boots", "shoe", "shoes",
    "mattress", "mattresses", "radio", "flashlight", "torch", "weapon", "mask",
    "photograph", "photo", "warning", "message", "bruise", "bruises", "wing",
    "wings", "eye", "footprint", "footprints", "crack", "ice", "sand",
)

_TIME = (
    (r"\bbefore dawn\b", "before dawn"),
    (r"\bat dawn\b|\bdawn\b", "dawn"),
    (r"\bat dusk\b|\bdusk\b|\btwilight\b", "dusk"),
    (r"\bmidnight\b", "midnight"),
    (r"\bafter midnight\b", "after midnight"),
    (r"\bat night\b|\bnight\b|\bnighttime\b", "night"),
    (r"\bin the morning\b|\bmorning\b", "morning"),
    (r"\bin the evening\b|\bevening\b", "evening"),
    (r"\bafternoon\b", "afternoon"),
    (r"\bsunrise\b", "sunrise"),
    (r"\bsunset\b", "sunset"),
    (r"\b\d{3,4}\b", None),  # year like 1995 — captured specially
)

_MOOD = (
    (r"\bnervous\b|\banxious\b|\buneasy\b", "uneasy"),
    (r"\bafraid\b|\bscared\b|\bterrified\b|\bfear\b", "fearful"),
    (r"\bpanic\b|\bhysteria\b", "panicked"),
    (r"\bstrange\b|\bunusual\b|\bweird\b|\bodd\b", "unsettling"),
    (r"\bsilent\b|\bquiet\b", "quiet"),
    (r"\bdark\b|\bdarkness\b", "dark"),
    (r"\btense\b|\btense\b", "tense"),
    (r"\blonely\b|\balone\b", "lonely"),
    (r"\bcalm\b|\bpeaceful\b", "calm"),
)

_MOTION_ACTIONS = {
    "walk", "walking", "ran", "run", "running", "carry", "carrying", "flee",
    "fleeing", "chase", "chasing", "crawl", "crawling", "drag", "dragging",
    "approach", "approaching", "enter", "entering", "escape", "escaping",
    "return", "returning", "arrive", "arriving", "gather", "gathering",
}

_DETAIL_HINTS = re.compile(
    r"\b(close[- ]?up|on the screen|in (?:his|her|their) hands?|held|holding|"
    r"bruises?|warning|message|footprint)\b",
    re.I,
)


def _tokens(text: str) -> list:
    return [
        t.lower()
        for t in re.findall(r"[A-Za-z][A-Za-z']*", text or "")
        if t.lower() not in _STOP and len(t) > 1
    ]


def _unique(items) -> list:
    out = []
    seen = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _phrase_match(text: str, vocab: tuple) -> list:
    low = " " + re.sub(r"[^a-z0-9']+", " ", (text or "").lower()) + " "
    low = re.sub(r"\s+", " ", low)
    found = []
    for word in vocab:
        if " {0} ".format(word) in low:
            found.append(word)
    return _unique(found)


def _extract_subjects(text: str) -> list:
    found = _phrase_match(text, _ROLES)
    # "the <noun>" when noun looks like a role/person word already covered;
    # also keep capitalized proper-ish tokens that are not sentence starts alone.
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\b", text or ""):
        word = m.group(1)
        if word.lower() in _STOP:
            continue
        # Skip if it's the first word of the beat (likely sentence case).
        if m.start() == 0:
            continue
        found.append(word.lower())
    # "the hacker" / "a fisherman" patterns
    for m in re.finditer(
        r"\b(?:the|a|an)\s+([a-z]{3,})(?:\s+([a-z]{3,}))?",
        (text or "").lower(),
    ):
        a, b = m.group(1), m.group(2)
        if a in _ROLES:
            found.append(a)
        elif a not in _STOP and a in ("person", "figure", "silhouette"):
            found.append(a)
        if b and b in _ROLES:
            found.append(b)
    return _unique(found)[:4]


def _extract_actions(text: str) -> list:
    low = (text or "").lower()
    found = []
    for stem in _ACTION_STEMS:
        if re.search(r"\b{0}\b".format(re.escape(stem)), low):
            # Prefer -ing forms in output when both exist.
            found.append(stem)
    # Normalize past/present to a readable action word for queries.
    norm = []
    mapping = {
        "ran": "running", "run": "running", "walk": "walking",
        "carry": "carrying", "carried": "carrying", "notice": "noticing",
        "noticed": "noticing", "look": "looking", "looked": "looking",
        "enter": "entering", "entered": "entering", "return": "returning",
        "returned": "returning", "hold": "holding", "held": "holding",
        "find": "finding", "found": "finding", "sleep": "sleeping",
        "slept": "sleeping", "wake": "waking", "woke": "waking",
        "sit": "sitting", "stand": "standing", "wait": "waiting",
        "watch": "watching", "burn": "burning", "chase": "chasing",
        "flee": "fleeing", "escape": "escaping", "hide": "hiding",
        "open": "opening", "opened": "opening", "close": "closing",
        "closed": "closing", "arrive": "arriving", "arrived": "arriving",
        "approach": "approaching", "examine": "examining",
        "inspect": "inspecting", "print": "printing", "printed": "printing",
        "gather": "gathering", "spread": "spreading", "type": "typing",
        "read": "reading", "write": "writing", "knock": "knocking",
        "crawl": "crawling", "drag": "dragging", "grab": "grabbing",
        "reach": "reaching", "point": "pointing", "scream": "screaming",
        "whisper": "whispering", "fight": "fighting", "search": "searching",
        "leave": "leaving",
    }
    for item in found:
        norm.append(mapping.get(item, item))
    return _unique(norm)[:4]


def _extract_locations(text: str, setting: str = "") -> list:
    found = _phrase_match(text, _LOCATIONS)
    # Prepositional snippets: "to the village", "in a dark room", "on the screen"
    for m in re.finditer(
        r"\b(?:in|on|to|toward|towards|at|inside|outside|across|near)\s+"
        r"(?:the|a|an)\s+([a-z][a-z\s]{2,40}?)(?=[,.;!?]|$)",
        (text or "").lower(),
    ):
        chunk = re.sub(r"\s+", " ", m.group(1)).strip()
        words = [w for w in chunk.split() if w not in _STOP][:4]
        if words:
            found.append(" ".join(words))
    # Setting / broll_query only fills location when the beat narration has none.
    # Explicit preservation of broll_query happens in build_queries fallbacks.
    if setting and not found:
        for tok in _phrase_match(setting, _LOCATIONS):
            found.append(tok)
        if not found:
            setting_bits = [
                w for w in _tokens(setting)
                if w not in _GENERIC_ONLY
            ][:4]
            if setting_bits:
                found.append(" ".join(setting_bits))
    return _unique(found)[:4]


def _extract_objects(text: str) -> list:
    found = _phrase_match(text, _OBJECTS)
    # "the strange object", "an unusual warning"
    for m in re.finditer(
        r"\b(?:the|a|an)\s+(?:strange|unusual|mysterious|old|wet|burning|locked)?\s*"
        r"([a-z]{3,})(?:\s+([a-z]{3,}))?",
        (text or "").lower(),
    ):
        a, b = m.group(1), m.group(2)
        if a in _OBJECTS or a in {"object", "artifact", "warning", "message"}:
            phrase = a
            if b and b not in _STOP:
                phrase = "{0} {1}".format(a, b)
            found.append(phrase.strip())
        elif b and b in _OBJECTS:
            found.append(b)
    # "on the screen" / "in his hands"
    if re.search(r"\bon the screen\b|\bcomputer screen\b", text or "", re.I):
        found.append("computer screen")
    if re.search(r"\bin (?:his|her|their) hands?\b", text or "", re.I):
        found.append("object in hands")
    return _unique(found)[:4]


def _extract_time(text: str) -> str:
    low = text or ""
    for pattern, label in _TIME:
        m = re.search(pattern, low, flags=re.I)
        if not m:
            continue
        if label is None:
            # year
            return m.group(0)
        return label
    return None


def _extract_mood(text: str) -> str:
    for pattern, label in _MOOD:
        if re.search(pattern, text or "", flags=re.I):
            return label
    return None


def _shot_prefix(text: str, objects: list, actions: list) -> str:
    if _DETAIL_HINTS.search(text or "") or (
        objects and not actions
    ):
        return "close-up of"
    if actions and any(a in _MOTION_ACTIONS or a.endswith("ing") for a in actions):
        return ""
    return ""


def build_visual_intent(
    narration: str,
    subject=None,
    action=None,
    location=None,
    time_context: str = None,
    mood: str = None,
    objects=None,
) -> str:
    """Compose a concrete viewer-facing description from extracted parts."""
    subject = list(subject or [])
    action = list(action or [])
    location = list(location or [])
    objects = list(objects or [])
    bits = []
    prefix = _shot_prefix(narration, objects, action)
    if prefix:
        bits.append(prefix)

    if subject:
        bits.append(subject[0])
    elif re.search(r"\b(he|she|they)\b", narration or "", re.I):
        # Pronoun-only beat: do not invent a person role.
        pass

    if action:
        bits.append(action[0])

    if objects:
        obj = objects[0]
        if action and action[0] in {"carrying", "holding", "finding", "examining"}:
            bits.append(obj)
        elif "screen" in obj or "warning" in obj:
            bits.append(obj)
        elif not action:
            bits.append(obj)
        else:
            bits.append(obj)

    if location:
        loc = location[0]
        if not any(loc in b for b in bits):
            # Prefer "in/on/toward" glue when natural.
            if re.search(r"\b(village|room|street|building|forest|courtyard)\b", loc):
                bits.append("in {0}".format(loc) if not loc.startswith("in ") else loc)
            elif re.search(r"\b(screen|monitor|sand|shore|beach)\b", loc):
                bits.append("on {0}".format(loc) if not loc.startswith("on ") else loc)
            else:
                bits.append(loc)

    if time_context:
        bits.append(time_context)
    if mood and mood not in {"dark"}:
        bits.append(mood)

    intent = re.sub(r"\s+", " ", " ".join(bits)).strip(" ,")
    if not intent:
        # Last resort: cleaned narration snippet — not a one-word generic.
        toks = _tokens(narration)[:8]
        intent = " ".join(toks)
    return intent.strip()


def _is_weak_query(q: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9']+", (q or "").lower()) if w]
    if not words:
        return True
    if len(words) == 1 and words[0] in _GENERIC_ONLY:
        return True
    if len(words) <= 2 and all(w in _GENERIC_ONLY or w in _STOP for w in words):
        return True
    return False


def _query_from_parts(parts: list, limit: int = 10) -> str:
    words = []
    for part in parts:
        if not part:
            continue
        for w in str(part).replace(" in ", " ").replace(" on ", " ").split():
            wl = w.lower().strip(",.")
            if not wl or wl in _STOP:
                continue
            if wl not in words:
                words.append(wl)
            if len(words) >= limit:
                break
        if len(words) >= limit:
            break
    return " ".join(words)


def build_queries(
    intent: str,
    subject=None,
    action=None,
    location=None,
    time_context: str = None,
    mood: str = None,
    objects=None,
    setting: str = "",
    explicit_queries=None,
) -> tuple:
    """
    Return (queries, fallback_queries).

    Primary queries are specific. Fallbacks broaden gradually but stay relevant.
    Explicit user queries are preserved at the front of `queries`.
    """
    subject = list(subject or [])
    action = list(action or [])
    location = list(location or [])
    objects = list(objects or [])
    explicit = []
    for raw in explicit_queries or []:
        for piece in str(raw).split("|"):
            piece = piece.strip()
            if piece:
                explicit.append(piece)

    primary = _query_from_parts([
        subject[0] if subject else "",
        action[0] if action else "",
        objects[0] if objects else "",
        location[0] if location else "",
        time_context or "",
        mood or "",
    ])
    if _is_weak_query(primary) and intent:
        primary = _query_from_parts(intent.split(), limit=10)

    # Fallback 1: drop mood/time
    fb1 = _query_from_parts([
        subject[0] if subject else "",
        action[0] if action else "",
        objects[0] if objects else "",
        location[0] if location else "",
    ])
    # Fallback 2: subject + location or action + object
    fb2_parts = []
    if subject and location:
        fb2_parts = [subject[0], location[0]]
    elif subject and objects:
        fb2_parts = [subject[0], objects[0]]
    elif action and location:
        fb2_parts = [action[0], location[0]]
    elif action and objects:
        fb2_parts = [action[0], objects[0]]
    elif location:
        fb2_parts = [location[0], time_context or mood or ""]
    else:
        fb2_parts = (intent or primary).split()[:5]
    fb2 = _query_from_parts(fb2_parts)

    # Fallback 3: setting / broader place if available
    fb3 = ""
    if setting and not _is_weak_query(setting):
        fb3 = setting.strip()
    elif location:
        fb3 = _query_from_parts([location[0], time_context or ""]) 

    queries = _unique(explicit + ([primary] if primary and not _is_weak_query(primary) else []))
    # If primary duplicated an explicit, still fine via _unique.
    if not queries and intent and not _is_weak_query(intent):
        queries = [_query_from_parts(intent.split())]

    fallbacks = []
    for candidate in (fb1, fb2, fb3):
        if not candidate or _is_weak_query(candidate):
            continue
        if candidate.lower() in {q.lower() for q in queries}:
            continue
        if candidate.lower() in {f.lower() for f in fallbacks}:
            continue
        fallbacks.append(candidate)
        if len(fallbacks) >= 3:
            break

    # Always preserve exact setting/broll_query in fallbacks when provided.
    if setting and setting.strip():
        s = setting.strip()
        if s.lower() not in {q.lower() for q in queries} and s.lower() not in {
            f.lower() for f in fallbacks
        }:
            if len(fallbacks) >= 3:
                fallbacks[-1] = s
            else:
                fallbacks.append(s)

    return queries[:6], fallbacks[:3]


def prefer_asset_types(narration: str, action=None, objects=None) -> list:
    """Prefer video/image only when narration supports a clear bias."""
    action = list(action or [])
    objects = list(objects or [])
    motion = any(a in _MOTION_ACTIONS for a in action)
    detail = bool(_DETAIL_HINTS.search(narration or ""))
    if motion and not detail:
        return ["video", "image"]
    if detail and not motion:
        return ["image", "video"]
    if objects and any("screen" in o or "bruise" in o or "warning" in o for o in objects):
        return ["image", "video"]
    return ["video", "image"]


def analyze_narration(narration: str, setting: str = "") -> dict:
    """Extract semantic fields from one beat's narration (no invented facts)."""
    text = (narration or "").strip()
    subject = _extract_subjects(text)
    action = _extract_actions(text)
    location = _extract_locations(text, setting=setting)
    objects = _extract_objects(text)
    time_context = _extract_time(text)
    mood = _extract_mood(text)
    intent = build_visual_intent(
        text,
        subject=subject,
        action=action,
        location=location,
        time_context=time_context,
        mood=mood,
        objects=objects,
    )
    return {
        "subject": subject,
        "action": action,
        "location": location,
        "time_context": time_context,
        "mood": mood,
        "objects": objects,
        "visual_intent": intent,
    }


def enrich_beat(
    beat: VisualBeat,
    *,
    setting: str = "",
    explicit_queries=None,
) -> VisualBeat:
    """Return a new VisualBeat with semantic fields and queries filled."""
    analysis = analyze_narration(beat.narration, setting=setting)
    queries, fallbacks = build_queries(
        analysis["visual_intent"],
        subject=analysis["subject"],
        action=analysis["action"],
        location=analysis["location"],
        time_context=analysis["time_context"],
        mood=analysis["mood"],
        objects=analysis["objects"],
        setting=setting,
        explicit_queries=explicit_queries,
    )
    prefs = prefer_asset_types(
        beat.narration,
        action=analysis["action"],
        objects=analysis["objects"],
    )
    return replace(
        beat,
        subject=analysis["subject"],
        action=analysis["action"],
        location=analysis["location"],
        time_context=analysis["time_context"],
        mood=analysis["mood"],
        objects=analysis["objects"],
        visual_intent=analysis["visual_intent"],
        queries=queries,
        fallback_queries=fallbacks,
        asset_type_preference=prefs,
    )


def _explicit_keys_from_part(part: dict) -> list:
    if not part:
        return []
    keys = part.get("broll_queries") or part.get("image_prompts") or []
    out = []
    for key in keys:
        s = str(key or "").strip()
        if s:
            out.append(s)
    return out


def enrich_visual_beats(beats: list, part: dict = None) -> list:
    """
    Enrich a chronological VisualBeat list.

    Explicit PART signals:
      - broll_query / setting → preserved on every beat (fallback + location bias)
      - broll_queries[i] → preserved as leading queries for beat i when present
    Never discards the user's explicit visual direction.
    """
    part = part or {}
    setting = (part.get("broll_query") or part.get("setting") or "").strip()
    keys = _explicit_keys_from_part(part)
    enriched = []
    prev_primary = ""
    for i, beat in enumerate(beats or []):
        explicit = []
        if i < len(keys):
            explicit.append(keys[i])
        # Always pass setting so it is preserved in fallbacks.
        row = enrich_beat(beat, setting=setting, explicit_queries=explicit)
        # Light diversity nudge: if adjacent primary query identical, prefer
        # a fallback that differs when narration changed.
        if (
            row.queries
            and prev_primary
            and row.queries[0].lower() == prev_primary.lower()
            and (beat.narration or "").strip().lower()
            != ((beats[i - 1].narration if i else "") or "").strip().lower()
        ):
            for fb in row.fallback_queries:
                if fb.lower() != prev_primary.lower():
                    row = replace(
                        row,
                        queries=_unique([fb] + row.queries),
                        fallback_queries=[
                            q for q in row.fallback_queries if q.lower() != fb.lower()
                        ][:3],
                    )
                    break
        if row.queries:
            prev_primary = row.queries[0]
        enriched.append(row)
    return enriched

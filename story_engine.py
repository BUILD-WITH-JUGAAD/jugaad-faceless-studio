"""
Expand a short prompt into spoken narration.

Providers:
  ollama — local, no key (default)
  openai — Settings / OPENAI_API_KEY (optional)

Requests go through the studio backend. Keys never reach the browser.

Story mode returns a pack with spoken text plus a shot plan optimized for
AI video (Pixazo): short clips with one clear progressing action each.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter

import requests

import config

_OLLAMA_CACHE = {"t": 0.0, "status": None}
_OLLAMA_CACHE_TTL = 8.0

_STORY_INSTRUCTION = (
    "You are JUGAAD Story Engine for short faceless videos.\n"
    "Return valid JSON only. No markdown. No preamble.\n"
    "\n"
    "Schema:\n"
    "{\n"
    '  "title": "3-6 words",\n'
    '  "story": "spoken voiceover only",\n'
    '  "setting": "short place phrase e.g. abandoned hospital night",\n'
    '  "setting_search_keys": ["2-4 short stock search phrases"],\n'
    '  "visual_search_keys": ["5-8 short stock search phrases"],\n'
    '  "characters": [{"name": "", "look": "short appearance + clothing"}],\n'
    '  "shots": [\n'
    '    {"shot_number": 1, "duration": 5, "action": "", "camera": ""}\n'
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- story: short clear spoken sentences. No camera notes. No Okay/Here's a story.\n"
    "- setting_search_keys + visual_search_keys: 3-10 words each, no full sentences.\n"
    "- characters: 1-2 entries. look is one short line (who + clothes).\n"
    "- shots: exactly the count requested. Each duration is 5.\n"
    "- Each shot = ONE clear physical action that changes what is on screen.\n"
    "- action: 1-2 short sentences (what happens). camera: 1 short sentence.\n"
    "- Keep looks, place, and time consistent across shots. Sequence them in order.\n"
    "- No dialogue in action/camera. No vague lines like cinematic scene or something mysterious happens.\n"
    "- Prefer: walks, stops, turns, opens, picks up, drops, runs, looks behind, steps back, enters, chase.\n"
    "- Do NOT write long cinematic paragraphs. Do NOT invent visual_prompt fields.\n"
    "\n"
    "Example shot style:\n"
    '{"shot_number":1,"duration":5,'
    '"action":"Man walks through hospital corridor.",'
    '"camera":"Camera follows behind him."}\n'
)

_IMAGE_INSTRUCTION = (
    "You are JUGAAD Image Engine. Turn a short idea into visual beats for a comic storyboard.\n"
    "\n"
    "Requirements:\n"
    "- Write 5 to 8 short sentences. Each sentence is one picture.\n"
    "- Describe what is on screen: who, action, place, weather, light.\n"
    "- No spoken narration, no dialogue, no sound effects, no camera jargon.\n"
    "- Do not start with Okay, Here's a story, or any preamble.\n"
    "- After the beats, add these lines (not drawn):\n"
    "TITLE: <3-6 word title, no quotes>\n"
    "SETTING: <3-8 word visual location>\n"
    "Then one VISUAL: search key per beat (4-10 words, no sentences).\n"
    "- No markdown. Match the prompt's language if it is not English."
)


class StoryError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def api_key() -> str:
    return (
        (getattr(config, "OPENAI_API_KEY", "") or "")
        or (os.getenv("OPENAI_API_KEY") or "")
    ).strip().strip('"').strip("'")


def ollama_base() -> str:
    return (
        (os.getenv("OLLAMA_HOST") or "")
        or (getattr(config, "OLLAMA_HOST", "") or "")
        or "http://127.0.0.1:11434"
    ).strip().rstrip("/")


def ollama_model() -> str:
    return (
        (os.getenv("OLLAMA_MODEL") or "")
        or (getattr(config, "OLLAMA_MODEL", "") or "")
        or "gemma3:4b"
    ).strip() or "gemma3:4b"


def _word_budget(seconds: int) -> int:
    n = int(seconds or 0)
    if n <= 0:
        return 420
    return max(80, min(520, int(round(n * 2.2))))


def target_shot_count(seconds: int) -> int:
    """Preferred shot count for a target final duration (≈4–6s per shot)."""
    n = int(seconds or 0)
    if n <= 0:
        n = 180
    # ~5s per shot, then clamp to product guidance bands.
    raw = int(round(n / 5.0))
    if n <= 30:
        return max(5, min(8, max(raw, 5)))
    if n <= 60:
        return max(10, min(14, max(raw, 10)))
    # Longer shorts: denser plan, still bounded for model reliability.
    hard = int(getattr(config, "PIXAZO_MAX_CLIPS", 12) or 12)
    return max(12, min(max(hard, 24), max(raw, 12)))


def _user_brief(idea: str, title: str, setting: str, seconds: int, mode: str = "story") -> str:
    idea = (idea or "").strip()
    if not idea:
        raise StoryError(400, "Write a short prompt first.")
    if (mode or "story").strip().lower() == "images":
        return "Idea:\n{0}\n\nWrite visual beats only.".format(idea)
    words = _word_budget(seconds)
    shots = target_shot_count(seconds)
    bits = [
        "Spoken story: about {0} words.".format(words),
        "Final video ~{0}s → exactly {1} shots, duration 5 each.".format(
            int(seconds or 180), shots,
        ),
        "Keep every field short. JSON only.",
        "Idea:\n{0}".format(idea),
    ]
    if (title or "").strip():
        bits.insert(-1, "Title hint: {0}".format(title.strip()[:48]))
    if (setting or "").strip():
        bits.insert(-1, "Setting hint: {0}".format(setting.strip()[:80]))
    return "\n".join(bits)


def _instruction_for(mode: str) -> str:
    if (mode or "story").strip().lower() == "images":
        return _IMAGE_INSTRUCTION
    return _STORY_INSTRUCTION


_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "had",
    "has", "have", "he", "her", "his", "i", "in", "is", "it", "its", "me", "my",
    "of", "on", "or", "our", "she", "so", "that", "the", "then", "there", "they",
    "this", "to", "was", "we", "were", "with", "you",
}

_TITLE_NOISE = _STOP | {
    "again", "back", "been", "could", "didn", "didnt", "dont", "down", "even",
    "felt", "heard", "into", "just", "last", "left", "looked", "not", "now", "only",
    "over", "said", "saw", "still", "stop", "thing", "things", "told", "very",
    "would",
}


def derive_title(text: str) -> str:
    words = [
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", text or "")
        if w.lower() not in _TITLE_NOISE
    ]
    pair, n = (Counter(zip(words, words[1:])).most_common(1) or [(None, 0)])[0]
    if pair and n >= 2:
        extra = [
            w for w, c in Counter(words).most_common()
            if c >= 2 and w not in pair and len(w) >= 4
        ][:1]
        return " ".join(list(pair) + extra).title()[:48]
    ranked = [w for w, c in Counter(words).most_common() if c >= 2 and len(w) >= 4][:4]
    if len(ranked) >= 2:
        return " ".join(ranked).title()[:48]
    sent = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    first = sent[0] if sent else (text or "")
    kept = [w for w in re.findall(r"[A-Za-z][A-Za-z']*", first) if w.lower() not in _STOP][:6]
    if not kept:
        kept = re.findall(r"[A-Za-z][A-Za-z']*", first)[:4]
    return (" ".join(kept).strip() or "jugaad")[:48]


def derive_setting(text: str) -> str:
    raw = (text or "").strip()
    loc = re.search(
        r"\b(?:in|inside|at|outside|through)\s+(?:the\s+)?([^.,!?]{6,48})",
        raw,
        re.I,
    )
    if loc:
        place = re.sub(r"\s+", " ", loc.group(0)).strip(" .")
        extras = [w for w in ("rain", "night", "fog", "storm", "dark") if re.search(r"\b" + w + r"\b", raw, re.I)]
        bits = [place] + extras[:2]
        return " ".join(bits)[:80]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z']*", " ".join(re.split(r"(?<=[.!?])\s+", raw)[:2])) if w.lower() not in _STOP][:8]
    return (" ".join(words) + " cinematic").strip()[:80] or "dark cinematic night"


def _boxed_fits_story(boxed: str, story: str) -> bool:
    """True when a typed title/setting is actually about this narration, not leftover copy."""
    boxed = (boxed or "").strip()
    if not boxed:
        return False
    story_l = (story or "").lower()
    tokens = [
        t.lower()
        for t in re.findall(r"[A-Za-z][A-Za-z']{2,}", boxed)
        if t.lower() not in _STOP
    ]
    if not tokens:
        return False
    return any(t in story_l for t in tokens)


def effective_title(story: str, boxed: str = "") -> str:
    boxed = (boxed or "").strip()
    if boxed and _boxed_fits_story(boxed, story):
        return boxed[:48]
    return derive_title(story)


def effective_setting(story: str, boxed: str = "") -> str:
    boxed = (boxed or "").strip()
    if boxed and _boxed_fits_story(boxed, story):
        return boxed[:80]
    return derive_setting(story)


def visual_to_image_beat(raw: str) -> str:
    """Turn 'key | alt | alt' into one illustration prompt."""
    parts = [p.strip() for p in str(raw or "").split("|") if p.strip()]
    return ", ".join(parts)


def _is_scene_heading(line: str) -> bool:
    s = (line or "").strip()
    if re.search(r"visual\s+search\s+keys|search\s+keys", s, re.I):
        return True
    if re.match(r"^(?:visuals?|visual\s+keys?|search\s+keys?)\s*:?\s*$", s, re.I):
        return True
    numbered = re.match(r"^\d+[\.\)]\s+(.+)$", s)
    if numbered:
        rest = numbered.group(1).strip()
        return 1 <= len(rest.split()) <= 4
    if len(s.split()) <= 4 and re.match(
        r"^(Opening|Ending|Hook|Climax|Chase|Appearance|Aftermath|Finale)\b",
        s,
        re.I,
    ):
        return True
    return False


def _strip_visual_line(line: str) -> str:
    s = (line or "").strip().strip("`").strip()
    return re.sub(r"^\d+[\.\)]\s+", "", s).strip()


_META_VISUAL = re.compile(
    r"\b("
    r"here'?s|here is|designed for|aiming for|unnerving story|"
    r"youtube\s+shorts?|tiktok|instagram\s+reels?|short.?form|"
    r"faceless|dread'?s?\s+effect|story\s+designed"
    r")\b",
    re.I,
)


def _is_chat_preamble(line: str) -> bool:
    """LLM chat fluff that must never become spoken text or stock keys."""
    s = (line or "").strip()
    if not s:
        return False
    if re.match(
        r"^(okay[,.]?\s+)?here'?s\s+(a\s+)?(short\b|unnerving\b|scary\b|creepy\b)?",
        s,
        re.I,
    ):
        return True
    if re.match(r"^(okay[,.]?\s+)?here\s+is\s+(a\s+)?(short\b|story\b)", s, re.I):
        return True
    if re.search(r"designed for\s+(a\s+)?(youtube\s+)?short", s, re.I):
        return True
    if re.search(r"aiming for\b", s, re.I) and re.search(r"\b(story|effect|dread)\b", s, re.I):
        return True
    if s.rstrip().endswith(":") and _META_VISUAL.search(s) and len(s.split()) >= 6:
        return True
    return False


def _is_visual_key(line: str) -> bool:
    s = _strip_visual_line(line)
    if not s or re.search(r"[.!?]", s):
        return False
    if s.endswith(":"):
        return False
    if _is_chat_preamble(s) or _META_VISUAL.search(s):
        return False
    words = s.split()
    return 3 <= len(words) <= 16


def is_stock_visual_key(line: str) -> bool:
    """True for a stock/search phrase (or alternates joined with |)."""
    s = (line or "").strip()
    if not s:
        return False
    if "|" in s:
        parts = [p.strip() for p in s.split("|") if p.strip()]
        return bool(parts) and all(_is_visual_key(p) for p in parts)
    return _is_visual_key(s)


def filter_stock_visuals(keys) -> list:
    out = []
    for key in keys or []:
        s = str(key or "").strip()
        if is_stock_visual_key(s):
            out.append(s)
    return out[:16]


def parse_visuals(text: str) -> list:
    """Parse ChatGPT-style visual search keys or VISUAL: lines.

    Numbered scene titles are ignored. Keys under one heading become
    one scene joined with ' | ' (Pexels alternates). Bare lists stay
    one key per line.
    """
    groups = []
    current = []
    saw_heading = False
    for raw in (text or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        if _is_chat_preamble(line):
            continue
        if re.match(r"^(TITLE|SETTING)\s*:", line, re.I):
            continue
        tagged = re.match(r"^(?:VISUAL|KEY|SEARCH)\s*:\s*(.+)$", line, re.I)
        if tagged:
            line = tagged.group(1).strip()
        if _is_scene_heading(line):
            saw_heading = True
            if current:
                groups.append(current)
                current = []
            continue
        if _is_visual_key(line):
            current.append(_strip_visual_line(line))
    if current:
        groups.append(current)
    if not groups:
        return []
    if saw_heading:
        return [" | ".join(g) for g in groups if g][:16]
    return [k for g in groups for k in g][:16]


def derive_visuals(text: str, setting: str = "") -> list:
    cleaned = _clean_story(text)
    parsed = filter_stock_visuals(parse_visuals(cleaned))
    if parsed:
        return parsed
    sentences = re.split(r"(?<=[.!?])\s+", (cleaned or "").strip())
    keys = []
    loc = (setting or "").strip()
    for sent in sentences:
        if _is_chat_preamble(sent):
            continue
        words = [
            w for w in re.findall(r"[A-Za-z0-9'\-]+", sent)
            if w.lower() not in _STOP
        ][:8]
        if len(words) < 3:
            continue
        phrase = " ".join(words)
        if loc and loc.lower() not in phrase.lower():
            phrase = (loc + " " + phrase).strip()
        if not is_stock_visual_key(phrase) and len(phrase.split()) < 3:
            continue
        # Sentence extracts can have punctuation removed already; accept 3–16 words.
        if 3 <= len(phrase.split()) <= 16:
            keys.append(phrase[:80])
        if len(keys) >= 8:
            break
    return keys


def _parse_pack(raw: str) -> dict:
    cleaned = _clean_story(raw)
    title = ""
    setting = ""
    visuals = []
    body = []
    in_visuals = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if _is_chat_preamble(stripped):
            continue
        t = re.match(r"^TITLE:\s*(.+)$", stripped, re.I)
        s = re.match(r"^SETTING:\s*(.+)$", stripped, re.I)
        v = re.match(r"^(?:VISUAL|KEY|SEARCH)\s*:\s*(.+)$", stripped, re.I)
        if t:
            title = t.group(1).strip().strip('"').strip("'")
            in_visuals = False
            continue
        if s:
            setting = s.group(1).strip().strip('"').strip("'")
            in_visuals = False
            continue
        if re.match(r"^(?:visuals?|visual\s+keys?)\s*:?\s*$", stripped, re.I):
            in_visuals = True
            continue
        if v:
            key = v.group(1).strip()
            if _is_visual_key(key):
                visuals.append(key)
            continue
        if in_visuals:
            if _is_visual_key(stripped):
                visuals.append(stripped)
            continue
        body.append(line)
    text = _clean_story("\n".join(body)) or cleaned
    text = _clean_story(text)
    if not title:
        title = derive_title(text)
    if not setting:
        setting = derive_setting(text)
    visuals = filter_stock_visuals(visuals)
    if not visuals:
        visuals = parse_visuals(cleaned)
    visuals = filter_stock_visuals(visuals)
    return {
        "text": text,
        "title": title[:48],
        "setting": setting[:80],
        "visuals": visuals[:16],
        "setting_search_keys": [],
        "characters": [],
        "shots": [],
        "setting_detail": {},
    }


def _extract_json_object(raw: str):
    """Best-effort extract of a top-level JSON object from model output."""
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def _setting_string(data: dict) -> str:
    keys = [
        str(k).strip()
        for k in (data.get("setting_search_keys") or [])
        if str(k).strip()
    ]
    if keys:
        first = keys[0]
        if is_stock_visual_key(first) or len(first.split()) >= 2:
            return first[:80]
    setting = data.get("setting")
    if isinstance(setting, dict):
        bits = [
            str(setting.get(k) or "").strip()
            for k in ("location", "time", "atmosphere")
            if str(setting.get(k) or "").strip()
        ]
        joined = " ".join(bits).strip()
        if joined:
            return joined[:80]
    if isinstance(setting, str) and setting.strip():
        return setting.strip()[:80]
    return ""


def _normalize_character(row) -> dict:
    if not isinstance(row, dict):
        return {}
    look = str(row.get("look") or "").strip()
    appearance = str(row.get("appearance") or "").strip()
    clothing = str(row.get("clothing") or "").strip()
    description = str(row.get("description") or "").strip()
    if look and not appearance:
        appearance = look
    out = {
        "name": str(row.get("name") or "").strip()[:64],
        "description": description[:160],
        "appearance": appearance[:160],
        "clothing": clothing[:120],
        "look": (look or " ".join(p for p in (appearance, clothing) if p).strip())[:160],
    }
    if not any(out[k] for k in ("name", "look", "appearance", "clothing", "description")):
        return {}
    return out


def _place_phrase(setting_detail: dict, fallback: str = "") -> str:
    if isinstance(setting_detail, dict):
        bits = [
            str(setting_detail.get(k) or "").strip()
            for k in ("location", "time", "atmosphere")
            if str(setting_detail.get(k) or "").strip()
        ]
        if bits:
            return " ".join(bits)
        loc = str(setting_detail.get("location") or "").strip()
        if loc:
            return loc
    return (fallback or "").strip()


def _character_look_line(characters: list) -> str:
    for ch in characters or []:
        if not isinstance(ch, dict):
            continue
        look = str(ch.get("look") or "").strip()
        if not look:
            look = " ".join(
                str(ch.get(k) or "").strip()
                for k in ("appearance", "clothing")
                if str(ch.get(k) or "").strip()
            ).strip()
        name = str(ch.get("name") or "").strip()
        if look and name:
            return "{0}, {1}".format(name, look)
        if look:
            return look
        if name:
            return name
    return ""


def _compose_visual_prompt(shot: dict, setting_detail: dict, characters: list, setting_fallback: str = "") -> str:
    """Build a compact Pixazo prompt from short shot fields (app-side, not Ollama)."""
    place = _place_phrase(setting_detail, setting_fallback)
    look = _character_look_line(characters)
    action = str(shot.get("action") or "").strip()
    camera = str(shot.get("camera") or "").strip()
    # Optional legacy fields if an older pack still has them.
    start_state = str(shot.get("start_state") or "").strip()
    end_state = str(shot.get("end_state") or "").strip()
    style = ""
    if isinstance(setting_detail, dict):
        style = str(setting_detail.get("visual_style") or "").strip()

    parts = []
    if place:
        parts.append(place.rstrip(".") + ".")
    if look:
        parts.append(look.rstrip(".") + ".")
    if start_state and start_state.lower() not in action.lower():
        parts.append(start_state.rstrip(".") + ".")
    if action:
        parts.append(action if action.endswith((".", "!", "?")) else action + ".")
    if camera:
        parts.append(camera if camera.endswith((".", "!", "?")) else camera + ".")
    if end_state and end_state.lower() not in " ".join(parts).lower():
        parts.append(end_state.rstrip(".") + ".")
    if style:
        parts.append(style.rstrip(".") + ".")
    parts.append("Clear physical motion through the shot, no freeze-frame.")
    return " ".join(parts).strip()[:500]


def _normalize_shot(
    row,
    index: int,
    setting_detail: dict,
    characters: list,
    setting_fallback: str = "",
) -> dict:
    if not isinstance(row, dict):
        return {}
    try:
        duration = float(row.get("duration") or 5)
    except (TypeError, ValueError):
        duration = 5.0
    # Keep Pixazo clips in the 4–6s planning band (assembly stretches to VO).
    duration = 5.0 if 4.0 <= duration <= 6.0 else max(4.0, min(6.0, duration))
    action = str(row.get("action") or "").strip()
    camera = str(row.get("camera") or "").strip()
    start_state = str(row.get("start_state") or "").strip()
    end_state = str(row.get("end_state") or "").strip()
    purpose = str(row.get("purpose") or "").strip()
    raw_prompt = str(row.get("visual_prompt") or "").strip()
    # Prefer app-composed prompts from short fields; only fall back to model prompt.
    if action or camera:
        prompt = _compose_visual_prompt(
            {
                "action": action,
                "camera": camera,
                "start_state": start_state,
                "end_state": end_state,
            },
            setting_detail,
            characters,
            setting_fallback=setting_fallback,
        )
    else:
        prompt = raw_prompt
    if not prompt:
        return {}
    try:
        number = int(row.get("shot_number") or index)
    except (TypeError, ValueError):
        number = index
    return {
        "shot_number": number,
        "duration": 5.0,
        "purpose": purpose[:120],
        "start_state": start_state[:160],
        "action": action[:200],
        "end_state": end_state[:160],
        "camera": camera[:120],
        "visual_prompt": prompt[:500],
    }


def normalize_shots(raw_shots, setting_detail=None, characters=None, setting: str = "") -> list:
    """Public: normalize shot dicts for Studio / PARTS / Pixazo."""
    setting_detail = setting_detail if isinstance(setting_detail, dict) else {}
    characters = characters if isinstance(characters, list) else []
    setting_fallback = (setting or "").strip()
    out = []
    for i, row in enumerate(raw_shots or [], start=1):
        shot = _normalize_shot(
            row, i, setting_detail, characters, setting_fallback=setting_fallback,
        )
        if shot:
            out.append(shot)
    for i, shot in enumerate(out, start=1):
        shot["shot_number"] = i
    return out


def _parse_json_pack(data: dict) -> dict:
    story = _clean_story(str(data.get("story") or data.get("text") or ""))
    raw_setting = data.get("setting")
    setting_detail = raw_setting if isinstance(raw_setting, dict) else {}
    if isinstance(raw_setting, str) and raw_setting.strip() and not setting_detail:
        setting_detail = {"location": raw_setting.strip()[:80]}
    characters = [
        ch for ch in (_normalize_character(row) for row in (data.get("characters") or []))
        if ch
    ]
    setting = _setting_string(data)
    shots = normalize_shots(
        data.get("shots") or [],
        setting_detail=setting_detail,
        characters=characters,
        setting=setting,
    )
    setting_keys = filter_stock_visuals(data.get("setting_search_keys") or [])
    visual_keys = filter_stock_visuals(
        data.get("visual_search_keys") or data.get("visuals") or []
    )
    title = str(data.get("title") or "").strip().strip('"').strip("'")
    if not title:
        title = derive_title(story)
    if not setting:
        setting = derive_setting(story)
    if not visual_keys:
        visual_keys = derive_visuals(story, setting)
    if not setting_keys and setting:
        setting_keys = filter_stock_visuals([setting]) or [setting]
    return {
        "text": story,
        "title": title[:48],
        "setting": setting[:80],
        "setting_detail": {
            "location": str(setting_detail.get("location") or setting or "").strip()[:80],
            "time": str(setting_detail.get("time") or "").strip()[:40],
            "atmosphere": str(setting_detail.get("atmosphere") or "").strip()[:80],
            "visual_style": str(setting_detail.get("visual_style") or "").strip()[:80],
        },
        "setting_search_keys": setting_keys[:8],
        "visuals": visual_keys[:16],
        "characters": characters[:8],
        "shots": shots,
    }


def _finish_story(raw: str) -> dict:
    data = _extract_json_object(raw)
    if data and (data.get("story") or data.get("text") or data.get("shots")):
        pack = _parse_json_pack(data)
    else:
        pack = _parse_pack(raw)
    if len(pack["text"].split()) < 8:
        raise StoryError(502, "The story engine returned an empty story.")
    visuals = filter_stock_visuals(pack.get("visuals") or [])
    if not visuals:
        visuals = derive_visuals(pack["text"], pack.get("setting") or "")
    pack["visuals"] = visuals
    pack.setdefault("setting_search_keys", [])
    pack.setdefault("characters", [])
    pack.setdefault("shots", [])
    pack.setdefault("setting_detail", {})
    if pack.get("shots"):
        pack["shots"] = normalize_shots(
            pack["shots"],
            setting_detail=pack.get("setting_detail") or {},
            characters=pack.get("characters") or [],
            setting=pack.get("setting") or "",
        )
    return pack


def shot_visual_prompts(part: dict) -> list:
    """Extract ordered visual prompts from PARTS shots (or empty)."""
    shots = normalize_shots(
        part.get("shots") or [],
        setting=part.get("broll_query") or part.get("setting") or "",
        characters=part.get("characters") or [],
    )
    return [s["visual_prompt"] for s in shots if s.get("visual_prompt")]


def empty_shot_pack_fields() -> dict:
    return {
        "setting_search_keys": [],
        "characters": [],
        "shots": [],
        "setting_detail": {},
    }


def _model_installed(names: list, wanted: str) -> bool:
    wanted = (wanted or "").strip().lower()
    if not wanted:
        return False
    for raw in names:
        name = (raw or "").strip().lower()
        if not name:
            continue
        if name == wanted or name.startswith(wanted):
            return True
    return False


def ollama_status(force: bool = False) -> dict:
    """Lightweight ping + whether the recommended model is installed."""
    now = time.time()
    if not force and _OLLAMA_CACHE["status"] and now - _OLLAMA_CACHE["t"] < _OLLAMA_CACHE_TTL:
        return dict(_OLLAMA_CACHE["status"])
    model = ollama_model()
    out = {
        "online": False,
        "model": model,
        "model_ready": False,
        "hint": "Ollama isn't running. Start Ollama and try again.",
    }
    try:
        resp = requests.get(ollama_base() + "/api/tags", timeout=2)
    except requests.RequestException:
        _OLLAMA_CACHE["t"] = now
        _OLLAMA_CACHE["status"] = out
        return dict(out)
    if resp.status_code >= 400:
        _OLLAMA_CACHE["t"] = now
        _OLLAMA_CACHE["status"] = out
        return dict(out)
    names = []
    try:
        payload = resp.json() or {}
        for item in payload.get("models") or []:
            if isinstance(item, dict):
                names.append(item.get("name") or item.get("model") or "")
    except ValueError:
        names = []
    ready = _model_installed(names, model)
    out["online"] = True
    out["model_ready"] = ready
    out["hint"] = (
        ""
        if ready
        else "Model {0} is not installed. Run: ollama pull {0}".format(model)
    )
    _OLLAMA_CACHE["t"] = now
    _OLLAMA_CACHE["status"] = out
    return dict(out)


def generate_story(
    provider: str,
    idea: str,
    title: str = "",
    setting: str = "",
    seconds: int = 180,
    mode: str = "story",
) -> dict:
    kind = (provider or "ollama").strip().lower()
    if kind in ("openai", "chatgpt", "gpt"):
        return generate_story_with_openai(idea, title=title, setting=setting, seconds=seconds, mode=mode)
    return generate_story_with_ollama(idea, title=title, setting=setting, seconds=seconds, mode=mode)


def generate_story_with_ollama(
    idea: str,
    title: str = "",
    setting: str = "",
    seconds: int = 180,
    mode: str = "story",
) -> dict:
    status = ollama_status(force=True)
    model = status.get("model") or ollama_model()
    if not status.get("online"):
        raise StoryError(503, "Ollama isn't running. Start Ollama and try again.")
    if not status.get("model_ready"):
        raise StoryError(503, "Model {0} is not installed. Run: ollama pull {0}".format(model))
    brief = _user_brief(idea, title, setting, seconds, mode=mode)
    prompt = _instruction_for(mode) + "\n\n" + brief
    payload = {"model": model, "prompt": prompt, "stream": False}
    if (mode or "story").strip().lower() != "images":
        # Ask Ollama for structured JSON (shot plan + story).
        payload["format"] = "json"
    try:
        resp = requests.post(
            ollama_base() + "/api/generate",
            json=payload,
            timeout=240,
        )
    except requests.RequestException as exc:
        raise StoryError(503, "Ollama isn't running. Start Ollama and try again.") from exc
    if resp.status_code == 404:
        raise StoryError(503, "Model {0} is not installed. Run: ollama pull {0}".format(model))
    if resp.status_code >= 400:
        raise StoryError(502, "Ollama could not write that story.")
    try:
        text = (resp.json() or {}).get("response") or ""
    except ValueError as exc:
        raise StoryError(502, "Ollama returned an empty story.") from exc
    return _finish_story(text)


def generate_story_with_openai(
    idea: str,
    title: str = "",
    setting: str = "",
    seconds: int = 180,
    mode: str = "story",
) -> dict:
    key = api_key()
    if not key:
        raise StoryError(
            503,
            "Add an OpenAI key in Settings, or put OPENAI_API_KEY in .env and restart studio.",
        )
    brief = _user_brief(idea, title, setting, seconds, mode=mode)
    words = _word_budget(seconds)
    model = (os.getenv("OPENAI_MODEL") or getattr(config, "OPENAI_MODEL", "") or "gpt-4o-mini").strip()
    system = _instruction_for(mode)
    story_mode = (mode or "story").strip().lower() != "images"
    if story_mode:
        system = system + " About {0} English words in the story field.".format(words)
    body = {
        "model": model,
        "temperature": 0.85,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": brief},
        ],
    }
    if story_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise StoryError(502, "Could not reach OpenAI.") from exc

    if resp.status_code == 401:
        raise StoryError(401, "OpenAI rejected that key. Check it in Settings.")
    if resp.status_code == 429:
        raise StoryError(429, "OpenAI is rate-limiting this key. Try again in a moment.")
    if resp.status_code >= 400:
        detail = "OpenAI could not write that story."
        try:
            err = resp.json().get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else ""
            if msg and "key" not in msg.lower() and "sk-" not in msg:
                detail = msg
        except ValueError:
            pass
        raise StoryError(502, detail)

    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise StoryError(502, "OpenAI returned an empty story.") from exc
    return _finish_story(text)


def expand_story(idea: str, title: str = "", setting: str = "", seconds: int = 180) -> str:
    """Back-compat: OpenAI path used before providers existed."""
    pack = generate_story_with_openai(idea, title=title, setting=setting, seconds=seconds)
    return pack["text"]


def _clean_story(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:\w+)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    lines = [ln.strip() for ln in raw.splitlines()]
    if lines and re.match(r"^story\s*:", lines[0], re.I):
        lines = lines[1:]
    kept = []
    for ln in lines:
        if _is_chat_preamble(ln):
            continue
        if re.match(r"^(okay[,.]?\s+)?here'?s\s+(a\s+)?(short\s+)?story\b", ln, re.I):
            continue
        ln = re.sub(
            r"^(?:Narrator|Narration|VO|V\.O\.|Voice[- ]?over)\s*:\s*",
            "",
            ln,
            flags=re.I,
        )
        kept.append(ln)
    return "\n\n".join(p for p in re.split(r"\n\s*\n", "\n".join(kept)) if p.strip()).strip()


def clean_story(text: str) -> str:
    """Public: drop LLM chat preambles from spoken narration."""
    return _clean_story(text)

"""
Expand a short prompt into spoken narration.

Providers:
  ollama — local, no key (default)
  openai — Settings / OPENAI_API_KEY (optional)

Requests go through the studio backend. Keys never reach the browser.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter

import requests

import config

_OLLAMA_CACHE = {"t": 0.0, "status": None}
_OLLAMA_CACHE_TTL = 8.0

_STORY_INSTRUCTION = (
    "You are JUGAAD Story Engine, an expert short-form storytelling AI.\n"
    "\n"
    "Create engaging stories designed for faceless YouTube Shorts, TikTok, and Instagram Reels.\n"
    "\n"
    "Requirements:\n"
    "- Start with a strong hook.\n"
    "- Keep the pacing fast.\n"
    "- Make the story easy to visualize.\n"
    "- Use short, clear sentences.\n"
    "- Build curiosity and tension.\n"
    "- Include a satisfying twist or payoff when appropriate.\n"
    "- Avoid unnecessary exposition.\n"
    "- Make the story suitable for AI-generated visuals.\n"
    "- Do not include camera directions, speaker labels (Narrator:), or stage directions in parentheses.\n"
    "- Spoken words only. Do not write (silence), (music swells), or (SFX) lines.\n"
    "- After the story, add these lines (not part of the spoken narration):\n"
    "TITLE: <3-6 word title, no quotes>\n"
    "SETTING: <3-8 word visual location for stock footage, e.g. old wooden house rain night>\n"
    "Then 5 to 8 visual search keys, one per line:\n"
    "VISUAL: <4-10 word image/stock search, no sentences>\n"
    "- Each VISUAL names place, subject, action, weather, and time of day for that beat.\n"
    "- For Japanese or anime stories, include location words (Tokyo, school, railway) and the creature when it appears.\n"
    "- No markdown, no quotation marks around the whole piece.\n"
    "- Complete beginning, middle, and end. Match the prompt's language if it is not English."
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


def _user_brief(idea: str, title: str, setting: str, seconds: int, mode: str = "story") -> str:
    idea = (idea or "").strip()
    if not idea:
        raise StoryError(400, "Write a short prompt first.")
    if (mode or "story").strip().lower() == "images":
        return "Idea:\n{0}\n\nWrite visual beats only.".format(idea)
    words = _word_budget(seconds)
    return (
        "Write a spoken narration of about {0} English words.\n"
        "Story idea:\n{1}"
    ).format(words, idea)


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


def _is_visual_key(line: str) -> bool:
    s = _strip_visual_line(line)
    if not s or re.search(r"[.!?]", s):
        return False
    words = s.split()
    return 3 <= len(words) <= 16


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
    parsed = parse_visuals(text)
    if parsed:
        return parsed
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    keys = []
    loc = (setting or "").strip()
    for sent in sentences:
        words = [
            w for w in re.findall(r"[A-Za-z0-9'\-]+", sent)
            if w.lower() not in _STOP
        ][:8]
        if len(words) < 3:
            continue
        phrase = " ".join(words)
        if loc and loc.lower() not in phrase.lower():
            phrase = (loc + " " + phrase).strip()
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
            visuals.append(v.group(1).strip())
            continue
        if in_visuals:
            if _is_visual_key(stripped):
                visuals.append(stripped)
            continue
        body.append(line)
    text = _clean_story("\n".join(body)) or cleaned
    if not title:
        title = derive_title(text)
    if not setting:
        setting = derive_setting(text)
    if not visuals:
        visuals = parse_visuals(cleaned)
    return {
        "text": text,
        "title": title[:48],
        "setting": setting[:80],
        "visuals": visuals[:16],
    }


def _finish_story(raw: str) -> dict:
    pack = _parse_pack(raw)
    if len(pack["text"].split()) < 8:
        raise StoryError(502, "The story engine returned an empty story.")
    if not pack.get("visuals"):
        pack["visuals"] = derive_visuals(pack["text"], pack.get("setting") or "")
    return pack


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
    try:
        resp = requests.post(
            ollama_base() + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=180,
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
    if (mode or "story").strip().lower() != "images":
        system = system + " About {0} English words.".format(words)
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.85,
                "messages": [
                    {
                        "role": "system",
                        "content": system,
                    },
                    {"role": "user", "content": brief},
                ],
            },
            timeout=90,
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

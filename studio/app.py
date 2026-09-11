"""
JUGAAD studio — local web UI for the faceless reel pipeline.

    python studio.py
    open http://127.0.0.1:8787
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Union

from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from epidemic_engine import EpidemicError, api_key as epidemic_api_key
from epidemic_engine import import_track as epidemic_import
from epidemic_engine import is_hls as epidemic_is_hls
from epidemic_engine import list_tracks as epidemic_list
from epidemic_engine import media_type_for as epidemic_media_type
from epidemic_engine import open_media as epidemic_open_media
from epidemic_engine import open_preview as epidemic_open_preview
from epidemic_engine import rewrite_hls as epidemic_rewrite_hls
from music_engine import list_music_tracks
from youtube_engine import YouTubeError
from youtube_engine import auth_url as youtube_auth_url
from youtube_engine import client_credentials as youtube_client
from youtube_engine import delete_video as youtube_delete
from youtube_engine import exchange_code as youtube_exchange
from youtube_engine import list_videos as youtube_list
from youtube_engine import redirect_uri as youtube_redirect
from youtube_engine import update_video as youtube_update
from youtube_engine import upload_video as youtube_upload
from story_engine import (
    StoryError,
    clean_story,
    derive_visuals,
    effective_setting,
    effective_title,
    filter_stock_visuals,
    generate_story,
    normalize_shots,
    ollama_status,
    parse_visuals,
    visual_to_image_beat,
)

from tts_engine import list_voices, resolve_voice, voice_preview_path

from . import auth

STUDIO = Path(__file__).resolve().parent
STATIC = STUDIO / "static"
SCRIPTS = ROOT / "scripts" / "studio"
JOBS_DIR = ROOT / "jobs"
SCRIPTS.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

_SHOW_DOCS = (os.getenv("JUGAAD_DOCS") or "").strip().lower() in ("1", "true", "yes")
app = FastAPI(
    title="JUGAAD",
    version="0.1",
    docs_url="/docs" if _SHOW_DOCS else None,
    redoc_url="/redoc" if _SHOW_DOCS else None,
    openapi_url="/openapi.json" if _SHOW_DOCS else None,
)

_lock = threading.Lock()
_current = None
_voice_preview_lock = threading.Lock()
_rate_lock = threading.Lock()
_rate_hits = {}
_STATIC_EXT = {".css", ".js", ".map", ".ico", ".png", ".svg", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}
_IMAGE_STYLES = {"comic", "cartoon", "anime"}
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_MUSIC_MAX_BYTES = 12 * 1024 * 1024


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    key = "{0}:{1}:{2}".format(
        bucket,
        _client_ip(request),
        request.session.get("user_id") or "anon",
    )
    now = time.time()
    with _rate_lock:
        hits = [stamp for stamp in _rate_hits.get(key, []) if now - stamp < window]
        if len(hits) >= limit:
            raise HTTPException(429, "Too many tries. Wait a minute.")
        hits.append(now)
        _rate_hits[key] = hits


def _account_key_on(user_value: str, env_value: str) -> bool:
    if (user_value or "").strip():
        return True
    if auth.isolate_account_keys():
        return False
    return bool((env_value or "").strip())


# Video models. Free illustrated styles need no key. Stock / Pixazo only
# appear when that account (or local .env) has the matching API key.
_VIDEO_MODELS = (
    {
        "id": "live",
        "label": "Live b-roll",
        "hint": "Real Pexels stock, timed to the story",
        "key_field": "pexels",
    },
    {
        "id": "pixabay",
        "label": "Pixabay",
        "hint": "Free stock video from Pixabay",
        "key_field": "pixabay",
    },
    {
        "id": "photos",
        "label": "Unsplash",
        "hint": "HD stock photos + camera motion",
        "key_field": "unsplash",
    },
    {
        "id": "comic",
        "label": "2D comic",
        "hint": "Illustrated panels + camera motion",
        "key_field": None,
    },
    {
        "id": "cartoon",
        "label": "Cartoon",
        "hint": "Flat cel-shaded stills",
        "key_field": None,
    },
    {
        "id": "anime",
        "label": "Anime",
        "hint": "Clean line art stills",
        "key_field": None,
    },
    {
        "id": "pixazo",
        "label": "Pixazo AI",
        "hint": "Free LTX AI video (fair-use key)",
        "key_field": "pixazo",
    },
)


def _video_models_for(user_id: int) -> list:
    """Free models always; key-gated models only when the key is set."""
    out = []
    for row in _VIDEO_MODELS:
        need = row.get("key_field")
        if need and not auth.key_is_available(user_id, need):
            continue
        out.append({
            "id": row["id"],
            "label": row["label"],
            "hint": row["hint"],
        })
    return out


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path in ("/docs", "/redoc", "/openapi.json") and not _SHOW_DOCS:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if path.startswith("/api/auth") or path == "/api/health":
        return await call_next(request)
    suffix = Path(path).suffix.lower()
    if suffix in _STATIC_EXT:
        return await call_next(request)
    if path in ("/login", "/login.html"):
        return await call_next(request)
    user = auth.current_user(request)
    if not user:
        if path.startswith("/api/") or path.startswith("/media/"):
            return JSONResponse({"detail": "Sign in first"}, status_code=401)
        if path.endswith(".html") and path != "/login.html":
            return RedirectResponse("/login", status_code=302)
        if path in ("/", "/studio", "/settings"):
            return RedirectResponse("/login", status_code=302)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SECRET,
    session_cookie="jugaad",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=auth.cookie_secure(),
)


class AuthBody(BaseModel):
    email: str
    password: str
    name: str = ""
    invite: str = ""


def _require_user(request: Request) -> dict:
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")
    return user


def _page(name: str) -> FileResponse:
    return FileResponse(str(STATIC / name))


@contextmanager
def _openai_for(user_id: int):
    extra = auth.apply_user_keys(user_id)
    prev = config.OPENAI_API_KEY
    prev_env = os.environ.get("OPENAI_API_KEY")
    try:
        if extra.get("OPENAI_API_KEY"):
            config.OPENAI_API_KEY = extra["OPENAI_API_KEY"]
            os.environ["OPENAI_API_KEY"] = extra["OPENAI_API_KEY"]
        elif auth.isolate_account_keys():
            config.OPENAI_API_KEY = ""
            os.environ.pop("OPENAI_API_KEY", None)
        yield
    finally:
        config.OPENAI_API_KEY = prev
        if prev_env is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = prev_env


@contextmanager
def _epidemic_for(user_id: int):
    keys = auth.apply_user_keys(user_id)
    user_key = (keys.get("EPIDEMIC_API_KEY") or "").strip()
    if user_key:
        with epidemic_api_key(user_key):
            yield
    elif auth.isolate_account_keys():
        with epidemic_api_key(""):
            yield
    else:
        yield


def _job_owners() -> dict:
    """stem -> set of user_id values (None = cut from before accounts)."""
    owners = {}
    for job_path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(job_path.read_text())
        except (OSError, ValueError):
            continue
        stem = data.get("stem")
        if not stem:
            continue
        owners.setdefault(stem, set()).add(data.get("user_id"))
    return owners


def _legacy_stems_ok(user_id: int) -> bool:
    first = auth.first_user_id()
    return first is not None and int(user_id) == int(first)


def _user_stems(user_id: int) -> set:
    """Cuts this account owns. Pre-account leftovers stay with the first user only."""
    visible = set()
    owners = _job_owners()
    legacy = _legacy_stems_ok(user_id)
    for stem, uids in owners.items():
        claimed = {uid for uid in uids if uid is not None}
        if user_id in claimed or (legacy and not claimed):
            visible.add(stem)
    for path in config.OUTPUT_DIR.glob("*.mp4"):
        claimed = {uid for uid in owners.get(path.stem, set()) if uid is not None}
        if user_id in claimed or (legacy and not claimed):
            visible.add(path.stem)
    for stem in _disk_stems():
        claimed = {uid for uid in owners.get(stem, set()) if uid is not None}
        if user_id in claimed or (legacy and not claimed):
            visible.add(stem)
    return visible


def _user_owns_stem(user_id: int, stem: str) -> bool:
    return stem in _user_stems(user_id)


def _image_jobs_for(user_id: int):
    jobs = []
    for job_path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(job_path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("kind") != "images":
            continue
        if data.get("user_id") != user_id:
            continue
        jobs.append(data)
    return jobs


def _user_owns_image(user_id: int, stem: str) -> bool:
    return any(job.get("stem") == stem for job in _image_jobs_for(user_id))


def _panel_public(stem: str, panel: dict) -> dict:
    path = Path(panel.get("path") or "")
    name = path.name or "panel.jpg"
    return {
        "file": name,
        "url": "/media/images/{0}/{1}".format(stem, name),
        "beat": panel.get("beat") or "",
    }


def _user_boards(user_id: int) -> list:
    boards = []
    for job in _image_jobs_for(user_id):
        if job.get("status") != "done":
            continue
        panels = job.get("panels") or []
        if not panels:
            continue
        boards.append({
            "name": job.get("title") or job.get("stem"),
            "stem": job.get("stem"),
            "file": job.get("stem"),
            "cover": panels[0].get("url"),
            "count": len(panels),
            "panels": panels,
            "prompt": job.get("prompt") or "",
            "setting": job.get("setting") or "",
            "visuals": job.get("visuals") or [],
            "model": job.get("model") or "comic",
            "size": job.get("size") or "9:16",
            "mtime": _job_path(job["id"]).stat().st_mtime if _job_path(job["id"]).exists() else 0,
        })
    boards.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
    return boards[:24]


def _seed_for(stem: str) -> int:
    return int(hashlib.md5(stem.encode("utf-8")).hexdigest()[:8], 16) % 100000


def _visual_list(raw) -> list:
    if isinstance(raw, list):
        text = "\n".join(str(x) for x in raw)
    else:
        text = str(raw or "")
    return filter_stock_visuals(parse_visuals(text))


class GenerateBody(BaseModel):
    prompt: str = Field(..., min_length=20)
    model: str = "live"
    music: str = "random"
    size: str = "9:16"
    title: str = ""
    setting: str = ""
    visuals: Union[str, List[str]] = ""
    shots: List[dict] = []
    characters: List[dict] = []
    voice: str = ""
    ref: str = ""
    ref_role: str = "creature"
    board: str = ""
    length: str = "youtube"
    seconds: int = 180


class EpidemicImportBody(BaseModel):
    id: str
    title: str = ""
    kind: str = "music"


class ImageBody(BaseModel):
    prompt: str = Field(..., min_length=8)
    model: str = "comic"
    size: str = "9:16"
    title: str = ""
    setting: str = ""
    visuals: Union[str, List[str]] = ""
    panels: int = 0
    ref: str = ""
    ref_role: str = "creature"


class StoryExpandBody(BaseModel):
    idea: str = ""
    title: str = ""
    setting: str = ""
    seconds: int = 180
    provider: str = "ollama"
    mode: str = "story"


class YouTubeUploadBody(BaseModel):
    title: str = ""
    description: str = ""
    privacy: str = "unlisted"
    shorts: bool = True
    made_for_kids: bool = False


class YouTubeUpdateBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    privacy: str = ""


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (cleaned[:40] or fallback)


def _job_path(job_id: str) -> Path:
    if not _JOB_ID_RE.fullmatch(job_id or ""):
        raise HTTPException(404, "Unknown job")
    return JOBS_DIR / "{0}.json".format(job_id)


def _read_job(job_id: str) -> dict:
    path = _job_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Unknown job")
    return json.loads(path.read_text())


def _write_job(job: dict) -> None:
    _job_path(job["id"]).write_text(json.dumps(job, indent=2))


def _safe_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Bad filename")
    return name


def _ref_dir(user_id: int) -> Path:
    path = config.REFS_DIR / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ref_file(name: str, user_id: int) -> Path:
    name = _safe_name(name)
    folder = _ref_dir(user_id)
    path = folder / name
    if path.exists() and _inside(path, folder):
        return path
    if _legacy_stems_ok(user_id):
        legacy = config.REFS_DIR / name
        if legacy.is_file() and legacy.parent.resolve() == config.REFS_DIR.resolve():
            return legacy
    raise HTTPException(404, "Reference image not found")


def _music_dir(user_id: int) -> Path:
    path = config.MUSIC_DIR / "users" / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _music_file(name: str, user_id: int) -> Path:
    name = _safe_name(name)
    shared = config.MUSIC_DIR / name
    if shared.is_file() and shared.parent.resolve() == config.MUSIC_DIR.resolve():
        return shared
    folder = _music_dir(user_id)
    path = folder / name
    if path.is_file() and _inside(path, folder):
        return path
    raise HTTPException(404, "Track not found")


def _youtube_public(keys: dict, base_url: str = "") -> dict:
    tokens = auth.parse_youtube_tokens(keys.get("youtube") or "")
    client_id, secret = youtube_client(keys)
    return {
        "enabled": bool(client_id and secret),
        "connected": bool(tokens.get("refresh_token")),
        "channel": (tokens.get("channel") or ""),
        "redirect": youtube_redirect(base_url),
    }


def _options(user_id: int, base_url: str = "") -> dict:
    tracks = []
    for path in list_music_tracks(user_id):
        tracks.append({
            "id": path.stem,
            "name": path.name,
            "preview": "/media/music/{0}".format(path.name),
        })
    library = _library_items(user_id)
    keys = auth.get_keys(user_id)
    models = _video_models_for(user_id)
    return {
        "brand": "JUGAAD",
        "tagline": "Faceless studio",
        "models": models,
        "models_hint": (
            ""
            if models
            else "Add a Pexels, Pixabay, Unsplash, or Pixazo key in Settings for stock / AI video."
        ),
        "voices": [
            {
                "id": voice["id"],
                "label": voice["label"],
                "hint": voice["hint"],
                "preview": "/api/voices/{0}/preview".format(voice["id"]),
            }
            for voice in list_voices()
        ],
        "sizes": [
            {"id": "9:16", "label": "9:16", "hint": "Shorts / Reels", "w": 9, "h": 16},
            {"id": "1:1", "label": "1:1", "hint": "Square", "w": 1, "h": 1},
            {"id": "4:5", "label": "4:5", "hint": "Feed", "w": 4, "h": 5},
            {"id": "16:9", "label": "16:9", "hint": "YouTube", "w": 16, "h": 9},
            {"id": "4:3", "label": "4:3", "hint": "Classic", "w": 4, "h": 3},
        ],
        "lengths": list(config.PLATFORM_LENGTHS),
        "length_min": config.LENGTH_MIN_SECONDS,
        "length_max": config.LENGTH_MAX_SECONDS,
        "music": [
            {"id": "random", "name": "Random", "preview": None},
            {"id": "off", "name": "No music", "preview": None},
        ] + tracks,
        "library": library[:24],
        "busy": _current is not None,
        "epidemic": {
            "enabled": auth.key_is_available(user_id, "epidemic"),
        },
        "openai": {
            "enabled": auth.key_is_available(user_id, "openai"),
        },
        "youtube": _youtube_public(keys, base_url),
        "story": _story_options(user_id, keys),
        "keys": auth.keys_public(user_id),
        "generate": {
            "enabled": auth.generate_enabled(),
            "hint": (
                "This beta host saves accounts and keys. Generate still runs on your machine "
                "(`python studio.py`) until a worker is attached."
                if not auth.generate_enabled()
                else ""
            ),
        },
        "images": {
            "enabled": True,
            "hint": (
                "Pollinations draws the panels here. A short prompt is enough — no full story. A key in Settings helps if the free pool is busy."
                + (
                    " Files on this host vanish if the box sleeps — generate locally to keep them."
                    if os.getenv("RENDER")
                    else ""
                )
            ),
            "models": [
                {"id": "comic", "label": "2D comic", "hint": "Graphic-novel stills"},
                {"id": "cartoon", "label": "Cartoon", "hint": "Flat cel-shaded stills"},
                {"id": "anime", "label": "Anime", "hint": "Clean line art stills"},
            ],
            "library": _user_boards(user_id),
        },
    }


@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "generate": auth.generate_enabled(),
        "postgres": auth.using_postgres(),
        "register": auth.register_open(),
        "invite": auth.invite_required(),
    }


@app.get("/")
def page_home(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=302)
    return _page("dashboard.html")


@app.get("/login")
def page_login(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=302)
    return _page("login.html")


@app.get("/studio")
def page_studio(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=302)
    return _page("index.html")


@app.get("/settings")
def page_settings(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=302)
    return _page("settings.html")


@app.post("/api/auth/register")
def api_register(request: Request, body: AuthBody):
    _rate_limit(request, "register", 5, 3600)
    auth.assert_can_register(body.invite)
    user = auth.register_user(body.email, body.password, body.name)
    auth.login_session(request, user)
    return {"ok": True, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@app.post("/api/auth/login")
def api_login(request: Request, body: AuthBody):
    _rate_limit(request, "login", 10, 60)
    user = auth.authenticate(body.email, body.password)
    auth.login_session(request, user)
    return {"ok": True, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@app.post("/api/auth/logout")
def api_logout(request: Request):
    auth.logout_session(request)
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    user = _require_user(request)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "keys": auth.keys_public(user["id"]),
        "youtube": _youtube_public(auth.get_keys(user["id"]), str(request.base_url)),
    }


@app.get("/api/settings")
def api_settings_get(request: Request):
    user = _require_user(request)
    return {"keys": auth.keys_public(user["id"])}


@app.put("/api/settings")
async def api_settings_put(request: Request):
    user = _require_user(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Expected JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected JSON object")
    updates = {name: body[name] for name in auth.KEY_FIELDS if name in body}
    return {"keys": auth.save_keys(user["id"], updates)}


def _story_options(user_id: int, keys: dict) -> dict:
    ollama = ollama_status()
    openai_on = auth.key_is_available(user_id, "openai")
    providers = [
        {
            "id": "ollama",
            "label": "Ollama",
            "hint": "FREE LOCAL" if ollama.get("online") else "offline",
            "online": bool(ollama.get("online")),
            "model": ollama.get("model") or "gemma3:4b",
            "model_ready": bool(ollama.get("model_ready")),
        },
    ]
    if openai_on:
        providers.append({
            "id": "openai",
            "label": "OpenAI",
            "hint": "API",
            "online": True,
        })
    return {
        "default": "ollama",
        "providers": providers,
    }


@app.post("/api/story/expand")
def api_story_expand(request: Request, body: StoryExpandBody):
    _rate_limit(request, "story", 20, 60)
    user = _require_user(request)
    provider = (body.provider or "ollama").strip().lower()
    try:
        if provider in ("openai", "chatgpt", "gpt"):
            with _openai_for(user["id"]):
                pack = generate_story(
                    "openai",
                    body.idea,
                    title=body.title,
                    setting=body.setting,
                    seconds=body.seconds,
                    mode=body.mode,
                )
        else:
            pack = generate_story(
                "ollama",
                body.idea,
                title=body.title,
                setting=body.setting,
                seconds=body.seconds,
                mode=body.mode,
            )
    except StoryError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {
        "ok": True,
        "text": pack["text"],
        "title": pack.get("title") or "",
        "setting": pack.get("setting") or "",
        "visuals": pack.get("visuals") or [],
        "setting_search_keys": pack.get("setting_search_keys") or [],
        "characters": pack.get("characters") or [],
        "shots": pack.get("shots") or [],
        "setting_detail": pack.get("setting_detail") or {},
        "provider": provider,
    }


@app.post("/api/story/derive")
def api_story_derive(request: Request, body: StoryExpandBody):
    _require_user(request)
    text = (body.idea or "").strip()
    if not text:
        raise HTTPException(400, "Story is empty.")
    setting = effective_setting(text, body.setting)
    return {
        "ok": True,
        "title": effective_title(text, body.title),
        "setting": setting,
        "visuals": derive_visuals(text, setting),
    }


@app.get("/api/options")
def api_options(request: Request):
    user = _require_user(request)
    return _options(user["id"], str(request.base_url))


def _youtube_http(exc: YouTubeError):
    # Never surface YouTube/Google auth failures as 401 — the frontend treats
    # 401 "Sign in first" as a studio session miss and redirects to /login.
    status = int(exc.status or 400)
    if status == 401:
        status = 400
    raise HTTPException(status_code=status, detail=str(exc))


def _youtube_map_path(user_id: int) -> Path:
    folder = STUDIO / "data" / "youtube"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "{0}.json".format(int(user_id))


def _youtube_map(user_id: int) -> dict:
    path = _youtube_map_path(user_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _youtube_remember(user_id: int, stem: str, video: dict) -> None:
    stem = (stem or "").strip()
    video_id = (video or {}).get("id") or ""
    if not stem or not video_id:
        return
    data = _youtube_map(user_id)
    data[stem] = {
        "id": video_id,
        "url": video.get("url") or "https://youtu.be/{0}".format(video_id),
        "title": video.get("title") or stem,
        "privacy": video.get("privacy") or "",
    }
    _youtube_map_path(user_id).write_text(json.dumps(data, indent=2))


def _youtube_forget(user_id: int, video_id: str) -> None:
    video_id = (video_id or "").strip()
    if not video_id:
        return
    data = _youtube_map(user_id)
    kept = {k: v for k, v in data.items() if (v or {}).get("id") != video_id}
    _youtube_map_path(user_id).write_text(json.dumps(kept, indent=2))


def _youtube_creds(user: dict):
    extra = auth.apply_user_keys(user["id"])
    if extra.get("GOOGLE_CLIENT_ID"):
        config.GOOGLE_CLIENT_ID = extra["GOOGLE_CLIENT_ID"]
    if extra.get("GOOGLE_CLIENT_SECRET"):
        config.GOOGLE_CLIENT_SECRET = extra["GOOGLE_CLIENT_SECRET"]
    tokens = auth.youtube_tokens(user["id"])
    if not tokens.get("refresh_token"):
        raise HTTPException(400, "Connect YouTube in Settings first.")
    keys = auth.get_keys(user["id"])
    client_id, secret = youtube_client(keys)
    if not client_id or not secret:
        raise HTTPException(400, "Save a Google client id and secret first.")
    return tokens, client_id, secret


@app.get("/api/youtube/connect")
def api_youtube_connect(request: Request):
    user = _require_user(request)
    keys = auth.get_keys(user["id"])
    extra = auth.apply_user_keys(user["id"])
    if extra.get("GOOGLE_CLIENT_ID"):
        config.GOOGLE_CLIENT_ID = extra["GOOGLE_CLIENT_ID"]
    if extra.get("GOOGLE_CLIENT_SECRET"):
        config.GOOGLE_CLIENT_SECRET = extra["GOOGLE_CLIENT_SECRET"]
    client_id, secret = youtube_client(keys)
    if not client_id or not secret:
        return RedirectResponse("/settings?youtube=needclient", status_code=302)
    redirect = youtube_redirect(str(request.base_url))
    state = secrets.token_urlsafe(24)
    request.session["youtube_oauth"] = state
    request.session["youtube_redirect"] = redirect
    return RedirectResponse(youtube_auth_url(client_id, redirect, state), status_code=302)


@app.get("/youtube/callback")
def youtube_callback(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if request.query_params.get("error"):
        return RedirectResponse("/settings?youtube=denied", status_code=302)
    expected = request.session.get("youtube_oauth") or ""
    state = request.query_params.get("state") or ""
    if not expected or state != expected:
        return RedirectResponse("/settings?youtube=state", status_code=302)
    code = (request.query_params.get("code") or "").strip()
    if not code:
        return RedirectResponse("/settings?youtube=denied", status_code=302)
    keys = auth.get_keys(user["id"])
    extra = auth.apply_user_keys(user["id"])
    if extra.get("GOOGLE_CLIENT_ID"):
        config.GOOGLE_CLIENT_ID = extra["GOOGLE_CLIENT_ID"]
    if extra.get("GOOGLE_CLIENT_SECRET"):
        config.GOOGLE_CLIENT_SECRET = extra["GOOGLE_CLIENT_SECRET"]
    client_id, secret = youtube_client(keys)
    redirect = request.session.get("youtube_redirect") or youtube_redirect(str(request.base_url))
    try:
        tokens = youtube_exchange(code, client_id, secret, redirect)
    except YouTubeError:
        return RedirectResponse("/settings?youtube=error", status_code=302)
    request.session.pop("youtube_oauth", None)
    request.session.pop("youtube_redirect", None)
    auth.save_youtube_tokens(user["id"], tokens)
    return RedirectResponse("/settings?youtube=ok", status_code=302)


@app.post("/api/youtube/disconnect")
def api_youtube_disconnect(request: Request):
    user = _require_user(request)
    return {"ok": True, "keys": auth.save_youtube_tokens(user["id"], {})}


@app.post("/api/library/{name}/youtube")
def api_youtube_upload(request: Request, name: str, body: YouTubeUploadBody):
    user = _require_user(request)
    name = _safe_name(name)
    stem = Path(name).stem
    if not _user_owns_stem(user["id"], stem):
        raise HTTPException(404, "Video not found")
    video = config.OUTPUT_DIR / (stem + ".mp4")
    if not video.exists() or not _inside(video, config.OUTPUT_DIR):
        raise HTTPException(404, "Video not found")
    tokens, client_id, secret = _youtube_creds(user)
    title = (body.title or stem.replace("_", " ")).strip()
    try:
        result = youtube_upload(
            video,
            tokens,
            client_id,
            secret,
            title=title,
            description=body.description,
            privacy=body.privacy,
            shorts=body.shorts,
            made_for_kids=body.made_for_kids,
        )
    except YouTubeError as exc:
        _youtube_http(exc)
    if result.get("tokens"):
        auth.save_youtube_tokens(user["id"], result["tokens"])
    _youtube_remember(user["id"], stem, result)
    return {
        "ok": True,
        "id": result.get("id") or "",
        "url": result.get("url") or "",
        "title": result.get("title") or title,
        "privacy": result.get("privacy") or body.privacy,
    }


@app.get("/api/youtube/videos")
def api_youtube_videos(request: Request):
    user = _require_user(request)
    tokens, client_id, secret = _youtube_creds(user)
    try:
        result = youtube_list(tokens, client_id, secret)
    except YouTubeError as exc:
        _youtube_http(exc)
    if result.get("tokens"):
        auth.save_youtube_tokens(user["id"], result["tokens"])
    return {"ok": True, "videos": result.get("videos") or []}


@app.patch("/api/youtube/videos/{video_id}")
def api_youtube_update(request: Request, video_id: str, body: YouTubeUpdateBody):
    user = _require_user(request)
    tokens, client_id, secret = _youtube_creds(user)
    try:
        result = youtube_update(
            video_id,
            tokens,
            client_id,
            secret,
            title=body.title,
            description=body.description,
            privacy=body.privacy,
        )
    except YouTubeError as exc:
        _youtube_http(exc)
    if result.get("tokens"):
        auth.save_youtube_tokens(user["id"], result["tokens"])
    video = result.get("video") or {}
    if video.get("id"):
        mapped = _youtube_map(user["id"])
        for stem, row in mapped.items():
            if (row or {}).get("id") == video["id"]:
                _youtube_remember(user["id"], stem, video)
                break
    return {"ok": True, "video": video}


@app.delete("/api/youtube/videos/{video_id}")
def api_youtube_remove(request: Request, video_id: str):
    user = _require_user(request)
    tokens, client_id, secret = _youtube_creds(user)
    try:
        result = youtube_delete(video_id, tokens, client_id, secret)
    except YouTubeError as exc:
        _youtube_http(exc)
    if result.get("tokens"):
        auth.save_youtube_tokens(user["id"], result["tokens"])
    _youtube_forget(user["id"], video_id)
    return {"ok": True, "id": video_id}


@app.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    user = _require_user(request)
    job = _read_job(job_id)
    if job.get("user_id") != user["id"]:
        raise HTTPException(404, "Unknown job")
    return job


@app.post("/api/generate")
def api_generate(request: Request, body: GenerateBody):
    global _current
    _rate_limit(request, "generate", 8, 60)
    user = _require_user(request)
    if not auth.generate_enabled():
        raise HTTPException(
            503,
            "This beta host saves accounts and keys. Generate still runs on your machine with `python studio.py`.",
        )
    prompt = clean_story(body.prompt or "") or (body.prompt or "").strip()
    if len(prompt.split()) < 8:
        raise HTTPException(400, "Write a fuller story — at least a few sentences.")
    allowed = {m["id"] for m in _video_models_for(user["id"])}
    model = (body.model or "").strip().lower()
    if model not in allowed:
        raise HTTPException(
            400,
            "That model needs an API key. Pick one from the list, or add the key in Settings.",
        )
    try:
        config.apply_aspect_ratio(body.size)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        seconds = config.apply_max_seconds(body.seconds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    with _lock:
        if _current is not None:
            raise HTTPException(409, "A render is already running. Wait for it to finish.")
        job_id = uuid.uuid4().hex[:8]
        title = effective_title(prompt, body.title)
        setting = effective_setting(prompt, body.setting)
        visuals = _visual_list(body.visuals) or derive_visuals(prompt, setting)
        shots = normalize_shots(body.shots or [])
        characters = [
            c for c in (body.characters or [])
            if isinstance(c, dict) and any(str(c.get(k) or "").strip() for k in ("name", "appearance", "clothing", "description"))
        ]
        stem = _slug(title, "jugaad_{0}".format(job_id))
        job = {
            "id": job_id,
            "user_id": user["id"],
            "status": "queued",
            "prompt": prompt,
            "model": model,
            "music": body.music or "random",
            "voice": resolve_voice(body.voice),
            "ref": (body.ref or "").strip(),
            "ref_role": (body.ref_role or "creature").strip().lower() or "creature",
            "board": (body.board or "").strip(),
            "size": body.size or "9:16",
            "length": body.length or "youtube",
            "seconds": seconds,
            "title": title,
            "setting": setting,
            "visuals": visuals,
            "shots": shots,
            "characters": characters,
            "stem": stem,
            "created": datetime.now().isoformat(timespec="seconds"),
            "log": "",
            "video": None,
            "error": None,
        }
        _write_job(job)
        _current = job_id

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return job


@app.post("/api/images/generate")
def api_images_generate(request: Request, body: ImageBody):
    _rate_limit(request, "images", 10, 60)
    global _current
    user = _require_user(request)
    prompt = (body.prompt or "").strip()
    if len(prompt.split()) < 3:
        raise HTTPException(400, "Write a short prompt — a scene or subject is enough.")
    style = (body.model or "comic").strip().lower()
    if style not in _IMAGE_STYLES:
        raise HTTPException(400, "Pick comic, cartoon, or anime.")
    try:
        config.apply_aspect_ratio(body.size)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    panels = int(body.panels or 0)
    if panels and (panels < 3 or panels > 16):
        raise HTTPException(400, "Panel count must be between 3 and 16.")

    with _lock:
        if _current is not None:
            raise HTTPException(409, "A render is already running. Wait for it to finish.")
        job_id = uuid.uuid4().hex[:8]
        title = effective_title(prompt, body.title)
        setting = effective_setting(prompt, body.setting)
        visuals = _visual_list(body.visuals) or derive_visuals(prompt, setting)
        folder = config.AI_IMAGE_DIR / stem
        video = config.OUTPUT_DIR / (stem + ".mp4")
        if folder.exists() or video.exists():
            stem = "{0}_{1}".format(stem, job_id)
        job = {
            "id": job_id,
            "kind": "images",
            "user_id": user["id"],
            "status": "queued",
            "prompt": prompt,
            "model": style,
            "size": body.size or "9:16",
            "title": title,
            "setting": setting,
            "visuals": visuals,
            "ref": (body.ref or "").strip(),
            "ref_role": (body.ref_role or "creature").strip().lower() or "creature",
            "stem": stem,
            "panels": [],
            "panel_count": panels,
            "created": datetime.now().isoformat(timespec="seconds"),
            "log": "",
            "error": None,
        }
        _write_job(job)
        _current = job_id

    thread = threading.Thread(target=_run_image_job, args=(job_id,), daemon=True)
    thread.start()
    return job


def _run_image_job(job_id: str) -> None:
    global _current
    job = _read_job(job_id)
    job["status"] = "running"
    job["log"] = "Drawing panels…\n"
    _write_job(job)
    log_lines = ["Drawing panels…"]
    prev_key = config.POLLINATIONS_API_KEY
    prev_size = (config.VIDEO_WIDTH, config.VIDEO_HEIGHT)
    prev_ref = getattr(config, "REFERENCE_IMAGE", "")
    prev_role = getattr(config, "REFERENCE_ROLE", "creature")
    user_id = job.get("user_id")
    try:
        extra = auth.apply_user_keys(int(user_id)) if user_id else {}
        if extra.get("POLLINATIONS_API_KEY"):
            config.POLLINATIONS_API_KEY = extra["POLLINATIONS_API_KEY"]
        elif auth.isolate_account_keys():
            config.POLLINATIONS_API_KEY = ""
        if job.get("ref") and user_id:
            config.REFERENCE_IMAGE = str(_ref_file(job["ref"], int(user_id)))
        config.REFERENCE_ROLE = (job.get("ref_role") or "creature").strip().lower() or "creature"
        try:
            config.apply_aspect_ratio(job.get("size") or "9:16")
        except ValueError:
            pass
        count = int(job.get("panel_count") or 0) or None
        stem = job["stem"]
        out_dir = config.AI_IMAGE_DIR / stem
        from image_engine import generate_board

        def on_panel(done, total, panel):
            public = _panel_public(stem, panel)
            current = _read_job(job_id)
            current["panels"] = (current.get("panels") or []) + [public]
            log_lines.append("[image] panel {0}/{1}: {2}".format(done, total, (panel.get("beat") or "")[:80]))
            current["log"] = "\n".join(log_lines)[-12000:]
            _write_job(current)

        visuals = [v for v in (job.get("visuals") or []) if str(v).strip()]
        if count and visuals:
            visuals = visuals[:count]
        part = {
            "text": job["prompt"],
            "broll_query": job.get("setting") or "",
        }
        if visuals:
            part["image_prompts"] = [visual_to_image_beat(v) for v in visuals]

        generate_board(
            part,
            out_dir,
            style=job["model"],
            character_seed=_seed_for(stem),
            panel_count=count,
            on_panel=on_panel,
            story=job.get("prompt") or "",
        )
        job = _read_job(job_id)
        job["status"] = "done"
        job["log"] = "\n".join(log_lines + ["Board ready."])[-20000:]
        _write_job(job)
    except Exception as exc:
        job = _read_job(job_id)
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"] = "\n".join(log_lines + [str(exc)])[-20000:]
        _write_job(job)
    finally:
        config.POLLINATIONS_API_KEY = prev_key
        config.VIDEO_WIDTH, config.VIDEO_HEIGHT = prev_size
        config.REFERENCE_IMAGE = prev_ref
        config.REFERENCE_ROLE = prev_role
        with _lock:
            if _current == job_id:
                _current = None


def _write_script(job: dict) -> Path:
    setting = job.get("setting") or "dark cinematic night village"
    visuals = [v for v in (job.get("visuals") or []) if str(v).strip()]
    shots = normalize_shots(job.get("shots") or [])
    characters = [
        c for c in (job.get("characters") or [])
        if isinstance(c, dict)
    ]
    extra = ""
    if visuals:
        extra += (
            '    "image_prompts": {0},\n'
            '    "broll_queries": {1},\n'
        ).format(
            repr([visual_to_image_beat(v) for v in visuals]),
            repr(visuals),
        )
    if shots:
        extra += '    "shots": {0},\n'.format(repr(shots))
    if characters:
        extra += '    "characters": {0},\n'.format(repr(characters))
    path = SCRIPTS / "{0}.py".format(job["stem"])
    path.write_text(
        "VIDEO_TYPE = {0}\n"
        "PARTS = [{{\n"
        "    \"text\": {1},\n"
        "    \"broll_query\": {2},\n"
        "{3}}}]\n".format(
            repr(job["model"]),
            repr(job["prompt"]),
            repr(setting),
            extra,
        )
    )
    return path


def _pipeline_python() -> str:
    """Prefer the venv CLI interpreter.

    Apple's Python.app (what Cursor often launches) has a 64KB main-thread
    stack. OpenBLAS then SIGSEGVs in gemm_thread_n and macOS shows
    "Python quit unexpectedly".
    """
    venv = ROOT / "venv" / "bin" / "python3"
    if venv.exists():
        return str(venv)
    exe = Path(sys.executable)
    if "Python.app" in exe.parts:
        for parent in exe.parents:
            cli = parent / "bin" / "python3"
            if cli.exists() and "Python.app" not in cli.parts:
                return str(cli)
    return sys.executable


def _run_job(job_id: str) -> None:
    global _current
    job = _read_job(job_id)
    job["status"] = "running"
    _write_job(job)
    script = _write_script(job)
    user_id = job.get("user_id")
    music = job["music"]
    if user_id and music not in ("random", "off", "", None):
        for path in list_music_tracks(int(user_id)):
            if path.stem == music or path.name == music:
                music = str(path)
                break
    cmd = [
        _pipeline_python(), "-u", str(ROOT / "main.py"),
        str(script),
        job["stem"],
        "model={0}".format(job["model"]),
        "music={0}".format(music),
        "voice={0}".format(resolve_voice(job.get("voice"))),
        "size={0}".format(job["size"]),
        "max={0}".format(job.get("seconds") if job.get("seconds") is not None else 180),
    ]
    if job.get("ref") and user_id:
        cmd.append("ref={0}".format(_ref_file(job["ref"], int(user_id))))
    if job.get("ref_role"):
        cmd.append("ref_role={0}".format(job["ref_role"]))
    if job.get("board"):
        cmd.append("board={0}".format(job["board"]))
    log_chunks = []
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    if sys.platform == "darwin":
        env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    if auth.isolate_account_keys():
        for name in auth.ACCOUNT_KEY_ENV:
            env.pop(name, None)
    if user_id:
        env.update(auth.apply_user_keys(int(user_id)))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            log_chunks.append(line)
            if len(log_chunks) % 3 == 0:
                job = _read_job(job_id)
                job["log"] = "".join(log_chunks)[-12000:]
                _write_job(job)
        code = proc.wait()
        job = _read_job(job_id)
        job["log"] = "".join(log_chunks)[-20000:]
        out = config.OUTPUT_DIR / "{0}.mp4".format(job["stem"])
        if code == 0 and out.exists():
            job["status"] = "done"
            job["video"] = "/media/output/{0}.mp4".format(job["stem"])
        else:
            job["status"] = "error"
            if code in (-11, 139):
                job["error"] = (
                    "Render crashed while loading TTS/video libraries. "
                    "Click Generate again. If it keeps failing, run it in Terminal from this folder."
                )
            else:
                job["error"] = "Render failed (exit {0}). Check the log.".format(code)
            if not job.get("log"):
                job["log"] = "Process died before printing a log (exit {0}).\n".format(code)
        _write_job(job)
    except Exception as exc:
        job = _read_job(job_id)
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"] = "".join(log_chunks)[-20000:]
        _write_job(job)
    finally:
        with _lock:
            if _current == job_id:
                _current = None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _remove_path(path: Path, root: Path, deleted):
    if not path.exists() or not _inside(path, root):
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    try:
        deleted.append(str(path.relative_to(ROOT)))
    except ValueError:
        deleted.append(str(path))


def _glob_existing(root: Path, pattern: str):
    if not root.exists():
        return []
    return list(root.glob(pattern))


def _iter_stem_paths(stem: str):
    """Every generated file/dir that belongs to this cut name."""
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        return
    named = [
        (config.OUTPUT_DIR, stem + ".mp4"),
        (config.AUDIO_DIR, stem + ".wav"),
        (config.AUDIO_DIR, stem + ".script.txt"),
        (config.AUDIO_DIR, stem + ".trim.wav"),
        (config.CAPTIONS_DIR, stem + "_words.json"),
        (config.BROLL_DIR, stem + "_broll.json"),
        (SCRIPTS, stem + ".py"),
    ]
    for root, name in named:
        yield root, root / name
    for path in _glob_existing(config.AUDIO_DIR, stem + "_chunk*"):
        yield config.AUDIO_DIR, path
    for path in _glob_existing(config.BROLL_DIR, stem + "_broll_*"):
        yield config.BROLL_DIR, path
    for root in (
        config.OUTPUT_DIR,
        config.AUDIO_DIR,
        config.CAPTIONS_DIR,
        config.BROLL_DIR,
        config.STILLS_DIR,
        config.AI_IMAGE_DIR,
        config.AI_VIDEO_DIR,
        SCRIPTS,
    ):
        for path in _glob_existing(root, stem + "TEMP_MPY*"):
            yield root, path
    for root in (config.STILLS_DIR, config.AI_IMAGE_DIR, config.AI_VIDEO_DIR):
        yield root, root / stem
        for path in _glob_existing(root, stem + "_*"):
            yield root, path
    pycache = SCRIPTS / "__pycache__"
    for path in _glob_existing(pycache, stem + "*"):
        yield SCRIPTS, path


def _disk_stems() -> set:
    """Cut names that still have files on disk, even if the mp4 is gone."""
    skip = {"voice_previews", "__pycache__", "__init__"}
    stems = set()
    if SCRIPTS.exists():
        for path in SCRIPTS.glob("*.py"):
            if path.stem not in skip:
                stems.add(path.stem)
    if config.AUDIO_DIR.exists():
        for path in config.AUDIO_DIR.iterdir():
            if path.name in skip or path.name.startswith("."):
                continue
            name = path.name
            if name.endswith(".script.txt"):
                stems.add(name[: -len(".script.txt")])
            elif name.endswith(".trim.wav"):
                stems.add(name[: -len(".trim.wav")])
            elif "_chunk" in name:
                stems.add(name.split("_chunk", 1)[0])
            elif name.endswith(".wav"):
                stems.add(path.stem)
    for folder, marker in (
        (config.CAPTIONS_DIR, "_words.json"),
        (config.BROLL_DIR, "_broll"),
    ):
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if marker in path.name:
                stems.add(path.name.split(marker, 1)[0])
    for folder in (config.STILLS_DIR, config.AI_IMAGE_DIR, config.AI_VIDEO_DIR):
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if path.name.startswith(".") or path.name in skip:
                continue
            stems.add(path.name)
    if config.OUTPUT_DIR.exists():
        for path in config.OUTPUT_DIR.iterdir():
            if "TEMP_MPY" in path.name:
                stems.add(path.name.split("TEMP_MPY", 1)[0])
            elif path.suffix.lower() == ".mp4":
                stems.add(path.stem)
    return {item for item in stems if item and item not in skip}


def _video_jobs_for_stem(stem: str):
    jobs = []
    for job_path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(job_path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("kind") == "images":
            continue
        if data.get("stem") != stem:
            continue
        jobs.append((job_path, data))
    return jobs


def _stem_mtime(stem: str) -> float:
    best = 0.0
    for _root, path in _iter_stem_paths(stem):
        try:
            if path.exists():
                best = max(best, path.stat().st_mtime)
        except OSError:
            continue
    for job_path, _data in _video_jobs_for_stem(stem):
        try:
            best = max(best, job_path.stat().st_mtime)
        except OSError:
            continue
    return best


def _stem_has_leftovers(stem: str) -> bool:
    if any(path.exists() for _root, path in _iter_stem_paths(stem)):
        return True
    return bool(_video_jobs_for_stem(stem))


def _library_items(user_id: int) -> list:
    allowed = _user_stems(user_id)
    uploaded = _youtube_map(user_id)
    items = []
    seen = set()
    videos = sorted(
        config.OUTPUT_DIR.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in videos:
        if path.stem not in allowed:
            continue
        seen.add(path.stem)
        items.append({
            "name": path.stem,
            "file": path.name,
            "url": "/media/output/{0}".format(path.name),
            "bytes": path.stat().st_size,
            "mtime": path.stat().st_mtime,
            "youtube": uploaded.get(path.stem) or None,
        })
    leftovers = []
    for stem in allowed:
        if stem in seen or not _stem_has_leftovers(stem):
            continue
        leftovers.append({
            "name": stem,
            "file": stem + ".mp4",
            "url": None,
            "bytes": 0,
            "mtime": _stem_mtime(stem),
            "orphan": True,
        })
    leftovers.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
    return (items + leftovers)[:24]


def _purge_library_stem(stem: str):
    """Permanently delete a cut and every generated file tied to that stem."""
    deleted = []
    seen = set()
    for root, path in _iter_stem_paths(stem):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        _remove_path(path, root, deleted)

    for job_path, _data in _video_jobs_for_stem(stem):
        _remove_path(job_path, JOBS_DIR, deleted)

    return deleted


def _purge_image_stem(stem: str, user_id: int):
    deleted = []
    for root in (config.AI_IMAGE_DIR, config.STILLS_DIR):
        _remove_path(root / stem, root, deleted)
        for path in _glob_existing(root, stem + "_*"):
            _remove_path(path, root, deleted)
    for job_path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(job_path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("kind") != "images":
            continue
        if data.get("stem") != stem:
            continue
        if data.get("user_id") != user_id:
            continue
        _remove_path(job_path, JOBS_DIR, deleted)
    return deleted


@app.delete("/api/images/{name}")
def api_delete_images(request: Request, name: str):
    user = _require_user(request)
    name = _safe_name(name)
    stem = Path(name).stem
    if not _user_owns_image(user["id"], stem):
        raise HTTPException(404, "Board not found")
    deleted = _purge_image_stem(stem, user["id"])
    if not deleted:
        raise HTTPException(404, "Board not found")
    return {"ok": True, "deleted": deleted}


@app.delete("/api/library/{name}")
def api_delete_library(request: Request, name: str):
    user = _require_user(request)
    name = _safe_name(name)
    stem = Path(name).stem
    if not _user_owns_stem(user["id"], stem):
        raise HTTPException(404, "Video not found")
    deleted = _purge_library_stem(stem)
    if not deleted:
        raise HTTPException(404, "Video not found")
    return {"ok": True, "deleted": deleted}


def _epidemic_http(exc: EpidemicError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _epidemic_stream(upstream, source_url: str):
    raw_type = upstream.headers.get("Content-Type") or ""
    ctype = epidemic_media_type(source_url, raw_type)
    if epidemic_is_hls(source_url, raw_type):
        try:
            body = upstream.content.decode("utf-8", "replace")
        finally:
            upstream.close()
        return Response(
            epidemic_rewrite_hls(body, source_url),
            media_type="application/vnd.apple.mpegurl",
        )

    def chunks():
        try:
            for chunk in upstream.iter_content(8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(chunks(), media_type=ctype)


@app.get("/api/voices/{voice_id}/preview")
def api_voice_preview(request: Request, voice_id: str):
    _require_user(request)
    speaker = resolve_voice(voice_id)
    allowed = {item["id"] for item in list_voices()}
    if speaker not in allowed:
        raise HTTPException(404, "Unknown voice")
    path = voice_preview_path(speaker)
    if path.exists() and path.stat().st_size > 2000:
        return FileResponse(str(path), media_type="audio/wav")
    with _voice_preview_lock:
        if path.exists() and path.stat().st_size > 2000:
            return FileResponse(str(path), media_type="audio/wav")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        env["VECLIB_MAXIMUM_THREADS"] = "1"
        env["NUMEXPR_NUM_THREADS"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        if sys.platform == "darwin":
            env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        cmd = [
            _pipeline_python(),
            "-u",
            "-c",
            "from tts_engine import ensure_voice_preview; ensure_voice_preview({0})".format(repr(speaker)),
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Voice sample took too long. Try again.")
        if proc.returncode != 0 or not path.exists() or path.stat().st_size < 2000:
            raise HTTPException(502, "Could not build that voice sample.")
    return FileResponse(str(path), media_type="audio/wav")


@app.get("/api/epidemic/tracks")
def api_epidemic_tracks(request: Request, q: str = "", offset: int = 0, limit: int = 24, kind: str = "music"):
    _rate_limit(request, "epidemic", 40, 60)
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            return epidemic_list(term=q, limit=limit, offset=offset, kind=kind)
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.get("/api/epidemic/preview/{track_id}")
def api_epidemic_preview(request: Request, track_id: str, kind: str = "music"):
    _rate_limit(request, "epidemic", 40, 60)
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            mode, payload = epidemic_open_preview(track_id, kind=kind)
            if mode == "file":
                return FileResponse(str(payload), media_type="audio/mpeg")
            upstream = epidemic_open_media(payload, trusted=True)
            source = upstream.url or payload
            return _epidemic_stream(upstream, source)
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.get("/api/epidemic/hls")
def api_epidemic_hls(request: Request, u: str = "", s: str = ""):
    _rate_limit(request, "epidemic", 40, 60)
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            upstream = epidemic_open_media(u, sig=s)
            return _epidemic_stream(upstream, u or upstream.url or "")
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.post("/api/epidemic/import")
def api_epidemic_import(request: Request, body: EpidemicImportBody):
    _rate_limit(request, "epidemic", 20, 60)
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            return epidemic_import(body.id, title=body.title, kind=body.kind)
    except EpidemicError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@app.post("/api/ref/upload")
async def api_ref_upload(request: Request, file: UploadFile = File(...)):
    user = _require_user(request)
    original = _safe_name(file.filename or "ref.jpg")
    ext = Path(original).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "Use a photo (.jpg, .png, .webp)")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "Keep the reference under 8 MB.")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(original).stem).strip("._") or "ref"
    folder = _ref_dir(user["id"])
    dest = folder / (stem + ".jpg")
    n = 2
    while dest.exists():
        dest = folder / "{0}_{1}.jpg".format(stem, n)
        n += 1
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    im.thumbnail((1280, 1280))
    im.save(dest, "JPEG", quality=88)
    return {
        "ok": True,
        "id": dest.name,
        "name": dest.name,
        "url": "/media/ref/{0}".format(dest.name),
    }


@app.get("/media/ref/{name}")
def media_ref(request: Request, name: str):
    user = _require_user(request)
    path = _ref_file(name, user["id"])
    return FileResponse(str(path), media_type="image/jpeg")


@app.post("/api/music/upload")
async def api_music_upload(request: Request, file: UploadFile = File(...)):
    user = _require_user(request)
    original = _safe_name(file.filename or "track.mp3")
    ext = Path(original).suffix.lower()
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        raise HTTPException(400, "Use an audio file (.mp3, .wav, .m4a)")
    raw = await file.read()
    if len(raw) > _MUSIC_MAX_BYTES:
        raise HTTPException(400, "Keep the track under 12 MB.")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(original).stem).strip("._") or "track"
    folder = _music_dir(user["id"])
    dest = folder / (stem + ext)
    n = 2
    while dest.exists():
        dest = folder / "{0}_{1}{2}".format(stem, n, ext)
        n += 1
    dest.write_bytes(raw)
    return {
        "ok": True,
        "id": dest.stem,
        "name": dest.name,
        "preview": "/media/music/{0}".format(dest.name),
    }


@app.get("/media/music/{name}")
def media_music(request: Request, name: str):
    user = _require_user(request)
    path = _music_file(name, user["id"])
    return FileResponse(str(path), media_type="audio/mpeg")


@app.get("/media/images/{stem}/{name}")
def media_image(request: Request, stem: str, name: str):
    user = _require_user(request)
    stem = _safe_name(stem)
    name = _safe_name(name)
    if not _user_owns_image(user["id"], stem):
        raise HTTPException(404, "Image not found")
    path = config.AI_IMAGE_DIR / stem / name
    if not path.exists() or not _inside(path, config.AI_IMAGE_DIR):
        raise HTTPException(404, "Image not found")
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/media/output/{name}")
def media_output(request: Request, name: str):
    user = _require_user(request)
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Bad filename")
    stem = Path(name).stem
    if not _user_owns_stem(user["id"], stem):
        raise HTTPException(404, "Video not found")
    path = config.OUTPUT_DIR / name
    if not path.exists() or not _inside(path, config.OUTPUT_DIR):
        raise HTTPException(404, "Video not found")
    return FileResponse(str(path), media_type="video/mp4")


app.mount("/", StaticFiles(directory=str(STATIC), html=False), name="static")

"""
JUGAAD studio — local web UI for the faceless reel pipeline.

    python studio.py
    open http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
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

from . import auth

STUDIO = Path(__file__).resolve().parent
STATIC = STUDIO / "static"
SCRIPTS = ROOT / "scripts" / "studio"
JOBS_DIR = ROOT / "jobs"
SCRIPTS.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="JUGAAD", version="0.1")

_lock = threading.Lock()
_current = None
_STATIC_EXT = {".css", ".js", ".map", ".ico", ".png", ".svg", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
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


def _require_user(request: Request) -> dict:
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")
    return user


def _page(name: str) -> FileResponse:
    return FileResponse(str(STATIC / name))


@contextmanager
def _epidemic_for(user_id: int):
    keys = auth.apply_user_keys(user_id)
    with epidemic_api_key(keys.get("EPIDEMIC_API_KEY") or ""):
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


def _user_stems(user_id: int) -> set:
    """Cuts this account can see: theirs, plus anything made before login existed."""
    visible = set()
    owners = _job_owners()
    for stem, uids in owners.items():
        claimed = {uid for uid in uids if uid is not None}
        if user_id in claimed or not claimed:
            visible.add(stem)
    for path in config.OUTPUT_DIR.glob("*.mp4"):
        claimed = {uid for uid in owners.get(path.stem, set()) if uid is not None}
        if user_id in claimed or not claimed:
            visible.add(path.stem)
    return visible


def _user_owns_stem(user_id: int, stem: str) -> bool:
    return stem in _user_stems(user_id)


class GenerateBody(BaseModel):
    prompt: str = Field(..., min_length=20)
    model: str = "live"
    music: str = "random"
    size: str = "9:16"
    title: str = ""
    setting: str = ""
    length: str = "youtube"
    seconds: int = 180


class EpidemicImportBody(BaseModel):
    id: str
    title: str = ""
    kind: str = "music"


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (cleaned[:40] or fallback)


def _job_path(job_id: str) -> Path:
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


def _options(user_id: int) -> dict:
    tracks = []
    for path in list_music_tracks():
        tracks.append({
            "id": path.stem,
            "name": path.name,
            "preview": "/media/music/{0}".format(path.name),
        })
    allowed = _user_stems(user_id)
    library = []
    for path in sorted(config.OUTPUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.stem not in allowed:
            continue
        library.append({
            "name": path.stem,
            "file": path.name,
            "url": "/media/output/{0}".format(path.name),
            "bytes": path.stat().st_size,
            "mtime": path.stat().st_mtime,
        })
    keys = auth.get_keys(user_id)
    return {
        "brand": "JUGAAD",
        "tagline": "Faceless studio",
        "models": [
            {"id": "live", "label": "Live b-roll", "hint": "Real Pexels stock, timed to the story"},
            {"id": "comic", "label": "2D comic", "hint": "Illustrated panels + camera motion"},
            {"id": "cartoon", "label": "Cartoon", "hint": "Flat cel-shaded stills"},
            {"id": "anime", "label": "Anime", "hint": "Clean line art stills"},
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
        "epidemic": {"enabled": bool((keys.get("epidemic") or "").strip())},
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
    }


@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "generate": auth.generate_enabled(),
        "postgres": auth.using_postgres(),
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
    user = auth.register_user(body.email, body.password, body.name)
    auth.login_session(request, user)
    return {"ok": True, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@app.post("/api/auth/login")
def api_login(request: Request, body: AuthBody):
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


@app.get("/api/options")
def api_options(request: Request):
    user = _require_user(request)
    return _options(user["id"])


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
    user = _require_user(request)
    if not auth.generate_enabled():
        raise HTTPException(
            503,
            "This beta host saves accounts and keys. Generate still runs on your machine with `python studio.py`.",
        )
    prompt = (body.prompt or "").strip()
    if len(prompt.split()) < 8:
        raise HTTPException(400, "Write a fuller story — at least a few sentences.")
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
        stem = _slug(body.title, "jugaad_{0}".format(job_id))
        job = {
            "id": job_id,
            "user_id": user["id"],
            "status": "queued",
            "prompt": prompt,
            "model": body.model,
            "music": body.music or "random",
            "size": body.size or "9:16",
            "length": body.length or "youtube",
            "seconds": seconds,
            "title": body.title.strip() or stem,
            "setting": (body.setting or "").strip(),
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


def _write_script(job: dict) -> Path:
    setting = job.get("setting") or "dark cinematic night village"
    path = SCRIPTS / "{0}.py".format(job["stem"])
    path.write_text(
        "VIDEO_TYPE = {0}\n"
        "PARTS = [{{\n"
        "    \"text\": {1},\n"
        "    \"broll_query\": {2},\n"
        "}}]\n".format(
            repr(job["model"]),
            repr(job["prompt"]),
            repr(setting),
        )
    )
    return path


def _run_job(job_id: str) -> None:
    global _current
    job = _read_job(job_id)
    job["status"] = "running"
    _write_job(job)
    script = _write_script(job)
    cmd = [
        sys.executable, "-u", str(ROOT / "main.py"),
        str(script),
        job["stem"],
        "model={0}".format(job["model"]),
        "music={0}".format(job["music"]),
        "size={0}".format(job["size"]),
        "max={0}".format(job.get("seconds") if job.get("seconds") is not None else 180),
    ]
    log_chunks = []
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    user_id = job.get("user_id")
    if user_id:
        env.update(auth.apply_user_keys(int(user_id)))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
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
            job["error"] = "Render failed (exit {0}). Check the log.".format(code)
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


def _purge_library_stem(stem: str):
    """Permanently delete a cut and every generated file tied to that stem."""
    deleted = []
    files = [
        (config.OUTPUT_DIR, config.OUTPUT_DIR / (stem + ".mp4")),
        (config.AUDIO_DIR, config.AUDIO_DIR / (stem + ".wav")),
        (config.AUDIO_DIR, config.AUDIO_DIR / (stem + ".script.txt")),
        (config.AUDIO_DIR, config.AUDIO_DIR / (stem + ".trim.wav")),
        (config.CAPTIONS_DIR, config.CAPTIONS_DIR / (stem + "_words.json")),
        (config.BROLL_DIR, config.BROLL_DIR / (stem + "_broll.json")),
        (SCRIPTS, SCRIPTS / (stem + ".py")),
    ]
    for root, path in files:
        _remove_path(path, root, deleted)

    for path in config.AUDIO_DIR.glob(stem + "_chunk*"):
        _remove_path(path, config.AUDIO_DIR, deleted)
    for path in config.BROLL_DIR.glob(stem + "_broll_*"):
        _remove_path(path, config.BROLL_DIR, deleted)

    for root in (config.STILLS_DIR, config.AI_IMAGE_DIR, config.AI_VIDEO_DIR):
        _remove_path(root / stem, root, deleted)

    for job_path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(job_path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("stem") == stem:
            _remove_path(job_path, JOBS_DIR, deleted)

    return deleted


@app.delete("/api/library/{name}")
def api_delete_library(request: Request, name: str):
    user = _require_user(request)
    name = _safe_name(name)
    stem = Path(name).stem
    if not _user_owns_stem(user["id"], stem):
        raise HTTPException(404, "Video not found")
    video = config.OUTPUT_DIR / (stem + ".mp4")
    if not video.exists() or not _inside(video, config.OUTPUT_DIR):
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


@app.get("/api/epidemic/tracks")
def api_epidemic_tracks(request: Request, q: str = "", offset: int = 0, limit: int = 24, kind: str = "music"):
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            return epidemic_list(term=q, limit=limit, offset=offset, kind=kind)
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.get("/api/epidemic/preview/{track_id}")
def api_epidemic_preview(request: Request, track_id: str, kind: str = "music"):
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            mode, payload = epidemic_open_preview(track_id, kind=kind)
            if mode == "file":
                return FileResponse(str(payload), media_type="audio/mpeg")
            upstream = epidemic_open_media(payload)
            source = upstream.url or payload
            return _epidemic_stream(upstream, source)
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.get("/api/epidemic/hls")
def api_epidemic_hls(request: Request, u: str = ""):
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            upstream = epidemic_open_media(u)
            return _epidemic_stream(upstream, u or upstream.url or "")
    except EpidemicError as exc:
        _epidemic_http(exc)


@app.post("/api/epidemic/import")
def api_epidemic_import(request: Request, body: EpidemicImportBody):
    user = _require_user(request)
    try:
        with _epidemic_for(user["id"]):
            return epidemic_import(body.id, title=body.title, kind=body.kind)
    except EpidemicError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@app.post("/api/music/upload")
async def api_music_upload(request: Request, file: UploadFile = File(...)):
    _require_user(request)
    original = _safe_name(file.filename or "track.mp3")
    ext = Path(original).suffix.lower()
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        raise HTTPException(400, "Use an audio file (.mp3, .wav, .m4a)")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(original).stem).strip("._") or "track"
    dest = config.MUSIC_DIR / (stem + ext)
    n = 2
    while dest.exists():
        dest = config.MUSIC_DIR / "{0}_{1}{2}".format(stem, n, ext)
        n += 1
    dest.write_bytes(await file.read())
    return {
        "ok": True,
        "id": dest.stem,
        "name": dest.name,
        "preview": "/media/music/{0}".format(dest.name),
    }


@app.get("/media/music/{name}")
def media_music(request: Request, name: str):
    _require_user(request)
    name = _safe_name(name)
    path = config.MUSIC_DIR / name
    if not path.exists() or path.parent.resolve() != config.MUSIC_DIR.resolve():
        raise HTTPException(404, "Track not found")
    return FileResponse(str(path), media_type="audio/mpeg")


@app.get("/media/output/{name}")
def media_output(request: Request, name: str):
    user = _require_user(request)
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Bad filename")
    stem = Path(name).stem
    if not _user_owns_stem(user["id"], stem):
        raise HTTPException(404, "Video not found")
    path = config.OUTPUT_DIR / name
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(str(path), media_type="video/mp4")


app.mount("/", StaticFiles(directory=str(STATIC), html=False), name="static")

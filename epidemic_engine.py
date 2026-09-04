"""
Epidemic Sound Partner API — browse, preview, download into MUSIC_DIR.

The API key stays on this machine (EPIDEMIC_API_KEY). Studio routes proxy
catalog/preview/download so the browser never sees it.

Docs: https://developers.epidemicsite.com/docs/getting-started/
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests

import config

_tls = threading.local()

DEFAULT_BASE = "https://partner-content-api.epidemicsound.com"
PARTNER_USER_ID = "jugaad-studio"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
_MEDIA_HOSTS = (
    "epidemicsound.com",
    "cloudfront.net",
)


class EpidemicError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@contextmanager
def api_key(key: str):
    """Use this account's Epidemic key for the current thread only."""
    prev = getattr(_tls, "key", None)
    _tls.key = key if key is not None else ""
    try:
        yield
    finally:
        _tls.key = prev


def _active_key() -> str:
    override = getattr(_tls, "key", None)
    if override is not None:
        return (override or "").strip()
    return (getattr(config, "EPIDEMIC_API_KEY", "") or "").strip()


def configured() -> bool:
    return bool(_active_key())


def safe_id(track_id: str) -> str:
    track_id = (track_id or "").strip()
    if not _ID_RE.fullmatch(track_id):
        raise EpidemicError("Bad track id", 400)
    return track_id


def _base() -> str:
    return (getattr(config, "EPIDEMIC_BASE_URL", "") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict:
    key = _active_key()
    if not key:
        raise EpidemicError("Add your Epidemic Sound key in Settings", 503)
    return {
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "x-partner-user-id": PARTNER_USER_ID,
    }


def _get(path: str, params=None) -> dict:
    url = path if path.startswith("http") else _base() + path
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=25)
    except requests.RequestException as exc:
        raise EpidemicError(
            "Epidemic Sound is unreachable ({0})".format(type(exc).__name__),
            502,
        ) from exc
    if resp.status_code == 401:
        raise EpidemicError("Epidemic Sound rejected the API key", 401)
    if resp.status_code == 403:
        raise EpidemicError("This sound is not available to download on your plan", 403)
    if resp.status_code == 404:
        raise EpidemicError("Sound not found", 404)
    if resp.status_code == 429:
        raise EpidemicError("Epidemic Sound rate limit — try again in a moment", 429)
    if resp.status_code >= 400:
        raise EpidemicError(
            "Epidemic Sound request failed ({0})".format(resp.status_code),
            502 if resp.status_code >= 500 else resp.status_code,
        )
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError as exc:
        raise EpidemicError("Epidemic Sound returned a bad response", 502) from exc


def _get_optional(path: str, params=None):
    """Like _get, but missing endpoints return None instead of raising."""
    url = path if path.startswith("http") else _base() + path
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=25)
    except requests.RequestException as exc:
        raise EpidemicError(
            "Epidemic Sound is unreachable ({0})".format(type(exc).__name__),
            502,
        ) from exc
    if resp.status_code in (400, 404):
        return None
    if resp.status_code == 401:
        raise EpidemicError("Epidemic Sound rejected the API key", 401)
    if resp.status_code == 403:
        return None
    if resp.status_code == 429:
        raise EpidemicError("Epidemic Sound rate limit — try again in a moment", 429)
    if resp.status_code >= 400:
        return None
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return None


def _length_seconds(raw) -> int:
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if n > 3600:
        return max(1, n // 1000)
    return max(0, n)


def _artists(track: dict) -> str:
    artists = track.get("mainArtists") or []
    if isinstance(artists, str):
        return artists
    return ", ".join(str(a) for a in artists if a)


def serialize_track(track: dict, collection: str = "", kind: str = "music") -> dict:
    images = track.get("images") or {}
    track_id = str(track.get("id") or "")
    kind = "sfx" if kind == "sfx" else "music"
    preview = None
    if track_id:
        preview = "/api/epidemic/preview/{0}?kind={1}".format(track_id, kind)
    return {
        "id": track_id,
        "title": track.get("title") or "Untitled",
        "artists": _artists(track) if kind == "music" else (collection or "Sound effect"),
        "length": _length_seconds(track.get("length")),
        "bpm": track.get("bpm"),
        "preview_only": bool(track.get("isPreviewOnly")),
        "cover": images.get("S") or images.get("XS") or images.get("default") or "",
        "collection": collection,
        "kind": kind,
        "hls": kind == "music",
        "preview": preview,
    }


def list_tracks(term: str = "", limit: int = 24, offset: int = 0, kind: str = "music") -> dict:
    kind = "sfx" if kind == "sfx" else "music"
    limit = max(1, min(int(limit or 24), 40))
    offset = max(0, int(offset or 0))
    term = (term or "").strip()
    if kind == "sfx":
        return _list_sfx(term, limit, offset)
    return _list_music(term, limit, offset)


def _list_music(term: str, limit: int, offset: int) -> dict:
    if term:
        data = _get(
            "/v0/tracks/search",
            {"term": term, "limit": limit, "offset": offset},
        )
        tracks = [serialize_track(t) for t in (data.get("tracks") or [])]
        pagination = data.get("pagination") or {}
        next_link = (data.get("links") or {}).get("next")
        return {
            "tracks": tracks,
            "offset": pagination.get("offset", offset),
            "limit": pagination.get("limit", limit),
            "has_more": bool(next_link),
            "source": "search",
            "kind": "music",
        }

    data = _get("/v0/collections", {"limit": 10, "offset": 0})
    seen = set()
    tracks = []
    for col in data.get("collections") or []:
        name = col.get("name") or ""
        for item in col.get("tracks") or []:
            tid = item.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            tracks.append(serialize_track(item, collection=name))
            if len(tracks) >= limit:
                break
        if len(tracks) >= limit:
            break
    return {
        "tracks": tracks,
        "offset": 0,
        "limit": limit,
        "has_more": False,
        "source": "collections",
        "kind": "music",
    }


def _list_sfx(term: str, limit: int, offset: int) -> dict:
    if term:
        data = _get(
            "/v0/sound-effects/search",
            {"term": term, "limit": limit, "offset": offset},
        )
        effects = data.get("soundEffects") or data.get("tracks") or []
        tracks = [serialize_track(t, kind="sfx") for t in effects]
        pagination = data.get("pagination") or {}
        next_link = (data.get("links") or {}).get("next")
        return {
            "tracks": tracks,
            "offset": pagination.get("offset", offset),
            "limit": pagination.get("limit", limit),
            "has_more": bool(next_link),
            "source": "search",
            "kind": "sfx",
        }

    data = _get("/v0/sound-effects/collections", {"limit": 8, "offset": 0})
    seen = set()
    tracks = []
    for col in data.get("collections") or []:
        name = col.get("name") or ""
        for item in col.get("soundEffects") or []:
            tid = item.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            tracks.append(serialize_track(item, collection=name, kind="sfx"))
            if len(tracks) >= limit:
                break
        if len(tracks) >= limit:
            break
    if not tracks:
        return _list_sfx("atmosphere", limit, offset)
    return {
        "tracks": tracks,
        "offset": 0,
        "limit": limit,
        "has_more": False,
        "source": "collections",
        "kind": "sfx",
    }


def allowed_media_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in _MEDIA_HOSTS)


def _hls_secret() -> bytes:
    try:
        from studio import auth

        material = auth.SECRET
    except Exception:
        material = (os.getenv("JUGAAD_SECRET") or os.getenv("SECRET_KEY") or "").strip()
    return hashlib.sha256(("jugaad-hls:" + (material or "")).encode("utf-8")).digest()


def media_sig(url: str) -> str:
    return hmac.new(_hls_secret(), (url or "").encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def check_media_sig(url: str, sig: str) -> bool:
    expected = media_sig(url)
    given = (sig or "").strip()
    if not given or len(given) != len(expected):
        return False
    return hmac.compare_digest(expected, given)


def proxy_media_path(url: str) -> str:
    return "/api/epidemic/hls?u={0}&s={1}".format(quote(url, safe=""), media_sig(url))


def rewrite_hls(body: str, base_url: str) -> str:
    """Point playlist URIs at our same-origin proxy so the browser can play HLS."""
    lines = []
    for raw in (body or "").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            abs_url = urljoin(base_url, stripped)
            if allowed_media_url(abs_url):
                lines.append(proxy_media_path(abs_url))
            else:
                lines.append(raw)
        else:
            lines.append(raw)
    return "\n".join(lines) + "\n"


def _stream_url(path: str) -> str:
    data = _get_optional(path)
    return ((data or {}).get("url") or "").strip()


def preview_cache_path(track_id: str) -> Path:
    folder = Path(config.ASSETS) / "epidemic_preview"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (safe_id(track_id) + ".mp3")


def preview_url(track_id: str, kind: str = "music") -> str:
    track_id = safe_id(track_id)
    kind = "sfx" if kind == "sfx" else "music"
    if kind == "sfx":
        url = _stream_url("/v0/sound-effects/{0}/stream".format(track_id))
        if not url:
            url = _stream_url("/v0/sound-effects/{0}/hls".format(track_id))
        if url:
            return url
        return _download_link(track_id, "sfx")
    url = _stream_url("/v0/tracks/{0}/stream".format(track_id))
    if not url:
        url = _stream_url("/v0/tracks/{0}/hls".format(track_id))
    if not url:
        data = _get("/v0/tracks/{0}/stream".format(track_id))
        url = (data.get("url") or "").strip()
    if not url:
        raise EpidemicError("No preview available for this track", 404)
    return url


def open_preview(track_id: str, kind: str = "music"):
    """Return ('file', Path) or ('url', cdn_url) for the studio preview route."""
    kind = "sfx" if kind == "sfx" else "music"
    track_id = safe_id(track_id)
    if kind == "sfx":
        cache = preview_cache_path(track_id)
        if cache.exists() and cache.stat().st_size > 1000:
            return "file", cache
        url = _stream_url("/v0/sound-effects/{0}/stream".format(track_id))
        if not url:
            url = _stream_url("/v0/sound-effects/{0}/hls".format(track_id))
        if url and ".m3u8" not in url.lower():
            return "url", url
        dl = _download_link(track_id, "sfx")
        try:
            audio = requests.get(dl, timeout=90)
        except requests.RequestException as exc:
            raise EpidemicError("Preview failed") from exc
        if audio.status_code >= 400 or not audio.content:
            if url:
                return "url", url
            raise EpidemicError("No preview available for this sound", 404)
        cache.write_bytes(audio.content)
        return "file", cache
    return "url", preview_url(track_id, kind)


def open_media(url: str, sig: str = "", trusted: bool = False):
    if not allowed_media_url(url):
        raise EpidemicError("Bad preview url", 400)
    if not trusted and not check_media_sig(url, sig):
        raise EpidemicError("Bad preview url", 400)
    try:
        resp = requests.get(url, stream=True, timeout=30)
    except requests.RequestException as exc:
        raise EpidemicError("Preview failed") from exc
    if resp.status_code >= 400:
        resp.close()
        raise EpidemicError("Preview failed", 502)
    return resp


def is_hls(url: str, content_type: str = "") -> bool:
    return media_type_for(url, content_type) == "application/vnd.apple.mpegurl"


def media_type_for(url: str, content_type: str = "") -> str:
    path = urlparse(url or "").path.lower()
    if path.endswith(".m3u8") or "mpegurl" in (content_type or "").lower():
        return "application/vnd.apple.mpegurl"
    if path.endswith(".ts"):
        return "video/mp2t"
    if path.endswith(".aac") or path.endswith(".m4s") or path.endswith(".mp4"):
        return "audio/mp4"
    if path.endswith(".mp3"):
        return "audio/mpeg"
    return content_type or "application/octet-stream"


def _download_link(track_id: str, kind: str) -> str:
    track_id = safe_id(track_id)
    if kind == "sfx":
        path = "/v0/sound-effects/{0}/download".format(track_id)
    else:
        path = "/v0/tracks/{0}/download".format(track_id)
    data = _get(path, {"format": "mp3", "quality": "normal"})
    url = (data.get("url") or "").strip()
    if not url:
        raise EpidemicError("No download URL for this sound", 404)
    return url


def _safe_stem(title: str, track_id: str, kind: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", title or "track").strip("._") or "track"
    slug = slug[:48]
    tid = re.sub(r"[^a-zA-Z0-9_-]+", "", track_id)[:16]
    prefix = "esfx" if kind == "sfx" else "es"
    if tid:
        return "{0}_{1}_{2}".format(prefix, tid, slug)
    return "{0}_{1}".format(prefix, slug)


def download_to(track_id: str, dest: Path, kind: str = "sfx") -> Path:
    url = _download_link(track_id, kind)
    try:
        audio = requests.get(url, timeout=90)
    except requests.RequestException as exc:
        raise EpidemicError("Could not download the Epidemic sound") from exc
    if audio.status_code >= 400 or not audio.content:
        raise EpidemicError("Could not download the Epidemic sound", 502)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio.content)
    return dest


def import_track(track_id: str, title: str = "", kind: str = "music") -> dict:
    kind = "sfx" if kind == "sfx" else "music"
    track_id = safe_id(track_id)
    title = (title or "").strip() or track_id
    url = _download_link(track_id, kind)
    try:
        audio = requests.get(url, timeout=90)
    except requests.RequestException as exc:
        raise EpidemicError("Could not download the Epidemic sound") from exc
    if audio.status_code >= 400 or not audio.content:
        raise EpidemicError("Could not download the Epidemic sound", 502)

    dest_dir = Path(config.MUSIC_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(title, track_id, kind)
    dest = dest_dir / (stem + ".mp3")
    n = 2
    while dest.exists():
        dest = dest_dir / "{0}_{1}.mp3".format(stem, n)
        n += 1
    dest.write_bytes(audio.content)
    return {
        "ok": True,
        "id": dest.stem,
        "name": dest.name,
        "preview": "/media/music/{0}".format(dest.name),
    }

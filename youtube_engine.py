"""YouTube Data API — OAuth connect and resumable mp4 upload.

Client id/secret stay on this machine. The browser never sees them.
Docs: https://developers.google.com/youtube/v3/guides/uploading_a_video
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
SCOPES = "https://www.googleapis.com/auth/youtube"
_ISO_DUR = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
_CHUNK = 8 * 1024 * 1024
_PRIVACY = {"unlisted", "private", "public"}


class YouTubeError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def redirect_uri(base_url: str = "") -> str:
    configured = (getattr(config, "YOUTUBE_REDIRECT_URI", "") or "").strip()
    if configured:
        return configured
    base = (base_url or "http://127.0.0.1:8787").rstrip("/")
    base = base.replace("://localhost", "://127.0.0.1")
    return base + "/youtube/callback"


def client_credentials(user_keys: dict = None) -> tuple:
    keys = user_keys or {}
    client_id = (
        (keys.get("google_client_id") or "").strip()
        or (getattr(config, "GOOGLE_CLIENT_ID", "") or "").strip()
    )
    secret = (
        (keys.get("google_client_secret") or "").strip()
        or (getattr(config, "GOOGLE_CLIENT_SECRET", "") or "").strip()
    )
    return client_id, secret


def has_client(user_keys: dict = None) -> bool:
    client_id, secret = client_credentials(user_keys)
    return bool(client_id and secret)


def auth_url(client_id: str, redirect: str, state: str) -> str:
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return AUTH_URL + "?" + query


def exchange_code(code: str, client_id: str, secret: str, redirect: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    data = _json(resp)
    if resp.status_code >= 400 or not data.get("access_token"):
        raise YouTubeError(_google_message(data) or "Google did not return tokens.", 400)
    tokens = _normalize_tokens(data)
    tokens["channel"] = channel_title(tokens["access_token"]) or ""
    return tokens


def refresh_tokens(tokens: dict, client_id: str, secret: str) -> dict:
    refresh = (tokens or {}).get("refresh_token") or ""
    if not refresh:
        raise YouTubeError("YouTube is not connected. Open Settings and connect.", 401)
    expiry = float(tokens.get("expiry") or 0)
    if tokens.get("access_token") and expiry > time.time() + 60:
        return tokens
    resp = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    data = _json(resp)
    if resp.status_code >= 400 or not data.get("access_token"):
        raise YouTubeError(
            _google_message(data) or "YouTube login expired. Connect again in Settings.",
            401,
        )
    merged = dict(tokens)
    merged.update(_normalize_tokens(data))
    if not merged.get("refresh_token"):
        merged["refresh_token"] = refresh
    if not merged.get("channel"):
        merged["channel"] = channel_title(merged["access_token"]) or ""
    return merged


def channel_title(access_token: str) -> str:
    try:
        resp = requests.get(
            CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": "Bearer " + access_token},
            timeout=20,
        )
        data = _json(resp)
        items = data.get("items") or []
        if items:
            return (items[0].get("snippet") or {}).get("title") or ""
    except requests.RequestException:
        return ""
    return ""


def upload_video(
    path: Path,
    tokens: dict,
    client_id: str,
    secret: str,
    title: str,
    description: str = "",
    privacy: str = "unlisted",
    shorts: bool = True,
    made_for_kids: bool = False,
) -> dict:
    path = Path(path)
    if not path.is_file():
        raise YouTubeError("Video file is gone.", 404)
    size = path.stat().st_size
    if size < 1000:
        raise YouTubeError("That file is too small to upload.", 400)
    fresh = refresh_tokens(tokens, client_id, secret)
    privacy = privacy if privacy in _PRIVACY else "unlisted"
    title = (title or path.stem).strip()[:100] or path.stem
    text = (description or "").strip()
    if shorts and "#shorts" not in text.lower() and "#shorts" not in title.lower():
        text = (text + "\n\n#Shorts").strip()
    body = {
        "snippet": {
            "title": title,
            "description": text,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }
    start = requests.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": "Bearer " + fresh["access_token"],
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        json=body,
        timeout=30,
    )
    if start.status_code >= 400:
        raise YouTubeError(_google_message(_json(start)) or "YouTube refused the upload.", start.status_code)
    location = start.headers.get("Location") or ""
    if not location:
        raise YouTubeError("YouTube did not start a resumable upload.")
    uploaded = _put_file(location, path, size)
    video_id = uploaded.get("id") or ""
    return {
        "id": video_id,
        "url": "https://youtu.be/{0}".format(video_id) if video_id else "",
        "title": title,
        "privacy": privacy,
        "tokens": fresh,
    }


def iso_seconds(raw: str) -> int:
    match = _ISO_DUR.match((raw or "").strip())
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _serialize_video(item: dict) -> dict:
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    details = item.get("contentDetails") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (
        (thumbs.get("medium") or {}).get("url")
        or (thumbs.get("default") or {}).get("url")
        or ""
    )
    video_id = item.get("id") or ""
    title = snippet.get("title") or ""
    description = snippet.get("description") or ""
    seconds = iso_seconds(details.get("duration") or "")
    hay = (title + " " + description).lower()
    return {
        "id": video_id,
        "url": "https://youtu.be/{0}".format(video_id) if video_id else "",
        "title": title,
        "description": description,
        "privacy": status.get("privacyStatus") or "",
        "thumb": thumb,
        "seconds": seconds,
        "shorts": seconds <= 180 or "#shorts" in hay,
        "published": snippet.get("publishedAt") or "",
    }


def list_videos(tokens: dict, client_id: str, secret: str, max_results: int = 25) -> dict:
    fresh = refresh_tokens(tokens, client_id, secret)
    headers = {"Authorization": "Bearer " + fresh["access_token"]}
    channel = requests.get(
        CHANNELS_URL,
        params={"part": "contentDetails", "mine": "true"},
        headers=headers,
        timeout=20,
    )
    data = _json(channel)
    if channel.status_code >= 400:
        raise YouTubeError(_google_message(data) or "Could not read the YouTube channel.", channel.status_code)
    items = data.get("items") or []
    playlist = ""
    if items:
        playlist = (
            ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
            or ""
        )
    if not playlist:
        return {"videos": [], "tokens": fresh}
    listed = requests.get(
        PLAYLIST_ITEMS_URL,
        params={
            "part": "contentDetails",
            "playlistId": playlist,
            "maxResults": max(1, min(int(max_results or 25), 50)),
        },
        headers=headers,
        timeout=20,
    )
    listed_data = _json(listed)
    if listed.status_code >= 400:
        raise YouTubeError(_google_message(listed_data) or "Could not list YouTube videos.", listed.status_code)
    ids = []
    for row in listed_data.get("items") or []:
        vid = ((row.get("contentDetails") or {}).get("videoId") or "").strip()
        if vid and vid not in ids:
            ids.append(vid)
    if not ids:
        return {"videos": [], "tokens": fresh}
    details = requests.get(
        VIDEOS_URL,
        params={"part": "snippet,status,contentDetails", "id": ",".join(ids)},
        headers=headers,
        timeout=20,
    )
    details_data = _json(details)
    if details.status_code >= 400:
        raise YouTubeError(_google_message(details_data) or "Could not load YouTube videos.", details.status_code)
    videos = [_serialize_video(item) for item in (details_data.get("items") or [])]
    return {"videos": videos, "tokens": fresh}


def update_video(
    video_id: str,
    tokens: dict,
    client_id: str,
    secret: str,
    title: str = None,
    description: str = None,
    privacy: str = None,
) -> dict:
    video_id = (video_id or "").strip()
    if not video_id:
        raise YouTubeError("Missing YouTube video id.", 400)
    fresh = refresh_tokens(tokens, client_id, secret)
    headers = {"Authorization": "Bearer " + fresh["access_token"]}
    got = requests.get(
        VIDEOS_URL,
        params={"part": "snippet,status", "id": video_id},
        headers=headers,
        timeout=20,
    )
    data = _json(got)
    if got.status_code >= 400:
        raise YouTubeError(_google_message(data) or "Could not load that YouTube video.", got.status_code)
    items = data.get("items") or []
    if not items:
        raise YouTubeError("YouTube video not found.", 404)
    item = items[0]
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    if title is not None:
        snippet["title"] = (title or "").strip()[:100] or snippet.get("title") or "Untitled"
    if description is not None:
        snippet["description"] = description
    if privacy:
        status["privacyStatus"] = privacy if privacy in _PRIVACY else status.get("privacyStatus")
    resp = requests.put(
        VIDEOS_URL,
        params={"part": "snippet,status"},
        headers=headers,
        json={"id": video_id, "snippet": snippet, "status": status},
        timeout=30,
    )
    payload = _json(resp)
    if resp.status_code >= 400:
        raise YouTubeError(_google_message(payload) or "Could not update that YouTube video.", resp.status_code)
    return {"video": _serialize_video(payload or item), "tokens": fresh}


def delete_video(video_id: str, tokens: dict, client_id: str, secret: str) -> dict:
    video_id = (video_id or "").strip()
    if not video_id:
        raise YouTubeError("Missing YouTube video id.", 400)
    fresh = refresh_tokens(tokens, client_id, secret)
    resp = requests.delete(
        VIDEOS_URL,
        params={"id": video_id},
        headers={"Authorization": "Bearer " + fresh["access_token"]},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise YouTubeError(_google_message(_json(resp)) or "Could not delete that YouTube video.", resp.status_code)
    return {"ok": True, "id": video_id, "tokens": fresh}


def _put_file(location: str, path: Path, size: int) -> dict:
    start = 0
    with path.open("rb") as fh:
        while start < size:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            end = start + len(chunk) - 1
            resp = requests.put(
                location,
                data=chunk,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": "bytes {0}-{1}/{2}".format(start, end, size),
                    "Content-Type": "video/mp4",
                },
                timeout=300,
            )
            if resp.status_code in (200, 201):
                return _json(resp)
            if resp.status_code != 308:
                raise YouTubeError(
                    _google_message(_json(resp)) or "Upload failed at {0}%.".format(int(end * 100 / size)),
                    resp.status_code,
                )
            start = end + 1
    raise YouTubeError("Upload finished without a YouTube video id.")


def _normalize_tokens(data: dict) -> dict:
    expires = int(data.get("expires_in") or 3600)
    out = {
        "access_token": data.get("access_token") or "",
        "expiry": time.time() + max(expires - 30, 60),
    }
    if data.get("refresh_token"):
        out["refresh_token"] = data["refresh_token"]
    return out


def _json(resp) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _google_message(data: dict) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        return (err.get("message") or err.get("status") or "").strip()
    if isinstance(err, str):
        desc = (data.get("error_description") or "").strip()
        return desc or err.replace("_", " ")
    return ""

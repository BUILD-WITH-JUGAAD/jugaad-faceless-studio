"""
Background bed from assets/background_music.

Drop royalty-free mp3/wav/m4a files in that folder. Pick one with
music=horror_piano, set MUSIC_TRACK in config, or leave empty for random.
"""

from __future__ import annotations

import random
from pathlib import Path

import config

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def list_music_tracks(user_id=None) -> list:
    folder = Path(getattr(config, "MUSIC_DIR", config.ASSETS / "background_music"))
    folder.mkdir(parents=True, exist_ok=True)
    tracks = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    ]
    if user_id is not None:
        extra = folder / "users" / str(int(user_id))
        if extra.is_dir():
            tracks.extend(
                p for p in extra.iterdir()
                if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
            )
    return sorted(tracks)


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def resolve_track(name: str) -> Path:
    """Match a filename, stem, or unique substring against shared beds.

    Per-account uploads live under MUSIC_DIR/users/{id}/. Pass those as an
    existing file path. This lookup never walks other users' folders —
    pick_background_music has no user context.
    """
    requested = (name or "").strip()
    if not requested:
        raise ValueError("Empty music track name")

    raw = Path(requested).expanduser()
    if raw.is_file():
        return raw.resolve()

    folder = Path(getattr(config, "MUSIC_DIR", config.ASSETS / "background_music"))
    direct = (folder / requested).resolve()
    if direct.is_file() and direct.parent == folder.resolve():
        return direct

    tracks = list_music_tracks()
    if not tracks:
        raise FileNotFoundError(
            "No tracks in assets/background_music/ (add .mp3/.wav files)"
        )

    needle = _norm(Path(requested).stem)
    exact, partial = [], []
    for track in tracks:
        stem = _norm(track.stem)
        filename = _norm(track.name)
        if stem == needle or filename == _norm(requested):
            exact.append(track)
        elif needle and needle in stem:
            partial.append(track)

    matches = exact or partial
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(t.name for t in tracks)
    if not matches:
        raise FileNotFoundError(
            f"Unknown music={requested!r}. Available: {names}"
        )
    raise ValueError(
        f"Ambiguous music={requested!r}. Matches: "
        + ", ".join(t.name for t in matches)
    )


def pick_background_music() -> Path:
    if not getattr(config, "MUSIC_ENABLED", True):
        return None
    requested = (getattr(config, "MUSIC_TRACK", "") or "").strip()
    if requested:
        chosen = resolve_track(requested)
        print(f"[music] {chosen.name}")
        return chosen
    tracks = list_music_tracks()
    if not tracks:
        print(
            "[music] no tracks in assets/background_music/ "
            "(add .mp3/.wav files to mix under the voice)"
        )
        return None
    chosen = random.choice(tracks)
    print(f"[music] {chosen.name} (random)")
    return chosen

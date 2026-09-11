"""
VisualBeat — one timed visual slot on a narration timeline.

Phase 2 of the visual intelligence upgrade: data model only.
Provider engines and run_pipeline do not consume this yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


def _as_str_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _as_preference(value) -> list:
    prefs = _as_str_list(value)
    if not prefs:
        return ["video", "image"]
    allowed = {"video", "image"}
    cleaned = []
    for item in prefs:
        key = item.strip().lower()
        if key in allowed and key not in cleaned:
            cleaned.append(key)
    return cleaned or ["video", "image"]


@dataclass
class VisualBeat:
    """One visual change aligned to a slice of narration."""

    id: str
    start: float
    end: float
    duration: float
    narration: str = ""
    subject: list = field(default_factory=list)
    action: list = field(default_factory=list)
    location: list = field(default_factory=list)
    time_context: str = None
    mood: str = None
    objects: list = field(default_factory=list)
    visual_intent: str = ""
    asset_type_preference: list = field(default_factory=lambda: ["video", "image"])
    queries: list = field(default_factory=list)
    fallback_queries: list = field(default_factory=list)
    candidate_assets: list = field(default_factory=list)
    selected_asset: Any = None
    relevance_score: float = 0.0

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip() or uuid4().hex[:12]
        self.start = float(self.start)
        self.end = float(self.end)
        self.narration = str(self.narration or "")
        self.subject = _as_str_list(self.subject)
        self.action = _as_str_list(self.action)
        self.location = _as_str_list(self.location)
        self.objects = _as_str_list(self.objects)
        self.queries = _as_str_list(self.queries)
        self.fallback_queries = _as_str_list(self.fallback_queries)
        self.asset_type_preference = _as_preference(self.asset_type_preference)
        self.visual_intent = str(self.visual_intent or "").strip()
        if self.time_context is not None:
            self.time_context = str(self.time_context).strip() or None
        if self.mood is not None:
            self.mood = str(self.mood).strip() or None
        if self.candidate_assets is None:
            self.candidate_assets = []
        else:
            self.candidate_assets = list(self.candidate_assets)
        self.relevance_score = float(self.relevance_score or 0.0)

        if self.start < 0:
            raise ValueError("VisualBeat start must be >= 0 (got {0})".format(self.start))
        if self.end <= self.start:
            raise ValueError(
                "VisualBeat end must be greater than start "
                "(start={0}, end={1})".format(self.start, self.end)
            )

        span = self.end - self.start
        if self.duration is None or float(self.duration) <= 0:
            self.duration = span
        else:
            self.duration = float(self.duration)
            if abs(self.duration - span) > 0.05:
                raise ValueError(
                    "VisualBeat duration ({0}) must match end-start ({1})".format(
                        self.duration, span,
                    )
                )
            self.duration = span

    def to_dict(self) -> dict:
        """Plain dict for JSON manifests and provider adapters."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VisualBeat":
        if not isinstance(data, dict):
            raise TypeError("VisualBeat.from_dict expects a dict")
        known = {
            "id", "start", "end", "duration", "narration", "subject", "action",
            "location", "time_context", "mood", "objects", "visual_intent",
            "asset_type_preference", "queries", "fallback_queries",
            "candidate_assets", "selected_asset", "relevance_score",
        }
        payload = {k: data[k] for k in known if k in data}
        if "id" not in payload:
            payload["id"] = uuid4().hex[:12]
        if "start" not in payload or "end" not in payload:
            raise ValueError("VisualBeat requires start and end")
        if "duration" not in payload:
            payload["duration"] = float(payload["end"]) - float(payload["start"])
        return cls(**payload)


def make_visual_beat(
    start: float,
    end: float,
    narration: str = "",
    *,
    beat_id: str = None,
    duration: float = None,
    subject=None,
    action=None,
    location=None,
    time_context: str = None,
    mood: str = None,
    objects=None,
    visual_intent: str = "",
    asset_type_preference=None,
    queries=None,
    fallback_queries=None,
    candidate_assets=None,
    selected_asset=None,
    relevance_score: float = 0.0,
) -> VisualBeat:
    """Convenience constructor; generates an id when beat_id is omitted."""
    if duration is None:
        duration = float(end) - float(start)
    return VisualBeat(
        id=beat_id or uuid4().hex[:12],
        start=start,
        end=end,
        duration=duration,
        narration=narration,
        subject=subject or [],
        action=action or [],
        location=location or [],
        time_context=time_context,
        mood=mood,
        objects=objects or [],
        visual_intent=visual_intent,
        asset_type_preference=asset_type_preference or ["video", "image"],
        queries=queries or [],
        fallback_queries=fallback_queries or [],
        candidate_assets=candidate_assets or [],
        selected_asset=selected_asset,
        relevance_score=relevance_score,
    )

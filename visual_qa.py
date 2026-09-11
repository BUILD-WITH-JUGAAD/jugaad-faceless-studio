"""
Visual QA — timeline validation, density metrics, and diagnostics (Phase 8).

Observes planner / adapter outputs. Does not render, download, or call providers.
Does not alter VISUAL_PLANNER_ENABLED behavior.
"""

from __future__ import annotations

from pathlib import Path

import config

# Tiny tolerance for float rounding vs narration duration.
_EPS = 0.05


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def _item_start(item) -> float:
    if hasattr(item, "start"):
        return _f(item.start)
    if isinstance(item, dict):
        return _f(item.get("start"))
    return 0.0


def _item_end(item) -> float:
    if hasattr(item, "end"):
        return _f(item.end)
    if isinstance(item, dict):
        return _f(item.get("end"))
    return 0.0


def _item_duration(item) -> float:
    if hasattr(item, "duration") and item.duration:
        return max(0.0, _f(item.duration))
    return max(0.0, _item_end(item) - _item_start(item))


def _asset_key(item) -> str:
    """Stable identity for reuse/duplicate detection (path > id > url)."""
    if item is None:
        return ""
    if isinstance(item, dict):
        for key in ("path", "asset_id", "id", "url", "file"):
            val = item.get(key)
            if val is None or val == "":
                continue
            if key == "path":
                return str(Path(val))
            return str(val)
        sel = item.get("selected_asset")
        if isinstance(sel, dict):
            return _asset_key(sel)
        return ""
    sel = getattr(item, "selected_asset", None)
    if sel is not None:
        return _asset_key(sel if isinstance(sel, dict) else {"id": sel})
    path = getattr(item, "path", None)
    return str(Path(path)) if path else ""


def _has_asset(item) -> bool:
    if item is None:
        return False
    if isinstance(item, dict):
        if item.get("miss") or item.get("failed"):
            return False
        if item.get("path") or item.get("selected_asset") or item.get("asset_id") or item.get("id"):
            # reused_previous still counts as a covering asset
            return True
        return False
    return bool(getattr(item, "selected_asset", None) or getattr(item, "path", None))


def validate_visual_beats(beats, narration_duration: float) -> dict:
    """
    Validate planned VisualBeat / timed-item timeline.

    Reports problems; does not repair them.
    """
    narration_duration = max(0.0, _f(narration_duration))
    rows = list(beats or [])
    issues = []
    ordered = True
    prev_end = None
    gap_total = 0.0
    overlap_total = 0.0
    durations = []

    for i, beat in enumerate(rows):
        start = _item_start(beat)
        end = _item_end(beat)
        dur = end - start
        durations.append(dur)
        bid = getattr(beat, "id", None) or (beat.get("beat_id") if isinstance(beat, dict) else None) or i

        if start < -_EPS:
            issues.append("beat {0}: start {1:.3f} < 0".format(bid, start))
        if end <= start + 1e-9:
            issues.append("beat {0}: end ({1:.3f}) <= start ({2:.3f})".format(bid, end, start))
        if dur <= 0:
            issues.append("beat {0}: non-positive duration {1:.3f}".format(bid, dur))
        if end > narration_duration + _EPS:
            issues.append(
                "beat {0}: end {1:.3f} exceeds narration {2:.3f}".format(
                    bid, end, narration_duration,
                )
            )
        if prev_end is not None:
            if start + _EPS < prev_end:
                overlap_total += prev_end - start
                issues.append(
                    "beat {0}: overlaps previous by {1:.3f}s".format(bid, prev_end - start)
                )
            elif start > prev_end + _EPS:
                gap_total += start - prev_end
                issues.append(
                    "beat {0}: gap {1:.3f}s after previous".format(bid, start - prev_end)
                )
            if start + _EPS < _item_start(rows[i - 1]):
                ordered = False
                issues.append("beat {0}: not chronological".format(bid))
        prev_end = max(prev_end or 0.0, end) if prev_end is not None else end
        # track chronological by start
        if i and start + _EPS < _item_start(rows[i - 1]):
            ordered = False

    coverage = 0.0
    if rows:
        # union coverage via sweep of intervals
        intervals = sorted(
            ((_item_start(b), _item_end(b)) for b in rows),
            key=lambda x: x[0],
        )
        cur_s, cur_e = intervals[0]
        covered = 0.0
        for s, e in intervals[1:]:
            if s <= cur_e + _EPS:
                cur_e = max(cur_e, e)
            else:
                covered += max(0.0, cur_e - cur_s)
                cur_s, cur_e = s, e
        covered += max(0.0, cur_e - cur_s)
        coverage = min(narration_duration, covered) if narration_duration else covered

    uncovered = max(0.0, narration_duration - coverage) if narration_duration else 0.0
    # leading/trailing gaps vs 0..duration
    if rows and narration_duration:
        first = _item_start(rows[0])
        last = _item_end(rows[-1])
        if first > _EPS:
            gap_total += first
            issues.append("leading gap {0:.3f}s before first beat".format(first))
        if last < narration_duration - _EPS:
            gap_total += narration_duration - last
            issues.append(
                "trailing gap {0:.3f}s after last beat".format(narration_duration - last)
            )

    return {
        "narration_duration": narration_duration,
        "beat_count": len(rows),
        "coverage_seconds": round(coverage, 3),
        "uncovered_seconds": round(uncovered, 3),
        "overlap_seconds": round(max(0.0, overlap_total), 3),
        "gap_seconds": round(max(0.0, gap_total), 3),
        "min_duration": round(min(durations), 3) if durations else 0.0,
        "avg_duration": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "max_duration": round(max(durations), 3) if durations else 0.0,
        "chronological": ordered and not any("not chronological" in x for x in issues),
        "issues": issues,
        "valid": len(issues) == 0,
    }


def density_metrics(beats, narration_duration: float) -> dict:
    """
    Density vs AGENTS.md targets, scaled by narration length.

    Short narrations are not judged against the 180s absolute beat counts.
    """
    narration_duration = max(0.0, _f(narration_duration))
    rows = list(beats or [])
    n = len(rows)
    holds = [_item_duration(b) for b in rows]
    preferred = _f(getattr(config, "VISUAL_BEAT_PREFERRED_SECONDS", 4.0), 4.0)
    soft_max = _f(getattr(config, "VISUAL_BEAT_MAX_SECONDS", 6.0), 6.0)
    hard_max = _f(getattr(config, "VISUAL_BEAT_HARD_MAX_SECONDS", 8.0), 8.0)

    seconds_per_beat = (narration_duration / n) if n else 0.0
    beats_per_minute = (n / (narration_duration / 60.0)) if narration_duration > 0 else 0.0
    pct_le_6 = (
        100.0 * sum(1 for h in holds if h <= soft_max + _EPS) / n if n else 0.0
    )
    pct_le_8 = (
        100.0 * sum(1 for h in holds if h <= hard_max + _EPS) / n if n else 0.0
    )
    over_hard = sum(1 for h in holds if h > hard_max + _EPS)

    # Scale 180s targets: ~24–50 beats → 8–16.7 beats/min; prefer ~4s holds.
    status = "ok"
    notes = []
    if narration_duration <= 0 or n == 0:
        status = "empty"
        notes.append("no beats to score")
    else:
        expected_min = max(1, int(round(narration_duration / hard_max)))
        expected_pref = max(1, int(round(narration_duration / preferred)))
        # For >= 90s apply absolute-style density guidance scaled from 180s.
        if narration_duration >= 90:
            scale = narration_duration / 180.0
            abs_min = max(1, int(round(24 * scale)))
            abs_pref_lo = max(1, int(round(30 * scale)))
            abs_pref_hi = max(abs_pref_lo, int(round(50 * scale)))
            if n < abs_min:
                status = "sparse"
                notes.append(
                    "beat count {0} below scaled minimum ~{1} for {2:.0f}s".format(
                        n, abs_min, narration_duration,
                    )
                )
            elif n < abs_pref_lo:
                status = "low"
                notes.append(
                    "beat count {0} under preferred ~{1}–{2}".format(
                        n, abs_pref_lo, abs_pref_hi,
                    )
                )
            elif over_hard:
                status = "holds_long"
                notes.append("{0} beat(s) exceed hard max {1:.0f}s".format(over_hard, hard_max))
            else:
                status = "ok"
        else:
            # Short form: judge hold lengths, not 180s absolute counts.
            if over_hard:
                status = "holds_long"
                notes.append("{0} beat(s) exceed hard max {1:.0f}s".format(over_hard, hard_max))
            elif seconds_per_beat > soft_max + 1.0:
                status = "low"
                notes.append(
                    "avg {0:.1f}s/beat above soft max {1:.0f}s".format(
                        seconds_per_beat, soft_max,
                    )
                )
            else:
                status = "ok"
            notes.append(
                "short narration; expected ~{0}–{1} beats at preferred hold".format(
                    expected_min, expected_pref,
                )
            )

    return {
        "beat_count": n,
        "beats_per_minute": round(beats_per_minute, 2),
        "seconds_per_beat": round(seconds_per_beat, 3),
        "pct_beats_le_6s": round(pct_le_6, 1),
        "pct_beats_le_8s": round(pct_le_8, 1),
        "beats_over_hard_max": over_hard,
        "preferred_seconds": preferred,
        "soft_max_seconds": soft_max,
        "hard_max_seconds": hard_max,
        "density_status": status,
        "notes": notes,
    }


def reuse_metrics(assets) -> dict:
    """Duplicate / adjacent-reuse diagnostics on timed assets."""
    rows = list(assets or [])
    keys = []
    for item in rows:
        if not _has_asset(item):
            keys.append(None)
            continue
        keys.append(_asset_key(item) or None)

    present = [k for k in keys if k]
    unique = set(present)
    adjacent = 0
    max_run = 1 if present else 0
    run = 1
    for i in range(1, len(keys)):
        if keys[i] and keys[i - 1] and keys[i] == keys[i - 1]:
            adjacent += 1
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1

    reused_refs = len(present) - len(unique)
    return {
        "asset_references": len(present),
        "unique_assets": len(unique),
        "reused_references": max(0, reused_refs),
        "adjacent_duplicates": adjacent,
        "max_consecutive_reuse": max_run if present else 0,
        "failed_or_missing": sum(1 for k in keys if k is None),
    }


def semantic_flags(beats, min_relevance: float = None) -> dict:
    """
    Diagnostic flags using existing relevance fields when present.
    Does not reject assets.
    """
    threshold = (
        _f(min_relevance)
        if min_relevance is not None
        else _f(getattr(config, "VISUAL_MIN_RELEVANCE_SCORE", 0.60), 0.60)
    )
    rows = list(beats or [])
    low = 0
    missing_intent = 0
    missing_query = 0
    fallbacks = 0
    details = []

    for i, beat in enumerate(rows):
        intent = ""
        queries = []
        score = None
        fallback = False
        sel = None
        if hasattr(beat, "visual_intent"):
            intent = (beat.visual_intent or "").strip()
            queries = list(beat.queries or [])
            sel = beat.selected_asset if isinstance(getattr(beat, "selected_asset", None), dict) else None
            # relevance_score defaults to 0.0 on VisualBeat — only trust it after selection
            if sel is not None and beat.relevance_score is not None:
                score = _f(beat.relevance_score)
        elif isinstance(beat, dict):
            intent = str(beat.get("visual_intent") or beat.get("beat") or "").strip()
            q = beat.get("queries") or beat.get("search_query") or beat.get("query")
            if isinstance(q, (list, tuple)):
                queries = [str(x) for x in q if x]
            elif q:
                queries = [str(q)]
            sel = beat.get("selected_asset") if isinstance(beat.get("selected_asset"), dict) else None
            if sel is None and (beat.get("path") or beat.get("asset_id")):
                sel = beat
            if beat.get("relevance_score") is not None and sel is not None:
                score = _f(beat.get("relevance_score"))
            elif isinstance(sel, dict) and sel.get("score") is not None:
                score = _f(sel.get("score"))

        if isinstance(sel, dict):
            if sel.get("used_fallback_query"):
                fallback = True
            if score is None and sel.get("score") is not None:
                score = _f(sel.get("score"))

        if not intent:
            missing_intent += 1
        if not queries and not (isinstance(sel, dict) and sel.get("search_query")):
            missing_query += 1
        if fallback:
            fallbacks += 1
        low_hit = False
        if score is not None and score < threshold:
            low_hit = True
        elif isinstance(sel, dict) and sel.get("low_confidence"):
            low_hit = True
        if low_hit:
            low += 1

        if low_hit or fallback or not intent:
            details.append({
                "index": i,
                "intent": intent[:80],
                "query": (queries[0] if queries else "")[:80],
                "relevance": score,
                "fallback": fallback,
            })

    return {
        "min_relevance_threshold": threshold,
        "low_relevance_beats": low,
        "missing_visual_intent": missing_intent,
        "missing_query": missing_query,
        "fallback_queries_used": fallbacks,
        "flagged": details[:24],
    }


def analyze_visual_plan(
    beats=None,
    assets=None,
    narration_duration: float = 0.0,
    *,
    min_relevance: float = None,
    provider: str = "",
) -> dict:
    """
    Combine timeline, density, reuse, and semantic diagnostics.

    `beats`   — planned VisualBeat[] (optional but preferred)
    `assets`  — renderer items [{path,start,end}, ...] and/or enriched beats
    """
    narration_duration = max(0.0, _f(narration_duration))
    planned = list(beats or [])
    rendered = list(assets if assets is not None else planned)

    timeline = validate_visual_beats(rendered if rendered else planned, narration_duration)
    # Density on what will actually appear on screen.
    density = density_metrics(rendered if rendered else planned, narration_duration)
    reuse = reuse_metrics(rendered)
    semantic = semantic_flags(planned if planned else rendered, min_relevance=min_relevance)

    successful = sum(1 for a in rendered if _has_asset(a))
    failed = max(0, len(rendered) - successful) if rendered else (
        sum(1 for b in planned if not _has_asset(b)) if planned else 0
    )

    coverage_pct = 0.0
    if narration_duration > 0:
        coverage_pct = 100.0 * timeline["coverage_seconds"] / narration_duration

    warnings = []
    errors = list(timeline.get("issues") or [])
    if density["density_status"] in {"sparse", "holds_long"}:
        warnings.extend(density.get("notes") or [])
    elif density["density_status"] == "low":
        warnings.extend(density.get("notes") or [])
    if reuse["adjacent_duplicates"]:
        warnings.append(
            "{0} adjacent duplicate asset pair(s)".format(reuse["adjacent_duplicates"])
        )
    if semantic["low_relevance_beats"]:
        warnings.append(
            "{0} low-relevance beat(s)".format(semantic["low_relevance_beats"])
        )
    if semantic["fallback_queries_used"]:
        warnings.append(
            "{0} beat(s) used a fallback query".format(semantic["fallback_queries_used"])
        )
    if failed:
        warnings.append("{0} missing/failed visual(s)".format(failed))

    if errors and not timeline["valid"]:
        status = "FAIL"
    elif warnings:
        status = "PASS WITH WARNINGS"
    elif not planned and not rendered:
        status = "EMPTY"
    else:
        status = "PASS"

    return {
        "provider": provider or "",
        "narration_duration": round(narration_duration, 3),
        "planned_beats": len(planned),
        "rendered_slots": len(rendered),
        "successful_assets": successful,
        "failed_assets": failed,
        "unique_assets": reuse["unique_assets"],
        "reused_assets": reuse["reused_references"],
        "adjacent_duplicates": reuse["adjacent_duplicates"],
        "max_consecutive_reuse": reuse["max_consecutive_reuse"],
        "coverage_pct": round(coverage_pct, 1),
        "avg_beat": timeline["avg_duration"],
        "min_beat": timeline["min_duration"],
        "max_beat": timeline["max_duration"],
        "gaps": timeline["gap_seconds"],
        "overlaps": timeline["overlap_seconds"],
        "fallbacks": semantic["fallback_queries_used"],
        "low_relevance_beats": semantic["low_relevance_beats"],
        "timeline": timeline,
        "density": density,
        "reuse": reuse,
        "semantic": semantic,
        "warnings": warnings,
        "errors": errors,
        "status": status,
    }


def format_visual_report(report: dict) -> str:
    """Concise human-readable VISUAL QA block."""
    report = report or {}
    lines = [
        "",
        "VISUAL QA",
        "---------",
        "Narration: {0:.1f}s".format(_f(report.get("narration_duration"))),
        "Planned beats: {0}".format(report.get("planned_beats", 0)),
        "Successful visuals: {0}".format(report.get("successful_assets", 0)),
        "Unique visuals: {0}".format(report.get("unique_assets", 0)),
        "Reused visuals: {0}".format(report.get("reused_assets", 0)),
        "Coverage: {0:.0f}%".format(_f(report.get("coverage_pct"))),
        "Average beat: {0:.2f}s".format(_f(report.get("avg_beat"))),
        "Max beat: {0:.2f}s".format(_f(report.get("max_beat"))),
        "Fallbacks: {0}".format(report.get("fallbacks", 0)),
        "Adjacent duplicates: {0}".format(report.get("adjacent_duplicates", 0)),
        "Low-relevance beats: {0}".format(report.get("low_relevance_beats", 0)),
        "Gaps: {0:.2f}s".format(_f(report.get("gaps"))),
        "Overlaps: {0:.2f}s".format(_f(report.get("overlaps"))),
    ]
    dens = report.get("density") or {}
    if dens:
        lines.append(
            "Density: {0} ({1:.1f} beats/min, {2:.2f}s/beat)".format(
                dens.get("density_status", "?"),
                _f(dens.get("beats_per_minute")),
                _f(dens.get("seconds_per_beat")),
            )
        )
    if report.get("provider"):
        lines.append("Provider: {0}".format(report["provider"]))
    lines.append("")
    lines.append("Status: {0}".format(report.get("status", "UNKNOWN")))
    warns = report.get("warnings") or []
    if warns:
        lines.append("Warnings:")
        for w in warns[:12]:
            lines.append("- {0}".format(w))
    errs = report.get("errors") or []
    if errs:
        lines.append("Issues:")
        for e in errs[:12]:
            lines.append("- {0}".format(e))
    lines.append("")
    return "\n".join(lines)


def log_beat_debug(beat, index: int = 0, provider: str = "") -> None:
    """Optional one-line debug (concise; no API payloads)."""
    start = _item_start(beat)
    end = _item_end(beat)
    dur = max(0.0, end - start)
    score = None
    fallback = "no"
    asset = ""
    if hasattr(beat, "relevance_score"):
        score = beat.relevance_score
        sel = beat.selected_asset if isinstance(getattr(beat, "selected_asset", None), dict) else {}
        if sel.get("used_fallback_query"):
            fallback = "yes"
        asset = str(sel.get("slug") or sel.get("id") or "")[:48]
    elif isinstance(beat, dict):
        score = beat.get("relevance_score") or beat.get("score")
        if beat.get("used_fallback_query") or (beat.get("selected_asset") or {}).get("used_fallback_query"):
            fallback = "yes"
        asset = str(Path(beat["path"]).name) if beat.get("path") else str(beat.get("asset_id") or "")[:48]
    score_s = "{0:.2f}".format(_f(score)) if score is not None else "n/a"
    print(
        "[VISUAL QA] beat={0} duration={1:.1f}s provider={2} relevance={3} fallback={4} asset={5}".format(
            index, dur, provider or "-", score_s, fallback, asset or "-",
        ),
        flush=True,
    )


def emit_visual_qa(
    *,
    narration_duration: float,
    assets: list,
    beats: list = None,
    provider: str = "",
    verbose_beats: bool = False,
) -> dict:
    """Analyze + print summary. Safe to call from run_pipeline planner branches only."""
    report = analyze_visual_plan(
        beats=beats,
        assets=assets,
        narration_duration=narration_duration,
        provider=provider,
    )
    if verbose_beats:
        for i, row in enumerate(beats or assets or []):
            log_beat_debug(row, index=i + 1, provider=provider)
    print(format_visual_report(report), flush=True)
    return report

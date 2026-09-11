# AGENTS.md

## Project

JUGAAD Faceless Studio is a local-first application that turns a written
story into a finished vertical video with:

- AI/local narration
- captions
- stock video or images
- illustrated stills
- optional AI-generated visuals
- background music
- ffmpeg/MoviePy assembly

The primary goal of this project is:

> Every visual shown in the final video must meaningfully correspond to the
> narration happening at that moment.

The application must not generate a video where a long narration is covered
by only a few repeated, generic, or unrelated visuals.

---

# Core Development Principle

DO NOT treat visual generation as:

Story -> 3 or 5 b-roll queries -> 3 or 5 assets -> long video

Instead treat it as:

Story
-> narration timeline
-> semantic beats
-> visual beats
-> multiple candidate assets per beat
-> relevance scoring
-> timeline scheduling
-> final assembly

Visual planning is a first-class stage of the pipeline.

---

# Visual Density Requirements

The final video must have sufficient visual variety.

Use these defaults unless explicitly overridden:

## Visual change targets

For video/photo based content:

- minimum visual change frequency: every 6 seconds
- preferred frequency: every 3 to 5 seconds
- fast-paced content: every 2 to 3.5 seconds
- absolute maximum continuous reuse of one visual: 8 seconds

For a 180-second video:

- preferred target: 30 to 50 visual beats
- minimum target: 24 visual beats
- never create only 3 to 10 visuals for a 3-minute video unless explicitly requested

A "visual beat" can be:

- a new video clip
- a new image
- a crop/reframe of an image ONLY when it still supports the narration
- a cutaway
- an establishing shot
- a detail shot
- a reaction/mood shot
- a location shot
- a symbolic shot

Do not fake visual density by repeatedly reusing unrelated images.

---

# Semantic Alignment Rule

Every visual beat must answer:

"What is being said in the narration right now?"

Before selecting an asset, identify:

1. Subject
2. Action
3. Location
4. Time/era
5. Emotion/mood
6. Important objects
7. Narrative purpose

Example:

Narration:
"The fisherman returned to the village just before dawn, carrying the strange
object he had found in the sea."

Visual plan should not search only:

"ocean"

Instead create multiple possible visual concepts:

1. fisherman walking toward village dawn
2. empty coastal village before sunrise
3. wet boots walking on sand
4. mysterious object in hands
5. waves at dawn
6. nervous fisherman close-up
7. old fishing boat on shore

Each beat should receive specific search queries.

---

# Never Use Generic Search for a Specific Beat

Bad:

"dark"
"history"
"scary"
"ocean"
"person"
"city"

Better:

"abandoned coastal village fog sunrise"
"fisherman walking beach carrying object dawn"
"stormy Indian Ocean waves close up"
"old wooden fishing boat shore twilight"
"mysterious artifact wet hands cinematic"

Search queries should combine meaningful attributes where possible:

SUBJECT + ACTION + LOCATION + MOOD/TIME

---

# Two-Level Visual Planning

Implement two planning levels.

## Level 1: Narrative scenes

Split the story into major narrative scenes.

Example:

1. Introduction
2. Setting
3. Character introduction
4. Trigger event
5. Discovery
6. Escalation
7. Climax
8. Resolution

## Level 2: Visual beats

Each scene must be split into multiple visual beats based on narration timing.

Example:

Scene:
"That night, the villagers heard something moving outside their homes."

Visual beats:

1. night exterior of village
2. empty street
3. window moving in wind
4. frightened silhouette
5. footsteps
6. door slowly opening
7. darkness outside

The number of visual beats should depend on duration, not merely the number
of paragraphs in the input script.

---

# Timestamp-Driven Planning

Do not assign one visual per paragraph.

Generate narration timing first, or use sentence/word timing when available.

Use actual timestamps to create visual beats.

Suggested workflow:

1. Generate TTS.
2. Obtain narration duration.
3. Obtain Whisper word timestamps if available.
4. Split narration into semantic units.
5. Assign timestamps to each unit.
6. Create visual beats approximately every 2-6 seconds.
7. Keep visual changes aligned with sentence boundaries when possible.
8. Allow cuts inside long sentences when a meaningful visual transition exists.

Never force the number of visuals to equal the number of original PARTS.

---

# Visual Beat Data Model

Prefer a structured model similar to:

```python
VisualBeat = {
    "id": str,
    "start": float,
    "end": float,
    "duration": float,

    "narration": str,

    "subject": list[str],
    "action": list[str],
    "location": list[str],
    "time_context": str | None,
    "mood": str | None,
    "objects": list[str],

    "visual_intent": str,

    "asset_type_preference": [
        "video",
        "image"
    ],

    "queries": list[str],
    "fallback_queries": list[str],

    "candidate_assets": list,
    "selected_asset": object | None,

    "relevance_score": float
}

# Implementation Safety Rules

## Do Not Rewrite the Existing Pipeline

This project is being upgraded incrementally.

Never rewrite the entire pipeline merely to introduce visual planning.

Preserve existing:

* CLI
* Studio
* configuration
* PARTS format
* narration generation
* Coqui TTS
* Whisper
* captions
* music
* SFX
* MoviePy
* FFmpeg
* Pexels
* Pixabay
* Unsplash
* Pixazo
* AI image generation
* AI video generation
* comic modes
* cartoon/anime modes
* reference-image functionality
* character consistency
* caching
* output structure

## Backwards Compatibility

Existing input must continue to work:

```python
PARTS = [
    {
        "text": "...",
        "broll_query": "..."
    }
]
```

Do not require existing projects to change their PARTS structure.

Existing explicit visual queries/prompts must be respected.

The new visual planner should enhance existing input rather than silently discard it.

## Safe Integration

Prefer this architecture:

```text
existing narration
      ↓
existing Whisper timestamps
      ↓
new visual planner
      ↓
VisualBeat[]
      ↓
existing provider engines
      ↓
existing renderer
```

Do not replace working provider engines unless there is a demonstrated technical reason.

Use adapters/wrappers where possible.

## Feature Flag

The new visual planner must have a safe fallback/feature flag.

Prefer:

```python
VISUAL_PLANNER_ENABLED = True
```

When disabled, the existing visual-generation behavior must remain available.

Do not duplicate the entire rendering pipeline just to implement the feature flag.

## Existing Configuration

Do not remove or silently change the meaning of existing configuration variables.

If a new setting is required, add a new configuration variable with a sensible default.

Existing user configuration files must continue to load.

## Provider Independence

The visual planner should describe WHAT should be shown.

The provider decides HOW to obtain/render it.

For example:

```text
VisualBeat
    ↓
Pexels → stock video
Pixabay → stock video
Unsplash → image
AI image → generated still
Comic → comic panel
AI video → generated clip
```

Do not force all providers into one rendering implementation.

## No Fake Visual Density

Never increase visual count simply to satisfy a target number.

Every visual beat must have a meaningful relationship to the narration.

Do not repeatedly reuse unrelated assets merely to increase the number of cuts.

## No Blind API Selection

Never automatically choose the first returned API asset when multiple candidates exist.

Use the project's relevance scoring/ranking system.

## Incremental Implementation

Implement the visual upgrade in phases:

1. Audit
2. VisualBeat model
3. Timing/planner
4. Query generation
5. Ranking + dedupe
6. Stock provider integration
7. AI/comic/provider integration
8. QA + logging
9. Regression tests

Do not implement all phases simultaneously unless explicitly instructed.

After each phase:

1. Run relevant tests.
2. Inspect the diff.
3. Confirm existing functionality has not been removed.
4. Report changed files.
5. Stop and wait for approval before beginning the next major phase.

## Testing Requirement

"The code runs" is not sufficient.

For the visual pipeline, verify actual visual density and timing.

At minimum test:

* 30-second narration
* 60-second narration
* 180-second narration

For 180 seconds, the system should normally produce approximately 30–50 visual beats, subject to narration semantics and available assets.

Never fabricate test results.

Report actual measurements.

## Change Minimization

Before modifying a function, determine whether it can be:

* reused
* wrapped
* extended
* adapted

Prefer those approaches over replacement.

Do not make unrelated cleanup/refactoring changes while implementing the visual intelligence system.

## Rollback Safety

Keep each implementation phase independently reversible.

Do not combine unrelated changes into one large commit.

A failed visual-planning experiment must not make it difficult to restore the previously working application.

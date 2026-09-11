"""
Central config for the faceless horror-reel pipeline.
Everything here is free:
- Coqui TTS: fully local, no key, no quota
- Whisper: fully local, no key, no quota
- Pexels / Pixabay / Unsplash: free API keys for stock
- Pixazo: free fair-use AI video (LTX)
- Pollinations: free illustrated stills (paid optional video)

Get a free Pexels key at: https://www.pexels.com/api/  (takes 2 minutes)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---- Paths ----
ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
AUDIO_DIR = ASSETS / "audio"
BROLL_DIR = ASSETS / "broll"
STILLS_DIR = ASSETS / "stills"
AI_IMAGE_DIR = ASSETS / "ai_images"
AI_VIDEO_DIR = ASSETS / "ai_video"
CAPTIONS_DIR = ASSETS / "captions"
MUSIC_DIR = ASSETS / "background_music"
SFX_DIR = ASSETS / "sfx"
REFS_DIR = ASSETS / "refs"
OUTPUT_DIR = ROOT / "output"

for d in [AUDIO_DIR, BROLL_DIR, STILLS_DIR, AI_IMAGE_DIR, AI_VIDEO_DIR, CAPTIONS_DIR, MUSIC_DIR, SFX_DIR, REFS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Visual pipeline. Scripts and CLI can override (model=live, model=comic, …).
#   live / realistic_broll / pexels — real stock video from Pexels (assets/broll)
#   pixabay                         — real stock video from Pixabay (assets/broll)
#   photos / unsplash               — stock photos + Ken Burns (assets/stills)
#   comic / 2d_comic                — illustrated stills + camera motion
#   cartoon / anime                 — other illustrated stills
#   pixazo                          — free Pixazo LTX AI video (optional key)
#   ai_video                        — paid Pollinations video (optional)
VIDEO_TYPE = "live"

# Pollinations still model when VIDEO_TYPE is comic/cartoon/anime.
# Empty = pick per style (flux-anime for comic/cartoon/anime). CLI: image_model=flux
IMAGE_MODEL = ""

# Mix a track from assets/background_music under the voiceover.
# MUSIC_TRACK: filename or unique substring (e.g. "horror_piano"). Empty = random.
MUSIC_ENABLED = True
MUSIC_TRACK = ""
MUSIC_VOLUME = 0.12
# Upper bound on legacy (VISUAL_PLANNER_ENABLED=False) stock clip/photo counts.
# When the planner is on, beat count comes from visual_planner — this is unused.
BROLL_CLIP_COUNT = 12

# Visual intelligence planner (Phase 6 stock + Phase 7 illustrated/generative).
# False → existing fetch_story_broll / generate_photo_storyboard / generate_storyboard /
# generate_pixazo_storyboard / generate_video_storyboard paths unchanged.
VISUAL_PLANNER_ENABLED = True
# Phase 8: print VISUAL QA summary after planner-enabled visual builds (diagnostics only).
VISUAL_QA_ENABLED = True
# Preferred visual change interval (seconds). Planner targets this range.
VISUAL_BEAT_MIN_SECONDS = 2.0
VISUAL_BEAT_PREFERRED_SECONDS = 4.0
VISUAL_BEAT_MAX_SECONDS = 6.0
# Absolute max continuous hold before the planner force-splits a unit.
VISUAL_BEAT_HARD_MAX_SECONDS = 8.0
# Phase 5 ranking: normalized 0–1 relevance floor (best available still used if none pass).
VISUAL_MIN_RELEVANCE_SCORE = float(os.getenv("VISUAL_MIN_RELEVANCE_SCORE", "0.60") or 0.60)
# Penalties applied after semantic score (still allow reuse when it is the only viable pick).
VISUAL_USED_ASSET_PENALTY = 0.15
VISUAL_ADJACENT_ASSET_PENALTY = 0.35

# Same integer across a series makes Pollinations keep a related look.
# Scripts can also set CHARACTER_SEED / CHARACTER_LOCK.
CHARACTER_SEED = None
# Optional local image used as a character/style lock for comic/cartoon/anime
# (and as the first frame for AI video). CLI: ref=path/to/face.jpg
REFERENCE_IMAGE = ""
# How the photo is used. CLI: ref_role=creature|character|style|off
#   creature  — ghost/monster only (default). People follow the story's he/she.
#   character — lock every human face to the photo
#   style     — mood only, do not copy the face
#   off       — ignore the photo
REFERENCE_ROLE = "creature"
# Existing Images-tab board to time under a new voiceover. CLI: board=stem
REUSE_BOARD_DIR = ""

# Appended to every comic prompt so the channel stays visually consistent.
# Punchy cel-animation stills, not muddy concept art.
COMIC_STYLE_SUFFIX = (
    ", full-bleed vertical 9:16 2D animation KEYFRAME, thick black ink outlines, "
    "flat saturated cel-shaded color, neon rim light, dramatic camera angle, "
    "speed lines, high contrast graphic novel, energetic horror anime still, "
    "NOT oil painting, NOT photorealistic, NOT 3D, NOT desaturated, NOT muddy, "
    "no letterbox, no black bars, no panel border, no text, no watermark"
)
CARTOON_STYLE_SUFFIX = (
    ", full-bleed vertical 9:16 2D cartoon animation keyframe, thick ink outlines, "
    "flat saturated color fills, cel shading, action pose, speed lines, "
    "NOT photorealistic, NOT 3D, NOT muddy, no letterbox, no text, no watermark"
)
ANIME_STYLE_SUFFIX = (
    ", full-bleed vertical 9:16 2D anime keyframe, clean line art, saturated cel "
    "shading, dramatic camera, motion blur, horror atmosphere, "
    "NOT photorealistic, NOT 3D, no letterbox, no photograph, no text, no watermark"
)

# Camera move: smash-zoom from START down to END (1.0 = no zoom).
KEN_BURNS_ZOOM = 1.14
SMASH_ZOOM_START = 1.55
SMASH_ZOOM_END = 1.12
# How many comic panels to cut between during one part.
COMIC_PANELS = 10
# Overlap between panels. 0 = hard cut (punchier for reels).
COMIC_CROSSFADE = 0.0
# Whip-slide + impact flash on each panel cut.
WHIP_DURATION = 0.16
IMPACT_FLASH = 0.08
CAMERA_SHAKE = 14

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt/"
POLLINATIONS_VIDEO_URL = "https://gen.pollinations.ai/video/"
# Image stills work anonymously. Video models are paid — get a key at
# https://enter.pollinations.ai/keys  and put POLLINATIONS_API_KEY in .env
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")
# wan-fast: 5s silent 480p, cheapest. veo / seedance-pro / wan are higher quality.
AI_VIDEO_MODEL = "wan-fast"
AI_VIDEO_SECONDS = 5

# Pixazo free tier (Flux + LTX 2.3). Email signup, no card: https://www.pixazo.ai/api/free
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY", "")
PIXAZO_GATEWAY = "https://gateway.pixazo.ai"
# Free LTX base (async queue). Old /ltx/text-to-video path returns 404.
PIXAZO_VIDEO_PATH = "/ltx-video/v1/text-to-video"
PIXAZO_IMAGE_PATH = "/flux/text-to-image"
# Free LTX is slow; hard cap on clips. Actual count also scales with duration
# (~1 cut / 5s with shot plans). Override with PIXAZO_MAX_CLIPS=8 etc.
PIXAZO_MAX_CLIPS = int(os.getenv("PIXAZO_MAX_CLIPS", "12") or 12)
# LTX clip length (frames / fps). Free default ~2s; raise only if your tier allows.
PIXAZO_NUM_FRAMES = int(os.getenv("PIXAZO_NUM_FRAMES", "49") or 49)
PIXAZO_FRAME_RATE = int(os.getenv("PIXAZO_FRAME_RATE", "24") or 24)

# ---- TTS settings ----
# Coqui TTS model — this one is a good, moody, low-cost-to-run English voice.
# Full model list: https://github.com/coqui-ai/TTS#model-list
TTS_MODEL = "tts_models/en/vctk/vits"
TTS_SPEAKER = "p326"  # horror default in this checkpoint (sounds female; VCTK IDs are scrambled)
# Curated VCTK speakers for the studio. Same local model, no extra download.
# Coqui's tts_models/en/vctk/vits embeddings do NOT match VCTK speaker-info.txt
# (known zoo bug). Labels below are by ear for this checkpoint, not corpus metadata.
TTS_VOICES = (
    {"id": "p326", "label": "Deep Male", "hint": "Horror default"},
    {"id": "p270", "label": "Low Female", "hint": "Calm, even"},
    {"id": "p232", "label": "Grave Male", "hint": "Heavier read"},
    {"id": "p364", "label": "Warm Female", "hint": "Softer narration"},
    {"id": "p376", "label": "Steady Male", "hint": "Even pacing"},
    {"id": "p243", "label": "Young Female", "hint": "Brighter, quicker"},
    {"id": "p267", "label": "Warm Male", "hint": "Storyteller"},
    {"id": "p225", "label": "Clear Female", "hint": "Direct read"},
    {"id": "p228", "label": "Soft Male", "hint": "Quiet, close"},
    {"id": "p294", "label": "Bright Female", "hint": "Crisp read"},
)

# ---- Whisper settings ----
WHISPER_MODEL = "small"   # tiny/base/small/medium/large — small is a good speed/accuracy tradeoff on CPU

# ---- OpenAI (story expand — Settings or .env; optional if Ollama is used) ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Local Ollama — no key. Default model fits a 16 GB Mac.
OLLAMA_HOST = (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL") or "gemma3:4b"
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")  # put your free key in a .env file
PEXELS_ORIENTATION = "portrait"  # unused for search; we crop landscape stock to 9:16
# Free stock video: https://pixabay.com/api/docs/
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
# Free HD photos: https://unsplash.com/developers (Access Key)
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

# ---- Epidemic Sound Partner API (music + SFX into assets/background_music) ----
# Key from the developer portal. Never expose it to the browser.
# https://developers.epidemicsite.com/docs/getting-started/
EPIDEMIC_API_KEY = os.getenv("EPIDEMIC_API_KEY", "")
EPIDEMIC_BASE_URL = os.getenv(
    "EPIDEMIC_BASE_URL",
    "https://partner-content-api.epidemicsound.com",
)

# YouTube Data API (OAuth). Client id/secret from Google Cloud; tokens are per account.
# Redirect must match exactly — default is http://127.0.0.1:8787/youtube/callback
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") or os.getenv("YOUTUBE_CLIENT_ID") or ""
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("YOUTUBE_CLIENT_SECRET") or ""
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")

# ---- Video output settings ----
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
ASPECT_RATIOS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
    "4:3": (1440, 1080),
}


def apply_aspect_ratio(label: str):
    key = (label or "9:16").strip().replace(" ", "")
    pair = ASPECT_RATIOS.get(key)
    if not pair:
        known = ", ".join(ASPECT_RATIOS)
        raise ValueError("Unknown size={0!r}. Use one of: {1}".format(label, known))
    global VIDEO_WIDTH, VIDEO_HEIGHT
    VIDEO_WIDTH, VIDEO_HEIGHT = pair
    return pair


# Default cap = YouTube Shorts. 0 means follow the story with no trim.
# Sources (checked 2026-09):
#   YouTube Shorts  180s  https://support.google.com/youtube/answer/15424877
#   TikTok in-app   600s  company cap since Feb 2022; uploads may be 30–60 min
#   Instagram Reels 1200s https://help.instagram.com/2720958398006062
#                         (Reels over 3 min are not recommended to new audiences)
#   Facebook Reels  none  https://www.facebook.com/business/help/581040529926114
SHORTS_MAX_SECONDS = 180
LENGTH_MIN_SECONDS = 3
LENGTH_MAX_SECONDS = 3600

PLATFORM_LENGTHS = [
    {
        "id": "youtube",
        "label": "Shorts",
        "hint": "YouTube · 3m",
        "seconds": 180,
        "note": "Official Shorts cap is 3 minutes (180s). Longer vertical files post as regular YouTube videos.",
    },
    {
        "id": "tiktok",
        "label": "TikTok",
        "hint": "10 min",
        "seconds": 600,
        "note": "In-app recording max is 10 minutes (600s). Some accounts can upload 30–60 minutes.",
    },
    {
        "id": "instagram",
        "label": "IG",
        "hint": "Reels · 20m",
        "seconds": 1200,
        "note": "Reels can run 20 minutes (1200s). Over 3 minutes is not recommended to people who do not follow you.",
    },
    {
        "id": "facebook",
        "label": "FB",
        "hint": "Reels · no cap",
        "seconds": 0,
        "note": "Facebook Reels have no official min or max (June 2025). The cut follows the story unless you set seconds.",
    },
    {
        "id": "custom",
        "label": "Custom",
        "hint": "Seconds",
        "seconds": None,
        "note": "Type any length in seconds. Narration longer than this is trimmed.",
    },
]


def apply_max_seconds(value):
    """Set SHORTS_MAX_SECONDS. 0 / empty / off = no cap. Returns the applied int."""
    global SHORTS_MAX_SECONDS
    if value is None or str(value).strip() == "":
        SHORTS_MAX_SECONDS = 0
        return 0
    raw = str(value).strip().lower()
    if raw in {"0", "off", "none", "full", "story", "n/a"}:
        SHORTS_MAX_SECONDS = 0
        return 0
    try:
        seconds = int(round(float(raw)))
    except (TypeError, ValueError):
        raise ValueError("max seconds must be a number, or 0 for no cap")
    if seconds < 0:
        raise ValueError("max seconds cannot be negative")
    if seconds > 0 and seconds < LENGTH_MIN_SECONDS:
        seconds = LENGTH_MIN_SECONDS
    if seconds > LENGTH_MAX_SECONDS:
        seconds = LENGTH_MAX_SECONDS
    SHORTS_MAX_SECONDS = seconds
    return seconds

# Caption styling
# IMPORTANT: MoviePy treats a float like 0.72 as 0.72 PIXELS unless relative=True.
# Keep PART label at the top and captions in the lower third so they never overlap.
CAPTION_FONT = "Arial-Bold"
CAPTION_FONTSIZE = 64
CAPTION_COLOR = "white"
CAPTION_STROKE_COLOR = "black"
CAPTION_STROKE_WIDTH = 4
CAPTION_POSITION = ("center", 0.82)  # 0=top, 1=bottom — converted to pixels
PART_LABEL_POSITION = (48, 56)  # top-left pixels — keep off the lower-third captions
PART_LABEL_DURATION = 1.4
PART_LABEL_FONTSIZE = 48

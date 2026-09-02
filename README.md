# Faceless Horror Reel Pipeline — 100% Free, Self-Hosted

Turns a script into a captioned, narrated, vertical video automatically.
No subscriptions, no watermarks, no usage caps you don't control.

## What's free vs. what needs a (free) key

| Component | Cost | Notes |
|---|---|---|
| Narration (Coqui TTS) | Free, local | Downloads a model once (~150-300MB), then runs forever offline |
| Captions (Whisper) | Free, local | Same — one-time model download, then fully offline |
| Comic / 2D / anime stills | Free | Pollinations image API, no key required |
| Live b-roll (Pexels) | Free API key | Only needed when `VIDEO_TYPE = "live"` |
| Video assembly (moviepy/ffmpeg) | Free, local | — |

Total ongoing cost: **$0**. The only thing to grab is a free Pexels API key.

## Setup

```bash
# 1. Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install system tools
#    espeak-ng: Coqui VITS (tts_models/en/vctk/vits) needs it for phonemes
#    ffmpeg:    moviepy needs it to assemble video
#    Mac:   brew install espeak-ng ffmpeg
#    Linux: sudo apt install espeak-ng ffmpeg
#    Windows: install espeak-ng and ffmpeg, then add both to PATH

# 4. Get a free Pexels API key: https://www.pexels.com/api/
#    Create a .env file in this folder with:
echo "PEXELS_API_KEY=your_key_here" > .env
```

## Studio (JUGAAD)

Open a ChatGPT-style desk in the browser: write the story, pick model / score / frame, watch the cut.

```bash
pip install -r requirements.txt
python studio.py
# → http://127.0.0.1:8787
```

**JUGAAD** is the working name (a clever local hack). The Images tab is a placeholder for later stills.

## Run it

```bash
python main.py scripts/popobawa.py
python main.py scripts/popobawa.py model=live
python main.py scripts/popobawa.py model=comic image_model=flux-anime
python main.py scripts/popobawa.py model=live music=off
python main.py scripts/popobawa.py music=horror_piano
```

This generates `output/popobawa.mp4` — narrated, captioned, vertical, ready
to upload as a YouTube Short (platform max **180 seconds**). CLI `model=`
overrides the script's `VIDEO_TYPE`. The pipeline keeps the full story on
the audio and captions; it does not cut off mid-sentence.

Drop royalty-free `.mp3` / `.wav` files in `assets/background_music/`.
Default is a random pick. Pin a track with `music=horror_piano` (filename
or unique substring), skip with `music=off`, or set `MUSIC_TRACK` in
`config.py` / `BACKGROUND_MUSIC` in the script.

## Visual models (`VIDEO_TYPE` / `model=`)

| Value | What you get |
|---|---|
| `live` / `realistic_broll` / `pexels` | Real stock **video** from Pexels → `assets/broll/` |
| `comic` / `2d_comic` | Illustrated stills + camera motion |
| `cartoon` / `anime` | Other illustrated stills |
| `ai_video` | Paid Pollinations video (`video_model=wan-fast`) |

Set the default in `config.py` (`VIDEO_TYPE`, `IMAGE_MODEL`, `AI_VIDEO_MODEL`),
in the script (`VIDEO_TYPE = "2d_comic"`), or on the command line. CLI wins.

```python
# config.py
VIDEO_TYPE = "live"          # default when a script does not set VIDEO_TYPE
IMAGE_MODEL = ""             # empty = flux-anime for comic; or flux / turbo
AI_VIDEO_MODEL = "wan-fast"  # paid Pollinations video only
MUSIC_ENABLED = True
MUSIC_TRACK = ""             # empty = random; or "horror_piano.mp3"
MUSIC_VOLUME = 0.12
BROLL_CLIP_COUNT = 12         # live shorts: one Pexels clip per story beat
```

## How `assets/broll/` videos are created

Those files are **not generated animation**. When `model=live` (or
`VIDEO_TYPE = "live"` / `"realistic_broll"`):

1. `broll_engine.py` builds short stock queries from each spoken beat
2. It searches Pexels **without** a portrait filter (landscape is cropped to 9:16)
3. Every result is scored against the beat using the clip's Pexels URL slug
4. Tourism/gym/Tokyo/kayak/festival-lantern slugs are rejected

Older files like `popobawa_part1_broll.mp4` are leftover downloads from
the multi-part series. Same type of file: real Pexels stock, not AI video.
You need a free `PEXELS_API_KEY` in `.env` for this path.

## Adding your own legends

Copy `scripts/popobawa.py`, rename it (e.g. `scripts/qallupilluk.py`), and
replace the `PARTS` list with your own text and `broll_query` per part
(scene descriptions for the illustrator, or Pexels search terms if live).

Set the look at the top of the script (or pass `model=live` on the CLI):

```python
VIDEO_TYPE = "2d_comic"       # or "live", "cartoon", "anime"
CHARACTER_SEED = 1995         # same seed → related look across parts
CHARACTER_LOCK = "Popobawa, one-eyed bat-winged shetani, leathery wings"
```

Each part can have `image_prompts` (several comic panels timed to the voiceover)
and `broll_query` (Pexels, or location flavor for illustrated prompts).

If you omit `image_prompts`, the pipeline splits the narration into about
`COMIC_PANELS` (default 5) scenes and draws one panel per scene.

```python
{"label": "PART 2", "text": "...", "broll_query": "...", "image_prompt": "..."}
```

Then run:
```bash
python main.py scripts/qallupilluk.py
```

## Tuning notes

- **Voice**: `config.py` → `TTS_SPEAKER`. Coqui's VCTK model has 100+ speaker
  IDs (p225-p376ish) — worth generating a few seconds of each to find your
  channel's signature voice. List them with:
  ```python
  from TTS.api import TTS
  print(TTS(model_name="tts_models/en/vctk/vits").speakers)
  ```
- **Caption pacing**: `words_per_chunk` in `main.py` — 2 words is fast/punchy
  (good default for horror), try 3-4 for a calmer read.
- **Whisper speed**: if generation feels slow on CPU, drop `WHISPER_MODEL` in
  `config.py` from `"small"` to `"tiny"` (faster, slightly less accurate —
  usually fine for short, clearly-spoken narration).
- **First run is slow**: both TTS and Whisper download their models the
  first time you run the pipeline. After that, everything runs offline.

## Folder structure

```
faceless_pipeline/
├── config.py            # all settings in one place
├── tts_engine.py         # local narration (Coqui TTS)
├── captions_engine.py    # local word-timed captions (Whisper)
├── visuals_engine.py     # comic / cartoon / anime stills, or live Pexels
├── broll_engine.py       # Pexels stock video (used when VIDEO_TYPE is live)
├── music_engine.py       # random bed from assets/background_music
├── assemble_video.py     # combines everything with moviepy
├── main.py               # orchestrator — run this
├── scripts/
│   └── popobawa.py       # example Short script
├── assets/
│   ├── background_music/ # drop .mp3/.wav here
│   └── broll/            # downloaded Pexels clips
└── output/                # final .mp4 files land here
```

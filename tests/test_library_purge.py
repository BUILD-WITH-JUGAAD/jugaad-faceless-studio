"""Studio delete must wipe every file a generate writes for that cut."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from studio.app import JOBS_DIR, SCRIPTS, _purge_library_stem


STEM = "_jugaad_purge_probe"
KEEP = "_jugaad_purge_keep"


def _touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class LibraryPurgeTest(unittest.TestCase):
    def tearDown(self):
        _purge_library_stem(STEM)
        _purge_library_stem(KEEP)

    def test_delete_removes_every_studio_artifact(self):
        planted = [
            config.OUTPUT_DIR / (STEM + ".mp4"),
            config.OUTPUT_DIR / (STEM + "TEMP_MPY_wvf_snd.mp4"),
            config.AUDIO_DIR / (STEM + ".wav"),
            config.AUDIO_DIR / (STEM + ".script.txt"),
            config.AUDIO_DIR / (STEM + ".trim.wav"),
            config.AUDIO_DIR / (STEM + "_chunk01.wav"),
            config.CAPTIONS_DIR / (STEM + "_words.json"),
            config.BROLL_DIR / (STEM + "_broll.json"),
            config.BROLL_DIR / (STEM + "_broll_0.mp4"),
            config.BROLL_DIR / (STEM + "_broll_0TEMP_MPY_wvf_snd.mp4"),
            SCRIPTS / (STEM + ".py"),
            SCRIPTS / "__pycache__" / (STEM + ".cpython-39.pyc"),
            config.STILLS_DIR / STEM / "panel_1.jpg",
            config.STILLS_DIR / (STEM + "_broll_0") / "panel_1.jpg",
            config.AI_IMAGE_DIR / STEM / "panel_1.jpg",
            config.AI_VIDEO_DIR / STEM / "clip_1.mp4",
        ]
        keepers = [
            config.OUTPUT_DIR / (KEEP + ".mp4"),
            config.AUDIO_DIR / "voice_previews" / "p326.wav",
            Path(config.ROOT) / "scripts" / "popobawa.py",
        ]
        for path in planted:
            _touch(path)
        job = JOBS_DIR / "purgeprobe.json"
        job.write_text(json.dumps({"id": "purgeprobe", "stem": STEM, "user_id": 3}))
        keep_job = JOBS_DIR / "purgekeep.json"
        keep_job.write_text(json.dumps({"id": "purgekeep", "stem": KEEP, "user_id": 3}))
        _touch(keepers[0])
        _touch(keepers[1])

        deleted = _purge_library_stem(STEM)

        for path in planted:
            self.assertFalse(path.exists(), "left behind {0}".format(path))
        self.assertFalse(job.exists())
        self.assertTrue((config.STILLS_DIR / STEM).exists() is False)
        self.assertTrue((config.AI_IMAGE_DIR / STEM).exists() is False)
        self.assertTrue((config.AI_VIDEO_DIR / STEM).exists() is False)
        self.assertTrue(keepers[0].exists(), "deleted an unrelated video")
        self.assertTrue(keepers[1].exists(), "deleted voice previews")
        self.assertTrue(keepers[2].exists(), "deleted a real CLI script")
        self.assertTrue(keep_job.exists(), "deleted an unrelated job")
        self.assertTrue(deleted)


if __name__ == "__main__":
    unittest.main()

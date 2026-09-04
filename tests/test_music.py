"""Shared music lookup must not pick another account's upload."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_engine import list_music_tracks, resolve_track


class ResolveTrackScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmpdir.name)
        (self.folder / "horror_piano.mp3").write_bytes(b"shared")
        user_a = self.folder / "users" / "1"
        user_b = self.folder / "users" / "2"
        user_a.mkdir(parents=True)
        user_b.mkdir(parents=True)
        (user_a / "fable.mp3").write_bytes(b"a")
        (user_b / "fable.mp3").write_bytes(b"b")
        (user_a / "only_mine.mp3").write_bytes(b"mine")
        self.patcher = mock.patch("music_engine.config.MUSIC_DIR", self.folder)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_shared_name(self):
        path = resolve_track("horror_piano")
        self.assertEqual(path, self.folder / "horror_piano.mp3")

    def test_ignores_unique_user_upload(self):
        with self.assertRaises(FileNotFoundError):
            resolve_track("only_mine")

    def test_ignores_colliding_user_uploads(self):
        with self.assertRaises(FileNotFoundError):
            resolve_track("fable.mp3")

    def test_absolute_user_path_still_works(self):
        mine = self.folder / "users" / "1" / "only_mine.mp3"
        self.assertEqual(resolve_track(str(mine)), mine.resolve())

    def test_relative_users_path_does_not_escape(self):
        with self.assertRaises(FileNotFoundError):
            resolve_track("users/1/only_mine.mp3")

    def test_list_shared_excludes_user_uploads(self):
        names = [p.name for p in list_music_tracks()]
        self.assertEqual(names, ["horror_piano.mp3"])

    def test_list_for_user_includes_theirs(self):
        names = [p.name for p in list_music_tracks(1)]
        self.assertEqual(set(names), {"horror_piano.mp3", "fable.mp3", "only_mine.mp3"})


if __name__ == "__main__":
    unittest.main()

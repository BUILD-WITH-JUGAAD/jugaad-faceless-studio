#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_engine import iso_seconds  # noqa: E402


class DurationTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(iso_seconds("PT45S"), 45)

    def test_minutes(self):
        self.assertEqual(iso_seconds("PT2M3S"), 123)

    def test_empty(self):
        self.assertEqual(iso_seconds(""), 0)


if __name__ == "__main__":
    unittest.main()

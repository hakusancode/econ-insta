import unittest
from datetime import datetime
from pathlib import Path

from econ_insta import audio


def _track(name="a"):
    return audio.Track(path=Path(f"{name}.mp3"), title=name, artist="아무개",
                       license="cc-by-4.0", credit=f"{name} · 아무개 · CC BY 4.0")


class PickTrackTest(unittest.TestCase):
    def test_주차로_결정적_선택(self):
        tracks = [_track("a"), _track("b"), _track("c")]
        when = datetime(2026, 8, 30)          # ISO 35주차 → 35 % 3 == 2
        self.assertEqual(audio.pick_track(when, tracks), tracks[2])

    def test_트랙이_없으면_None(self):
        self.assertIsNone(audio.pick_track(datetime(2026, 8, 30), []))

    def test_cc_by만_크레딧_필요(self):
        self.assertTrue(audio.needs_credit(_track()))
        cc0 = audio.Track(Path("z.mp3"), "z", "x", "cc0", "")
        self.assertFalse(audio.needs_credit(cc0))


class LoadTracksTest(unittest.TestCase):
    def test_번들_tracks_json이_유효하다(self):
        for track in audio.load_tracks():
            self.assertTrue(track.path.exists(), track.path)
            self.assertIn(track.license, {"cc0", "cc-by-3.0", "cc-by-4.0"})
            if audio.needs_credit(track):
                self.assertTrue(track.credit.strip())

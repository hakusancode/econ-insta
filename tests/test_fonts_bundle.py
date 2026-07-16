import unittest
from pathlib import Path

from PIL import ImageFont

from econ_insta.config import PROJECT_ROOT

WEIGHTS = ("Black", "ExtraBold", "Bold", "SemiBold", "Regular")


class FontBundleTest(unittest.TestCase):
    def test_all_weights_present_and_loadable(self):
        for w in WEIGHTS:
            path = PROJECT_ROOT / "assets" / "fonts" / f"Pretendard-{w}.otf"
            self.assertTrue(path.exists(), f"누락: {path}")
            # 한글 글리프가 있는지까지 확인
            font = ImageFont.truetype(str(path), 40)
            self.assertGreater(font.getlength("경제"), 0)

    def test_ofl_license_bundled(self):
        ofl = PROJECT_ROOT / "assets" / "fonts" / "OFL.txt"
        self.assertTrue(ofl.exists())
        self.assertIn("Open Font License", ofl.read_text(encoding="utf-8"))

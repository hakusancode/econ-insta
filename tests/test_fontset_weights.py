import unittest
from pathlib import Path

from econ_insta.renderer import FontSet


class FontSetWeightTest(unittest.TestCase):
    def test_weight_names_accepted(self):
        fonts = FontSet.discover()
        for w in ("black", "extrabold", "bold", "semibold", "regular"):
            font = fonts.at(64, weight=w)
            self.assertGreater(font.getlength("경제"), 0)

    def test_bold_flag_maps_to_bold_weight(self):
        fonts = FontSet.discover()
        self.assertEqual(fonts._path_for(weight="bold"), fonts._path_for(bold=True))

    def test_missing_weight_falls_back(self):
        # black·extrabold 파일이 없는 FontSet 이라도 예외 없이 bold 로 폴백
        f = FontSet(regular=Path("r"), bold=Path("b"))
        self.assertEqual(f._path_for(weight="black"), Path("b"))
        self.assertEqual(f._path_for(weight="semibold"), Path("r"))

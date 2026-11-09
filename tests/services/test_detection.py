import unittest

import cv2
import numpy as np

import services.detection as detection
from config import STAGE_MAINMENU_REGION


def _blank_frame():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def _load_bgr(filename):
    img = cv2.imread(f"templates/{filename}", cv2.IMREAD_UNCHANGED)
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


class MainMenuTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        detection.load_templates()

    def _detect_with_paste(self, template_file, x, y):
        frame = _blank_frame()
        tpl = _load_bgr(template_file)
        h, w = tpl.shape[:2]
        frame[y:y + h, x:x + w] = tpl
        return detection.detect_stage(frame)

    def test_original_template_still_matches_in_widened_region(self):
        # MAINMENU_1's natural position is (162, 161) on a 1280x720 frame;
        # the widened region must not regress the original template.
        self.assertEqual(self._detect_with_paste("MAINMENU_1.png", 162, 161), "MAINMENU")

    def test_region_covers_both_lobby_and_header_strip(self):
        x1, y1, x2, y2 = STAGE_MAINMENU_REGION
        self.assertLessEqual((x1, y1), (154, 42))
        self.assertGreaterEqual((x2, y2), (832, 225))

    def test_blank_frame_is_not_mainmenu(self):
        self.assertIsNone(detection.detect_stage(_blank_frame()))


if __name__ == "__main__":
    unittest.main()

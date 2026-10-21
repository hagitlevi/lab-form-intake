"""Unit tests for backend.pipeline's OCR-preprocessing experiment hooks (task: fix OCR
misses on hospital screen printouts). Channel/binarize functions are pure and fast;
run_ocr's scale param needs one real (tiny, English) OCR call to check bbox rescaling.

Run: python -m unittest tests.test_pipeline
"""

import unittest

import cv2
import numpy as np

from backend.pipeline import (
    BLANK_INK_RATIO,
    Word,
    _binarize_adaptive,
    _binarize_otsu,
    _confirm_blank,
    _gray_max_channel,
    _gray_red_channel,
    _is_blank,
    merge_ocr_passes,
    run_ocr,
)


def word(text, left, top, width=50, height=20, conf=90.0) -> Word:
    return Word(text=text, conf=conf, left=left, top=top, width=width, height=height)

YELLOW_BGR = (0, 255, 255)  # highlighter: high R, high G, low B
WHITE_BGR = (255, 255, 255)
BLACK_BGR = (0, 0, 0)


def solid(color, size=(10, 10)) -> np.ndarray:
    return np.full((size[1], size[0], 3), color, dtype=np.uint8)


class TestChannelGray(unittest.TestCase):
    def test_red_channel_makes_yellow_look_like_white(self):
        yellow = solid(YELLOW_BGR)
        white = solid(WHITE_BGR)
        self.assertEqual(_gray_red_channel(yellow)[0, 0], _gray_red_channel(white)[0, 0])

    def test_red_channel_still_shows_black_text_as_dark(self):
        black = solid(BLACK_BGR)
        self.assertEqual(_gray_red_channel(black)[0, 0], 0)

    def test_max_channel_also_erases_yellow(self):
        yellow = solid(YELLOW_BGR)
        white = solid(WHITE_BGR)
        self.assertEqual(_gray_max_channel(yellow)[0, 0], _gray_max_channel(white)[0, 0])

    def test_max_channel_still_shows_black_text_as_dark(self):
        black = solid(BLACK_BGR)
        self.assertEqual(_gray_max_channel(black)[0, 0], 0)


class TestBinarize(unittest.TestCase):
    def _bimodal_gray(self) -> np.ndarray:
        img = np.zeros((20, 20), dtype=np.uint8)
        img[:, 10:] = 255
        return img

    def test_otsu_output_is_binary(self):
        out = _binarize_otsu(self._bimodal_gray())
        self.assertTrue(set(np.unique(out)).issubset({0, 255}))

    def test_adaptive_output_is_binary(self):
        out = _binarize_adaptive(self._bimodal_gray(), block_size=11)
        self.assertTrue(set(np.unique(out)).issubset({0, 255}))


class TestRunOcrScale(unittest.TestCase):
    def _text_image(self) -> np.ndarray:
        img = np.full((100, 300), 255, dtype=np.uint8)
        cv2.putText(img, "TEST123", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0,), 3)
        return img

    def test_scale_2x_bbox_matches_scale_1x_within_rounding(self):
        img = self._text_image()
        words_1x = run_ocr(img, lang="eng")
        words_2x = run_ocr(img, lang="eng", scale=2)

        self.assertTrue(words_1x, "expected at least one word at scale=1")
        self.assertTrue(words_2x, "expected at least one word at scale=2")

        w1, w2 = words_1x[0], words_2x[0]
        # rescaled back to the SAME (original) coordinate space - small tolerance for rounding
        self.assertLessEqual(abs(w1.left - w2.left), 2)
        self.assertLessEqual(abs(w1.top - w2.top), 2)
        self.assertLessEqual(abs(w1.width - w2.width), 2)

    def test_config_is_passed_through(self):
        img = self._text_image()
        words = run_ocr(img, lang="eng", config="--psm 6")
        self.assertTrue(words)


class TestMergeOcrPasses(unittest.TestCase):
    def test_overlapping_box_keeps_higher_confidence(self):
        primary = [word("abc", 10, 10, conf=50.0)]
        secondary = [word("abc", 11, 11, conf=90.0)]  # near-identical box, higher conf
        merged = merge_ocr_passes(primary, secondary)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].conf, 90.0)

    def test_non_overlapping_words_from_both_passes_are_kept(self):
        primary = [word("left", 0, 0)]
        secondary = [word("right", 500, 500)]
        merged = merge_ocr_passes(primary, secondary)
        self.assertEqual({w.text for w in merged}, {"left", "right"})

    def test_primary_only_word_is_unaffected_by_unrelated_secondary_word(self):
        primary = [word("alone", 0, 0, conf=40.0)]
        secondary = [word("elsewhere", 900, 900, conf=99.0)]
        merged = merge_ocr_passes(primary, secondary)
        kept = next(w for w in merged if w.text == "alone")
        self.assertEqual(kept.conf, 40.0)  # not swapped just because something else scored higher


class TestConfirmBlank(unittest.TestCase):
    """A page the cheap ink-ratio check flags as blank must only actually be skipped if it
    also has no PDF text, no OCR words and no barcodes - guards against a real, very light
    form being misjudged blank (see the 'batch interrupted, 0 forms' bug report)."""

    def test_truly_blank_page_is_confirmed_blank(self):
        white = np.full((600, 400, 3), 255, dtype=np.uint8)
        self.assertTrue(_is_blank(white))
        self.assertTrue(_confirm_blank(white, pdf_text=""))

    def test_page_with_pdf_text_is_never_confirmed_blank(self):
        white = np.full((600, 400, 3), 255, dtype=np.uint8)
        self.assertFalse(_confirm_blank(white, pdf_text="מספר התחייבות 12345"))

    def test_faint_page_with_real_printed_text_is_not_confirmed_blank(self):
        # a big white page with a small amount of real text - below the ink-ratio
        # threshold by pixel count, but OCR-detectable.
        image = np.full((1200, 1600, 3), 255, dtype=np.uint8)
        cv2.putText(image, "FORM TEXT", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        ink_ratio = float((cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 250).sum()) / (1200 * 1600)
        self.assertLess(ink_ratio, BLANK_INK_RATIO, "fixture should sit below the blank threshold")
        self.assertFalse(_confirm_blank(image, pdf_text=""))


if __name__ == "__main__":
    unittest.main()

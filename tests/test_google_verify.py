import unittest
from datetime import date
from unittest import mock

import cv2
import numpy as np

from backend.pipeline import Word
from backend.pipeline import google_ocr as g
from backend.pipeline.confidence import PageData, score_form
from backend.pipeline.models import ExtractedForm

TODAY = date(2026, 10, 1)


def _case():
    form = ExtractedForm(page_indexes=[0], **{"first_name": {"value": "דוד"}})
    pages = [PageData(source="a.pdf", page_number=1, words=[])]
    ok, buf = cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))
    return form, pages, [buf.tobytes()], score_form(form, pages, TODAY)


class GoogleVerify(unittest.TestCase):
    def run_it(self, ocr, verify=True):
        form, pages, images, conf = _case()
        with mock.patch.object(g, "VERIFY", verify), mock.patch.object(g, "google_ocr", ocr):
            return conf, g.rescore_with_google(form, pages, images, conf, TODAY)

    def test_flips_to_safe(self):
        conf, new = self.run_it(mock.Mock(return_value=[Word("דוד", 90, 1, 2, 3, 4)]))
        self.assertEqual(conf.first_name.state, "review")
        self.assertEqual(new.first_name.state, "safe")

    def test_error_keeps_original(self):
        conf, new = self.run_it(mock.Mock(side_effect=g.GoogleOcrError("x")))
        self.assertIs(new, conf)

    def test_flag_off_not_called(self):
        ocr = mock.Mock()
        self.run_it(ocr, verify=False)
        ocr.assert_not_called()

    def test_all_safe_not_called(self):
        form, pages, images, _ = _case()
        pages[0].words.append(Word("דוד", 90, 1, 2, 3, 4))
        conf = score_form(form, pages, TODAY)
        ocr = mock.Mock()
        with mock.patch.object(g, "VERIFY", True), mock.patch.object(g, "google_ocr", ocr):
            # other fields are missing -> review with no value, so Google must stay idle
            g.rescore_with_google(form, pages, images, conf, TODAY)
        ocr.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class OcrAgreement(unittest.TestCase):
    """Claude vs Tesseract -> Claude vs Google -> if still review: Tesseract vs Google."""

    def run_central(self, claude, tess_text, google_text):
        form = ExtractedForm(page_indexes=[0], central_numbers=[{"value": claude}])
        pages = [PageData(source="a.pdf", page_number=1, words=[Word(tess_text, 90, 100, 100, 60, 20)] if tess_text else [])]
        ok, buf = cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))
        conf = score_form(form, pages, TODAY)
        gw = [Word(google_text, 90, 100, 100, 60, 20)] if google_text else []
        with mock.patch.object(g, "VERIFY", True), mock.patch.object(g, "google_ocr", mock.Mock(return_value=gw)):
            return g.rescore_with_google(form, pages, [buf.tobytes()], conf, TODAY).central_numbers[0]

    def test_tesseract_and_google_agree_on_other_value(self):
        r = self.run_central("929158", "939158", "939158")
        self.assertEqual(r.state, "review")          # never auto-corrected
        self.assertEqual(r.value, "929158")          # AI value kept
        self.assertIn("'939158'", r.reasons[0])
        self.assertIn("Tesseract ו-Google", r.reasons[0])

    def test_engines_disagree_with_each_other_no_hint(self):
        r = self.run_central("929158", "A58", "939158")
        self.assertFalse(any("Tesseract ו-Google" in x for x in r.reasons))

    def test_google_agrees_with_claude_is_safe(self):
        r = self.run_central("929158", "939158", "929158")
        self.assertEqual(r.state, "safe")

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

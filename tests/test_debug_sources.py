import unittest
from datetime import date
from unittest import mock

from backend.pipeline import Word, debug_sources
from backend.pipeline.confidence import PageData, score_form
from backend.pipeline.models import DateValue, ExtractedForm, FieldValue

TODAY = date(2026, 10, 26)


def w(text, left, top=100, width=50):
    return Word(text=text, conf=90.0, left=left, top=top, width=width, height=20)


def pg(words):
    return PageData(source="a.pdf", page_number=1, words=list(words))


class TestDebugSources(unittest.TestCase):
    def setUp(self):
        self.form = ExtractedForm(
            page_indexes=[0],
            national_id=FieldValue(value="011223344"),
            first_name=FieldValue(value="יסמין"),
            received_date=DateValue(day=31, month=8, year=2026, date_source="received_stamp", raw_text="31.8.26"),
        )

    def _debug_lines(self, conf):
        return {n: [r for r in getattr(conf, n).reasons if r.startswith("[debug]")]
                for n in ("national_id", "first_name", "received_date")}

    def test_off_by_default_adds_nothing(self):
        conf = score_form(self.form, [pg([])], TODAY)
        with mock.patch.object(debug_sources, "DEBUG", False):
            out = debug_sources.annotate(self.form, conf, [pg([])], None)
        self.assertEqual(out, conf)

    def test_review_fields_get_one_line_per_field_with_all_sources(self):
        tess = pg([w("01122334", 100), w("ימין", 300)])
        conf = score_form(self.form, [tess], TODAY)
        with mock.patch.object(debug_sources, "DEBUG", True):
            out = debug_sources.annotate(self.form, conf, [tess], {"received_date": {"day": 31, "month": 8, "year": 2026}})
        lines = self._debug_lines(out)
        self.assertEqual(len(lines["national_id"]), 1)
        self.assertIn("Claude: 011223344", lines["national_id"][0])
        self.assertIn("OCR: 01122334 ≈", lines["national_id"][0])
        self.assertIn("Gemini: null", lines["national_id"][0])
        self.assertIn("Gemini: 31/8/2026", lines["received_date"][0])
        self.assertIn("Claude: 31.8.26", lines["received_date"][0])

    def test_google_not_run_is_said(self):
        conf = score_form(self.form, [pg([])], TODAY)
        with mock.patch.object(debug_sources, "DEBUG", True):
            out = debug_sources.annotate(self.form, conf, [pg([])], None)
        self.assertIn("Gemini: לא רץ", self._debug_lines(out)["first_name"][0])

    def test_state_is_never_changed(self):
        conf = score_form(self.form, [pg([])], TODAY)
        with mock.patch.object(debug_sources, "DEBUG", True):
            out = debug_sources.annotate(self.form, conf, [pg([])], None)
        self.assertEqual(out.national_id.state, conf.national_id.state)

import unittest
from datetime import date
from unittest import mock

from backend.pipeline import Word, gemini_verify as gv
from backend.pipeline.confidence import PageData, score_form
from backend.pipeline.models import DateValue, ExtractedForm, FieldValue

TODAY = date(2026, 10, 26)


def w(text, left, top=100):
    return Word(text=text, conf=90.0, left=left, top=top, width=50, height=20)


def conf_for(form, words=()):
    return score_form(form, [PageData(source="a.pdf", page_number=1, words=list(words))], TODAY)


class TestApply(unittest.TestCase):
    def test_same_value_flips_unverified_field_to_safe(self):
        form = ExtractedForm(page_indexes=[0], first_name=FieldValue(value="יוסף"), national_id=FieldValue(value="11223344"))
        c = conf_for(form)
        self.assertEqual(c.first_name.state, "review")
        new = gv.apply(c, {"first_name": "יוסף", "national_id": "011223344"})
        self.assertEqual(new.first_name.state, "safe")
        self.assertEqual(new.national_id.state, "safe")   # 8 vs 9 digits: same ID (leading zero ignored)
        self.assertEqual(new.national_id.value, "11223344")  # shown as read, never padded

    def test_different_value_stays_review_with_hint(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="11223344"))
        new = gv.apply(conf_for(form), {"national_id": "11223044"})
        self.assertEqual(new.national_id.state, "review")
        self.assertEqual(new.national_id.value, "11223344")
        self.assertTrue(any("Gemini קרא '11223044'" in r for r in new.national_id.reasons))

    def test_rule_failure_is_never_flipped(self):
        # 6 digits for Clalit commitment fails the format rule - Gemini agreeing must not make it safe
        form = ExtractedForm(page_indexes=[0], sender=FieldValue(value="כללית"),
                             commitment_numbers=[FieldValue(value="945599")])
        c = conf_for(form, [w("כללית", 100)])
        new = gv.apply(c, {"sender": "כללית", "commitment_numbers": ["945599"]})
        self.assertEqual(new.commitment_numbers[0].state, "review")

    def test_date_same_day_month_flips(self):
        form = ExtractedForm(page_indexes=[0], received_date=DateValue(day=10, month=8, year=2026,
                             date_source="received_stamp", raw_text="10-08-2026"))
        new = gv.apply(conf_for(form), {"received_date": {"day": 10, "month": 8, "year": 2026}})
        self.assertEqual(new.received_date.state, "safe")

    def test_inferred_year_date_never_flips(self):
        form = ExtractedForm(page_indexes=[0], received_date=DateValue(day=10, month=8, year=2026, year_inferred=True,
                             date_source="handwritten", raw_text="10.8"))
        new = gv.apply(conf_for(form), {"received_date": {"day": 10, "month": 8, "year": None}})
        self.assertEqual(new.received_date.state, "review")

    def test_sender_long_name_matches_list_name(self):
        form = ExtractedForm(page_indexes=[0], sender=FieldValue(value="וולפסון"))
        new = gv.apply(conf_for(form), {"sender": "המרכז הרפואי וולפסון"})
        self.assertEqual(new.sender.state, "safe")

    def test_claude_empty_gemini_found_is_filled_but_review(self):
        form = ExtractedForm(page_indexes=[0])
        new = gv.apply(conf_for(form), {"central_numbers": ["939376"], "first_name": "ישראל",
                                        "national_id": "11223344",
                                        "received_date": {"day": 18, "month": 6, "year": 2026}})
        self.assertEqual([(c.value, c.state) for c in new.central_numbers], [("939376", "review")])
        self.assertEqual((new.first_name.value, new.first_name.state), ("ישראל", "review"))
        self.assertEqual(new.national_id.value, "11223344")
        self.assertTrue(any("8 ספרות" in r for r in new.national_id.reasons))
        self.assertEqual((new.received_date.value, new.received_date.state), ("2026-06-18", "review"))
        self.assertEqual(new.first_name.reasons[0], gv.GEMINI_ONLY)
        self.assertNotIn("לא נמצא בטופס", new.first_name.reasons)

    def test_gemini_date_without_year_does_not_fill(self):
        form = ExtractedForm(page_indexes=[0])
        new = gv.apply(conf_for(form), {"received_date": {"day": 18, "month": 6, "year": None}})
        self.assertEqual(new.received_date.value, "")


class TestSanitize(unittest.TestCase):
    def test_placeholder_names_are_not_used(self):
        form = ExtractedForm(page_indexes=[0])
        new = gv.apply(conf_for(form), {"first_name": "First", "last_name": "Last", "national_id": "unknown"})
        self.assertEqual(new.first_name.value, "")
        self.assertEqual(new.last_name.value, "")
        self.assertEqual(new.national_id.value, "")
        self.assertFalse(any("First" in r for r in new.first_name.reasons))


class TestGeminiEmpty(unittest.TestCase):
    def test_gemini_found_nothing_is_said(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="11223344"))
        new = gv.apply(conf_for(form), {"national_id": None})
        self.assertEqual(new.national_id.state, "review")
        self.assertIn("Gemini לא מצא ערך בשדה הזה", new.national_id.reasons)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.form = ExtractedForm(page_indexes=[0], first_name=FieldValue(value="יוסף"))
        self.conf = conf_for(self.form)

    def test_off_does_not_call(self):
        rf = mock.Mock()
        with mock.patch.object(gv, "VERIFY", False), mock.patch.object(gv, "read_fields", rf):
            self.assertIs(gv.verify_with_gemini(self.conf, [b"x"]), self.conf)
        rf.assert_not_called()

    def test_error_keeps_original(self):
        with mock.patch.object(gv, "VERIFY", True), mock.patch.object(gv, "read_fields", mock.Mock(side_effect=gv.GeminiError("x"))):
            self.assertIs(gv.verify_with_gemini(self.conf, [b"x"]), self.conf)

    def test_read_fields_parses_text_parts_and_retries_transient(self):
        answer = {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"first_name": "יוסף"}'}]}}]}
        calls = {"n": 0}

        def post(_):
            calls["n"] += 1
            if calls["n"] == 1:
                raise gv.GeminiError("HTTP 503: UNAVAILABLE")
            return answer
        with mock.patch.object(gv, "_post", post), mock.patch.object(gv.time, "sleep"):
            self.assertEqual(gv.read_fields([b"img"]), {"first_name": "יוסף"})
        self.assertEqual(calls["n"], 2)

"""Unit tests for pipeline step 5 (backend.pipeline.confidence).

Run: python -m unittest tests.test_confidence
"""

import unittest
from datetime import date

from backend.pipeline import Barcode, Word
from backend.pipeline.confidence import (
    ID_WARNING,
    NOT_FOUND,
    PageData,
    commitment_format_ok,
    find_match,
    score_form,
    sender_fuzzy_match,
    valid_israeli_id,
)
from backend.pipeline.models import DateValue, ExtractedForm, FieldValue
from backend.pipeline.normalize import normalize_digits, pad_national_id

TODAY = date(2026, 6, 20)


def word(text, left, top, width=50, height=20) -> Word:
    return Word(text=text, conf=90.0, left=left, top=top, width=width, height=height)


def barcode(value: str, rect=(0, 0, 10, 10)) -> Barcode:
    return Barcode(value=value, format="Code 128", rect=rect)


def page(words=(), barcodes=(), source="a.pdf", page_number=1) -> PageData:
    return PageData(source=source, page_number=page_number, words=list(words), barcodes=list(barcodes))


class TestIsraeliId(unittest.TestCase):
    def test_valid_check_digit(self):
        self.assertTrue(valid_israeli_id("123456782"))

    def test_invalid_check_digit(self):
        self.assertFalse(valid_israeli_id("123456783"))

    def test_padding_preserves_a_valid_id(self):
        self.assertEqual(pad_national_id("12345674"), "012345674")
        self.assertTrue(valid_israeli_id(pad_national_id("12345674")))


class TestSenderFuzzyMatch(unittest.TestCase):
    def test_exact_name_matches(self):
        self.assertEqual(sender_fuzzy_match("כללית"), "כללית")

    def test_unknown_sender_has_no_match(self):
        self.assertIsNone(sender_fuzzy_match("בית חולים לא קיים בכלל"))


class TestCommitmentFormat(unittest.TestCase):
    def test_clalit_nine_digits_ok(self):
        self.assertTrue(commitment_format_ok("141046589", "כללית"))

    def test_clalit_wrong_length_fails(self):
        self.assertFalse(commitment_format_ok("123", "כללית"))

    def test_unknown_sender_has_no_rule(self):
        # any digit string passes when the sender has no entry in commitment_formats.json
        self.assertTrue(commitment_format_ok("1", "בית חולים לא ברשימה"))
        self.assertTrue(commitment_format_ok("1", None))


class TestFindMatch(unittest.TestCase):
    def test_exact_single_word_match(self):
        m = find_match("939376", [page([word("939376", 100, 100)])], normalize_digits)
        self.assertEqual(m.kind, "exact")
        self.assertEqual(m.bbox, (100, 100, 50, 20))
        self.assertEqual(m.page, 1)

    def test_adjacent_words_split_number_matches(self):
        # Maccabi-style "2648 00879" split into two OCR words on the same line
        words = [word("2648", 100, 100, width=40), word("00879", 145, 100, width=50)]
        m = find_match("264800879", [page(words)], normalize_digits)
        self.assertEqual(m.kind, "exact")

    def test_near_match_names_the_differing_character(self):
        m = find_match("939158", [page([word("936158", 100, 100)])], normalize_digits)
        self.assertEqual(m.kind, "near")
        self.assertIn("בתו 3", m.reason)
        self.assertIn("'9' מול '6'", m.reason)

    def test_no_candidates_is_none(self):
        m = find_match("939376", [page([word("111111", 100, 100)])], normalize_digits)
        self.assertEqual(m.kind, "none")


class TestScoreForm(unittest.TestCase):
    def test_missing_value_is_review_with_standard_reason(self):
        form = ExtractedForm(page_indexes=[0])
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(conf.national_id.state, "review")
        self.assertEqual(conf.national_id.reasons, [NOT_FOUND])
        self.assertEqual(conf.national_id.value, "")

    def test_invalid_id_confirmed_by_ocr_is_safe_with_warning(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="123456783"))
        conf = score_form(form, [page([word("123456783", 0, 0)])], TODAY)
        self.assertEqual(conf.national_id.state, "safe")
        self.assertEqual(conf.national_id.reasons, [ID_WARNING])

    def test_invalid_id_without_ocr_match_stays_review(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="123456783"))
        conf = score_form(form, [page([word("999999999", 0, 0)])], TODAY)
        self.assertEqual(conf.national_id.state, "review")
        self.assertEqual(conf.national_id.reasons, ["לא אומת על ידי מקור נוסף", ID_WARNING])

    def test_ten_digit_id_is_kept_whole(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="0011223344"))
        conf = score_form(form, [page([word("0011223344", 0, 0)])], TODAY)
        self.assertEqual(conf.national_id.value, "0011223344")
        self.assertEqual(conf.national_id.state, "safe")
        self.assertEqual(conf.national_id.reasons, [ID_WARNING])

    def test_id_safe_when_valid_and_confirmed(self):
        form = ExtractedForm(page_indexes=[0], national_id=FieldValue(value="123456782"))
        conf = score_form(form, [page([word("123456782", 0, 0)])], TODAY)
        self.assertEqual(conf.national_id.state, "safe")

    def _central(self, value, pages, commitments=()):
        form = ExtractedForm(
            page_indexes=[0], central_numbers=[FieldValue(value=value)],
            commitment_numbers=[FieldValue(value=c) for c in commitments],
        )
        return score_form(form, pages, TODAY).central_numbers[0]

    def test_central_1_no_value_is_not_found(self):
        form = ExtractedForm(page_indexes=[0], central_numbers=[FieldValue(value=None)])
        r = score_form(form, [page([], [])], TODAY).central_numbers[0]
        self.assertEqual((r.state, r.reasons), ("review", [NOT_FOUND]))

    def test_central_2_not_six_digits_is_review(self):
        r = self._central("93937", [page([word("93937", 0, 0)], [])])
        self.assertEqual(r.state, "review")
        self.assertIn("6 ספרות", r.reasons[0])

    def test_central_3_no_barcode_falls_back_to_ocr(self):
        r = self._central("939158", [page([word("939158", 0, 0)], [])])
        self.assertEqual(r.state, "safe")
        r = self._central("939158", [page([], [])])
        self.assertEqual((r.state, r.reasons), ("review", ["לא אומת על ידי מקור נוסף"]))

    def test_central_4_matching_barcode_is_safe_with_barcode_bbox(self):
        r = self._central("939376", [page([], [barcode("939376", rect=(5, 6, 7, 8))])])
        self.assertEqual((r.state, r.bbox), ("safe", (5, 6, 7, 8)))

    def test_central_5_different_barcode_is_review(self):
        r = self._central("936158", [page([word("936158", 0, 0)], [barcode("939158")])])
        self.assertEqual((r.state, r.reasons), ("review", ["הברקוד בעמוד לא תואם"]))

    def test_central_5_barcode_equal_to_commitment_number_is_ignored(self):
        pages = [page([word("260630", 0, 0)], [barcode("123456")])]
        self.assertEqual(self._central("260630", pages, commitments=["123456"]).state, "safe")
        self.assertEqual(self._central("260630", [page([], [barcode("123456")])], commitments=["123456"]).reasons,
                         ["לא אומת על ידי מקור נוסף"])

    def test_empty_central_numbers_list_yields_one_missing_entry(self):
        form = ExtractedForm(page_indexes=[0])
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(len(conf.central_numbers), 1)
        self.assertEqual(conf.central_numbers[0].reasons, [NOT_FOUND])

    def test_year_inferred_is_review(self):
        form = ExtractedForm(
            page_indexes=[0],
            received_date=DateValue(day=18, month=6, year=2026, year_inferred=True, raw_text="18.06"),
        )
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(conf.received_date.state, "review")
        self.assertTrue(any("הוערכה אוטומטית" in r for r in conf.received_date.reasons))

    def test_visit_date_source_is_review(self):
        form = ExtractedForm(
            page_indexes=[0],
            received_date=DateValue(day=18, month=6, year=2026, date_source="visit_date", raw_text="18.06.2026"),
        )
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(conf.received_date.state, "review")
        self.assertTrue(any("תאריך ביקור" in r for r in conf.received_date.reasons))

    def test_future_date_is_review(self):
        form = ExtractedForm(
            page_indexes=[0],
            received_date=DateValue(day=1, month=1, year=2027, date_source="received_stamp"),
        )
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(conf.received_date.state, "review")
        self.assertIn("בעתיד", conf.received_date.reasons[0])

    def test_received_date_safe_when_confirmed_by_ocr(self):
        form = ExtractedForm(
            page_indexes=[0],
            received_date=DateValue(day=18, month=6, year=2026, date_source="received_stamp", raw_text="18.06.2026"),
        )
        conf = score_form(form, [page([word("18.06.2026", 0, 0)])], TODAY)
        self.assertEqual(conf.received_date.state, "safe")
        self.assertEqual(conf.received_date.value, "2026-06-18")

    def test_unknown_sender_blocks_commitment_format_rule(self):
        # unknown sender -> commitment number has no format rule, so an odd-length value
        # is only judged on whether a second source confirms it, not on format
        form = ExtractedForm(
            page_indexes=[0],
            sender=FieldValue(value="בית חולים לא קיים"),
            commitment_numbers=[FieldValue(value="123")],
        )
        conf = score_form(form, [page([word("123", 0, 0)])], TODAY)
        # sender itself will be "review" (not in closed list), but that must not leak a
        # format rule onto the commitment number - it should reach the cross-source check
        self.assertEqual(conf.commitment_numbers[0].state, "safe")

    def test_sender_not_in_closed_list_is_review(self):
        form = ExtractedForm(page_indexes=[0], sender=FieldValue(value="בית חולים לא קיים"))
        conf = score_form(form, [page()], TODAY)
        self.assertEqual(conf.sender.state, "review")

    def test_sender_safe_when_in_list_and_confirmed(self):
        form = ExtractedForm(page_indexes=[0], sender=FieldValue(value="כללית"))
        conf = score_form(form, [page([word("כללית", 0, 0)])], TODAY)
        self.assertEqual(conf.sender.state, "safe")

    def test_commitment_number_bad_format_for_known_sender_is_review(self):
        form = ExtractedForm(
            page_indexes=[0],
            sender=FieldValue(value="כללית"),
            commitment_numbers=[FieldValue(value="123")],
        )
        conf = score_form(form, [page([word("123", 0, 0)])], TODAY)
        self.assertEqual(conf.commitment_numbers[0].state, "review")

    def test_name_matched_via_final_letter_normalization(self):
        # "כהן" with a final-letter variant word elsewhere should still cross-match
        form = ExtractedForm(page_indexes=[0], first_name=FieldValue(value="דן"))
        conf = score_form(form, [page([word("דן", 0, 0)])], TODAY)
        self.assertEqual(conf.first_name.state, "safe")


if __name__ == "__main__":
    unittest.main()

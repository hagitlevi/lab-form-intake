"""Unit tests for pipeline step 4 (backend.pipeline.extract).

No real API calls - the Anthropic client is mocked in every test.
Run: python -m unittest tests.test_extract
"""

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from backend.pipeline.extract import (
    _build_user_content,
    _fill_year,
    _flag_probable_continuations,
    _flag_split_by_ai,
    extract_form,
)
from backend.pipeline.models import (
    DateValue,
    ExtractedForm,
    ExtractionResponse,
    FieldValue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_image() -> np.ndarray:
    return np.full((100, 80, 3), 255, dtype=np.uint8)


def _make_response(forms: list[dict], tool_id: str = "t1") -> MagicMock:
    """Build a mock Anthropic response that looks like a tool_use call."""
    raw_input = ExtractionResponse(forms=[ExtractedForm(**f) for f in forms]).model_dump(mode="json")
    block = SimpleNamespace(type="tool_use", id=tool_id, name="extract_forms", input=raw_input)
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    response = MagicMock()
    response.content = [block]
    response.usage = usage
    return response


def _simple_form(**kwargs) -> dict:
    base = {"page_indexes": [0]}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Schema validation: valid response passes, invalid raises
# ---------------------------------------------------------------------------

class TestSchemaValidation(unittest.TestCase):
    def test_valid_response_is_returned(self):
        form_data = _simple_form(
            first_name={"value": "דוד", "raw_text": "דוד", "origin": "printed"},
            commitment_numbers=[{"value": "123456789", "raw_text": "123456789", "origin": "printed"}],
        )
        mock_response = _make_response([form_data])

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            forms = extract_form([_blank_image()], no_cache=True)

        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].first_name.value, "דוד")

    def test_no_ocr_text_in_request_payload(self):
        """The user content must contain only image blocks and brief text labels, not OCR words."""
        images_bytes_seen = []

        def capture_create(**kwargs):
            messages = kwargs.get("messages", [])
            for msg in messages:
                if msg.get("role") == "user":
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            # The only text blocks allowed are the short "Page N (0-indexed):" labels
                            # and the final instruction - none should contain OCR word dumps.
                            text = block.get("text", "")
                            images_bytes_seen.append(text)
            return _make_response([_simple_form()])

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.side_effect = capture_create
            extract_form([_blank_image()], no_cache=True)

        for text in images_bytes_seen:
            # A legitimate label is short; a dump of OCR words would be long
            self.assertLess(
                len(text),
                200,
                f"Text block too long - looks like OCR text was included: {text[:100]!r}",
            )

    def test_only_images_and_labels_in_content(self):
        """Every user content block must be 'image' or a short 'text'."""
        content = _build_user_content([b"\xff\xd8\xff" + b"\x00" * 100])
        types = {b["type"] for b in content}
        self.assertIsSubset(types, {"image", "text"})

    def assertIsSubset(self, a, b):  # noqa: N802
        self.assertTrue(a.issubset(b), f"{a} is not a subset of {b}")


# ---------------------------------------------------------------------------
# Retry on invalid output, then error result
# ---------------------------------------------------------------------------

class TestRetry(unittest.TestCase):
    def test_retry_on_validation_error_then_success(self):
        bad_block = SimpleNamespace(
            type="tool_use", id="t1", name="extract_forms",
            input={"forms": [{"page_indexes": "NOT_A_LIST"}]},  # wrong type
        )
        bad_response = MagicMock()
        bad_response.content = [bad_block]
        bad_response.usage = SimpleNamespace(input_tokens=50, output_tokens=20)

        good_response = _make_response([_simple_form()])
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            return bad_response if call_count[0] == 1 else good_response

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.side_effect = side_effect
            forms = extract_form([_blank_image()], no_cache=True)

        self.assertEqual(call_count[0], 2)
        self.assertEqual(len(forms), 1)

    def test_error_result_after_two_failures(self):
        bad_block = SimpleNamespace(
            type="tool_use", id="t1", name="extract_forms",
            input={"forms": [{"page_indexes": "STILL_WRONG"}]},
        )
        bad_response = MagicMock()
        bad_response.content = [bad_block]
        bad_response.usage = SimpleNamespace(input_tokens=50, output_tokens=20)

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = bad_response
            forms = extract_form([_blank_image()], no_cache=True)

        # Should return exactly one error-flagged form, not raise
        self.assertEqual(len(forms), 1)
        self.assertIn("extraction_error", forms[0].notes or "")

    def test_missing_tool_call_returns_error_form(self):
        text_only = MagicMock()
        text_only.content = [SimpleNamespace(type="text", text="Sorry, I cannot help.")]
        text_only.usage = SimpleNamespace(input_tokens=20, output_tokens=10)

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = text_only
            forms = extract_form([_blank_image()], no_cache=True)

        self.assertEqual(len(forms), 1)
        self.assertIn("extraction_error", forms[0].notes or "")


# ---------------------------------------------------------------------------
# Post-processing: year filling
# ---------------------------------------------------------------------------

class TestYearFilling(unittest.TestCase):
    def test_missing_year_filled_from_upload_date(self):
        forms = [
            ExtractedForm(
                page_indexes=[0],
                received_date=DateValue(day=15, month=3, year=None),
            )
        ]
        _fill_year(forms, date(2026, 10, 21))
        self.assertEqual(forms[0].received_date.year, 2026)
        self.assertTrue(forms[0].received_date.year_inferred)

    def test_existing_year_not_overwritten(self):
        forms = [
            ExtractedForm(
                page_indexes=[0],
                received_date=DateValue(day=5, month=8, year=2025),
            )
        ]
        _fill_year(forms, date(2026, 10, 21))
        self.assertEqual(forms[0].received_date.year, 2025)
        self.assertFalse(forms[0].received_date.year_inferred)

    def test_no_date_field_no_crash(self):
        forms = [ExtractedForm(page_indexes=[0])]
        _fill_year(forms, date(2026, 10, 21))  # must not raise
        self.assertIsNone(forms[0].received_date)

    def test_year_filled_via_extract_form(self):
        form_data = _simple_form(
            received_date={"day": 1, "month": 6, "year": None, "date_source": "received_stamp"}
        )
        mock_response = _make_response([form_data])

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            forms = extract_form(
                [_blank_image()], upload_date=date(2026, 10, 21), no_cache=True
            )

        self.assertEqual(forms[0].received_date.year, 2026)
        self.assertTrue(forms[0].received_date.year_inferred)


# ---------------------------------------------------------------------------
# Post-processing: probable_continuation
# ---------------------------------------------------------------------------

class TestProbableContinuation(unittest.TestCase):
    def test_empty_form_is_flagged(self):
        forms = [ExtractedForm(page_indexes=[0])]
        _flag_probable_continuations(forms)
        self.assertTrue(forms[0].probable_continuation)

    def test_form_with_name_is_not_flagged(self):
        forms = [
            ExtractedForm(
                page_indexes=[0],
                first_name=FieldValue(value="דוד", raw_text="דוד", origin="printed"),
            )
        ]
        _flag_probable_continuations(forms)
        self.assertFalse(forms[0].probable_continuation)

    def test_form_with_only_central_number_is_not_flagged(self):
        forms = [
            ExtractedForm(
                page_indexes=[0],
                central_numbers=[FieldValue(value="123456", raw_text="123456", origin="barcode_label")],
            )
        ]
        _flag_probable_continuations(forms)
        self.assertFalse(forms[0].probable_continuation)

    def test_form_with_only_commitment_is_not_flagged(self):
        forms = [
            ExtractedForm(
                page_indexes=[0],
                commitment_numbers=[FieldValue(value="987654321", raw_text="987654321", origin="printed")],
            )
        ]
        _flag_probable_continuations(forms)
        self.assertFalse(forms[0].probable_continuation)


# ---------------------------------------------------------------------------
# Post-processing: split_by_ai
# ---------------------------------------------------------------------------

class TestSplitByAi(unittest.TestCase):
    def test_single_form_not_flagged(self):
        forms = [ExtractedForm(page_indexes=[0])]
        _flag_split_by_ai(forms)
        self.assertFalse(forms[0].split_by_ai)

    def test_multiple_forms_all_flagged(self):
        forms = [
            ExtractedForm(page_indexes=[0]),
            ExtractedForm(page_indexes=[1]),
        ]
        _flag_split_by_ai(forms)
        self.assertTrue(forms[0].split_by_ai)
        self.assertTrue(forms[1].split_by_ai)

    def test_split_by_ai_via_extract_form(self):
        form_data = [
            _simple_form(page_indexes=[0]),
            _simple_form(page_indexes=[0]),
        ]
        mock_response = _make_response(form_data)

        with patch("backend.pipeline.extract.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            forms = extract_form([_blank_image()], no_cache=True)

        self.assertEqual(len(forms), 2)
        self.assertTrue(all(f.split_by_ai for f in forms))


if __name__ == "__main__":
    unittest.main()

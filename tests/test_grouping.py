"""Unit tests for pipeline step 3 (backend.grouping): classify_page() and group_pages().

Run: python -m unittest tests.test_grouping
"""

import tempfile
import unittest
from pathlib import Path

import pymupdf

from backend.grouping import PageFacts, _central_number_in_text, classify_page, group_pages, run
from backend.pipeline import Word


def word(text, left, top, width=50, height=20) -> Word:
    return Word(text=text, conf=90.0, left=left, top=top, width=width, height=height)


def page(source="a.pdf", page_number=1, **kwargs) -> PageFacts:
    return PageFacts(source=source, page_number=page_number, **kwargs)


class TestGroupPages(unittest.TestCase):
    def test_single_page_form_with_sticker(self):
        pages = [page(sticker_candidates=["123456"])]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0].flagged)
        self.assertEqual(len(groups[0].pages), 1)
        self.assertEqual(groups[0].page_rules, ["sticker_candidate"])

    def test_page_2_of_2_is_a_continuation(self):
        pages = [
            page(page_number=1, sticker_candidates=["123456"]),
            page(page_number=2, continuation_of=(2, 2)),
        ]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].pages), 2)
        self.assertFalse(groups[0].flagged)
        result = classify_page(pages[1], previous=pages[0])
        self.assertEqual(result.rule, "page_of_total")

    def test_page_with_nothing_is_still_a_new_form(self):
        # the new default: no sticker, no "מרכזי"+digit, no page-of-total marker - still a
        # new form, not a silent continuation (a wrong merge is worse than a wrong split).
        pages = [
            page(page_number=1, sticker_candidates=["123456"]),
            page(page_number=2),  # nothing at all
        ]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 2)
        self.assertFalse(groups[1].flagged)
        self.assertEqual(groups[1].page_rules, ["default"])

    def test_two_consecutive_forms_of_the_same_template_do_not_merge(self):
        # same sender/template (same informational fields), different sticker - must NOT
        # merge just because they look alike; nothing but the 3 rules decides this.
        pages = [
            page(page_number=1, sticker_candidates=["111111"], national_id="123456789"),
            page(page_number=2, sticker_candidates=["222222"], national_id="123456789"),
        ]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0].pages), 1)
        self.assertEqual(len(groups[1].pages), 1)

    def test_continuation_crosses_file_boundary(self):
        pages = [
            page(source="fileA.pdf", page_number=1, sticker_candidates=["123456"]),
            page(source="fileB.pdf", page_number=1, continuation_of=(2, 2)),
        ]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 1)
        self.assertEqual([p.source for p in groups[0].pages], ["fileA.pdf", "fileB.pdf"])
        self.assertFalse(groups[0].flagged)

    def test_first_page_saying_page_2_is_a_flagged_new_form(self):
        # "page 2 of n" would normally mean continuation, but there's no previous page at
        # all (first page of the whole batch) - new form, flagged, not silently dropped.
        pages = [page(page_number=1, continuation_of=(2, 3))]

        groups = group_pages(pages)

        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].flagged)
        self.assertEqual(groups[0].page_rules, ["page_of_total_no_previous"])


class TestCentralNumberInText(unittest.TestCase):
    def test_six_digit_word_next_to_label_matches(self):
        words = [word("מרכזי", 100, 100), word("939158", 160, 100)]
        self.assertTrue(_central_number_in_text(words))

    def test_six_digit_word_far_from_label_does_not_match(self):
        words = [word("מרכזי", 100, 100), word("939158", 5000, 5000)]
        self.assertFalse(_central_number_in_text(words))

    def test_six_digit_word_with_no_label_anywhere_does_not_match(self):
        words = [word("16062026", 100, 100), word("939158", 160, 100)]
        self.assertFalse(_central_number_in_text(words))


class TestBlankPageReported(unittest.TestCase):
    def test_blank_page_is_skipped_and_reported_not_dropped_silently(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = str(Path(tmp_dir) / "fixture.pdf")
            doc = pymupdf.open()
            doc.new_page()  # page 1: blank
            non_blank = doc.new_page()  # page 2: not blank
            non_blank.draw_rect(non_blank.rect, color=(0, 0, 0), fill=(0, 0, 0))
            doc.save(pdf_path)
            doc.close()

            groups, skipped = run([pdf_path])

        self.assertEqual(skipped, [{"source": pdf_path, "page_number": 1}])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].pages[0].page_number, 2)


if __name__ == "__main__":
    unittest.main()

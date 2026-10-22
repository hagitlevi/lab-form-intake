import unittest
from types import SimpleNamespace as NS

from backend.tools.evaluate import classify_grouping, compare_list, compare_scalar, form_records, norm


def fr(value, state="safe", reasons=()):
    return NS(value=value, state=state, reasons=list(reasons))


class NormTests(unittest.TestCase):
    def test_id_padded(self):
        self.assertEqual(norm("national_id", "12345678"), "012345678")
        self.assertEqual(norm("national_id", "012345678"), "012345678")

    def test_dates(self):
        self.assertEqual(norm("received_date", "2026-08-13"), "2026-08-13")
        self.assertEqual(norm("received_date", "13.8.26"), "2026-08-13")

    def test_names_final_letters_and_spaces(self):
        self.assertEqual(norm("first_name", " דוד  "), norm("first_name", "דוד"))
        self.assertEqual(norm("last_name", "כהן"), norm("last_name", "כהן"))
        self.assertEqual(norm("first_name", "שלום"), norm("first_name", "שלומ"))

    def test_sender_canonical(self):
        self.assertEqual(norm("sender", "מכבי"), "מכבי")

    def test_empty_is_none(self):
        for f in ("first_name", "national_id", "received_date", "sender", "central_numbers"):
            self.assertIsNone(norm(f, ""))
            self.assertIsNone(norm(f, None))

    def test_commitment_keeps_leading_letter(self):
        self.assertEqual(norm("commitment_numbers", "x 132767"), "X132767")


class CompareTests(unittest.TestCase):
    def test_scalar_categories(self):
        self.assertEqual(compare_scalar("a", "a", "safe"), "correct_safe")
        self.assertEqual(compare_scalar("a", "a", "review"), "correct_review")
        self.assertEqual(compare_scalar("a", "b", "review"), "wrong_review")
        self.assertEqual(compare_scalar("a", "b", "safe"), "wrong_safe")
        self.assertEqual(compare_scalar(None, None, "review"), "both_empty")
        self.assertEqual(compare_scalar(None, "b", "safe"), "invented")
        self.assertEqual(compare_scalar("a", None, "review"), "missed")

    def test_list_per_item(self):
        out = compare_list({"111111", "222222"}, {"111111": "safe", "333333": "safe"})
        cats = {(e, g): c for c, e, g, _ in out}
        self.assertEqual(cats[("111111", "111111")], "correct_safe")
        self.assertEqual(cats[("(not this item)", "333333")], "wrong_safe")
        self.assertEqual(cats[("222222", None)], "missed")

    def test_list_invented_and_empty(self):
        self.assertEqual(compare_list(set(), {"1": "review"})[0][0], "invented")
        self.assertEqual(compare_list(set(), {})[0][0], "both_empty")
        self.assertEqual(compare_list({"1"}, {})[0][0], "missed")


class GroupingTests(unittest.TestCase):
    def test_labels(self):
        t = [{("f", 1)}, {("f", 2), ("f", 3)}, {("f", 4)}, {("f", 5)}]
        g = [{("f", 1)}, {("f", 2)}, {("f", 3), ("f", 4)}]
        self.assertEqual(classify_grouping(t, g), ["exact", "merged", "merged", "missing"])

    def test_split(self):
        self.assertEqual(classify_grouping([{("f", 1), ("f", 2)}], [{("f", 1)}, {("f", 2)}]), ["split"])


class FormRecordTests(unittest.TestCase):
    def test_wrong_safe_and_invented(self):
        tf = {"file": "a.pdf", "pages": [1], "filled": True, "first_name": "דן", "last_name": None,
              "national_id": "1", "central_numbers": ["123456"], "received_date": None,
              "sender": "מכבי", "commitment_numbers": []}
        conf = NS(first_name=fr("דנה"), last_name=fr("לוי", "review", ["x"]), national_id=fr("000000001"),
                  central_numbers=[fr("123456")], received_date=fr(""), sender=fr("מכבי"),
                  commitment_numbers=[fr("")])
        by = {(r["field"], r["cat"]) for r in form_records(tf, conf)}
        self.assertIn(("first_name", "wrong_safe"), by)
        self.assertIn(("last_name", "invented"), by)
        self.assertIn(("national_id", "correct_safe"), by)
        self.assertIn(("central_numbers", "correct_safe"), by)
        self.assertIn(("received_date", "both_empty"), by)
        self.assertIn(("commitment_numbers", "both_empty"), by)

    def test_no_pipeline_form_is_all_missed(self):
        tf = {"file": "a.pdf", "pages": [1], "filled": True, "first_name": "דן", "central_numbers": ["123456"]}
        cats = {(r["field"], r["cat"]) for r in form_records(tf, None)}
        self.assertIn(("first_name", "missed"), cats)
        self.assertIn(("central_numbers", "missed"), cats)


if __name__ == "__main__":
    unittest.main()

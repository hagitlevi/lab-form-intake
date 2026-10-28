import unittest

from backend.forms_api import MANUAL_OVERRIDE_REASON, _resolve_state

BAD_ID = "ספרת הביקורת של תעודת הזהות שגויה"
NOT_LISTED = "השולח אינו ברשימה הסגורה"


class TestResolveState(unittest.TestCase):
    def test_passing_validation_becomes_corrected(self):
        state, reasons = _resolve_state("001123324", [], old=None, has_prior_patch=False)
        self.assertEqual(state, "corrected")
        self.assertEqual(reasons, [])

    def test_none_value_always_corrected_empty(self):
        old = {"value": "x", "state": "review", "reasons": [BAD_ID]}
        state, reasons = _resolve_state(None, [], old=old, has_prior_patch=True)
        self.assertEqual(state, "corrected")
        self.assertEqual(reasons, [])

    def test_first_failing_attempt_stays_review_with_message(self):
        state, reasons = _resolve_state("001123344", [BAD_ID], old=None, has_prior_patch=False)
        self.assertEqual(state, "review")
        self.assertEqual(reasons, [BAD_ID])

    def test_first_ever_patch_matching_the_ai_original_value_still_stays_review(self):
        """A field already flagged 'review' by step 5 (never touched by a human) - the
        technician's FIRST PATCH, even if it resubmits that same AI value unchanged, must
        not jump straight to an override. Only a prior PATCH counts as "already told you
        once"."""
        old = {"value": "001123344", "state": "review", "reasons": [BAD_ID]}
        state, reasons = _resolve_state("001123344", [BAD_ID], old, has_prior_patch=False)
        self.assertEqual(state, "review")
        self.assertEqual(reasons, [BAD_ID])

    def test_first_failing_attempt_over_a_different_old_value_stays_review(self):
        old = {"value": "000000000", "state": "review", "reasons": ["לא נמצא בטופס"]}
        state, reasons = _resolve_state("001123344", [BAD_ID], old, has_prior_patch=True)
        self.assertEqual(state, "review")
        self.assertEqual(reasons, [BAD_ID])

    def test_resubmitting_same_failing_value_via_a_second_patch_is_a_manual_override(self):
        old = {"value": "001123344", "state": "review", "reasons": [BAD_ID]}
        state, reasons = _resolve_state("001123344", [BAD_ID], old, has_prior_patch=True)
        self.assertEqual(state, "corrected")
        self.assertEqual(reasons, [MANUAL_OVERRIDE_REASON])

    def test_resubmitting_a_different_failing_value_does_not_override(self):
        old = {"value": "001123344", "state": "review", "reasons": [BAD_ID]}
        state, reasons = _resolve_state("009999999", [BAD_ID], old, has_prior_patch=True)
        self.assertEqual(state, "review")
        self.assertEqual(reasons, [BAD_ID])

    def test_resubmitting_same_value_with_a_different_reason_does_not_override(self):
        old = {"value": "x", "state": "review", "reasons": [NOT_LISTED]}
        state, reasons = _resolve_state("x", [BAD_ID], old, has_prior_patch=True)
        self.assertEqual(state, "review")
        self.assertEqual(reasons, [BAD_ID])

    def test_already_overridden_value_resubmitted_unchanged_stays_overridden(self):
        old = {"value": "001123344", "state": "corrected", "reasons": [MANUAL_OVERRIDE_REASON]}
        state, reasons = _resolve_state("001123344", [BAD_ID], old, has_prior_patch=True)
        self.assertEqual(state, "corrected")
        self.assertEqual(reasons, [MANUAL_OVERRIDE_REASON])


if __name__ == "__main__":
    unittest.main()

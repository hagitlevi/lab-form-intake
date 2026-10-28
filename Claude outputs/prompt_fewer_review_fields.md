Goal: fewer correct values stuck in "review", with ZERO increase in WRONG+SAFE. Work in backend/pipeline/confidence.py (and backend/tools/evaluate.py for step 1). Read CLAUDE.md "Confidence" section and the confidence.py module docstring first. Do the steps in order and measure after each.

## Measuring (use for every step)
Run with the AI cache so Claude's output is identical between runs and only the confidence logic changes:
  $env:AI_CACHE="1"; $env:GOOGLE_VERIFY="1"; python -m backend.tools.evaluate
Compare against samples/eval_with_google.md (current baseline: correct+safe 68, correct+review 37, WRONG+SAFE 7).
A step is kept only if correct+safe goes up and WRONG+SAFE does not go up. If WRONG+SAFE rises, revert that step and tell me which forms broke.

## Step 1 - see WHY fields are in review (evaluate.py only, no pipeline change)
Add a section "## Review reasons" to the report: for every field item whose value is correct but state is "review", count by (field, reason). Reasons are the Hebrew strings in FieldResult.reasons; group "התאמה קרובה..." near-match reasons under one bucket "near match". Print a table field | reason | count, sorted by count. Run it and show me the table before doing step 2.

## Step 2 - national ID: compare padded to padded
score_national_id pads Claude's value to 9 digits (pad_national_id) but find_match normalizes OCR words with normalize_digits only. A form that prints an 8-digit ID ("12345678") therefore never matches "012345678". Fix: pass a normalize_fn that pads OCR candidates the same way, but only for candidates of 7-9 digits (so short numbers are not padded into fake IDs). Do not change the ID_WARNING behaviour.

## Step 3 - numbers split across several OCR words
find_match only joins TWO adjacent words. Many forms print the ID (and some commitment numbers) in boxes, one digit or a few digits per box, so OCR returns 3-9 separate words. Extend the adjacent-word concatenation to runs of up to 10 consecutive words on the same line (same _adjacent test between each neighbour, left-to-right AND right-to-left order since the page is RTL). Only digit-only words take part in runs. Keep the existing exact/near semantics; a run match is "exact" only if the joined digits equal the value exactly.

## Step 4 - dates: compare day/month, not raw strings
score_received_date matches normalize_digits(raw_text) against single OCR words, so "25/8/2026" vs "25/08/2026" or a date split into words never matches. Fix: parse OCR words (and adjacent runs from step 3) that look like dates (d/m, d.m, d-m, with optional 2- or 4-digit year, with or without leading zeros) into (day, month, year?) and treat it as exact when day and month equal the extracted date and the year either equals it or is absent on the OCR side. Keep every existing rule check (future date, invalid, year_inferred, visit_date) BEFORE the match, unchanged.

## Rules
- Do not touch the rule checks, Google verification, or the AI prompt.
- Add unit tests in tests/ for steps 2-4 (8-digit ID, boxed ID split into 9 words, date with/without leading zeros, a date that must NOT match: different day).
- Run the full test suite.
- At the end, show the before/after overall table and the WRONG+SAFE list, and tell me which CLAUDE.md Confidence lines need updating (find_match semantics changed).

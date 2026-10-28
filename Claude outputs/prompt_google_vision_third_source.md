Add Google Cloud Vision as a third verification source, called only for forms that still have fields in "review" after step 5.

## Context (read first)
- CLAUDE.md, backend/pipeline/confidence.py (module docstring = the confidence rules), backend/pipeline/google_ocr.py, backend/batch_runner.py (process_group, around the `score_form(...)` call ~line 229).
- Today google_ocr.py is only reachable through `OCR_ENGINE=google` in `run_ocr()` (backend/pipeline/__init__.py ~line 285), which REPLACES Tesseract. Leave that experiment path as is; do not change Tesseract, scan.py, or the step-2 cache (`settings_hash`).

## What to build
1. New env flag `GOOGLE_VERIFY` (default off; on for "1/true/yes"). Read it in one place (backend/config.py if that's where env flags live, else top of batch_runner.py). Requires `GOOGLE_VISION_API_KEY`; if the flag is on and the key is missing, log one warning at startup and behave as off.
2. In batch_runner.process_group, right after `confidence = score_form(form, form_pages, today)`:
   - If the flag is on AND at least one field in `confidence` has `state == "review"` AND a non-empty `value` (include list items in central_numbers / commitment_numbers), then:
     - For each page of this form, decode the stored page JPEG (the same bytes already loaded into `group_images` - this is scan.color, the oriented/deskewed image, so its coordinates match the Tesseract words and barcodes) and call `google_ocr(image)`.
     - Build new PageData objects with `words = tesseract_words + google_words` (same barcodes, same source/page_number). Plain concatenation is fine: find_match only needs any word to match.
     - Re-run `score_form(form, extended_pages, today)` and use that result instead.
   - Pages are independent: call Google for a form's pages concurrently is NOT needed; sequential is fine.
   - Fields with no value, or that failed a rule (bad ID check digit, future date, unknown sender, wrong format) stay "review" - that's automatic, since score_form checks rules before the second-source match. Do not change confidence.py logic.
3. Failure must never break a form: wrap the Google step in try/except (GoogleOcrError and any Exception); on failure log a warning with the form/page id and keep the original Tesseract-only confidence. Never write to forms.error for this.
4. When a field becomes "safe" only thanks to Google, its reason/source should say so, so the lab tech and the judges can see the third source working. Simplest way: tag Google words when parsing (e.g. a new optional field on Word, `engine: str = "tesseract"`, set to "google" in google_ocr.parse_words) and in find_match/FieldResult carry it through so the UI can show "אומת ע"י Google Vision". If this turns into more than ~30 lines across files, skip the tagging and just log per form how many fields Google flipped from review to safe.
5. Privacy (CLAUDE.md rule): Google OCR words live only in memory for this re-score. Do not cache them, do not store them in the DB, do not log their text. Only the matched word's bbox may be kept, exactly as for Tesseract.
6. Update the docstring at the top of google_ocr.py (it currently says "never in the cloud") to describe GOOGLE_VERIFY, and add GOOGLE_VERIFY + GOOGLE_VISION_API_KEY to .env.example (placeholder value, never a real key).
7. Remind me at the end which lines in CLAUDE.md need updating (the OCR_ENGINE / privacy line and the Confidence section: "second source" can now be Tesseract, barcode, or Google Vision for review fields).

## Tests
- Unit test in tests/: mock google_ocr to return a Word whose text equals a review field's value; assert the field flips to "safe". Second test: mock it to raise GoogleOcrError; assert the confidence is unchanged and no exception escapes.
- Assert google_ocr is NOT called when the flag is off, and NOT called when every field is already "safe".
- Run the existing test suite and report results.

Keep it minimal: no new dependencies (google_ocr.py already uses urllib), no new abstractions beyond what's listed.

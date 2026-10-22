# lab-form-intake

Hackathon project (AI Open Code Hackathon 2026, Central Virology Lab challenge).
Turns scanned / photographed / handwritten lab commitment forms (טפסי התחייבות) into
structured records that a lab technician reviews, corrects and approves.
Final demo: 28/10/2026. Prefer simple, working code over abstractions.

## Stack
- Backend: Python 3.13, FastAPI, psycopg 3 with ConnectionPool, PostgreSQL (Render, Frankfurt)
- Processing: PyMuPDF (PDF → images), OpenCV (preprocessing), Tesseract via pytesseract
  (lang "heb+eng", runs locally), zxing-cpp (barcodes), Anthropic SDK (multimodal extraction)
- Frontend: React + Vite, Hebrew RTL UI, built and served as static files by FastAPI (one service, one URL).
  FastAPI serves frontend/dist and returns index.html for every non-/api path. frontend/dist is not in git.
- Config from environment (.env locally, never committed): DATABASE_URL, ANTHROPIC_API_KEY,
  ANTHROPIC_MODEL, TESSERACT_CMD (optional, Windows only), LOCAL_WORKERS (default 2), AI_WORKERS (default 3),
  AI_CACHE (dev only, default off; never enable in the cloud - it writes patient fields to disk)
- Deployment: Render web service using Docker (step 11). Multi-stage build: Node stage runs
  npm ci && npm run build, Python stage installs tesseract-ocr + tesseract-ocr-heb and copies dist.
  Page images are stored in the DB (BYTEA) because Render's disk is ephemeral.

## Fields extracted per commitment (7)
first_name, last_name, national_id, central_numbers (list), received_date,
sender (hospital / HMO, chosen from a closed list), commitment_numbers (list).
One record per commitment number; patient fields live on the form and are shared.

## Domain rules
- Central number: lab sticker with a barcode, a 6-digit printed number, and the word "מרכזי"
  beside it. Sometimes handwritten. A form may have several central numbers: keep all.
  A central number is always a standalone 6-digit number: never cut one out of a longer number
  (e.g. 944000 from sample number 944000361) - if the only candidate is part of a longer number,
  return null. A handwritten 6-digit number with a short date beside it ("944619 / 13.8.26")
  is the central number plus the received date, like "947054-25.8".
- Received date: "התקבל" stamp, or handwritten under the central number. Missing year → take the
  upload year and flag "השנה הושלמה". A date taken from the printed visit date (no "התקבל" stamp)
  is flagged with a reason.
- Commitment number: may appear as "מספר התחייבות", "דרישה", or (Leumit only) "דגימה".
  The word "דגימה" appears on most forms with other meanings; treat it as a commitment
  number only on Leumit forms, or on a form that states the sample number is the commitment
  number ("מספר הדגימה מהווה מספר התחייבות", e.g. Rambam). A form may have several commitment numbers: keep all.
  Hospital system screenshots: every number in the "מספר התחייבות" table column is a commitment number.
- A field that cannot be read is left empty (null). Never guess a value.
- The sample forms have replaced personal details (identical blue handwritten name/ID on all forms)
  and organizer yellow highlighter. Never use either for grouping or confidence rules, but DO extract
  the handwritten name and ID as the patient's first_name, last_name and national_id.
  The AI prompt has a "Patient name" section: first word = first_name, the rest = last_name; never the
  commitment issuer ("מפיק/ת ההתחייבות") or a doctor.

## Pipeline
1. Split uploads into an ordered list of pages (file order as shown in the UI, then page order).
   Blank pages are skipped and reported.
2. Per page, in parallel (LOCAL_WORKERS): digital-PDF check (text layer AND no full-page image);
   if scanned: preprocess (rotate, deskew, clean) → OCR words with bounding boxes → barcodes.
   If digital: take text from the PDF instead of preprocessing and OCR.
3. Group pages into forms (see Grouping).
4. Per form, in parallel (AI_WORKERS): ONE multimodal call with all page images of that form.
   The response may contain several forms if the group actually holds different patients.
5. Validate and score each field (see Confidence).
6. Save as pending records; the technician reviews, corrects, approves.

## Grouping
Default: a page starts a new form. Checks in order, stop at first match:
1. a decoded 6-digit barcode → new form. Not necessarily the central number itself (some
   6-digit barcodes are commitment numbers, e.g. Bnei Zion) - just a grouping signal;
2. a 6-digit number directly next to a printed "מרכזי" in OCR text (also OCR a crop around
   each barcode rotated 90°/270°, since the label is often printed sideways) → new form;
3. "Page/עמוד n of/מתוך m" with n > 1 → continuation of the previous page;
4. otherwise → new form.
Continuations may cross file boundaries. A first page of the batch reporting n > 1 (no
previous page to continue) → new form, flagged.
Only printed barcodes and printed OCR text decide this - never handwriting, highlighter
color, or blue handwritten IDs. Not used in the decision (though extracted values may still
be kept as informational fields): form titles, "מספר התחייבות", patient stickers, national ID
changes, case numbers, form codes, and any "fall back to continuation" rule. A wrong merge of
two forms is worse than a wrong split, so the default is to split, not to merge.
After AI extraction:
- the AI finds two forms in one group (two central numbers or two patients) → split and flag;
- a form with no name, ID, central number and commitment number → tag "כנראה המשך של הטופס הקודם"
  with "צירוף" (merge, one new AI call for the merged form) and "זה טופס נפרד".
Form-level `flagged` means an uncertain form boundary; it is separate from field-level orange.

## Confidence (per field, positive checks; any "no" → flagged for review)
AI found a value? → value passes the rules? → exact same value found by another source
(OCR words, or barcode for central numbers)? → SAFE.
Central number rules, in order: no value → "לא נמצא מספר מרכזי"; not exactly 6 digits → flagged;
a decoded 6-digit barcode equal to the value → safe (barcode is the second source); a decoded 6-digit
barcode that differs → "הברקוד בעמוד לא תואם", unless that barcode equals one of the form's commitment
numbers (Bnei Zion), which is ignored; no usable barcode → NOT a flag reason, normal OCR second-source check.
Near match (one character different) is flagged and the differing character is reported.
Rules: Israeli ID check digit (pad to 9), date valid and not in the future,
sender in the closed list (fuzzy match), commitment number format per sender when known.
Only two states are shown in the UI: safe or "for review" (orange), always with a reason in Hebrew.
Manual edit: a typed value that fails validation stays orange with the error; pressing Enter again
with the same value accepts it ("אושר ידנית למרות שגיאת אימות"), recorded in the audit log.

## API
POST /api/batches, GET /api/batches/{id}, GET /api/pages/{id}/image, PATCH /api/forms/{id}
(list items as {"commitment_numbers": {"<item_index>": value}}), merge-with-previous, mark-separate,
approve (409 while any field is orange), GET /api/records, GET /api/records/export.csv (UTF-8 BOM),
GET /api/senders.

## Frontend
Routes (small history-based router in App.jsx, no library): / upload, /batches/:id review table,
/forms/:id single-form view (page image with the selected field's bbox, zoom, rotate, page tabs),
/records. Reuse frontend/src/styles.css and the patterns in Review.jsx; follow design-reference/
(not in git). Do not introduce a new visual style. Font Heebo; background #F4F7F9; white cards,
border #DDE3E8; primary #0B6A85; orange ONLY for fields for review (bg #FFF3E6, 2px border #E8892B,
reason text #8A4500); status badges pending #FFF3E6/#8A4500, approved #E5F3EA/#1D6B37.
All numbers dir="ltr" with tabular digits.

## Privacy
Only form page images are sent to the AI; OCR runs locally. Extract and store only the 7 fields,
their metadata and the page images. Never store full OCR text, diagnoses, addresses or phones.

## Local development
- Server: python -m uvicorn backend.main:app --reload --reload-dir backend --port 8080
  (port 8000 is blocked on Windows). Use 127.0.0.1, not localhost, also in the Vite proxy.
- Frontend dev: npm run dev in frontend/ → http://127.0.0.1:5173.
- DB: docker container labpg (postgres) on port 5433 (5432 is taken locally).
- Tests: python -m unittest (pytest is not installed). Tools: python -m backend.tools.scan_pdf,
  python -m backend.tools.group_pdf, python -m backend.tools.e2e_api (server must be running).
- samples/ is git-ignored (sample PDFs, grouping_truth.json, debug output).
- experiments/ holds side experiments (e.g. hw_ocr handwriting OCR); not part of the app.

## Conventions
- Run the relevant tests after each change. Keep secrets out of code and git.
- If you change any rule, endpoint or storage described here, update this file in the same commit.
- Finish each task with a short summary of what was built and how to run it, then stop.
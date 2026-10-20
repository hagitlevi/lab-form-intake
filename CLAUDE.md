# lab-form-intake

Hackathon project (AI Open Code Hackathon 2026, Central Virology Lab challenge).
Turns scanned / photographed / handwritten lab commitment forms (טפסי התחייבות) into
structured records that a lab technician reviews, corrects and approves.
Final demo: 28/10/2026. Prefer simple, working code over abstractions.

## Stack
- Backend: Python 3.13, FastAPI, psycopg 3 with ConnectionPool, PostgreSQL (Render, Frankfurt)
- Processing: PyMuPDF (PDF → images), OpenCV (preprocessing), Tesseract via pytesseract
  (lang "heb+eng", runs locally), zxing-cpp (barcodes), Anthropic SDK (multimodal extraction)
- Frontend: React + Vite, Hebrew RTL UI, built and served as static files by FastAPI (one service, one URL)
- Config from environment (.env locally, never committed): DATABASE_URL, ANTHROPIC_API_KEY,
  ANTHROPIC_MODEL, TESSERACT_CMD (optional, Windows only), LOCAL_WORKERS (default 2), AI_WORKERS (default 6)
- Deployment: Render web service using Docker. Do NOT create a Dockerfile or deploy config
  until explicitly asked.

## Fields extracted per commitment (7)
first_name, last_name, national_id, central_numbers (list), received_date,
sender (hospital / HMO, chosen from a closed list), commitment_numbers (list).
One record per commitment number; patient fields live on the form and are shared.

## Domain rules
- Central number: lab sticker with a barcode, a 6-digit printed number, and the word "מרכזי"
  beside it. Sometimes handwritten. A form may have several central numbers: keep all.
- Received date: "התקבל" stamp, or handwritten under the central number.
- Commitment number: may appear as "מספר התחייבות", "דרישה", or (Leumit only) "דגימה".
  The word "דגימה" appears on most forms with other meanings; treat it as a commitment
  number only on Leumit forms. A form may have several commitment numbers: keep all.
- A field that cannot be read is left empty (null). Never guess a value.

## Pipeline
1. Split uploads into an ordered list of pages (file order as shown in the UI, then page order).
2. Per page, in parallel (LOCAL_WORKERS): digital-PDF check (text layer AND no full-page image);
   if scanned: preprocess (rotate, deskew, clean) → OCR words with bounding boxes → barcodes.
   If digital: take text from the PDF instead of preprocessing and OCR.
3. Group pages into forms, checks in order, stop at first match:
   central barcode found → new form;
   6-digit central number in OCR text, or undecodable barcode region → new form, flagged "barcode unread";
   "Page/עמוד n of/מתוך m" with n > 1 → continuation;
   form title, or a national ID different from the previous page → new form, flagged;
   first page of the batch → error shown to the user;
   otherwise → continuation of the previous page. Continuations may cross file boundaries.
4. Per form, in parallel (AI_WORKERS): ONE multimodal call with all page images of that form.
   The response may contain several forms if the group actually holds different patients.
5. Validate and score each field (see Confidence).
6. Save as pending records; the technician reviews, corrects, approves.

## Confidence (per field, positive checks; any "no" → flagged for review)
AI found a value? → value passes the rules? → (central number only) barcode agrees?
→ exact same value found by another source (OCR words, or barcode for central numbers)? → SAFE.
Near match (one character different) is flagged and the differing character is reported.
Rules: Israeli ID check digit (pad to 9), date valid and not in the future,
sender in the closed list (fuzzy match), commitment number format per sender when known.
Only two states are shown in the UI: safe or "for review" (orange), always with a reason.

## Privacy
Only form page images are sent to the AI; OCR runs locally. Extract and store only the 7 fields,
their metadata and the page images. Never store full OCR text, diagnoses, addresses or phones.

## Conventions
- Run the relevant tests after each change. Keep secrets out of code and git.
- Finish each task with a short summary of what was built and how to run it, then stop.

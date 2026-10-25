"""One-off check that GOOGLE_VISION_API_KEY works. Run from the repo root:
    python scripts/check_google_vision.py
Sends the first non-blank sample page (personal data already replaced) to Google Vision. Prints counts only, never text."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
import pymupdf

from backend.pipeline import load_page
from backend.pipeline.google_ocr import GoogleOcrError, google_ocr

path = "samples/commitments_part1.pdf"
doc = pymupdf.open(path)
page = next(pg for i in range(doc.page_count) if (pg := load_page(doc, path, i)) is not None)  # skip blank pages
print(f"testing page {page.page_number} of {path}")
try:
    words = google_ocr(page.image)
except GoogleOcrError as e:
    print("FAILED:", e)
    sys.exit(1)
print(f"OK - Google Vision returned {len(words)} words, {sum(w.text.isdigit() for w in words)} of them digits-only")

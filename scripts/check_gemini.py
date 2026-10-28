"""One-off check that GEMINI_API_KEY and GEMINI_MODEL work. Run from the repo root:
    python scripts/check_gemini.py
Sends the first non-blank sample page (personal data already replaced) to Gemini and prints
which fields came back filled - never their values."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
import cv2
import pymupdf

from backend.pipeline import load_page
from backend.pipeline import gemini_verify as gv

path = "samples/commitments_part1.pdf"
doc = pymupdf.open(path)
page = next(pg for i in range(doc.page_count) if (pg := load_page(doc, path, i)) is not None)
ok, buf = cv2.imencode(".jpg", page.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
print(f"model: {gv.MODEL} - testing page {page.page_number} of {path}")
try:
    data = gv.read_fields([buf.tobytes()])
except gv.GeminiError as e:
    print("FAILED:", e)
    sys.exit(1)
filled = [k for k, v in data.items() if v not in (None, "", [], {})]
print(f"OK - Gemini answered; filled fields: {', '.join(filled) or 'none'}")

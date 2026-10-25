"""Google Cloud Vision (DOCUMENT_TEXT_DETECTION), two optional uses. Both need GOOGLE_VISION_API_KEY.

1. OCR_ENGINE=google: replaces Tesseract in run_ocr() (experiment, off by default).
2. GOOGLE_VERIFY=1: third verification source. Only forms that still have a "review" field with a
   value after score_form() send their page images to Google; the words are added to the
   Tesseract words for one re-score (rescore_with_google) and then dropped - never cached, stored
   or logged. A fallen-over call keeps the Tesseract-only result.

Privacy: both send page images to Google. Use them only on sample forms with replaced personal
data, never in the cloud deployment without a decision on that.
Google documents handwriting support for Latin scripts only, so Hebrew handwriting is a test.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()
VERIFY = os.environ.get("GOOGLE_VERIFY", "").lower() in ("1", "true", "yes")
if VERIFY and not os.environ.get("GOOGLE_VISION_API_KEY"):
    log.warning("GOOGLE_VERIFY is on but GOOGLE_VISION_API_KEY is missing: Google verification disabled")
    VERIFY = False

URL = "https://vision.googleapis.com/v1/images:annotate"


class GoogleOcrError(RuntimeError):
    pass


def _annotate(jpeg: bytes) -> dict:
    key = os.environ.get("GOOGLE_VISION_API_KEY")
    if not key:
        raise GoogleOcrError("set GOOGLE_VISION_API_KEY")
    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(jpeg).decode()},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["he", "en"]},
            }
        ]
    }
    req = urllib.request.Request(
        f"{URL}?key={key}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())["responses"][0]
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        raise GoogleOcrError(f"Google Vision request failed: {e}") from e
    if "error" in resp:
        raise GoogleOcrError(f"Google Vision: {resp['error'].get('message')}")
    return resp


def parse_words(resp: dict, scale: float = 1.0) -> list:
    """Vision response -> backend.pipeline.Word list (coordinates divided by scale)."""
    from backend.pipeline import Word  # late import: backend.pipeline imports this module

    words = []
    for page in resp.get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for w in para.get("words", []):
                    text = "".join(s.get("text", "") for s in w.get("symbols", []))
                    v = w["boundingBox"]["vertices"]
                    xs, ys = [p.get("x", 0) for p in v], [p.get("y", 0) for p in v]
                    if not text.strip():
                        continue
                    words.append(
                        Word(
                            text=text,
                            conf=float(w.get("confidence", 0)) * 100,  # Tesseract-style 0-100
                            left=round(min(xs) / scale),
                            top=round(min(ys) / scale),
                            width=round((max(xs) - min(xs)) / scale),
                            height=round((max(ys) - min(ys)) / scale),
                        )
                    )
    return words


def google_ocr(image: np.ndarray, scale: float = 1.0) -> list:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise GoogleOcrError("JPEG encode failed")
    return parse_words(_annotate(buf.tobytes()), scale)


def rescore_with_google(form, pages, images: list[bytes], confidence, today):
    """Re-run score_form with Google words added, if any field with a value is still "review".
    pages and images are the form's pages, same order. Never raises: on failure returns confidence."""
    from dataclasses import replace

    from backend.pipeline.confidence import score_form  # late import: confidence imports backend.pipeline

    def fields(c):
        for f in type(c).model_fields:
            v = getattr(c, f)
            yield from v if isinstance(v, list) else [v]

    if not VERIFY or not any(f.state == "review" and f.value for f in fields(confidence)):
        return confidence
    try:
        extended = [
            replace(p, words=p.words + google_ocr(cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)))
            for p, img in zip(pages, images)
        ]
        new = score_form(form, extended, today)
    except Exception as exc:  # GoogleOcrError or anything else: Tesseract-only result stays
        log.warning("Google verification failed for pages %s: %s", [(p.source, p.page_number) for p in pages], exc)
        return confidence
    flipped = sum(a.state == "review" and b.state == "safe" for a, b in zip(fields(confidence), fields(new)))
    log.info("Google verification flipped %d field(s) from review to safe", flipped)
    return new

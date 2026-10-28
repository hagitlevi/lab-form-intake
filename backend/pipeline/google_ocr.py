"""Optional OCR engine: Google Cloud Vision (DOCUMENT_TEXT_DETECTION) instead of Tesseract.

Off by default. Turn on with OCR_ENGINE=google and GOOGLE_VISION_API_KEY. run_ocr() in
backend.pipeline delegates here; everything downstream still gets the same Word list.

Privacy: unlike Tesseract this sends page images to Google. Use it only on sample forms with
replaced personal data, never in the cloud deployment without a decision on that.
Google documents handwriting support for Latin scripts only, so Hebrew handwriting is a test.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

import cv2
import numpy as np

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

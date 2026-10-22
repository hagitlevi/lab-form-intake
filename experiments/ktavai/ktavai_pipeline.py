"""KtavAI (cloud Hebrew HTR) + optional Claude post-correction. Side experiment, not part of the app.

NOT VERIFIED against KtavAI's real API: I could not find its docs. The URL, auth header, upload
field name and response shape below are assumptions - set them from their docs and fix
`_post_image` / `parse_response` if they differ.

Privacy: this sends the image to a third-party cloud (KtavAI) and, for correct_with_claude, the
recognised text to Anthropic. Use only on sample forms with replaced personal data.

Env: KTAVAI_API_URL, KTAVAI_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_MODEL (optional).
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from html import unescape

import cv2
import numpy as np

from backend.pipeline.extract import blue_ink_crops  # blue-ink isolation, same code as the main pipeline


class KtavAIError(RuntimeError):
    pass


def _post_image(jpeg: bytes) -> str:
    url, key = os.environ.get("KTAVAI_API_URL"), os.environ.get("KTAVAI_API_KEY")
    if not url or not key:
        raise KtavAIError("set KTAVAI_API_URL and KTAVAI_API_KEY")
    boundary = uuid.uuid4().hex  # stdlib multipart: neither requests nor httpx is installed
    head = "\r\n".join(
        [
            f"--{boundary}",
            'Content-Disposition: form-data; name="file"; filename="page.jpg"',
            "Content-Type: image/jpeg",
            "",
            "",
        ]
    )
    body = head.encode() + jpeg + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",  # assumption
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as e:
        raise KtavAIError(f"KtavAI request failed: {e}") from e


def parse_response(body: str) -> str:
    """Plain text stays as is; hOCR / ALTO XML is reduced to its words, one line per line."""
    if "<" not in body:
        return body.strip()
    if "<String " in body:  # ALTO
        lines = re.split(r"</TextLine>", body)
        return "\n".join(
            " ".join(unescape(w) for w in re.findall(r'CONTENT="([^"]*)"', ln)) for ln in lines
        ).strip()
    lines = re.findall(r'<span class=.ocr_line.[^>]*>(.*?)</span>\s*(?=<span class=.ocr_line|</)', body, re.S)
    lines = lines or [body]
    return "\n".join(
        " ".join(" ".join(unescape(t) for t in re.findall(r">([^<>]+)<", f">{ln}<")).split()) for ln in lines
    ).strip()


def read_handwriting(path: str, blue_only: bool = False) -> list[str]:
    """Send the image (or its blue-ink crops) to KtavAI. Returns one text per image sent."""
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)  # fromfile: Hebrew/Unicode paths on Windows
    if img is None:
        raise KtavAIError(f"cannot read image: {path}")
    if blue_only:
        crops = blue_ink_crops(img)
        if not crops:
            return []
    else:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        crops = [buf.tobytes()] if ok else []
    return [parse_response(_post_image(c)) for c in crops]


def correct_with_claude(text: str) -> str:
    """Fix OCR slips in Hebrew text. Never adds words; unreadable parts stay as they are.
    Numbers (IDs, central/commitment numbers) are never touched - a wrong guess there is worse
    than a flagged one."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        max_tokens=1024,
        system=(
            "You get raw OCR output of Hebrew handwriting. Fix only clear letter-level slips and split or "
            "joined words. Do not add, translate or complete words, and never change digits. If a part is "
            "unclear, copy it unchanged. Reply with the corrected text only."
        ),
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text.strip()


if __name__ == "__main__":
    if len(sys.argv) > 1:  # python -m experiments.ktavai.ktavai_pipeline handwriting.jpg [--blue] [--fix]
        raw = read_handwriting(sys.argv[1], blue_only="--blue" in sys.argv)
        for t in raw:
            print("KtavAI:", t)
            if "--fix" in sys.argv:
                print("Claude:", correct_with_claude(t))
    else:  # offline self-check of the parser only
        assert parse_response("ישראל ישראלי\n") == "ישראל ישראלי"
        alto = '<TextLine><String CONTENT="ישראל"/><String CONTENT="ישראלי"/></TextLine>'
        assert parse_response(alto) == "ישראל ישראלי", parse_response(alto)
        hocr = '<span class="ocr_line" title="x"><span class="ocrx_word">ישראל</span> <span class="ocrx_word">ישראלי</span></span></div>'
        assert parse_response(hocr) == "ישראל ישראלי", parse_response(hocr)
        print("parser ok")

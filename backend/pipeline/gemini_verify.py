"""Gemini as the second reader for fields still in "review" (replaces Google Vision verification).

GEMINI_VERIFY=1 + GEMINI_API_KEY. After score_form() (Claude vs Tesseract/barcodes), a form that still
has a review field sends its page images to Gemini ONCE. Gemini reads the 7 fields independently -
it is never shown Claude's answer. Then, per review field:

- Gemini read the same value (after the same normalization the rules use) and the field's only
  problem was "no second source" / a near OCR match  ->  "safe".
- Gemini read something else  ->  stays "review", with the reason "Gemini קרא 'X'" so the lab
  technician sees the alternative. Never auto-corrected.
- Claude found nothing but Gemini read a value  ->  the value is shown, still "review", with the
  reason "נקרא רק על ידי Gemini" (saves typing; the technician confirms it). Never "safe".
- A field that failed a RULE (bad format for the sender, future/inferred/visit date, barcode
  conflict, not in the closed sender list, same as a central number) is never flipped by Gemini.

Privacy: page images go to Google. Use a PAID Gemini API key only (on the free tier Google may use
prompts for training). Nothing Gemini returns is stored except the value it read for a disagreeing
field, inside that field's review reason. Any failure keeps the result as it was.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

VERIFY = os.environ.get("GEMINI_VERIFY", "").lower() in ("1", "true", "yes")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
if VERIFY and not os.environ.get("GEMINI_API_KEY"):
    log.warning("GEMINI_VERIFY is on but GEMINI_API_KEY is missing: Gemini verification disabled")
    VERIFY = False

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TRANSIENT = ("unavailable", "overloaded", "deadline", "timed out", "503", "502", "500", "429", "rate", "resource_exhausted")

PROMPT = """You are reading an Israeli medical lab commitment form (טופס התחייבות). The images are its pages.
Read these fields and return ONLY a JSON object, no other text:

{"first_name": str|null, "last_name": str|null, "national_id": str|null,
 "central_numbers": [str], "received_date": {"day": int|null, "month": int|null, "year": int|null} | null,
 "sender": str|null, "commitment_numbers": [str]}

Rules:
- first_name / last_name: the PATIENT's name (often handwritten, near "שם החולה" / "נבדק"), not a doctor.
  Copy names EXACTLY as written, in the same script (Hebrew stays Hebrew letters). Never translate or
  transliterate a name (e.g. never "Isaac" for "יוסף"/"יצחק", never "Joel" for "טובי").
- national_id: the patient's ID number (ת.ז. / מספר זיהוי), digits only.
- central_numbers: 6-digit lab numbers on a sticker with a barcode and the word "מרכזי" (or handwritten next to "מרכזי"). All of them.
- received_date: the "התקבל" stamp date, or a handwritten date under the central number.
- sender: the HMO or hospital that sent the form (כללית, מכבי, מאוחדת, לאומית, or a hospital name), in Hebrew as printed.
  Take it from the logo/header of the form. The receiving lab's "התקבל" stamp ("המעבדה המרכזית לנגיפים",
  "המרכז הרפואי המשולב") is NEVER the sender.
- commitment_numbers: numbers labeled "מספר התחייבות" / "דרישה" (on Leumit: "דגימה"). Never a central number.
  A form's own serial number (only "מס'" beside the form title) is NOT a commitment number. On Wolfson
  (וולפסון) forms the commitment number is the 7-digit number on the barcode sticker.
- If a field is not on the form or you cannot read it with confidence, use null (or [] for lists). Never guess.
- Never output placeholder or label text such as "First", "Last", "Name", "illegible", "unknown" - use null.
- Copy digits exactly as written; do not fix or complete them."""


class GeminiError(RuntimeError):
    pass


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        URL.format(model=MODEL), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise GeminiError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise GeminiError(f"Gemini request failed: {e}") from e


def read_fields(images: list[bytes], attempts: int = 3) -> dict:
    """One Gemini call for one form's page JPEGs -> the parsed JSON dict."""
    parts = [{"text": PROMPT}] + [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}} for img in images
    ]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json"}}
    for attempt in range(attempts):
        try:
            resp = _post(body)
            text = "".join(p.get("text", "") for c in resp.get("candidates", [])[:1]
                           for p in c.get("content", {}).get("parts", []) if not p.get("thought"))
            if not text:
                raise GeminiError(f"empty Gemini answer: {str(resp)[:200]}")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise GeminiError("Gemini answer is not a JSON object")
            return data
        except (GeminiError, json.JSONDecodeError) as e:
            if attempt == attempts - 1 or not any(t in str(e).lower() for t in _TRANSIENT):
                raise GeminiError(str(e)) from e
            log.info("Gemini transient error (attempt %d/%d): %s", attempt + 1, attempts, e)
            time.sleep(2 * (attempt + 1))
    raise GeminiError("unreachable")


# --- comparing Gemini with the scored fields ---------------------------------------------------

def _verifiable(reasons: list[str]) -> bool:
    """True when a review field's ONLY problem is the missing second source (no rule failed)."""
    from backend.pipeline.confidence import ID_WARNING_PREFIX
    real = [r for r in reasons if not r.startswith(ID_WARNING_PREFIX) and not r.startswith("[debug]")]
    return bool(real) and all(r == "לא אומת על ידי מקור נוסף" or r.startswith("התאמה קרובה") for r in real)


def _norms():
    from backend.pipeline.confidence import sender_fuzzy_match
    from backend.pipeline.confidence import id_key
    from backend.pipeline.normalize import normalize_digits, normalize_hebrew_name
    name = lambda v: normalize_hebrew_name(v) if v else ""
    return {
        "first_name": name,
        "last_name": name,
        "national_id": lambda v: id_key(v) if v else "",
        "sender": lambda v: (sender_fuzzy_match(v) or v.strip()) if v else "",
        "central_numbers": lambda v: normalize_digits(v) if v else "",
        "commitment_numbers": lambda v: normalize_digits(v, keep_leading_letter=True) if v else "",
    }


def _gemini_date(g: dict) -> Optional[tuple]:
    d = g.get("received_date") or {}
    if not isinstance(d, dict) or d.get("day") is None or d.get("month") is None:
        return None
    y = d.get("year")
    if isinstance(y, int) and y < 100:
        y += 2000
    return int(d["day"]), int(d["month"]), y


GEMINI_ONLY = "נקרא רק על ידי Gemini (Claude לא מצא) - יש לאמת"


def _display(name: str, raw: str, norm) -> str:
    """The value to SHOW for a Gemini-only reading: names as Gemini wrote them, numbers normalized."""
    if name in ("first_name", "last_name"):
        return raw.strip()
    if name == "national_id":
        from backend.pipeline.normalize import normalize_digits
        return normalize_digits(raw)  # as read, never padded
    return norm(raw)


_HEBREW = re.compile(r"[\u05d0-\u05ea]")
_DIGIT = re.compile(r"\d")


def sanitize(gemini: dict) -> dict:
    """Drop readings that cannot be real values: names/sender without a Hebrew letter (Gemini has
    returned the placeholders "First"/"Last" for an unreadable name), numbers without a digit."""
    out = dict(gemini)
    for name in ("first_name", "last_name", "sender"):
        v = out.get(name)
        if not (isinstance(v, str) and _HEBREW.search(v)):
            out[name] = None
    v = out.get("national_id")
    if not (isinstance(v, (str, int)) and _DIGIT.search(str(v))):
        out["national_id"] = None
    for name in ("central_numbers", "commitment_numbers"):
        v = out.get(name)
        out[name] = [x for x in (v if isinstance(v, list) else []) if _DIGIT.search(str(x))]
    if not isinstance(out.get("received_date"), dict):
        out["received_date"] = None
    return out


def apply(confidence, gemini: dict):
    """Pure function: confidence + Gemini's dict -> new confidence (see module docstring)."""
    from backend.pipeline.confidence import NOT_FOUND, id_warning
    gemini = sanitize(gemini)
    norms = _norms()
    updates = {}
    for name, norm in norms.items():
        current = getattr(confidence, name)
        is_list = isinstance(current, list)
        g = gemini.get(name)
        g_raw = [str(x) for x in (g if isinstance(g, list) else [g]) if x not in (None, "")]
        g_values = [norm(x) for x in g_raw]
        items = current if is_list else [current]
        # Claude found nothing, Gemini read something: show Gemini's reading, still orange (never safe)
        if g_raw and all(not r.value and r.state == "review" for r in items):
            seen, filled = set(), []
            for raw in g_raw:
                shown = _display(name, raw, norm)
                if shown and shown not in seen:
                    seen.add(shown)
                    extra = id_warning(shown) if name == "national_id" else []
                    filled.append(items[0].model_copy(update={
                        "value": shown, "state": "review",
                        "reasons": [GEMINI_ONLY, *[x for x in items[0].reasons if x != NOT_FOUND], *extra]}))
            if filled:
                updates[name] = filled if is_list else filled[0]
                continue
        out = []
        for r in items:
            if r.state != "review":
                out.append(r)
                continue
            mine = norm(r.value) if r.value else ""
            if mine and mine in g_values and _verifiable(r.reasons):
                keep = [x for x in r.reasons if not (x == "לא אומת על ידי מקור נוסף" or x.startswith("התאמה קרובה"))]
                out.append(r.model_copy(update={"state": "safe", "reasons": keep}))
            elif g_values and mine not in g_values:
                hint = "Gemini קרא " + ", ".join(f"'{_display(name, raw, norm)}'" for raw in g_raw)
                out.append(r.model_copy(update={"reasons": [*r.reasons, hint]}))
            elif mine and not g_values:
                out.append(r.model_copy(update={"reasons": [*r.reasons, "Gemini לא מצא ערך בשדה הזה"]}))
            else:
                out.append(r)
        updates[name] = out if is_list else out[0]

    rd = confidence.received_date
    gd = _gemini_date(gemini)
    if rd.state == "review" and not rd.value and gd and gd[2]:
        from backend.pipeline.normalize import normalize_date_iso
        iso = normalize_date_iso(gd[0], gd[1], gd[2])
        if iso:
            updates["received_date"] = rd.model_copy(update={
                "value": iso, "reasons": [GEMINI_ONLY, *[x for x in rd.reasons if x != NOT_FOUND]]})
    elif rd.state == "review" and rd.value and gd:
        y, m, d = (int(x) for x in rd.value.split("-"))
        if (gd[0], gd[1]) == (d, m) and gd[2] in (None, y) and _verifiable(rd.reasons):
            keep = [x for x in rd.reasons if not (x == "לא אומת על ידי מקור נוסף" or x.startswith("התאמה קרובה"))]
            updates["received_date"] = rd.model_copy(update={"state": "safe", "reasons": keep})
        elif (gd[0], gd[1]) != (d, m) or gd[2] not in (None, y):
            shown = f"{gd[0]:02d}/{gd[1]:02d}" + (f"/{gd[2]}" if gd[2] else "")
            updates["received_date"] = rd.model_copy(update={"reasons": [*rd.reasons, f"Gemini קרא '{shown}'"]})
    return confidence.model_copy(update=updates)


def has_review(confidence) -> bool:
    for f in type(confidence).model_fields:
        v = getattr(confidence, f)
        if any(x.state == "review" for x in (v if isinstance(v, list) else [v])):
            return True
    return False


def verify_with_gemini(confidence, images: list[bytes], gemini_out: Optional[dict] = None):
    """Never raises. gemini_out (dev debug only) receives Gemini's dict when it ran."""
    if not VERIFY:
        if gemini_out is not None:
            gemini_out["_status"] = "כבוי (GEMINI_VERIFY / GEMINI_API_KEY)"
        return confidence
    if not has_review(confidence):
        return confidence
    try:
        gemini = read_fields(images)
    except Exception as exc:
        log.warning("Gemini verification failed: %s", exc)
        if gemini_out is not None:
            gemini_out["_status"] = f"נכשל: {str(exc)[:120]}"
        return confidence
    if gemini_out is not None:
        gemini_out.update(gemini)
    try:
        new = apply(confidence, gemini)
    except Exception as exc:
        log.warning("Gemini comparison failed: %s", exc)
        return confidence
    flipped = sum(a.state == "review" and b.state == "safe"
                  for f in type(confidence).model_fields
                  for a, b in zip(*(x if isinstance(x, list) else [x] for x in (getattr(confidence, f), getattr(new, f)))))
    log.info("Gemini verification flipped %d field(s) from review to safe", flipped)
    return new

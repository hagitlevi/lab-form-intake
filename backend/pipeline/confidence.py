"""Step 5 of the pipeline: normalization, validation and the confidence decision
(CLAUDE.md "Confidence" section).

Per field (per item, for the two list fields), positive checks in order - the first "no"
stops the chain and the field is "review":
  1. value found?
  2. value passes the rules? (ID check digit; date valid & not future, and not
     year_inferred/visit_date; sender in the closed list; commitment format per sender)
  3. (central numbers only) a decoded barcode on this form's pages agrees?
  4. the exact same (normalized) value is read by a second source - an OCR word (also
     checking adjacent words concatenated, for numbers split by spaces) or a barcode for
     central numbers? A same-length one-character-off candidate counts as "review" too
     (near match), with the differing character named.
Only two states ever reach the UI: "safe" or "review", always with a short Hebrew reason.
A missing value is never a guess: state "review", value "", reason "לא נמצא בטופס".

Privacy: only the matched word's/barcode's bbox is kept here - never full OCR text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from rapidfuzz import process as fuzz_process
from rapidfuzz.distance import Hamming
from rapidfuzz.fuzz import ratio as fuzz_ratio

from backend.pipeline import Barcode, Word
from backend.pipeline.models import DateValue, ExtractedForm, FieldValue
from backend.pipeline.normalize import (
    normalize_date_iso,
    normalize_digits,
    normalize_hebrew_name,
    pad_national_id,
)
from pydantic import BaseModel

NOT_FOUND = "לא נמצא בטופס"
SENDER_FUZZY_CUTOFF = 80  # rapidfuzz ratio, 0-100

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SENDERS: list[str] = json.loads((_DATA_DIR / "senders.json").read_text(encoding="utf-8"))
_COMMITMENT_FORMATS: dict[str, str] = {
    k: v
    for k, v in json.loads((_DATA_DIR / "commitment_formats.json").read_text(encoding="utf-8")).items()
    if not k.startswith("_")
}


@dataclass
class PageData:
    """One form's page - the OCR words and decoded barcodes from step 2, pooled across a
    form's pages for cross-source matching. bbox-bearing coordinates are in this page's
    stored (preprocessed) image - already rotation-corrected by step 2."""

    source: str
    page_number: int
    words: list[Word]
    barcodes: list[Barcode] = dc_field(default_factory=list)


class FieldResult(BaseModel):
    value: Optional[str] = None
    state: Literal["safe", "review"]
    reasons: list[str] = []
    bbox: Optional[tuple[int, int, int, int]] = None
    page: Optional[int] = None
    source: Optional[str] = None


class FormConfidence(BaseModel):
    first_name: FieldResult
    last_name: FieldResult
    national_id: FieldResult
    central_numbers: list[FieldResult] = []
    received_date: FieldResult
    sender: FieldResult
    commitment_numbers: list[FieldResult] = []


def _missing() -> FieldResult:
    return FieldResult(value="", state="review", reasons=[NOT_FOUND])


# --- Rules -------------------------------------------------------------------

def valid_israeli_id(padded: str) -> bool:
    if not padded.isdigit() or len(padded) != 9:
        return False
    total = 0
    for i, ch in enumerate(padded):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def sender_fuzzy_match(value: str) -> Optional[str]:
    if value in _SENDERS:
        return value
    match = fuzz_process.extractOne(value, _SENDERS, scorer=fuzz_ratio, score_cutoff=SENDER_FUZZY_CUTOFF)
    return match[0] if match else None


def commitment_format_ok(digits_value: str, sender: Optional[str]) -> bool:
    if not sender or sender not in _COMMITMENT_FORMATS:
        return True  # unknown sender -> no rule
    return bool(re.match(_COMMITMENT_FORMATS[sender], digits_value))


# --- Cross-source matching -----------------------------------------------------

@dataclass
class Match:
    kind: Literal["exact", "near", "none"]
    bbox: Optional[tuple[int, int, int, int]] = None
    page: Optional[int] = None
    source: Optional[str] = None
    reason: str = ""


def _bbox_of(w: Word) -> tuple[int, int, int, int]:
    return (w.left, w.top, w.width, w.height)


def _union_bbox(a: Word, b: Word) -> tuple[int, int, int, int]:
    x0 = min(a.left, b.left)
    y0 = min(a.top, b.top)
    x1 = max(a.left + a.width, b.left + b.width)
    y1 = max(a.top + a.height, b.top + b.height)
    return (x0, y0, x1 - x0, y1 - y0)


def _adjacent(a: Word, b: Word) -> bool:
    """Same line, close together - two OCR words that are really one number split by a
    visual gap (e.g. Maccabi's "2648 00879")."""
    same_line = abs(a.top - b.top) < max(a.height, b.height)
    gap = max(a.left, b.left) - min(a.left + a.width, b.left + b.width)
    return same_line and gap < 2 * max(a.height, b.height)


def find_match(value: str, pages: list[PageData], normalize_fn) -> Match:
    if not value:
        return Match("none")

    candidates: list[tuple[str, Word, int, str]] = []  # (normalized_text, word, page, source)
    for page in pages:
        words = page.words
        for w in words:
            candidates.append((normalize_fn(w.text), w, page.page_number, page.source))
        # adjacent-pair concatenation, both orders, same page only
        for i in range(len(words) - 1):
            a, b = words[i], words[i + 1]
            if not _adjacent(a, b):
                continue
            na, nb = normalize_fn(a.text), normalize_fn(b.text)
            for combined in (na + nb, nb + na):
                if combined == value:
                    return Match("exact", _union_bbox(a, b), page.page_number, page.source)

    for text, w, page_number, source in candidates:
        if text == value:
            return Match("exact", _bbox_of(w), page_number, source)

    same_len = [(t, w, p, s) for (t, w, p, s) in candidates if len(t) == len(value)]
    for text, w, page_number, source in same_len:
        if Hamming.distance(value, text) == 1:
            diff_pos = next(i for i in range(len(value)) if value[i] != text[i])
            reason = (
                f"התאמה קרובה אך לא מדויקת: מקור אחר מראה '{text}' "
                f"(שונה בתו {diff_pos + 1}: '{value[diff_pos]}' מול '{text[diff_pos]}')"
            )
            return Match("near", _bbox_of(w), page_number, source, reason)

    return Match("none")


def find_barcode_match(value: str, pages: list[PageData]) -> Match:
    barcodes = [(b, p.page_number, p.source) for p in pages for b in p.barcodes]
    for b, page_number, source in barcodes:
        if b.value.strip() == value:
            return Match("exact", b.rect, page_number, source)
    if barcodes:
        return Match("none", reason="הברקוד בעמוד מראה מספר אחר")
    return Match("none", reason="אין ברקוד מאשר בעמוד")


# --- Per-field scoring ----------------------------------------------------------

def _score_text_field(raw_value: Optional[str], pages: list[PageData], normalize_fn) -> FieldResult:
    if not raw_value:
        return _missing()
    value = normalize_fn(raw_value)
    match = find_match(value, pages, normalize_fn)
    if match.kind == "exact":
        return FieldResult(value=raw_value, state="safe", bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=raw_value, state="review", reasons=[match.reason], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=raw_value, state="review", reasons=["לא אומת על ידי מקור נוסף"])


def score_national_id(fv: Optional[FieldValue], pages: list[PageData]) -> FieldResult:
    if fv is None or not fv.value:
        return _missing()
    padded = pad_national_id(fv.value)
    if not valid_israeli_id(padded):
        return FieldResult(value=padded, state="review", reasons=["ספרת הביקורת של תעודת הזהות שגויה"])
    match = find_match(padded, pages, normalize_digits)
    if match.kind == "exact":
        return FieldResult(value=padded, state="safe", bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=padded, state="review", reasons=[match.reason], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=padded, state="review", reasons=["לא אומת על ידי מקור נוסף"])


def score_received_date(dv: Optional[DateValue], pages: list[PageData], reference: date) -> FieldResult:
    if dv is None or (dv.day is None and dv.month is None and dv.year is None):
        return _missing()
    iso = normalize_date_iso(dv.day, dv.month, dv.year)
    reasons = []
    if iso is None:
        reasons.append("תאריך לא תקין")
    else:
        try:
            y, m, d = (int(x) for x in iso.split("-"))
            if date(y, m, d) > reference:
                reasons.append("התאריך בעתיד")
        except ValueError:
            reasons.append("תאריך לא תקין")
    if dv.year_inferred:
        reasons.append("השנה הוערכה אוטומטית ולא נקראה מהטופס")
    if dv.date_source == "visit_date":
        reasons.append("התאריך מבוסס על תאריך ביקור, לא על חותמת קבלה")
    if reasons:
        return FieldResult(value=iso or "", state="review", reasons=reasons)

    raw = (dv.raw_text or "").strip()
    match = find_match(normalize_digits(raw), pages, normalize_digits) if raw else Match("none")
    if match.kind == "exact":
        return FieldResult(value=iso, state="safe", bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=iso, state="review", reasons=[match.reason], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=iso, state="review", reasons=["לא אומת על ידי מקור נוסף"])


def score_sender(fv: Optional[FieldValue], pages: list[PageData]) -> FieldResult:
    if fv is None or not fv.value:
        return _missing()
    matched = sender_fuzzy_match(fv.value)
    if matched is None:
        return FieldResult(value=fv.value, state="review", reasons=["השולח אינו ברשימה הסגורה"])
    match = find_match(matched, pages, lambda s: s.strip())
    if match.kind == "exact":
        return FieldResult(value=matched, state="safe", bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=matched, state="review", reasons=[match.reason], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=matched, state="review", reasons=["לא אומת על ידי מקור נוסף"])


def score_central_number(fv: FieldValue, pages: list[PageData]) -> FieldResult:
    if not fv.value:
        return _missing()
    value = normalize_digits(fv.value)
    barcode_match = find_barcode_match(value, pages)
    if barcode_match.kind != "exact":
        return FieldResult(value=value, state="review", reasons=[barcode_match.reason])
    # barcode agrees - still look for an OCR word too, for the bbox (barcode is itself a
    # valid "second source", so this is already past the exact-match gate)
    ocr_match = find_match(value, pages, normalize_digits)
    bbox, page, source = (
        (ocr_match.bbox, ocr_match.page, ocr_match.source)
        if ocr_match.kind == "exact"
        else (barcode_match.bbox, barcode_match.page, barcode_match.source)
    )
    return FieldResult(value=value, state="safe", bbox=bbox, page=page, source=source)


def score_commitment_number(fv: FieldValue, pages: list[PageData], sender: Optional[str]) -> FieldResult:
    if not fv.value:
        return _missing()
    value = normalize_digits(fv.value, keep_leading_letter=True)
    if not commitment_format_ok(value, sender):
        return FieldResult(value=value, state="review", reasons=[f"פורמט לא תואם לשולח ({sender})"])
    match = find_match(value, pages, lambda s: normalize_digits(s, keep_leading_letter=True))
    if match.kind == "exact":
        return FieldResult(value=value, state="safe", bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=value, state="review", reasons=[match.reason], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=value, state="review", reasons=["לא אומת על ידי מקור נוסף"])


def score_form(form: ExtractedForm, pages: list[PageData], reference_date: date) -> FormConfidence:
    sender_result = score_sender(form.sender, pages)
    sender_value = sender_result.value if sender_result.state == "safe" else (form.sender.value if form.sender else None)

    return FormConfidence(
        first_name=_score_text_field(form.first_name.value if form.first_name else None, pages, normalize_hebrew_name),
        last_name=_score_text_field(form.last_name.value if form.last_name else None, pages, normalize_hebrew_name),
        national_id=score_national_id(form.national_id, pages),
        central_numbers=[score_central_number(cn, pages) for cn in form.central_numbers] or [_missing()],
        received_date=score_received_date(form.received_date, pages, reference_date),
        sender=sender_result,
        commitment_numbers=[score_commitment_number(cn, pages, sender_value) for cn in form.commitment_numbers] or [_missing()],
    )

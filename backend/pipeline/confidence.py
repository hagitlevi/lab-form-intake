"""Step 5 of the pipeline: normalization, validation and the confidence decision
(CLAUDE.md "Confidence" section).

Per field (per item, for the two list fields), positive checks in order - the first "no"
stops the chain and the field is "review":
  1. value found?
  2. value passes the rules? (ID check digit; date valid & not future, and not
     year_inferred/visit_date; sender in the closed list; commitment format per sender)
  3. (central numbers only) exactly 6 digits, and no conflicting decoded 6-digit barcode?
     (a missing barcode is NOT a reason; a barcode equal to a commitment number is ignored)
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


def _union_bbox_all(ws: list[Word]) -> tuple[int, int, int, int]:
    x0 = min(w.left for w in ws)
    y0 = min(w.top for w in ws)
    x1 = max(w.left + w.width for w in ws)
    y1 = max(w.top + w.height for w in ws)
    return (x0, y0, x1 - x0, y1 - y0)


MAX_RUN_WORDS = 10
_NUMERIC_WORD = re.compile(r"[\d.\-/:]*\d[\d.\-/:]*")  # digits plus date/number separators, no letters


def _numeric_runs(words: list[Word], min_len: int, max_len: int = MAX_RUN_WORDS):
    """Consecutive OCR words (in OCR order) that are each numeric and pairwise adjacent on the
    same line - a number or a date printed in boxes or split by a stamp's spacing ("31 -08- 2026").
    Yields each run in both orders (OCR word order vs. reading order can differ on RTL pages)."""
    n = len(words)
    for i in range(n):
        if not _NUMERIC_WORD.fullmatch(words[i].text.strip()):
            continue
        run = [words[i]]
        for j in range(i + 1, min(n, i + max_len)):
            w = words[j]
            if not _NUMERIC_WORD.fullmatch(w.text.strip()) or not _adjacent(run[-1], w):
                break
            run.append(w)
            if len(run) >= min_len:
                yield list(run)
                yield list(reversed(run))
        if min_len <= 1:
            yield run[:1]


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

    # numbers split over 3+ words (ID in boxes, stamp spacing) - exact only, never "near"
    for page in pages:
        for run in _numeric_runs(page.words, min_len=3):
            if "".join(normalize_fn(w.text) for w in run) == value:
                return Match("exact", _union_bbox_all(run), page.page_number, page.source)

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


ID_WARNING = "מספר הזהות אינו תקין (ספרת ביקורת או אורך) - נשמר כפי שנקרא"


def id_warning(padded: str) -> list[str]:
    """A national ID that fails the check digit / 9-digit rule is a warning, not a review reason:
    the number is kept exactly as read (never cut or changed)."""
    return [] if valid_israeli_id(padded) else [ID_WARNING]


def score_national_id(fv: Optional[FieldValue], pages: list[PageData]) -> FieldResult:
    if fv is None or not fv.value:
        return _missing()
    padded = pad_national_id(fv.value)
    warn = id_warning(padded)
    match = find_match(padded, pages, normalize_digits)
    if match.kind == "exact":
        return FieldResult(value=padded, state="safe", reasons=warn, bbox=match.bbox, page=match.page, source=match.source)
    if match.kind == "near":
        return FieldResult(value=padded, state="review", reasons=[match.reason, *warn], bbox=match.bbox, page=match.page, source=match.source)
    return FieldResult(value=padded, state="review", reasons=["לא אומת על ידי מקור נוסף", *warn])


_SEP = r"\s*[.\-/]\s*"  # a real separator, spaces around it allowed ("31 -08- 2026")
_DMY = re.compile(rf"\D{{0,2}}(\d{{1,2}}){_SEP}(\d{{1,2}})(?:{_SEP}(\d{{4}}|\d{{2}}))?\D{{0,2}}")
# spaces only (boxed digits "31 08 2026") - only with a 4-digit year, so "1 6" in a table never reads as a date
_DMY_SPACES = re.compile(r"(\d{1,2})\s+(\d{1,2})\s+(\d{4})")
_YMD = re.compile(rf"\D{{0,2}}(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})\D{{0,2}}")


def _parse_date_text(text: str) -> Optional[tuple[int, int, Optional[int]]]:
    """'31-08-2026', '25.8.26', '17/6', '31 -08- 2026', '31 08 2026', '2026-08-31' -> (day, month, year|None)."""
    text = text.strip()
    m = _YMD.fullmatch(text)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = _DMY.fullmatch(text) or _DMY_SPACES.fullmatch(text)
    if m:
        y = m.group(3)
        year = None if y is None else (2000 + int(y) if len(y) == 2 else int(y))
        return int(m.group(1)), int(m.group(2)), year
    return None


def _misread_year(ocr_year: int, year: int) -> bool:
    """OCR year that cannot be a real date year (e.g. a stamp's 2026 read as 2726) and is one
    character away from the extracted year - an OCR misread, not a different date."""
    a, b = str(ocr_year), str(year)
    return not (1900 <= ocr_year <= 2100) and len(a) == len(b) and Hamming.distance(a, b) == 1


def find_date_match(day: int, month: int, year: int, pages: list[PageData], require_year: bool = False) -> Match:
    """An OCR word, or a run of adjacent numeric words, that reads as the same day and month,
    and the same year when the OCR side has one (a stamp/handwriting with no year still counts)."""
    for page in pages:
        for run in _numeric_runs(page.words, min_len=1):
            parsed = _parse_date_text(" ".join(w.text for w in run))
            if parsed is None:
                continue
            d, m, y = parsed
            if d == day and m == month and (y == year or (not require_year and (y is None or _misread_year(y, year)))):
                return Match("exact", _union_bbox_all(run), page.page_number, page.source)
    return Match("none")


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
    if dv.year_inferred and iso is not None:
        # the AI saw no year; if OCR shows this exact day/month WITH the inferred year, the year was read after all
        yy, mm, dd = (int(x) for x in iso.split("-"))
        if find_date_match(dd, mm, yy, pages, require_year=True).kind != "exact":
            reasons.append("השנה הוערכה אוטומטית ולא נקראה מהטופס")
    if dv.date_source == "visit_date":
        reasons.append("התאריך מבוסס על תאריך ביקור, לא על חותמת קבלה")
    if reasons:
        return FieldResult(value=iso or "", state="review", reasons=reasons)

    y, m, d = (int(x) for x in iso.split("-"))
    match = find_date_match(d, m, y, pages)
    if match.kind == "none":  # fallback: the old raw-digits comparison (keeps its near-match reason)
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


def score_central_number(fv: FieldValue, pages: list[PageData], commitment_values: list[str] = (),
                         central_values: list[str] = ()) -> FieldResult:
    """No value -> review; not exactly 6 digits -> review; a decoded 6-digit barcode equal to
    the value -> safe; a different 6-digit barcode -> review, unless that barcode is one of
    the form's commitment numbers (Bnei Zion) and then ignored; no usable barcode (the common
    case) -> the same OCR second-source check as every other field."""
    if not fv.value:
        return _missing()
    value = normalize_digits(fv.value)
    if not re.fullmatch(r"\d{6}", value):
        return FieldResult(value=value, state="review", reasons=["מספר מרכזי חייב להכיל 6 ספרות"])

    # barcodes that belong to another field of this form are not a conflict: the form's commitment
    # numbers (Bnei Zion) and its OTHER central numbers (one sticker+barcode per tube, e.g. 937309/937310/937311)
    commitments = {normalize_digits(c) for c in commitment_values} | ({normalize_digits(c) for c in central_values} - {value})
    barcodes = [(b, p.page_number, p.source) for p in pages for b in p.barcodes]
    six = [(b, n, src) for b, n, src in barcodes if re.fullmatch(r"\d{6}", b.value.strip()) and b.value.strip() not in commitments]

    ocr_match = find_match(value, pages, normalize_digits)
    for b, n, src in six:
        if b.value.strip() == value:
            # barcode is the second source; keep the OCR word's bbox when there is one
            if ocr_match.kind == "exact":
                return FieldResult(value=value, state="safe", bbox=ocr_match.bbox, page=ocr_match.page, source=ocr_match.source)
            return FieldResult(value=value, state="safe", bbox=b.rect, page=n, source=src)
    if six:
        return FieldResult(value=value, state="review", reasons=["הברקוד בעמוד לא תואם"])

    if ocr_match.kind == "exact":
        return FieldResult(value=value, state="safe", bbox=ocr_match.bbox, page=ocr_match.page, source=ocr_match.source)
    if ocr_match.kind == "near":
        return FieldResult(value=value, state="review", reasons=[ocr_match.reason], bbox=ocr_match.bbox, page=ocr_match.page, source=ocr_match.source)
    return FieldResult(value=value, state="review", reasons=["לא אומת על ידי מקור נוסף"])


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
        central_numbers=[score_central_number(cn, pages, [c.value for c in form.commitment_numbers if c.value],
                                              [c.value for c in form.central_numbers if c.value]) for cn in form.central_numbers] or [_missing()],
        received_date=score_received_date(form.received_date, pages, reference_date),
        sender=sender_result,
        commitment_numbers=[score_commitment_number(cn, pages, sender_value) for cn in form.commitment_numbers] or [_missing()],
    )

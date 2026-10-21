"""Step 5 helpers: normalize raw extracted/OCR text into comparable values.

Normalization never changes what gets stored as the field's value - it only produces the
form used for rule-checking and cross-source matching.
"""

from __future__ import annotations

import re
from typing import Optional

_FINAL_LETTERS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})


def normalize_digits(value: str, keep_leading_letter: bool = False) -> str:
    """Strip everything but digits. If keep_leading_letter, a single leading A-Za-z
    (e.g. Meuhedet's "X132767") is kept in front of the digits."""
    value = value.strip()
    letter = ""
    if keep_leading_letter and value and value[0].isalpha() and value[0].isascii():
        letter = value[0].upper()
        value = value[1:]
    return letter + re.sub(r"\D", "", value)


def pad_national_id(value: str) -> str:
    digits = normalize_digits(value)
    return digits.zfill(9) if digits else digits


def normalize_date_iso(day: Optional[int], month: Optional[int], year: Optional[int]) -> Optional[str]:
    """(day, month, year) -> "YYYY-MM-DD". A 2-digit year is read as 20xx. None if
    day/month/year is missing or out of range."""
    if day is None or month is None or year is None:
        return None
    if year < 100:
        year += 2000
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_hebrew_name(value: str) -> str:
    """Final letters -> regular form, for NAME MATCHING ONLY - never change the stored value."""
    return value.strip().translate(_FINAL_LETTERS)

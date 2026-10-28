"""DEV ONLY - DEBUG_SOURCES=1. For every field still in "review", add one reason line that starts
with "[debug]" and says what each source read for it:

    [debug] Claude: 31.8 | OCR: 31/08/2026 ≈ | Gemini: 31/08/2026

OCR/Google show the closest word (or run of adjacent words) on the form's pages to Claude's value:
✓ = verified match, ≈ = similar but not a match, ✗ = nothing close. The OCR engines do not know
which word is which field, so this is "closest text on the page", not "what it read in that field".

Privacy: this stores small OCR snippets in fields.reasons. Never turn it on in the cloud deployment.
The frontend shows "[debug]" reasons as a grey line and never counts them as review reasons.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

from dotenv import load_dotenv
from rapidfuzz.fuzz import ratio

from backend.pipeline.confidence import _numeric_runs, _parse_date_text, find_date_match, find_match
from backend.pipeline.normalize import normalize_digits, normalize_hebrew_name, pad_national_id

load_dotenv()
DEBUG = os.environ.get("DEBUG_SOURCES", "").lower() in ("1", "true", "yes")
log = logging.getLogger(__name__)
if DEBUG:
    log.warning("DEBUG_SOURCES is ON - review fields get a [debug] line with OCR snippets (dev only)")
PREFIX = "[debug]"
_MIN_SIMILARITY = 50  # below this the closest text is shown as "nothing close"


def _candidates(words, numeric: bool):
    """(display text, words) for single words, adjacent pairs and numeric runs."""
    out = [(w.text, [w]) for w in words]
    if numeric:
        for run in _numeric_runs(words, min_len=2):
            out.append((" ".join(w.text for w in run), run))
    else:
        for a, b in zip(words, words[1:]):
            out.append((f"{a.text} {b.text}", [a, b]))
    return out


def _closest(value: str, pages, kind: str, date_parts=None) -> str:
    if not pages or not any(p.words for p in pages):
        return "אין טקסט"
    if kind == "date" and date_parts:
        d, m, y = date_parts
        mark = "✓" if find_date_match(d, m, y, pages).kind == "exact" else "≈"
        best, best_score = None, 0
        for p in pages:
            for text, run in _candidates(p.words, numeric=True):
                parsed = _parse_date_text(text)
                if parsed is None:
                    continue
                score = ratio(f"{parsed[0]}{parsed[1]}", f"{d}{m}") + (10 if parsed[2] == y else 0)
                if score > best_score:
                    best, best_score = text, score
        if best is None:
            return "✗ אין תאריך"
        return f"{best} {mark}"

    if kind == "id":
        from backend.pipeline.confidence import id_key
        norm, target = id_key, id_key(value)
    elif kind == "digits":
        norm, target = normalize_digits, normalize_digits(value)
    else:
        norm, target = normalize_hebrew_name, normalize_hebrew_name(value)
    if not target:
        return "-"
    match = find_match(target, pages, norm)
    best, best_score = None, 0
    for p in pages:
        for text, _ in _candidates(p.words, numeric=kind in ("digits", "id")):
            n = norm(text)
            if not n:
                continue
            score = ratio(n, target) if kind not in ("digits", "id") else ratio(n.lstrip("0"), target.lstrip("0"))
            if score > best_score:
                best, best_score = text, score
    if match.kind == "exact":
        return f"{best} ✓" if best_score == 100 else f"{target} ✓"
    if best is None or best_score < _MIN_SIMILARITY:
        return "✗ לא נמצא"
    return f"{best} ≈"


_KINDS = {"first_name": "name", "last_name": "name", "sender": "name", "national_id": "id",
          "received_date": "date", "central_numbers": "digits", "commitment_numbers": "digits"}


def _gemini_text(name: str, gemini: dict | None) -> str:
    if not gemini:
        return "לא רץ"
    if "_status" in gemini:
        return gemini["_status"]
    from backend.pipeline.gemini_verify import sanitize
    raw = gemini.get(name)
    gemini = sanitize(gemini)
    if raw not in (None, "", []) and gemini.get(name) in (None, []):
        return f"null (נפסל: {raw})"
    v = gemini.get(name)
    if name == "received_date" and isinstance(v, dict):
        parts = [v.get("day"), v.get("month"), v.get("year")]
        return "/".join(str(x) for x in parts if x is not None) or "null"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "[]"
    return "null" if v in (None, "") else str(v)


def annotate(form, confidence, pages, gemini: dict | None):
    """Return confidence with a "[debug]" reason added to every review field. Never raises."""
    if not DEBUG:
        return confidence
    try:
        updates = {}
        for name, kind in _KINDS.items():
            results = getattr(confidence, name)
            sources = getattr(form, name)
            is_list = isinstance(results, list)
            res_list = results if is_list else [results]
            src_list = (sources or []) if is_list else [sources]
            new_list = []
            for i, r in enumerate(res_list):
                fv = src_list[i] if i < len(src_list) else None
                if r.state != "review":
                    new_list.append(r)
                    continue
                if not r.value or any(x.startswith("נקרא רק על ידי Gemini") for x in r.reasons):
                    # Claude found nothing (the value, if any, is Gemini's): show what Gemini read
                    line = f"{PREFIX} Claude: (ריק) | Gemini: {_gemini_text(name, gemini)}"
                    new_list.append(r.model_copy(update={"reasons": [*r.reasons, line]}))
                    continue
                claude = (getattr(fv, "raw_text", None) or getattr(fv, "value", None) or r.value) if fv else r.value
                date_parts = None
                if kind == "date":
                    y, m, d = (int(x) for x in r.value.split("-"))
                    date_parts = (d, m, y)
                ocr = _closest(r.value, pages, kind, date_parts)
                line = f"{PREFIX} Claude: {claude} | OCR: {ocr} | Gemini: {_gemini_text(name, gemini)}"
                new_list.append(r.model_copy(update={"reasons": [*r.reasons, line]}))
            updates[name] = new_list if is_list else new_list[0]
        return confidence.model_copy(update=updates)
    except Exception:  # debug must never break a form - but say why it is missing
        log.exception("DEBUG_SOURCES: could not build the debug line")
        return confidence

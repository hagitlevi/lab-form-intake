"""PATCH/merge/mark-separate/approve logic for forms - reuses the step-5 rule functions
(backend.pipeline.confidence) for validation, never re-implements them."""

from __future__ import annotations

from datetime import date

import cv2
import numpy as np

from backend.batch_runner import persist_form
from backend.grouping import FormGroup
from backend.pipeline import read_barcodes, run_ocr
from backend.pipeline.confidence import PageData, commitment_format_ok, score_form, sender_fuzzy_match, valid_israeli_id
from backend.pipeline.extract import extract_form
from backend.pipeline.normalize import normalize_digits, pad_national_id

ALLOWED_FIELDS = {
    "first_name": "scalar", "last_name": "scalar", "national_id": "scalar",
    "received_date": "scalar", "sender": "scalar",
    "central_numbers": "list", "commitment_numbers": "list",
}
_SINGULAR = {"central_numbers": "central_number", "commitment_numbers": "commitment_number"}
MANUAL_OVERRIDE_REASON = "אושר ידנית למרות שגיאת אימות"


def _normalize_scalar(name: str, value, sender_value) -> tuple[str | None, list[str]]:
    if value is None:
        return None, []
    if name == "national_id":
        padded = pad_national_id(value)
        return padded, [] if valid_israeli_id(padded) else ["ספרת הביקורת של תעודת הזהות שגויה"]
    if name == "sender":
        matched = sender_fuzzy_match(value)
        return (matched or value), ([] if matched else ["השולח אינו ברשימה הסגורה"])
    if name == "received_date":
        try:
            y, m, d = (int(x) for x in str(value).split("-"))
            iso_date = date(y, m, d)
        except (ValueError, TypeError):
            return str(value), ["תאריך לא תקין"]
        return value, (["התאריך בעתיד"] if iso_date > date.today() else [])
    return value, []


def _normalize_list_item(singular: str, value, sender_value) -> tuple[str | None, list[str]]:
    if value is None:
        return None, []
    if singular == "commitment_number":
        digits = normalize_digits(value, keep_leading_letter=True)
        ok = commitment_format_ok(digits, sender_value)
        return digits, ([] if ok else [f"פורמט לא תואם לשולח ({sender_value})"])
    if singular == "central_number":
        return normalize_digits(value), []
    return value, []


_UPSERT_FIELD = """
INSERT INTO fields (form_id, name, item_index, value, state, reasons, bbox, page_id)
VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL)
ON CONFLICT (form_id, name, item_index)
DO UPDATE SET value = EXCLUDED.value, state = EXCLUDED.state, reasons = EXCLUDED.reasons,
              bbox = NULL, page_id = NULL, updated_at = now()
RETURNING id
"""


def _resolve_state(norm_value: str | None, reasons: list[str], old: dict | None, has_prior_patch: bool) -> tuple[str, list[str]]:
    """A PATCHed value that fails validation does not become 'corrected': it is saved with
    state 'review' and the validation message as the reason, so the technician sees it
    under the cell. Submitting that exact same value again via a SECOND PATCH (same field,
    same validation message already on record from a PRIOR patch - not merely coinciding
    with the AI's original value) is a manual override: accepted as 'corrected' with a
    fixed reason, instead of the validation message. has_prior_patch distinguishes "this
    field was already PATCHed before" from a field still sitting at its original step-5
    state, so clicking into an AI-flagged cell and confirming it unchanged on the very
    first PATCH does NOT skip straight to an override."""
    if not reasons:
        return "corrected", reasons
    if old and has_prior_patch and old["value"] == norm_value:
        if old["state"] == "review" and old["reasons"] == reasons:
            return "corrected", [MANUAL_OVERRIDE_REASON]
        if old["state"] == "corrected" and old["reasons"] == [MANUAL_OVERRIDE_REASON]:
            return "corrected", [MANUAL_OVERRIDE_REASON]  # already overridden - stays overridden
    return "review", reasons


def _has_prior_patch(conn, field_id: int | None) -> bool:
    if field_id is None:
        return False
    return conn.execute("SELECT 1 FROM audit_log WHERE field_id = %s LIMIT 1", (field_id,)).fetchone() is not None


def apply_patch(conn, form_id: int, patch: dict) -> dict:
    """Whitelisted field correction: validates with the step-5 rules. A value that passes
    becomes 'corrected' (a human confirmation overrides the machine state and never blocks
    batch approval); a value that fails stays 'review' with the validation message, unless
    the same failing value is resubmitted (see _resolve_state), which is a manual override.
    value=None means confirmed empty (always accepted). All writes (fields + audit_log)
    commit together as one transaction."""
    unknown = set(patch) - set(ALLOWED_FIELDS)
    if unknown:
        raise ValueError(f"unknown field(s): {sorted(unknown)}")

    sender_row = conn.execute(
        "SELECT value FROM fields WHERE form_id = %s AND name = 'sender' AND item_index = 0", (form_id,)
    ).fetchone()
    current_sender = patch.get("sender", sender_row[0] if sender_row else None)

    updated: dict = {}
    for name, value in patch.items():
        kind = ALLOWED_FIELDS[name]
        if kind == "scalar":
            norm_value, reasons = _normalize_scalar(name, value, current_sender)
            old_row = conn.execute(
                "SELECT id, value, state, reasons FROM fields WHERE form_id = %s AND name = %s AND item_index = 0",
                (form_id, name),
            ).fetchone()
            old = {"id": old_row[0], "value": old_row[1], "state": old_row[2], "reasons": old_row[3]} if old_row else None
            has_prior_patch = _has_prior_patch(conn, old["id"] if old else None)
            state, final_reasons = _resolve_state(norm_value, reasons, old, has_prior_patch)
            field_id = conn.execute(_UPSERT_FIELD, (form_id, name, 0, norm_value, state, final_reasons)).fetchone()[0]
            conn.execute(
                "INSERT INTO audit_log (form_id, field_id, old_value, new_value) VALUES (%s, %s, %s, %s)",
                (form_id, field_id, old["value"] if old else None, norm_value),
            )
            updated[name] = {"value": norm_value, "state": state, "reasons": final_reasons}
        else:
            singular = _SINGULAR[name]
            old_rows = conn.execute(
                "SELECT item_index, id, value, state, reasons FROM fields WHERE form_id = %s AND name = %s",
                (form_id, singular),
            ).fetchall()
            old_by_index = {r[0]: {"id": r[1], "value": r[2], "state": r[3], "reasons": r[4]} for r in old_rows}
            if isinstance(value, dict):  # {"<item_index>": value}: touch only these items
                items = None
                targets = [(int(k), v) for k, v in value.items()]
            else:
                items = value or []
                targets = list(enumerate(items))
            item_results = {}
            for i, item_value in targets:
                norm_value, reasons = _normalize_list_item(singular, item_value, current_sender)
                old = old_by_index.get(i)
                has_prior_patch = _has_prior_patch(conn, old["id"] if old else None)
                state, final_reasons = _resolve_state(norm_value, reasons, old, has_prior_patch)
                field_id = conn.execute(_UPSERT_FIELD, (form_id, singular, i, norm_value, state, final_reasons)).fetchone()[0]
                conn.execute(
                    "INSERT INTO audit_log (form_id, field_id, old_value, new_value) VALUES (%s, %s, %s, %s)",
                    (form_id, field_id, old["value"] if old else None, norm_value),
                )
                item_results[i] = {"value": norm_value, "state": state, "reasons": final_reasons}
            if items is not None:
                conn.execute(
                    "DELETE FROM fields WHERE form_id = %s AND name = %s AND item_index >= %s",
                    (form_id, singular, len(items)),
                )
            updated[name] = item_results

    conn.commit()
    return updated


def get_form(conn, form_id: int) -> dict | None:
    """One form for the single-form screen: its 7 fields (one entry per item) with state,
    reasons, bbox [x, y, w, h] and the page_id the bbox belongs to. bbox is in the pixel
    space of GET /api/pages/{page_id}/image. No OCR text."""
    form = conn.execute(
        "SELECT id, batch_id, status, probable_continuation, split_by_ai, error FROM forms WHERE id = %s", (form_id,)
    ).fetchone()
    if form is None:
        return None
    page_ids = [r[0] for r in conn.execute(
        """
        SELECT p.id FROM pages p JOIN files fi ON fi.id = p.file_id
        WHERE p.form_id = %s ORDER BY fi.position, p.page_index
        """,
        (form_id,),
    ).fetchall()]
    fields = [
        {"name": r[0], "item_index": r[1], "value": r[2], "state": r[3], "reasons": r[4], "bbox": r[5], "page_id": r[6]}
        for r in conn.execute(
            "SELECT name, item_index, value, state, reasons, bbox, page_id FROM fields WHERE form_id = %s ORDER BY name, item_index",
            (form_id,),
        ).fetchall()
    ]
    return {
        "id": form[0], "batch_id": form[1], "status": form[2], "probable_continuation": form[3],
        "split_by_ai": form[4], "error": form[5], "page_ids": page_ids, "fields": fields,
    }


def mark_separate(conn, form_id: int) -> None:
    conn.execute("UPDATE forms SET probable_continuation = false, updated_at = now() WHERE id = %s", (form_id,))
    conn.commit()


def _ordered_pages(conn, batch_id: int) -> list[tuple]:
    """(page_id, form_id, image_bytes) for every page of the batch, in canonical reading order."""
    return conn.execute(
        """
        SELECT p.id, p.form_id, p.image
        FROM pages p JOIN files fi ON fi.id = p.file_id
        WHERE p.batch_id = %s
        ORDER BY fi.position, p.page_index
        """,
        (batch_id,),
    ).fetchall()


def merge_with_previous(conn, form_id: int) -> int:
    """Merge this form's pages with the immediately preceding form's pages (in batch page
    order), re-extract with ONE AI call, rerun step 5, and replace both forms with one.

    ponytail: re-OCR here is a single pass (not scan_page's dual-pass merge) - the stored
    image is already oriented/preprocessed, and this is a rare correction path, not the
    bulk ingestion path the dual-pass tuning targeted. Upgrade if merged forms show
    materially worse field recovery than fresh ingestion.
    """
    batch_row = conn.execute("SELECT batch_id FROM forms WHERE id = %s", (form_id,)).fetchone()
    if batch_row is None:
        raise ValueError("form not found")
    batch_id = batch_row[0]

    ordered = _ordered_pages(conn, batch_id)
    first_idx = next((i for i, (_, fid, _) in enumerate(ordered) if fid == form_id), None)
    if first_idx is None or first_idx == 0:
        raise ValueError("no previous form to merge with")
    _, prev_form_id, _ = ordered[first_idx - 1]
    if prev_form_id is None or prev_form_id == form_id:
        raise ValueError("no previous form to merge with")

    merged_rows = [row for row in ordered if row[1] in (form_id, prev_form_id)]
    merged_page_ids = [row[0] for row in merged_rows]

    images, pages_data = [], []
    for page_id, _, image_bytes in merged_rows:
        color = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        images.append(color)
        gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
        words = run_ocr(gray)
        barcodes = read_barcodes(color, gray, words)
        pages_data.append(PageData(source=f"page:{page_id}", page_number=0, words=words, barcodes=barcodes))

    forms = extract_form(images, group_flagged=True)
    today = date.today()
    page_id_lookup = {(pd.source, pd.page_number): pid for pd, pid in zip(pages_data, merged_page_ids)}

    conn.execute("DELETE FROM forms WHERE id = ANY(%s)", ([form_id, prev_form_id],))

    new_form_id = None
    for form in forms:
        form_pages = [pages_data[i] for i in form.page_indexes]
        confidence = score_form(form, form_pages, today)
        form_page_ids = [merged_page_ids[i] for i in form.page_indexes]
        group = FormGroup(pages=[], flagged=True, reasons=["merged_with_previous"])
        new_form_id = persist_form(conn, batch_id, group, page_id_lookup, form_page_ids, form, confidence)
    conn.commit()
    return new_form_id


def approve_form(conn, form_id: int) -> list[dict]:
    """Same rule as approve_batch, scoped to one form: still-in-review fields are returned
    (caller should 409) and nothing changes; otherwise the form becomes 'approved'."""
    reviewing = conn.execute(
        "SELECT form_id, name, item_index, reasons FROM fields WHERE form_id = %s AND state = 'review'", (form_id,)
    ).fetchall()
    if reviewing:
        return [{"form_id": r[0], "field": r[1], "item_index": r[2], "reasons": r[3]} for r in reviewing]
    conn.execute("UPDATE forms SET status = 'approved', updated_at = now() WHERE id = %s", (form_id,))
    conn.commit()
    return []


def approve_batch(conn, batch_id: int) -> list[dict]:
    """Returns the still-in-review fields (caller should 409) without changing anything;
    if none, approves every form and the batch."""
    reviewing = conn.execute(
        """
        SELECT fl.form_id, fl.name, fl.item_index, fl.reasons
        FROM fields fl JOIN forms f ON f.id = fl.form_id
        WHERE f.batch_id = %s AND fl.state = 'review'
        """,
        (batch_id,),
    ).fetchall()
    if reviewing:
        return [{"form_id": r[0], "field": r[1], "item_index": r[2], "reasons": r[3]} for r in reviewing]
    conn.execute("UPDATE forms SET status = 'approved', updated_at = now() WHERE batch_id = %s", (batch_id,))
    conn.execute("UPDATE batches SET status = 'approved', updated_at = now() WHERE id = %s", (batch_id,))
    conn.commit()
    return []

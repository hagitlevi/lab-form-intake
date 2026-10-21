"""Wires pipeline steps 1-5 (already implemented in backend.pipeline / backend.grouping)
into the database. This module does no OCR/grouping/extraction/confidence logic itself -
it only orchestrates those existing modules and persists their output.

A failing page or form is recorded (pages.error / forms.error) and does not stop the
batch; only a failure in the orchestration itself (not scoped to one page/form) marks the
whole batch 'interrupted'.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import cv2
from psycopg.types.json import Json

from backend.config import AI_WORKERS, LOCAL_WORKERS
from backend.db import pool
from backend.grouping import FormGroup, group_pages, page_facts_from_ocr
from backend.pipeline import split_files
from backend.pipeline.confidence import FieldResult, FormConfidence, PageData, _missing, score_form
from backend.pipeline.extract import extract_form
from backend.pipeline.models import ExtractedForm
from backend.pipeline.scan import scan_pages

log = logging.getLogger(__name__)

JPEG_QUALITY = 90


def _encode_image(image) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def _empty_confidence() -> FormConfidence:
    return FormConfidence(
        first_name=_missing(), last_name=_missing(), national_id=_missing(),
        received_date=_missing(), sender=_missing(), central_numbers=[], commitment_numbers=[],
    )


def create_batch(conn, filenames: list[str]) -> tuple[int, dict[int, int]]:
    """Insert the batch and its files rows. Returns (batch_id, {file_position: file_id})."""
    batch_id = conn.execute(
        "INSERT INTO batches (status) VALUES ('processing') RETURNING id"
    ).fetchone()[0]
    file_id_by_position = {}
    for position, filename in enumerate(filenames):
        file_id = conn.execute(
            "INSERT INTO files (batch_id, filename, position) VALUES (%s, %s, %s) RETURNING id",
            (batch_id, filename, position),
        ).fetchone()[0]
        file_id_by_position[position] = file_id
    conn.commit()
    return batch_id, file_id_by_position


def _field_rows(confidence: FormConfidence) -> list[tuple[str, int, FieldResult]]:
    rows = [
        ("first_name", 0, confidence.first_name),
        ("last_name", 0, confidence.last_name),
        ("national_id", 0, confidence.national_id),
        ("received_date", 0, confidence.received_date),
        ("sender", 0, confidence.sender),
    ]
    rows += [("central_number", i, c) for i, c in enumerate(confidence.central_numbers)]
    rows += [("commitment_number", i, c) for i, c in enumerate(confidence.commitment_numbers)]
    return rows


def persist_form(
    conn, batch_id: int, group: FormGroup, page_ids: dict[tuple[str, int], int],
    form_page_ids: list[int], form: ExtractedForm, confidence: FormConfidence,
    error: str | None = None,
) -> int:
    """Insert one forms row + its fields rows; assign form_page_ids (already resolved by
    the caller to this specific ExtractedForm's pages, since a group may split into several)."""
    form_id = conn.execute(
        """
        INSERT INTO forms (batch_id, flagged, flag_reasons, probable_continuation, split_by_ai, error)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (batch_id, group.flagged, group.reasons, form.probable_continuation, form.split_by_ai, error),
    ).fetchone()[0]

    if form_page_ids:
        conn.execute("UPDATE pages SET form_id = %s WHERE id = ANY(%s)", (form_id, form_page_ids))

    for name, item_index, result in _field_rows(confidence):
        bbox = list(result.bbox) if result.bbox else None
        page_id = page_ids.get((result.source, result.page)) if result.source and result.page else None
        conn.execute(
            """
            INSERT INTO fields (form_id, name, item_index, value, state, reasons, bbox, page_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (form_id, name, item_index, result.value, result.state, result.reasons, bbox, page_id),
        )
    conn.commit()
    return form_id


def _update_batch(conn, batch_id: int, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(
        f"UPDATE batches SET {set_clause}, updated_at = now() WHERE id = %s",
        (*fields.values(), batch_id),
    )
    conn.commit()


def process_batch(batch_id: int, file_paths: list[str], file_id_by_position: dict[int, int]) -> None:
    """The whole pipeline for one batch: split -> scan (LOCAL_WORKERS) -> group ->
    extract+confidence per form group (AI_WORKERS), persisting as each group finishes."""
    try:
        _run_batch(batch_id, file_paths, file_id_by_position)
    except Exception as exc:  # orchestration-level failure, not a single page/form
        log.exception("batch %s failed", batch_id)
        with pool.connection() as conn:
            _update_batch(conn, batch_id, status="interrupted", error=f"{type(exc).__name__}: {exc}")


def _run_batch(batch_id: int, file_paths: list[str], file_id_by_position: dict[int, int]) -> None:
    pages, skipped = split_files(file_paths)

    skipped_pages = [{"filename": Path(s["source"]).name, "page_index": s["page_number"]} for s in skipped]

    page_ids: dict[tuple[str, int], int] = {}
    with pool.connection() as conn:
        _update_batch(conn, batch_id, total_pages=len(pages), skipped_pages=Json(skipped_pages))
        for position, path in enumerate(file_paths):
            file_id = file_id_by_position[position]
            for page in pages:
                if page.source != path:
                    continue
                page_bytes = _encode_image(page.image)
                page_id = conn.execute(
                    "INSERT INTO pages (batch_id, file_id, page_index, image) VALUES (%s, %s, %s, %s) RETURNING id",
                    (batch_id, file_id, page.page_number, page_bytes),
                ).fetchone()[0]
                page_ids[(path, page.page_number)] = page_id
        conn.commit()

    pages_done = 0

    def on_progress(page, elapsed: float, from_cache: bool) -> None:
        nonlocal pages_done
        pages_done += 1
        with pool.connection() as conn:
            _update_batch(conn, batch_id, pages_done=pages_done)

    scans, _ = scan_pages(pages, max_workers=LOCAL_WORKERS, on_progress=on_progress)

    with pool.connection() as conn:
        for page, scan in zip(pages, scans):
            page_id = page_ids[(page.source, page.page_number)]
            if scan.error:
                conn.execute("UPDATE pages SET error = %s WHERE id = %s", (scan.error, page_id))
            else:
                # field bboxes are in the oriented/deskewed space of scan.color, so store that
                conn.execute("UPDATE pages SET image = %s WHERE id = %s", (_encode_image(scan.color), page_id))
        conn.commit()

    facts = []
    for page, scan in zip(pages, scans):
        main_words = [w for w in scan.words if w.rotation == 0]
        crop_words = [w for w in scan.words if w.rotation != 0]
        facts.append(page_facts_from_ocr(page, main_words, scan.barcodes, crop_words))
    groups = group_pages(facts)

    with pool.connection() as conn:
        _update_batch(conn, batch_id, total_forms=len(groups))

    scan_by_key = {(s.source, s.page_number): s for s in scans}
    today = date.today()

    def process_group(group: FormGroup) -> None:
        group_pagedata = [
            PageData(
                source=pf.source,
                page_number=pf.page_number,
                words=[w for w in scan_by_key[(pf.source, pf.page_number)].words if w.rotation == 0],
                barcodes=scan_by_key[(pf.source, pf.page_number)].barcodes,
            )
            for pf in group.pages
        ]
        group_images = [scan_by_key[(pf.source, pf.page_number)].color for pf in group.pages]
        group_page_ids = [page_ids[(pf.source, pf.page_number)] for pf in group.pages]

        try:
            forms = extract_form(group_images, upload_date=today, group_flagged=group.flagged)
        except Exception as exc:  # extract_form() itself shouldn't raise, but be defensive
            forms = [ExtractedForm(page_indexes=list(range(len(group.pages))), notes=f"extraction_error: {exc}")]

        for form in forms:
            form_error = None
            try:
                form_pages = [group_pagedata[i] for i in form.page_indexes]
                confidence = score_form(form, form_pages, today)
                form_page_ids = [group_page_ids[i] for i in form.page_indexes]
            except Exception as exc:
                log.exception("scoring failed for a form in batch %s", batch_id)
                confidence = _empty_confidence()
                form_page_ids = group_page_ids
                form_error = f"{type(exc).__name__}: {exc}"

            with pool.connection() as conn:
                persist_form(conn, batch_id, group, page_ids, form_page_ids, form, confidence, error=form_error)

    forms_done = 0
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as executor:
        futures = {executor.submit(process_group, g): g for g in groups}
        for fut in as_completed(futures):
            group = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.exception("group processing failed in batch %s", batch_id)
                with pool.connection() as conn:
                    persist_form(
                        conn, batch_id, group, page_ids,
                        [page_ids[(pf.source, pf.page_number)] for pf in group.pages],
                        ExtractedForm(page_indexes=list(range(len(group.pages)))),
                        _empty_confidence(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
            forms_done += 1
            with pool.connection() as conn:
                _update_batch(conn, batch_id, forms_done=forms_done)

    with pool.connection() as conn:
        _update_batch(conn, batch_id, status="done")

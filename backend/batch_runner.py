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
from types import SimpleNamespace

import cv2
import pymupdf
from psycopg.types.json import Json

from backend.config import AI_WORKERS, LOCAL_WORKERS
from backend.db import pool
from backend.grouping import FormGroup, group_pages, page_facts_from_ocr
from backend.pipeline import load_page
from backend.pipeline.confidence import FieldResult, FormConfidence, PageData, _missing, score_form
from backend.pipeline.extract import extract_form
from backend.pipeline.gemini_verify import verify_with_gemini
from backend.pipeline import debug_sources
from backend.pipeline.models import ExtractedForm
from backend.pipeline.scan import _failed_scan, scan_page

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


def _batch_exists(batch_id: int) -> bool:
    """False once the user deleted the batch (DELETE /api/batches/{id}) mid-processing."""
    with pool.connection() as conn:
        return conn.execute("SELECT 1 FROM batches WHERE id = %s", (batch_id,)).fetchone() is not None


def process_batch(batch_id: int, file_paths: list[str], file_id_by_position: dict[int, int]) -> None:
    """The whole pipeline for one batch: split -> scan (LOCAL_WORKERS) -> group ->
    extract+confidence per form group (AI_WORKERS), persisting as each group finishes."""
    try:
        _run_batch(batch_id, file_paths, file_id_by_position)
    except Exception as exc:  # orchestration-level failure, not a single page/form
        if not _batch_exists(batch_id):  # deleted by the user while processing: not a failure
            log.info("batch %s was deleted during processing; stopped", batch_id)
            return
        log.exception("batch %s failed", batch_id)
        with pool.connection() as conn:
            _update_batch(conn, batch_id, status="interrupted", error=f"{type(exc).__name__}: {exc}")


def _scan_one_page(path: str, index: int):
    """Render + scan one page and drop its big images straight away: returns None for a blank
    page, else (page_number, jpeg_bytes, words, barcodes, error). Only this keeps the web
    service inside 512 MB - never hold more than LOCAL_WORKERS full-resolution pages."""
    with pymupdf.open(path) as doc:
        page = load_page(doc, path, index)
    if page is None:
        return None
    try:
        scan = scan_page(page)
    except Exception as e:
        scan = _failed_scan(page, f"{type(e).__name__}: {e}")
    # field bboxes are in the oriented/deskewed space of scan.color, so store that
    return page.page_number, _encode_image(scan.color), scan.words, scan.barcodes, scan.error


def _run_batch(batch_id: int, file_paths: list[str], file_id_by_position: dict[int, int]) -> None:
    tasks = []  # (position, path, 0-based page index), in upload order
    for position, path in enumerate(file_paths):
        with pymupdf.open(path) as doc:
            tasks += [(position, path, i) for i in range(doc.page_count)]
    with pool.connection() as conn:
        _update_batch(conn, batch_id, total_pages=len(tasks))

    page_ids: dict[tuple[str, int], int] = {}
    skipped_pages, facts, ocr = [], [], {}
    pages_done = 0
    with ThreadPoolExecutor(max_workers=LOCAL_WORKERS) as executor:
        futures = [executor.submit(_scan_one_page, path, i) for _, path, i in tasks]
        for n, (position, path, i) in enumerate(tasks):  # consumed in order, so ids follow page order
            res = futures[n].result()
            futures[n] = None  # release the page JPEG as soon as it is stored
            with pool.connection() as conn:
                if res is None:
                    skipped_pages.append({"filename": Path(path).name, "page_index": i + 1})
                else:
                    page_number, image_bytes, words, barcodes, error = res
                    page_ids[(path, page_number)] = conn.execute(
                        "INSERT INTO pages (batch_id, file_id, page_index, image, error) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (batch_id, file_id_by_position[position], page_number, image_bytes, error),
                    ).fetchone()[0]
                    main_words = [w for w in words if w.rotation == 0]
                    crop_words = [w for w in words if w.rotation != 0]
                    key = SimpleNamespace(source=path, page_number=page_number)
                    facts.append(page_facts_from_ocr(key, main_words, barcodes, crop_words))
                    ocr[(path, page_number)] = (main_words, barcodes)
                pages_done += 1
                _update_batch(conn, batch_id, pages_done=pages_done)

    with pool.connection() as conn:
        _update_batch(conn, batch_id, total_pages=len(page_ids), pages_done=len(page_ids),
                      skipped_pages=Json(skipped_pages))

    groups = group_pages(facts)

    with pool.connection() as conn:
        _update_batch(conn, batch_id, total_forms=len(groups))

    today = date.today()

    def process_group(group: FormGroup) -> None:
        if not _batch_exists(batch_id):  # deleted mid-processing: skip the AI call
            return
        group_pagedata = [
            PageData(
                source=pf.source,
                page_number=pf.page_number,
                words=ocr[(pf.source, pf.page_number)][0],
                barcodes=ocr[(pf.source, pf.page_number)][1],
            )
            for pf in group.pages
        ]
        group_page_ids = [page_ids[(pf.source, pf.page_number)] for pf in group.pages]
        with pool.connection() as conn:  # stored JPEGs, decoded one at a time inside extract_form
            group_images = [bytes(conn.execute("SELECT image FROM pages WHERE id = %s", (pid,)).fetchone()[0])
                            for pid in group_page_ids]

        try:
            forms = extract_form(group_images, upload_date=today, group_flagged=group.flagged)
        except Exception as exc:  # extract_form() itself shouldn't raise, but be defensive
            forms = [ExtractedForm(page_indexes=list(range(len(group.pages))), notes=f"extraction_error: {exc}")]

        for form in forms:
            form_error = form.notes if (form.notes or "").startswith("extraction_error") else None
            try:
                form_pages = [group_pagedata[i] for i in form.page_indexes]
                confidence = score_form(form, form_pages, today)
                gemini: dict = {}
                confidence = verify_with_gemini(confidence, [group_images[i] for i in form.page_indexes],
                                                gemini_out=gemini if debug_sources.DEBUG else None)
                confidence = debug_sources.annotate(form, confidence, form_pages, gemini or None)  # no-op unless DEBUG_SOURCES=1
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

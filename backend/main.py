import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import batch_runner, forms_api, records
from .db import apply_migrations, mark_interrupted_batches, pool

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    with pool.connection() as conn:
        apply_migrations(conn)
        mark_interrupted_batches(conn)
    yield
    pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/batches")
async def create_batch(files: list[UploadFile] = File(...)):
    tmp_dir = Path(tempfile.mkdtemp(prefix="batch_"))
    file_paths, filenames = [], []
    for i, upload in enumerate(files):
        dest = tmp_dir / f"{i:03d}{Path(upload.filename or '').suffix}"
        dest.write_bytes(await upload.read())
        file_paths.append(str(dest))
        filenames.append(upload.filename or dest.name)

    with pool.connection() as conn:
        batch_id, file_id_by_position = batch_runner.create_batch(conn, filenames)

    thread = threading.Thread(
        target=batch_runner.process_batch, args=(batch_id, file_paths, file_id_by_position), daemon=True
    )
    thread.start()
    return {"batch_id": batch_id}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int):
    with pool.connection() as conn:
        batch = conn.execute(
            """
            SELECT id, status, total_pages, pages_done, total_forms, forms_done, skipped_pages, error
            FROM batches WHERE id = %s
            """,
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise HTTPException(404, "batch not found")
        rows = records.fetch_rows(conn, batch_id=batch_id)
    return {
        "id": batch[0],
        "status": batch[1],
        "total_pages": batch[2],
        "pages_done": batch[3],
        "total_forms": batch[4],
        "forms_done": batch[5],
        "skipped_pages": batch[6],
        "error": batch[7],
        "rows": rows,
    }


@app.get("/api/pages/{page_id}/image")
def get_page_image(page_id: int):
    with pool.connection() as conn:
        row = conn.execute("SELECT image FROM pages WHERE id = %s", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "page not found")
    return Response(content=row[0], media_type="image/jpeg")


@app.patch("/api/forms/{form_id}")
async def patch_form(form_id: int, request: Request):
    patch = await request.json()
    if not isinstance(patch, dict):
        raise HTTPException(400, "body must be a JSON object")
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM forms WHERE id = %s", (form_id,)).fetchone() is None:
            raise HTTPException(404, "form not found")
        try:
            updated = forms_api.apply_patch(conn, form_id, patch)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"updated": updated}


@app.post("/api/forms/{form_id}/merge-with-previous")
def merge_with_previous(form_id: int):
    with pool.connection() as conn:
        try:
            new_form_id = forms_api.merge_with_previous(conn, form_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"form_id": new_form_id}


@app.post("/api/forms/{form_id}/mark-separate")
def mark_separate(form_id: int):
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM forms WHERE id = %s", (form_id,)).fetchone() is None:
            raise HTTPException(404, "form not found")
        forms_api.mark_separate(conn, form_id)
    return {"ok": True}


@app.post("/api/batches/{batch_id}/approve")
def approve_batch(batch_id: int):
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM batches WHERE id = %s", (batch_id,)).fetchone() is None:
            raise HTTPException(404, "batch not found")
        still_review = forms_api.approve_batch(conn, batch_id)
    if still_review:
        raise HTTPException(409, detail={"count": len(still_review), "fields": still_review})
    return {"status": "approved"}


@app.get("/api/records")
def search_records(q: Optional[str] = None, status: Optional[str] = None):
    with pool.connection() as conn:
        form_ids = records.search_form_ids(conn, q, status)
        rows = records.fetch_rows(conn, form_ids=form_ids) if form_ids else []
    return {"rows": rows}


@app.get("/api/records/export.csv")
def export_records_csv(q: Optional[str] = None, status: Optional[str] = None):
    with pool.connection() as conn:
        form_ids = records.search_form_ids(conn, q, status)
        rows = records.fetch_rows(conn, form_ids=form_ids) if form_ids else []
    csv_bytes = records.rows_to_csv_bytes(rows)
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=records.csv"},
    )


if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")

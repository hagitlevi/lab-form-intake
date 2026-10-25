"""Derives one row per commitment number from forms+fields (CLAUDE.md: "One record per
commitment number; patient fields live on the form and are shared"), for the review UI
(GET /api/batches/{id}) and the records search/export (GET /api/records*)."""

from __future__ import annotations

import csv
import html
import io

import pymupdf

_SHARED_FIELDS = ("first_name", "last_name", "national_id", "received_date", "sender")
_MISSING_FIELD = {"value": None, "state": "review", "reasons": ["לא נמצא בטופס"], "bbox": None, "page_id": None}


def _group_fields(field_rows) -> dict[int, dict]:
    """(form_id, name, item_index, value, state, reasons, bbox, page_id) rows -> nested
    {form_id: {name: {item_index: {...}}}}."""
    by_form: dict[int, dict] = {}
    for form_id, name, item_index, value, state, reasons, bbox, page_id in field_rows:
        by_form.setdefault(form_id, {}).setdefault(name, {})[item_index] = {
            "value": value, "state": state, "reasons": reasons, "bbox": bbox, "page_id": page_id,
        }
    return by_form


def _form_rows(form: dict, fields_by_name: dict, pages: list[dict]) -> list[dict]:
    shared = {name: fields_by_name.get(name, {}).get(0, _MISSING_FIELD) for name in _SHARED_FIELDS}
    central = [fields_by_name.get("central_number", {})[i] for i in sorted(fields_by_name.get("central_number", {}))]
    commitments = fields_by_name.get("commitment_number", {})
    rows = []
    for item_index in sorted(commitments) or [0]:
        rows.append({
            "form_id": form["id"],
            "status": form["status"],
            "flagged": form["flagged"],
            "flag_reasons": form["flag_reasons"],
            "probable_continuation": form["probable_continuation"],
            "split_by_ai": form["split_by_ai"],
            "error": form["error"],
            "pages": pages,
            "item_index": item_index,
            **shared,
            "central_numbers": central,
            "commitment_number": commitments.get(item_index, _MISSING_FIELD),
        })
    return rows


_FORM_COLUMNS = "id, status, flagged, flag_reasons, probable_continuation, split_by_ai, error"


def fetch_rows(conn, batch_id: int | None = None, form_ids: list[int] | None = None) -> list[dict]:
    """Derived rows for a batch, or for an explicit list of form ids (used by the search
    endpoint, which filters forms first)."""
    if batch_id is not None:
        forms = conn.execute(f"SELECT {_FORM_COLUMNS} FROM forms WHERE batch_id = %s ORDER BY id", (batch_id,)).fetchall()
    elif form_ids:
        forms = conn.execute(f"SELECT {_FORM_COLUMNS} FROM forms WHERE id = ANY(%s) ORDER BY id", (form_ids,)).fetchall()
    else:
        return []

    form_dicts = [
        dict(zip(("id", "status", "flagged", "flag_reasons", "probable_continuation", "split_by_ai", "error"), r))
        for r in forms
    ]
    ids = [f["id"] for f in form_dicts]
    field_rows = conn.execute(
        "SELECT form_id, name, item_index, value, state, reasons, bbox, page_id FROM fields WHERE form_id = ANY(%s)",
        (ids,),
    ).fetchall() if ids else []
    fields_by_form = _group_fields(field_rows)
    pages_by_form: dict[int, list[dict]] = {}
    for form_id, page_id, filename, page_index, error in conn.execute(
        """
        SELECT p.form_id, p.id, fi.filename, p.page_index, p.error
        FROM pages p JOIN files fi ON fi.id = p.file_id
        WHERE p.form_id = ANY(%s) ORDER BY fi.position, p.page_index
        """,
        (ids,),
    ).fetchall() if ids else []:
        pages_by_form.setdefault(form_id, []).append(
            {"id": page_id, "filename": filename, "page_index": page_index, "error": error}
        )

    rows = []
    for form in form_dicts:
        rows.extend(_form_rows(form, fields_by_form.get(form["id"], {}), pages_by_form.get(form["id"], [])))
    return rows


def search_form_ids(conn, q: str | None, status: str | None) -> list[int]:
    """Form ids matching a free-text search over national_id/central_number/commitment_number
    values and/or a forms.status filter."""
    clauses, params = [], []
    base = "SELECT DISTINCT f.id FROM forms f"
    if q:
        base += " JOIN fields fl ON fl.form_id = f.id"
        clauses.append("fl.name IN ('national_id', 'central_number', 'commitment_number') AND fl.value ILIKE %s")
        params.append(f"%{q}%")
    if status:
        clauses.append("f.status = %s")
        params.append(status)
    if clauses:
        base += " WHERE " + " AND ".join(clauses)
    return [r[0] for r in conn.execute(base, params).fetchall()]


CSV_HEADER = [
    "form_id", "status", "first_name", "last_name", "national_id", "received_date", "sender",
    "central_numbers", "commitment_number", "flagged", "flag_reasons",
]


def rows_to_csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([
            row["form_id"], row["status"],
            row["first_name"]["value"], row["last_name"]["value"], row["national_id"]["value"],
            row["received_date"]["value"], row["sender"]["value"],
            ";".join(c["value"] or "" for c in row["central_numbers"]),
            row["commitment_number"]["value"],
            row["flagged"], ";".join(row["flag_reasons"] or []),
        ])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _il_date(v: str | None) -> str | None:
    """ISO YYYY-MM-DD -> DD/MM/YYYY for the printable PDF (CSV keeps ISO for other systems)."""
    if v and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return f"{v[8:10]}/{v[5:7]}/{v[0:4]}"
    return v


def rows_to_pdf_bytes(rows: list[dict]) -> bytes:
    """Records table as an RTL A4-landscape PDF (PyMuPDF Story; its built-in Hebrew font)."""
    def num(v):
        return f'<span dir="ltr">{html.escape(v)}</span>' if v else "—"
    head = ["סטטוס", "שם", "ת״ז", "מספר מרכזי", "תאריך קבלה", "גורם שולח", "מספר התחייבות"]
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in (
            "אושרה" if r["status"] == "approved" else "ממתינה",
            html.escape(" ".join(filter(None, (r["first_name"]["value"], r["last_name"]["value"])))) or "—",
            num(r["national_id"]["value"]),
            ", ".join(num(c["value"]) for c in r["central_numbers"] if c["value"]) or "—",
            num(_il_date(r["received_date"]["value"])),
            html.escape(r["sender"]["value"] or "") or "—",
            num(r["commitment_number"]["value"]),
        )) + "</tr>"
        for r in rows
    )
    doc = (
        '<html dir="rtl"><body style="font-family:sans-serif;font-size:10pt"><h2>רשומות</h2>'
        '<table style="width:100%;border-collapse:collapse">'
        "<tr>" + "".join(f'<th style="border:1px solid #999;background:#eee;padding:3px">{h}</th>' for h in head) + "</tr>"
        + body.replace("<td>", '<td style="border:1px solid #999;padding:3px;text-align:center">')
        + "</table></body></html>"
    )
    story = pymupdf.Story(doc)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page = pymupdf.paper_rect("a4-l")
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(page + (30, 30, -30, -30))
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()

"""E2E smoke test against a running server.

Start the server first: uvicorn backend.main:app
Then:    python -m backend.tools.e2e_api [--base-url http://127.0.0.1:8000]
                                          [--extra-sample samples/commitments_part1.pdf --pages 2]

Uploads samples/2pages.pdf (small, on purpose - never the full 48-page sample set unless
you explicitly add a slice via --extra-sample/--pages), polls GET /api/batches/{id} until
done, prints the derived rows, PATCHes one field, checks audit_log (direct DB read - this
tool already needs DATABASE_URL), tries to approve (expects 409 while review fields
remain), and exports the CSV.
"""

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psycopg
import pymupdf
from dotenv import load_dotenv

load_dotenv()

DEFAULT_FILE = Path("samples/2pages.pdf")


def _sliced_pdf(path: Path, pages: int) -> Path:
    doc = pymupdf.open(path)
    sliced = pymupdf.open()
    for i in range(min(pages, doc.page_count)):
        sliced.insert_pdf(doc, from_page=i, to_page=i)
    tmp = Path(tempfile.mkdtemp(prefix="e2e_")) / f"sliced_{pages}_{path.name}"
    sliced.save(tmp)
    return tmp


def _multipart_body(file_paths: list[Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks = []
    for path in file_paths:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        chunks.append(header + path.read_bytes() + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _request(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get(url: str) -> tuple[int, bytes]:
    return _request("GET", url)


def post_json(url: str, obj: dict) -> tuple[int, bytes]:
    return _request("POST", url, json.dumps(obj).encode(), {"Content-Type": "application/json"})


def patch_json(url: str, obj: dict) -> tuple[int, bytes]:
    return _request("PATCH", url, json.dumps(obj).encode(), {"Content-Type": "application/json"})


def audit_log_count(form_id: int) -> int:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return conn.execute("SELECT count(*) FROM audit_log WHERE form_id = %s", (form_id,)).fetchone()[0]


def _summary(result: dict) -> str:
    return f"state={result['state']} reason_count={len(result['reasons'])}"


MANUAL_OVERRIDE_REASON_COUNT = 1  # the fixed override reason is always a single item


def check_validation_override(base: str, form_id: int) -> bool:
    """A PATCHed value that fails validation must stay 'review' with the validation
    message (not silently become 'corrected'); resubmitting the identical failing value
    is accepted as a manual override ('corrected', fixed reason). Exercises both a scalar
    field (national_id) and a list field via the dict PATCH form (commitment_numbers)."""
    ok = True

    bad_id = "001123344"  # fails the Israeli ID check digit
    _, payload = patch_json(f"{base}/api/forms/{form_id}", {"national_id": bad_id})
    first = json.loads(payload)["updated"]["national_id"]
    print(f"  national_id PATCH #1 (failing value): {_summary(first)}")
    ok &= first["state"] == "review" and len(first["reasons"]) > 0

    _, payload = patch_json(f"{base}/api/forms/{form_id}", {"national_id": bad_id})
    second = json.loads(payload)["updated"]["national_id"]
    print(f"  national_id PATCH #2 (same value again): {_summary(second)}")
    ok &= second["state"] == "corrected" and len(second["reasons"]) == MANUAL_OVERRIDE_REASON_COUNT

    bad_commitment = "12"  # too short for any known sender's commitment-number format
    _, payload = patch_json(f"{base}/api/forms/{form_id}", {"commitment_numbers": {"0": bad_commitment}})
    first_c = json.loads(payload)["updated"]["commitment_numbers"]["0"]
    print(f"  commitment_numbers[0] PATCH #1 (failing value): {_summary(first_c)}")
    ok &= first_c["state"] == "review" and len(first_c["reasons"]) > 0

    _, payload = patch_json(f"{base}/api/forms/{form_id}", {"commitment_numbers": {"0": bad_commitment}})
    second_c = json.loads(payload)["updated"]["commitment_numbers"]["0"]
    print(f"  commitment_numbers[0] PATCH #2 (same value again): {_summary(second_c)}")
    ok &= second_c["state"] == "corrected" and len(second_c["reasons"]) == MANUAL_OVERRIDE_REASON_COUNT

    return ok


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--extra-sample", help="an additional sample PDF to upload, sliced to --pages pages")
    parser.add_argument("--pages", type=int, default=2, help="how many pages of --extra-sample to include (default 2)")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    files = [DEFAULT_FILE]
    if args.extra_sample:
        files.append(_sliced_pdf(Path(args.extra_sample), args.pages))

    for f in files:
        if not f.exists():
            print(f"missing sample file: {f}", file=sys.stderr)
            return 1

    body, boundary = _multipart_body(files)
    status, payload = _request(
        "POST", f"{base}/api/batches", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status != 200:
        print(f"POST /api/batches failed: {status} {payload!r}")
        return 1
    batch_id = json.loads(payload)["batch_id"]
    print(f"batch_id={batch_id}")

    batch = None
    for _ in range(150):
        status, payload = get(f"{base}/api/batches/{batch_id}")
        batch = json.loads(payload)
        if batch["status"] in ("done", "interrupted"):
            break
        print(f"  ... pages {batch['pages_done']}/{batch['total_pages']}, forms {batch['forms_done']}/{batch['total_forms']}")
        time.sleep(2)
    else:
        print("timed out waiting for batch to finish")
        return 1

    print(f"batch status: {batch['status']}" + (f" error={batch['error']}" if batch["error"] else ""))
    if batch["skipped_pages"]:
        print(f"skipped pages: {batch['skipped_pages']}")

    rows = batch["rows"]
    print(f"{len(rows)} row(s):")
    for row in rows:
        print(
            f"  form={row['form_id']} name={row['first_name']['value']} {row['last_name']['value']}"
            f" id={row['national_id']['value']} commitment={row['commitment_number']['value']}"
            f" sender={row['sender']['value']} ({row['national_id']['state']}/{row['commitment_number']['state']})"
        )

    if not rows:
        print("no rows produced - nothing to PATCH/approve against")
        return 1

    form_id = rows[0]["form_id"]
    before = audit_log_count(form_id)
    patch_body = {"sender": rows[0]["sender"]["value"] or "כללית"}
    status, payload = patch_json(f"{base}/api/forms/{form_id}", patch_body)
    print(f"PATCH /api/forms/{form_id} {patch_body}: {status} {payload.decode()}")
    if status != 200:
        return 1
    after = audit_log_count(form_id)
    print(f"audit_log rows for form {form_id}: {before} -> {after}" + (" OK" if after > before else " MISSING"))

    before_override = audit_log_count(form_id)
    override_ok = check_validation_override(base, form_id)
    after_override = audit_log_count(form_id)
    print(
        "validation-override behavior: " + ("OK" if override_ok else "FAILED")
        + f" (audit_log {before_override} -> {after_override})"
    )
    if not override_ok:
        return 1

    status, payload = get(f"{base}/api/records?q=&status=")
    print(f"GET /api/records: {status}, {len(json.loads(payload)['rows'])} row(s)")

    status, payload = post_json(f"{base}/api/batches/{batch_id}/approve", {})
    if status == 409:
        detail = json.loads(payload)["detail"]
        print(f"POST approve: 409 as expected, {detail['count']} field(s) still in review")
    elif status == 200:
        print("POST approve: 200, batch had no fields left in review")
    else:
        print(f"POST approve: unexpected status {status} {payload!r}")
        return 1

    status, payload = get(f"{base}/api/records/export.csv")
    print(f"GET /api/records/export.csv: {status}, {len(payload)} bytes")

    print("\ne2e OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

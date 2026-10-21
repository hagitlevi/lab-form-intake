"""CLI: python -m backend.tools.confidence_pdf <file1> [<file2> ...] [--no-cache] [--max-forms 5]

Runs grouping (steps 2+3) + AI extraction (step 4, cached) + confidence (step 5) and prints,
per form, each field with its state ("safe"/"review") and reasons. Ends with a summary: total
fields scored, how many are "review", broken down by field and by reason.

Step-4 results are cached in samples/debug/<first file stem>_extract.json, keyed by a hash of
the source file's bytes + the pages in that group - so re-running to develop/test step 5 costs
no API calls. --no-cache forces a fresh call and overwrites the cached entry.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from backend.grouping import group_pages, page_facts_from_ocr
from backend.pipeline import split_files
from backend.pipeline.confidence import FieldResult, FormConfidence, PageData, score_form
from backend.pipeline.extract import extract_form
from backend.pipeline.models import ExtractedForm
from backend.pipeline.scan import file_hash, load_cache, scan_pages

DEBUG_DIR = Path("samples/debug")


def _group_cache_key(pages: list) -> str:
    parts = [f"{file_hash(p.source)}:{p.page_number}" for p in pages]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _fmt_field(name: str, result: FieldResult) -> str:
    loc = f"  @ {result.source}:p{result.page} bbox={result.bbox}" if result.page else ""
    reasons = f"  [{'; '.join(result.reasons)}]" if result.reasons else ""
    return f"  {name}: value={result.value!r}  state={result.state}{reasons}{loc}"


def _print_form(idx: int, total: int, form: ExtractedForm, confidence: FormConfidence, pages_desc: str) -> None:
    print(f"\n--- Form {idx}/{total} ---")
    print(f"  pages: {pages_desc}")

    print(_fmt_field("first_name", confidence.first_name))
    print(_fmt_field("last_name", confidence.last_name))
    print(_fmt_field("national_id", confidence.national_id))
    for i, c in enumerate(confidence.central_numbers):
        print(_fmt_field(f"central_numbers[{i}]", c))
    print(_fmt_field("received_date", confidence.received_date))
    print(_fmt_field("sender", confidence.sender))
    for i, c in enumerate(confidence.commitment_numbers):
        print(_fmt_field(f"commitment_numbers[{i}]", c))


def _origin(fv) -> str:
    return fv.origin if fv is not None and fv.origin else "(none)"


def _all_results(form: ExtractedForm, confidence: FormConfidence) -> list[tuple[str, str, FieldResult]]:
    """(field_name, origin, result) - origin comes from the AI's own field (None/"(none)"
    when the field was absent, since there's nothing to attribute an origin to)."""
    out = [
        ("first_name", _origin(form.first_name), confidence.first_name),
        ("last_name", _origin(form.last_name), confidence.last_name),
        ("national_id", _origin(form.national_id), confidence.national_id),
        ("received_date", _origin(form.received_date), confidence.received_date),
        ("sender", _origin(form.sender), confidence.sender),
    ]
    for i, c in enumerate(confidence.central_numbers):
        fv = form.central_numbers[i] if i < len(form.central_numbers) else None
        out.append(("central_numbers", _origin(fv), c))
    for i, c in enumerate(confidence.commitment_numbers):
        fv = form.commitment_numbers[i] if i < len(form.commitment_numbers) else None
        out.append(("commitment_numbers", _origin(fv), c))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.tools.confidence_pdf")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--no-cache", action="store_true", help="force fresh step-4 calls, overwrite cache")
    parser.add_argument("--max-forms", type=int, default=5, help="stop after this many form groups (default 5)")
    args = parser.parse_args(argv)

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DEBUG_DIR / f"{Path(args.files[0]).stem}_extract.json"
    cache: dict[str, dict] = {}
    if cache_path.exists():
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cache = loaded
        # else: this file was written by a different tool (e.g. extract_pdf.py's own list
        # format) - not our cache schema, start fresh rather than crash on it.

    pages, skipped = split_files(args.files)
    if skipped:
        print(f"skipped {len(skipped)} blank page(s): " + ", ".join(f"{s['source']}:{s['page_number']}" for s in skipped))

    scan_cache_path = DEBUG_DIR / f"{Path(args.files[0]).stem}_scan.json"
    scan_cache = {} if args.no_cache else load_cache(scan_cache_path)
    scans, _ = scan_pages(pages, cache=scan_cache, no_cache=args.no_cache)  # read-only: scan_pdf writes this cache

    color_images, words_list, barcodes_list, facts_list = [], [], [], []
    for page, scan in zip(pages, scans):
        main_words = [w for w in scan.words if w.rotation == 0]
        crop_words = [w for w in scan.words if w.rotation != 0]
        facts_list.append(page_facts_from_ocr(page, main_words, scan.barcodes, crop_words))
        color_images.append(scan.color)
        words_list.append(main_words)  # confidence's PageData: main OCR pass only, as before
        barcodes_list.append(scan.barcodes)

    groups = group_pages(facts_list)
    print(f"found {len(groups)} form group(s) across {len(pages)} page(s)")

    today = date.today()
    field_counts: Counter = Counter()
    review_by_field: Counter = Counter()
    review_by_reason: Counter = Counter()
    origin_counts: Counter = Counter()
    review_by_origin: Counter = Counter()
    form_total = 0

    for gi, group in enumerate(groups[: args.max_forms], start=1):
        page_idxs = [facts_list.index(pf) for pf in group.pages]
        group_images = [color_images[i] for i in page_idxs]
        group_pagedata = [
            PageData(source=facts_list[i].source, page_number=facts_list[i].page_number, words=words_list[i], barcodes=barcodes_list[i])
            for i in page_idxs
        ]
        pages_desc = group.page_range

        key = _group_cache_key(group.pages)
        if key in cache and not args.no_cache:
            forms = [ExtractedForm.model_validate(f) for f in cache[key]["forms"]]
        else:
            forms = extract_form(group_images, upload_date=today, no_cache=args.no_cache, group_flagged=group.flagged)
            cache[key] = {
                "pages": [{"source": pd.source, "page_number": pd.page_number} for pd in group_pagedata],
                "forms": [f.model_dump(mode="json") for f in forms],
            }

        print(f"\n[group {gi}/{len(groups)}] {pages_desc} {'(flagged)' if group.flagged else ''}")

        for fi, form in enumerate(forms, start=1):
            form_pages = [group_pagedata[i] for i in form.page_indexes]
            confidence = score_form(form, form_pages, today)
            form_total += 1
            _print_form(fi, len(forms), form, confidence, pages_desc)

            for field_name, origin, result in _all_results(form, confidence):
                field_counts[field_name] += 1
                origin_counts[origin] += 1
                if result.state == "review":
                    review_by_field[field_name] += 1
                    review_by_origin[origin] += 1
                    for reason in result.reasons or ["(ללא סיבה)"]:
                        review_by_reason[reason] += 1

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    total_fields = sum(field_counts.values())
    total_review = sum(review_by_field.values())
    print(f"\n{'=' * 60}")
    print(f"forms processed : {form_total}")
    print(f"total fields    : {total_fields}")
    print(f"review          : {total_review}")
    print("\nby field:")
    for field_name, count in field_counts.items():
        print(f"  {field_name}: {review_by_field.get(field_name, 0)}/{count} review")
    print("\nby origin:")
    for origin, count in origin_counts.most_common():
        print(f"  {origin}: {review_by_origin.get(origin, 0)}/{count} review")
    print("\nby reason:")
    for reason, count in review_by_reason.most_common():
        print(f"  {count}x  {reason}")

    print(f"\nwrote {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

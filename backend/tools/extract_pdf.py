"""CLI: python -m backend.tools.extract_pdf <file1> [<file2> ...] [--max-forms 5] [--form N] [--no-cache]

Runs splitting + step 2 (preprocess/OCR/barcodes) + grouping + AI extraction on each form group.
Prints per form: pages, each field with value/raw_text/origin, and flags.
Writes samples/debug/<first-file-stem>_extract.json.
At the end: total tokens, total seconds, average seconds per form.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from backend.grouping import page_facts_from_ocr, run as grouping_run
from backend.pipeline import barcode_crop_words, preprocess, read_barcodes, run_ocr, split_files
from backend.pipeline.extract import ANTHROPIC_MODEL, extract_form
from backend.pipeline.models import ExtractedForm

DEBUG_DIR = Path("samples/debug")


def _fmt_field(name: str, obj) -> list[str]:
    if obj is None:
        return [f"  {name}: (absent)"]
    lines = [f"  {name}:"]
    if hasattr(obj, "value"):
        lines.append(f"    value={obj.value!r}  raw={obj.raw_text!r}  origin={obj.origin}")
    if hasattr(obj, "day"):
        lines.append(
            f"    day={obj.day} month={obj.month} year={obj.year}"
            f"  source={obj.date_source}  year_inferred={obj.year_inferred}"
        )
    return lines


def _print_form(idx: int, total: int, form: ExtractedForm, pages_desc: str) -> None:
    flags = []
    if form.probable_continuation:
        flags.append("probable_continuation")
    if form.split_by_ai:
        flags.append("split_by_ai")
    flag_str = f"  [{', '.join(flags)}]" if flags else ""
    print(f"\n--- Form {idx}/{total}{flag_str} ---")
    print(f"  pages: {pages_desc}")
    print(f"  page_indexes: {form.page_indexes}")

    for line in _fmt_field("first_name", form.first_name):
        print(line)
    for line in _fmt_field("last_name", form.last_name):
        print(line)
    for line in _fmt_field("national_id", form.national_id):
        print(line)

    if form.central_numbers:
        for i, cn in enumerate(form.central_numbers):
            for line in _fmt_field(f"central_numbers[{i}]", cn):
                print(line)
    else:
        print("  central_numbers: []")

    for line in _fmt_field("received_date", form.received_date):
        print(line)
    for line in _fmt_field("sender", form.sender):
        print(line)

    if form.commitment_numbers:
        for i, cn in enumerate(form.commitment_numbers):
            for line in _fmt_field(f"commitment_numbers[{i}]", cn):
                print(line)
    else:
        print("  commitment_numbers: []")

    if form.notes:
        print(f"  notes: {form.notes}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.tools.extract_pdf")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--max-forms", type=int, default=5, help="stop after this many form groups (default 5)")
    parser.add_argument("--form", type=int, default=None, help="process only this 1-based group index")
    parser.add_argument("--no-cache", action="store_true", help="ignore and overwrite AI cache")
    args = parser.parse_args(argv)

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"model: {ANTHROPIC_MODEL}")
    print("running step 2 (preprocess / OCR / barcodes) + grouping …")

    pages, skipped = split_files(args.files)
    if skipped:
        print(
            f"skipped {len(skipped)} blank page(s): "
            + ", ".join(f"{s['source']}:{s['page_number']}" for s in skipped)
        )

    # Step 2: per-page preprocessing; keep images alongside facts
    color_images: list = []
    facts_list = []
    for page in pages:
        gray, color = preprocess(page.image)
        words = run_ocr(gray)
        barcodes = read_barcodes(color, gray, words)
        crop_words = [w for b in barcodes for w in barcode_crop_words(gray, b.rect)]
        facts_list.append(page_facts_from_ocr(page, words, barcodes, crop_words))
        color_images.append(color)

    from backend.grouping import group_pages
    groups = group_pages(facts_list)

    print(f"found {len(groups)} form group(s) across {len(pages)} page(s)")

    # Select which groups to process
    group_indices = list(range(len(groups)))
    if args.form is not None:
        idx = args.form - 1
        if not (0 <= idx < len(groups)):
            print(f"error: --form {args.form} out of range (1–{len(groups)})", file=sys.stderr)
            return 1
        group_indices = [idx]
    else:
        group_indices = group_indices[: args.max_forms]

    total_input_tokens = 0
    total_output_tokens = 0
    total_elapsed = 0.0
    all_results: list[dict] = []
    form_count = 0

    today = date.today()

    for gi in group_indices:
        group = groups[gi]
        page_idxs_in_batch = [facts_list.index(pf) for pf in group.pages]
        group_images = [color_images[i] for i in page_idxs_in_batch]
        pages_desc = group.page_range

        print(f"\n[group {gi+1}/{len(groups)}] {pages_desc} {'(flagged)' if group.flagged else ''}")

        import time as _time
        t0 = _time.monotonic()

        forms = extract_form(
            group_images,
            upload_date=today,
            no_cache=args.no_cache,
            group_flagged=group.flagged,
        )
        elapsed = _time.monotonic() - t0
        total_elapsed += elapsed
        form_count += 1

        # Read token counts from cache metadata if available
        from backend.pipeline.extract import _cache_key, _read_cache, _resize_to_jpeg, AI_MAX_IMAGE_PX
        images_bytes = [_resize_to_jpeg(img, AI_MAX_IMAGE_PX) for img in group_images]
        cache_key = _cache_key(images_bytes, ANTHROPIC_MODEL)
        cached = _read_cache(cache_key)
        if cached and "_meta" in cached:
            meta = cached["_meta"]
            total_input_tokens += meta.get("input_tokens", 0)
            total_output_tokens += meta.get("output_tokens", 0)

        for fi, form in enumerate(forms, start=1):
            _print_form(fi, len(forms), form, pages_desc)
            form_count_display = fi

        all_results.append(
            {
                "group_index": gi + 1,
                "pages": pages_desc,
                "group_flagged": group.flagged,
                "group_reasons": group.reasons,
                "forms": [f.model_dump(mode="json") for f in forms],
            }
        )

    # Summary
    print(f"\n{'=' * 60}")
    print(f"groups processed : {len(group_indices)}")
    print(f"total tokens     : input={total_input_tokens}  output={total_output_tokens}")
    print(f"total time       : {total_elapsed:.1f}s")
    if form_count:
        print(f"avg per group    : {total_elapsed / form_count:.1f}s")

    stem = Path(args.files[0]).stem
    out_path = DEBUG_DIR / f"{stem}_extract.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

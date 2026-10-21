"""CLI: python -m backend.tools.ocr_experiment

Task-2 experiment: compares OCR preprocessing variants on the two known-miss hospital
screen-printout pages (commitments_part1.pdf p3, commitments_part2.pdf p20), searching for
specific known values at each tesseract confidence. Writes samples/debug/ocr_experiment.md.

Does not touch the OCR cache (bypassed on purpose, per the task) and does not change any
pipeline default - that happens separately, after reviewing the results here.
"""

import json
import sys
from pathlib import Path

from backend.pipeline import (
    Barcode,
    Word,
    _binarize_adaptive,
    _binarize_otsu,
    _gray_max_channel,
    _gray_red_channel,
    apply_orientation,
    apply_preprocessing,
    barcode_crop_words,
    merge_ocr_passes,
    read_barcodes,
    run_ocr,
    split_files,
)
from backend.pipeline.scan import load_cache, page_cache_key

DEBUG_DIR = Path("samples/debug")

KNOWN_VALUES = {
    "samples/commitments_part1.pdf": {"page": 3, "targets": ["127138045", "מספר התחייבות", "מרכזי"]},
    "samples/commitments_part2.pdf": {"page": 20, "targets": ["2603203", "2603204", "מספר התחייבות", "מרכזי"]},
}


def _find(words: list[Word], target: str):
    """Exact match on a single word, or on two adjacent-by-index words concatenated (OCR
    sometimes splits a table's label into two tokens, or a number loses a digit to a
    neighbor) - returns the best (found, confidence) pair."""
    target_joined = target.replace(" ", "")
    best = (False, None)
    for w in words:
        if w.text.strip() == target:
            return True, w.conf
    for a, b in zip(words, words[1:]):
        if (a.text + b.text).replace(" ", "") == target_joined or (b.text + a.text).replace(" ", "") == target_joined:
            best = (True, min(a.conf, b.conf))
    return best


def _variant_gray(oriented, variant: str):
    if variant == "a_current":
        return apply_preprocessing(oriented)[0]
    if variant == "b_red":
        return apply_preprocessing(oriented, to_gray=_gray_red_channel)[0]
    if variant == "b_max":
        return apply_preprocessing(oriented, to_gray=_gray_max_channel)[0]
    if variant == "c_red_otsu":
        return apply_preprocessing(oriented, to_gray=_gray_red_channel, binarize=_binarize_otsu)[0]
    if variant == "c_red_adaptive11":
        return apply_preprocessing(oriented, to_gray=_gray_red_channel, binarize=lambda g: _binarize_adaptive(g, 11))[0]
    if variant == "c_red_adaptive25":
        return apply_preprocessing(oriented, to_gray=_gray_red_channel, binarize=lambda g: _binarize_adaptive(g, 25))[0]
    raise ValueError(variant)


def run_variant(oriented, variant: str, scale: float = 1, config: str = "") -> list[Word]:
    gray = _variant_gray(oriented, variant)
    return run_ocr(gray, config=config, scale=scale)


def main_known_pages(argv: list[str]) -> int:
    rows = []  # (file, variant, total_words, {target: (found, conf)})

    for path, spec in KNOWN_VALUES.items():
        pages, _ = split_files([path])
        page = next(p for p in pages if p.page_number == spec["page"])
        angle, oriented = apply_orientation(page.image)
        print(f"{path} p{spec['page']}: orientation angle={angle} (0 = none needed, else corrected)")

        plans = [
            ("a_current", 1, ""),
            ("b_red", 1, ""),
            ("b_max", 1, ""),
            ("c_red_otsu", 1, ""),
            ("c_red_adaptive11", 1, ""),
            ("c_red_adaptive25", 1, ""),
        ]
        results_by_variant = {}
        for variant, scale, config in plans:
            words = run_variant(oriented, variant, scale, config)
            results_by_variant[variant] = words
            found = {t: _find(words, t) for t in spec["targets"]}
            rows.append((path, spec["page"], variant, len(words), found))
            print(f"  {variant:20s} words={len(words):4d} " + " ".join(f"{t}={f[0]}" for t, f in found.items()))

        # d: 2x upscale of whichever b/c variant found the most known values so far
        best_so_far = max(
            (v for v in results_by_variant if v != "a_current"),
            key=lambda v: sum(_find(results_by_variant[v], t)[0] for t in spec["targets"]),
        )
        for variant_label, scale in (("d_2x_" + best_so_far, 2),):
            words = run_variant(oriented, best_so_far, scale)
            found = {t: _find(words, t) for t in spec["targets"]}
            rows.append((path, spec["page"], variant_label, len(words), found))
            print(f"  {variant_label:20s} words={len(words):4d} " + " ".join(f"{t}={f[0]}" for t, f in found.items()))

        # e: --psm 6 / --psm 11 with the best preprocessing so far (by found-count, including d)
        best_overall = max(
            (r for r in rows if r[0] == path),
            key=lambda r: sum(f[0] for f in r[4].values()),
        )
        best_variant_name = best_overall[2]
        base_variant = best_variant_name.replace("d_2x_", "") if best_variant_name.startswith("d_2x_") else best_variant_name
        base_scale = 2 if best_variant_name.startswith("d_2x_") else 1
        for psm in (6, 11):
            words = run_variant(oriented, base_variant, base_scale, config=f"--psm {psm}")
            label = f"e_psm{psm}_on_{best_variant_name}"
            found = {t: _find(words, t) for t in spec["targets"]}
            rows.append((path, spec["page"], label, len(words), found))
            print(f"  {label:20s} words={len(words):4d} " + " ".join(f"{t}={f[0]}" for t, f in found.items()))

    _write_markdown(rows)
    return 0


def _write_markdown(rows) -> None:
    targets_seen = []
    for *_h, found in rows:
        for t in found:
            if t not in targets_seen:
                targets_seen.append(t)

    lines = ["# OCR preprocessing experiment", ""]
    header = "| file | page | variant | total words | " + " | ".join(targets_seen) + " |"
    sep = "|---" * (4 + len(targets_seen)) + "|"
    lines += [header, sep]
    for path, page, variant, total, found in rows:
        cells = []
        for t in targets_seen:
            ok, conf = found.get(t, (False, None))
            cells.append(f"✅ {conf:.0f}" if ok else "❌")
        lines.append(f"| {Path(path).name} | {page} | {variant} | {total} | " + " | ".join(cells) + " |")

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEBUG_DIR / "ocr_experiment.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")


FULL_FILES = ["samples/commitments_part1.pdf", "samples/commitments_part2.pdf"]


def _stats(word_texts: list[str], barcodes: list[dict]) -> dict:
    stickers = {b["value"].strip() for b in barcodes if len(b["value"].strip()) == 6 and b["value"].strip().isdigit()}
    central = sum(1 for b in barcodes if b.get("is_central"))
    return {"words": len(word_texts), "stickers": len(stickers), "central": central}


def _printed_values(forms_entry: dict) -> list[str]:
    values = []
    for form in forms_entry:
        for name in ("first_name", "last_name", "national_id", "sender"):
            fv = form.get(name)
            if fv and fv.get("origin") == "printed" and fv.get("value"):
                values.append(fv["value"].strip())
        for name in ("central_numbers", "commitment_numbers"):
            for fv in form.get(name) or []:
                if fv.get("origin") == "printed" and fv.get("value"):
                    values.append(fv["value"].strip())
    return values


def run_full_comparison(variant: str) -> None:
    """Compares `variant` (an _variant_gray() key) against the EXISTING scan cache -
    samples/debug/<file>_scan.json must already exist (run scan_pdf.py first); this never
    recomputes or overwrites that baseline. Across every page of both sample files."""
    baseline_totals = {"words": 0, "stickers": 0, "central": 0}
    variant_totals = {"words": 0, "stickers": 0, "central": 0}
    per_page_rows = []
    baseline_words_by_page: dict[tuple[str, int], set] = {}
    variant_words_by_page: dict[tuple[str, int], set] = {}

    for path in FULL_FILES:
        pages, _ = split_files([path])
        cache = load_cache(DEBUG_DIR / f"{Path(path).stem}_scan.json")
        if not cache:
            print(f"warning: no scan cache for {path} - run scan_pdf.py first. Skipping it.")
            continue

        for page in pages:
            entry = cache.get(page_cache_key(path, page.page_number))
            if entry is None:
                continue
            baseline_word_texts = [w["text"].strip() for w in entry["words"]]
            b_stats = _stats(baseline_word_texts, entry["barcodes"])
            baseline_words_by_page[(path, page.page_number)] = set(baseline_word_texts)

            angle, oriented = apply_orientation(page.image)
            gray_a, color = apply_preprocessing(oriented)
            words_a = run_ocr(gray_a)

            if variant == "merged":
                gray_b, _ = apply_preprocessing(oriented, to_gray=_gray_max_channel)
                words_b = run_ocr(gray_b)
                words = merge_ocr_passes(words_a, words_b)
                gray = gray_a  # crop-OCR (sideways labels) still anchored to the current/luminance image
            elif variant == "b_max":
                gray, color = apply_preprocessing(oriented, to_gray=_gray_max_channel)
                words = run_ocr(gray)
            else:
                gray, words = gray_a, words_a

            barcodes = read_barcodes(color, gray, words)  # uses `gray` internally for its own crop-OCR too
            crop_words = [w for b in barcodes for w in barcode_crop_words(gray, b.rect)]
            v_word_texts = [w.text.strip() for w in words + crop_words]  # match scan_page()'s combined words
            v_barcodes = [{"value": b.value, "is_central": b.is_central} for b in barcodes]
            v_stats = _stats(v_word_texts, v_barcodes)
            variant_words_by_page[(path, page.page_number)] = set(v_word_texts)

            for k in baseline_totals:
                baseline_totals[k] += b_stats[k]
                variant_totals[k] += v_stats[k]
            per_page_rows.append((path, page.page_number, b_stats, v_stats))
            print(
                f"{Path(path).name} p{page.page_number}: baseline words={b_stats['words']:4d} "
                f"stickers={b_stats['stickers']} central={b_stats['central']}   |   "
                f"{variant} words={v_stats['words']:4d} stickers={v_stats['stickers']} central={v_stats['central']}"
            )

    # Printed-value recovery, from whatever step-4 extraction is ALREADY cached - no new API calls.
    printed_total = 0
    printed_in_baseline = 0
    printed_in_variant = 0
    for path in FULL_FILES:
        extract_cache_path = DEBUG_DIR / f"{Path(path).stem}_extract.json"
        if not extract_cache_path.exists():
            continue
        extract_cache = json.loads(extract_cache_path.read_text(encoding="utf-8"))
        for entry in extract_cache.values():
            page_numbers = [pg["page_number"] for pg in entry["pages"]]
            b_pool = set().union(*(baseline_words_by_page.get((path, p), set()) for p in page_numbers))
            v_pool = set().union(*(variant_words_by_page.get((path, p), set()) for p in page_numbers))
            for value in _printed_values(entry["forms"]):
                printed_total += 1
                printed_in_baseline += value in b_pool
                printed_in_variant += value in v_pool

    print(f"\n{'=' * 60}")
    print(f"TOTALS (both files, {len(per_page_rows)} pages)")
    print(f"  baseline : words={baseline_totals['words']} stickers={baseline_totals['stickers']} central={baseline_totals['central']}")
    print(f"  {variant:8s} : words={variant_totals['words']} stickers={variant_totals['stickers']} central={variant_totals['central']}")
    print(f"\nAI-extracted PRINTED values found in OCR (from cached step-4 results only, {printed_total} values checked):")
    print(f"  baseline : {printed_in_baseline}/{printed_total}")
    print(f"  {variant:8s} : {printed_in_variant}/{printed_total}")

    _append_full_comparison_markdown(variant, per_page_rows, baseline_totals, variant_totals, printed_total, printed_in_baseline, printed_in_variant)


def _append_full_comparison_markdown(variant, per_page_rows, baseline_totals, variant_totals, printed_total, printed_in_baseline, printed_in_variant) -> None:
    lines = ["", f"## Full-file comparison: current vs {variant}", ""]
    lines.append(f"| file | page | baseline words | baseline stickers | baseline central | {variant} words | {variant} stickers | {variant} central |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for path, page_number, b, v in per_page_rows:
        lines.append(
            f"| {Path(path).name} | {page_number} | {b['words']} | {b['stickers']} | {b['central']} | "
            f"{v['words']} | {v['stickers']} | {v['central']} |"
        )
    lines.append("")
    lines.append(
        f"**Totals:** baseline words={baseline_totals['words']} stickers={baseline_totals['stickers']} "
        f"central={baseline_totals['central']}; {variant} words={variant_totals['words']} "
        f"stickers={variant_totals['stickers']} central={variant_totals['central']}"
    )
    lines.append("")
    lines.append(
        f"**AI-extracted printed values found in OCR** (cached step-4 results only, {printed_total} checked): "
        f"baseline {printed_in_baseline}/{printed_total}, {variant} {printed_in_variant}/{printed_total}"
    )

    out_path = DEBUG_DIR / "ocr_experiment.md"
    with open(out_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nappended to {out_path}")


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--full":
        variant = argv[1] if len(argv) > 1 else "b_max"
        run_full_comparison(variant)
        return 0
    return main_known_pages(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

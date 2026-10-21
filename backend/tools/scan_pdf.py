"""CLI: python -m backend.tools.scan_pdf samples/<file>.pdf [more files...] [--page N] [--words] [--no-cache]

Runs pipeline step 2 (split -> digital check -> preprocess -> OCR -> barcodes) on each
page, across LOCAL_WORKERS threads, and prints one JSON line per page plus per-page
progress ("page 5/26 ... 4.2s") and a final summary (total time, average time per stage).
Saves debug images with OCR/barcode boxes drawn to samples/debug/.

Writes the step-2 cache to samples/debug/<first file stem>_scan.json (words with bbox and
confidence, barcodes, orientation - never a full OCR text string or the page image) so
group_pdf/confidence_pdf can reuse it without recomputing. --page N restricts which pages
run but still reads/writes the full file's cache. --page N also limits progress reporting
to that page. --words writes OCR words to a TSV (Hebrew text never goes to the terminal).
--no-cache forces every page to be recomputed and overwrites its cache entry.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2

from backend.pipeline import Page, split_files
from backend.pipeline.scan import STAGES, failed_pages, load_cache, scan_pages

DEBUG_DIR = Path("samples/debug")


def _draw_debug_image(color, words, barcodes) -> "cv2.Mat":
    image = color.copy()
    for w in words:
        cv2.rectangle(image, (w.left, w.top), (w.left + w.width, w.top + w.height), (0, 200, 0), 1)
    for b in barcodes:
        x, y, bw, bh = (int(v) for v in b.rect)
        box_color = (0, 0, 255) if b.is_central else (255, 0, 0)
        cv2.rectangle(image, (x, y), (x + bw, y + bh), box_color, 3)
    return image


def _reading_order(rows):
    """Group into visual lines by y-overlap, then order lines top-to-bottom and words right-to-left.

    ponytail: a simple overlap-clustering heuristic, not a layout engine - good enough for a
    debug dump, can misorder heavily skewed or overlapping lines.
    """
    lines = []
    for row in sorted(rows, key=lambda r: r[3]):
        _, _, _, y, _, h, _ = row
        cy = y + h / 2
        for line in lines:
            if line["y0"] <= cy <= line["y1"]:
                line["items"].append(row)
                line["y0"] = min(line["y0"], y)
                line["y1"] = max(line["y1"], y + h)
                break
        else:
            lines.append({"y0": y, "y1": y + h, "items": [row]})

    lines.sort(key=lambda line: (line["y0"] + line["y1"]) / 2)
    ordered = []
    for line in lines:
        ordered.extend(sorted(line["items"], key=lambda r: -r[2]))  # right-to-left: x descending
    return ordered


def _write_words_tsv(path: Path, words) -> None:
    rows = [(w.text, w.conf, w.left, w.top, w.width, w.height, w.rotation) for w in words]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["text", "confidence", "x", "y", "w", "h", "rotation"])
        writer.writerows(_reading_order(rows))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.tools.scan_pdf")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--page", type=int, default=None, help="process only this 1-based page number")
    parser.add_argument("--words", action="store_true", help="also dump OCR words to a TSV file")
    parser.add_argument("--no-cache", action="store_true", help="recompute every page, overwrite its cache entry")
    args = parser.parse_args(argv)

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DEBUG_DIR / f"{Path(args.files[0]).stem}_scan.json"
    cache = {} if args.no_cache else load_cache(cache_path)

    all_pages, skipped = split_files(args.files)
    if skipped:
        print(f"skipped {len(skipped)} blank page(s): " + ", ".join(f"{s['source']}:{s['page_number']}" for s in skipped))

    pages: list[Page] = [p for p in all_pages if args.page is None or p.page_number == args.page]
    total = len(pages)

    done = 0

    def on_progress(page: Page, elapsed: float, from_cache: bool) -> None:
        nonlocal done
        done += 1  # called on the main thread only, no lock needed
        tag = "cached" if from_cache else f"{elapsed:.1f}s"
        print(f"page {done}/{total} ({page.source}:{page.page_number}) ... {tag}")

    t0 = time.monotonic()
    scans, cache = scan_pages(pages, cache=cache, no_cache=args.no_cache, on_progress=on_progress, cache_path=cache_path)
    total_elapsed = time.monotonic() - t0
    for line in failed_pages(scans):
        print(f"FAILED page: {line}")

    for page, scan in zip(pages, scans):
        main_words = [w for w in scan.words if w.rotation == 0]
        central_barcode = next((b.value for b in scan.barcodes if b.is_central), None)
        sticker_candidates = sorted({b.value for b in scan.barcodes if len(b.value) == 6 and b.value.isdigit()})

        print(
            json.dumps(
                {
                    "source": page.source,
                    "page_number": page.page_number,
                    "digital": page.is_digital,
                    "barcodes": [
                        {"value": b.value, "format": b.format, "central": b.is_central}
                        for b in scan.barcodes
                    ],
                    "ocr_word_count": len(main_words),
                    "central_barcode": central_barcode,
                    "sticker_candidates": sticker_candidates,
                },
                ensure_ascii=False,
            )
        )

        debug_image = _draw_debug_image(scan.color, scan.words, scan.barcodes)
        stem = Path(page.source).stem
        cv2.imwrite(str(DEBUG_DIR / f"{stem}_p{page.page_number}.png"), debug_image)

        if args.words:
            tsv_path = DEBUG_DIR / f"{stem}_p{page.page_number}_words.tsv"
            _write_words_tsv(tsv_path, scan.words)
            print(tsv_path)

    computed = [s.timings for s in scans if s.timings]
    print(f"\n{'=' * 60}")
    print(f"pages processed : {total} ({len(computed)} computed, {total - len(computed)} from cache)")
    print(f"total time      : {total_elapsed:.1f}s")
    if computed:
        print("average per stage (computed pages only):")
        for stage in STAGES:
            avg = sum(t.get(stage, 0.0) for t in computed) / len(computed)
            print(f"  {stage:14s}: {avg:.2f}s")
    print(f"\nwrote {cache_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

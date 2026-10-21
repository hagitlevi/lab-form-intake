"""CLI: python -m backend.tools.scan_pdf samples/<file>.pdf [more files...] [--page N] [--words]

Runs pipeline step 2 (split -> digital check -> preprocess -> OCR -> barcodes) on each
page and prints one JSON line per page. Saves debug images with OCR/barcode boxes drawn
to samples/debug/.

--page N   process only page N (1-based, within each file) instead of every page.
--words    also write each processed page's OCR words to a TSV (OCR words are Hebrew
           text, so they go to a file, never to the terminal - only its path is printed).
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2

from backend.pipeline import barcode_crop_words, preprocess, read_barcodes, run_ocr, split_files

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


def _barcode_crop_rows(gray, barcode):
    """Rotated-crop OCR words around a barcode, as plain (text, conf, x, y, w, h, rotation)
    rows for the TSV dump - reuses backend.pipeline's own crop/rotate/unrotate geometry."""
    return [
        (w.text, w.conf, w.left, w.top, w.width, w.height, w.rotation)
        for w in barcode_crop_words(gray, barcode.rect)
    ]


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


def _write_words_tsv(path: Path, gray, words, barcodes) -> None:
    rows = [(w.text, w.conf, w.left, w.top, w.width, w.height, w.rotation) for w in words]
    for barcode in barcodes:
        rows.extend(_barcode_crop_rows(gray, barcode))

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["text", "confidence", "x", "y", "w", "h", "rotation"])
        writer.writerows(_reading_order(rows))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.tools.scan_pdf")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--page", type=int, default=None, help="process only this 1-based page number")
    parser.add_argument("--words", action="store_true", help="also dump OCR words to a TSV file")
    args = parser.parse_args(argv)

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    pages, skipped = split_files(args.files)
    if skipped:
        print(f"skipped {len(skipped)} blank page(s): " + ", ".join(f"{s['source']}:{s['page_number']}" for s in skipped))
    if args.page is not None:
        pages = [p for p in pages if p.page_number == args.page]

    for page in pages:
        gray, color = preprocess(page.image)
        words = run_ocr(gray)
        barcodes = read_barcodes(color, gray, words)
        central_barcode = next((b.value for b in barcodes if b.is_central), None)
        sticker_candidates = sorted({b.value for b in barcodes if len(b.value) == 6 and b.value.isdigit()})

        print(
            json.dumps(
                {
                    "source": page.source,
                    "page_number": page.page_number,
                    "digital": page.is_digital,
                    "barcodes": [
                        {"value": b.value, "format": b.format, "central": b.is_central}
                        for b in barcodes
                    ],
                    "ocr_word_count": len(words),
                    "central_barcode": central_barcode,
                    "sticker_candidates": sticker_candidates,
                },
                ensure_ascii=False,
            )
        )

        debug_image = _draw_debug_image(color, words, barcodes)
        stem = Path(page.source).stem
        cv2.imwrite(str(DEBUG_DIR / f"{stem}_p{page.page_number}.png"), debug_image)

        if args.words:
            tsv_path = DEBUG_DIR / f"{stem}_p{page.page_number}_words.tsv"
            _write_words_tsv(tsv_path, gray, words, barcodes)
            print(tsv_path)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

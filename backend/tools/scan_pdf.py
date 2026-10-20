"""CLI: python -m backend.tools.scan_pdf samples/<file>.pdf [more files...]

Runs pipeline step 2 (split -> digital check -> preprocess -> OCR -> barcodes) on each
page and prints one JSON line per page. Saves debug images with OCR/barcode boxes drawn
to samples/debug/.
"""

import json
import sys
from pathlib import Path

import cv2

from backend.pipeline import preprocess, read_barcodes, run_ocr, split_files

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


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m backend.tools.scan_pdf <file> [<file> ...]")
        return 1

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    pages = split_files(argv)

    for page in pages:
        gray, color = preprocess(page.image)
        words = run_ocr(gray)
        barcodes = read_barcodes(color, words)
        central = next((b.value for b in barcodes if b.is_central), None)

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
                    "central_number": central,
                },
                ensure_ascii=False,
            )
        )

        debug_image = _draw_debug_image(color, words, barcodes)
        name = f"{Path(page.source).stem}_p{page.page_number}.png"
        cv2.imwrite(str(DEBUG_DIR / name), debug_image)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

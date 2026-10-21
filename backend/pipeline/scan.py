"""Step 2 orchestration: timed, parallel (LOCAL_WORKERS), cached per-page scanning.

Wraps backend.pipeline's existing primitives (preprocess, run_ocr, read_barcodes,
barcode_crop_words) - none of their settings change here, this is only orchestration
around them: timing per stage, a disk cache keyed by (file hash, page number, settings
hash), and running pages across a thread pool.

Cache schema at samples/debug/<file>_scan.json: {cache_key: {"words": [...], "barcodes":
[...], "orientation": int}} - only what steps 3/4/5 actually use. Never the page image or
a full-OCR-text string.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract

from backend.pipeline import (
    BLANK_INK_RATIO,
    CENTRAL_PROXIMITY_PX,
    DIGITAL_IMAGE_COVERAGE,
    DPI,
    Barcode,
    Page,
    Word,
    _gray_max_channel,
    _rotate_image,
    apply_orientation,
    apply_preprocessing,
    barcode_crop_words,
    merge_ocr_passes,
    read_barcodes,
    run_ocr,
)

LOCAL_WORKERS = int(os.environ.get("LOCAL_WORKERS", 2))

STAGES = ("render", "orientation", "preprocessing", "ocr", "ocr_secondary", "barcodes", "rotated_crops")


# --- settings hash / file hash / cache key -----------------------------------

def settings_hash() -> str:
    """Hash of everything that affects step-2 output. Change any of these constants (or
    the tesseract binary) and this changes too, invalidating every cached page."""
    try:
        tess_version = str(pytesseract.get_tesseract_version())
    except Exception:
        tess_version = "unknown"
    settings = {
        "dpi": DPI,
        "blank_ink_ratio": BLANK_INK_RATIO,
        "digital_image_coverage": DIGITAL_IMAGE_COVERAGE,
        "central_proximity_px": CENTRAL_PROXIMITY_PX,
        "ocr_lang": "heb+eng",
        "clahe_clip_limit": 2.0,
        "clahe_tile_grid": [8, 8],
        "ocr_passes": ["luminance", "max_channel"],  # merged via merge_ocr_passes; see scan_page()
        "ocr_merge_iou_threshold": 0.3,
        "tesseract_version": tess_version,
    }
    blob = json.dumps(settings, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


_FILE_HASH_CACHE: dict[str, str] = {}


def file_hash(path: str) -> str:
    if path not in _FILE_HASH_CACHE:
        _FILE_HASH_CACHE[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    return _FILE_HASH_CACHE[path]


def page_cache_key(path: str, page_number: int) -> str:
    raw = f"{file_hash(path)}:{page_number}:{settings_hash()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# --- cache file I/O ------------------------------------------------------------

def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a killed run never leaves a half-written cache


def _word_to_dict(w: Word) -> dict:
    return {"text": w.text, "conf": w.conf, "left": w.left, "top": w.top, "width": w.width, "height": w.height, "rotation": w.rotation}


def _word_from_dict(d: dict) -> Word:
    return Word(text=d["text"], conf=d["conf"], left=d["left"], top=d["top"], width=d["width"], height=d["height"], rotation=d.get("rotation", 0))


def _barcode_to_dict(b: Barcode) -> dict:
    return {"value": b.value, "format": b.format, "rect": list(b.rect), "is_central": b.is_central}


def _barcode_from_dict(d: dict) -> Barcode:
    return Barcode(value=d["value"], format=d["format"], rect=tuple(d["rect"]), is_central=d.get("is_central", False))


# --- per-page scan, with timing ------------------------------------------------

@dataclass
class PageScan:
    source: str
    page_number: int
    orientation: int
    gray: np.ndarray
    color: np.ndarray
    words: list[Word]
    barcodes: list[Barcode]
    timings: dict[str, float] = field(default_factory=dict)
    from_cache: bool = False
    error: Optional[str] = None  # set when the page failed (e.g. tesseract timeout); words/barcodes are empty


def scan_page(page: Page) -> PageScan:
    """Run steps orientation -> preprocessing -> OCR -> barcodes -> rotated crops on one
    page, with per-stage timing.

    The OCR step is two passes merged (see backend.tools.ocr_experiment /
    samples/debug/ocr_experiment.md): standard luminance grayscale, plus a max-channel
    grayscale that erases yellow highlighter (which reads as near-white on that channel)
    - this recovers printed values on hospital screen printouts that highlighter/dense
    tables hide from the luminance pass alone. Measured on all 48 sample pages: zero
    regressions (words/stickers/central-confirmations never lower than the single-pass
    baseline on any page), so this is the new default, not an experiment-only option.
    Binarization and upscaling were tried too but didn't measurably help (see the .md) and
    aren't used here. `color` (sent to the AI, stored for review) is never touched.
    """
    timings = {"render": page.render_seconds}

    t0 = time.monotonic()
    angle, oriented = apply_orientation(page.image)
    timings["orientation"] = time.monotonic() - t0

    t0 = time.monotonic()
    gray, color = apply_preprocessing(oriented)
    timings["preprocessing"] = time.monotonic() - t0

    t0 = time.monotonic()
    words_primary = run_ocr(gray)
    timings["ocr"] = time.monotonic() - t0

    t0 = time.monotonic()
    gray_secondary, _ = apply_preprocessing(oriented, to_gray=_gray_max_channel)
    words_secondary = run_ocr(gray_secondary)
    words = merge_ocr_passes(words_primary, words_secondary)
    timings["ocr_secondary"] = time.monotonic() - t0

    t0 = time.monotonic()
    barcodes = read_barcodes(color, gray, words)
    timings["barcodes"] = time.monotonic() - t0

    t0 = time.monotonic()
    crop_words = [w for b in barcodes for w in barcode_crop_words(gray, b.rect)]
    timings["rotated_crops"] = time.monotonic() - t0

    return PageScan(
        source=page.source,
        page_number=page.page_number,
        orientation=angle,
        gray=gray,
        color=color,
        words=words + crop_words,
        barcodes=barcodes,
        timings=timings,
    )


def _scan_from_cache_entry(page: Page, entry: dict) -> PageScan:
    """Rebuild gray/color (needed by later steps) and reuse cached words/barcodes/orientation -
    skips OCR/barcode decoding, not image preprocessing (images aren't cached, see module docstring)."""
    angle = entry["orientation"]
    oriented = _rotate_image(page.image, angle) if angle else page.image
    gray, color = apply_preprocessing(oriented)
    return PageScan(
        source=page.source,
        page_number=page.page_number,
        orientation=angle,
        gray=gray,
        color=color,
        words=[_word_from_dict(w) for w in entry["words"]],
        barcodes=[_barcode_from_dict(b) for b in entry["barcodes"]],
        timings={},
        from_cache=True,
    )


def _failed_scan(page: Page, error: str) -> PageScan:
    gray = cv2.cvtColor(page.image, cv2.COLOR_BGR2GRAY)
    return PageScan(page.source, page.page_number, 0, gray, page.image, [], [], error=error)


def scan_pages(
    pages: list[Page],
    cache: Optional[dict] = None,
    no_cache: bool = False,
    max_workers: int = LOCAL_WORKERS,
    on_progress=None,
    cache_path: Optional[Path] = None,
) -> tuple[list[PageScan], dict]:
    """Scan every page, reusing `cache` entries unless no_cache. Cache misses run across
    up to max_workers threads. Returns (scans, updated_cache). A page that raises (e.g. a
    tesseract timeout) gets an empty PageScan with .error set - the run goes on; callers
    report those. If cache_path is given, the cache is written once, after all pages are
    done. No locks: workers only compute; progress callbacks and cache updates happen on
    the calling thread."""
    cache = dict(cache or {})
    results: list[Optional[PageScan]] = [None] * len(pages)
    to_compute: list[int] = []

    for i, page in enumerate(pages):
        key = page_cache_key(page.source, page.page_number)
        entry = None if no_cache else cache.get(key)
        if entry is not None:
            results[i] = _scan_from_cache_entry(page, entry)
            if on_progress:
                on_progress(page, 0.0, from_cache=True)
        else:
            to_compute.append(i)

    def _run(i: int) -> tuple[PageScan, float]:
        t0 = time.monotonic()
        try:
            scan = scan_page(pages[i])
        except Exception as e:
            scan = _failed_scan(pages[i], f"{type(e).__name__}: {e}")
        return scan, time.monotonic() - t0

    if to_compute:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run, i): i for i in to_compute}
            for fut in as_completed(futures):
                i = futures[fut]
                scan, elapsed = fut.result()
                results[i] = scan
                if on_progress:
                    on_progress(pages[i], elapsed, from_cache=False)
                if scan.error is None:  # never cache a failure
                    cache[page_cache_key(scan.source, scan.page_number)] = {
                        "words": [_word_to_dict(w) for w in scan.words],
                        "barcodes": [_barcode_to_dict(b) for b in scan.barcodes],
                        "orientation": scan.orientation,
                    }
        if cache_path is not None:
            save_cache(cache_path, cache)

    return results, cache  # type: ignore[return-value]


def failed_pages(scans: list[PageScan]) -> list[str]:
    return [f"{s.source}:{s.page_number} - {s.error}" for s in scans if s.error]

"""Unit tests for backend.pipeline.scan: caching, hashing, and the cache-hit path.

Run: python -m unittest tests.test_scan

Deliberately avoids calling scan_page() for real (needs tesseract + takes seconds per
page) - the cache-hit path is tested by pre-seeding a cache entry, which is exactly what
makes repeat runs fast.
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from backend.pipeline import Barcode, Page, Word
from backend.pipeline.scan import (
    PageScan,
    failed_pages,
    _barcode_from_dict,
    _barcode_to_dict,
    _word_from_dict,
    _word_to_dict,
    file_hash,
    load_cache,
    page_cache_key,
    save_cache,
    scan_pages,
    settings_hash,
)


def blank_page(source: str, page_number=1, size=(100, 100)) -> Page:
    image = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    return Page(source=source, page_number=page_number, image=image, is_digital=False)


class TestSettingsHash(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(settings_hash(), settings_hash())

    def test_is_a_short_hex_string(self):
        h = settings_hash()
        self.assertEqual(len(h), 16)
        int(h, 16)  # raises if not hex


class TestFileHash(unittest.TestCase):
    def test_same_file_same_hash(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"hello world")
            path = f.name
        try:
            self.assertEqual(file_hash(path), file_hash(path))
        finally:
            Path(path).unlink()

    def test_different_content_different_hash(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f1, tempfile.NamedTemporaryFile(
            suffix=".pdf", delete=False
        ) as f2:
            f1.write(b"content one")
            f2.write(b"content two, different")
            p1, p2 = f1.name, f2.name
        try:
            self.assertNotEqual(file_hash(p1), file_hash(p2))
        finally:
            Path(p1).unlink()
            Path(p2).unlink()


class TestPageCacheKey(unittest.TestCase):
    def test_different_page_numbers_differ(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"some bytes")
            path = f.name
        try:
            self.assertNotEqual(page_cache_key(path, 1), page_cache_key(path, 2))
        finally:
            Path(path).unlink()

    def test_same_file_and_page_is_stable(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"some bytes")
            path = f.name
        try:
            self.assertEqual(page_cache_key(path, 1), page_cache_key(path, 1))
        finally:
            Path(path).unlink()


class TestSerialization(unittest.TestCase):
    def test_word_round_trip(self):
        w = Word(text="939376", conf=91.5, left=10, top=20, width=30, height=15, rotation=90)
        self.assertEqual(_word_from_dict(_word_to_dict(w)), w)

    def test_barcode_round_trip(self):
        b = Barcode(value="939376", format="Code 128", rect=(1, 2, 3, 4), is_central=True)
        self.assertEqual(_barcode_from_dict(_barcode_to_dict(b)), b)


class TestCacheFileIO(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            data = {"abc": {"words": [], "barcodes": [], "orientation": 0}}
            save_cache(path, data)
            self.assertEqual(load_cache(path), data)

    def test_missing_file_is_empty_dict(self):
        self.assertEqual(load_cache(Path("/no/such/file.json")), {})

    def test_wrong_schema_file_is_empty_dict(self):
        # a list (e.g. from a different tool's output format) must not crash the loader
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(load_cache(path), {})


class TestScanPagesCacheHit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self._tmp.write(b"fake pdf bytes for hashing purposes only")
        self._tmp.close()
        self.source = self._tmp.name

    def tearDown(self):
        Path(self.source).unlink()

    def test_cached_page_is_not_recomputed(self):
        page = blank_page(self.source)
        key = page_cache_key(page.source, page.page_number)
        cache = {key: {"words": [_word_to_dict(Word("939376", 90.0, 0, 0, 10, 10))], "barcodes": [], "orientation": 0}}

        progress_calls = []
        scans, _ = scan_pages([page], cache=cache, on_progress=lambda p, e, from_cache: progress_calls.append(from_cache))

        self.assertEqual(len(scans), 1)
        self.assertTrue(scans[0].from_cache)
        self.assertEqual([w.text for w in scans[0].words], ["939376"])
        self.assertEqual(progress_calls, [True])

    def test_no_cache_flag_ignores_existing_entry(self):
        # forces recompute - a small blank image so the real (if slow) path still runs fast
        page = blank_page(self.source, size=(20, 20))
        key = page_cache_key(page.source, page.page_number)
        cache = {key: {"words": [_word_to_dict(Word("stale", 1.0, 0, 0, 1, 1))], "barcodes": [], "orientation": 0}}

        scans, updated_cache = scan_pages([page], cache=cache, no_cache=True)

        self.assertFalse(scans[0].from_cache)
        # a blank page should produce no OCR words, not the stale cached one
        self.assertNotIn("stale", [w.text for w in scans[0].words])


class TestScanPagesParallelWithCacheWrite(unittest.TestCase):
    def test_two_workers_cache_write_finishes_and_reports_failed_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "f.pdf"
            src.write_bytes(b"fake pdf")
            cache_path = Path(tmp) / "f_scan.json"
            pages = [blank_page(str(src), n, size=(20, 20)) for n in range(1, 7)]
            progress = []

            def fake_scan_page(page):
                time.sleep(0.05)
                if page.page_number == 3:
                    raise RuntimeError("Tesseract process timeout")
                return PageScan(page.source, page.page_number, 0, page.image[:, :, 0], page.image, [], [])

            result = {}

            def go():
                with mock.patch("backend.pipeline.scan.scan_page", fake_scan_page):
                    result["out"] = scan_pages(
                        pages, max_workers=2, cache_path=cache_path,
                        on_progress=lambda p, e, from_cache: progress.append(p.page_number),
                    )

            t = threading.Thread(target=go, daemon=True)
            t.start()
            t.join(timeout=10)
            self.assertFalse(t.is_alive(), "scan_pages hung")

            scans, _ = result["out"]
            self.assertEqual(len(progress), 6)
            self.assertEqual(len(failed_pages(scans)), 1)
            self.assertIn(":3 - ", failed_pages(scans)[0])
            self.assertEqual(len(load_cache(cache_path)), 5)  # failed page is not cached
            self.assertFalse(cache_path.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()

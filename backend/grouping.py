"""Step 3 of the pipeline: group pages into forms ("Grouping" section of CLAUDE.md).

Default = new form. classify_page() applies the checks in order, stopping at the first
match; group_pages() walks an ordered list of pages (file order, then page order -
continuations may cross file boundaries) and uses it to split them into form groups.

Only PRINTED text decides grouping: barcodes and printed OCR words. Handwriting, highlighter
marks, and handwritten IDs are never used here - extracted values may still be carried as
informational fields (see page_facts_from_ocr), they just don't affect the split/continue
decision. Removed entirely from the decision (see project history for why): form titles,
"מספר התחייבות", patient stickers, national ID changes, case numbers, form codes, and the old
"fallback to continuation" rule - the new default already covers what that rule used to catch,
without needing a positive signal to split (a wrong merge is worse than a wrong split).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import backend.pipeline as pipeline
from backend.pipeline.scan import failed_pages, load_cache, scan_pages

PAGE_OF_RE = re.compile(r"(?:page|עמוד)\s*(\d+)\s*(?:of|מתוך)\s*(\d+)", re.IGNORECASE)
SIX_DIGIT_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
NATIONAL_ID_RE = re.compile(r'ת["\'\.]?ז[:.]?\s*(\d{7,10})')
LABEL_PROXIMITY_PX = 400  # how close an OCR word must be to "מרכזי" to "belong" to it


@dataclass
class PageFacts:
    """Everything classify_page() needs about one page, plus informational-only extras.
    Tests can build this directly with just the fields a given scenario needs."""

    source: str
    page_number: int
    sticker_candidates: list[str] = field(default_factory=list)  # decoded 6-digit barcodes
    central_number_in_text: bool = False  # printed 6-digit number directly next to "מרכזי"
    continuation_of: tuple[int, int] | None = None  # (n, m) parsed from "Page n of m"
    # informational only, never used by classify_page:
    central_confirmed: bool = False
    national_id: str | None = None


@dataclass
class Classification:
    kind: str  # "new_form" | "continuation"
    rule: str  # which rule fired, for debugging/tests
    flagged: bool = False
    reason: str = ""


@dataclass
class FormGroup:
    pages: list[PageFacts]
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    page_rules: list[str] = field(default_factory=list)  # classify_page().rule, one per page

    @property
    def page_range(self) -> str:
        first, last = self.pages[0], self.pages[-1]
        if first.source == last.source and len(self.pages) > 1:
            return f"{first.source} p{first.page_number}-{last.page_number}"
        if len(self.pages) == 1:
            return f"{first.source} p{first.page_number}"
        return f"{first.source} p{first.page_number} .. {last.source} p{last.page_number}"


def classify_page(facts: PageFacts, previous: PageFacts | None) -> Classification:
    """Apply the grouping rules in order, stopping at the first match. Default: new form."""
    if facts.sticker_candidates:
        return Classification("new_form", "sticker_candidate")

    if facts.central_number_in_text:
        return Classification("new_form", "central_number_next_to_label")

    if facts.continuation_of and facts.continuation_of[0] > 1:
        if previous is None:
            return Classification(
                "new_form",
                "page_of_total_no_previous",
                flagged=True,
                reason="says a later page of a form, but it's the first page of the batch",
            )
        return Classification("continuation", "page_of_total")

    return Classification("new_form", "default")


def group_pages(pages: list[PageFacts]) -> list[FormGroup]:
    groups: list[FormGroup] = []
    previous: PageFacts | None = None
    for facts in pages:
        result = classify_page(facts, previous)
        if result.kind == "continuation":
            groups[-1].pages.append(facts)
        else:
            groups.append(FormGroup(pages=[facts]))
        groups[-1].page_rules.append(result.rule)
        if result.flagged:
            groups[-1].flagged = True
            groups[-1].reasons.append(result.reason)
        previous = facts
    return groups


def _words_near(a, b, radius) -> bool:
    acx, acy = a.left + a.width / 2, a.top + a.height / 2
    bcx, bcy = b.left + b.width / 2, b.top + b.height / 2
    return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5 <= radius


def _central_number_in_text(words) -> bool:
    """A bare 6-digit OCR word sitting right next to a printed 'מרכזי' OCR word (not any
    stray 6-digit token elsewhere on the page - dates and report numbers must NOT trigger
    this, and this never looks at handwriting/highlighter color, only OCR'd text)."""
    labels = [w for w in words if w.text.strip() == "מרכזי"]
    if not labels:
        return False
    digit_words = [w for w in words if SIX_DIGIT_RE.fullmatch(w.text.strip())]
    return any(_words_near(label, d, LABEL_PROXIMITY_PX) for label in labels for d in digit_words)


def page_facts_from_ocr(page, words, barcodes, crop_words=()) -> PageFacts:
    """Bridge real pipeline output into PageFacts. crop_words are the rotated (90/270) OCR
    passes around each decoded barcode (backend.pipeline.barcode_crop_words) - folded into the
    "מרכזי" search since that label is often printed sideways next to the sticker.

    ponytail: national_id is a best-effort regex, kept only as informational metadata - it
    never affects grouping.
    """
    all_words = list(words) + list(crop_words)
    text = " ".join(w.text for w in words)

    sticker_candidates = sorted({b.value for b in barcodes if len(b.value) == 6 and b.value.isdigit()})

    page_of_match = PAGE_OF_RE.search(text)
    continuation_of = (
        (int(page_of_match.group(1)), int(page_of_match.group(2))) if page_of_match else None
    )

    national_id_match = NATIONAL_ID_RE.search(text)

    return PageFacts(
        source=page.source,
        page_number=page.page_number,
        sticker_candidates=sticker_candidates,
        central_number_in_text=_central_number_in_text(all_words),
        continuation_of=continuation_of,
        central_confirmed=any(b.is_central for b in barcodes),
        national_id=national_id_match.group(1) if national_id_match else None,
    )


def run(files: list[str], no_cache: bool = False) -> tuple[list[FormGroup], list[dict]]:
    """Run pipeline steps 2+3 on files and group the result. Returns (groups, skipped_pages).

    Step 2 reuses samples/debug/<first file stem>_scan.json if present (shared with
    backend.tools.scan_pdf) instead of recomputing OCR/barcodes, and writes it once at the
    end of the scan. --no-cache (no_cache=True) ignores it. Failed pages are printed.
    """
    pages, skipped = pipeline.split_files(files)
    cache_path = Path("samples/debug") / f"{Path(files[0]).stem}_scan.json"
    cache = {} if no_cache else load_cache(cache_path)
    scans, _ = scan_pages(pages, cache=cache, no_cache=no_cache, cache_path=cache_path)
    for line in failed_pages(scans):
        print(f"FAILED page: {line}")

    facts = []
    for page, scan in zip(pages, scans):
        main_words = [w for w in scan.words if w.rotation == 0]
        crop_words = [w for w in scan.words if w.rotation != 0]
        facts.append(page_facts_from_ocr(page, main_words, scan.barcodes, crop_words))
    return group_pages(facts), skipped

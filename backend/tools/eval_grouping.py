"""CLI: python -m backend.tools.eval_grouping [file1 ...]

Runs grouping on each file (default: both files in samples/grouping_truth.json) and
compares the result against the ground truth there. Prints expected vs actual groups per
file, then two counts: wrong merges (two true forms landed in one actual group - the
dangerous direction, pages silently merged) and wrong splits (one true form landed in
several actual groups), counted separately since they are not equally bad.
"""

import json
import sys
from pathlib import Path

from backend.grouping import run

TRUTH_PATH = Path("samples/grouping_truth.json")


def _group_id_per_page(ranges: list[tuple[int, int]], page_count: int) -> list[int]:
    ids = [-1] * (page_count + 1)  # 1-indexed, index 0 unused
    for group_id, (start, end) in enumerate(ranges):
        for page in range(start, end + 1):
            ids[page] = group_id
    return ids


def compare(true_ranges, actual_ranges, page_count: int) -> tuple[int, int]:
    true_id = _group_id_per_page(true_ranges, page_count)
    actual_id = _group_id_per_page(actual_ranges, page_count)

    wrong_merges = 0
    for start, end in actual_ranges:
        true_ids_spanned = {true_id[p] for p in range(start, end + 1)}
        if len(true_ids_spanned) > 1:
            wrong_merges += 1

    wrong_splits = 0
    for start, end in true_ranges:
        actual_ids_spanned = {actual_id[p] for p in range(start, end + 1)}
        if len(actual_ids_spanned) > 1:
            wrong_splits += 1

    return wrong_merges, wrong_splits


def main(argv: list[str]) -> int:
    truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    files = argv or [name for name in truth if not name.startswith("_")]

    total_merges = total_splits = 0
    for file in files:
        name = Path(file).name
        if name not in truth:
            print(f"{name}: no ground truth entry, skipping")
            continue

        path = file if Path(file).exists() else str(Path("samples") / name)
        groups, skipped = run([path])
        actual_ranges = [(g.pages[0].page_number, g.pages[-1].page_number) for g in groups]
        true_ranges = [tuple(r) for r in truth[name]["groups"]]

        print(f"\n{name}")
        if skipped:
            print(f"  skipped blank pages: {[s['page_number'] for s in skipped]}")
        print(f"  expected ({len(true_ranges)} forms): {true_ranges}")
        print(f"  actual   ({len(actual_ranges)} forms): {actual_ranges}")

        merges, splits = compare(true_ranges, actual_ranges, truth[name]["page_count"])
        total_merges += merges
        total_splits += splits
        print(f"  wrong merges: {merges}  (two true forms in one group - the dangerous direction)")
        print(f"  wrong splits: {splits}  (one true form spread across several groups)")

    print(f"\nTOTAL wrong merges: {total_merges}, wrong splits: {total_splits}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""CLI: python -m backend.tools.group_pdf <file1> [<file2> ...]

Runs pipeline steps 2+3 (per-page OCR/barcodes, then grouping into forms) and prints one
line per form group: its pages as file:page, the rule that decided each page, and any
flags. Blank pages are reported too (skipped), not dropped silently. Writes the full
result to samples/debug/<first file stem>_groups.json.
"""

import argparse
import json
import sys
from pathlib import Path

from backend.grouping import FormGroup, run

DEBUG_DIR = Path("samples/debug")


def _format_group_line(index: int, group: FormGroup) -> str:
    page_list = ", ".join(f"{p.source}:{p.page_number}" for p in group.pages)
    rules = ", ".join(group.page_rules)
    status = f"flagged ({', '.join(group.reasons)})" if group.flagged else "ok"
    return f"form {index} [{status}] pages=[{page_list}] rules=[{rules}]"


def _group_to_dict(group: FormGroup) -> dict:
    return {
        "flagged": group.flagged,
        "reasons": group.reasons,
        "pages": [
            {
                "source": p.source,
                "page_number": p.page_number,
                "sticker_candidates": p.sticker_candidates,
                "central_confirmed": p.central_confirmed,
                "national_id": p.national_id,
            }
            for p in group.pages
        ],
        "page_rules": group.page_rules,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.tools.group_pdf")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--no-cache", action="store_true", help="ignore the step-2 scan cache, recompute everything")
    args = parser.parse_args(argv)

    groups, skipped = run(args.files, no_cache=args.no_cache)
    for i, group in enumerate(groups, start=1):
        print(_format_group_line(i, group))
    if skipped:
        print(f"skipped {len(skipped)} blank page(s): " + ", ".join(f"{s['source']}:{s['page_number']}" for s in skipped))

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEBUG_DIR / f"{Path(args.files[0]).stem}_groups.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"groups": [_group_to_dict(g) for g in groups], "skipped_pages": skipped},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"wrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

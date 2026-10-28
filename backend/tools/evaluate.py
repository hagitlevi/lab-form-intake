"""CLI: python -m backend.tools.evaluate [--fresh] [--files f1.pdf f2.pdf ...] [--init-truth]

Measures the pipeline against the hand-filled samples/ground_truth.json and writes
samples/eval_report.md. It only measures: it runs the same steps as backend.batch_runner
(split -> scan (stage-2 OCR cache allowed) -> grouping -> one AI call per form group ->
confidence) without the DB, and changes nothing in the pipeline.

--fresh      never read or write the AI extract cache (AI_CACHE is otherwise honoured as usual)
--files      only these files (names under samples/ or paths); default: every file in the truth
--init-truth write the empty samples/ground_truth.json template from samples/grouping_truth.json
             (refuses to overwrite an existing file; fields are never filled from pipeline output)

AI calls are made only for form groups that contain a page of a "filled": true truth form -
unfilled entries are skipped by the evaluator, so calling the AI for them would only cost money.
Grouping is evaluated for every truth entry of the selected files, filled or not.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import cv2

from backend.grouping import group_pages, page_facts_from_ocr
from backend.pipeline import split_files
from backend.pipeline import extract as extract_mod
from backend.pipeline.confidence import PageData, score_form, sender_fuzzy_match
from backend.pipeline.extract import extract_form
from backend.pipeline.normalize import normalize_date_iso, normalize_digits, normalize_hebrew_name, pad_national_id
from backend.pipeline.scan import load_cache, scan_pages

SAMPLES = Path("samples")
TRUTH_PATH = SAMPLES / "ground_truth.json"
GROUPING_TRUTH_PATH = SAMPLES / "grouping_truth.json"
REPORT_PATH = SAMPLES / "eval_report.md"
JPEG_QUALITY = 90  # same as backend.batch_runner
AI_WORKERS = int(os.environ.get("AI_WORKERS", 3))

SCALARS = ["first_name", "last_name", "national_id", "received_date", "sender"]
LISTS = ["central_numbers", "commitment_numbers"]
FIELDS = ["first_name", "last_name", "national_id", "central_numbers", "received_date", "sender", "commitment_numbers"]
CATS = ["correct_safe", "correct_review", "wrong_review", "wrong_safe", "both_empty", "invented", "missed"]


# --- ground truth template -------------------------------------------------------

def init_truth() -> None:
    if TRUTH_PATH.exists():
        print(f"{TRUTH_PATH} already exists - not overwriting")
        return
    src = json.loads(GROUPING_TRUTH_PATH.read_text(encoding="utf-8"))
    forms = []
    for file, entry in src.items():
        if file.startswith("_"):
            continue
        for first, last in entry["groups"]:
            forms.append({
                "file": file, "pages": list(range(first, last + 1)), "filled": False,
                "first_name": None, "last_name": None, "national_id": None, "central_numbers": [],
                "received_date": None, "sender": None, "commitment_numbers": [], "notes": "",
            })
    TRUTH_PATH.write_text(json.dumps({"forms": forms}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {TRUTH_PATH}: {len(forms)} forms, all filled=false")


# --- normalization + comparison (unit-tested) --------------------------------------

def _norm_date(s: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
        if not m:
            return s
        d, mo, y = (int(x) for x in m.groups())
    return normalize_date_iso(d, mo, y) or s


def norm(field: str, value) -> str | None:
    """Comparable form of a truth or pipeline value; None = empty."""
    s = "" if value is None else str(value).strip()
    if not s:
        return None
    if field == "national_id":
        return pad_national_id(s) or None
    if field == "received_date":
        return _norm_date(s)
    if field == "sender":
        return sender_fuzzy_match(s) or s
    if field == "central_numbers":
        return normalize_digits(s) or None
    if field == "commitment_numbers":
        return normalize_digits(s, keep_leading_letter=True) or None
    return normalize_hebrew_name(" ".join(s.split()))


def compare_scalar(expected: str | None, got: str | None, state: str) -> str:
    """Both arguments already normalized (None = empty)."""
    if expected is None:
        return "both_empty" if got is None else "invented"
    if got is None:
        return "missed"
    if expected == got:
        return f"correct_{state}"
    return f"wrong_{state}"


def compare_list(expected: set[str], got: dict[str, str]) -> list[tuple[str, str | None, str | None, str]]:
    """expected: normalized truth items; got: normalized pipeline item -> state.
    Returns (category, expected_item, got_item, state), one per item (or one both_empty)."""
    if not expected and not got:
        return [("both_empty", None, None, "review")]
    out = []
    for item in sorted(expected | set(got)):
        if item in expected and item in got:
            out.append((f"correct_{got[item]}", item, item, got[item]))
        elif item in got:
            cat = "invented" if not expected else f"wrong_{got[item]}"
            out.append((cat, None if not expected else "(not this item)", item, got[item]))
        else:
            out.append(("missed", item, None, "review"))
    return out


def classify_grouping(truth_pages: list[set], groups: list[set]) -> list[str]:
    """truth_pages / groups: sets of (file, page). One label per truth form, in order:
    exact | merged (a group touching it also holds other pages) | split | missing."""
    labels = []
    for t in truth_pages:
        touching = [g for g in groups if g & t]
        if not touching:
            labels.append("missing")
        elif any(g - t for g in touching):
            labels.append("merged")
        elif len(touching) > 1:
            labels.append("split")
        else:
            labels.append("exact")
    return labels


def form_records(tf: dict, conf) -> list[dict]:
    """Compare one filled truth form with its pipeline FormConfidence (None = no pipeline form)."""
    sender = norm("sender", tf.get("sender")) or "(no sender)"
    out = []

    def add(field, cat, expected, got, state, reasons):
        out.append({"form": f"{tf['file']} p{tf['pages'][0]}", "sender": sender, "field": field, "cat": cat,
                    "expected": expected, "got": got, "state": state, "reasons": reasons})

    for f in SCALARS:
        res = getattr(conf, f) if conf else None
        got = norm(f, res.value) if res else None
        state = res.state if res else "review"
        exp = norm(f, tf.get(f))
        add(f, compare_scalar(exp, got, state), exp, got, state, res.reasons if res else [])
    for f in LISTS:
        results = getattr(conf, f) if conf else []
        got = {}
        reasons = {}
        for r in results:
            v = norm(f, r.value)
            if v is not None:  # a duplicated item is safe only if every copy is
                got[v] = "review" if r.state == "review" or got.get(v) == "review" else "safe"
                reasons[v] = r.reasons
        expected = {v for v in (norm(f, x) for x in tf.get(f) or []) if v}
        for cat, e, g, state in compare_list(expected, got):
            add(f, cat, e, g, state, reasons.get(g, []) if g else [])
    return out


# --- pipeline run ------------------------------------------------------------------

def run_pipeline(paths: list[str], filled_pages: set, fresh: bool) -> dict:
    """Steps 1-5 as the batch runner does them. Returns pipeline forms, groups, skipped pages,
    timings and counters."""
    today = date.today()
    facts, pagedata, jpegs, skipped = [], {}, {}, []
    for path in paths:
        pages, skip = split_files([path])
        skipped += [(Path(s["source"]).name, s["page_number"]) for s in skip]
        cache_path = Path("samples/debug") / f"{Path(path).stem}_scan.json"
        scans, _ = scan_pages(pages, cache=load_cache(cache_path), cache_path=cache_path)
        for page, scan in zip(pages, scans):
            main_words = [w for w in scan.words if w.rotation == 0]
            crop_words = [w for w in scan.words if w.rotation != 0]
            facts.append(page_facts_from_ocr(page, main_words, scan.barcodes, crop_words))
            key = (page.source, page.page_number)
            pagedata[key] = PageData(page.source, page.page_number, main_words, scan.barcodes)
            ok, buf = cv2.imencode(".jpg", scan.color, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            jpegs[key] = buf.tobytes()
        del pages, scans
    groups = group_pages(facts)

    hits = _CacheHits()
    logging.getLogger(extract_mod.__name__).addHandler(hits)
    logging.getLogger(extract_mod.__name__).setLevel(logging.INFO)
    ai_seconds, results = 0.0, {}

    def do_group(gi: int):
        g = groups[gi]
        t0 = time.monotonic()
        forms = extract_form([jpegs[(p.source, p.page_number)] for p in g.pages], upload_date=today,
                             no_cache=fresh, group_flagged=g.flagged)
        return gi, forms, time.monotonic() - t0

    todo = [i for i, g in enumerate(groups)
            if any((Path(p.source).name, p.page_number) in filled_pages for p in g.pages)]
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as ex:
        for gi, forms, secs in ex.map(do_group, todo):
            ai_seconds += secs
            results[gi] = forms

    out_forms = []
    for gi, forms in results.items():
        g = groups[gi]
        for form in forms:
            idx = [i for i in form.page_indexes if i < len(g.pages)] or [0]
            error = form.notes if (form.notes or "").startswith("extraction_error") else None
            try:
                conf = score_form(form, [pagedata[(g.pages[i].source, g.pages[i].page_number)] for i in form.page_indexes], today)
            except Exception as exc:
                conf, error = None, f"{type(exc).__name__}: {exc}"
            first = g.pages[idx[0]]
            out_forms.append({"key": (Path(first.source).name, first.page_number), "conf": conf, "error": error})
    return {"forms": out_forms, "groups": groups, "skipped": skipped, "ai_seconds": ai_seconds,
            "ai_calls": len(todo), "ai_groups_total": len(groups), "cache_hits": hits.n}


class _CacheHits(logging.Handler):
    def __init__(self):
        super().__init__()
        self.n = 0

    def emit(self, record):
        if "cache hit" in record.getMessage():
            self.n += 1


# --- report ------------------------------------------------------------------------

def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def _row(label: str, recs: list[dict]) -> str:
    c = Counter(r["cat"] for r in recs)
    filled = c["correct_safe"] + c["correct_review"] + c["wrong_safe"] + c["wrong_review"] + c["invented"]
    flagged = sum(1 for r in recs if r["state"] == "review" and r["cat"] in
                  ("correct_review", "wrong_review", "invented"))
    correct = c["correct_safe"] + c["correct_review"] + c["both_empty"]
    cells = [c[k] for k in CATS]
    return f"| {label} | {len(recs)} | " + " | ".join(str(x) for x in cells) + \
           f" | {_pct(correct, len(recs))} | {_pct(flagged, filled)} |"


def table(recs: list[dict]) -> str:
    head = ("| field | items | correct+safe | correct+review | wrong+review | **WRONG+SAFE** | "
            "empty/empty | invented | missed | accuracy | % values flagged |\n" + "|---" * 11 + "|")
    rows = [_row(f, [r for r in recs if r["field"] == f]) for f in FIELDS]
    return "\n".join([head, *rows, _row("**ALL**", recs)])


def build_report(args_files, truth_forms, filled, res, truth_labels, records, total_s, fresh) -> str:
    labels = Counter(truth_labels)
    truth_pages_all = set().union(*(set((f["file"], p) for p in f["pages"]) for f in truth_forms)) if truth_forms else set()
    extra = sum(1 for g in res["groups"] if not any((Path(p.source).name, p.page_number) in truth_pages_all for p in g.pages))
    if fresh:
        cache = "off (--fresh)"
    elif extract_mod.AI_CACHE:
        cache = f"ON - {res['cache_hits']} of {res['ai_calls']} AI call(s) came from the cache"
    else:
        cache = "off (AI_CACHE not set)"
    errors = [f for f in res["forms"] if f["error"]]
    L = [
        "# Evaluation report", "",
        f"- date: {datetime.now():%Y-%m-%d %H:%M}",
        f"- model: {extract_mod.ANTHROPIC_MODEL} (prompt {extract_mod.PROMPT_VERSION})",
        f"- AI cache: {cache}",
        f"- stage-2 OCR cache: allowed (samples/debug/*_scan.json)",
        f"- files: {', '.join(args_files)}",
        f"- truth forms in these files: {len(truth_forms)}; evaluated (filled=true): {len(filled)}",
        f"- AI calls: {res['ai_calls']} of {res['ai_groups_total']} form groups (groups without a filled truth form are skipped)",
        f"- total time: {total_s:.1f}s; AI time: {res['ai_seconds']:.1f}s (sum of the AI calls, they run {AI_WORKERS} in parallel)",
        f"- pipeline forms with an error: {len(errors)}" + (": " + "; ".join(f"{k} {e}" for (k, _), e in
                                                              ((f['key'], f['error']) for f in errors)) if errors else ""),
        f"- blank pages skipped: {len(res['skipped'])}", "",
        "## Grouping", "",
        f"Truth forms: {len(truth_forms)} (compared with {len(res['groups'])} pipeline groups, all filled or not).", "",
        "| result | truth forms |", "|---|---|",
        f"| matched exactly | {labels['exact']} |",
        f"| split (pages spread over several groups) | {labels['split']} |",
        f"| merged (group also holds another truth form; takes precedence over split) | {labels['merged']} |",
        f"| missing (no page of it in any group) | {labels['missing']} |",
        f"| pipeline groups with no truth page | {extra} |", "",
    ]
    bad = [(f, l) for f, l in zip(truth_forms, truth_labels) if l != "exact"]
    if bad:
        L += ["Not matched exactly: " + ", ".join(f"{f['file']} p{f['pages'][0]}-{f['pages'][-1]} ({l})" for f, l in bad), ""]
    L += ["## Fields - overall", ""]
    if not records:
        L += ["No filled truth forms - nothing to compare. Fill samples/ground_truth.json (see GROUND_TRUTH_README.md).", ""]
    else:
        L += [table(records), "", "`% values flagged` = of the values the pipeline produced (correct, wrong or invented), "
              "how many were orange. `accuracy` counts correct + empty/empty over all items. "
              "List fields are counted per item; a swapped item counts as one wrong/invented and one missed.", ""]
        L += ["## Fields - per sender (sender as in the truth)", ""]
        for s in sorted({r["sender"] for r in records}):
            sub = [r for r in records if r["sender"] == s]
            L += [f"### {s} ({len({r['form'] for r in sub})} forms)", "", table(sub), ""]
        L += ["## WRONG+SAFE and invented values", ""]
        crit = [r for r in records if r["cat"] in ("wrong_safe", "invented")]
        if not crit:
            L += ["None."]
        else:
            L += ["| kind | form | field | expected | got | reason |", "|---|---|---|---|---|---|"]
            for r in crit:
                kind = "WRONG+SAFE" if r["cat"] == "wrong_safe" else f"invented ({r['state']})"
                L.append(f"| {kind} | {r['form']} | {r['field']} | {r['expected']} | {r['got']} | "
                         f"{'; '.join(r['reasons']) or '-'} |")
        L.append("")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.tools.evaluate")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--init-truth", action="store_true")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.init_truth:
        init_truth()
        return 0
    if not TRUTH_PATH.exists():
        print(f"{TRUTH_PATH} missing - run with --init-truth first")
        return 1

    all_truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))["forms"]
    names = [Path(f).name for f in args.files] if args.files else list(dict.fromkeys(f["file"] for f in all_truth))
    paths = [f if Path(f).exists() else str(SAMPLES / Path(f).name) for f in (args.files or names)]
    truth_forms = [f for f in all_truth if f["file"] in names]
    filled = [f for f in truth_forms if f.get("filled")]
    filled_pages = {(f["file"], p) for f in filled for p in f["pages"]}

    t0 = time.monotonic()
    res = run_pipeline(paths, filled_pages, args.fresh)
    skipped = set(res["skipped"])

    truth_sets = [{(f["file"], p) for p in f["pages"]} - skipped for f in truth_forms]
    group_sets = [{(Path(p.source).name, p.page_number) for p in g.pages} for g in res["groups"]]
    truth_labels = classify_grouping(truth_sets, group_sets)

    by_key = {}
    for pf in res["forms"]:
        by_key.setdefault(pf["key"], pf)
    records = []
    for tf in filled:
        pf = by_key.get((tf["file"], tf["pages"][0]))
        records += form_records(tf, pf["conf"] if pf else None)

    report = build_report(names, truth_forms, filled, res, truth_labels, records, time.monotonic() - t0, args.fresh)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

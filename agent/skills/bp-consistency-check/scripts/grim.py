#!/usr/bin/env python3
"""GRIM test: is a reported mean possible given n integer-valued observations?

For n participants answering k integer items, every possible mean is s / (n*k) for an
integer s. A mean reported to d decimals is testable when n*k < 10**d.
(Brown & Heathers, "The GRIM test".)

Usage:
    python grim.py bp_out/grim_input.csv --out bp_out
    python grim.py --mean 3.48 --n 25 [--items 1]

CSV columns: label, mean, n, items (optional, default 1), location (optional).
Keep `mean` exactly as printed in the paper (trailing zeros matter: 3.40 ≠ 3.4).
Outputs: grim.findings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from decimal import ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path


def grim(mean_str: str, n: int, items: int = 1):
    """Return True (possible), False (impossible) or None (not testable)."""
    m = Decimal(mean_str.strip())
    exp = m.as_tuple().exponent
    d = -exp if isinstance(exp, int) and exp < 0 else 0
    total_n = n * items
    if total_n <= 0 or total_n >= 10 ** d:
        return None
    q = Decimal(1).scaleb(-d)
    approx = float(m) * total_n
    for s in range(math.floor(approx) - 1, math.ceil(approx) + 2):
        val = Decimal(s) / Decimal(total_n)
        for mode in (ROUND_HALF_UP, ROUND_HALF_DOWN, ROUND_HALF_EVEN):
            if val.quantize(q, rounding=mode) == m:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", type=Path)
    ap.add_argument("--mean")
    ap.add_argument("--n", type=int)
    ap.add_argument("--items", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    rows = []
    if args.csv:
        with open(args.csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append({
                    "label": r.get("label", ""), "mean": r["mean"], "n": int(float(r["n"])),
                    "items": int(float(r.get("items") or 1)), "location": r.get("location", ""),
                })
    elif args.mean and args.n:
        rows.append({"label": "cli", "mean": args.mean, "n": args.n, "items": args.items, "location": ""})
    else:
        ap.error("give a CSV file or --mean and --n")

    findings, n_ok, n_bad, n_untestable = [], 0, 0, 0
    for r in rows:
        res = grim(r["mean"], r["n"], r["items"])
        status = {True: "possible", False: "IMPOSSIBLE", None: "not testable"}[res]
        print(f"{r['label']:<30} mean={r['mean']:<8} n={r['n']:<6} items={r['items']:<3} -> {status}")
        if res is None:
            n_untestable += 1
        elif res:
            n_ok += 1
        else:
            n_bad += 1
            findings.append({
                "module": "consistency", "type": "grim_inconsistency", "severity": "yellow", "claim_ids": [],
                "location": r["location"] or r["label"],
                "evidence": (f"Mean {r['mean']} cannot arise from {r['n']} participants x {r['items']} integer item(s) "
                             f"(GRIM). Assumes integer responses and no missing data."),
                "source": "script:grim.py",
                "question_for_authors": "Was n different for this measure (missing responses, exclusions), or is the mean mistyped?",
            })
    if n_ok:
        findings.append({
            "module": "consistency", "type": "grim_inconsistency", "severity": "green", "claim_ids": [],
            "location": "reported means", "evidence": f"{n_ok} testable mean(s) passed GRIM; {n_untestable} not testable.",
            "source": "script:grim.py", "question_for_authors": None,
        })
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "grim.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"possible={n_ok} impossible={n_bad} not_testable={n_untestable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

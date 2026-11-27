#!/usr/bin/env python3
"""Recompute p-values from reported test statistics (statcheck-style, lightweight).

Recognised formats (APA-like), e.g.
    t(28) = 2.20, p = .036        F(2, 45) = 3.91, p < .05
    chi2(1) = 4.10, p = .043      χ2(3, N = 120) = 9.1, p = .028
    r(48) = .31, p = .029         z = 2.45, p = .014

The reported test statistic is assumed to be rounded, so the recomputed p-value is an
interval; a reported p is "consistent" if it is compatible with that interval at the
reported precision. A "decision error" is an inconsistency that flips significance at alpha.

Usage:
    python statcheck_lite.py bp_work/paper.md --out bp_out [--alpha 0.05]
Outputs: statcheck_table.csv, statcheck.findings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

from scipy import stats

NORMALISE = str.maketrans({"−": "-", "–": "-", "—": "-", " ": " ", " ": " ", "≤": "<", "≥": ">"})
NUM = r"-?\d*\.?\d+"
PVAL = r"p\s*(?P<comp>[<>=])\s*(?P<p>\d*\.\d+(?:[eE]-?\d+)?|\d(?:\.\d+)?[eE]-?\d+|[01])"
SEP = r"\s*[,;]\s*"
PATTERNS = [
    ("t", re.compile(rf"\bt\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>{NUM}){SEP}{PVAL}")),
    ("F", re.compile(rf"\bF\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*,\s*(?P<df2>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>{NUM}){SEP}{PVAL}")),
    ("chi2", re.compile(
        rf"(?:χ\s*2|χ²|[Cc]hi\s*-?\s*[Ss]quared?|[Cc]hi\s*\^?\s*2|X2)\s*\(\s*(?P<df1>\d+)(?:\s*,\s*[Nn]\s*=\s*\d+)?\s*\)"
        rf"\s*=\s*(?P<stat>{NUM}){SEP}{PVAL}")),
    ("r", re.compile(rf"\br\s*\(\s*(?P<df1>\d+)\s*\)\s*=\s*(?P<stat>{NUM}){SEP}{PVAL}")),
    ("z", re.compile(rf"\b[zZ]\s*=\s*(?P<stat>{NUM}){SEP}{PVAL}")),
]
ONE_TAILED = re.compile(r"one[- ]?(tailed|sided)", re.I)
PAGE_MARK = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.I)


def decimals(s: str) -> int:
    s = s.lower().split("e")[0]
    return len(s.split(".")[1]) if "." in s else 0


def exponent(s: str) -> int:
    s = s.lower()
    return int(s.split("e")[1]) if "e" in s else 0


def p_of(test: str, x: float, df1: float | None, df2: float | None) -> float:
    if test == "t":
        return 2 * stats.t.sf(abs(x), df1)
    if test == "F":
        return stats.f.sf(x, df1, df2)
    if test == "chi2":
        return stats.chi2.sf(x, df1)
    if test == "r":
        r = min(abs(x), 0.999999)
        t = r * math.sqrt(df1 / (1 - r * r))
        return 2 * stats.t.sf(t, df1)
    if test == "z":
        return 2 * stats.norm.sf(abs(x))
    raise ValueError(test)


def p_interval(test, stat_str, df1, df2):
    """p-value interval implied by rounding of the reported statistic."""
    x = abs(float(stat_str))
    half = 0.5 * 10 ** (-decimals(stat_str))
    lo_x, hi_x = max(x - half, 0.0), x + half
    if test == "r":
        hi_x = min(hi_x, 0.999999)
    # p decreases as |statistic| grows
    return p_of(test, hi_x, df1, df2), p_of(test, lo_x, df1, df2), p_of(test, x, df1, df2)


def consistent(comp, p_rep, p_str, p_lo, p_hi) -> bool:
    if comp == "=":
        tol = 0.5 * 10 ** (-decimals(p_str)) * 10 ** exponent(p_str)
        return p_lo - tol <= p_rep <= p_hi + tol
    if comp == "<":
        return p_lo < p_rep
    return p_hi > p_rep  # ">"


def location_of(text: str, pos: int) -> str:
    page = None
    for m in PAGE_MARK.finditer(text, 0, pos):
        page = m.group(1)
    line = text.count("\n", 0, pos) + 1
    return f"p. {page}, line {line}" if page else f"line {line}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args()

    text = args.text.read_text(encoding="utf-8", errors="replace").translate(NORMALISE)
    rows, findings = [], []
    matches = sorted(((m.start(), test, m) for test, pat in PATTERNS for m in pat.finditer(text)), key=lambda t: t[0])
    for _, test, m in matches:
        g = m.groupdict()
        df1 = float(g["df1"]) if g.get("df1") else None
        df2 = float(g["df2"]) if g.get("df2") else None
        p_str, comp = g["p"], g["comp"]
        p_rep = float(p_str)
        p_lo, p_hi, p_mid = p_interval(test, g["stat"], df1, df2)
        ok = consistent(comp, p_rep, p_str, p_lo, p_hi)
        note = ""
        ctx = text[max(0, m.start() - 200): m.end() + 200]
        if not ok and test in ("t", "z", "r") and ONE_TAILED.search(ctx):
            if consistent(comp, p_rep, p_str, p_lo / 2, p_hi / 2):
                ok, note = True, "consistent as one-tailed test"
        a = args.alpha
        rep_sig = (comp in "=<" and p_rep <= a)
        rep_nonsig = (comp in "=>" and p_rep >= a) and not (comp == "=" and p_rep == a)
        decision = (not ok) and ((rep_sig and p_lo >= a) or (rep_nonsig and p_hi < a))
        loc = location_of(text, m.start())
        rows.append({
            "test": test, "raw": " ".join(m.group(0).split()), "location": loc,
            "df1": df1, "df2": df2, "stat": g["stat"], "comp": comp, "p_reported": p_str,
            "p_recomputed": f"{p_mid:.4g}", "p_range": f"[{p_lo:.4g}, {p_hi:.4g}]",
            "consistent": ok, "decision_error": decision, "note": note,
        })
        if not ok:
            findings.append({
                "module": "consistency",
                "type": "stat_decision_error" if decision else "stat_inconsistency",
                "severity": "red" if decision else "yellow",
                "claim_ids": [],
                "location": loc,
                "evidence": (f"Reported '{rows[-1]['raw']}'; recomputed two-tailed p = {p_mid:.4g} "
                             f"(range {rows[-1]['p_range']} given rounding of the statistic)."
                             + (" Significance at alpha = %.2g flips." % a if decision else "")),
                "source": "script:statcheck_lite.py",
                "question_for_authors": "Is the test statistic, the degrees of freedom or the p-value mistyped, or was a one-tailed/corrected test used?",
            })

    n_ok = sum(r["consistent"] for r in rows)
    if rows and n_ok:
        findings.append({
            "module": "consistency", "type": "stat_inconsistency", "severity": "green", "claim_ids": [],
            "location": "whole text", "evidence": f"{n_ok} of {len(rows)} reported test statistics are consistent with their p-values.",
            "source": "script:statcheck_lite.py", "question_for_authors": None,
        })

    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "statcheck_table.csv", "w", newline="", encoding="utf-8") as fh:
        fields = ["test", "raw", "location", "df1", "df2", "stat", "comp", "p_reported", "p_recomputed",
                  "p_range", "consistent", "decision_error", "note"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (args.out / "statcheck.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"statistics found={len(rows)} consistent={n_ok} "
          f"inconsistent={len(rows) - n_ok} decision_errors={sum(r['decision_error'] for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

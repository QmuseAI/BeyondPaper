#!/usr/bin/env python3
"""Check effect estimates against their confidence intervals and p-values.

Medical and meta-analysis papers usually report "estimate (95% CI lo, hi; p = x)" rather than
APA test statistics, so statcheck-style checks miss them. For each such report this script checks:

1. the estimate lies inside its CI (allowing for rounding);
2. the CI and the p-value agree on significance: a 95% CI that excludes the null (0 for
   differences, 1 for ratios: OR, RR, HR, IRR) implies p < .05, and vice versa;
3. the p-value roughly matches the one implied by the CI (Altman & Bland, BMJ 2011:
   SE = (hi - lo) / (2 x 1.96), z = estimate / SE; ratios on the log scale). Only gross gaps
   (more than 3x, or significance flips) are flagged, because the approximation assumes a
   symmetric Wald interval.

Accuracy-type metrics (AUC, sensitivity, accuracy, C-index, percentages) only get check 1.

It also records which interval methods the paper (and plain-text supplements in bp_work/si/) names,
e.g. Wilson, Clopper-Pearson, Sison-Glaz, bootstrap BCa. build_passport.py uses ci_methods.json to keep two
nearby intervals for the same quantity grey when they can come from two disclosed methods (P03: Abstract
[20, 59] vs Results [19, 57], Sison-Glaz and Wilson, both named in the supplement).

Usage:
    python ci_check.py bp_work/paper.md [--si bp_work/si] --out bp_out
Outputs: ci_table.csv, ci.findings.json, ci_methods.json
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

NORMALISE = str.maketrans({"−": "-", "–": "-", "—": "-", "·": ".", " ": " ", " ": " ",
                           "≤": "<", "≥": ">"})
NUM = r"-?\d*\.?\d+"
CI_WORD = r"(?:95\s*%\s*)?(?:CI|Cl|C\.I\.|confidence\s+interval)(?:\s*\(CI\))?"
PATTERN = re.compile(
    rf"(?P<label>\b(?:SMD|WMD|MD|aOR|OR|RR|aHR|HR|IRR|RD|ARD|d|g|AUROC|AUC|C-index|beta|β|b|r|"
    rf"sensitivity|specificity|accuracy)\b[\s:=]{{0,4}})?"
    rf"(?P<est>{NUM})\s*(?P<pct>%)?\s*[,;]?\s*(?:[\(\[]\s*{CI_WORD}?|{CI_WORD})\s*[:,]?\s*[\(\[]?\s*"
    rf"(?P<lo>{NUM})\s*%?\s*(?:,|;|to|-(?=\s*-?\d)|\s-\s)\s*(?P<hi>{NUM})\s*%?\s*[\)\]]?"
    rf"(?P<tail>[^\n]{{0,40}})",
    re.I)
HTML_CELL = re.compile(r"</?(?:td|th)\b[^>]*>", re.I)
HTML_ROW = re.compile(r"</?tr\b[^>]*>", re.I)
HTML_INLINE = re.compile(r"</?(?:p|div|table|tbody|thead|tfoot|br|span|b|i|em|strong|sup|sub|u|colgroup|col)\b[^>]*/?>", re.I)


def flatten_html(text: str) -> str:
    """HTML tables inside markdown (docx/pandoc output) split "<p>.571</p><p>(-.544,.032)</p>" across tags, so the
    estimate and its CI were never paired (Campbell et al. 2017, Table 3). Keep page markers (HTML comments)."""
    text = re.sub(r"</p>\s*<p\b[^>]*>", " ", text, flags=re.I)
    text = HTML_CELL.sub(" | ", text)
    text = HTML_ROW.sub("\n", text)
    return HTML_INLINE.sub(" ", text)


# v1.10.3: tables whose note says "95% CI presented in parentheses" print a bare "b (lo, hi)" in each cell
CI_IN_PARENS = re.compile(r"(?:\bCI|\bC\.I\.|confidence\s+intervals?)[^.\n]{0,40}\b(?:in|within)\s+(?:parenthes|brackets)", re.I)
BARE_CELL = re.compile(rf"(?:^|\|)\s*(?P<est>{NUM})\s*[*+†]*\s*[\(\[]\s*(?P<lo>{NUM})\s*,\s*(?P<hi>{NUM})\s*[\)\]]\s*[*+†]*\s*(?=\||$)",
                       re.M)


PVAL = re.compile(r"\bp\s*(?:=\s*)?(?P<comp>[<>=])\s*(?P<p>\d*\.\d+|[01](?:\.0+)?)", re.I)
RATIO = {"or", "aor", "rr", "hr", "ahr", "irr"}
ACCURACY = {"auroc", "auc", "c-index", "sensitivity", "specificity", "accuracy"}
PAGE_MARK = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.I)
CI_METHODS = [
    ("Wilson", r"\bwilson(?:'s)?\s+(?:score\s+)?(?:method|interval|CI|confidence)|wilson\s+score"),
    ("Clopper-Pearson / exact binomial", r"clopper[- ]pearson|exact\s+binomial|exact\s+(?:95\s*%\s*)?(?:CI|confidence)"),
    ("Agresti-Coull", r"agresti[- ]coull"),
    ("Jeffreys", r"jeffreys\s+(?:interval|prior|method)"),
    ("Wald", r"\bwald\s+(?:interval|method|CI|confidence)"),
    ("Sison-Glaz (multinomial)", r"sison[- ]glaz|multinomial\s+(?:confidence|CI|interval)"),
    ("Goodman (multinomial)", r"goodman(?:'s)?\s+(?:method|interval)"),
    ("Newcombe", r"newcombe"),
    ("bootstrap percentile / BCa", r"\bBCa\b|bias[- ]corrected\s+(?:and\s+accelerated|bootstrap)|percentile\s+bootstrap|bootstrap(?:ped)?\s+(?:95\s*%\s*)?(?:CI|confidence|percentile)"),
    ("profile likelihood", r"profile[- ]likelihood"),
    ("Hartung-Knapp", r"hartung[- ]knapp|knapp[- ]hartung"),
    ("Fieller", r"fieller"),
    ("mid-p", r"mid[- ]p\b"),
    ("delta method", r"delta\s+method"),
    ("Bayesian credible interval", r"credible\s+interval|highest\s+(?:posterior\s+)?density"),
]


def ci_methods(texts: dict[str, str]) -> list[dict]:
    found = []
    for name, pat in CI_METHODS:
        for src, t in texts.items():
            m = re.search(pat, t, re.I)
            if m:
                found.append({"method": name, "source": src,
                              "context": " ".join(t[max(0, m.start() - 80):m.end() + 80].split())})
                break
    return found


REVERSED = re.compile(rf"[\(\[]\s*(?P<a>{NUM})\s*(?P<sep>to|,|;|–|—)\s*(?P<b>{NUM})\s*[\)\]]")


def decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def location_of(text: str, pos: int) -> str:
    page = None
    for m in PAGE_MARK.finditer(text, 0, pos):
        page = m.group(1)
    line = text.count("\n", 0, pos) + 1
    return f"p. {page}, line {line}" if page else f"line {line}"


def implied_p(est: float, lo: float, hi: float, h: float, ratio: bool) -> tuple[float, float, float]:
    """p-value implied by the CI, with a range for rounding of the three numbers."""
    if ratio:
        f = lambda v: math.log(max(v, 1e-12))
        e, l, u = f(est), f(lo), f(hi)
        # rounding on the natural scale, mapped to the log scale (conservative)
        he = h / max(est, 1e-12); hl = h / max(lo, 1e-12); hu = h / max(hi, 1e-12)
    else:
        e, l, u, he, hl, hu = est, lo, hi, h, h, h
    width = u - l
    if width <= 0:
        return float("nan"), float("nan"), float("nan")
    se = width / (2 * 1.959964)
    se_min = max((width - hl - hu) / (2 * 1.959964), 1e-12)
    se_max = (width + hl + hu) / (2 * 1.959964)
    z = abs(e) / se
    z_hi = (abs(e) + he) / se_min
    z_lo = max(abs(e) - he, 0.0) / se_max
    p = 2 * stats.norm.sf(z)
    return p, 2 * stats.norm.sf(z_hi), 2 * stats.norm.sf(z_lo)


# v1.11.6: Cohen's d / Hedges' g printed next to two group means and SDs (B08-v1114: "M = 4.83, SD = 1.25 ... M = 4.24,
# SD = 1.42; Cohen's d = .64" while the pooled-SD d is 0.44 and the authors' stored mes() output says 0.44).
_MSD = r"(?<![A-Za-z])M\s*=\s*(-?\d*\.?\d+)\s*[,;]\s*SD\s*=\s*(\d*\.?\d+)"
D_FROM_MSD = re.compile(rf"{_MSD}(?P<mid>[^\n]{{0,260}}?){_MSD}(?P<gap>[^\n]{{0,60}}?)"
                        rf"(?:Cohen['’]?s\s*|Hedges['’]?\s*)?(?<![A-Za-z])(?P<lab>d|g)\s*=\s*(?P<d>-?\d*\.?\d+)")
WITHIN = re.compile(r"(?i)paired|within[- ]subject|repeated[- ]measures|pre[- ]?post|\bd_?z\b|\bd_?av\b|\bd_?rm\b|"
                    r"change score")


def effect_size_findings(text: str) -> list[dict]:
    """Recompute a two-group standardized mean difference from the printed means and SDs (pooled SD, equal-n form).

    Only gross gaps are flagged (more than 0.10 and more than 25 % of the recomputed value): unequal group sizes,
    Hedges' correction and rounding of the inputs move d by far less. Within-subject designs are skipped, because
    d_z or d_av can legitimately differ a lot from the between-groups formula.
    """
    out = []
    plain = text.translate(str.maketrans({"*": " ", "_": " "}))  # same length, so positions and lines still match
    for m in D_FROM_MSD.finditer(plain):
        if WITHIN.search(plain[max(0, m.start() - 300):m.end()]):
            continue
        try:
            m1, s1, m2, s2, d_rep = (float(m.group(i)) for i in (1, 2, 4, 5, "d"))
        except (TypeError, ValueError):
            continue
        if s1 <= 0 or s2 <= 0:
            continue
        d_calc = abs(m1 - m2) / math.sqrt((s1 ** 2 + s2 ** 2) / 2)
        gap = abs(abs(d_rep) - d_calc)
        if gap > 0.10 and gap > 0.25 * d_calc:
            raw = " ".join(m.group(0).split())[:220]
            out.append({"module": "consistency", "type": "stat_inconsistency", "severity": "yellow", "claim_ids": [],
                        "location": location_of(text, m.start()),
                        "evidence": f"'{raw}': the reported {m.group('lab')} = {m.group('d')}, but the two means and SDs "
                                    f"give a standardized difference of about {d_calc:.2f} "
                                    f"(|{m1:g} - {m2:g}| / sqrt(({s1:g}^2 + {s2:g}^2) / 2)).",
                        "source": "script:ci_check.py",
                        "question_for_authors": "Which inputs or formula produced the reported effect size?"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--si", type=Path, default=None,
                    help="folder with plain-text supplements (.md/.txt); default: si/ next to the paper, if present")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--ratio-gap", type=float, default=3.0, help="flag p-values off by more than this factor")
    args = ap.parse_args()

    text = flatten_html(args.text.read_text(encoding="utf-8", errors="replace")).translate(NORMALISE)
    rows, findings = [], []
    matches = list(PATTERN.finditer(text))
    if CI_IN_PARENS.search(text):
        taken = [(m.start(), m.end()) for m in matches]
        matches += [m for m in BARE_CELL.finditer(text)
                    if not any(a <= m.start("est") < b for a, b in taken)]
        matches.sort(key=lambda m: m.start())
    for m in matches:
        g = m.groupdict()
        label = (g.get("label") or "").strip(" :=").lower()
        est_s, lo_s, hi_s = g["est"], g["lo"], g["hi"]
        est, lo, hi = float(est_s), float(lo_s), float(hi_s)
        reversed_ci = lo > hi
        if reversed_ci:
            lo, hi = hi, lo
        h = 0.5 * 10 ** (-max(decimals(est_s), decimals(lo_s), decimals(hi_s)))
        raw = " ".join(m.group(0).split())[:160]
        loc = location_of(text, m.start())
        row = {"raw": raw, "location": loc, "label": label, "estimate": est_s, "lo": lo_s, "hi": hi_s,
               "p_reported": "", "p_implied": "", "issue": ""}
        accuracy = label in ACCURACY or bool(g.get("pct")) or (0 <= lo and hi <= 1 and label in ("", "r") and est > 0.5
                                                               and "AUC" in text[max(0, m.start() - 60):m.start()])
        issues = []
        if reversed_ci:
            issues.append(("stat_inconsistency", "yellow",
                           f"Lower CI limit {lo_s if float(lo_s) > float(hi_s) else hi_s} is printed above the upper limit "
                           f"({g['lo']} to {g['hi']}).", "Are the CI limits swapped or mistyped?"))
        if not (lo - h <= est <= hi + h):
            issues.append(("stat_inconsistency", "red",
                           f"Estimate {est_s} lies outside its own 95% CI [{lo_s}, {hi_s}].",
                           "Is the estimate or one of the CI limits mistyped?"))
        pm = PVAL.search(g.get("tail") or "")
        if pm and not accuracy:
            comp, p_s = pm.group("comp"), pm.group("p")
            p_rep = float(p_s)
            ratio = label in RATIO or (label == "" and lo > 0 and "ratio" in text[max(0, m.start() - 40):m.start()].lower())
            null = 1.0 if ratio else 0.0
            if ratio and lo <= 0:
                ratio, null = False, 0.0
            excludes = not (lo - h <= null <= hi + h) if True else False
            strictly_excludes = lo > null or hi < null
            p_mid, p_min, p_max = implied_p(est, lo, hi, h, ratio)
            row.update(p_reported=f"{comp}{p_s}", p_implied=f"{p_mid:.3g} [{p_min:.3g}, {p_max:.3g}]")
            rep_sig = (comp in "<=" and p_rep < args.alpha) or (comp == "<" and p_rep <= args.alpha)
            rep_nonsig = comp in "=>" and p_rep > args.alpha
            boundary = abs(lo - null) <= h or abs(hi - null) <= h
            if not boundary and ((strictly_excludes and rep_nonsig) or (not excludes and rep_sig)):
                issues.append(("stat_decision_error", "red",
                               f"The 95% CI [{lo_s}, {hi_s}] {'excludes' if strictly_excludes else 'includes'} the null value "
                               f"{null:g}, but the reported p is {comp} {p_s}; significance at alpha = {args.alpha:g} disagrees. "
                               f"p implied by the CI (Altman-Bland) is about {p_mid:.2g}.",
                               "Is the p-value, the estimate or a CI limit mistyped?"))
            elif comp == "=" and p_rep > 0 and not math.isnan(p_mid):
                lo_ok, hi_ok = p_min / args.ratio_gap, p_max * args.ratio_gap
                if not (lo_ok <= p_rep <= hi_ok):
                    issues.append(("stat_inconsistency", "yellow",
                                   f"Reported p = {p_s} differs from the p implied by the 95% CI [{lo_s}, {hi_s}] "
                                   f"(about {p_mid:.2g}, range {p_min:.2g}-{p_max:.2g}) by more than {args.ratio_gap:g}x. "
                                   f"The check assumes a symmetric Wald interval.",
                                   "Which test produced the p-value, and is it reported correctly?"))
        row["issue"] = "; ".join(i[0] for i in issues)
        rows.append(row)
        for ftype, sev, ev, q in issues:
            findings.append({"module": "consistency", "type": ftype, "severity": sev, "claim_ids": [],
                             "location": loc, "evidence": f"'{raw}': {ev}", "source": "script:ci_check.py",
                             "question_for_authors": q})

    # Intervals written on their own (table cells, "(7.11 to 6.86)") whose lower limit exceeds the upper one.
    seen_pos = {m.start("lo") for m in PATTERN.finditer(text)}
    for m in REVERSED.finditer(text):
        a_s, b_s = m.group("a"), m.group("b")
        if m.start("a") in seen_pos or float(a_s) <= float(b_s):
            continue
        before = text[max(0, m.start() - 60):m.start()]
        if m.group("sep").strip() not in ("to", "–", "—") and not re.search(r"CI|confidence|interval", before, re.I):
            continue
        loc = location_of(text, m.start())
        raw = " ".join(text[max(0, m.start() - 40):m.end()].split())[-120:]
        rows.append({"raw": raw, "location": loc, "label": "", "estimate": "", "lo": a_s, "hi": b_s,
                     "p_reported": "", "p_implied": "", "issue": "stat_inconsistency"})
        findings.append({"module": "consistency", "type": "stat_inconsistency", "severity": "yellow", "claim_ids": [],
                         "location": loc, "evidence": f"'{raw}': interval ({a_s} to {b_s}) has its lower limit above the upper limit.",
                         "source": "script:ci_check.py", "question_for_authors": "Are the CI limits swapped or mistyped?"})

    findings += effect_size_findings(text)

    n_bad = sum(bool(r["issue"]) for r in rows)
    if rows and n_bad < len(rows):
        findings.append({"module": "consistency", "type": "stat_inconsistency", "severity": "green", "claim_ids": [],
                         "location": "whole text",
                         "evidence": f"{len(rows) - n_bad} of {len(rows)} estimate-CI reports are internally consistent.",
                         "source": "script:ci_check.py", "question_for_authors": None})
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "ci_table.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["raw"])
        w.writeheader()
        w.writerows(rows)
    (args.out / "ci.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")

    texts = {str(args.text.name): text}
    si = args.si if args.si is not None else args.text.parent / "si"
    if si.is_dir():
        for f in sorted(si.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".md", ".txt"):
                texts[str(f.relative_to(si.parent))] = f.read_text(encoding="utf-8", errors="replace").translate(NORMALISE)
    methods = ci_methods(texts)
    (args.out / "ci_methods.json").write_text(json.dumps({"methods": methods, "n_methods": len(methods)},
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"estimate-CI reports={len(rows)} flagged={n_bad} with_p={sum(bool(r['p_reported']) for r in rows)} "
          f"interval_methods_named={len(methods)}" + (f" ({', '.join(m['method'] for m in methods)})" if methods else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

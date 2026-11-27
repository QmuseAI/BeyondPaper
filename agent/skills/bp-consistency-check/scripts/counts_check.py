#!/usr/bin/env python3
"""Check sample sizes and other counts in a paper's text.

Counts (participants, patients, articles, trials, items, ...) are the numbers that LLM extraction
most often skips, so this script reads them directly from paper.md:

1. pct_fraction   — "15/18 (83%)", "(83%; n = 15/18)", "9 of 25 (36%)": the percentage must match the
                    fraction within rounding; a numerator larger than the denominator is an error.
2. part_whole     — "156 participants [73 in the experimental condition]",
                    "103 participants (60 instructors and 43 academics)": a subgroup cannot exceed the total,
                    and listed subgroups that do not add up are reported (yellow: the list may be partial).
3. multiplier     — "2.5 times the original 65 participants ... a minimum of 163": the stated product must
                    match the multiplier times the base (rounded up or down).
4. near_singleton — a count of the same thing that appears once and is within 10% of a count that appears
                    at least twice and is used at least three times in all (counting "6/25", "9 of 25") ("27 articles" once, "25 articles" six times). Often a typo, but just
                    as often a real subgroup, so it is only a grey pointer: check it against the ledger and
                    raise it to yellow only if both counts describe the same set.

Every count mention is written to counts_table.csv so the agent can compare it with the ledger.

Usage:
    python counts_check.py bp_work/paper.md --out bp_out
Outputs: counts_table.csv, counts.findings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

NORMALISE = str.maketrans({"−": "-", "–": "-", "—": "-", " ": " ", " ": " ", "·": ".", "，": ",", "（": "(", "）": ")"})
NOUNS = (r"participants?|patients?|subjects?|respondents?|individuals?|people|persons?|adults?|children|students?|"
         r"articles?|papers?|studies|study|trials?|RCTs?|reports?|records?|items?|questions?|samples?|specimens?|"
         r"cases?|controls?|couples?|families|households?|mice|rats|animals?|cells?|images?|sessions?|"
         r"centres?|centers?|sites?|hospitals?|schools?|universities|countries|reactions?|compounds?|molecules?|"
         r"volunteers?|users?|women|men|infants?|eyes?|teeth|tumou?rs?|lesions?|values?|outcomes?|datasets?|cohorts?")
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
PAGE_MARK = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.I)
HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
INT = r"\d{1,3}(?:,\d{3})+|\d+"

# 1. percentage vs fraction
PF = [
    re.compile(rf"(?P<x>{INT})\s*(?:/|of|out of)\s*(?P<y>{INT})(?:\s+\w+){{0,3}}?\s*[\(\[,;]\s*(?P<p>\d+(?:\.\d+)?)\s*%"),
    re.compile(rf"(?P<p>\d+(?:\.\d+)?)\s*%\s*[;,]?\s*[\(\[]?\s*(?:n\s*=\s*)?(?P<x>{INT})\s*/\s*(?P<y>{INT})"),
    re.compile(rf"(?P<p>\d+(?:\.\d+)?)\s*%\s*\(\s*(?:n\s*=\s*)?(?P<x>{INT})\s*(?:of|out of)\s*(?P<y>{INT})\s*\)"),
]
# 2. total followed by subgroup(s) in brackets
PW = re.compile(rf"(?P<n>{INT})\s+(?:\w+\s+){{0,2}}?(?P<noun>{NOUNS})\b[^\(\[\n]{{0,25}}[\(\[](?P<inner>[^\)\]\n]{{3,160}})[\)\]]", re.I)
SUB = re.compile(rf"(?<![\d.%])(?P<m>{INT})(?!\s*%|\.\d)\s+(?:were\s+|was\s+)?(?:in|from|with|for|of|at|"
                 rf"(?:\w+\s+){{0,2}}?(?:{NOUNS}))\b", re.I)
# 3. multiplier statements
MULT = re.compile(r"(?P<k>\d+(?:\.\d+)?)\s*(?:x|×|times)\b|\b(?P<w>twice|double|triple|three times|four times)\b", re.I)
WORD_K = {"twice": 2, "double": 2, "triple": 3, "three times": 3, "four times": 4}
TARGET = re.compile(rf"(?:minimum of|at least|total of|target(?:ed)? (?:sample )?(?:size )?of|resulting in|"
                    rf"suggest(?:s|ed)? (?:a )?(?:minimum of )?|requir(?:es|ed|ing) (?:a minimum of |at least )?)"
                    rf"\s*(?P<t>{INT})", re.I)
# 4. all counts
COUNT = re.compile(rf"(?<![\w.,/%-])(?P<n>{INT}|{'|'.join(WORD_NUM)})(?!\s*(?:x|×|times)\b)\s+"
                   rf"(?:[A-Za-z-]+\s+){{0,2}}?(?P<noun>{NOUNS})\b", re.I)


def num(s: str) -> int:
    s = s.lower()
    return WORD_NUM[s] if s in WORD_NUM else int(s.replace(",", ""))


def lemma(noun: str) -> str:
    n = noun.lower()
    irregular = {"studies": "study", "families": "family", "children": "child", "people": "person", "mice": "mouse",
                 "women": "woman", "men": "man", "teeth": "tooth", "universities": "university", "countries": "country"}
    if n in irregular:
        return irregular[n]
    return n[:-1] if n.endswith("s") and not n.endswith("ss") else n


class Locator:
    def __init__(self, text: str):
        self.text = text
        self.pages = [(m.start(), m.group(1)) for m in PAGE_MARK.finditer(text)]
        self.heads = [(m.start(), m.group(1).strip()) for m in HEADING.finditer(text)]

    def __call__(self, pos: int) -> str:
        page = next((p for s, p in reversed(self.pages) if s <= pos), None)
        head = next((h for s, h in reversed(self.heads) if s <= pos), None)
        line = self.text.count("\n", 0, pos) + 1
        parts = [f"p. {page}" if page else None, f"Sec. {head[:40]}" if head else None, f"line {line}"]
        return ", ".join(p for p in parts if p)


def finding(ftype, sev, loc, ev, q):
    return {"module": "consistency", "type": ftype, "severity": sev, "claim_ids": [], "location": loc,
            "evidence": ev, "source": "script:counts_check.py", "question_for_authors": q}


def snippet(text: str, a: int, b: int, pad: int = 30) -> str:
    return " ".join(text[max(0, a - pad):min(len(text), b + pad)].split())


def check_pct_fraction(text, where, out):
    seen = set()
    for rx in PF:
        for m in rx.finditer(text):
            if m.start() in seen:
                continue
            seen.add(m.start())
            x, y, p_s = num(m["x"]), num(m["y"]), m["p"]
            if y == 0:
                continue
            p = float(p_s)
            dec = len(p_s.split(".")[1]) if "." in p_s else 0
            tol = 0.5 * 10 ** (-dec) + 1e-9
            exact = 100 * x / y
            # skip year ranges and dates such as 2014/2015 or 12/05
            if y > 1900 and x > 1900:
                continue
            if x > y:
                out.append(finding("sample_size", "red", where(m.start()),
                                   f"'{snippet(text, m.start(), m.end())}': numerator {x} is larger than denominator {y}.",
                                   "Is one of the two counts mistyped?"))
            elif abs(exact - p) > tol and abs(exact - p) > 0.5:
                out.append(finding("sample_size", "red", where(m.start()),
                                   f"'{snippet(text, m.start(), m.end())}': {x}/{y} = {exact:.1f}%, but the text reports {p_s}%.",
                                   "Is the percentage, the numerator or the denominator mistyped?"))


def check_part_whole(text, where, out):
    for m in PW.finditer(text):
        total = num(m["n"])
        inner = m["inner"]
        if "%" in inner and not SUB.search(inner):
            continue
        subs = [num(s["m"]) for s in SUB.finditer(inner)]
        subs = [s for s in subs if s != total]
        if not subs:
            continue
        loc = where(m.start())
        big = [s for s in subs if s > total]
        if big:
            out.append(finding("sample_size", "red", loc,
                               f"'{snippet(text, m.start(), m.end(), 10)}': subgroup count {big[0]} exceeds the total of {total} {m['noun']}.",
                               "Is the subgroup or the total mistyped?"))
        elif len(subs) >= 2 and re.search(r"\band\b|,|;", inner) and sum(subs) != total:
            out.append(finding("sample_size", "yellow", loc,
                               f"'{snippet(text, m.start(), m.end(), 10)}': subgroups {' + '.join(map(str, subs))} = {sum(subs)}, "
                               f"not {total}. The list may be partial or overlapping.",
                               "Do the subgroups add up to the total?"))


N_EQ = re.compile(r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)\b")
SPLIT_WORD = re.compile(r"(?i)\b(?:into|split|divided|allocated|randomi[sz]ed|assigned|partitioned|stratified)\b")


def check_split_sum(text, where, out):
    """v1.11.9: 'The participants (Cohort 1, n = 426) were randomly ... sampled into a discovery dataset (n = 284) and
    a test set (n = 152)' - the parts of a split must add up to the whole (B02-v1117p missed 284 + 152 = 436)."""
    pos = 0
    for sent in re.split(r"(?<=[.;])\s+(?=[A-Z(])", text):
        start = text.find(sent, pos)
        pos = max(pos, start)
        ms = list(N_EQ.finditer(sent))
        if len(ms) < 3 or not SPLIT_WORD.search(sent[ms[0].end():ms[1].start()]):
            continue
        total, parts = num(ms[0].group(1)), [num(m.group(1)) for m in ms[1:]]
        if any(p >= total for p in parts) or sum(parts) == total:
            continue
        if abs(sum(parts) - total) > 0.25 * total:
            continue  # a partial list, not a split of the whole
        out.append(finding("sample_size", "yellow", where(max(start, 0) + ms[0].start()),
                           f"'{sent.strip()[:240]}': the parts {' + '.join(map(str, parts))} = {sum(parts)}, "
                           f"not the stated total n = {total}.",
                           "Which of the counts is mistyped?"))


def check_multiplier(text, where, out):
    # a sentence ends at . ! ? followed by whitespace; "2.5" and "i.e.," stay inside the sentence
    sentences = [(m.start(), m.group(0)) for m in re.finditer(r"(?:[^.!?\n]|[.!?](?=\S))+[.!?]?", text)]
    for i, (start, s) in enumerate(sentences):
        for km in MULT.finditer(s):
            k = float(km["k"]) if km["k"] else WORD_K[km["w"].lower()]
            if not (1 < k <= 20):
                continue
            window = " ".join(x for _, x in sentences[max(0, i - 1):i + 2])
            tm = TARGET.search(s) or TARGET.search(window)
            if not tm:
                continue
            target = num(tm["t"])
            bases = [num(c["n"]) for c in COUNT.finditer(window)]
            bases = [b for b in bases if b not in (target,) and b * k <= 10 * target]
            if not bases:
                continue
            ok = any(abs(target - b * k) <= 1 or target in (math.floor(b * k), math.ceil(b * k)) for b in bases)
            if ok:
                break
            else:
                b = min(bases, key=lambda b: abs(target - b * k))
                out.append(finding("sample_size", "yellow", where(start),
                                   f"'{' '.join(s.split())[:200]}': {k:g} x {b} = {b * k:g}, but the stated figure is {target}.",
                                   "How was this number derived from the base and the multiplier?"))
                break


def count_table(text, where):
    rows = []
    for m in COUNT.finditer(text):
        n = num(m["n"])
        if 1900 <= n <= 2100 and not m["n"].lower() in WORD_NUM:  # years
            continue
        rows.append({"count": n, "noun": lemma(m["noun"]), "raw": " ".join(m.group(0).split()),
                     "location": where(m.start()), "pos": m.start()})
    return rows


SAMPLE_DEF = re.compile(r"(?i)\b(?:final sample|analy[sz]ed sample|analytic sample|study sample|sample (?:comprised|consisted|included)|"
                        r"in total|a total of|total sample|were (?:included|enrolled|analy[sz]ed)|comprised)\b")


def sentence_at(text: str, pos: int) -> str:
    a = max(text.rfind(". ", 0, pos), text.rfind("\n", 0, pos))
    b = min([i for i in (text.find(". ", pos), text.find("\n", pos)) if i >= 0] or [len(text)])
    return text[a + 1:b]


def check_near_singleton(rows, text, out, rel=0.10, dominant=3):
    by_noun = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_noun[r["noun"]][r["count"]].append(r)
    def support(c, rs):
        # noun mentions plus denominators ("6/25", "9 of 25", "out of the 25") of the same number
        denom = len(re.findall(rf"(?:/|\bof|\bout of)\s*(?:the\s+)?{c}\b(?![.,]\d)", text))
        return len(rs) + denom

    for noun, counts in by_noun.items():
        common = [c for c, rs in counts.items() if len(rs) >= 2 and support(c, rs) >= dominant]
        for c, rs in counts.items():
            if len(rs) != 1 or c < 10:
                continue
            near = [d for d in common if d != c and abs(d - c) <= rel * d]
            if near:
                d = near[0]
                r = rs[0]
                # v1.11.11: yellow when the single count is the one that defines the sample ("Our final sample
                # comprised 113 participants" vs 103 three times, B10 I3; "789 values reported in 27 articles" vs 25,
                # B07 I2). Other singletons are usually a different subgroup (72 Round-1 items vs 78 in total).
                sent = sentence_at(text, r["pos"])
                sev = "yellow" if SAMPLE_DEF.search(sent) else "grey"
                out.append(finding("sample_size", sev, r["location"],
                                   f"'{snippet(text, r['pos'], r['pos'] + len(r['raw']))}': {c} {noun}(s) appears once, while "
                                   f"{d} {noun}(s) appears {len(counts[d])} times (and {d} is used as a denominator elsewhere). "
                                   "A typo, or a different subgroup?",
                                   f"Is the count {c} correct here, or should it be {d}?"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()
    text = args.text.read_text(encoding="utf-8", errors="replace").translate(NORMALISE)
    # drop the reference list: years and page ranges there are not counts
    cut = re.search(r"^#{1,6}\s*(References|Bibliography)\b", text, re.M | re.I)
    if cut:
        text = text[:cut.start()]
    where = Locator(text)
    findings: list[dict] = []
    check_pct_fraction(text, where, findings)
    check_part_whole(text, where, findings)
    check_split_sum(text, where, findings)
    check_multiplier(text, where, findings)
    rows = count_table(text, where)
    check_near_singleton(rows, text, findings)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "counts_table.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["count", "noun", "raw", "location"])
        w.writeheader()
        w.writerows({k: r[k] for k in ("count", "noun", "raw", "location")} for r in rows)
    flagged = len(findings)
    findings.append(finding("sample_size", "green" if not flagged else "grey", "whole text",
                            f"{len(rows)} count mentions read; {flagged} flagged. Full list in counts_table.csv.", None))
    (args.out / "counts.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"count mentions={len(rows)} flagged={flagged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

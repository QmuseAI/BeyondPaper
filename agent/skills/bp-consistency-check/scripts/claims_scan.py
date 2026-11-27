#!/usr/bin/env python3
"""Scan the paper text for two kinds of problems that judgement checks used to catch only when the model did them
(v1.11.7). Both are written as yellow findings, whichever model runs the stages.

  phantom_experiment  a sentence in the Abstract, Results or Discussion reports an extra study or analysis
                      ("validated in a prospective cohort of 214 participants", "an individual-participant-data
                      re-analysis of 312 patients", "a follow-up survey of 46 heads of department", "we also repeated
                      the study online with 412 participants") whose head count appears nowhere else in the paper,
                      whose describing phrase appears in no other sentence (Methods included), and which cites no
                      source. Chen 2024 I2, Stefanetti 2020 I4, Delphi I4, Campbell I3 in the benchmark.
  number_mismatch     a number in the Abstract that the body never states, while a body sentence about the same
                      quantity (two or more shared words, the same metric word) gives a different number of the same
                      kind (Chen 2024 I1: abstract sensitivity 0.950, Results 0.905).
  sample_size         a head count in the text that the authors' own data table totals with transposed or changed
                      digits, the table's total appearing nowhere in the paper (Stefanetti 2020 I1: 1850 vs 1805).

Usage:
    python claims_scan.py bp_work/paper.md [--materials bp_work] --out bp_out
Output: bp_out/claims_scan.findings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

STOP = set("""the and for with from that this were was are has have had been which their there these those than then
into onto over under also only more most less such each other both after before between among within without about
above below using used use based study studies analysis analyses result results data model models test tests value
values number numbers total patients participants subjects group groups showed shown show found reported compared
compared significant significantly respectively including include included per our its it's they them while when where
what whom whose who all any some one two three four five six seven eight nine ten first second third mean median""".split())

SECTION_HEAD = re.compile(r"^\s*(?:#{1,6}\s*(.+?)\s*#*\s*$|\*\*([^*]{2,80})\*\*\s*$"
                          r"|(?i:(abstract|introduction|background|methods?|materials and methods|methods and materials|"
                          r"results|results and discussion|discussion|conclusions?|references|acknowledge?ments?|"
                          r"data availability|code availability))\s*:?\s*$)")
SEC_KIND = [
    ("abstract", re.compile(r"(?i)\babstract\b|research in context")),
    ("methods", re.compile(r"(?i)\bmethods?\b|materials|procedure|design|participants|sample|data analysis|"
                           r"statistical|measures|search strategy|experimental")),
    ("results", re.compile(r"(?i)\bresults?\b|findings")),
    ("discussion", re.compile(r"(?i)discussion|conclusion|summary of results|limitations")),
    ("intro", re.compile(r"(?i)introduction|background|study objective|context")),
    ("end", re.compile(r"(?i)references|acknowledg|funding|author contrib|competing|conflict|data availability|"
                       r"code availability|supplementa|open practices|declaration|ethics")),
]

COUNT_NOUN = (r"(?:participants|patients|subjects|individuals|people|persons|respondents|articles|papers|studies|"
              r"trials|samples|cases|controls|children|adults|women|men|heads|students|teachers|instructors|mice|rats|"
              r"animals|families|volunteers|members|households|schools|hospitals|sites|centres|centers|donors|"
              r"recruits|workers|users|employees|clinicians|physicians|nurses|infants|pregnancies|eyes|images|values|items|"
              r"records|observations|measurements|questionnaires|responses)")
N_COUNT = re.compile(rf"(?<![\w.,])(\d{{1,3}}(?:,\d{{3}})+|\d+)\s+(?:[A-Za-z][\w-]*\s+){{0,3}}?{COUNT_NOUN}\b", re.I)
N_EQ = re.compile(r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)\b")
TRIGGER = re.compile(
    r"(?i)\b(?:prospective|follow-?up|additional|independent|further|separate|second|third|fourth|fifth|new|online|"
    r"replication|validation|comparison|pilot|subsequent|later|larger)\s+(?:[\w-]+\s+){0,2}?"
    r"(?:cohort|sample|survey|study|trial|dataset|data\s+set|re-?analysis|experiment|centre|center|wave|panel)s?\b"
    r"|individual[- ]participant[- ]data|individual[- ]patient[- ]data|\bIPD\b"
    r"|\bre-?analy[sz](?:is|ed)\b"
    r"|\b(?:repeated|replicated|re-ran|reran)\s+(?:the|this|our)\s+(?:study|experiment|survey|analysis|trial)"
    r"|\bfurther validated\b|\bwas (?:then |also )?validated in\b")
CAPTION = re.compile(r"\s*(?:\*\*|_)?(?:Fig\.?|Figure|Table|Supplementary|Extended Data)\s*[A-Z]?\d", re.I)
CITE = re.compile(r"\^\[?\d|\[\d+(?:[,–-]\s*\d+)*\]|\(\s*[A-Z][A-Za-z'\-]+(?:\s+(?:et al\.?|and|&)\s*[A-Za-z'\-]*)?"
                  r",?\s*(?:19|20)\d\d[a-z]?\s*[;)]|<sup>|\bet al\.?,?\s*\(?(?:19|20)\d\d")


def split_sections(text: str) -> list[tuple[str, str, int]]:
    """Return (kind, chunk, start_line) blocks. Text before the first heading is 'front'.

    Only top-level headings (#, ##, or a bold line) change the section; a sub-heading such as "### Patients, data
    collection, and study design" inside the Results stays in the Results, unless it opens an Abstract or the end
    matter."""
    out, kind, buf, start = [], "front", [], 1
    for i, line in enumerate(text.splitlines(), start=1):
        m = SECTION_HEAD.match(line)
        if m:
            title = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            level = len(line.lstrip()) - len(line.lstrip().lstrip("#")) if m.group(1) else 2
            new = next((k for k, rx in SEC_KIND if rx.search(title)), None)
            if new and level > 2 and new not in ("abstract", "end") and kind != "front":
                new = None
            if new:
                if buf:
                    out.append((kind, "\n".join(buf), start))
                kind, buf, start = new, [], i + 1
                continue
        buf.append(line)
    if buf:
        out.append((kind, "\n".join(buf), start))
    return out


def sentences(chunk: str, start_line: int):
    """Yield (sentence, line_number). Tables and page markers are skipped."""
    pos = 0
    for para in re.split(r"\n\s*\n", chunk):
        para_start = chunk.find(para, pos)
        pos = max(pos, para_start + len(para))
        if para.lstrip().startswith("|") or para.count("|") > 6:
            continue
        if CAPTION.match(para):
            continue  # figure and table legends ("n = 179 independent patient samples")
        flat = re.sub(r"<!--.*?-->", " ", para, flags=re.S)
        flat = re.sub(r"\s+", " ", flat)
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[*])", flat):
            if len(s) > 20:
                line = start_line + chunk[:max(0, para_start)].count("\n")
                yield s.strip(), line


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]{3,}", s.lower()) if w not in STOP}


def count_tokens(text: str, token: str) -> int:
    pat = re.escape(token) if "," in token else rf"{re.escape(token)}"
    return len(re.findall(rf"(?<![\d.,]){pat}(?![\d]|[.,]\d)", text))


def phantom(text: str, secs, materials_text: str) -> list[dict]:
    out = []
    all_sents = [(k, s, ln) for k, chunk, st in secs if k != "end" for s, ln in sentences(chunk, st)]
    for kind, s, ln in all_sents:
        if kind not in ("abstract", "results", "discussion", "front"):
            continue
        trigs = list(TRIGGER.finditer(s))
        if not trigs:
            continue
        ns = [m.group(1) for m in N_COUNT.finditer(s)] + [m.group(1) for m in N_EQ.finditer(s)]
        ns = [n for n in ns if int(n.replace(",", "")) >= 10]
        if not ns:
            continue
        if CITE.search(s):
            continue  # someone else's study, cited
        others = " ".join(x for _, x, _ in all_sents if x != s)
        # the describing phrase is used elsewhere (e.g. the Methods describe the cohort): not a phantom.
        # Every trigger phrase in the sentence must be new ("further validated" may recur; "prospective cohort" not)
        fresh = [t for t in trigs if re.sub(r"\s+", " ", t.group(0).lower()) not in others.lower()
                 and not re.match(r"(?i)independent\s+(?:[\w-]+\s+){0,2}?(?:samples?|experiments?)$", t.group(0))]
        if not fresh:
            continue
        t = fresh[-1]
        # v1.11.8: the count phrase ("30 articles") counts, not the bare number: "30" occurs elsewhere for other
        # things (B07: "between 1 and 30 (median = 7 ...)" hid "a comparison sample of 30 articles")
        phrases = {m.group(1): m.group(0) for m in N_COUNT.finditer(s)}
        # the whole trigger phrase in code/README/file names means the analysis was released (single words such as
        # "comparison" are too common in code to count)
        mat_low = re.sub(r"[\s_.-]+", " ", materials_text.lower())
        trig_in_mat = any(re.sub(r"\s+", " ", f.group(0).lower()) in mat_low for f in fresh)
        lonely = []
        for n in ns:
            v = int(n.replace(",", ""))
            in_paper = count_tokens(others, n) > 0 if n not in phrases else \
                re.search(rf"(?<![\d.,]){re.escape(phrases[n])}", others) is not None
            in_mat = (v >= 100 and count_tokens(materials_text, n.replace(",", "")) > 0) or trig_in_mat
            if not in_paper and not in_mat:
                lonely.append(n)
        if not lonely:
            continue
        n = lonely[0]
        out.append({
            "module": "consistency", "type": "phantom_experiment", "severity": "yellow", "claim_ids": [],
            "location": f"{kind.capitalize()} (paper.md line {ln})",
            "evidence": (f"The text reports an additional study or analysis: '{s[:260]}'. Its head count {n} appears "
                         f"nowhere else in the paper, the phrase '{t.group(0)}' is used in no other sentence (the "
                         "Methods do not describe it), the sentence cites no source, and the code, README and "
                         "file names in the materials do not contain the number. Look for its data, code, table or "
                         "supplementary item; if there is none, the analysis cannot be checked from what was released."),
            "source": "script:claims_scan.py",
            "question_for_authors": f"Where are the data, code and methods for the analysis of {n} described here?"})
    return out


NUM = re.compile(r"(?<![\w.,/])(-?\d+\.\d+|\d{2,}(?:,\d{3})*)(\s*%)?(?![\w]|[.,]\d)")
METRIC = re.compile(r"(?i)\b(sensitivity|specificity|accuracy|auroc|auc|c-index|precision|recall|f1|ppv|npv|"
                    r"i2|i²|heterogeneity|smd|odds ratio|hazard ratio|risk ratio|correlation|consensus|reached|"
                    r"response rate|prevalence|incidence|mortality|survival|participants|patients|articles|items|"
                    r"studies|trials|values|discrepancies|effect size|cohen|mean|median)\b")


def decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def same_quantity(a: str, b: str) -> bool:
    """b (body) states a (abstract): equal, or a is b rounded to a's printed precision."""
    fa, fb = float(a.replace(",", "")), float(b.replace(",", ""))
    if fa == fb:
        return True
    da = decimals(a)
    return decimals(b) > da and abs(fb - fa) <= 0.5 * 10 ** -da + 1e-12


def same_kind(a: str, b: str) -> bool:
    fa, fb = float(a.replace(",", "")), float(b.replace(",", ""))
    if (decimals(a) > 0) != (decimals(b) > 0):
        return False
    if fa == 0 or fb == 0:
        return False
    r = fa / fb
    return 0.5 <= r <= 2.0


def abstract_body(secs) -> list[dict]:
    abs_s = [(s, ln) for k, chunk, st in secs if k == "abstract" for s, ln in sentences(chunk, st)]
    body_s = [(s, ln) for k, chunk, st in secs if k in ("results", "discussion", "methods") for s, ln in sentences(chunk, st)]
    if not abs_s or not body_s:
        return []
    # tables count as the body stating a number (B01: "6 (24%, [8,47])" sits only in Table 1)
    body_text = "\n".join(chunk for k, chunk, _ in secs if k in ("results", "discussion", "methods"))
    body_nums = [(s, ln, m.group(1), m.start()) for s, ln in body_s for m in NUM.finditer(s)]
    out, seen = [], set()
    for s, ln in abs_s:
        for m in NUM.finditer(s):
            a = m.group(1)
            fa = float(a.replace(",", ""))
            if decimals(a) == 0 and (1900 <= fa <= 2100 or fa < 10):
                continue
            if count_tokens(body_text, a) or any(same_quantity(a, b.group(1)) for b in NUM.finditer(body_text)):
                continue
            if m.group(2) and any(same_quantity(a, f"{float(x) * 100:.{max(0, decimals(x) - 2)}f}")
                                  for x in re.findall(r"(?<![\d.])0\.\d+", body_text)):
                continue  # "F1 64%" in the Abstract, "F1 0.64" in the body: one number, two notations
            left = s[max(0, m.start() - 70):m.start()]
            right = s[m.end():m.end() + 50]
            mw = METRIC.findall(left[-45:] + " " + right[:30])
            if not mw:
                continue
            ctx = words(left + " " + right)
            best = None
            for bs, bln, b, bpos in body_nums:
                if not same_kind(a, b):
                    continue
                bleft, bright = bs[max(0, bpos - 70):bpos], bs[bpos:bpos + 60]
                if not any(re.search(rf"(?i)\b{re.escape(w)}\b", bleft[-45:] + " " + bright[:40]) for w in mw):
                    continue
                shared = ctx & words(bs)
                if len(shared) >= 2 and (best is None or len(shared) > best[0]):
                    best = (len(shared), bs, bln, b, shared)
            if not best or (a, best[3]) in seen:
                continue
            seen.add((a, best[3]))
            _, bs, bln, b, shared = best
            out.append({
                "module": "consistency", "type": "number_mismatch", "severity": "yellow", "claim_ids": [],
                "location": f"Abstract (paper.md line {ln}) vs body (paper.md line {bln})",
                "evidence": (f"The Abstract gives {a} ('...{s[max(0, m.start() - 90):m.end() + 40]}...'); the body never "
                             f"states {a}. The body sentence about the same quantity (shared words: "
                             f"{', '.join(sorted(shared)[:5])}) gives {b}: '...{bs[:240]}...'. Confirm that both refer "
                             "to the same quantity (same cohort, test set, metric) before keeping it."),
                "source": "script:claims_scan.py",
                "question_for_authors": f"Which value is correct, {a} (Abstract) or {b} (body)?"})
    return out


TOTAL_LABEL = re.compile(r"(?i)\btotal\b|\bsum\b|\boverall\b")


def data_totals(root: Path | None) -> list[tuple[str, str, int, int]]:
    """(file, label, value, line) for rows of the authors' CSV/TSV tables labelled as a total ("Total patients (N),1805")."""
    out = []
    if not root or not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".csv", ".tsv") or not p.is_file() or p.stat().st_size > 20_000_000 \
                or "bp_out" in p.parts:
            continue
        try:
            lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        sep = "\t" if p.suffix.lower() == ".tsv" else ","
        for i, line in enumerate(lines[:100_000], start=1):
            cells = [c.strip().strip('"') for c in line.split(sep)]
            lab = next((c for c in cells if c), "")
            if not TOTAL_LABEL.search(lab) or len(lab) > 80:
                continue
            val = next((c for c in cells[cells.index(lab) + 1:] if re.fullmatch(r"\d+", c or "")), None)
            if val and int(val) >= 20:
                out.append((str(p.relative_to(root)), lab, int(val), i))
    return out


def near_count(a: int, b: int) -> bool:
    """Digits transposed, or one non-final digit changed (1805 -> 1850, 142 -> 152). A change in the last digit
    (130 articles vs 132 study designs, B09) is too common between related counts to mean a typo."""
    sa, sb = str(a), str(b)
    if len(sa) != len(sb) or a == b:
        return False
    return sorted(sa) == sorted(sb) or (sum(x != y for x, y in zip(sa, sb)) == 1 and sa[-1] == sb[-1])


def totals_vs_text(text: str, secs, root: Path | None) -> list[dict]:
    """v1.11.7: a head count in the text that the authors' own data table totals differently, where the table's total
    appears nowhere in the paper (Stefanetti 2020 I1: "130 articles with 1850 patients"; File 1 row "Total patients
    (N)" = 1805)."""
    tots = data_totals(root)
    if not tots:
        return []
    out, seen = [], set()
    flat = re.sub(r"\s+", " ", text)
    for k, chunk, st in secs:
        if k == "end":
            continue
        for s, ln in sentences(chunk, st):
            for m in N_COUNT.finditer(s):
                n = int(m.group(1).replace(",", ""))
                noun = m.group(0).split()[-1].lower().rstrip("s")
                for f, lab, v, row in tots:
                    if noun not in lab.lower() or not near_count(n, v) or (n, v) in seen:
                        continue
                    stated = count_tokens(flat, str(v)) + count_tokens(flat, f"{v:,}")
                    if stated and sorted(str(n)) != sorted(str(v)):
                        continue  # the paper also gives the table's total; a one-digit change may be another set
                    seen.add((n, v))
                    out.append({
                        "module": "consistency", "type": "sample_size", "severity": "yellow", "claim_ids": [],
                        "location": f"{k.capitalize()} (paper.md line {ln}) vs {f} row {row}",
                        "evidence": (f"The text gives {m.group(0)} ('...{s[:220]}...'); the authors' table {f} has the row "
                                     f"'{lab}' = {v}" + (f", which the paper itself gives elsewhere" if stated else
                                                         f", and {v} appears nowhere in the paper")
                                     + ". The two numbers differ by transposed or changed digits."),
                        "source": "script:claims_scan.py",
                        "question_for_authors": f"Is the number of {noun}s {n} (text) or {v} ({f})?"})
    return out


ID_COL = re.compile(r"(?i)^\s*([a-z]+?)[\s_.-]*(?:id|identifier|number|no)\s*$|^\s*id[\s_.-]*([a-z]+)\s*$")


def data_id_counts(root: Path | None) -> list[tuple[str, str, str, int]]:
    """(file, column, entity noun, number of distinct values) for ID columns of the authors' tables
    (data_articles.csv: articleID has 25 distinct values)."""
    out = []
    if not root or not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".csv", ".tsv") or not p.is_file() or p.stat().st_size > 20_000_000 \
                or "bp_out" in p.parts or "codebook" in p.name.lower():
            continue
        try:
            with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
                rows = list(csv.reader(fh, delimiter="\t" if p.suffix.lower() == ".tsv" else ","))
        except (OSError, csv.Error):
            continue
        if len(rows) < 3:
            continue
        for j, h in enumerate(rows[0]):
            m = ID_COL.match(h or "")
            if not m:
                continue
            ent = (m.group(1) or m.group(2) or "").lower()
            vals = {r[j].strip() for r in rows[1:] if len(r) > j and r[j].strip()}
            if ent and len(vals) >= 10:
                out.append((str(p.relative_to(root)), h, ent, len(vals)))
    return out


def ids_vs_text(text: str, secs, root: Path | None) -> list[dict]:
    """v1.11.9: a head count stated once in the Methods/Results that neither the authors' data nor the rest of the
    paper supports (Hardwicke I2: "789 values reported in 27 articles"; data_articles.csv has 25 distinct articleIDs
    and the paper says "25 articles" twice). Introduction and Discussion sentences describe other studies."""
    ids = data_id_counts(root)
    if not ids:
        return []
    flat = re.sub(r"\s+", " ", text)
    out, seen = [], set()
    for k, chunk, st in secs:
        if k not in ("abstract", "methods", "results"):
            continue
        for s, ln in sentences(chunk, st):
            if CITE.search(s):
                continue
            for m in N_COUNT.finditer(s):
                n = int(m.group(1).replace(",", ""))
                noun = m.group(0).split()[-1].lower()
                stem = noun.rstrip("s")
                for f, col, ent, u in ids:
                    if ent.rstrip("s") != stem or n == u or abs(n - u) > 0.2 * u or (n, u) in seen:
                        continue
                    phr_n = len(re.findall(rf"(?<![\d.,]){n}\s+(?:[A-Za-z][\w-]*\s+){{0,3}}?{noun}\b", flat))
                    phr_u = len(re.findall(rf"(?<![\d.,]){u}\s+(?:[A-Za-z][\w-]*\s+){{0,3}}?{noun}\b", flat))
                    if phr_n != 1 or phr_u < 1:
                        continue
                    seen.add((n, u))
                    out.append({
                        "module": "consistency", "type": "sample_size", "severity": "yellow", "claim_ids": [],
                        "location": f"{k.capitalize()} (paper.md line {ln}) vs {f} column '{col}'",
                        "evidence": (f"The text gives {m.group(0)} once ('...{s[:220]}...'); the authors' table {f} has "
                                     f"{u} distinct values of '{col}', and the paper says '{u} {noun}' {phr_u} time(s) "
                                     f"elsewhere."),
                        "source": "script:claims_scan.py",
                        "question_for_authors": f"Is the number of {noun} {n} or {u}?"})
    return out


def transposed(secs) -> list[dict]:
    """v1.11.8: one quantity written with transposed digits in two places ("37 major discrepancies remained among the
    879 target values" vs "37 ... out of 789 checked values", Hardwicke I3)."""
    seen, out = {}, []
    for k, chunk, st in secs:
        if k == "end":
            continue
        for s, ln in sentences(chunk, st):
            for m in N_COUNT.finditer(s):
                n = m.group(1).replace(",", "")
                if len(n) < 3 or 1900 <= int(n) <= 2100:
                    continue
                noun = m.group(0).split()[-1].lower()
                seen.setdefault((noun, "".join(sorted(n))), []).append((n, k, ln, s))
    for (noun, _), hits in seen.items():
        vals = sorted({h[0] for h in hits})
        if len(vals) != 2:
            continue
        a = next(h for h in hits if h[0] == vals[0])
        b = next(h for h in hits if h[0] == vals[1])
        out.append({
            "module": "consistency", "type": "number_mismatch", "severity": "yellow", "claim_ids": [],
            "location": f"{a[1].capitalize()} (paper.md line {a[2]}) vs {b[1].capitalize()} (paper.md line {b[2]})",
            "evidence": (f"The same digits in a different order for {noun}: {a[0]} ('...{a[3][:200]}...') and {b[0]} "
                         f"('...{b[3][:200]}...'). If both count the same set, one of them has transposed digits."),
            "source": "script:claims_scan.py",
            "question_for_authors": f"Is the number of {noun} {a[0]} or {b[0]}?"})
    return out


def materials_blob(root: Path | None) -> str:
    if not root or not root.exists():
        return ""
    parts, size = [], 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "bp_out" in p.parts or "prep" in p.parts or p.name == "paper.md":
            continue
        parts.append(str(p.relative_to(root)))
        if p.suffix.lower() in (".py", ".r", ".rmd", ".qmd", ".md", ".txt", ".m", ".do", ".sas", ".jl", ".sh") \
                and p.stat().st_size < 2_000_000 and size < 30_000_000:
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            size += len(t)
            parts.append(t)
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper", type=Path)
    ap.add_argument("--materials", type=Path, default=None, help="folder with code/README (default: the paper's folder)")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    a = ap.parse_args()
    text = a.paper.read_text(encoding="utf-8", errors="replace")
    # v1.11.8: a middle dot as decimal mark (Lancet style "I²=58·5%", Stefanetti 2020 Abstract) is a decimal point
    text = re.sub(r"(?<=\d)[·\u2027\u00b7](?=\d)", ".", text)
    secs = split_sections(text)
    mat = materials_blob(a.materials or a.paper.parent)
    findings = phantom(text, secs, mat) + abstract_body(secs) + totals_vs_text(text, secs, a.materials or a.paper.parent) \
        + transposed(secs)
    # a sentence already reported as an unbacked analysis is not reported again for its head count
    lines_done = {re.search(r"line (\d+)", f["location"]).group(1) for f in findings if f["type"] == "phantom_experiment"}
    findings += [f for f in ids_vs_text(text, secs, a.materials or a.paper.parent)
                 if re.search(r"line (\d+)", f["location"]).group(1) not in lines_done]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "claims_scan.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    kinds = {}
    for f in findings:
        kinds[f["type"]] = kinds.get(f["type"], 0) + 1
    print(f"claims_scan: {len(findings)} candidate(s) " + " ".join(f"{k}={v}" for k, v in kinds.items())
          + f"; sections: {', '.join(sorted({k for k, _, _ in secs}))}")
    for f in findings:
        print(f"  [{f['severity']}] {f['type']} {f['location']}: {f['evidence'][:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

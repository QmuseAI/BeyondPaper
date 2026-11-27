#!/usr/bin/env python3
"""Merge BeyondPaper outputs into findings.json and the relay package passport.md.

Usage:
    python build_passport.py bp_out [--no-print]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ORDER = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
LIGHT = {"red": "🔴", "yellow": "🟡", "grey": "⚪", "green": "🟢"}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def esc(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


NUMBER_TYPES = {"number_mismatch", "sample_size", "rerun_mismatch", "magnitude_error", "archive_mismatch"}
FILE_REF = re.compile(r"\b(data|results?|code|outputs?|figures|tables|scripts|src|notebooks?|capsule|materials)/"
                      r"|\w\.(csv|tsv|xlsx?|pdf|html?|rmd|r|py|ipynb|rds|rdata|mat|log)\b", re.I)
GATE_NOTE = (" [Capped at yellow by build_passport.py: a hand-written number finding that involves the authors' "
             "files has not passed the reproduction gate. Add the value to recomputed.json (`value` for a count "
             "made from their data, `archived` for a number read from their stored output) and re-run "
             "numbers_crosscheck.py.]")


def is_hand_written(item: dict, file_name: str = "") -> bool:
    """A finding the model wrote or edited. A script name in `source` does not make it a script finding when
    the finding sits in llm.findings.json or has module "llm" (B10-v18: five archive_mismatch reds written by
    the model, labelled "script:numbers_crosscheck.py", passed the v1.9.1 cap)."""
    src = str(item.get("source", "")).lower()
    return (not src.startswith("script:") or "llm" in src or "+" in src
            or str(item.get("module", "")).lower() == "llm" or file_name.startswith("llm."))


def note(item: dict, sev: str, reason: str, text: str, changes: list) -> dict:
    if item.get("severity") == sev:
        return item
    changes.append({"type": item.get("type"), "location": item.get("location"), "from": item.get("severity"),
                    "to": sev, "reason": reason, "evidence": str(item.get("evidence", ""))[:200]})
    item["severity_original"] = item.get("severity_original", item.get("severity"))
    item["severity"] = sev
    item["evidence"] = str(item.get("evidence", "")).rstrip() + " " + text
    item.setdefault("severity_reasons", []).append(reason)
    return item


def gate_cap(item: dict, file_name: str = "", changes: list | None = None) -> dict:
    """Hand-written number findings that cite a file go through the reproduction gate or stay yellow.

    Two places in the paper that disagree need no gate; a recount from the authors' data or a reading
    of their stored output does (B11: six hand-written reds, all recounting errors). A source that names a
    script but also "llm" (e.g. "script:numbers_crosscheck.py + llm") is hand-written (B11 rerun), and so is
    anything in llm.findings.json (B10-v18).
    """
    changes = [] if changes is None else changes
    if (item.get("severity") == "red" and item.get("type") in NUMBER_TYPES and is_hand_written(item, file_name)
            and FILE_REF.search(f"{item.get('location', '')} {item.get('evidence', '')}")):
        note(item, "yellow", "gate_cap", GATE_NOTE.strip(), changes)
        item["gate_capped"] = True
    return item


# ---- red lint: a red is "two sources that conflict"; a hand-written red has to show both values ----------
LINT_TYPES = NUMBER_TYPES | {"stat_inconsistency", "figure_data_mismatch", "param_mismatch", "grim_inconsistency",
                             "unit_mismatch"}
LINE_REF = re.compile(r"(?i:\b(?:lines?|p\.|pp\.|pages?|rows?|cells?|tables?|fig\.?|figures?|section|sec\.|steps?|"
                      r"stages?|chunks?|items?\s+no\.?|rounds?)\s*\d+(?:\s*[-–,]\s*\d+)*)"
                      r"|\.\w{1,5}:\d+(?:\s*[-–,]\s*\d+)*|\b[CFIPSTGXM]\d+\b")
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?:\s*%)?")
SUM_WORDS = re.compile(r"\b(sum|summed|total|add(?:s|ed)? up|adding|altogether)\b|合计|总和|加起来|相加", re.I)
EXPLICIT_SUM = re.compile(r"\d\s*\+\s*\d")


def salient_numbers(text: str, keep_small: bool = False) -> set[str]:
    """Numbers in a finding's evidence, without page/line/table references and small counting words."""
    t = LINE_REF.sub(" ", str(text))
    out = set()
    for m in NUMBER.finditer(t):
        v = m.group(0).replace(" ", "").replace(",", ".")
        try:
            f = float(v.rstrip("%"))
        except ValueError:
            continue
        if not keep_small and "." not in v and "%" not in v and 0 <= f <= 10:
            continue
        out.add(v.rstrip("%").rstrip("0").rstrip(".") if "." in v.rstrip("%") else v.rstrip("%"))
    return out


def red_lint(item: dict, file_name: str, changes: list) -> dict:
    if item.get("severity") != "red" or item.get("type") not in LINT_TYPES or not is_hand_written(item, file_name):
        return item
    ev = str(item.get("evidence", ""))
    if len(salient_numbers(ev, keep_small=True)) < 2 and item.get("type") != "param_mismatch":
        return note(item, "yellow", "red_lint_one_value",
                    "[Yellow, not red (build_passport.py red lint): a red finding names two sources that conflict, "
                    "and the evidence must show both values.]", changes)
    if SUM_WORDS.search(ev) and not EXPLICIT_SUM.search(ev):
        return note(item, "yellow", "red_lint_sum_without_cells",
                    "[Yellow, not red (build_passport.py red lint): the evidence relies on a total but does not "
                    "write the cells and the sum (e.g. 2 + 1 + 2 + 1 = 6).]", changes)
    return item


# ---- two intervals that may come from two disclosed interval methods (P03: [20, 59] vs [19, 57]) ----------
INTERVAL = re.compile(r"[\[(]\s*(-?\d*\.?\d+)\s*%?\s*(?:,|;|to|–|—|-)\s*(-?\d*\.?\d+)\s*%?\s*[\])]")
CI_METHOD_WORDS = re.compile(r"wilson|clopper|exact binomial|agresti|jeffreys|wald|sison|goodman|newcombe|bootstrap|"
                             r"\bBCa\b|profile likelihood|hartung|knapp|fieller|mid-p|delta method|credible", re.I)


def close_intervals(ev: str) -> bool:
    iv = []
    for m in INTERVAL.finditer(ev):
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo < hi and (lo, hi) not in iv:
            iv.append((lo, hi))
    for i in range(len(iv)):
        for j in range(i + 1, len(iv)):
            (a, b), (c, d) = iv[i], iv[j]
            width = ((b - a) + (d - c)) / 2
            if width > 0 and max(a, c) < min(b, d) and abs(a - c) <= 0.2 * width and abs(b - d) <= 0.2 * width:
                return True
    return False


def interval_method_cap(item: dict, ci_methods: list, changes: list) -> dict:
    if item.get("severity") not in ("red", "yellow") or item.get("type") not in LINT_TYPES:
        return item
    ev = str(item.get("evidence", ""))
    if not close_intervals(ev):
        return item
    named = [m.get("method") for m in ci_methods] if ci_methods else []
    if named or CI_METHOD_WORDS.search(ev):
        return note(item, "grey", "disclosed_interval_methods",
                    "[Grey (build_passport.py): the two intervals overlap and differ by less than a fifth of their width, "
                    f"and the paper or its supplement names interval methods ({', '.join(named) or 'see evidence'}); "
                    "two disclosed methods give intervals like these. Check which method each number used.]", changes)
    if item.get("severity") == "red":
        return note(item, "yellow", "close_intervals",
                    "[Yellow, not red (build_passport.py): the two intervals nearly coincide; check whether they were "
                    "computed with different interval methods before treating this as an error.]", changes)
    return item


# ---- analyses done in point-and-click / web tools (B06-v17: MetaboAnalyst) -------------------------------
def gui_cap(item: dict, gui: list, changes: list) -> dict:
    # v1.11.5: also a missing_artifact for such an analysis ("no MetaboAnalyst project file", "no Cox code" while the
    # Methods put Cox in SPSS; B02-v1114 F14/F15 - the same cases were judged false for phantom_experiment before)
    if item.get("type") not in ("phantom_experiment", "missing_artifact") or item.get("severity") not in ("red", "yellow") \
            or not gui:
        return item
    text = f"{item.get('evidence', '')} {item.get('location', '')}".lower()
    for g in gui:
        names = [g.get("tool", "")] + list(g.get("methods") or [])
        keys = [n.lower().split(" / ")[0].split(" (")[0] for n in names if n]
        if any(k and k in text for k in keys):
            return note(item, "grey", "gui_tool",
                        f"[Grey (build_passport.py): the Methods place this analysis in {g.get('tool')}, a point-and-click "
                        "or web tool, so no script is expected in the code. Ask for the project file or exported settings.]",
                        changes)
    return item


# ---- v1.10.1: three false reds from the first blind batch (B11-v191, B14-v191) -----------------------------
PCT_NOTATION = re.compile(r"10\^2|ratio is 100\b|fraction vs %|% vs fraction|percent(?:age)? vs (?:a )?fraction", re.I)
PORT_WORDS = re.compile(r"substitute\s*=\s*true|port_gap|\b(?:numpy|python|lifelines|scikit-learn|sklearn) port\b|our port\b", re.I)
SUM_EXPR = re.compile(r"(\d+(?:\.\d+)?)((?:\s*\+\s*\d+(?:\.\d+)?)+)\s*=\s*(\d+(?:\.\d+)?)")


PAPER_VS_STORED = re.compile(r"=\s*(-?\d*\.?\d+)\s*%?.{0,200}?\bstored output\b.{0,120}?\bgives\s+(-?\d*\.?\d+)", re.S)


def times_100(ev: str) -> bool:
    """numbers_crosscheck wording "reports 'x' = 0.68 % ...; stored output gives 68": the same number twice."""
    m = PAPER_VS_STORED.search(ev)
    if not m:
        return False
    a, b = sorted((abs(float(m.group(1))), abs(float(m.group(2)))))
    return 0 < a <= 1 and abs(b - 100 * a) <= 0.5


def notation_cap(item: dict, changes: list) -> dict:
    """0.64 in one place and 64% in another is the same number (B14-v191 F1; B14-v1101 F2: 0.68 vs 68)."""
    ev = str(item.get("evidence", ""))
    if item.get("severity") in ("red", "yellow") and (
            (item.get("type") == "unit_mismatch" and PCT_NOTATION.search(ev))
            or (item.get("type") in ("unit_mismatch", "magnitude_error", "archive_mismatch", "number_mismatch")
                and times_100(ev))):
        return note(item, "grey", "percent_fraction_notation",
                    "[Grey (build_passport.py): the two values differ by exactly a factor of 100, one written as a "
                    "fraction and one as a percentage; this is the same number in two notations, not a conflict.]", changes)
    return item


def port_rerun_cap(item: dict, changes: list, gate_counts: dict | None = None) -> dict:
    """A baseline that fails only in our port is a port gap, not a paper problem (B14-v191 F2).

    Also when the evidence does not say so: if the reproduction gate recorded port gaps and no value where the
    authors' stored output disagrees with the paper, a baseline that fails in the multiverse re-run comes from
    the same port (B12-v1101 F1: xgboost 3.2 gives 0.931, the authors' log and the paper give 0.9513).
    """
    c = gate_counts or {}
    # v1.10.5: an archive mismatch (paper vs the authors' own stored output) is reported by the gate as its own
    # finding and does not make our port's re-run evidence (B14-v1103 F24: gate port_gap 3, archive_mismatch 1;
    # the baseline failed only in our numpy port of the TF 1.4 code).
    gate_says_port = c.get("port_gap", 0) > 0 and not c.get("not_reproduced", 0)
    if item.get("type") == "rerun_mismatch" and item.get("severity") in ("red", "yellow") \
            and (PORT_WORDS.search(str(item.get("evidence", "")))
                 or (gate_says_port and "multiverse" in str(item.get("source", "")).lower())):
        return note(item, "grey", "port_rerun",
                    "[Grey (build_passport.py): the mismatch comes from our own port of the authors' code; the "
                    "reproduction gate decides whether the paper is affected.]", changes)
    # v1.11.1: the gate reproduced the paper's values from the authors' files and found no conflict, so a multiverse
    # baseline that fails is the adapter (B06-v1110 F15: AUROC 0.165 in our re-run, 7 values reproduced by the gate)
    if item.get("type") == "rerun_mismatch" and item.get("severity") in ("red", "yellow") \
            and "multiverse" in str(item.get("source", "")).lower() and c.get("reproduced", 0) > 0 \
            and not c.get("not_reproduced", 0) and not c.get("archive_mismatch", 0):
        return note(item, "grey", "gate_reproduced",
                    "[Grey (build_passport.py): the reproduction gate reproduced the paper's values from the authors' "
                    "files with no conflict; a baseline that fails only in the multiverse re-run comes from our "
                    "adapter.]", changes)
    # v1.11.14: the gate already showed that our port misses a value the authors' stored output reproduces
    # (port_gap) and found no stored value that disagrees with the paper; a value only our port re-computes, with no
    # stored output to compare, comes from the same port (B06-v11113p F2/F3: port without normalize gives
    # sensitivity 0.6875, stored AUROC matches the paper)
    if item.get("type") == "rerun_mismatch" and item.get("severity") in ("red", "yellow") \
            and "numbers_crosscheck" in str(item.get("source", "")) and c.get("port_gap", 0) > 0 \
            and not c.get("archive_mismatch", 0) and "No stored output" in str(item.get("evidence", "")):
        return note(item, "grey", "port_gap_same_port",
                    "[Grey (build_passport.py): the reproduction gate found our port off on a value the authors' "
                    "stored output reproduces, and no stored value that disagrees with the paper; this value has no "
                    "stored output and was re-computed by the same port.]", changes)
    if item.get("type") == "rerun_mismatch" and item.get("severity") == "red":
        # same rule as not_reproduced in the gate: only our re-run disagrees, so yellow at most (B14-v1101 F3)
        return note(item, "yellow", "rerun_only",
                    "[Yellow, not red (build_passport.py): only our own re-run disagrees with the paper; red needs two "
                    "sources that conflict.]", changes)
    return item


# ---- v1.10.3: two kinds of false or unverifiable flags from the third batch (B14-v1102, B13-v1102) ----------
PCT_PAIR = re.compile(r"=\s*(-?\d+(?:\.\d+)?)\s*%.{0,200}?\bstored output\b.{0,120}?\bgives\s+(-?\d+(?:\.\d+)?)", re.S)


def _dec(x: str) -> int:
    return len(x.split(".")[1]) if "." in x else 0


def rounding_explains(a: str, b: str, b_stored: bool = False) -> bool:
    """True when two printed numbers can be the same value written at two precisions (v1.11.7).

    - different numbers of decimals: the coarser one is the finer one rounded or truncated to the coarser precision
      (32.21 -> 32, 62.86 -> 62 or 63); 58.5 vs 58.05 is not (58.05 gives 58.0 or 58.1), it is a transcription error
      (Stefanetti 2020, Abstract I2 58.5% vs 58.05%), which v1.11.6 greyed as "within one point";
    - two integers one apart (63 vs 62): one rounded, one truncated, from the same 62.86 (B14-v1101 F4/F5).
    """
    if b_stored:  # "62.0" is how a script stores a printed "62%" (B14-v1102 F5); a paper's "21.0" keeps its decimal
        b = re.sub(r"\.0+$", "", b)
    fa, fb = float(a), float(b)
    da, db = _dec(a), _dec(b)
    if da == db:
        return da == 0 and abs(fa - fb) <= 1.0
    (c, dc), (f, _) = ((fa, da), (fb, db)) if da < db else ((fb, db), (fa, da))
    unit = 10 ** -dc
    return abs(f - c) <= 0.5 * unit + 1e-9 or 0 <= abs(f) - abs(c) < unit - 1e-9


def rounding_cap(item: dict, changes: list) -> dict:
    """A percentage in the paper and the authors' printed percentage that are the same value at two precisions.

    The authors' scripts often print a truncated integer (62.86 -> "62%") while the paper rounds (63%);
    B14-v1101 F4/F5 and B14-v1102 F5/F6 were all this. Counts (61 vs 60) are not percentages and are kept.
    v1.11.7: "within one point" alone is not enough; the two must be one value at two precisions (58.5 vs 58.05 stays).
    """
    if item.get("type") not in ("archive_mismatch", "number_mismatch") or item.get("severity") not in ("red", "yellow"):
        return item
    ev = str(item.get("evidence", ""))
    m = PCT_PAIR.search(ev)
    stored = bool(m)
    m = m or PCT_PIPE.search(ev)
    if not m:
        return item
    a, b = m.group(1), m.group(2)
    if float(a) > 1 and float(b) > 1 and rounding_explains(a, b, b_stored=stored):
        return note(item, "grey", "percent_rounding",
                    "[Grey (build_passport.py): the two percentages are one value at two precisions (a truncated "
                    "printout 62.86 -> 62, a rounded value in the paper 63).]", changes)
    return item


# numbers_crosscheck.py "reported with different values. Table 3: 32.21 metres | Discussion: 32 metres" (B09-v1116p F2)
VAL_PIPE = re.compile(r"reported with different values\.\s*[^|:]{1,160}:\s*(-?\d+(?:\.\d+)?)\s*([^\s|]{0,15})\s*\|"
                      r"\s*[^|:]{1,160}:\s*(-?\d+(?:\.\d+)?)\s*([^\s|.]{0,15})")


def precision_cap(item: dict, changes: list) -> dict:
    """v1.11.7: the same value printed with fewer decimals in another place is rounding, not a conflict
    (B09-v1116p F2: Table 3 32.21 m, Discussion 32 m, red). numbers_crosscheck.py read 32 as 32.0."""
    if item.get("type") not in ("number_mismatch", "sample_size") or item.get("severity") not in ("red", "yellow"):
        return item
    m = VAL_PIPE.search(str(item.get("evidence", "")))
    if not m or m.group(2) != m.group(4):
        return item
    a, b = m.group(1), m.group(3)
    if _dec(a) != _dec(b) and rounding_explains(a, b):
        return note(item, "grey", "precision_rounding",
                    f"[Grey (build_passport.py): {a} and {b} are one value printed at two precisions.]", changes)
    return item


HEDGE_LOC = re.compile(r"(?i)\b(?:almost|nearly|about|approximately|approx\.?|around|roughly|over|more than|"
                       r"less than|up to|close to)\s+\d|[~≈]\s*\d")


def approx_phrase_cap(item: dict, changes: list) -> dict:
    """v1.11.7: "almost 90%" for 87% is a rounded phrase (B10-v1116p F4, red)."""
    if item.get("type") not in ("number_mismatch",) or item.get("severity") not in ("red", "yellow"):
        return item
    ev = str(item.get("evidence", ""))
    m = VAL_PIPE.search(ev)
    if not m or not HEDGE_LOC.search(f"{item.get('location', '')} {ev[:m.end()]}"):
        return item
    a, b = float(m.group(1)), float(m.group(3))
    if abs(a - b) <= 0.1 * max(abs(a), abs(b)):
        return note(item, "grey", "approximate_phrase",
                    "[Grey (build_passport.py): one place states the value approximately in words (almost, about, "
                    "over); it is a rounded phrase, not a second value.]", changes)
    return item


STORED_CSV = re.compile(r"[\w./-]+\.(?:csv|tsv)\b")
VERSION_SUFFIX = re.compile(r"(?i)(?:\.\d+|[._](?:final|r\d|round_?\d|t\d|v\d|wave_?\d|post|pre|rev|revised|old|new))$")
WORK_ROOTS: list[Path] = []  # set by main() (bp_work) or by a replay


def find_stored(rel_path: str, roots: list[Path]) -> Path | None:
    for r in roots:
        for c in (r / rel_path, r / re.sub(r"^bp_work/", "", rel_path)):
            if c.exists() and c.is_file():
                return c
    name = Path(rel_path).name
    for r in roots:
        try:
            hit = next((p for p in r.rglob(name) if p.is_file()), None)
        except OSError:
            hit = None
        if hit:
            return hit
    return None


def versioned_cap(item: dict, changes: list, ctx: dict | None = None) -> dict:
    """v1.11.7: a red archive_mismatch against a stored table that has several versions of the compared column
    (consensus / consensus.1, mean / mean.1) is yellow until the authors' code that writes the paper's number is
    named (B10-v1116p F1/F3, B10-v18 F1/F2/F7: the Round-1 column of an intermediate table vs the paper's Table 2)."""
    if item.get("type") != "archive_mismatch" or item.get("severity") != "red" or item.get("archive_code"):
        return item
    text = f"{item.get('location', '')} {item.get('evidence', '')}"
    m = STORED_CSV.search(text)
    if not m:
        return item
    roots = list(WORK_ROOTS) + ([Path(ctx["root"]) / "bp_work"] if ctx and ctx.get("root") else [])
    path = find_stored(m.group(0), roots)
    if path is None:
        return item
    try:
        header = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return item
    cols = [c.strip().strip('"') for c in re.split(r"\t" if path.suffix.lower() == ".tsv" else ",", header)]
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(VERSION_SUFFIX.sub("", c), []).append(c)
    named = [v for k, v in groups.items() if k and len(v) > 1
             and any(re.search(rf"(?<![\w.]){re.escape(c)}(?![\w])", text) for c in v)]
    if not named:
        return item
    return note(item, "yellow", "versioned_columns",
                f"[Yellow, not red (build_passport.py): {path.name} holds several versions of the compared column "
                f"({', '.join(named[0])}); the paper's table may come from another version. Name the line of the "
                "authors' code that writes the paper's number before treating this as a conflict.]", changes)


SWAP_EV = re.compile(r"The paper reports '([^']+)' = (-?\d+(?:\.\d+)?)[^;]*;.{0,120}?\bgives (-?\d+(?:\.\d+)?)", re.S)


PORT_GAP_TEXT = re.compile(r"(?i)\bour (?:python |numpy |r )?port\b|\bour re-?implementation\b|\bport(?:ed)? of (?:the authors'? )?"
                           r"(?:code|[\w./-]+\.r)\b")


def port_restated_cap(item: dict, changes: list, ctx: dict | None = None) -> dict:
    """v1.11.8: a hand-written finding that rests on our port's numbers, in a run where the reproduction gate already
    recorded port gaps and no stored output disagrees with the paper, restates the port gap (B04-v1117p F13: "our port
    of the same data gives I2 53.4% and 57.5% ... the released data do not reproduce these two rows", the same values
    the gate had greyed as port_gap). The gap is ours, not the paper's."""
    gate = (ctx or {}).get("gate") or {}
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, (ctx or {}).get("file", "")):
        return item
    if not gate.get("port_gap") or gate.get("archive_mismatch"):
        return item
    ev = str(item.get("evidence", ""))
    if not PORT_GAP_TEXT.search(ev) or STORED_OUT.search(ev):
        return item
    if item.get("type") not in NUMBER_TYPES | {"missing_info", "unverifiable_claim", "figure_data_mismatch",
                                               "stat_inconsistency"}:
        return item
    return note(item, "grey", "port_restated",
                "[Grey (build_passport.py): the conflict comes from our port of the authors' code, which the reproduction "
                "gate has already recorded as a port gap; it is not a paper problem.]", changes)


def swapped_pairs(items: list[dict], changes: list) -> list[dict]:
    """v1.11.7: two archive_mismatch items whose paper values are each other's stored values are one red finding,
    whatever severity the gate gave each (Campbell 2017: SD 1.25 / 1.42 swapped between conditions; B03-v1116p F14/F15
    and B08-v1116p F19/F20 were greyed because each paper value also appears in the stored file)."""
    parsed = []
    for it in items:
        if it.get("type") != "archive_mismatch":
            continue
        m = SWAP_EV.search(str(it.get("evidence", "")))
        if m:
            parsed.append((it, m.group(1), m.group(2), m.group(3)))
    drop = set()
    for i, (a, na, ra, sa) in enumerate(parsed):
        for b, nb, rb, sb in parsed[i + 1:]:
            if id(a) in drop or id(b) in drop or float(ra) == float(rb):
                continue
            if not (float(ra) == float(sb) and float(rb) == float(sa)):
                continue
            wa, wb = set(re.findall(r"\w+", na.lower())), set(re.findall(r"\w+", nb.lower()))
            if len(wa & wb) < max(1, min(len(wa), len(wb)) - 2):
                continue
            keep, other = (a, b)
            changes.append({"type": "archive_mismatch", "location": keep.get("location"), "from": keep.get("severity"),
                            "to": "red", "reason": "swapped_pair", "evidence": str(keep.get("evidence", ""))[:200]})
            keep["severity_original"] = keep.get("severity_original", keep.get("severity"))
            keep["severity"] = "red"
            keep["evidence"] = (f"Values swapped between two quantities: the paper reports '{na}' = {ra} and '{nb}' = "
                                f"{rb}; the authors' stored output gives {sa} and {sb}, i.e. the two values are exchanged. "
                                f"[Red (build_passport.py, swapped_pair).]")
            keep.setdefault("severity_reasons", []).append("swapped_pair")
            keep.setdefault("merged", []).append({k: other.get(k) for k in ("type", "location", "severity", "source", "_orig")
                                                  if other.get(k) is not None})
            drop.add(id(other))
    return [it for it in items if id(it) not in drop]


# numbers_crosscheck.py "reported with different values. X: 63 % | results/predict-output.txt: 62 %" (B14-v1110 F1/F2)
PCT_PIPE = re.compile(r":\s*(-?\d+(?:\.\d+)?)\s*%\s*\|\s*[^|:]{1,120}:\s*(-?\d+(?:\.\d+)?)\s*%")


DEC_PAIR = re.compile(r"=\s*(-?\d*\.\d+)\b.{0,200}?\bstored output\b.{0,120}?\bgives\s+(-?\d*\.\d+)", re.S)


def last_digit_cap(item: dict, changes: list) -> dict:
    """Two decimals that differ by one unit in the last place the paper prints (v1.10.4, B03-v1103 F9: 1.65 vs 1.64,
    the stored 1.645 rounded two ways). Integers (counts such as 61 vs 60) never match this rule."""
    if item.get("type") not in ("archive_mismatch", "number_mismatch") or item.get("severity") not in ("red", "yellow"):
        return item
    m = DEC_PAIR.search(str(item.get("evidence", "")))
    if not m:
        return item
    pa, st = m.group(1), m.group(2)
    unit = 10 ** -min(len(pa.split(".")[1]), len(st.split(".")[1]))
    if abs(float(pa) - float(st)) <= unit * 1.0001:
        return note(item, "grey", "last_digit_rounding",
                    f"[Grey (build_passport.py): {pa} and {st} differ by one unit in the last printed decimal, "
                    "which is what rounding the same value two ways gives.]", changes)
    return item


def robustness_port_cap(item: dict, changes: list, gate_counts: dict | None = None) -> dict:
    """A multiverse result computed on our own port of the authors' code is at most yellow (B14-v1102 F4).

    Fragility is a statement about our re-implementation unless the baseline was run with the authors' own
    code; the reproduction gate records a port gap or an unreproduced value when it was not.
    """
    c = gate_counts or {}
    if item.get("type") == "fragile_conclusion" and item.get("severity") == "red" \
            and (c.get("port_gap", 0) > 0 or c.get("not_reproduced", 0) > 0
                 or PORT_WORDS.search(str(item.get("evidence", "")))):
        return note(item, "yellow", "robustness_on_port",
                    "[Yellow, not red (build_passport.py): the multiverse ran on our re-implementation of the "
                    "authors' code (the reproduction gate recorded a port gap), so the fragility needs to be "
                    "confirmed with the authors' own code.]", changes)
    return item


def subgroup_total_cap(item: dict, changes: list) -> dict:
    """The paper's number equals one addend of a total that the other source gives (B11-v191 F1: 2 vs 2 + 1 = 3)."""
    if item.get("severity") != "red" or item.get("type") not in NUMBER_TYPES:
        return item
    text = f"{item.get('location', '')} {item.get('evidence', '')}"
    for m in SUM_EXPR.finditer(text):
        addends = {m.group(1)} | set(re.findall(r"\d+(?:\.\d+)?", m.group(2)))
        total = m.group(3)
        rest = salient_numbers(SUM_EXPR.sub(" ", text), keep_small=True)
        if total in rest and addends & rest:
            return note(item, "yellow", "subgroup_total",
                        f"[Yellow, not red (build_passport.py): one value equals a part ({', '.join(sorted(addends & rest))}) "
                        f"of the total {total} given by the other source; check whether the paper's number refers to a "
                        "subgroup before treating this as a conflict.]", changes)
    return item


LOW_VALUE = {"seed_missing", "dependency_unpinned"}


def consolidate_low_value(items: list[dict], changes: list) -> list[dict]:
    """One finding per run for an unset seed or unpinned dependencies, with all locations (B12-v19: ten seed findings)."""
    out, first = [], {}
    has_seed = any(i.get("type") == "seed_missing" for i in items)
    for it in items:
        t = it.get("type")
        if has_seed and t == "missing_info" and SEED_TOPIC.search(str(it.get("evidence", ""))):
            t = "seed_missing"  # v1.11: the checklist M1 item and a hand-written "no seed" are the same finding
        if t in LOW_VALUE and it.get("severity") in ("yellow", "grey"):
            if t in first:
                k = first[t]
                k.setdefault("also_at", []).append(it.get("location"))
                if "_orig" in it:
                    k.setdefault("merged", []).append({"type": t, "location": it.get("location"), "_orig": it["_orig"]})
                k["claim_ids"] = sorted(set(k.get("claim_ids") or []) | set(it.get("claim_ids") or []))
                changes.append({"type": t, "location": it.get("location"), "from": it.get("severity"), "to": "merged",
                                "reason": "low_value_repeat", "evidence": str(it.get("evidence", ""))[:200]})
                continue
            first[t] = it
        out.append(it)
    for k in first.values():
        if k.get("also_at"):
            k["evidence"] = str(k.get("evidence", "")).rstrip() + f" Also at: {'; '.join(map(str, k['also_at'][:12]))}."
    return out


# ---- v1.11.3: one finding per pattern, not one per line or per copy of a file -------------------------------
# B16-v1111 listed the same StandardScaler call 16 times (two scripts x four lines x the capsule and the agent's
# copy) and the same test-set fit 8 times; B06-v1110 listed every test-set fit twice (_unz/ and code/). Across all
# judged runs no script source ever gave two different true findings of the same type in one run, so script flags
# of these sources are merged per type, with every location kept in also_at.
GROUP_SOURCES = {"script:leakage_scan.py", "script:methods_code_check.py", "script:data_checks.py"}
# hand-written flags join a head by type only where a run has one such issue; for leakage and test-set reuse a
# hand-written flag often adds a second mechanism (B12-v1103 F11: label-derived feature next to the scaler), so
# those join only when they name the same fitted object.
GROUP_TYPES_HW = {"phantom_experiment", "duplicate_output", "split_overlap"}
STOP = set("which where their there these those about after before other would could should paper reports report "
           "value values stored output table figure using based without within between".split())
OTHER_MECH = re.compile(r"(?i)\blabels?\b|\btarget\b|feature selection|select\w* features|correlation|\bPCA\b|weights?\b|"
                        r"imput|SMOTE|oversampl|PCC|tuning|hyper-?parameter")


def content_words(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z_\-]{4,}", str(text))} - STOP


def text_twin(a: dict, b: dict, cut: float = 0.35) -> bool:
    wa, wb = content_words(a.get("evidence", "")), content_words(b.get("evidence", ""))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= cut


CALL_TOKEN = re.compile(r"\b([A-Za-z_]\w*\.(?:fit_transform|fit|transform|predict)\(\s*[A-Za-z_]\w*)")


def group_key(it: dict):
    src, t = str(it.get("source", "")), it.get("type")
    if src in GROUP_SOURCES or (src == "script:deep_checks.py" and t == "phantom_experiment"):
        return t
    return None


def group_repeats(items: list[dict], changes: list, merge_hand_written: bool = True) -> list[dict]:
    heads: dict = {}
    calls: dict = {}
    out: list[dict] = []
    def absorb(head, it, reason):
        head.setdefault("also_at", []).append(it.get("location"))
        head.setdefault("merged", []).append({"type": it.get("type"), "location": it.get("location"),
                                              "severity": it.get("severity"), "file": it.get("file"),
                                              "source": it.get("source"), **({"_orig": it["_orig"]} if "_orig" in it else {})})
        head["claim_ids"] = sorted(set(head.get("claim_ids") or []) | set(it.get("claim_ids") or []))
        if ORDER.get(it.get("severity"), 9) < ORDER.get(head.get("severity"), 9):
            head["severity"] = it.get("severity")
        changes.append({"type": it.get("type"), "location": it.get("location"), "from": it.get("severity"),
                        "to": "merged", "reason": reason, "evidence": str(it.get("evidence", ""))[:200]})
    # pass 1: script flags, one head per type
    for it in items:
        k = group_key(it) if it.get("severity") in ("red", "yellow") else None
        if k is None:
            out.append(it)
            continue
        if k in heads:
            absorb(heads[k], it, "same_pattern")
            continue
        heads[k] = it
        out.append(it)
        for c in CALL_TOKEN.findall(str(it.get("evidence", ""))):
            calls.setdefault(c, it)
    if not merge_hand_written:
        res = out
    else:
        # pass 2: a hand-written flag that restates a script head (same type, or the same fitted object)
        res = []
        for it in out:
            if it.get("severity") in ("red", "yellow") and it.get("hand_written") and group_key(it) is None:
                head = heads.get(it.get("type")) if it.get("type") in GROUP_TYPES_HW else None
                if head is None:
                    for c in CALL_TOKEN.findall(str(it.get("evidence", ""))):
                        head = calls.get(c) or head
                # the same scaler fitted before the split, with no second mechanism (B12-v1111 F7)
                if head is None and it.get("type") in ("leakage_risk", "test_set_reuse") and it.get("type") in heads \
                        and not OTHER_MECH.search(str(it.get("evidence", ""))):
                    h = heads[it["type"]]
                    cls = re.findall(r"\b(\w*(?:Scaler|Normalizer|Imputer|Encoder|PCA))\b", str(h.get("evidence", "")))
                    if cls and any(c in str(it.get("evidence", "")) for c in cls):
                        head = h
                # the same sentence written twice: a hand-written flag that restates a script flag of its type
                if head is None:
                    head = next((x for x in out if x is not it and x.get("type") == it.get("type") and
                                 x.get("severity") in ("red", "yellow") and not x.get("hand_written") and
                                 text_twin(it, x)), None)
                if head is not None and head is not it:
                    absorb(head, it, "restates_script")
                    continue
            res.append(it)
    for h in heads.values():
        if h.get("also_at"):
            # the same file under capsule/, code/paper_code/ or _unz/ counts once (by file name and line)
            def short(x):
                m = re.match(r"\s*(\S+?)((?::\d+|\s+code cell \d+ line \d+)?)\s*$", strip_prefix(str(x)))
                return (Path(m.group(1)).name + m.group(2)) if m else strip_prefix(str(x))
            own = short(h.get("location"))
            uniq = [u for u in dict.fromkeys(short(x) for x in h["also_at"] if x) if u != own]
            if uniq:
                h["evidence"] = str(h.get("evidence", "")).rstrip() + f" Same pattern at {len(uniq)} more place(s): " \
                    + "; ".join(uniq[:8]) + ("; ..." if len(uniq) > 8 else "") + "."
    return res


# ---- v1.11.16 ------------------------------------------------------------------------------------------------
# Defaults of common estimation calls. A hand-written flag that says "no method= was passed, so the default (not X)
# was used" is wrong when the default IS X (B09-v11115p F14/F15: metafor rma() defaults to REML).
KNOWN_DEFAULTS = [
    # (call regex, argument, default value regex, human text)
    (r"\brma(?:\.uni)?\s*\(", r"method", r"REML", "metafor rma() uses method=\"REML\" when method= is not given"),
    (r"\blmer\s*\(", r"REML", r"REML", "lme4 lmer() fits by REML (REML=TRUE) by default"),
    (r"\bt\.test\s*\(", r"var\.equal", r"Welch", "R t.test() runs the Welch test (var.equal=FALSE) by default"),
    (r"\bcoxph\s*\(", r"ties", r"Efron", "survival coxph() uses the Efron method for ties by default"),
    (r"\bmetagen\s*\(|\bmetacont\s*\(|\bmetabin\s*\(", r"method\.tau", r"REML",
     "meta (>= 5.0) uses method.tau=\"REML\" by default"),
]
NO_ARG = re.compile(r"\bwithout\s+(?:a\s+|an\s+|the\s+)?(?:explicit\s+)?[`'\"]?(?P<a>[\w.]+)\s*=|\bno\s+[`'\"]?(?P<b>[\w.]+)\s*="
                    r"|\b(?:does not|doesn't|never)\s+(?:pass|set|specify|give)\s+[`'\"]?(?P<c>[\w.]+)\s*="
                    r"|\bdefault\s+(?:estimator|method|setting|value)", re.I)


def default_arg_cap(item: dict, changes: list, file_name: str = "") -> dict:
    """A hand-written flag that blames an omitted argument whose default is exactly what the paper states."""
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, file_name):
        return item
    ev = str(item.get("evidence", ""))
    if not NO_ARG.search(ev):
        return item
    for call, arg, dflt, text in KNOWN_DEFAULTS:
        if re.search(call, ev) and re.search(dflt, ev, re.I) and re.search(r"\b" + arg + r"\b|default", ev, re.I):
            # the flag only stands if it names a non-default value actually passed somewhere for the same call
            if re.search(arg + r"\s*=\s*[\"']?(?!" + dflt + r")\w", ev, re.I) and not re.search(r"without|no\s+" + arg, ev, re.I):
                continue
            return note(item, "grey", "known_default",
                        f"[Grey (build_passport.py, known_default): {text}, which is what the paper states; omitting "
                        f"the argument does not change the analysis.]", changes)
    return item


EQ_FWD = re.compile(r"(?<![\d.])(\d+(?:\s*\+\s*\d+)+)\s*=\s*(\d+)(?![\d.])")
EQ_BACK = re.compile(r"(?<![\d.])(\d+)\s*=\s*(\d+(?:\s*\+\s*\d+)+)(?![\d.])")


def _sums(ev: str) -> dict:
    """Valid count equations in the evidence, category names dropped: 'MAJOR_ERROR 29 + DECISION_ERROR 1 = 30'."""
    flat = re.sub(r"\b[A-Za-z][\w-]*\s+(?=\d)", "", ev)
    out = {}
    for m in EQ_FWD.finditer(flat):
        parts = [int(x) for x in re.findall(r"\d+", m.group(1))]
        if sum(parts) == int(m.group(2)):
            out[int(m.group(2))] = set(parts)
    for m in EQ_BACK.finditer(flat):
        parts = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if sum(parts) == int(m.group(1)):
            out[int(m.group(1))] = set(parts)
    return out


def category_sum_cap(item: dict, changes: list, file_name: str = "") -> dict:
    """A hand-written count mismatch whose own evidence shows the paper's number is the sum of a different set of
    the same categories (B01-v11115p F1: 37 = 29 + 1 + 7 counts EYEBALL_ERROR as major, 30 = 29 + 1 does not). That
    is a question of definition for the authors, not a wrong number."""
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, file_name):
        return item
    if item.get("type") not in NUMBER_TYPES | {"stat_inconsistency"}:
        return item
    sums = _sums(str(item.get("evidence", "")))
    if len(sums) < 2:
        return item
    keys = list(sums)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = sums[keys[i]], sums[keys[j]]
            if a & b and a != b:
                lo, hi = sorted((keys[i], keys[j]))
                return note(item, "grey", "category_definition",
                            f"[Grey (build_passport.py, category_definition): both {lo} and {hi} are sums of the same "
                            f"categories in the data; the paper uses a different grouping, which is a definition to ask "
                            f"the authors about, not a wrong count.]", changes)
    return item


STAT_TAIL = {"b", "beta", "β", "se", "ci", "low", "high", "lower", "upper", "lo", "hi", "p", "t", "z", "df", "or",
             "hr", "rr", "estimate", "coef", "sd", "mean", "n", "r", "f"}
REPORT_RE = re.compile(r"The paper reports '([^']+)' = (\S+)")


def _row_model(label: str):
    toks = label.split()
    model = " ".join(toks[:2]) if len(toks) >= 3 else label
    row = toks[:]
    while len(row) > 2 and row[-1].lower().strip("()") in STAT_TAIL:
        row.pop()
    return model, " ".join(row)


def group_archive_cells(items: list[dict], changes: list) -> list[dict]:
    """v1.11.16: archive_mismatch flags from numbers_crosscheck.py, one per table cell, for the same model in the same
    stored file (B03-v11115p2: 20 of 25 red/yellow were cells of three problems). Cells of one row become one flag;
    when one model has mismatching cells in three or more rows, the whole model is one flag ("this model is not the
    one stored"). The highest severity is kept and every cell is listed."""
    groups: dict = {}
    for it in items:
        if it.get("type") != "archive_mismatch" or it.get("severity") not in ("red", "yellow") \
                or it.get("hand_written") or "numbers_crosscheck" not in str(it.get("source", "")):
            continue
        m = REPORT_RE.search(str(it.get("evidence", "")))
        stored = re.search(r"stored output \(([^)]+)\)", str(it.get("evidence", "")))
        if not m:
            continue
        model, row = _row_model(m.group(1))
        groups.setdefault((stored.group(1) if stored else "", model), []).append((it, row, m.group(1), m.group(2)))
    drop = set()
    for (stored, model), cells in groups.items():
        rows = {}
        for c in cells:
            rows.setdefault(c[1], []).append(c)
        whole = len(rows) >= 3 and len(cells) >= 4
        bundles = [cells] if whole else [v for v in rows.values() if len(v) >= 2]
        for b in bundles:
            b = sorted(b, key=lambda c: ORDER.get(c[0].get("severity"), 9))
            head = b[0][0]
            others = b[1:]
            def val(c):
                g = re.search(r"gives (\S+?)\.?(?:\s|$)", str(c[0].get("evidence", "")))
                return f"{c[2]}: paper {c[3]} vs stored {g.group(1) if g else '?'}"
            for c in others:
                it = c[0]
                drop.add(id(it))
                head.setdefault("merged", []).append({"type": it.get("type"), "location": it.get("location"),
                                                      "severity": it.get("severity"), "file": it.get("file"),
                                                      "source": it.get("source"),
                                                      **({"_orig": it["_orig"]} if "_orig" in it else {})})
                head["claim_ids"] = sorted(set(head.get("claim_ids") or []) | set(it.get("claim_ids") or []))
                changes.append({"type": it.get("type"), "location": it.get("location"), "from": it.get("severity"),
                                "to": "merged", "reason": "same_model_cells" if whole else "same_row_cells",
                                "evidence": str(it.get("evidence", ""))[:200]})
            what = f"model '{model}'" if whole else f"row '{b[0][1]}'"
            head["evidence"] = str(head.get("evidence", "")).rstrip() + \
                f" The {what} differs from {stored or 'the stored output'} in {len(b)} cells" + \
                (f" across {len(rows)} rows, so the reported model is not the stored one" if whole else "") + ": " + \
                "; ".join(val(c) for c in b[:12]) + ("; ..." if len(b) > 12 else "") + "."
    return [it for it in items if id(it) not in drop]


# ---- near-duplicates: the same conflict written twice (B10-v18: F1/F5, F2/F6, F3/F16) ----------------------
FAMILY = {t: "numbers" for t in NUMBER_TYPES | {"figure_data_mismatch"}}


def merge_near_duplicates(items: list[dict], changes: list) -> list[dict]:
    kept: list[dict] = []
    for it in items:
        fam = FAMILY.get(it.get("type"), it.get("type"))
        nums = salient_numbers(it.get("evidence", ""))
        twin = None
        if len(nums) >= 2 and it.get("severity") in ("red", "yellow", "grey"):
            for k in kept:
                if FAMILY.get(k.get("type"), k.get("type")) != fam:
                    continue
                kn = salient_numbers(k.get("evidence", ""))
                if len(kn) >= 2 and len(nums & kn) / len(nums | kn) >= 0.6:
                    twin = k
                    break
        if twin is None:
            kept.append(it)
            continue
        better, worse = (it, twin) if ORDER.get(it.get("severity"), 9) < ORDER.get(twin.get("severity"), 9) else (twin, it)
        better["claim_ids"] = sorted(set(better.get("claim_ids") or []) | set(worse.get("claim_ids") or []))
        better.setdefault("merged", []).append({"type": worse.get("type"), "location": worse.get("location"),
                                                "severity": worse.get("severity"), "file": worse.get("file"),
                                                **({"_orig": worse["_orig"]} if "_orig" in worse else {})})
        changes.append({"type": worse.get("type"), "location": worse.get("location"), "from": worse.get("severity"),
                        "to": "merged", "reason": "near_duplicate", "evidence": str(worse.get("evidence", ""))[:200]})
        if better is it:
            kept[kept.index(twin)] = it
    return kept


# ---- v1.10.5: hand-written flags that contradict their own evidence (B11-v1101 F7, B14-v1103 F3) ----------
SELF_CONSISTENT = re.compile(
    r"(?:\b(?:is|are|was|were)\s+(?:fully\s+|therefore\s+|also\s+)?|[—–-]\s*)consistent\s+with\s+(?:the\s+)?"
    r"(?:data|paper|text|code|methods|stored output|archived? (?:output|table)|authors'? (?:files?|output|data))\b"
    r"|\bno\s+conflict\b|\bthe\s+two\s+are\s+equivalent\b", re.I)
CONTRAST = re.compile(r"\b(?:but|however|whereas|while|except|yet)\b", re.I)


def self_consistent_cap(item: dict, changes: list, file_name: str = "") -> dict:
    """A hand-written red/yellow whose own evidence says the values agree (B11-v1101 F7: "6 of 7 is consistent
    with the data"; B14-v1104 F19: "num_epochs keeps its default 1500 - consistent with the text"). Kept when the
    same sentence goes on with "but"/"however" (a real conflict elsewhere)."""
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, file_name):
        return item
    ev = str(item.get("evidence", ""))
    for m in SELF_CONSISTENT.finditer(ev):
        rest = re.split(r"(?<=[.;])\s", ev[m.end():], maxsplit=1)[0]
        if not CONTRAST.search(rest):
            return note(item, "grey", "self_consistent",
                        "[Grey (build_passport.py): the evidence itself says the values agree; the finding "
                        "records no conflict.]", changes)
    return item


SIGNATURE = re.compile(r"signature:?\s*(?:def\s+)?([A-Za-z_]\w*)\s*\(([^()]*)\)", re.I)
NUM_LITERAL = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def split_args(text: str) -> list[str]:
    return [a.strip() for a in text.split(",") if a.strip()]


def positional_cap(item: dict, changes: list, file_name: str = "") -> dict:
    """Recount positional arguments when the evidence quotes both a call and the function's signature.

    B14-v1103 F3 (red, false): model(X, Y, Xt, Yt, L_Z, 0.15) against model(X_train, Y_train, X_test, Y_test,
    L_Z, learning_rate=0.0001, num_epochs=1500, ...): the sixth argument is learning_rate, but the model wrote
    "num_epochs = 0.15". When a number passed by position is named as a different parameter, the finding is
    grey and the evidence says which parameter it is.
    """
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, file_name):
        return item
    ev = str(item.get("evidence", ""))
    for sm in SIGNATURE.finditer(ev):
        name = sm.group(1)
        params = [p.split("=")[0].split(":")[0].strip().lstrip("*") for p in split_args(sm.group(2))]
        params = [p for p in params if p and p not in ("self", "...")]
        for cm in re.finditer(r"\b" + re.escape(name) + r"\s*\(([^()]*)\)", ev):
            if sm.start() <= cm.start() < sm.end():
                continue
            pos = []
            for a in split_args(cm.group(1)):
                if re.match(r"[A-Za-z_]\w*\s*=", a):
                    break
                pos.append(a)
            for i, a in enumerate(pos):
                if i >= len(params) or not NUM_LITERAL.fullmatch(a):
                    continue
                for nm in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*" + re.escape(a) + r"(?![\d.])", ev):
                    if nm.group(1) in params and nm.group(1) != params[i]:
                        return note(item, "grey", "positional_miscount",
                                    f"[Grey (build_passport.py): in {name}({cm.group(1).strip()}) the value {a} is "
                                    f"positional argument {i + 1}, which the quoted signature names {params[i]}, "
                                    f"not {nm.group(1)}.]", changes)
    return item


# ==== v1.11: hand-written findings tested against the materials and against an executed check ================
# Every rule below comes from a finding that a human checked against the authors' files and judged false
# (scoring/results/*/adjudication.csv). Rules that need run files read bp_out/code_facts.json (deep_checks.py).

VALUE_CONFLICT = NUMBER_TYPES | {"param_mismatch", "figure_data_mismatch", "stat_inconsistency", "fragile_conclusion"}
DATASET = re.compile(r"(?i)\bdata\s?set[\s_-]*(\d+)\b")
METRIC_PAIRS = [(r"(?i)\bc-?index\b|\bconcordance\b", r"(?i)\bAU(?:RO)?C\b|area under the (?:ROC )?curve"),
                (r"(?i)\bmacro\b", r"(?i)\bbinary\b|\bmicro\b|\bweighted\b")]
DIFF_QTY = re.compile(r"(?i)\b(?:describe|describes|counts?|is|are)\s+(?:a\s+)?different (?:quantity|quantities|definition)"
                      r"|\bnot the same quantity\b|\bdifferent (?:quantity|quantities)\b")
OUR_PORT_VALUE = re.compile(r"(?i)\bour (?:python|numpy|lifelines|r|scikit-learn|sklearn|pandas)?\s*(?:/\s*\w+\s*)?"
                            r"(?:port|re-?run|re-?implementation)\b[^.]{0,80}?\b(?:gives?|yields?|returns?|produces?)\b")
ENDPOINT_FORK = re.compile(r"(?i)single changes that flip it:\s*((?:(?:endpoint|outcome|target|label|estimand|"
                           r"population|response)\s*=\s*[^;,.]+[;,]?\s*)+)\.?\s*$")
EPV = re.compile(r"(?i)events?[- ]per[- ](?:variable|covariate|predictor|parameter)|\bEPV\b|fewer than one event per")
PSEUDO_GROUP = re.compile(r"(?i)\b(?:row|index|idx)\s*//\s*\d+|pseudo-?(?:patient|subject|group)s?\b")
STORED_TOTAL = re.compile(r"\bTOTAL\s*=\s*(\d+)")
CLAIMED_SUBSET = re.compile(r"(?i)(?:first|rest on|evaluated on|evaluates on|only)\s+(\d+)\s+(?:rows|cases|samples)")
ENDPOINT_TOKEN = re.compile(r"\b(OS|PFS|DFS|EFS|RFS|DSS)\b")
COLUMN_CLAIM = re.compile(r"(?i)without (?:a|an) ['\"]?([\w][\w\- ]{2,40}?)['\"]? (?:label|column)|"
                          r"\bno ['\"]?([\w][\w\- ]{2,40}?)['\"]? column|lacks? (?:a|an|the) ['\"]?([\w][\w\- ]{2,40}?)['\"]? column")
BLAMES_OUTPUT = re.compile(r"(?i)missing from the stored|is missing from|dropped between|which (?:feature|covariate) was "
                           r"dropped|one (?:tested )?(?:feature|covariate) is missing")
AGENT_PATH = re.compile(r"(?i)(^|[/\s(])(repro_|adapter|port_|bp_|whatif_|check_)[\w.-]*\.(py|r)\b")
CHECKLIST_ITEM = re.compile(r"^\s*\[(?:G|M)\d+\]")
CODE_LOCUS = re.compile(r"(?i)\.(?:py|r|rmd|ipynb|csv|html|txt)\b|:\d+\b|\b(?:KFold|fit_transform|fit\(|train_test_split|"
                        r"MinMaxScaler|StandardScaler|split|load_data|read_csv|read\.csv)\b")
NO_SEED = re.compile(r"(?i)without (?:a |any )?(?:fixed |random )?seeds?\b|no random seed;|no (?:fixed |random )?seed (?:is )?(?:set|fixed)|unseeded|"
                     r"no random_state|seed is not (?:set|fixed)|does not (?:set|fix) (?:a |the )?(?:random )?seed")
NOT_ALL_MODELS = re.compile(r"(?i)not contain every|does not contain all|missing from the stored|fewer rows than|"
                            r"is missing|has no \w+ row|every model the code fits")
# stages of a sampling flow (B11-v1110 F3: 'Institutions sampled' 19 + 14 = 33 vs '20 forms' obtained)
STAGE_A = r"sampled|contacted|approached|invited|screened|eligible|assessed|identified|searched"
STAGE_B = r"obtained|received|returned|included|analy[sz]ed|completed|responded|forms?\b|coded"


def stage_conflict(text: str):
    a = re.search(r"(?i)'[^']{0,40}\b(" + STAGE_A + r")\b[^']{0,40}'|\b(?:\d+\s+)?(?:institutions?|sites?|RECs?|"
                  r"centres?|centers?|records?|participants?)\s+(" + STAGE_A + r")\b", text)
    b = re.search(r"(?i)\b\d+\s+(?:\w+\s+){0,2}(" + STAGE_B + r")|\b(" + STAGE_B + r")\b[^.]{0,30}\b\d+", text)
    if a and b:
        sa = next(g for g in a.groups() if g).lower()
        sb = next(g for g in b.groups() if g).lower()
        if sa != sb:
            return (sa, sb)
    return None


SEED_TOPIC = re.compile(r"(?i)\[M1\]|\brandom seed|\bno seed\b|random_state|set\.seed")
FUTURE_WORK = re.compile(r"(?i)\b(?:is|are|remains?)\s+(?:still\s+|currently\s+|largely\s+)?unknown\b|\bfuture\s+(?:stud(?:y|ies)|work|"
                         r"research|trials?)\b|\bcould\s+(?:compare|examine|test|investigate|explore|address)\b|"
                         r"\bremains?\s+to\s+be\b|\bwarrants?\s+further\b|\bfurther\s+(?:research|stud(?:y|ies)|work)\s+"
                         r"(?:is|are)\s+(?:needed|required|warranted)\b|\bwe\s+(?:hypothesi[sz]e|speculate)\b|\bopen question\b")
# v1.11.5: publication-bias tests (Egger, Begg, trim-and-fill) are run in the same script as the meta-analysis they
# belong to; when they are missing from that script it is a real gap (P02 item 2.6, greyed in B04-v1114 F11), so
# they are no longer in this list.
STAT_TEST = re.compile(r"(?i)names (?:Cox regression|Kaplan-Meier|t-test|chi-square test|Mann-Whitney|ANOVA|"
                       r"Bonferroni|Holm|Benjamini)")
# v1.11.4: words the evidence must contain to be about the item at all (only items with a narrow topic)
CHECKLIST_TOPIC = {
    "G3": r"(?i)missing|imput|\bNA\b|NaN|complete[- ]case|dropna|na\.omit",
    "G4": r"(?i)outlier|extreme|filter|remov|trim|winsor|z-?score|IQR|quantile|exclu",
    "M7": r"(?i)duplicat|overlap|identical|same (?:rows?|samples?|records?)|leak",
    "W1": r"(?i)reagent|supplier|catalog|catalogue|\blot\b|vendor|antibod|kit\b",
    "W2": r"(?i)instrument|model|setting|calibrat|scanner|spectromet|machine|device|camera|microscop",
    "W4": r"(?i)cell[- ]?line|passage|mycoplasma|authenticat|ATCC|\bSTR\b|culture",
    "W6": r"(?i)replicat|technical|biological|\bn\s*=",
}
NOTHING_REMOVED = re.compile(r"(?i)\bno (?:outlier )?(?:filter|filtering|removal|outlier removal)\b|"
                             r"has no (?:filter|outlier)|no (?:rows?|samples?|outliers?) (?:were |are )?(?:removed|excluded|dropped)|"
                             r"no filtering step|"
                             # v1.11.6: "No outlier rule stated or applied" (B03-v1115 F3)
                             r"no outlier (?:rule|definition)[^.;]{0,40}\b(?:applied|implemented|in (?:the )?code)\b")
# v1.11.6: the paper says an alternative analysis "does not change the results" and the authors' code holds it as a
# commented-out line the reader can switch on (B08-v1114 F12, B03-v1115 F6: two suspicious participants). That is a
# check a reader can run, not missing information.
COMMENTED_ALT = re.compile(r"(?i)comment(?:ed)?[- ]out|commented (?:line|code)|#\s*filter\(")
NO_CHANGE_CLAIM = re.compile(r"(?i)(?:results?|findings?|conclusions?)\s+(?:do|does|did)\s+not\s+change|"
                             r"not change (?:the )?(?:results?|findings?)|results? (?:are|were) unchanged")
CHECKLIST_OK = re.compile(r"(?i)\b(?:fitted|fit|computed|estimated|calculated|learned)\s+(?:only\s+)?on\s+the\s+training\s+"
                          r"(?:file|set|data|fold|split|portion)s?(?:\s+only)?\b|\bonly\s+on\s+the\s+training\b|"
                          r"consistent with the split|\bwithin (?:each|every) (?:training )?fold\b|inside the (?:CV|cross-validation) loop")


def _norm(s: str) -> str:
    return re.sub(r"[\s._\-]+", " ", str(s)).strip().lower()


def _hw_flag(item: dict, file_name: str) -> bool:
    return item.get("severity") in ("red", "yellow") and is_hand_written(item, file_name)


def _welch_df(s1, n1, s2, n2):
    a, b = s1 * s1 / n1, s2 * s2 / n2
    return (a + b) ** 2 / (a * a / (n1 - 1) + b * b / (n2 - 1))


def welch_matches(ev: str) -> bool:
    m = re.search(r"\bt\s*\(\s*(\d+(?:\.\d+)?)\s*\)", ev)
    if not m:
        return False
    df = float(m.group(1))
    sds = [float(b) for _, b in re.findall(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", ev)]
    sds += [float(x) for x in re.findall(r"(?i)\bSDs?\s*(?:=|of)?\s*(\d+(?:\.\d+)?)", ev)]
    ints = {int(x) for x in re.findall(r"(?<![\d.])(\d{1,6})(?![\d.])", ev) if 5 <= int(x) <= 100000}
    ns = set(ints) | {a - b for a in ints for b in ints if a > b >= 5}
    for i, s1 in enumerate(sds):
        for s2 in sds[i + 1:]:
            for n1 in ns:
                for n2 in ns:
                    try:
                        if abs(_welch_df(s1, n1, s2, n2) - df) < 1.0:
                            return True
                    except ZeroDivisionError:
                        continue
    return False


def confusion_consistent(ev: str, facts: dict) -> bool | None:
    g = {k.upper(): int(v) for k, v in re.findall(r"(?i)\b(TRUE POSITIVE|ACTUAL POSITIVE|TOTAL POSITIVE|EQUAL)\s*=\s*(\d+)", ev)}
    if not {"TRUE POSITIVE", "ACTUAL POSITIVE"} <= set(g):
        return None
    for c in (facts.get("confusion") or {}).values():
        if c.get("TP") == g.get("TRUE POSITIVE") and c.get("consistent"):
            return True
    total = re.search(r"(?i)(\d+)\s+(?:validation|test|evaluated)?\s*cases|TOTAL\s*=\s*(\d+)", ev)
    if total and "TOTAL POSITIVE" in g and "EQUAL" in g:
        n = int(total.group(1) or total.group(2))
        tp, ap, pp = g["TRUE POSITIVE"], g["ACTUAL POSITIVE"], g["TOTAL POSITIVE"]
        return tp + (n - ap - (pp - tp)) == g["EQUAL"]
    return None


def v111_caps(item: dict, changes: list, ctx: dict) -> dict:
    """Replayable caps (they need only the finding, the gate counts and code_facts.json)."""
    fn = ctx.get("file", "")
    facts = ctx.get("facts") or {}
    gate = ctx.get("gate") or {}
    ev = str(item.get("evidence", ""))
    loc = str(item.get("location", ""))
    t = item.get("type")
    sev = item.get("severity")
    if sev not in ("red", "yellow"):
        return item
    hw = is_hand_written(item, fn)

    def grey(reason, text):
        return note(item, "grey", reason, f"[Grey (build_passport.py, {reason}): {text}]", changes)

    # v1.11.2: no code from the authors at all (B15-v1111: capsule not public). Searching code that does not exist for
    # a named method, or for a dependency file, says nothing beyond "code not provided", which the ledger reports.
    inv = facts.get("inventory") or {}
    if inv.get("n_author_code") == 0 and (t == "dependency_unpinned" or (
            t == "phantom_experiment" and "methods_code_check" in str(item.get("source", "")))):
        return grey("no_author_code", "the materials contain no code from the authors; the missing code is reported "
                                      "once as a missing artifact.")
    # ---- v1.11.3 (from the v1.11.1 batch) ----
    src = str(item.get("source", ""))
    # our own re-run of a threshold claim is the only side that disagrees (B14-v1101 F3, B14-v1111 F16: 'precision
    # >= 63%' fails on 62.x). Six of six such flags were false; the reproduction gate reports the re-run separately.
    if t == "rerun_mismatch" and "multiverse" in src:
        return grey("multiverse_rerun", "only our own re-run of the claim disagrees; the reproduction section reports "
                                        "the re-run, and a threshold missed by rounding is not a conflict.")
    # a sentence about future work or an open question is not a claim that needs evidence (B11-v1111 F1)
    if t == "unsupported_claim" and FUTURE_WORK.search(ev):
        return grey("future_work", "the sentence states an open question or future work, not a result.")
    # statistical tests named in the Methods but not found in the code: usually run in a statistics package
    # (B06-v1101 F4 Cox in SPSS/R, B13-v1111 F1 Wilcoxon for the supplement's IHC data). Models stay yellow.
    if t == "phantom_experiment" and "methods_code_check" in src and STAT_TEST.search(ev[:80]):
        return grey("stat_test_not_in_code", "a standard statistical test is often run in a statistics package; its "
                                             "absence from the released scripts is a question for the authors.")
    # bootstrap named only as part of a random-forest description (B16-v1111 F4)
    if t == "phantom_experiment" and "methods_code_check" in src and re.search(r"(?i)names bootstrap", ev) and \
            re.search(r"(?i)\btrees?\b|random forest|bagging", ev):
        return grey("bootstrap_in_forest", "the bootstrap is the sampling inside a random forest, not a separate "
                                           "analysis.")
    if src.startswith("script:checklist") and t == "missing_info" and "->" in ev:
        q, _, ans = ev.partition("->")
        # the evidence answers the item: preprocessing fitted on training data only (B14-v1111 F5)
        mc = CHECKLIST_OK.search(ans)
        if mc and not CONTRAST.search(ans[mc.end():mc.end() + 80]):
            return grey("checklist_answered", "the checklist evidence itself shows the practice is followed in the code; "
                                              "only the wording in the paper is missing.")
        # the evidence is about seeds while the item asks something else (B13-v1111 F4: G1 data source)
        if SEED_TOPIC.search(ans) and not re.search(r"(?i)seed|random|\[M1\]", q):
            return grey("checklist_off_topic", "the evidence is about random seeds, not about the checklist item; seeds "
                                               "are reported in their own finding.")
        # v1.11.4: the evidence never mentions what the item asks about (B13-v1113 F3: W4 cell lines, evidence about
        # blinding in a clinical trial with no cell lines)
        item_id = re.search(r"\[([A-Z]\d+)\]", q)
        topic = CHECKLIST_TOPIC.get(item_id.group(1)) if item_id else None
        if topic and not re.search(topic, ans):
            return grey("checklist_off_topic", "the evidence does not address the checklist item (it talks about "
                                               "something else); the item may not apply to this study.")
        # v1.11.4: nothing was removed, so there is no removal rule to report (B14-v1113 F6: "code/nn.py has no filter")
        if item_id and item_id.group(1) == "G4" and NOTHING_REMOVED.search(ans):
            return grey("checklist_nothing_done", "the code removes no outliers, so there is no removal rule to report.")
    # v1.11.6: an alternative analysis kept in the authors' code as a commented-out line (B08-v1114 F12, B03-v1115 F6)
    if t in ("missing_info", "unverifiable_claim", "missing_artifact") and COMMENTED_ALT.search(ev) and \
            NO_CHANGE_CLAIM.search(ev):
        return grey("runnable_alternative", "the authors' code contains the alternative analysis as a commented-out "
                                            "line; re-running it is a check a reader can do, not missing information.")
    # findings about files the agent wrote itself (B12-v1101 F4: code/repro_ml.py)
    agent_files = set((facts.get("inventory") or {}).get("agent_files") or [])
    first_path = re.split(r"[:\s;]", loc.strip())[0] if loc.strip() else ""
    if (AGENT_PATH.search(" " + loc) or first_path in agent_files) and t in ("leakage_risk", "seed_missing",
                                                                             "test_set_reuse", "dependency_unpinned"):
        return grey("agent_file", "the flagged file is the agent's own port or adapter, not the authors' code.")
    # unpinned dependencies while the capsule pins its environment (B12-v1102 F1, B13-v1103 F1, B06-v1101 F5)
    if t == "dependency_unpinned" and ((facts.get("inventory") or {}).get("env_pinned") or ctx.get("run_mentions_pins")):
        return grey("env_pinned", "the materials pin the environment (Dockerfile / environment file).")
    # multiverse forks that change the claim itself (B13-v1102 F8: endpoint=OS)
    if t == "fragile_conclusion" and ENDPOINT_FORK.search(ev):
        return grey("endpoint_fork", "the only flipping changes swap the endpoint, outcome or population; that tests "
                                     "another claim, not the robustness of this one.")
    if t == "phantom_experiment" and re.search(r"(?i)GraphPad|Prism|SPSS|MetaboAnalyst|Excel|JMP|Minitab|SAS\b|"
                                               r"point-and-click|web tool", ev) and re.search(r"(?i)named|tools?\b", ev):
        return grey("gui_tool_named", "the paper names a point-and-click or web tool for this analysis; no code is "
                                      "expected for it.")
    # "no fixed seed" while the code sets one (B06-v1110 F5: the notebook sets random_state=12306)
    seeds = facts.get("seeds") or {}
    if t in ("missing_info", "seed_missing") and NO_SEED.search(ev) and seeds:
        named = [f for f in seeds if Path(f).name in f"{loc} {ev}" or strip_prefix(f) in f"{loc} {ev}"]
        if named or not re.search(r"(?i)\.(?:py|r|rmd|ipynb)\b", f"{loc} {ev}"):
            hits = [f"{f}: {', '.join(seeds[f][:3])}" for f in (named or list(seeds))[:3]]
            return grey("seed_present", "the code sets random seeds (" + "; ".join(hits) + "); the finding says it "
                                        "does not.")
    # v1.11.5: a split stratified by batch puts every batch on both sides by design; with one sample per participant
    # that is not grouped leakage (B06 runs judged it false four times; B02-v1114 F12 hand-written)
    if t == "grouped_split_risk" and re.search(r"(?i)stratif\w*[^.]{0,60}\bbatch|\bbatch\w*[^.]{0,60}stratif", ev) and \
            not re.search(r"(?i)same (?:patient|participant|subject|animal|donor)|repeated (?:measure|sample)|"
                          r"multiple samples per", ev):
        return grey("batch_stratified", "the split is stratified by batch, so batches appear on both sides by design; "
                                        "with one sample per participant this is not grouped leakage.")
    # what-ifs on row-index groups (B12-v19 F20: groups = row//500 on shuffled rows)
    if PSEUDO_GROUP.search(ev):
        return grey("pseudo_groups", "the grouped split uses groups built from row positions, not a subject id.")
    if not hw:
        # script findings: subgroup totals (B11-v191 F1, 2 vs 2 + 1 = 3) are grey from v1.11, not yellow
        if t in NUMBER_TYPES:
            for m in SUM_EXPR.finditer(f"{loc} {ev}"):
                addends = {m.group(1)} | set(re.findall(r"\d+(?:\.\d+)?", m.group(2)))
                rest = salient_numbers(SUM_EXPR.sub(" ", f"{loc} {ev}"), keep_small=True)
                if m.group(3) in rest and addends & rest:
                    return grey("subgroup_total", f"one value equals a part of the total {m.group(3)}; the paper's "
                                                  "number names a subgroup.")
        # reporting-checklist items with no concrete place in the code or data (G2/G4/M7 "not reported")
        if CHECKLIST_ITEM.search(ev) and not CODE_LOCUS.search(f"{loc} {ev}") and \
                str(item.get("source", "")).startswith("script:checklist"):
            return note(item, "grey", "checklist_no_locus",
                        "[Grey (build_passport.py): a reporting-checklist item with no place in the code or data where "
                        "it changes a result; kept as a question for the authors.]", changes)
        return item
    # ---- hand-written from here ----
    # a caption that lists subgroups "(n=2) or ... (n=1)" whose parts add up to the total (B11-v1101 F6)
    sg = re.search(r"\(n\s*=\s*(\d+)\)[^.]{0,160}?\bor\b[^.]{0,160}?\(n\s*=\s*(\d+)\)", ev)
    if t in NUMBER_TYPES and sg:
        a_, b_ = int(sg.group(1)), int(sg.group(2))
        if re.search(rf"\b{a_}\s*\+\s*{b_}\s*=\s*{a_ + b_}\b", ev):
            return grey("subgroup_total", f"the caption lists subgroups (n={a_}, n={b_}) that add up to {a_ + b_}; the "
                                          "parts are not a conflict with the total.")
    if t in VALUE_CONFLICT:
        first = re.split(r"(?<=[.;])\s+(?=[A-Z])", ev, maxsplit=1)[0]  # the comparison the finding makes
        ds = set(DATASET.findall(first))
        if len(ds) >= 2 and not re.search(r"(?i)same dataset", ev):
            return grey("different_dataset", f"the two values come from different datasets ({', '.join('Dataset ' + d for d in sorted(ds))}).")
        for a, b in METRIC_PAIRS:
            if re.search(a + r"[^.;]{0,40}?\d", ev) and re.search(b + r"[^.;]{0,40}?\d", ev) and t != "missing_info" \
                    and not re.search(r"(?i)\bse\b|sensitivity|ppv|precision|recall", ev):
                return grey("different_metric", "the two values are different metrics, each labelled as such.")
        if DIFF_QTY.search(ev) and t in NUMBER_TYPES:
            return grey("different_quantity", "the evidence itself says the two numbers count different things.")
        st = stage_conflict(f"{loc} {ev}")
        if st and t in NUMBER_TYPES:
            return grey("different_stage", f"one number counts {st[0]}, the other {st[1]}: two stages of the same "
                                           "sampling flow, not the same quantity.")
    if t in VALUE_CONFLICT and OUR_PORT_VALUE.search(ev) and gate.get("port_gap", 0) > 0 \
            and not gate.get("archive_mismatch", 0):
        return grey("port_value", "one side of the comparison is a value from our own port; the reproduction gate "
                                  "recorded port gaps, so the difference is not evidence about the paper.")
    if t == "missing_info" and SELF_CONSISTENT.search(ev):
        return grey("answered_in_materials", "the evidence says the paper agrees with the data; the detail asked "
                                             "for is documented in the authors' files.")
    if EPV.search(ev) and re.search(r"(?i)univariate", ev):
        return grey("epv_univariate", "events-per-variable limits concern multivariable models; each model here has "
                                      "one covariate.")
    if t == "stat_inconsistency" and re.search(r"(?i)\bdf\b|t\s*\(\d", ev) and welch_matches(ev):
        return grey("welch_df", "a Welch test on the group sizes and SDs in the evidence gives the reported df.")
    cc = confusion_consistent(ev, facts) if re.search(r"(?i)\bcorrect\b", ev) else None
    if cc:
        return grey("confusion_recount", "TP, FP, FN and TN from the stored counts add up to the stored number "
                                         "correct (TN = negatives - FP).")
    mt = STORED_TOTAL.search(ev)
    if t == "sample_size" and mt:
        subs = [int(x) for x in CLAIMED_SUBSET.findall(ev)]
        if subs and all(s < int(mt.group(1)) for s in subs):
            return grey("stored_total", f"the stored output evaluates TOTAL = {mt.group(1)}, more than the subset the "
                                        "finding says was evaluated; the slice is a training-time monitor.")
    # facts-based
    for f, objs in (facts.get("archive_objects") or {}).items():
        if Path(f).name not in f"{loc} {ev}":
            continue
        toks = {x.lower() for o in objs for x in re.split(r"[_.]", o)}
        ep = r"(OS|PFS|DFS|EFS|RFS|DSS)"
        said = {e.lower() for e in re.findall(r"rows?,\s*(?:the\s+)?" + ep + r"\b|stored " + ep + r" table|"
                                                r"contains only[^.]{0,60}?\b" + ep + r"\b|only[^.]{0,30}?\(\d+ rows?, " + ep, ev)
                for e in (e if isinstance(e, tuple) else (e,)) if e}
        held = {e for e in ("os", "pfs", "dfs", "efs", "rfs", "dss") if e in toks}
        if said and held and not (said & held):
            return grey("archive_identity", f"{Path(f).name} prints {', '.join(objs)}; the finding treats it as the "
                                            f"{'/'.join(s.upper() for s in said)} table.")
    for code_file, tr in (facts.get("r_trace") or {}).items():
        claimed = {int(x) for x in re.findall(r"(?i)\b(\d+)\s+(?:covariates|models|features|variables)\b", ev)}
        if tr.get("tested") and claimed and tr["tested"] not in claimed and \
                re.search(rf"\b{tr['tested']}\s+rows\b", ev) and NOT_ALL_MODELS.search(ev):
            return grey("index_trace", f"tracing {code_file} gives {tr.get('tested')} tested covariates (skipped: "
                                       f"{', '.join(tr.get('skipped', [])) or 'none'}), the number of rows the stored "
                                       f"table holds; the finding counts {', '.join(map(str, sorted(claimed)))}.")
        if BLAMES_OUTPUT.search(ev) and not all(s.split(".")[0].lower() in ev.lower() for s in tr.get("skipped", [])):
            return grey("index_trace", f"tracing {code_file} gives {tr.get('tested')} tested covariates "
                                       f"(skipped: {', '.join(tr.get('skipped', [])) or 'none'}); the stored table "
                                       "holds what the code tests.")
    if re.search(r"(?i)separat", ev):
        for ep, g in (facts.get("surv_groups") or {}).items():
            for cov, levels in (g.get("binary") or {}).items():
                if _norm(cov).split(" ")[0] in _norm(ev) and all(v["events"] >= 1 for v in levels.values()):
                    return grey("no_separation", f"{cov} ({ep}): " + "; ".join(
                        f"{k}: {v['events']} events / {v['n']}" for k, v in levels.items()) + " - every level has events.")
    m = COLUMN_CLAIM.search(ev)
    if m:
        phrase = _norm(next(g for g in m.groups() if g))
        for f, hdr in (facts.get("headers") or {}).items():
            if any(phrase in _norm(h) for h in hdr):
                return grey("column_exists", f"{f} has a column matching '{phrase}'.")
    return item


PHANTOM_SAMPLE = re.compile(r"(?i)\b(?:comparison|control|validation|replication|external|additional|second|pilot)?\s*"
                            r"(?:sample|cohort|group|experiment|study|dataset|survey)\s+of\s+\d+\b|"
                            r"\b\d+\s+(?:additional|further|other|comparison|control)\s+(?:articles|participants|patients|"
                            r"samples|subjects|studies|cases)\b")
NO_MATERIALS = re.compile(r"(?i)\bno (?:data(?: file)?|code|script|supplementary (?:table|file))\b[^.]{0,80}"
                          r"(?:released|provided|materials|capsule|repository)|not (?:in|part of) the released materials|"
                          r"has no (?:data|code)")


def phantom_promote(item: dict, changes: list, ctx: dict) -> dict:
    """v1.11.5: a named sample or experiment that the paper reports results for, while the released materials hold the
    data and code of everything else but nothing for it, is a phantom experiment (yellow), not an unverifiable claim.

    B07-v1114 F23 found the injected "comparison sample of 30 articles without open data badges (21, 70%)" and
    wrote it as a grey unverifiable_claim. Only the agent's own grey judgement is promoted (no cap reason attached),
    and only when the materials contain the authors' code, so a paper without any materials is not affected.
    """
    if item.get("severity") != "grey" or item.get("severity_reasons") or \
            item.get("type") not in ("unverifiable_claim", "missing_artifact", "missing_info"):
        return item
    if not is_hand_written(item, ctx.get("file", "")):
        return item
    inv = (ctx.get("facts") or {}).get("inventory") or {}
    if not inv.get("n_author_code"):
        return item
    ev = str(item.get("evidence", ""))
    if PHANTOM_SAMPLE.search(ev) and NO_MATERIALS.search(ev):
        item["type_original"] = item.get("type")
        item["type"] = "phantom_experiment"
        return note(item, "yellow", "phantom_sample",
                    "[Yellow (build_passport.py, phantom_sample): the paper reports results for a named sample or "
                    "experiment, and the released materials, which hold the data and code for the other analyses, "
                    "contain nothing for it.]", changes)
    return item


def load_verify(out: Path) -> dict | None:
    v = load_json(out / "verify.json", None)
    return v if isinstance(v, dict) else None


def verify_gate(item: dict, changes: list, ctx: dict) -> dict:
    """v1.11: a hand-written red/yellow stays red/yellow only after an executed check upheld it (verify.json).

    Of the human-judged false flags that survived every earlier cap, most were hand-written and each could have
    been refuted by one command: print which object the notebook stores (B13 PFS/OS), count the covariates the
    code tests (B13 40 vs 39), compare like with like (B12 Dataset 1 vs 2), recompute TN (B14), a Welch df (B03).
    """
    fn = ctx.get("file", "")
    if item.get("severity") not in ("red", "yellow") or not is_hand_written(item, fn) or ctx.get("no_verify"):
        return item
    verify = ctx.get("verify")
    entry = None
    if verify:
        for e in verify.get("findings", []):
            if e.get("file") == fn and e.get("index") == ctx.get("index"):
                entry = e
                break
    if not entry:
        return note(item, "grey", "not_verified",
                    "[Grey (build_passport.py, not_verified): a hand-written flag needs an executed check in "
                    "verify.json (verify_queue.py) before it can be red or yellow.]", changes)
    verdict = str(entry.get("verdict", "")).lower()
    if verdict not in ("upheld", "confirmed"):
        return note(item, "grey", "refuted_on_check",
                    f"[Grey (build_passport.py): the executed check did not uphold this flag ({verdict or 'no verdict'}): "
                    f"{str(entry.get('note', ''))[:200]}]", changes)
    outp = entry.get("check_output")
    base = ctx.get("root", Path("."))
    if not outp or not (base / outp).exists() or (base / outp).stat().st_size == 0:
        return note(item, "grey", "check_missing",
                    "[Grey (build_passport.py): verify.json names no check output file that exists.]", changes)
    a, b = entry.get("a") or {}, entry.get("b") or {}
    if item.get("type") in VALUE_CONFLICT and a and b:
        # v1.11.2: a paper value and a stored-output value always sit in different "tables" (Table 2 vs
        # predict-output.txt), and a parameter has no dataset; comparing those keys greyed five true flags in
        # B14-v1111 (lambda 0.02 vs 0.002, 4-6-1 vs 4-5-7-1). "table" counts only when both values come from the
        # paper; "dataset" only for values computed on data.
        keys = ["metric", "endpoint", "population", "split"]
        if item.get("type") != "param_mismatch":
            keys.append("dataset")
        else:
            # v1.11.9: a parameter in the Methods and the same parameter in the code are described in different words
            # ("share rating 7-9 in each nonstudent group" vs "perc_essential in noStudents, academic, instructor");
            # B10-v1117p greyed the upheld 70% vs 0.75 consensus threshold on those words. Only the endpoint counts.
            sides = [str(x.get("file", "")) + " " + str(x.get("table", "")) for x in (a, b)]
            if sum(bool(re.search(r"(?i)paper|methods|supplement", t)) for t in sides) == 1:
                keys = ["endpoint"]
        same_quantity = all(_norm(a.get(k, "")) and _norm(a.get(k, "")) == _norm(b.get(k, ""))
                            for k in ("what", "metric", "endpoint", "population"))
        # v1.11.5: two places in the paper that print the same quantity always sit in different tables or sections;
        # that is the conflict, not a sign of two quantities (B07-v1114: "879 target values" in Results vs 789 in the
        # Abstract was greyed because the "table" fields differed). Compare tables only when the quantity differs.
        if all("paper" in str(x.get("file", "")).lower() for x in (a, b)) and not same_quantity:
            keys.append("table")
        for k in keys:
            va, vb = _norm(a.get(k, "")), _norm(b.get(k, ""))
            if va in ("", "-", "na", "n a", "none") or vb in ("", "-", "na", "n a", "none"):
                continue
            if va in vb or vb in va:
                continue
            if va and vb and va != vb:
                return note(item, "grey", "different_quantity_declared",
                            f"[Grey (build_passport.py): verify.json gives different {k}s for the two values "
                            f"('{a.get(k)}' vs '{b.get(k)}'); they are not the same quantity.]", changes)
    item["verified_by"] = outp
    if item.get("gate_capped"):
        try:
            check_text = (base / outp).read_text(encoding="utf-8", errors="replace")[:2_000_000]
        except OSError:
            check_text = ""
        item = archive_lift(item, changes, check_text, ctx.get("file", ""))
    return item


STORED_OUT = re.compile(r"(?i)\b(?:results?|outputs?|logs?)/[\w./-]+|\w\.(?:html?|txt|log|out|rout)\b|"
                        r"stored output|knitted")
OUR_RUN = re.compile(r"(?i)\bour (?:re-?run|port|python port|re-?computation|recount)|adapter\.py|port\.py|"
                     r"bp_work/port|substitute\s*=\s*true|port_gap|re-?computed by us")


def archive_lift(item: dict, changes: list, check_text: str | None, file_name: str = "") -> dict:
    """v1.11.6: a gate-capped hand-written number finding goes back to red when the verification stage upheld it with
    an executed check that printed the authors' stored value.

    The gate cap exists for recounts from the authors' data (B11: six hand-written reds, all recounting errors).
    A reading of the authors' own stored output that verify_queue.py has already printed and upheld is the same
    evidence numbers_crosscheck.py turns red when the value sits in recomputed.json; the agent just did not copy it
    there. In B03-v1115 five such findings (Table 2 model not regenerable, Table 3 intercept, SDs swapped, mean age,
    copied CI) were all true and all stayed yellow. Across all judged runs, every gate-capped finding with a
    verified_by entry was true or a duplicate of a true one (B03-v1115 F7-F12, B08-v1114 F9, B14-v1113 F12).

    check_text is the check output; None means the caller cannot read it (replay of a saved findings.json).
    """
    if not item.get("gate_capped") or item.get("severity") != "yellow" or item.get("type") not in NUMBER_TYPES:
        return item
    if [r for r in item.get("severity_reasons", []) if r != "gate_cap"]:
        return item
    ev_core = str(item.get("evidence", "")).replace(GATE_NOTE.strip(), "").strip()
    text = f"{item.get('location', '')} {ev_core}"
    if not STORED_OUT.search(text) or OUR_RUN.search(text):
        return item
    nums = salient_numbers(ev_core)
    if len(nums) < 2:
        return item
    if check_text is not None and not any(n in check_text for n in nums):
        return item
    changes.append({"type": item.get("type"), "location": item.get("location"), "from": "yellow", "to": "red",
                    "reason": "archive_verified", "evidence": ev_core[:200]})
    item["severity"] = "red"
    item["evidence"] = ev_core + (" [Red (build_passport.py, archive_verified): the verification stage printed the "
                                  "authors' stored value with an executed check and upheld the conflict.]")
    item["severity_reasons"] = [r for r in item.get("severity_reasons", []) if r != "gate_cap"] + ["archive_verified"]
    item.pop("gate_capped", None)
    return red_lint(item, file_name, changes)


def strip_prefix(loc: str) -> str:
    """Drop the folder prefixes under which the same file appears twice (capsule-N/, unzipped copies in _unz/<part>/;
    B06-v1110 reported every leakage flag once for _unz/materials_part1_code_results/code/ and once for code/)."""
    return re.sub(r"(?:(?<=^)|(?<=[\s;(=]))(?:bp_work/)?(?:_unz/[^/\s]+/|capsule-\d+/)", "", str(loc))


def merge_findings(out: Path, changes: list | None = None) -> list[dict]:
    changes = [] if changes is None else changes
    ci_methods = (load_json(out / "ci_methods.json", {}) or {}).get("methods") or []
    gui = (load_json(out / "gui_tools.json", {}) or {}).get("tools") or []
    gate_counts = (load_json(out / "repro_status.json", {}) or {}).get("counts") or {}
    facts = load_json(out / "code_facts.json", {}) or {}
    verify = load_verify(out)
    all_text = " ".join(str(i.get("evidence", "")) for f in out.glob("*.findings.json") for i in load_json(f, [])
                        if isinstance(i, dict))
    pins = bool(re.search(r"(?i)(Dockerfile|environment\.yml|renv\.lock)[^.]{0,80}\bpin|Dockerfile[^.]{0,120}==\s*\d",
                          all_text))
    merged, seen = [], set()
    for f in sorted(out.glob("*.findings.json")):
        for idx, item in enumerate(load_json(f, [])):
            if not isinstance(item, dict):
                continue
            ctx = {"file": f.name, "index": idx, "facts": facts, "gate": gate_counts, "verify": verify,
                   "root": out.parent, "run_mentions_pins": pins}
            item = gate_cap(item, f.name, changes)
            item = red_lint(item, f.name, changes)
            item = interval_method_cap(item, ci_methods, changes)
            item = gui_cap(item, gui, changes)
            item = notation_cap(item, changes)
            item = port_rerun_cap(item, changes, gate_counts)
            item = rounding_cap(item, changes)
            item = precision_cap(item, changes)
            item = approx_phrase_cap(item, changes)
            item = last_digit_cap(item, changes)
            item = robustness_port_cap(item, changes, gate_counts)
            item = subgroup_total_cap(item, changes)
            item = self_consistent_cap(item, changes, f.name)
            item = positional_cap(item, changes, f.name)
            item = v111_caps(item, changes, ctx)
            item = default_arg_cap(item, changes, f.name)
            item = category_sum_cap(item, changes, f.name)
            item = versioned_cap(item, changes, ctx)
            item = port_restated_cap(item, changes, ctx)
            item = verify_gate(item, changes, ctx)
            item = phantom_promote(item, changes, ctx)
            item.setdefault("module", f.name.split(".")[0])
            item.setdefault("claim_ids", [])
            key = (item.get("type"), strip_prefix(item.get("location")), str(item.get("evidence", ""))[:160])
            if key in seen:
                continue
            seen.add(key)
            item["file"] = f.name
            item["hand_written"] = is_hand_written(item, f.name)
            merged.append(item)
    merged = swapped_pairs(merged, changes)
    merged.sort(key=lambda x: (ORDER.get(x.get("severity"), 9), x.get("module", ""), x.get("type", "")))
    merged = merge_near_duplicates(merged, changes)
    merged = consolidate_low_value(merged, changes)
    merged = group_repeats(merged, changes)
    merged = group_archive_cells(merged, changes)
    for i, item in enumerate(merged, start=1):
        item["id"] = f"F{i}"
    return merged


VALUE_TYPES = {"rerun_mismatch", "figure_data_mismatch"}


def auto_link(ledger: dict, findings: list[dict]) -> int:
    """Link unlinked red/yellow findings to the claims whose evidence uses the file they point at (v1.10.4).

    Script findings carry no claim_ids, so a claim could stay green while a script showed that its own test set
    was inside the discovery data (B06-v1103: C2 green next to F7 split_overlap on Xtest.xlsx). A finding is
    linked when its location or evidence names an artifact path of the claim (or a basename unique to it).
    """
    arts = {a.get("id"): str(a.get("path") or "") for a in ledger.get("artifacts", []) if a.get("path")}
    ev_arts = {e.get("id"): [arts[x] for x in e.get("artifacts", []) if x in arts] for e in ledger.get("evidence", [])}
    def norm(p: str) -> str:
        return re.sub(r"^(?:\./|bp_work/)+", "", p.replace("\\", "/")).strip("/")
    all_paths = [norm(p) for p in arts.values() if p]
    base_count: dict[str, int] = {}
    for p in all_paths:
        b = p.rsplit("/", 1)[-1]
        base_count[b] = base_count.get(b, 0) + 1
    claim_keys = {}
    for c in ledger.get("claims", []):
        keys = set()
        for eid in c.get("evidence", []):
            for p in ev_arts.get(eid, []):
                q = norm(p)
                if len(q) >= 5:
                    keys.add(q)
                b = q.rsplit("/", 1)[-1]
                if "." in b and len(b) >= 6 and base_count.get(b, 0) == 1:
                    keys.add(b)
        claim_keys[c["id"]] = keys
    n = 0
    for f in findings:
        if f.get("claim_ids") or f.get("severity") not in ("red", "yellow"):
            continue
        text = (str(f.get("location", "")) + " " + str(f.get("evidence", ""))).replace("\\", "/")
        hit = sorted(cid for cid, keys in claim_keys.items() if any(k in text for k in keys))
        if hit:
            f["claim_ids"] = hit
            f["claims_auto_linked"] = True
            n += 1
    return n


def claim_lights(ledger: dict, findings: list[dict]) -> list[dict]:
    """Worst linked finding per claim.

    A re-run that gives different numbers is not the same as a conclusion that fails: when the
    multiverse marks the claim as a robust conclusion, value-only mismatches cap the claim at yellow.
    """
    lights = []
    for c in ledger.get("claims", []):
        mine = [f for f in findings if c["id"] in f.get("claim_ids", [])]
        linked = [f for f in mine if f.get("severity") != "green"]
        robust = any(f.get("type") == "robust_conclusion" for f in mine)
        note = ""
        if linked:
            sev = [("yellow" if robust and f.get("type") in VALUE_TYPES and f["severity"] == "red" else f["severity"])
                   for f in linked]
            worst = min(sev, key=lambda s: ORDER.get(s, 9))
            if robust and any(f.get("type") in VALUE_TYPES and f["severity"] == "red" for f in linked):
                note = "values differ in the re-run, but the conclusion holds in the multiverse"
        elif c.get("evidence"):
            worst = "green"
        else:
            worst = "yellow"
        lights.append({"id": c["id"], "text": c.get("text", ""), "location": c.get("location", ""),
                       "light": worst, "findings": [f["id"] for f in linked], "note": note})
    return lights


GATE_ZH = {"archive_mismatch": "论文 ≠ 作者存档输出", "not_reproduced": "未复现（无作者存档佐证）",
           "port_gap": "我们的重算有偏差，作者存档与论文一致", "archive_ok": "作者存档与论文一致（未重跑）",
           "reproduced": "已复现", "not_run": "未运行", "unmatched": "论文中无对应数值"}
GATE_ORDER = list(GATE_ZH)


def reproduction_level(gate, mv) -> dict:
    """Summarise repro_status.json (written by numbers_crosscheck.py) into one reproduction level.

    full     every checked value was reproduced by our own re-run
    partial  some values reproduced or confirmed by the authors' stored output
    none     values were checked, none could be reproduced or confirmed
    """
    counts = (gate or {}).get("counts") or {}
    checked = sum(v for k, v in counts.items() if k not in ("unmatched", "not_run"))
    rep = counts.get("reproduced", 0)
    confirmed = counts.get("archive_ok", 0) + counts.get("port_gap", 0)
    if not checked:
        if mv and mv.get("baseline_error"):
            return {"level": "none", "counts": counts, "text": "baseline re-run failed; no value could be checked"}
        return {"level": "not attempted", "counts": counts, "text": "no re-computed values recorded"}
    level = "full" if rep == checked else ("partial" if rep + confirmed else "none")
    parts = [f"{rep}/{checked} values reproduced by our re-run"]
    if confirmed:
        parts.append(f"{confirmed} confirmed only by the authors' stored output")
    if counts.get("archive_mismatch"):
        parts.append(f"{counts['archive_mismatch']} where the paper differs from the authors' stored output")
    if counts.get("not_reproduced"):
        parts.append(f"{counts['not_reproduced']} not reproduced")
    return {"level": level, "counts": counts, "text": " · ".join(parts)}


def gate_table(gate, limit: int = 25) -> list[str]:
    items = sorted((gate or {}).get("items") or [],
                   key=lambda g: GATE_ORDER.index(g.get("status")) if g.get("status") in GATE_ORDER else 99)
    if not items:
        return []
    fmt = lambda v: "—" if v is None else esc(v)
    lines = ["", "### 复现闸门明细 Reproduction gate", "",
             "论文数值只有在我们的重算先对上作者自己的存档输出后，才会被当作论文问题报告。", "",
             "| Quantity | Paper | Authors' stored output | Our re-run | Status |", "|---|---|---|---|---|"]
    for g in items[:limit]:
        ours = fmt(g.get("ours")) + (" (port)" if g.get("substitute") and g.get("ours") is not None else "")
        lines.append(f"| {esc(g.get('name'))} | {fmt(g.get('reported'))} | {fmt(g.get('archived'))} | {ours} | "
                     f"{GATE_ZH.get(g.get('status'), esc(g.get('status')))} |")
    if len(items) > limit:
        lines.append(f"\n{len(items) - limit} more value(s) in `repro_status.json`.")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    ap.add_argument("--no-print", action="store_true", help="kept for old commands; the block is not printed by default")
    ap.add_argument("--print-block", action="store_true", help="print the marker block (off by default since v1.11.8)")
    ap.add_argument("--max-questions", type=int, default=10)
    args = ap.parse_args()
    out = args.out

    ledger = load_json(out / "ledger.json", {})
    if not ledger:  # tolerate a ledger left in bp_work/
        for alt in (out.parent / "bp_work" / "ledger.json", out / ".." / "ledger.json"):
            ledger = load_json(alt, {})
            if ledger:
                print(f"note: using ledger at {alt}; expected {out / 'ledger.json'}", file=sys.stderr)
                break
    if not ledger.get("claims"):
        print("warning: no claims found in ledger.json — the claim table will be empty", file=sys.stderr)
    changes: list[dict] = []
    if not (out / "llm.findings.json").exists() or not (out / "judgement_checks.json").exists():
        print("WARNING: stage 2 judgement checks are not done (bp_out/llm.findings.json and bp_out/judgement_checks.json); "
              "final_summary.py will refuse to finish. Do them now (skill bp-consistency-check, 'Judgement checks').",
              file=sys.stderr)
    findings = merge_findings(out, changes)
    (out / "severity_changes.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    auto_link(ledger, findings)
    lights = claim_lights(ledger, findings)
    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in ORDER}
    mv = load_json(out / "multiverse_summary.json", None)
    nx = load_json(out / "next_experiments.json", None)
    gate = load_json(out / "repro_status.json", None)
    repro = reproduction_level(gate, mv)
    paper = ledger.get("paper", {})
    env_first = ""
    if (out / "environment.txt").exists():
        env_lines = (out / "environment.txt").read_text(encoding="utf-8", errors="replace").strip().splitlines()
        py = [l for l in env_lines if l.lower().startswith(("python ", "r version"))]
        env_first = "; ".join(py[:2]) if py else (env_lines[0] if env_lines else "")

    result = {"paper": paper, "generated_by": "BeyondPaper build_passport.py", "counts": counts,
              "claims": lights, "findings": findings,
              "reproduction": {"level": repro["level"], "counts": repro["counts"]},
              "multiverse": {k: mv.get(k) for k in ("robustness", "baseline_holds", "breaking_changes", "n_ok")} if mv else None,
              "top_experiment": (nx["ranked"][0]["id"] if nx and nx.get("ranked") else None),
              "judgement_checks": load_json(out / "judgement_checks.json", None),
              "severity_changes": {r: sum(1 for c in changes if c["reason"] == r) for r in sorted({c["reason"] for c in changes})}}
    (out / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    arts = ledger.get("artifacts", [])
    data_ok = sum(1 for a in arts if a.get("kind") in ("data", "table", "supplement") and a.get("available"))
    code_ok = sum(1 for a in arts if a.get("kind") in ("code", "notebook") and a.get("available"))
    missing = sum(1 for a in arts if not a.get("available"))
    md = [f"# 研究接力包 · {paper.get('title', 'Untitled paper')}", "",
          "<!-- NARRATIVE -->", "",
          "> 本报告由自动系统生成，列出的是**潜在**不一致，需人工核实；不构成对作者的结论。", "",
          "## 1. 可复现性护照 Reproducibility passport", "",
          f"- DOI / URL: {paper.get('doi') or paper.get('url') or '未报告'}",
          f"- Data artifacts available: {data_ok} · code artifacts available: {code_ok} · referenced but missing: {missing}",
          f"- Environment: {env_first or 'not recorded'}",
          f"- Reproduction level: **{repro['level']}** — {repro['text']}",
          f"- Baseline re-run: " + ("not attempted" if not mv else
                                    ("failed — " + str(mv.get("baseline_error")) if mv.get("baseline_error")
                                     else ("claim reproduced" if mv.get("baseline_holds") else "claim NOT reproduced"))),
          f"- Multiverse robustness of the main claim: " + (f"{mv['robustness']:.0%} of {mv.get('n_ok')} analyses"
                                                             if mv and mv.get('robustness') is not None else "not run"),
          f"- Findings: 🔴 {counts['red']} · 🟡 {counts['yellow']} · ⚪ {counts['grey']} · 🟢 {counts['green']}"]
    hand_red = [f for f in findings if f.get("severity") == "red" and f.get("hand_written")]
    if changes or hand_red:
        md += ["", "### 自动复核 Automatic review of severities", ""]
        if changes:
            by = {}
            for c in changes:
                by[c["reason"]] = by.get(c["reason"], 0) + 1
            zh = {"gate_cap": "引用作者文件、未过复现门的手写数字红灯 → 黄", "red_lint_one_value": "证据里只有一个数值的红灯 → 黄",
                  "archive_verified": "核对阶段已执行命令打印出作者存档值并确认冲突 → 恢复红",
                  "runnable_alternative": "论文说的替代分析在作者代码里是一行注释，可直接运行 → 灰",
                  "red_lint_sum_without_cells": "依赖合计但没写出各项相加的红灯 → 黄",
                  "disclosed_interval_methods": "两种已披露区间算法造成的区间差异 → 灰",
                  "close_intervals": "几乎重合的两个区间 → 黄", "gui_tool": "Methods 说明在网页/图形界面工具里做的分析 → 灰",
                  "near_duplicate": "同一冲突重复写了两次 → 合并",
                  "percent_fraction_notation": "同一数值写成小数和百分数 → 灰", "port_rerun": "只在我们的移植里对不上的重跑 → 灰",
                  "subgroup_total": "一个数是另一来源总数中的一部分 → 黄", "low_value_repeat": "未设种子、未固定依赖的重复条目 → 合并",
                  "rerun_only": "只有我们自己的重跑与论文不符 → 黄",
                  "percent_rounding": "同一百分数的两种精度（截断与四舍五入）→ 灰",
                  "precision_rounding": "同一数值在另一处少写几位小数（32.21 写成 32）→ 灰",
                  "approximate_phrase": "一处用“几乎/约/超过”给出近似值 → 灰",
                  "versioned_columns": "存档表里同一列有多个版本（如 consensus / consensus.1），未指明作者生成论文数字的代码行 → 黄",
                  "port_restated": "手写条目的冲突来自我们对作者代码的移植，复现门已记为 port_gap → 灰",
                  "swapped_pair": "两个量的论文值正好是对方的存档值（数值写反）→ 合并为一条红灯",
                  "robustness_on_port": "在我们的移植上算出的多重宇宙脆弱性 → 黄",
                  "last_digit_rounding": "两个小数只差最后一位的一个单位（同一数值两种取整）→ 灰",
                  "self_consistent": "证据本身写明数值一致，没有冲突 → 灰",
                  "positional_miscount": "按引用的函数签名重数位置参数，与发现所说的参数不符 → 灰",
                  "not_verified": "手写红/黄灯没有执行过的核对（verify.json）→ 灰",
                  "refuted_on_check": "执行核对后不成立 → 灰", "check_missing": "核对输出文件不存在 → 灰",
                  "different_quantity_declared": "核对时写明两个数不是同一个量 → 灰",
                  "different_dataset": "两个数来自不同数据集 → 灰", "different_metric": "两个数是不同指标 → 灰",
                  "different_quantity": "证据自己写明两个数计的不是同一件事 → 灰",
                  "port_value": "比较的一方是我们移植算出的数 → 灰", "answered_in_materials": "所问的细节作者材料里就有 → 灰",
                  "epv_univariate": "单变量模型套用每变量事件数 → 灰", "welch_df": "按 Welch 检验算出的自由度与论文一致 → 灰",
                  "confusion_recount": "按存档计数重算混淆矩阵，与存档一致 → 灰",
                  "stored_total": "存档输出的样本数多于发现所说的子集 → 灰",
                  "archive_identity": "存档里打印的是另一张表 → 灰", "index_trace": "按代码追踪，实际检验的变量数与存档一致 → 灰",
                  "no_separation": "按数据重数，各组都有事件，不存在完全分离 → 灰", "column_exists": "所说缺失的列其实存在 → 灰",
                  "agent_file": "标出的是智能体自己写的移植文件 → 灰", "env_pinned": "材料已固定环境（Dockerfile 等）→ 灰",
                  "endpoint_fork": "多重宇宙里换的是终点/结局本身 → 灰", "pseudo_groups": "按行号构造的伪分组 → 灰",
                  "checklist_no_locus": "清单条目在代码和数据里没有对应位置 → 灰",
                  "same_pattern": "同一种代码问题在多处或多个副本里重复 → 合并为一条",
                  "restates_script": "手写条目重复了脚本已报的问题 → 合并",
                  "multiverse_rerun": "只有我们自己的重跑与阈值型结论不符 → 灰",
                  "future_work": "未来工作或开放问题，不是需要证据的结论 → 灰",
                  "stat_test_not_in_code": "统计检验在脚本里找不到（通常在统计软件里做）→ 灰",
                  "bootstrap_in_forest": "bootstrap 只是随机森林内部的抽样 → 灰",
                  "checklist_answered": "清单证据本身说明代码做法没问题 → 灰",
                  "checklist_off_topic": "清单条目的证据讲的是别的问题 → 灰"}
            md += [f"- {zh.get(r, r)}：{n} 条" for r, n in sorted(by.items())]
            md.append("- 明细：`severity_changes.json`")
        if len(hand_red) >= 3:
            md.append(f"- 注意：仍有 {len(hand_red)} 条红灯是智能体手写的（不是脚本产生），对外使用前请人工逐条核对。")
    md += gate_table(gate)
    md += ["", "## 2. 结论—证据账本 Claim–evidence ledger", "",
           "| Claim | Light | Statement | Location | Findings |", "|---|---|---|---|---|"]
    for c in lights:
        md.append(f"| {c['id']} | {LIGHT[c['light']]} | {esc(c['text'])[:140]} | {esc(c['location'])} | {', '.join(c['findings']) or '—'}{' — ' + c['note'] if c.get('note') else ''} |")
    if (out / "ledger_graph.mmd").exists():
        md += ["", "```mermaid", (out / "ledger_graph.mmd").read_text(encoding="utf-8").strip(), "```"]

    def table(rows):
        lines = ["| ID | Light | Type | Location | Evidence | Source |", "|---|---|---|---|---|---|"]
        lines += [f"| {f['id']} | {LIGHT.get(f.get('severity'), '?')} | {esc(f.get('type'))} | {esc(f.get('location'))} | "
                  f"{esc(f.get('evidence'))[:300]} | {esc(f.get('source'))} |" for f in rows]
        return lines if rows else ["None."]

    anomalies = [f for f in findings if f.get("module") in ("consistency", "llm", "ledger") and f.get("severity") in ("red", "yellow")]
    ghosts = [f for f in findings if f.get("module") == "ghost" and f.get("severity") in ("red", "yellow", "grey")]
    md += ["", "## 3. 数字、单位和图表异常 Anomalies", ""] + table(anomalies)
    md += ["", "## 4. 幽灵变量清单 Ghost variables", ""] + table(ghosts)

    md += ["", "## 5. 压力测试与失败条件地图 Stress test and failure map", ""]
    if mv:
        rob = mv.get("robustness")
        md += [f"- Claim: {mv['claim'].get('text') or mv['claim'].get('holds')}",
               f"- Universes run: {mv.get('n_ok')} ok / {mv.get('n_run')} · robustness: " + (f"{rob:.0%}" if rob is not None else "n/a"),
               "- Single changes that flip the conclusion (failure conditions):"]
        md += [f"  - `{b['fork']} = {b['value']}` → claim {'holds' if b['holds'] else 'fails'} (effect "
               + (f"{b['effect']:+.3g}" if isinstance(b.get('effect'), (int, float)) else "n/a") + ")"
               for b in mv.get("breaking_changes", [])] or ["  - none found"]
        md += ["", "| Fork | Value | n | Claim holds | Mean effect |", "|---|---|---|---|---|"]
        for fork, vals in mv.get("marginals", {}).items():
            for v, s in vals.items():
                hr = f"{s['holds_rate']:.0%}" if s.get("holds_rate") is not None else "—"
                me = f"{s['mean_effect']:.4g}" if s.get("mean_effect") is not None else "—"
                md.append(f"| {fork} | {v} | {s['n']} | {hr} | {me} |")
        for img in ("multiverse_heatmap.png", "multiverse_curve.png"):
            if (out / img).exists():
                md += ["", f"![{img}]({img})"]
    else:
        md.append("Not run (see skipped.json).")

    md += ["", "## 6. 最值得做的后续实验 Next experiments", ""]
    if (out / "next_experiments.md").exists():
        md.append((out / "next_experiments.md").read_text(encoding="utf-8").replace("## 下一实验 / Next experiments", "").strip())
    else:
        md.append("Not run.")

    qs = []  # findings are sorted red -> yellow, so the most important questions come first
    for f in findings:
        q = f.get("question_for_authors")
        if q and f.get("severity") in ("red", "yellow") and q not in [x for x, _ in qs]:
            qs.append((q, f["id"]))
    shown_q = qs[: args.max_questions]
    md += ["", "## 7. 给原作者的问题 Questions for the authors", ""]
    md += [f"{i}. {q} ({fid})" for i, (q, fid) in enumerate(shown_q, 1)] or ["None."]
    if len(qs) > len(shown_q):
        md.append(f"\n{len(qs) - len(shown_q)} more question(s) in `findings.json`.")
    md += ["", "---", "", "Generated by BeyondPaper (built on SciencePal). Findings: `findings.json`."]
    (out / "passport.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"findings={len(findings)} red={counts['red']} yellow={counts['yellow']} grey={counts['grey']} green={counts['green']} "
          f"claims={len(lights)} -> {out / 'passport.md'}")
    block = "<<<BP_FINDINGS_JSON>>>\n" + json.dumps(result, ensure_ascii=False) + "\n<<<END_BP_FINDINGS_JSON>>>\n"
    # The platform often ends a run without the closing message; the block is also kept as a file.
    (out / "BP_FINDINGS_BLOCK.txt").write_text(block, encoding="utf-8")
    if args.print_block:  # v1.11.8: off by default; 30 KB of JSON in the conversation fed the repetitive-output cut
        print(block, end="")
    else:
        print(f"marker block written to {out / 'BP_FINDINGS_BLOCK.txt'} ({len(block)} bytes); not printed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

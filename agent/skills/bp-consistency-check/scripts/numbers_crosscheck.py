#!/usr/bin/env python3
"""Cross-check the same quantity reported in several places, and against re-computed values.

Reads `reported_values` from a BeyondPaper ledger. Entries with the same (normalised)
`name` are the same quantity reported in different locations; they must agree up to the
precision printed. Optional re-computed values (from re-running code on the data) are
compared with the reported ones.

Flags
  number_mismatch   values differ beyond rounding
  sample_size       ... and the quantity is a sample size (n, N, patients, cells, ...)
  magnitude_error   values differ by an exact power of ten (e.g. 10x, 1000x)
  unit_mismatch     units are incompatible, or differ and suggest a prefix confusion (uM vs mM)
  rerun_mismatch    a re-computed value disagrees with the reported one
  archive_mismatch  the authors' own stored output (results/, logs, knitted HTML) disagrees with the paper
  port_gap          our re-computation disagrees, but the authors' stored output agrees with the paper:
                    the gap is attributed to our re-run, not to the paper

Reproduction gate. A re-computed record may carry the authors' stored output for the same quantity:
    {"name", "value", "unit", "source", "archived": <number>, "archive_source": "results/table2.csv"}
`value` may be null when the code could not be run but a stored output exists. Each value is compared
three ways (paper / stored output / our re-run) and sorted into a status:
    reproduced        our re-run matches the paper (directly or within the seed range)
    port_gap          stored output matches the paper, our re-run does not   -> grey, not a paper problem
    archive_mismatch  stored output differs from the paper                    -> red (two author sources)
    archive_ok        no re-run, stored output matches the paper
    not_reproduced    our re-run differs and no stored output confirms the paper
    unmatched         no reported counterpart in the ledger

Usage:
    python numbers_crosscheck.py bp_out/ledger.json [--recomputed bp_out/recomputed.json] --out bp_out
Outputs: numbers.findings.json, repro_status.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

SAMPLE_SIZE = re.compile(r"(^|\b)(n|sample size|participants|patients|subjects|samples|cells|mice|animals|cohort|donors)(\b|$)", re.I)
POW10 = {1, 2, 3, 6, 9}
SMALL_RERUN_GAP = 0.05  # relative gap below which a re-run mismatch is yellow, not red

try:
    import pint  # type: ignore
    UREG = pint.UnitRegistry()
    # Counting "units" used in per-entity quantities (eV/atom, pg/cell, ...): dimensionless for conversion.
    for _u in ("atom", "molecule", "cell", "particle", "site", "formula_unit", "compound", "residue"):
        try:
            UREG.define(f"{_u} = 1")
        except Exception:
            pass
except Exception:  # pragma: no cover - optional dependency
    UREG = None


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def norm_unit(u):
    if u is None:
        return None
    u = str(u).strip()
    if not u:
        return None
    u = u.replace("f.u.", "formula_unit").replace(" per ", "/").replace("per ", "1/")
    return u.replace("μ", "u").replace("µ", "u").replace("·", "*").replace("%", "percent")


def float_decimals(x) -> int:
    """Decimals as printed. v1.11.7: an int (32 in the ledger for "32 metres") has none; repr(float(32)) is "32.0",
    which made 32 vs 32.21 a red conflict (B09-v1116p F2: the Discussion rounds Table 3's 32.21 m to 32 m)."""
    if isinstance(x, bool):
        return 0
    if isinstance(x, int):
        return 0
    if isinstance(x, str):
        t = x.strip().rstrip("%").replace(",", "")
        if re.search(r"[eE]", t):
            return 6
        return len(t.split(".")[1]) if "." in t else 0
    r = repr(float(x))
    if "e" in r or "E" in r:
        return 6
    return len(r.split(".")[1]) if "." in r else 0


FRACTION_UNITS = {None, "ratio", "fraction", "proportion", "fraction_of_1", "1", "dimensionless", "unitless"}


def convert(value: float, unit_from, unit_to):
    """Return (converted_value, status) with status in {'same', 'converted', 'incompatible', 'unknown'}."""
    a, b = norm_unit(unit_from), norm_unit(unit_to)
    if a == b:
        return value, "same"
    # v1.11.2: a fraction and a percentage of the same metric (B14-v1110/v1111: F1 0.64 in the paper, "F1 62%" in the
    # stored output) are compared on one scale instead of being reported as an unconvertible unit pair.
    fa = a.lower() if isinstance(a, str) else a
    fb = b.lower() if isinstance(b, str) else b
    if fa in FRACTION_UNITS and fb == "percent" and abs(value) <= 1:
        return value * 100.0, "converted"
    if fa == "percent" and fb in FRACTION_UNITS:
        return value / 100.0, "converted"
    if a is None or b is None:
        return value, "unknown"
    if UREG is None:
        return value, "unknown"
    try:
        q = UREG.Quantity(value, a).to(b)
        return float(q.magnitude), "converted"
    except pint.errors.DimensionalityError:  # type: ignore[attr-defined]
        return value, "incompatible"
    except Exception:
        return value, "unknown"


def power_of_ten(a: float, b: float):
    if a == 0 or b == 0:
        return None
    lg = math.log10(abs(b / a))
    k = round(lg)
    if k != 0 and abs(k) in POW10 and abs(lg - k) < 0.01:
        return k
    return None


def agree(a: float, b: float, rtol: float, b_exact: bool = False, da: int | None = None, db: int | None = None) -> bool:
    """Equal up to the printed precision. If b is a re-computed (unrounded) value, only a's rounding counts.

    v1.11.7: decimals are taken from the printed value, so 32 (int) vs 32.21 agree (32.21 rounds to 32) while
    58.5 vs 58.05 does not (58.05 rounds to 58.0 or 58.1, never 58.5)."""
    da = float_decimals(a) if da is None else da
    db = float_decimals(b) if db is None else db
    tol = 0.5 * 10 ** (-da) + (0.0 if b_exact else 0.5 * 10 ** (-db))
    tol *= 1 + 1e-9  # v1.11.14: 0.76 - 0.755 is 0.0050000000000000044 in floating point (B09-v11113p F9)
    return abs(a - b) <= max(tol, rtol * max(abs(a), abs(b)))


def last_digit_apart(x: dict, y: dict) -> bool:
    """Two printed decimals with the same number of places that differ by exactly one unit in the last place."""
    try:
        a, b = float(x["value"]), float(y["value"])
    except (TypeError, ValueError, KeyError):
        return False
    if norm_unit(x.get("unit")) != norm_unit(y.get("unit")):
        return False
    da, db = float_decimals(x["value"]), float_decimals(y["value"])
    if da != db or da == 0 or da > 4:
        return False
    return abs(abs(a - b) - 10 ** -da) < 10 ** -(da + 3)


HEDGE = re.compile(r"(?i)\b(?:almost|nearly|about|approximately|approx\.?|around|roughly|some|over|more than|"
                   r"less than|under|up to|close to)\s+\d|[~≈<>]\s*\d")
APPROX_LOC = re.compile(r"read (?:off|from)|(?:taken|estimated|digiti[sz]ed) from (?:the )?(?:figure|plot|graph|image)|figure reading", re.I)


def approximate(rec: dict) -> bool:
    """Values read off a figure or marked approx are only as precise as the reading."""
    return bool(rec.get("approx")) or bool(APPROX_LOC.search(str(rec.get("location", ""))))


def cap(f, *recs):
    """Cap severity at yellow when a value is approximate or re-computed with a substitute tool."""
    if f is None or f["severity"] != "red":
        return f
    why = []
    if any(approximate(r) for r in recs):
        why.append("one value was read from a figure or marked approximate")
    if any(r.get("substitute") for r in recs):
        why.append("the re-computation used a substitute implementation (not the authors' code/tooling); "
                   "compare with the authors' stored outputs before treating this as an error")
    if why:
        f["severity"] = "yellow"
        f["evidence"] += " Capped at yellow: " + "; ".join(why) + "."
    return f


def make(ftype, severity, location, evidence, claim_ids, question):
    return {"module": "consistency", "type": ftype, "severity": severity, "claim_ids": sorted(set(claim_ids)),
            "location": location, "evidence": evidence, "source": "script:numbers_crosscheck.py",
            "question_for_authors": question}


def compare(x, y, rtol, kind="pair"):
    """Compare two value records; return a finding or None if they agree (severity capped if approximate)."""
    return cap(_compare(x, y, rtol, kind), x, y)


def _compare(x, y, rtol, kind="pair"):
    name = x["name"]
    claims = list(x.get("claim_ids", [])) + list(y.get("claim_ids", []))
    loc = f"{x['location']} vs {y['location']}"
    vy, status = convert(float(y["value"]), y.get("unit"), x.get("unit"))
    vx = float(x["value"])
    ux, uy = x.get("unit"), y.get("unit")
    vx_s, vy_s = f"{x['value']} {ux or ''}".strip(), f"{y['value']} {uy or ''}".strip()
    shown = f"{x['location']}: {vx_s} | {y['location']}: {vy_s}"
    which = f"Which value of '{name}' is correct ({vx_s} or {vy_s})?"

    if status == "incompatible":
        return make("unit_mismatch", "red", loc, f"'{name}' has incompatible units. {shown}", claims,
                    f"Which unit is intended for '{name}'?")
    if agree(vx, vy, rtol, b_exact=(kind == "rerun"), da=float_decimals(x["value"]),
             db=None if status == "converted" else float_decimals(y["value"])):
        return None
    if kind == "pair" and (HEDGE.search(str(x.get("location", "")) + " " + str(x.get("quote", "")))
                           or HEDGE.search(str(y.get("location", "")) + " " + str(y.get("quote", "")))) \
            and abs(vx - vy) <= 0.1 * max(abs(vx), abs(vy)):
        # v1.11.7: "almost 90%" for 87% is a rounded phrase, not a second value (B10-v1116p F4)
        return make("number_mismatch", "grey", loc, f"'{name}': one place gives an approximate value in words "
                    f"(about / almost / nearly / over). {shown}", claims, None)
    k = power_of_ten(vx, vy)  # vy is converted when possible, otherwise the raw value
    # v1.11.9: a count written with its noun on one side only ("27 articles" vs a stored 25) is the same unit; only a
    # power-of-ten gap keeps the unit reading (B07-v1118p I2 was greyed as an unconvertible unit pair)
    one_missing = (norm_unit(ux) is None) != (norm_unit(uy) is None)
    if status == "unknown" and norm_unit(ux) != norm_unit(uy) and not (one_missing and k is None):
        ftype = "unit_mismatch"
        sev = "red" if k is not None else "grey"
        msg = f"'{name}' differs and units differ ({ux} vs {uy}); could not convert automatically. {shown}"
        if k is not None:
            msg += f" Ratio is 10^{k}: possible unit-prefix confusion."
        return make(ftype, sev, loc, msg, claims, which)
    if k is not None:
        return make("magnitude_error", "red", loc, f"'{name}' differs by a factor of 10^{k}. {shown}", claims,
                    f"Is one of the values of '{name}' off by a power of ten ({vx_s} vs {vy_s})?")
    if kind == "rerun":
        rel = abs(vx - vy) / max(abs(vx), abs(vy), 1e-12)
        small = rel < SMALL_RERUN_GAP
        return make("rerun_mismatch", "yellow" if small else "red", loc,
                    f"Re-computed '{name}' = {y['value']} {uy or ''} does not match reported {x['value']} {ux or ''} "
                    f"({x['location']}); relative difference {rel:.1%}. Source: {y['location']}."
                    + (" Small gap: may be run-to-run variation (e.g. an unseeded random forest) or rounding;"
                       " re-run with several seeds and pass the spread as `range`." if small else ""), claims,
                    "Which code version, data version, seed or settings produced the reported value?")
    ftype = "sample_size" if SAMPLE_SIZE.search(name) else "number_mismatch"
    return make(ftype, "red", loc, f"'{name}' is reported with different values. {shown}", claims,
                which)


GATE_STATUSES = ("reproduced", "archive_ok", "port_gap", "archive_mismatch", "not_reproduced", "not_run", "unmatched")


def _half_step(m: dict) -> float:
    return 0.5 * 10 ** (-float_decimals(m["value"]))


def _in_range(r: dict, matches: list[dict]) -> bool:
    rng = r.get("range")  # [low, high] over seeds / repeated runs
    if not (rng and len(rng) == 2):
        return False
    lo, hi = sorted(float(v) for v in rng)
    return any(lo - _half_step(m) <= float(m["value"]) <= hi + _half_step(m) for m in matches)


def gate_one(r: dict, groups: dict, rtol: float):
    """Reproduction gate for one re-computed record: paper vs authors' stored output vs our re-run.

    Returns (status entry for repro_status.json, list of findings).
    """
    name = r["name"]
    src = r.get("source", "re-computed")
    arch_src = r.get("archive_source") or "authors' stored output"
    ours, archived = r.get("value"), r.get("archived")
    entry = {"name": name, "reported": None, "reported_location": None, "ours": ours, "archived": archived,
             "source": src, "archive_source": r.get("archive_source"), "substitute": bool(r.get("substitute")),
             "status": None}
    matches = groups.get(norm_name(name), [])
    if not matches:
        entry["status"] = "unmatched"
        return entry, [make("rerun_mismatch", "grey", src,
                            f"Re-computed '{name}' has no reported counterpart in the ledger.", [], None)]
    majority = max(matches, key=lambda m: sum(compare(m, o, rtol) is None for o in matches))
    entry["reported"], entry["reported_location"] = majority["value"], majority["location"]
    claims = sorted({c for m in matches for c in m.get("claim_ids", [])})

    rec = None
    if ours is not None:
        rec = {"name": name, "value": ours, "unit": r.get("unit"), "location": src, "claim_ids": [],
               "substitute": r.get("substitute", False), "approx": r.get("approx", False)}
        # Fine if the re-computed value agrees with any reported location (conflicts between
        # reported locations are flagged elsewhere) or the reported value lies within the seed range.
        if any(compare(m, rec, rtol, kind="rerun") is None for m in matches) or _in_range(r, matches):
            entry["status"] = "reproduced"
            return entry, []

    if archived is None:
        if rec is None:
            entry["status"] = "not_run"
            return entry, []
        entry["status"] = "not_reproduced"
        f = compare(majority, rec, rtol, kind="rerun")  # substitute / approximate values are capped at yellow
        if f is None:
            return entry, []
        if f["severity"] in ("red", "yellow"):
            # Only our re-run disagrees with the paper; no author source does. That is a risk to check
            # (wrong denominator, subset or port on our side is as likely), not a conflict between two sources.
            f["severity"] = "yellow"
            f["evidence"] = f["evidence"].rstrip(". ") + (". No stored output from the authors was recorded for this "
                            "value; it has not passed the reproduction gate, so it stays yellow. Check that the "
                            "denominator, subset and filters match the paper's before asking the authors.")
        return entry, [f]

    arc = {"name": name, "value": archived, "unit": r.get("archive_unit", r.get("unit")), "location": arch_src,
           "claim_ids": []}
    # v1.11.15: a stored value printed with at least two more decimals than the paper is unrounded output; the paper
    # must agree with it to its own printed precision, not within 0.5% (B08-v11114p: Table 3 intercept 5.468 vs
    # stored 5.48639 passed the gate)
    try:
        unrounded = float_decimals(archived) >= float_decimals(majority["value"]) + 2
    except (TypeError, ValueError):
        unrounded = False
    arch_rtol = min(rtol, 0.001) if unrounded else rtol
    if any(compare(m, arc, arch_rtol, kind="rerun") is None for m in matches):
        if rec is None:
            entry["status"] = "archive_ok"
            return entry, []
        entry["status"] = "port_gap"
        ev = (f"Our re-computation of '{name}' gives {ours} ({src}), but the authors' stored output ({arch_src}) "
              f"gives {archived}, which matches the paper ({majority['location']}: {majority['value']}). "
              "The gap comes from our re-run (environment, port to another language, package version or seed), "
              "not from the paper; it is not reported as a paper problem.")
        if claims:
            ev += f" Related claims: {', '.join(claims)}."
        return entry, [{"module": "consistency", "type": "port_gap", "severity": "grey", "claim_ids": [],
                        "location": f"{src} vs {arch_src}", "evidence": ev,
                        "source": "script:numbers_crosscheck.py", "question_for_authors": None}]

    # v1.11: before calling it a conflict, look for the paper's value in the same stored file. When it is there
    # (another column or row: B10-v18 F1/F2/F7 read the Round-1 column, the paper uses the final-round column),
    # the comparison picked the wrong cell; grey, with the cells named.
    elsewhere = value_elsewhere(arch_src, majority.get("value"))
    if elsewhere:
        entry["status"] = "archive_ok"
        entry["archive_note"] = elsewhere
        return entry, [{"module": "consistency", "type": "archive_mismatch", "severity": "grey", "claim_ids": claims,
                        "location": f"{majority['location']} vs {arch_src}",
                        "evidence": (f"The paper reports '{name}' = {majority['value']}; the cell recorded as stored "
                                     f"output gives {archived}, but {elsewhere}. Find the line of the authors' code "
                                     "that produces the paper's number and compare with that cell."),
                        "source": "script:numbers_crosscheck.py", "question_for_authors": None}]

    # v1.11.4: a head count whose stored value the paper itself states for another group. B06-v1113 F1/F2 compared
    # the whole study (702 = 389 GC + 313 NGC) with discovery_set.xlsx, which is Cohort 1 (426 = 145 GC + 281 NGC);
    # the paper gives "Cohort 1 ... (n = 426): 145 GC patients". Different groups, not two author sources in conflict.
    other = count_stated_elsewhere(name, r, majority, archived)
    if other:
        entry["status"] = "archive_ok"
        entry["archive_note"] = other
        return entry, [{"module": "consistency", "type": "archive_mismatch", "severity": "grey", "claim_ids": claims,
                        "location": f"{majority['location']} vs {arch_src}",
                        "evidence": (f"The paper reports '{name}' = {majority['value']}; {arch_src} gives {archived}, "
                                     f"but the paper itself states {other}. The stored file holds a different group "
                                     "(a cohort, split or subset) than the paper's number; compare counts for the same "
                                     "group."),
                        "source": "script:numbers_crosscheck.py", "question_for_authors": None,
                        "count_group_source": arch_src}]

    # The authors' stored output itself differs from the paper: two author sources conflict.
    entry["status"] = "archive_mismatch"
    f = compare(majority, arc, arch_rtol, kind="rerun")
    sev = f["severity"] if f else "yellow"
    # v1.11.7: a stored table with several versions of the compared column (consensus / consensus.1, mean / mean.1,
    # x / x_final) may be an intermediate table: B10-v1116p F1/F3 and B10-v18 F1/F2/F7 compared the Round-1 column of
    # 0.main_table.csv with Table 2, which the Rmd builds from a recomputed final column. Without the line of the
    # authors' code that writes the paper's number (`archive_code`), this is yellow, not red.
    vers = versioned_columns(arch_src)
    version_note = ""
    if vers and not r.get("archive_code"):
        if sev == "red":
            sev = "yellow"
        version_note = (f" Yellow, not red: {vers} The paper's table may be built from another version of the column; "
                        "add `archive_code` (file:line of the authors' code that writes the paper's number) to "
                        "recomputed.json once that column is confirmed.")
    ftype = f["type"] if f and f["type"] in ("unit_mismatch", "magnitude_error") else "archive_mismatch"
    ev = (f"The paper reports '{name}' = {majority['value']} {majority.get('unit') or ''} ({majority['location']}); "
          f"the authors' own stored output ({arch_src}) gives {archived}.").replace("  ", " ")
    if rec is not None:
        same = compare(arc, rec, rtol, kind="rerun") is None
        entry["ours_matches_archive"] = same
        ev += (f" Our re-run gives {ours}, which matches the stored output." if same
               else f" Our re-run gives {ours}, which matches neither.")
    if sev == "yellow" and f and f["type"] == "rerun_mismatch":
        ev += " The gap is small (<5%): it may be run-to-run variation if the stored output came from a different run."
    ev += version_note
    return entry, [{"module": "consistency", "type": ftype, "severity": sev, "claim_ids": claims,
                    "location": f"{majority['location']} vs {arch_src}", "evidence": ev,
                    "source": "script:numbers_crosscheck.py",
                    "question_for_authors": f"Which run produced the reported value of '{name}' "
                                            f"({majority['value']}), given that {arch_src} gives {archived}?"}]


PAPER_TEXT = ""  # set in main() from --paper (bp_work/paper.md)
COUNT_NOUN = (r"(?:patients|participants|subjects|samples|cases|controls|individuals|persons|people|donors|women|men|"
              r"children|mice|animals|cells|records|images|eyes|specimens|sera|plasmas?|tumou?rs|lesions)")


def is_count(name: str, rec: dict) -> bool:
    unit = str(rec.get("unit") or "")
    return bool(SAMPLE_SIZE.search(name) or re.search(COUNT_NOUN, unit, re.I) or re.search(r"(?i)^n\b|\bn$", unit))


def count_stated_elsewhere(name: str, rec: dict, majority: dict, archived) -> str | None:
    """The stored head count appears in the paper as a count of some group: return that phrase."""
    if not PAPER_TEXT or archived is None or not is_count(name, majority):
        return None
    try:
        a, p = float(archived), float(majority.get("value"))
    except (TypeError, ValueError):
        return None
    if a != int(a) or p != int(p) or a == p or a < 10:
        return None  # whole numbers only; small counts (2, 3) occur everywhere
    v = str(int(a))
    pats = [rf"(?<![\d.,]){v}(?![\d.,%])\s*(?:[A-Za-z-]+\s+){{0,2}}{COUNT_NOUN}",
            rf"(?i)\b[nN]\s*=\s*{v}(?![\d.])"]
    for pat in pats:
        m = re.search(pat, PAPER_TEXT, re.I)
        if m:
            ctx = re.sub(r"\s+", " ", PAPER_TEXT[max(0, m.start() - 80):m.end() + 20]).strip()
            return f"{v} as a count elsewhere ('...{ctx}...')"
    return None


def stored_file(arch_src) -> Path | None:
    m = re.search(r"[\w./-]+\.(?:csv|tsv|txt|html?|md|log|out)\b", str(arch_src or ""))
    if not m:
        return None
    cands = [Path(m.group(0)), Path("bp_work") / m.group(0), Path("bp_work/code/..") / m.group(0)]
    return next((c for c in cands if c.exists() and c.stat().st_size < 50_000_000), None)


VERSION_SUFFIX = re.compile(r"(?i)(?:\.\d+|[._](?:final|r\d|round_?\d|t\d|v\d|wave_?\d|post|pre|rev|revised|old|new))$")


def versioned_columns(arch_src) -> str | None:
    """If the stored table named in arch_src has several versions of a column it is compared on, describe them."""
    path = stored_file(arch_src)
    if path is None or path.suffix.lower() not in (".csv", ".tsv"):
        return None
    try:
        header = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    cols = [c.strip().strip('"') for c in re.split(r"\t" if path.suffix.lower() == ".tsv" else ",", header)]
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(VERSION_SUFFIX.sub("", c), []).append(c)
    groups = {k: v for k, v in groups.items() if len(v) > 1 and k}
    src = str(arch_src)
    named = [k for k, v in groups.items() if any(re.search(rf"(?<![\w.]){re.escape(c)}(?![\w])", src) for c in v)]
    if not named:
        return None
    return "; ".join(f"{path.name} holds the columns {', '.join(groups[k])}" for k in named[:2]) + "."


def swapped_pairs(gate: list[dict], findings: list[dict], rtol: float) -> list[dict]:
    """Two quantities whose paper values are each other's stored values (v1.11.7, Campbell 2017: the paper gives SD
    1.25 for the experimental and 1.42 for the control condition; the stored output gives 1.42 and 1.25). The
    value_elsewhere rule greyed both, because each paper value does appear in the stored file."""
    out = []
    cand = [g for g in gate if g.get("archived") is not None and g.get("reported") is not None
            and g.get("status") in ("archive_ok", "archive_mismatch")]
    used = set()
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if id(a) in used or id(b) in used or a.get("archive_source") != b.get("archive_source"):
                continue
            try:
                ra, rb, sa, sb = (float(a["reported"]), float(b["reported"]), float(a["archived"]), float(b["archived"]))
            except (TypeError, ValueError):
                continue
            if ra == rb:
                continue
            ok = (agree(ra, sb, rtol, da=float_decimals(a["reported"]), db=float_decimals(b["archived"]))
                  and agree(rb, sa, rtol, da=float_decimals(b["reported"]), db=float_decimals(a["archived"]))
                  and not agree(ra, sa, rtol) and not agree(rb, sb, rtol))
            wa, wb = set(norm_name(a["name"]).split()), set(norm_name(b["name"]).split())
            if not ok or len(wa & wb) < max(1, min(len(wa), len(wb)) - 2):
                continue
            used |= {id(a), id(b)}
            for g in (a, b):
                g["status"], g["archive_note"] = "archive_mismatch", "values swapped with " + (b if g is a else a)["name"]
            names = {a["name"], b["name"]}
            findings[:] = [f for f in findings if not (f.get("type") == "archive_mismatch" and any(
                str(f.get("evidence", "")).startswith(f"The paper reports '{n}'") for n in names))]
            out.append({"module": "consistency", "type": "archive_mismatch", "severity": "red", "claim_ids": [],
                        "location": f"{a.get('reported_location')} vs {a.get('archive_source')}",
                        "evidence": (f"Values swapped between two quantities: the paper reports '{a['name']}' = "
                                     f"{a['reported']} and '{b['name']}' = {b['reported']}; the authors' own stored output "
                                     f"({a.get('archive_source')}) gives {a['archived']} and {b['archived']}, i.e. the two "
                                     "values are exchanged."),
                        "source": "script:numbers_crosscheck.py",
                        "question_for_authors": f"Are the values of '{a['name']}' and '{b['name']}' swapped in the paper?"})
    return out


def value_elsewhere(arch_src, value) -> str | None:
    """Return a note if `value` appears verbatim in the stored file named in arch_src (csv/tsv/txt/html/md)."""
    if value is None:
        return None
    m = re.search(r"[\w./-]+\.(?:csv|tsv|txt|html?|md|log|out)\b", str(arch_src or ""))
    if not m:
        return None
    cands = [Path(m.group(0)), Path("bp_work") / m.group(0), Path("bp_work/code/..") / m.group(0)]
    path = next((c for c in cands if c.exists() and c.stat().st_size < 50_000_000), None)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    v = str(value).strip().rstrip("%")
    try:
        fv = float(v)
    except ValueError:
        return None
    digits = len(re.sub(r"^[0.]+", "", v.replace("-", "").replace(".", "")))
    if ("." in v and digits < 3) or ("." not in v and abs(fv) < 1000):
        return None  # short numbers (60, 0.5) occur everywhere in a log; only distinctive values count
    pat = re.escape(v) if "." in v else rf"{re.escape(v)}(?:\.0+)?"
    hits = [mm.start() for mm in re.finditer(rf"(?<![\d.]){pat}(?![\d])", text)]
    if not hits:
        return None
    line = text[:hits[0]].count("\n") + 1
    return f"{v} appears {len(hits)} time(s) in {path} (first on line {line}: '{text.splitlines()[line - 1][:120]}')"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--recomputed", type=Path, default=None)
    ap.add_argument("--rtol", type=float, default=0.005, help="relative tolerance on top of rounding (default 0.5%%)")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--paper", type=Path, default=None,
                    help="paper text, used to tell a count for another group from a conflict (default bp_work/paper.md)")
    args = ap.parse_args()
    global PAPER_TEXT
    paper_path = args.paper or next((c for c in (Path("bp_work/paper.md"), args.ledger.parent.parent / "bp_work" / "paper.md")
                                     if c.exists()), None)
    if paper_path and paper_path.exists():
        PAPER_TEXT = paper_path.read_text(encoding="utf-8", errors="replace")

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for v in ledger.get("reported_values", []):
        groups[norm_name(v["name"])].append(v)

    findings, n_groups_ok = [], 0
    for key, vals in groups.items():
        # Cluster mutually agreeing reports; the largest cluster is the reference.
        clusters: list[list[dict]] = []
        for v in vals:
            for c in clusters:
                if compare(c[0], v, args.rtol) is None:
                    c.append(v)
                    break
            else:
                clusters.append([v])
        if len(clusters) == 1:
            n_groups_ok += len(vals) > 1
            continue
        clusters.sort(key=len, reverse=True)
        ref = clusters[0]
        for c in clusters[1:]:
            f = compare(ref[0], c[0], args.rtol)
            if f is None:
                continue
            f["location"] = f"{', '.join(v['location'] for v in ref)} vs {', '.join(v['location'] for v in c)}"
            f["claim_ids"] = sorted({cid for v in ref + c for cid in v.get("claim_ids", [])})
            f["evidence"] = f["evidence"].rstrip(". ") + "." + (f" Agreeing reports: {len(ref)} location(s) "
                              f"[{', '.join(v['location'] for v in ref)}] vs {len(c)} [{', '.join(v['location'] for v in c)}].")
            if f["severity"] == "red" and last_digit_apart(ref[0], c[0]):
                # v1.11.4: 0.95 in Table 1 vs 0.94 in Table S5 (B16-v1113 F1) is a real inconsistency, but one unit in
                # the last printed digit is what rounding 0.945 two ways gives; it is not worth a red light.
                f["severity"] = "yellow"
                f["evidence"] += (" Yellow, not red: the values differ by one unit in the last printed digit, which "
                                  "rounding the same number two ways can produce.")
            findings.append(f)

    n_rerun_ok = 0
    gate = []  # one entry per re-computed record: the reproduction-gate status
    if args.recomputed and args.recomputed.exists():
        recomputed = json.loads(args.recomputed.read_text(encoding="utf-8"))
        for r in recomputed:
            status, f_extra = gate_one(r, groups, args.rtol)
            gate.append(status)
            findings.extend(f_extra)
        findings.extend(swapped_pairs(gate, findings, args.rtol))
        n_rerun_ok = sum(1 for g in gate if g["status"] in ("reproduced", "archive_ok"))

    # v1.11.4: once one head count from a stored file is shown to be another group's count, the other head counts from
    # the same file are that group's too (B06-v1113 F2: 313 NGC overall vs 281 NGC in Cohort 1, which the paper gives
    # only as the cohort total 426 = 145 + 281).
    other_group = {f.get("count_group_source") for f in findings if f.get("count_group_source")}
    for f in findings:
        if f["type"] == "archive_mismatch" and f["severity"] in ("red", "yellow") and other_group:
            m = re.match(r"The paper reports '([^']+)'", f["evidence"])
            src = next((g for g in other_group if f["location"].endswith(f" vs {g}")), None)
            if m and src and is_count(m.group(1), {}):
                f["severity"] = "grey"
                for g in gate:
                    if g.get("name") == m.group(1) and g.get("status") == "archive_mismatch":
                        g["status"], g["archive_note"] = "archive_ok", f"another group's count ({src})"
                f["evidence"] += (f" [Grey: another head count from {src} is the count of a different group stated in "
                                  "the paper, so this file holds that group, not the paper's.]")

    if n_groups_ok or n_rerun_ok:
        findings.append(make("number_mismatch", "green", "reported values",
                             f"{n_groups_ok} multiply-reported quantities agree across locations; "
                             f"{n_rerun_ok} re-computed values match the paper.", [], None))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "numbers.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    gate_counts = {s: sum(1 for g in gate if g["status"] == s) for s in GATE_STATUSES}
    if args.recomputed and args.recomputed.exists():
        (args.out / "repro_status.json").write_text(
            json.dumps({"counts": gate_counts, "items": gate}, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [f for f in findings if f["severity"] in ("red", "yellow")]
    print(f"quantities={len(groups)} flagged={len(bad)} agree={n_groups_ok} rerun_match={n_rerun_ok}"
          + ("" if UREG else "  (pint not installed: unit conversion disabled)"))
    if gate:
        print("reproduction gate: " + " ".join(f"{k}={v}" for k, v in gate_counts.items() if v))
        # v1.11.10: B07-v1119p2 re-computed 37 values under its own names (repro_withAI_pct ...); 36 matched no ledger
        # name, so none was compared with the paper and the 879-vs-789 slip in the Results went past the gate.
        unmatched = [g for g in gate if g["status"] == "unmatched"]
        if len(unmatched) >= 5 and len(unmatched) > len(gate) - len(unmatched):
            print(f"WARNING: {len(unmatched)} re-computed values match no ledger name and were not compared with the "
                  "paper. Give each record in recomputed.json the exact `name` of the ledger value it re-computes, "
                  "then run this script again. Ledger names whose value equals a re-computed one:")
            shown = 0
            for g in unmatched:
                try:
                    ours = float(g["ours"])
                except (TypeError, ValueError):
                    continue
                hits = sorted({m["name"] for ms in groups.values() for m in ms
                               if isinstance(m.get("value"), (int, float))
                               and agree(float(m["value"]), ours, args.rtol, b_exact=True, da=float_decimals(m["value"]))})
                if hits:
                    print(f"  {g['name']} = {g['ours']}  ->  {', '.join(hits[:3])}")
                    shown += 1
                if shown >= 15:
                    break
            if not shown:
                print("  (none by value; ledger names: " + ", ".join(sorted({m['name'] for ms in groups.values()
                                                                              for m in ms})[:20]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())

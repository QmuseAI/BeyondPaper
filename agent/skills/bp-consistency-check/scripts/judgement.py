#!/usr/bin/env python3
"""Write the stage 2 judgement checks one short command at a time (v1.11.8).

B02-v1117p spent the rest of its turn exploring data for the judgement checks in long python heredocs, the platform
removed its output as repetitive, and the run ended with neither llm.findings.json nor judgement_checks.json written,
although the agent had already verified the four facts it needed. With this script each fact goes to disk the moment
it is checked, so a cut-off turn loses nothing.

Usage:
    python judgement.py init  [--out bp_out]          # create both files and list the script candidates to review
    python judgement.py note  KEY ok|problem "what was compared, values and locations"   [--out bp_out]
    python judgement.py add   --type T --severity S --location L --evidence E [--question Q] [--key KEY] [--out bp_out]
    python judgement.py status [--out bp_out]          # what is still missing for the stage to count as done

KEY is one of: param_mismatch, figure_data_mismatch, phantom_experiment, abstract_vs_body, claims_scan.
A note is appended to what the key already holds. Notes saying TODO / pending do not count.
v1.11.9: every note carries a verdict. A key noted "problem" needs at least one finding added for it (`add`, the key
is taken from --type or given with --key): B07-v1118p wrote "no data file or code for the comparison sample exists"
as a note and never added the finding, so the run missed it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEYS = ("param_mismatch", "figure_data_mismatch", "phantom_experiment", "abstract_vs_body", "claims_scan")
TYPES = {"param_mismatch", "figure_data_mismatch", "phantom_experiment", "number_mismatch", "sample_size",
         "stat_inconsistency", "archive_mismatch", "rerun_mismatch", "missing_info", "missing_artifact",
         "unverifiable_claim", "unsupported_claim", "magnitude_error", "unit_mismatch", "test_set_reuse",
         "split_overlap", "duplicate_output", "leakage_risk", "stale_loop_variable"}


# finding types that answer each key (claims_scan answers to any type)
KEY_TYPES = {"param_mismatch": {"param_mismatch"},
             "figure_data_mismatch": {"figure_data_mismatch", "number_mismatch", "archive_mismatch"},
             "phantom_experiment": {"phantom_experiment", "missing_artifact", "unverifiable_claim"},
             "abstract_vs_body": {"number_mismatch", "sample_size", "unsupported_claim", "stat_inconsistency"}}
# a hand-written note without a verdict that reads like a problem (B07-v1118p) counts as "problem"
NEGATIVE = re.compile(r"(?i)\b(?:no|without)\s+(?:\w+\s+){0,3}?(?:data|code|file|table|script|supplement\w*)\b[^.;]*"
                      r"\b(?:exists?|found|provided|available|released|included)\b"
                      r"|\b(?:does not|doesn't|do not|cannot be)\s+(?:exist|match|be (?:found|verified|checked))\b"
                      r"|\bnot (?:in|among) the (?:materials|data|code)\b|\bnowhere in the (?:materials|data|code)\b")


def verdict_of(jc: dict, key: str) -> str | None:
    v = (jc.get("_verdicts") or {}).get(key)
    if v in ("ok", "problem"):
        return v
    txt = jc.get(key)
    return "problem" if isinstance(txt, str) and NEGATIVE.search(txt) else None


def answered(lf: list, key: str) -> bool:
    for f in lf:
        if not isinstance(f, dict) or f.get("severity") not in ("red", "yellow", "grey"):
            continue
        if f.get("judgement_key") == key or (key != "claims_scan" and f.get("type") in KEY_TYPES.get(key, ())):
            return True
    return key == "claims_scan" and any(isinstance(f, dict) for f in lf)


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def candidates(out: Path) -> list[str]:
    rows = []
    for name in ("claims_scan.findings.json", "methods_code.findings.json", "deep.findings.json",
                 "counts.findings.json", "numbers.findings.json"):
        for i, f in enumerate(load(out / name, []) or []):
            if isinstance(f, dict) and f.get("severity") in ("red", "yellow"):
                rows.append(f"{name}#{i} [{f.get('severity')}] {f.get('type')} @ {str(f.get('location'))[:70]}")
    return rows


def problems(out: Path) -> list[str]:
    probs = []
    lf = load(out / "llm.findings.json", None)
    if not isinstance(lf, list):
        probs.append("llm.findings.json missing or not a list")
    jc = load(out / "judgement_checks.json", None)
    if not isinstance(jc, dict):
        return probs + ["judgement_checks.json missing"]
    for k in KEYS:
        v = jc.get(k)
        txt = json.dumps(v, ensure_ascii=False) if v is not None else ""
        if len(txt) < 25 or "TODO" in txt.upper() or "PENDING" in txt.upper():
            probs.append(f"'{k}' not recorded")
        elif verdict_of(jc, k) == "problem" and not answered(lf if isinstance(lf, list) else [], k):
            probs.append(f"'{k}' is noted as a problem but no finding was added for it (judgement.py add ... "
                         f"--key {k}), or note it again with verdict ok if it is not a problem")
    return probs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "note", "add", "status"])
    ap.add_argument("key", nargs="?")
    ap.add_argument("verdict", nargs="?")
    ap.add_argument("text", nargs="?")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--type")
    ap.add_argument("--severity", choices=["red", "yellow", "grey", "green"])
    ap.add_argument("--location")
    ap.add_argument("--evidence")
    ap.add_argument("--question")
    ap.add_argument("--key", dest="for_key", choices=KEYS)
    a = ap.parse_args()
    lp, jp = a.out / "llm.findings.json", a.out / "judgement_checks.json"

    if a.cmd == "init":
        if not isinstance(load(lp, None), list):
            save(lp, [])
        jc = load(jp, None)
        if not isinstance(jc, dict):
            save(jp, {})
        print(f"{lp} and {jp} ready.")
        cand = candidates(a.out)
        if cand:
            print("Script candidates to review (record each under claims_scan or the matching key):")
            for c in cand[:25]:
                print("  " + c)
    elif a.cmd == "note":
        if a.key not in KEYS or a.verdict not in ("ok", "problem") or not a.text:
            print(f"usage: judgement.py note KEY ok|problem \"text\"; KEY in {', '.join(KEYS)}")
            return 2
        jc = load(jp, {}) or {}
        vd = jc.get("_verdicts") or {}
        vd[a.key] = a.verdict  # the latest verdict counts
        jc["_verdicts"] = vd
        old = jc.get(a.key)
        keep = isinstance(old, str) and old and "TODO" not in old.upper() and "PENDING" not in old.upper()
        jc[a.key] = f"{old}; {a.text}" if keep else a.text  # a placeholder is replaced, a real note is extended
        save(jp, jc)
        print(f"recorded {a.key} [{vd[a.key]}] ({len(jc[a.key])} chars)")
        if vd[a.key] == "problem" and not answered(load(lp, []) or [], a.key):
            print(f"now add the finding: judgement.py add --type ... --severity ... --location ... --evidence ... "
                  f"--key {a.key}")
    elif a.cmd == "add":
        missing = [n for n in ("type", "severity", "location", "evidence") if not getattr(a, n)]
        if missing:
            print("missing: " + ", ".join("--" + m for m in missing))
            return 2
        if a.type not in TYPES:
            print(f"note: type '{a.type}' is not a standard BeyondPaper type")
        lf = load(lp, []) or []
        key = (a.type, a.location.strip(), a.evidence.strip()[:120])
        if any((f.get("type"), str(f.get("location", "")).strip(), str(f.get("evidence", "")).strip()[:120]) == key
               for f in lf if isinstance(f, dict)):
            print("already recorded")
            return 0
        f = {"module": "llm", "type": a.type, "severity": a.severity, "claim_ids": [], "location": a.location,
             "evidence": a.evidence, "source": "llm", "question_for_authors": a.question}
        if a.for_key:
            f["judgement_key"] = a.for_key
        lf.append(f)
        save(lp, lf)
        print(f"llm.findings.json: {len(lf)} finding(s)")
    probs = problems(a.out)
    print("judgement stage: " + ("done" if not probs else "missing " + "; ".join(probs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

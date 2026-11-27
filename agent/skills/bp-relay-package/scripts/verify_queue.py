#!/usr/bin/env python3
"""Verification stage (v1.11): every hand-written red/yellow flag gets one executed check before the passport.

Why. Across the human-judged runs, the false flags that no rule caught were hand-written and each could have been
refuted by one command: print which object the knitted notebook stores (B13: PFS, not OS), count the covariates
the code tests (B13: 39, not 40), check that both numbers belong to the same dataset (B12: Dataset 1 vs Dataset 2),
recompute TN from the stored counts (B14), compute a Welch df (B03). `build_passport.py` keeps a hand-written flag
red or yellow only when bp_out/verify.json records an executed check that upheld it.

Steps
  1. python verify_queue.py bp_out --init
     lists the flags to check (bp_out/verify.json, one entry per flag, verdict "todo") and the pitfall-atlas
     mechanisms to mark checked / n/a.
  2. For each entry run ONE command that tests the flag against the files and save its output:
       <command> > bp_work/checks/V<k>.out 2>&1
     The command must print both values (or the absence) from the authors' files, not from your notes.
     Fill in the entry: check_cmd, check_output, verdict (upheld | refuted | weakened), note, and for flags that
     compare two values: a / b = {"value", "what", "dataset", "metric", "endpoint", "population", "split", "table",
     "file"}. Fill every field that applies; two values of the same quantity have the same dataset, metric,
     endpoint, population, split and table.
  3. python verify_queue.py bp_out --check
     prints what is still missing; exit code 0 when complete (writes "complete": true).

A refuted flag is not deleted: build_passport.py keeps it grey with the note, so the passport shows what was checked.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HINTS = {
    "number_mismatch": "print both numbers from their files with one command (grep -n / python -c); confirm same "
                       "dataset, metric, endpoint, population, table and stage (sampled/contacted vs obtained/"
                       "analysed); a percentage that differs by <= 1 point from a printed one is rounding",
    "archive_mismatch": "print the stored cell and the line of the authors' code that produces the paper's number",
    "param_mismatch": "print the code line(s) and the paper sentence; for a call, print the function signature and "
                      "number the positional arguments",
    "figure_data_mismatch": "compute the summary of the figure's data with python and print it next to the plotted value",
    "sample_size": "count rows the way the authors' code reads the file (header setting, filters) and print the count",
    "stat_inconsistency": "recompute the statistic (df, p, CI, confusion cells) with python from the stated inputs",
    "fragile_conclusion": "print the analysis result both ways; confirm the compared table is the one the claim uses",
    "missing_info": "grep the paper and the materials for the missing item and print what is found (or nothing); "
                    "for a count of covariates/models, print r_trace in bp_out/code_facts.json (tested, skipped) "
                    "and the stored row count; for 'no seed', print seeds in code_facts.json",
    "missing_artifact": "list the stored outputs and print which objects each one holds (e.g. which tables a knitted "
                        "notebook printed)",
    "phantom_experiment": "grep the code for the analysis and the Methods for the named tool; print both",
    "unsupported_claim": "print the passage and the evidence it cites",
    "leakage_risk": "print the fit call and the split call with line numbers",
    "grouped_split_risk": "print the group column and the counts per group in the data",
    "test_set_reuse": "print the loop / fit that uses the test data with line numbers",
}


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def hand_written(item: dict, fname: str) -> bool:
    src = str(item.get("source", "")).lower()
    return (not src.startswith("script:") or "llm" in src or "+" in src
            or str(item.get("module", "")).lower() == "llm" or fname.startswith("llm."))


def queue(out: Path) -> list[dict]:
    q = []
    for f in sorted(out.glob("*.findings.json")):
        for i, it in enumerate(load(f, [])):
            if isinstance(it, dict) and it.get("severity") in ("red", "yellow") and hand_written(it, f.name):
                q.append({"file": f.name, "index": i, "type": it.get("type"), "severity": it.get("severity"),
                          "location": it.get("location"), "evidence": str(it.get("evidence", ""))[:400]})
    return q


def init(out: Path) -> int:
    vpath = out / "verify.json"
    old = load(vpath, {})
    done = {(e.get("file"), e.get("index")): e for e in old.get("findings", [])}
    entries = []
    for k, q in enumerate(queue(out), start=1):
        e = done.get((q["file"], q["index"]))
        if e and e.get("evidence", "")[:200] == q["evidence"][:200]:
            entries.append(e)
            continue
        entries.append({**q, "id": f"V{k}", "hint": HINTS.get(q["type"], "run one command that tests the flag"),
                        "check_cmd": "", "check_output": f"bp_work/checks/V{k}.out", "verdict": "todo", "note": "",
                        "a": {}, "b": {}})
    prior = load(out / "atlas_prior.json", {})
    mech_old = {m.get("id"): m for m in old.get("mechanisms", [])}
    mechs = [mech_old.get(m["id"], {"id": m["id"], "name_zh": m.get("name_zh"), "detector": m.get("detector"),
                                     "status": "todo", "evidence": ""})
             for m in prior.get("mechanisms", [])]
    deep = load(out / "deep.findings.json", [])
    data = {"findings": entries, "mechanisms": mechs,
            "deep_checks": [f"{d.get('type')} {d.get('location')}" for d in deep if d.get("severity") in ("red", "yellow")],
            "complete": False}
    vpath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out.parent / "bp_work" / "checks").mkdir(parents=True, exist_ok=True)
    print(f"verify.json: {len(entries)} hand-written flag(s) to check, {len(mechs)} atlas mechanism(s) to mark")
    for e in entries:
        print(f"  {e['id']} [{e['severity']}] {e['type']} {e['file']}#{e['index']} {str(e['location'])[:80]}\n"
              f"      check: {e['hint']}  ->  > {e['check_output']} 2>&1")
    for m in mechs:
        print(f"  mechanism {m['id']} ({m.get('name_zh')}): mark checked / n/a with one line of evidence")
    return 0


def check(out: Path) -> int:
    vpath = out / "verify.json"
    v = load(vpath, None)
    if not v:
        print("verify.json missing: run verify_queue.py bp_out --init first")
        return 1
    current = {(q["file"], q["index"]) for q in queue(out)}
    have = {(e.get("file"), e.get("index")) for e in v.get("findings", [])}
    problems = [f"flag {f}#{i} is not in verify.json (re-run --init)" for f, i in sorted(current - have)]
    root = out.parent
    for e in v.get("findings", []):
        if (e.get("file"), e.get("index")) not in current:
            continue
        tag = f"{e.get('id')} {e.get('file')}#{e.get('index')}"
        if str(e.get("verdict", "")).lower() not in ("upheld", "refuted", "weakened", "confirmed"):
            problems.append(f"{tag}: verdict is '{e.get('verdict')}'")
        if not e.get("check_cmd"):
            problems.append(f"{tag}: no check_cmd")
        op = e.get("check_output")
        if not op or not (root / op).exists() or (root / op).stat().st_size == 0:
            problems.append(f"{tag}: check output {op} missing or empty")
        if e.get("type") in ("number_mismatch", "archive_mismatch", "param_mismatch", "figure_data_mismatch",
                             "sample_size", "stat_inconsistency", "fragile_conclusion") \
                and str(e.get("verdict", "")).lower() in ("upheld", "confirmed") and not (e.get("a") and e.get("b")):
            problems.append(f"{tag}: an upheld comparison needs a and b (value, what, dataset, metric, endpoint, table)")
    for m in v.get("mechanisms", []):
        if str(m.get("status", "")).lower() not in ("checked", "n/a", "na", "not applicable", "found"):
            problems.append(f"mechanism {m.get('id')}: status '{m.get('status')}'")
    v["complete"] = not problems
    vpath.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    if problems:
        print(f"verify.json incomplete ({len(problems)}):")
        for p in problems:
            print("  " + p)
        return 1
    n = len([e for e in v.get("findings", []) if (e.get("file"), e.get("index")) in current])
    up = sum(1 for e in v.get("findings", []) if str(e.get("verdict", "")).lower() in ("upheld", "confirmed"))
    print(f"verify.json complete: {n} flag(s) checked, {up} upheld, {n - up} refuted/weakened -> run build_passport.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    return init(a.out) if a.init else check(a.out)


if __name__ == "__main__":
    sys.exit(main())

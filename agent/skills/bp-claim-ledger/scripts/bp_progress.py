#!/usr/bin/env python3
"""Show which BeyondPaper stages are done (from the files in bp_out/) and what to run next.

Run it at the start of every turn and after every "继续": the run continues from the first
stage that has no output yet, instead of re-reading the paper.

Usage:
    python bp_progress.py [bp_out] [--work bp_work]
Writes bp_out/progress.json and prints a short table plus the NEXT line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

SKILLS = "/workspace/skills"
STAGES = [
    (0, "prep", "bp-claim-ledger", ["{work}/prep/index.md"],
     "python {S}/bp-claim-ledger/scripts/prep_paper.py {work}/paper.md --materials {work} --out {work}/prep"),
    (1, "claim ledger", "bp-claim-ledger", ["{out}/ledger.json", "{out}/ledger.findings.json"],
     "write {out}/ledger.json, then python {S}/bp-claim-ledger/scripts/validate_ledger.py {out}/ledger.json --root {work} --out {out}"),
    (1, "pitfall prior", "bp-pitfall-atlas", ["{out}/atlas_prior.md"],
     "python {S}/bp-pitfall-atlas/scripts/atlas.py prior {out} --work {work}; read {out}/atlas_prior.md"),
    (2, "consistency", "bp-consistency-check", ["{out}/numbers.findings.json", "{out}/statcheck.findings.json",
                                                 "{out}/claims_scan.findings.json"],
     "statcheck_lite.py, ci_check.py, counts_check.py, claims_scan.py, numbers_crosscheck.py (+ recomputed.json) — see the skill"),
    (2, "judgement checks", "bp-consistency-check", ["{out}/llm.findings.json", "{out}/judgement_checks.json"],
     "python {S}/bp-consistency-check/scripts/judgement.py init --out {out}; then for each of the five checks run "
     "judgement.py note KEY ok|problem \"what you compared\" and judgement.py add ... for each problem, one short command at a time "
     "(at most ~15 tool calls); this stage cannot be skipped"),
    (3, "deep checks", "bp-ghost-variables", ["{out}/code_facts.json"],
     "python {S}/bp-ghost-variables/scripts/deep_checks.py {work} --paper {work}/paper.md --out {out}"),
    (3, "ghost variables", "bp-ghost-variables", ["{out}/ghost_checklist.json"],
     "leakage_scan.py on the code, data_checks.py on the materials, then the checklist → {out}/ghost_checklist.json"),
    (4, "multiverse", "bp-multiverse", ["{out}/multiverse_summary.json"],
     "baseline re-run → recomputed.json → numbers_crosscheck.py; adapter + spec; multiverse.py; plot_multiverse.py"),
    (5, "next experiments", "bp-next-experiment", ["{out}/next_experiments.json"],
     "write {out}/next_experiments.yaml, then rank_experiments.py"),
    (6, "verify", "bp-relay-package", ["{out}/verify.json"],
     "python {S}/bp-relay-package/scripts/verify_queue.py {out} --init; one executed check per entry "
     "(> {work}/checks/Vk.out); fill verdict, a/b; python {S}/bp-relay-package/scripts/verify_queue.py {out} --check"),
    (6, "relay package", "bp-relay-package", ["{out}/passport.md", "{out}/findings.json"],
     "python {S}/bp-relay-package/scripts/build_passport.py {out}"),
    (7, "failure paths", "bp-null-result", ["{out}/failure_paths.md"],
     "python {S}/bp-null-result/scripts/from_run.py {out}"),
    (7, "precedents", "bp-pitfall-atlas", ["{out}/precedents.md"],
     "python {S}/bp-pitfall-atlas/scripts/atlas.py match {out}"),
    (7, "briefs", "bp-relay-package", ["{out}/brief_investor.md"],
     "python {S}/bp-relay-package/scripts/build_brief.py {out} --audience investor; same with --audience reviewer"),
    (8, "paper agent", "bp-paper-agent", ["{out}/paper_agent/AGENT_CARD.md"],
     "python {S}/bp-paper-agent/scripts/build_paper_agent.py {out} --spec {work}/multiverse_spec.yaml"),
    (9, "final message", "bp-relay-package", ["{out}/FINAL_MESSAGE.md"],
     "python {S}/bp-relay-package/scripts/final_summary.py {out} and paste its output unchanged as the last message"),
]


# v1.11.9: same verdict rule as bp-consistency-check/scripts/judgement.py (a key noted as a problem needs a finding)
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


JUDGEMENT_KEYS = ("param_mismatch", "figure_data_mismatch", "phantom_experiment", "abstract_vs_body", "claims_scan")


def judgement_problems(out: Path) -> list[str]:
    """v1.11.7: the hand-written checks are a stage of their own and cannot be skipped. In B02-v1116p and
    B02-v1116p2 (the platform switched the model mid-run) no llm.findings.json was written, and the run found 0/4 and
    1/4 of the injected errors; the scripts alone cannot catch a phantom cohort or an abstract that misquotes."""
    probs, lf = [], None
    try:
        lf = json.loads((out / "llm.findings.json").read_text(encoding="utf-8"))
        if not isinstance(lf, list):
            probs.append("llm.findings.json must be a JSON list ([] if no finding)")
    except FileNotFoundError:
        probs.append("llm.findings.json missing")
    except json.JSONDecodeError as e:
        probs.append(f"llm.findings.json is not valid JSON ({e})")
    try:
        jc = json.loads((out / "judgement_checks.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return probs + ["judgement_checks.json missing"]
    except json.JSONDecodeError as e:
        return probs + [f"judgement_checks.json is not valid JSON ({e})"]
    if not isinstance(jc, dict):
        return probs + ["judgement_checks.json must be an object with keys " + ", ".join(JUDGEMENT_KEYS)]
    for k in JUDGEMENT_KEYS:
        v = jc.get(k)
        txt = json.dumps(v, ensure_ascii=False) if v is not None else ""
        if len(txt) < 25 or "TODO" in txt.upper() or "PENDING" in txt.upper():
            probs.append(f"judgement_checks.json: '{k}' must say what was compared (values and locations)")
        elif verdict_of(jc, k) == "problem" and not answered(lf if isinstance(lf, list) else [], k):
            probs.append(f"judgement_checks.json: '{k}' is noted as a problem but llm.findings.json has no finding "
                         f"for it (judgement.py add ... --key {k})")
    return probs


def stage_done(name: str, paths: list) -> bool:
    if name == "judgement checks":
        return not judgement_problems(paths[0].parent)
    if not all(p.exists() and p.stat().st_size > 0 for p in paths):
        return False
    llm = paths[0].parent / "llm.findings.json"
    if name == "verify":  # complete only after verify_queue.py --check passed, and after the last hand-written finding
        try:
            ok = bool(json.loads(paths[0].read_text(encoding="utf-8")).get("complete"))
        except (OSError, json.JSONDecodeError):
            return False
        return ok and (not llm.exists() or paths[0].stat().st_mtime >= llm.stat().st_mtime)
    if name == "relay package":  # the passport must be newer than the verification and the hand-written findings
        v = paths[0].parent / "verify.json"
        newest = max([x.stat().st_mtime for x in (v, llm) if x.exists()] or [0])
        return paths[0].stat().st_mtime >= newest
    return True


def skipped(out: Path) -> dict:
    try:
        d = json.loads((out / "skipped.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(d, dict):
        return {str(k).lower(): v for k, v in d.items()}
    if isinstance(d, list):
        return {str(x.get("stage", "")).lower(): x for x in d if isinstance(x, dict)}
    return {}


# v1.11.11: wall-clock budget per stage, counted from the first time bp_progress.py reported it as NEXT. B03-v1117p and
# B10-v1119p spent over an hour (68M tokens on B03) re-writing a multiverse adapter that never reproduced the stored
# baseline, and ended turn after turn asking for a new sandbox. When the budget is used up the stage is recorded in
# skipped.json and the run moves on.
STAGE_BUDGET_MIN = {4: 40}


def over_budget(out: Path, num: int) -> float | None:
    """Minutes spent on stage `num` if over its budget, else None. Starts the clock on first call."""
    if num not in STAGE_BUDGET_MIN:
        return None
    stamp = out / f".stage{num}_started"
    if not stamp.exists():
        out.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(time.time()), encoding="utf-8")
        return None
    try:
        mins = (time.time() - float(stamp.read_text(encoding="utf-8").strip())) / 60
    except (OSError, ValueError):
        return None
    return mins if mins > STAGE_BUDGET_MIN[num] else None


def record_skip(out: Path, num: int, name: str, reason: str) -> None:
    p = out / "skipped.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    if isinstance(d, list):
        d = {str(x.get("stage", f"item {i}")): x for i, x in enumerate(d) if isinstance(x, dict)}
    if not isinstance(d, dict):
        d = {}
    d[f"stage {num}"] = {"stage": name, "reason": reason, "by": "bp_progress.py time budget"}
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default="bp_out")
    ap.add_argument("--work", default="bp_work")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    sk = skipped(out)
    rows, nxt = [], None
    for num, name, skill, files, cmd in STAGES:
        paths = [Path(f.format(out=out, work=work)) for f in files]
        done = stage_done(name, paths)
        skip = name != "judgement checks" and any(k in sk for k in (f"stage {num}", f"stage{num}", name)
                                                  + ((str(num),) if num != 2 else ()))
        if not done and not skip and nxt is None:
            mins = over_budget(out, num)
            if mins is not None:
                record_skip(out, num, name, f"stage ran {mins:.0f} min (budget {STAGE_BUDGET_MIN[num]} min) "
                                            f"without {', '.join(p.name for p in paths)}")
                print(f"stage {num} ({name}) skipped: {mins:.0f} min used, budget {STAGE_BUDGET_MIN[num]} min; "
                      "say so in the passport assumptions and go on")
                skip = True
        state = "done" if done else ("skipped" if skip else "todo")
        rows.append({"stage": num, "name": name, "skill": skill, "state": state, "files": [str(p) for p in paths]})
        if state == "todo" and nxt is None:
            nxt = {"stage": num, "name": name, "skill": skill,
                   "command": cmd.format(out=out, work=work, S=SKILLS)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "progress.json").write_text(json.dumps({"stages": rows, "next": nxt}, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        print(f"{r['stage']} {r['name']:<17} {r['state']}")
    jp = judgement_problems(out)
    if jp and any(r["state"] == "done" for r in rows if r["stage"] >= 3):
        print("JUDGEMENT CHECKS NOT DONE: " + "; ".join(jp))
    if nxt:
        print(f"NEXT: stage {nxt['stage']} ({nxt['name']}) — load_skill('{nxt['skill']}'); {nxt['command']}")
        print("Run it now. The same output for the same data is normal, not a broken sandbox; do not end the turn "
              "to ask for a new session.")
    else:
        print("NEXT: all stages done — paste bp_out/FINAL_MESSAGE.md (final_summary.py output) as the last message")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Map checklist judgements to findings, deterministically by impact.

  not_reported + high impact   -> yellow missing_info
  not_reported + medium impact -> grey   missing_info
  partial      + high impact   -> grey   missing_info
  everything else              -> no finding (counted in the summary)

Usage:
    python checklist_to_findings.py checklist.yaml bp_out/ghost_checklist.json --out bp_out
Outputs: ghost.findings.json, ghost_forks.json (items that suggest a multiverse fork)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checklist", type=Path)
    ap.add_argument("judgements", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()

    items = {it["id"]: it for it in yaml.safe_load(args.checklist.read_text(encoding="utf-8"))["items"]}
    judgements = json.loads(args.judgements.read_text(encoding="utf-8"))

    findings, forks, counts = [], [], {}
    for j in judgements:
        it = items.get(j["id"])
        if it is None:
            print(f"warning: unknown checklist id {j['id']}", file=sys.stderr)
            continue
        status, impact = j.get("status"), it.get("impact", "medium")
        counts[status] = counts.get(status, 0) + 1
        severity = None
        if status == "not_reported" and impact == "high":
            severity = "yellow"
        elif (status == "not_reported" and impact == "medium") or (status == "partial" and impact == "high"):
            severity = "grey"
        if severity:
            findings.append({
                "module": "ghost", "type": "missing_info", "severity": severity, "claim_ids": j.get("claim_ids", []),
                "location": j.get("location") or it.get("where_to_look", ""),
                "evidence": f"[{it['id']}] {it['question']} -> {status}. {j.get('note', '')}".strip(),
                "source": "script:checklist_to_findings.py",
                "question_for_authors": j.get("question") or it["question"],
            })
        if it.get("multiverse_fork") and status in ("partial", "not_reported"):
            forks.append({"id": it["id"], "fork": it["multiverse_fork"], "status": status, "note": j.get("note", "")})

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "ghost.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "ghost_forks.json").write_text(json.dumps(forks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"judged={len(judgements)} {counts} findings={len(findings)} fork_candidates={len(forks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Export what a paper did not write down, from a finished BeyondPaper run.

A paper keeps the final result. The run behind a BeyondPaper relay package also found the analysis
choices under which the conclusion fails, the numbers that could not be reproduced, the conditions
the paper left unreported, and the experiments nobody has done yet. This script collects them into
one shareable, searchable file so the next group does not walk the same paths again.

Reads (all optional): bp_out/ledger.json, multiverse_summary.json, repro_status.json,
                      findings.json (or *.findings.json), next_experiments.json
Writes: bp_out/failure_paths.json, bp_out/failure_paths.md

Usage:
    python from_run.py bp_out [--top-experiments 3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HIDDEN_TYPES = {"missing_info", "seed_missing", "exclusion_rule", "dependency_unpinned", "leakage_risk",
                "grouped_split_risk", "test_set_reuse", "split_overlap", "stale_loop_variable", "duplicate_output"}
REPRO_BAD = {"not_reproduced", "archive_mismatch", "port_gap"}
REPRO_ZH = {"not_reproduced": "未能复现（无作者存档佐证）", "archive_mismatch": "论文数值与作者自己的存档输出不一致",
            "port_gap": "换语言/环境重算对不上，作者存档与论文一致"}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def esc(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def num(x) -> str:
    return f"{x:+.3g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else "n/a"


def all_findings(out: Path) -> list[dict]:
    merged = load(out / "findings.json", {})
    if isinstance(merged, dict) and merged.get("findings"):
        return merged["findings"]
    items = []
    for f in sorted(out.glob("*.findings.json")):
        items += [x for x in load(f, []) if isinstance(x, dict)]
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    ap.add_argument("--top-experiments", type=int, default=3)
    args = ap.parse_args()
    out = args.out

    ledger = load(out / "ledger.json", {})
    paper = ledger.get("paper", {})
    claims = {c.get("id"): c.get("text", "") for c in ledger.get("claims", [])}
    mv = load(out / "multiverse_summary.json", None)
    gate = load(out / "repro_status.json", None)
    nx = load(out / "next_experiments.json", None)
    findings = all_findings(out)

    paths = []
    if mv and mv.get("breaking_changes"):
        claim = mv.get("claim") or {}
        for b in mv["breaking_changes"]:
            paths.append({
                "kind": "analysis_choice", "claim_id": claim.get("id"),
                "claim": claim.get("text") or claims.get(claim.get("id"), ""),
                "baseline": mv.get("baseline"), "change": {"fork": b.get("fork"), "value": b.get("value")},
                "holds_in_baseline": mv.get("baseline_holds"), "holds_after_change": b.get("holds"),
                "effect_baseline": mv.get("baseline_effect"), "effect_after_change": b.get("effect"),
                "robustness": mv.get("robustness"), "n_analyses": mv.get("n_ok"),
                "evidence": "multiverse_summary.json"})
    for g in (gate or {}).get("items", []):
        if g.get("status") in REPRO_BAD:
            paths.append({"kind": "reproduction", "quantity": g.get("name"), "status": g.get("status"),
                          "reported": g.get("reported"), "reported_location": g.get("reported_location"),
                          "authors_stored_output": g.get("archived"), "our_rerun": g.get("ours"),
                          "evidence": "repro_status.json"})
    for f in findings:
        if f.get("type") == "fragile_conclusion" and f.get("severity") in ("red", "yellow"):
            paths.append({"kind": "fragile_conclusion", "claim_ids": f.get("claim_ids", []),
                          "location": f.get("location"), "detail": f.get("evidence"), "evidence": f.get("file", "findings")})

    hidden = [{"type": f.get("type"), "severity": f.get("severity"), "location": f.get("location"),
               "detail": f.get("evidence"), "question": f.get("question_for_authors"), "id": f.get("id")}
              for f in findings if f.get("type") in HIDDEN_TYPES and f.get("severity") in ("red", "yellow")]

    open_q = []
    for r in ((nx or {}).get("ranked") or [])[: args.top_experiments]:
        open_q.append({"id": r.get("id"), "title": r.get("title"), "eig_bits": r.get("eig_bits"),
                       "cost": r.get("cost"), "evidence": "next_experiments.json"})

    for i, p in enumerate(paths, 1):
        p["id"] = f"P{i}"
    result = {"paper": paper, "generated_by": "BeyondPaper from_run.py", "failure_paths": paths,
              "hidden_conditions": hidden, "open_questions": open_q,
              "keywords": sorted({str(b.get('fork')) for b in (mv or {}).get('breaking_changes', [])})}
    (out / "failure_paths.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    title = paper.get("title", "Untitled paper")
    md = [f"# 论文没写下来的东西 · {title}", "",
          f"DOI / URL：{paper.get('doi') or paper.get('url') or '未报告'}", "",
          "> 论文只保留了成立的那条分析路线。下面是 BeyondPaper 重跑时走过、但论文里看不到的路：结论在哪些"
          "合理选择下不再成立，哪些数字复现不出来，哪些条件没有报告，以及还没人做过的实验。每一条都指向本次运行的文件。", "",
          "## 1. 失败路径：换一个合理的分析选择，结论就不成立", ""]
    choice = [p for p in paths if p["kind"] == "analysis_choice"]
    if choice:
        p0 = choice[0]
        rob = p0.get("robustness")
        md += [f"结论 {p0.get('claim_id') or ''}：{p0['claim']}",
               f"基线分析：{', '.join(f'{k}={v}' for k, v in (p0.get('baseline') or {}).items())}"
               + (f"；在 {p0.get('n_analyses')} 种分析里成立 {rob:.0%}" if isinstance(rob, (int, float)) else ""), "",
               "| ID | 只改这一项 | 结论 | 效应（基线 → 改后） |", "|---|---|---|---|"]
        for p in choice:
            md.append(f"| {p['id']} | `{p['change']['fork']} = {p['change']['value']}` | "
                      f"{'成立' if p['holds_after_change'] else '不成立'} | "
                      f"{num(p['effect_baseline'])} → {num(p['effect_after_change'])} |")
    else:
        md.append("本次运行没有找到让结论翻转的单项改动（或没有运行压力测试）。")
    md += ["", "## 2. 复现不出来的数字", ""]
    rep = [p for p in paths if p["kind"] == "reproduction"]
    if rep:
        md += ["| ID | 数值 | 论文 | 作者存档输出 | 我们重算 | 状态 |", "|---|---|---|---|---|---|"]
        md += [f"| {p['id']} | {esc(p['quantity'])} | {esc(p['reported'])} | {esc(p['authors_stored_output'] if p['authors_stored_output'] is not None else '—')} | "
               f"{esc(p['our_rerun'] if p['our_rerun'] is not None else '—')} | {REPRO_ZH[p['status']]} |" for p in rep]
    else:
        md.append("没有记录到复现失败的数值（见 repro_status.json）。")
    frag = [p for p in paths if p["kind"] == "fragile_conclusion"]
    if frag:
        md += ["", "其他被判为脆弱的结论：", ""] + [f"- {p['id']} · {esc(p['location'])}：{esc(p['detail'])[:300]}" for p in frag]
    md += ["", "## 3. 没有报告的条件", ""]
    md += [f"- {'🔴' if h['severity'] == 'red' else '🟡'} {esc(h['location'])}：{esc(h['detail'])[:300]}"
           + (f"（{h['id']}）" if h.get("id") else "") for h in hidden] or ["没有高影响的未报告条件。"]
    md += ["", "## 4. 还没人做过的实验（按信息增益排序）", ""]
    md += [f"{i}. {esc(q['title'])}（`{q['id']}`，期望信息增益 {q['eig_bits']:.3f} bits）" if isinstance(q.get("eig_bits"), (int, float))
           else f"{i}. {esc(q['title'])}（`{q['id']}`）" for i, q in enumerate(open_q, 1)] or ["没有运行下一实验阶段。"]
    md += ["", "---", "", "由 BeyondPaper `from_run.py` 从本次运行导出；机器可读版本：`failure_paths.json`，"
           "可用 `search_cards.py` 在多篇论文的导出里检索。"]
    (out / "failure_paths.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"failure_paths={len(paths)} (analysis_choice={len(choice)}, reproduction={len(rep)}, fragile={len(frag)}) "
          f"hidden_conditions={len(hidden)} open_questions={len(open_q)} -> {out / 'failure_paths.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

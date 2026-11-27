#!/usr/bin/env python3
"""Write the run's closing message from the files in bp_out/ (v1.11).

The closing message is built from files, never from memory: in B12-v1105 and B13-v1105 the model's last message
said "bp_out is empty, no stage ran" while all twelve stages had finished and BP_FINDINGS_BLOCK.txt was written
(the platform had truncated its context). Paste this script's output as the final message, unchanged.

Usage:
    python final_summary.py bp_out [--work bp_work]
Prints the five-line summary and one line per red/yellow finding (v1.11.8: no longer the full JSON block, which the
platform cut as repetitive output); the block stays in bp_out/BP_FINDINGS_BLOCK.txt. Also writes bp_out/FINAL_MESSAGE.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


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
    """Same test as bp_progress.py: llm.findings.json is a list and judgement_checks.json says what was compared."""
    probs = []
    lf = load(out / "llm.findings.json", None)
    if not isinstance(lf, list):
        probs.append("llm.findings.json 缺失或不是 JSON 列表")
    jc = load(out / "judgement_checks.json", None)
    if not isinstance(jc, dict):
        return probs + ["judgement_checks.json 缺失"]
    for k in JUDGEMENT_KEYS:
        v = jc.get(k)
        t = json.dumps(v, ensure_ascii=False) if v is not None else ""
        if len(t) < 25 or "TODO" in t.upper() or "PENDING" in t.upper():
            probs.append(f"judgement_checks.json 的 '{k}' 没写比较了什么")
        elif verdict_of(jc, k) == "problem" and not answered(lf if isinstance(lf, list) else [], k):
            probs.append(f"judgement_checks.json 的 '{k}' 记为有问题，但 llm.findings.json 里没有对应条目"
                         f"（judgement.py add ... --key {k}）")
    return probs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    ap.add_argument("--work", type=Path, default=Path("bp_work"))
    a = ap.parse_args()
    out = a.out
    fj = load(out / "findings.json")
    prog = load(out / "progress.json", {}) or {}
    stages = prog.get("stages", [])
    not_done = [f"{s['stage']} {s['name']} ({s['state']})" for s in stages if s.get("state") != "done"]
    lines = []
    if not fj:
        lines.append("运行未完成：bp_out/findings.json 不存在。")
        lines.append("已完成阶段：" + (", ".join(f"{s['stage']} {s['name']}" for s in stages if s.get("state") == "done")
                                  or "无（先运行 bp_progress.py）"))
        lines.append("未完成：" + (", ".join(not_done) or "未知"))
        text = "\n".join(lines) + "\n"
        print(text, end="")
        (out / "FINAL_MESSAGE.md").write_text(text, encoding="utf-8")
        return 1
    jp = judgement_problems(out)
    if jp:
        # v1.11.7: hard gate. B02-v1116p/p2 ended without llm.findings.json and found 0/4 and 1/4 injected errors.
        lines.append("运行未完成：阶段 2 的判断检查没有做完（" + "；".join(jp) + "）。")
        lines.append("下一步：load_skill('bp-consistency-check')，运行 judgement.py init，五项检查各用一条 judgement.py note KEY ok|problem "
                     "记录比较了什么、每个 problem 用 judgement.py add 写入一条发现，然后依次重跑 verify_queue.py --init/--check、"
                     "build_passport.py、final_summary.py。")
        text = "\n".join(lines) + "\n"
        print(text, end="")
        (out / "FINAL_MESSAGE.md").write_text(text, encoding="utf-8")
        return 1
    llm_t = (out / "llm.findings.json").stat().st_mtime
    if (out / "findings.json").stat().st_mtime < llm_t:
        lines.append("运行未完成：llm.findings.json 比 findings.json 新，先重跑 verify_queue.py --init/--check 和 build_passport.py。")
        text = "\n".join(lines) + "\n"
        print(text, end="")
        (out / "FINAL_MESSAGE.md").write_text(text, encoding="utf-8")
        return 1
    c = fj.get("counts", {})
    rep = (fj.get("reproduction") or {}).get("level", "not attempted")
    mv = fj.get("multiverse") or {}
    ledger = load(out / "ledger.json", {}) or {}
    n_claims = len(ledger.get("claims", []) or fj.get("claims", []))
    fragile = "未运行多重宇宙"
    if mv:
        rob = mv.get("robustness")
        br = mv.get("breaking_changes") or []
        fragile = (f"主结论在 {rob:.0%} 的分析里成立（{mv.get('n_ok')} 个）" if isinstance(rob, (int, float)) else "稳健性 n/a")
        if br:
            fragile += "；翻转条件：" + "; ".join(f"{b.get('fork')}={b.get('value')}" for b in br[:3])
    reds = [f for f in fj.get("findings", []) if f.get("severity") == "red"]
    if reds:
        fragile += f"；红灯 {len(reds)} 条，首条：{reds[0].get('type')} @ {str(reds[0].get('location'))[:80]}"
    nx = load(out / "next_experiments.json", {}) or {}
    top = (nx.get("ranked") or [{}])[0]
    top_s = f"{top.get('id', fj.get('top_experiment') or 'n/a')}: {str(top.get('title') or top.get('name') or '')[:100]}"
    st = load(out / "paper_agent" / "selftest.json", {}) or {}
    mode = st.get("mode") or st.get("serving_mode") or ("re-run" if st.get("rerun_ok") else ("read-only" if st else "未生成"))
    ver = load(out / "verify.json", {}) or {}
    vf = ver.get("findings", [])
    up = sum(1 for e in vf if str(e.get("verdict", "")).lower() in ("upheld", "confirmed"))
    lines.append(f"1. 结论 {n_claims} 条；复现等级 {rep}（{', '.join(f'{k} {v}' for k, v in ((fj.get('reproduction') or {}).get('counts') or {}).items() if v)}）")
    lines.append(f"2. 🔴 {c.get('red', 0)} · 🟡 {c.get('yellow', 0)} · ⚪ {c.get('grey', 0)} · 🟢 {c.get('green', 0)}；"
                 f"手写红/黄经执行核对 {len(vf)} 条，成立 {up} 条")
    lines.append(f"3. 最脆弱的结论：{fragile}")
    lines.append(f"4. 首选下一实验：{top_s}")
    paths = ["passport.md", "failure_paths.md", "precedents.md", "brief_investor.md", "paper_agent/AGENT_CARD.md"]
    lines.append("5. " + " · ".join(f"bp_out/{p}" + ("" if (out / p).exists() else "（缺）") for p in paths)
                 + f"；论文智能体模式：{mode}")
    if not_done:
        lines.append("未完成的阶段：" + ", ".join(not_done))
    # v1.11.8: the full JSON block (30 KB in B04-v1117p) made the platform cut the final message as repetitive output.
    # The message lists the red and yellow findings in one line each; the full JSON stays in BP_FINDINGS_BLOCK.txt.
    block_p = out / "BP_FINDINGS_BLOCK.txt"
    if not block_p.exists():
        block_p.write_text("<<<BP_FINDINGS_JSON>>>\n" + json.dumps(fj, ensure_ascii=False) + "\n<<<END_BP_FINDINGS_JSON>>>\n",
                           encoding="utf-8")
    shown = [f for f in fj.get("findings", []) if f.get("severity") in ("red", "yellow")]
    light = {"red": "🔴", "yellow": "🟡"}
    rows = [f"- {light[f['severity']]} {f.get('id', '')} {f.get('type')} @ {str(f.get('location', ''))[:90]}"
            for f in shown[:30]]
    if len(shown) > 30:
        rows.append(f"- …另有 {len(shown) - 30} 条，见 findings.json")
    text = ("\n".join(lines) + "\n\n红/黄条目：\n" + ("\n".join(rows) or "- 无") +
            f"\n\n完整结果：bp_out/findings.json；机器可读块：bp_out/BP_FINDINGS_BLOCK.txt（{block_p.stat().st_size} 字节）\n")
    (out / "FINAL_MESSAGE.md").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

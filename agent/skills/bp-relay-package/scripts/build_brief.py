#!/usr/bin/env python3
"""One-page brief of a finished BeyondPaper run for a reviewer, an editor or an investor doing due diligence.

passport.md is written for the next researcher and runs to many pages. This page answers what a reader who
has to decide something asks first: can the main conclusions be trusted, how far were they reproduced,
under which conditions do they fail, what is missing, and which questions to put to the authors.
It reorganises files the run already wrote; it adds no new judgement.

Reads (all optional except findings.json): bp_out/findings.json, ledger.json, failure_paths.json,
precedents.json, next_experiments.json, multiverse_summary.json
Writes: bp_out/brief_<audience>.md

Usage:
    python build_brief.py bp_out --audience investor|reviewer|editor [--questions 5] [--claims 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIGHT = {"red": "🔴", "yellow": "🟡", "grey": "⚪", "green": "🟢"}
ORDER = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
LEVEL_ZH = {"full": "完全复现", "partial": "部分复现", "none": "未能复现", "not attempted": "未尝试复现"}
MISSING_TYPES = {"missing_artifact", "unverifiable_claim", "missing_info", "seed_missing", "dependency_unpinned",
                 "phantom_experiment"}

TEXT = {
    "investor": {
        "title": "尽调简报",
        "intro": "本页把一次自动审读的结果整理给做技术尽调的读者。灯号表示证据是否支撑结论，"
                 "不评价技术的商业价值，也不构成投资建议。所列问题都是潜在不一致，需要向对方核实。",
        "claims": "核心技术主张与证据强度",
        "fail": "主张在哪些条件下不成立",
        "missing": "建议要求对方补充的材料",
        "questions": "尽调时要问的问题",
        "next": "能最快降低不确定性的验证",
    },
    "reviewer": {
        "title": "审稿意见摘要",
        "intro": "本页把一次自动审读的结果整理给审稿人。所列问题都是潜在不一致，需要作者回应；"
                 "灯号表示证据是否支撑结论，不是对稿件的总体评价。",
        "claims": "主要结论与证据",
        "fail": "结论在哪些合理的分析选择下不成立",
        "missing": "复现所缺的材料",
        "questions": "请作者回应的问题",
        "next": "建议的补充分析",
    },
    "editor": {
        "title": "编辑参考页",
        "intro": "本页把一次自动审读的结果整理给编辑。所列问题都是潜在不一致，需要作者回应；"
                 "灯号表示证据是否支撑结论，不是录用建议。",
        "claims": "主要结论与证据",
        "fail": "结论在哪些合理的分析选择下不成立",
        "missing": "数据、代码可得性与缺口",
        "questions": "建议转给作者的问题",
        "next": "建议的补充分析",
    },
}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def short(s, n: int = 160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def overall(lights: list[dict]) -> str:
    """Worst light among the main claims, but a claim with no linked problem does not hide the others."""
    if not lights:
        return "grey"
    return min((c.get("light", "grey") for c in lights), key=lambda s: ORDER.get(s, 9))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    ap.add_argument("--audience", choices=sorted(TEXT), default="investor")
    ap.add_argument("--questions", type=int, default=5)
    ap.add_argument("--claims", type=int, default=5)
    a = ap.parse_args()
    out, t = a.out, TEXT[a.audience]

    res = load(out / "findings.json", None)
    if not res:
        print(f"error: {out / 'findings.json'} not found; run build_passport.py first", file=sys.stderr)
        return 1
    findings = res.get("findings", [])
    byid = {f.get("id"): f for f in findings}
    paper = res.get("paper", {}) or {}
    claims = res.get("claims", [])[: a.claims]
    repro = res.get("reproduction") or {}
    mv = load(out / "multiverse_summary.json", None) or res.get("multiverse") or {}
    fp = load(out / "failure_paths.json", {}) or {}
    prec = load(out / "precedents.json", {}) or {}
    nx = load(out / "next_experiments.json", {}) or {}
    counts = res.get("counts", {})

    worst = overall(claims)
    lvl = repro.get("level", "not attempted")
    md = [f"# {t['title']} · {paper.get('title', 'Untitled paper')}", "",
          f"DOI / URL：{paper.get('doi') or paper.get('url') or '未报告'}", "", f"> {t['intro']}", "",
          f"**总体：{LIGHT.get(worst, '⚪')} · 复现等级：{LEVEL_ZH.get(lvl, lvl)}**"
          + (f" · 多重宇宙稳健性 {mv['robustness']:.0%}（{mv.get('n_ok')} 种分析）"
             if isinstance(mv.get("robustness"), (int, float)) else ""),
          f"发现：🔴 {counts.get('red', 0)} · 🟡 {counts.get('yellow', 0)} · ⚪ {counts.get('grey', 0)} · 🟢 {counts.get('green', 0)}",
          "", f"## 1. {t['claims']}", "", "| | 结论 | 主要依据的问题 |", "|---|---|---|"]
    for c in claims:
        linked = [byid[i] for i in c.get("findings", []) if i in byid and byid[i].get("severity") in ("red", "yellow")]
        linked.sort(key=lambda f: ORDER.get(f.get("severity"), 9))
        why = short(linked[0].get("evidence"), 120) if linked else ("无红黄问题" if c.get("light") == "green" else "材料不足以核实")
        md.append(f"| {LIGHT.get(c.get('light'), '⚪')} {c.get('id')} | {short(c.get('text'), 110)} | {why.replace('|', '/')} |")

    md += ["", f"## 2. {t['fail']}", ""]
    rows = []
    for p in fp.get("failure_paths", []):
        if p.get("kind") == "analysis_choice":
            ch = p.get("change") or {}
            rows.append(f"- 只改 `{ch.get('fork')} = {ch.get('value')}`，结论 {p.get('claim_id') or ''} 不再成立"
                        + (f"（效应 {p['effect_baseline']:+.3g} → {p['effect_after_change']:+.3g}）"
                           if isinstance(p.get("effect_baseline"), (int, float)) and isinstance(p.get("effect_after_change"), (int, float)) else ""))
    if not rows and mv.get("breaking_changes"):
        rows = [f"- 只改 `{b.get('fork')} = {b.get('value')}`，结论不再成立" for b in mv["breaking_changes"]]
    rep = [p for p in fp.get("failure_paths", []) if p.get("kind") == "reproduction" and p.get("status") == "archive_mismatch"]
    rows += [f"- 论文数值与作者自己的存档输出不一致：{short(p.get('quantity'), 60)}（论文 {p.get('reported')}，存档 {p.get('authors_stored_output')}）"
             for p in rep[:3]]
    hidden = [h for h in fp.get("hidden_conditions", []) if h.get("severity") == "red"][:3]
    rows += [f"- 未报告的条件：{short(h.get('detail'), 140)}" for h in hidden]
    md += rows[:7] or ["- 本次运行没有找到让结论翻转的单项改动（或没有运行压力测试）。"]

    md += ["", f"## 3. {t['missing']}", ""]
    miss = [f for f in findings if f.get("type") in MISSING_TYPES and f.get("severity") in ("red", "yellow", "grey")]
    seen, items = set(), []
    for f in sorted(miss, key=lambda f: ORDER.get(f.get("severity"), 9)):
        q = f.get("question_for_authors") or f.get("evidence")
        k = short(q, 80)
        if k in seen:
            continue
        seen.add(k)
        items.append(f"- {short(q, 150)}（{f.get('id')}）")
    md += items[:6] or ["- 无。"]

    md += ["", f"## 4. {t['questions']}", ""]
    qs, seenq = [], set()
    ranked = sorted(findings, key=lambda f: (ORDER.get(f.get("severity"), 9), 0 if f.get("claim_ids") else 1,
                                              1 if f.get("hand_written") else 0))
    for f in ranked:
        q = f.get("question_for_authors")
        if not q or f.get("severity") not in ("red", "yellow"):
            continue
        k = short(q, 70).lower()
        if k in seenq:
            continue
        seenq.add(k)
        qs.append(f"{len(qs) + 1}. {short(q, 200)}（{f.get('id')}，{LIGHT.get(f.get('severity'))}）")
        if len(qs) >= a.questions:
            break
    md += qs or ["无。"]

    links = [r for r in prec.get("links", []) if r.get("precedents")]
    if links:
        md += ["", f"先例：{len(links)} 条问题属于在其他已发表论文里核实过的失败机制（见 `precedents.md`）。"]

    md += ["", f"## 5. {t['next']}", ""]
    md += [f"{i}. {short(r.get('title'), 140)}" for i, r in enumerate((nx.get("ranked") or [])[:3], 1)] or ["未运行下一实验阶段。"]
    md += ["", "---", "", "由 BeyondPaper `build_brief.py` 从本次运行的 findings.json、failure_paths.json 等文件整理；"
           "每条都可在 `passport.md` 里按编号找到证据。"]
    path = out / f"brief_{a.audience}.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"brief ({a.audience}): overall={worst} reproduction={lvl} claims={len(claims)} questions={len(qs)} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

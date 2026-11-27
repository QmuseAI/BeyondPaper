#!/usr/bin/env python3
"""Build a BeyondPaper null-result card (负结果卡片) from card.json + a results table.

The script checks the card, recomputes the outcome per condition (method vs best baseline),
compares it with the stated verdict, hashes the artifacts, and renders the card.

Usage:
    python build_card.py bp_work/null/card.json --out bp_out [--tol 0.01]
Outputs (in --out): null_result_card.md, null_summary.json, null.findings.json, card_index.json,
                    null_gap.png (if matplotlib is available)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REQUIRED = ["title", "question", "hypothesis", "method", "baselines", "metric", "results", "verdict", "why_stopped"]
VERDICTS = {"method_loses", "no_difference", "method_wins_only_when", "inconclusive"}
RESERVED = {"condition", "method", "value", "run"}
ZH = {"loss": "输", "win": "赢", "tie": "持平"}


def finding(ftype, severity, location, evidence, question=None):
    return {"module": "null", "type": ftype, "severity": severity, "claim_ids": [], "location": location,
            "evidence": evidence, "source": "script:build_card.py", "question_for_authors": question}


def esc(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def fnum(x) -> str:
    if x is None:
        return "—"
    return f"{x:.4g}" if isinstance(x, float) else str(x)


def load_rows(card: dict, card_dir: Path):
    res = card.get("results") or {}
    if res.get("rows"):
        return list(res["rows"]), "inline rows"
    table = res.get("table")
    if not table:
        return [], None
    path = Path(table)
    if not path.is_absolute():
        path = card_dir / path
    if not path.exists():
        return None, str(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh)), str(path)


def analyse(card: dict, rows: list[dict], tol: float):
    """Per condition: mean/spread per method, method vs best baseline, outcome."""
    target = card["method"]["name"]
    baselines = [b["name"] for b in card.get("baselines", [])]
    lower = (card["metric"].get("direction", "lower").lower() == "lower")
    cells = defaultdict(lambda: defaultdict(list))
    axes = defaultdict(set)
    bad_rows = 0
    for r in rows:
        try:
            v = float(r["value"])
        except (KeyError, TypeError, ValueError):
            bad_rows += 1
            continue
        cond = str(r.get("condition", "all"))
        cells[cond][str(r.get("method", "")).strip()].append(v)
        for k, val in r.items():
            if k not in RESERVED and val not in (None, ""):
                axes[k].add(str(val))
    out = []
    for cond, by in cells.items():
        stats = {m: {"mean": statistics.fmean(v), "min": min(v), "max": max(v), "n": len(v)} for m, v in by.items()}
        present = [b for b in baselines if b in stats]
        row = {"condition": cond, "methods": stats, "outcome": None, "best_baseline": None, "gap_rel": None}
        if target not in stats or not present:
            row["outcome"] = "incomplete"
            out.append(row)
            continue
        best = (min if lower else max)(present, key=lambda b: stats[b]["mean"])
        m, b = stats[target], stats[best]
        worse = (m["mean"] - b["mean"]) if lower else (b["mean"] - m["mean"])  # > 0: method is worse
        gap = worse / max(abs(b["mean"]), 1e-12)
        overlap = m["n"] > 1 and b["n"] > 1 and not (m["min"] > b["max"] or m["max"] < b["min"])
        outcome = "tie" if (abs(gap) <= tol or overlap) else ("loss" if worse > 0 else "win")
        row.update(best_baseline=best, gap_abs=worse, gap_rel=gap, outcome=outcome)
        out.append(row)
    out.sort(key=lambda r: r["condition"])
    return out, {k: sorted(v) for k, v in axes.items()}, bad_rows


def overall(conds):
    o = [c["outcome"] for c in conds if c["outcome"] != "incomplete"]
    if not o:
        return "no_data"
    if all(x == "loss" for x in o):
        return "loses_in_all_tested_conditions"
    if all(x == "tie" for x in o):
        return "no_difference_in_tested_conditions"
    if "win" in o and "loss" in o:
        return "mixed"
    if "win" in o:
        return "wins_in_some_conditions"
    return "loses_or_ties"


CONSISTENT = {
    "method_loses": {"loses_in_all_tested_conditions", "loses_or_ties"},
    "no_difference": {"no_difference_in_tested_conditions", "loses_or_ties"},
    "method_wins_only_when": {"mixed", "wins_in_some_conditions"},
    "inconclusive": {"mixed", "no_data", "loses_or_ties", "no_difference_in_tested_conditions",
                     "wins_in_some_conditions", "loses_in_all_tested_conditions"},
}


def sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def plot(conds, card, out: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    rows = [c for c in conds if c["outcome"] != "incomplete"]
    if not rows:
        return False
    target = card["method"]["name"]
    labels = [c["condition"] for c in rows]
    mv = [c["methods"][target]["mean"] for c in rows]
    bv = [c["methods"][c["best_baseline"]]["mean"] for c in rows]
    x = range(len(rows))
    fig, ax = plt.subplots(figsize=(max(4.5, 1.2 * len(rows) + 2), 3.4))
    w = 0.38
    ax.bar([i - w / 2 for i in x], mv, w, label=target, color="#c0504d")
    ax.bar([i + w / 2 for i in x], bv, w, label="best baseline", color="#4f81bd")
    for i, c in enumerate(rows):
        ax.annotate(c["best_baseline"], (i + w / 2, bv[i]), ha="center", va="bottom", fontsize=7)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    m = card["metric"]
    ax.set_ylabel(f"{m.get('name', 'metric')}" + (f" ({m['unit']})" if m.get("unit") else "")
                  + (" ↓" if m.get("direction", "lower") == "lower" else " ↑"), fontsize=8)
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "null_gap.png", dpi=160)
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--tol", type=float, default=0.01, help="relative gap counted as a tie (default 1%%)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    card = json.loads(args.card.read_text(encoding="utf-8"))
    card_dir = args.card.resolve().parent
    findings = []

    missing = [k for k in REQUIRED if not card.get(k)]
    if missing:
        findings.append(finding("card_incomplete", "red", "card.json", f"Missing required fields: {', '.join(missing)}."))
    if card.get("verdict", {}).get("label") not in VERDICTS:
        findings.append(finding("card_incomplete", "red", "card.json → verdict.label",
                                f"verdict.label must be one of {sorted(VERDICTS)}."))
    if any(k in missing for k in ("method", "baselines", "metric", "results")):
        (args.out / "null.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
        print("card incomplete; see null.findings.json", file=sys.stderr)
        return 1

    rows, table_path = load_rows(card, card_dir)
    if rows is None:
        findings.append(finding("missing_artifact", "red", "results.table", f"Results table not found: {table_path}."))
        rows = []
    conds, axes, bad_rows = analyse(card, rows, args.tol)
    verdict_calc = overall(conds)
    label = card.get("verdict", {}).get("label")

    if not rows:
        findings.append(finding("unverifiable_claim", "yellow", "results",
                                "No results rows: the verdict cannot be checked against data."))
    if bad_rows:
        findings.append(finding("number_mismatch", "yellow", table_path or "results",
                                f"{bad_rows} row(s) have no numeric `value` and were ignored."))
    names = {card["method"]["name"], *[b["name"] for b in card.get("baselines", [])]}
    seen = {m for c in conds for m in c["methods"]}
    if seen - names:
        findings.append(finding("card_incomplete", "yellow", table_path or "results",
                                f"Methods in the table but not in the card: {', '.join(sorted(seen - names))}."))
    for c in conds:
        if c["outcome"] == "incomplete":
            findings.append(finding("missing_artifact", "yellow", f"condition {c['condition']}",
                                    "The method or every baseline is missing in this condition; no comparison."))
    if label in CONSISTENT and verdict_calc != "no_data" and verdict_calc not in CONSISTENT[label]:
        findings.append(finding("unsupported_claim", "red", "verdict.label vs results",
                                f"The card says '{label}', but the data give '{verdict_calc}'.",
                                "Which conditions does the verdict refer to?"))
    elif verdict_calc != "no_data":
        findings.append(finding("robust_conclusion", "green", "verdict.label vs results",
                                f"Verdict '{label}' agrees with the data ('{verdict_calc}', {len(conds)} condition(s))."))
    if not card.get("conditions_not_tested"):
        findings.append(finding("missing_info", "yellow", "conditions_not_tested",
                                "No untested conditions listed; say where the method might still work."))
    if len(card.get("baselines", [])) and not any(b.get("why_this_baseline") for b in card["baselines"]):
        findings.append(finding("missing_info", "yellow", "baselines",
                                "No baseline says why it was chosen; a null result against a weak baseline is uninformative."))
    if not (card.get("reproduce") or {}).get("command"):
        findings.append(finding("missing_info", "grey", "reproduce", "No command to re-run the comparison."))

    arts = []
    for a in card.get("artifacts", []):
        p = Path(a.get("path", ""))
        p = p if p.is_absolute() else card_dir / p
        digest = sha256(p)
        arts.append({**a, "sha256": digest, "exists": digest is not None})
        if digest is None:
            findings.append(finding("missing_artifact", "grey", a.get("path", "?"), "Artifact listed in the card was not found."))

    summary = {"id": card.get("id"), "verdict_stated": label, "verdict_computed": verdict_calc,
               "tolerance": args.tol, "metric": card["metric"], "conditions": conds, "axes": axes,
               "artifacts": arts, "results_table": table_path}
    (args.out / "null_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "null.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    index = {k: card.get(k) for k in ("id", "title", "domain", "keywords", "question", "date", "contact")}
    index.update(method=card["method"]["name"], baselines=[b["name"] for b in card.get("baselines", [])],
                 verdict=label, verdict_computed=verdict_calc, card_path=str(args.card))
    (args.out / "card_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    has_plot = plot(conds, card, args.out)

    m = card["metric"]
    target = card["method"]["name"]
    losses = sum(c["outcome"] == "loss" for c in conds)
    md = [f"# 负结果卡片 · {card.get('title', '')}", "",
          f"`{card.get('id', 'NR-?')}` · {card.get('domain', '')} · {card.get('date', '')}"
          + (f" · {card['contact']}" if card.get("contact") else "") + (f" · {card['license']}" if card.get("license") else ""), "",
          f"> **结论（按数据重算）**：{target} 在 {len(conds)} 个测试条件中输 {losses} 个；"
          f"卡片判断 `{label}`，脚本判断 `{verdict_calc}`。"
          + ("" if verdict_calc == "no_data" or label not in CONSISTENT or verdict_calc in CONSISTENT[label]
             else " ⚠️ 两者不一致，见末尾检查项。"), "",
          f"{card['verdict'].get('text', '')}", "",
          "## 1. 问题与预期", "", f"- 问题：{card.get('question', '')}", f"- 预期：{card.get('hypothesis', '')}", ""]
    data = card.get("data") or {}
    if any(data.values()):
        md += ["- 数据 / 实例：" + "；".join(f"{k}: {v}" for k, v in data.items() if v), ""]
    md += ["## 2. 方法与对照", "", "| 角色 | 名称 | 版本 | 设置 | 预算 | 为什么选它 |", "|---|---|---|---|---|---|"]
    mm = card["method"]
    md.append(f"| 被测方法 | {esc(mm.get('name'))} | {esc(mm.get('version'))} | {esc(mm.get('settings'))} | {esc(mm.get('budget'))} | — |")
    for b in card.get("baselines", []):
        md.append(f"| 对照 | {esc(b.get('name'))} | {esc(b.get('version'))} | {esc(b.get('settings'))} | "
                  f"{esc(b.get('budget'))} | {esc(b.get('why_this_baseline'))} |")
    unit = f" ({m['unit']})" if m.get("unit") else ""
    arrow = "越低越好" if m.get("direction", "lower") == "lower" else "越高越好"
    md += ["", f"## 3. 结果（指标：{m.get('name')}{unit}，{arrow}）", "",
           f"| 条件 | {esc(target)} | 最强对照 | 对照值 | 差距（绝对 / 相对） | 结果 |", "|---|---|---|---|---|---|"]
    for c in conds:
        if c["outcome"] == "incomplete":
            md.append(f"| {esc(c['condition'])} | — | — | — | — | 数据不全 |")
            continue
        t, b = c["methods"][target], c["methods"][c["best_baseline"]]
        spread = lambda s: f"{s['mean']:.4g}" + (f" [{s['min']:.4g}, {s['max']:.4g}], n={s['n']}" if s["n"] > 1 else "")
        md.append(f"| {esc(c['condition'])} | {spread(t)} | {esc(c['best_baseline'])} | {spread(b)} | "
                  f"{c['gap_abs']:+.4g} / {c['gap_rel']:+.1%} | {ZH[c['outcome']]} |")
    md += ["", f"差距 > 0 表示被测方法更差（相对差距以最强对照为分母）；|相对差距| ≤ {args.tol:.0%} 或多次运行的区间重叠时记为持平。"
           f" 数据来源：{esc((card.get('results') or {}).get('source') or table_path or '—')}。"]
    if has_plot:
        md += ["", "![null_gap.png](null_gap.png)"]
    md += ["", "## 4. 测过的条件，和没测过的条件", ""]
    if axes:
        md += [f"- 已测 `{k}`：{', '.join(v)}" for k, v in axes.items()]
    if card.get("conditions_tested"):
        md.append(f"- {card['conditions_tested']}")
    md += ["", "**还没人试过（这个方法可能仍有机会的地方）：**", ""]
    md += [f"- {x}" for x in card.get("conditions_not_tested", [])] or ["- 未列出"]
    md += ["", "## 5. 为什么停下", "", card.get("why_stopped", ""), "", "## 6. 如何复现", ""]
    rp = card.get("reproduce") or {}
    md += [f"- 命令：`{rp['command']}`" if rp.get("command") else "- 命令：未提供",
           f"- 环境：{rp.get('environment') or '未提供'}", f"- 运行时间：{rp.get('runtime') or '未提供'}"]
    if arts:
        md += ["", "| 文件 | 类型 | SHA-256 |", "|---|---|---|"]
        md += [f"| {esc(a.get('path'))} | {esc(a.get('kind'))} | " + (f"`{a['sha256'][:16]}…`" if a["sha256"] else "未找到") + " |"
               for a in arts]
    if card.get("related_claims"):
        md += ["", "## 7. 相关的已发表结论", ""]
        md += [f"- {c.get('claim', '')}" + (f" — {c['url']}" if c.get("url") else "") for c in card["related_claims"]]
    issues = [f for f in findings if f["severity"] in ("red", "yellow")]
    if issues:
        md += ["", "## 卡片检查项", ""]
        md += [f"- {'🔴' if f['severity'] == 'red' else '🟡'} {f['location']}：{f['evidence']}" for f in issues]
    md += ["", "---", "", "BeyondPaper 负结果卡片 · 由 `build_card.py` 生成；数字均来自上面列出的结果文件。"]
    (args.out / "null_result_card.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    reds = sum(f["severity"] == "red" for f in findings)
    print(f"conditions={len(conds)} losses={losses} verdict_stated={label} verdict_computed={verdict_calc} "
          f"red={reds} -> {args.out / 'null_result_card.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

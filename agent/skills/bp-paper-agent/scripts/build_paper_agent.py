#!/usr/bin/env python3
"""Build an audited paper agent from a finished BeyondPaper run (stage 8).

Like Paper2Agent, every executable tool is tested before it is enabled, and a tool that fails is
switched off rather than shipped. Unlike Paper2Agent, the reference for the tests is not only the
paper: the tools are checked against our recorded re-run, and every answer carries the paper's
reproduction status, the claim's traffic light and the choices under which the claim flips.

Tests
  T1 resources      findings.json and ledger.json exist (resource layer: claims, findings, failure paths)
  T2 baseline       the adapter run with the paper's own choices gives the recorded baseline outputs
  T3 deterministic  two baseline runs give the same outputs
  T4 flips          every recorded breaking change flips the claim again when re-run
reproduce / whatif are enabled only if T2 and T3 pass; T4 failures are listed on the card.

Usage:
    python build_paper_agent.py bp_out --spec bp_work/multiverse_spec.yaml [--rtol 1e-4] [--skip-run]
                                [--timeout 60] [--no-demo]
Outputs (in bp_out/paper_agent/): paper_agent.py, agent_manifest.json, tool_tests.json, AGENT_CARD.md,
mcp_config.json, selftest.json, DEMO_CHAIN.md / DEMO_CHAIN.json (fixed follow-up chain; what-if results cached)
Without a tested adapter the agent runs read-only: reproduce / whatif answer from the recorded multiverse table.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paper_agent import PaperAgent, close, demo, load_json, selftest, show, LIGHT_ZH, READONLY  # noqa: E402

LIGHT = {"red": "🔴", "yellow": "🟡", "grey": "⚪", "green": "🟢"}


def test(name, fn):
    t0 = time.time()
    try:
        passed, detail = fn()
    except Exception as e:  # a failing tool is reported, not fatal
        passed, detail = False, f"{type(e).__name__}: {e}"
    return {"test": name, "passed": bool(passed), "detail": detail, "seconds": round(time.time() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--spec", type=Path, help="multiverse spec (yaml/json); default bp_work/multiverse_spec.yaml")
    ap.add_argument("--rtol", type=float, default=1e-4)
    ap.add_argument("--skip-run", action="store_true", help="resource layer only (no adapter runs)")
    # v1.10.3: 300 s (B06-v1101 timed out at 60 s); the answers are cached, so a live question returns the re-run at once
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per re-run in the demo chain")
    ap.add_argument("--no-demo", action="store_true", help="do not write DEMO_CHAIN.md")
    a = ap.parse_args()
    out = a.out.resolve()
    agent_dir = out / "paper_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "paper_agent.py", agent_dir / "paper_agent.py")

    mv = load_json(out / "multiverse_summary.json")
    spec = a.spec or Path("bp_work/multiverse_spec.yaml")
    spec = spec.resolve() if spec.exists() else None
    adapter = (mv or {}).get("adapter")
    if spec and not adapter:
        try:
            import yaml
            s = yaml.safe_load(spec.read_text(encoding="utf-8")) if spec.suffix != ".json" else json.loads(spec.read_text())
            ad = Path(s.get("adapter", ""))
            adapter = str(ad if ad.is_absolute() else (spec.parent / ad).resolve()) if s.get("adapter") else None
        except Exception:
            adapter = None
    manifest = {"bp_out": str(out), "spec": str(spec) if spec else None, "adapter": adapter,
                "built": time.strftime("%Y-%m-%d %H:%M:%S"), "tools": {}}
    (agent_dir / "agent_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    agent = PaperAgent(out)
    tests = [test("T1 resources", lambda: ((out / "findings.json").exists() and (out / "ledger.json").exists(),
                                          "findings.json + ledger.json"))]
    executable = bool(spec and adapter and Path(adapter).exists() and not a.skip_run)
    if executable:
        rec = agent.recorded_baseline()

        def t2():
            o = agent.run(agent.baseline_config())
            if not rec:
                return False, "no recorded baseline row in multiverse_results.csv"
            bad = {k: (o.get(k), v) for k, v in rec.items() if not close(o.get(k), v, a.rtol)}
            return not bad, ("all %d outputs match" % len(rec)) if not bad else f"differs: {bad}"

        def t3():
            cfg = agent.baseline_config()
            o1, o2 = agent.run(cfg), agent.run(cfg)
            bad = {k: (o1[k], o2.get(k)) for k in o1 if not close(o1[k], o2.get(k), 1e-12)}
            return not bad, "identical" if not bad else f"not deterministic: {bad}"

        def t4():
            flips = (mv or {}).get("breaking_changes", [])
            if not flips:
                return True, "no breaking changes recorded"
            res = []
            for b in flips:
                cfg = dict(agent.baseline_config())
                vals = [v for v in agent.spec()["forks"][b["fork"]] if show(v) == b["value"]]
                cfg[b["fork"]] = vals[0] if vals else b["value"]
                ev = agent.evaluate(agent.run(cfg))
                res.append((b["fork"], b["value"], ev.get("holds") == b["holds"]))
            bad = [f"{f}={v}" for f, v, ok in res if not ok]
            return not bad, f"{len(res) - len(bad)}/{len(res)} flips replayed" + (f"; not replayed: {bad}" if bad else "")

        tests += [test("T2 baseline", t2), test("T3 deterministic", t3), test("T4 flips", t4)]
        ok_exec = all(t["passed"] for t in tests if t["test"] in ("T2 baseline", "T3 deterministic"))
        reason = "T2 and T3 passed" if ok_exec else "baseline not reproduced or not deterministic (see tool_tests.json)"
    else:
        ok_exec = False
        reason = "no multiverse adapter/spec for this run" if not a.skip_run else "--skip-run"
    res_ok = tests[0]["passed"]
    manifest["tools"] = {
        "claims": {"enabled": res_ok, "layer": "resource"}, "claim": {"enabled": res_ok, "layer": "resource"},
        "findings": {"enabled": res_ok, "layer": "resource"}, "failure-paths": {"enabled": res_ok, "layer": "resource"},
        "questions": {"enabled": res_ok, "layer": "resource"}, "next": {"enabled": res_ok, "layer": "resource"},
        "reproduce": {"enabled": ok_exec, "layer": "tool", "reason": reason},
        "whatif": {"enabled": ok_exec, "layer": "tool", "reason": reason},
        "precedents": {"enabled": res_ok, "layer": "resource"},
    }
    manifest["mode"] = "rerun" if ok_exec else "readonly"
    (agent_dir / "agent_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (agent_dir / "tool_tests.json").write_text(json.dumps(tests, ensure_ascii=False, indent=2), encoding="utf-8")
    (agent_dir / "mcp_config.json").write_text(json.dumps({"mcpServers": {"beyondpaper-paper": {
        "command": "python", "args": [str(agent_dir / "paper_agent.py"), "--bp-out", str(out), "serve"]}}},
        indent=2), encoding="utf-8")

    # ---------- selftest and demo chain (read-only answers come from the recorded multiverse) ----------
    st_agent = PaperAgent(out, timeout=a.timeout)
    st = selftest(st_agent, quick=True)
    chain = None
    if not a.no_demo:
        try:
            chain = demo(PaperAgent(out, timeout=a.timeout))
        except Exception as e:  # the demo is a convenience; never fail the build on it
            chain = {"error": f"{type(e).__name__}: {e}", "steps": []}

    # ---------- agent card ----------
    doc = load_json(out / "findings.json", {}) or {}
    paper = doc.get("paper") or (load_json(out / "ledger.json", {}) or {}).get("paper", {})
    rep = doc.get("reproduction") or {}
    counts = rep.get("counts") or {}
    n_gate = sum(counts.values()) if counts else 0
    n_ok = counts.get("reproduced", 0) + counts.get("archive_ok", 0)
    claims = doc.get("claims", [])
    lights = {k: sum(1 for c in claims if c.get("light") == k) for k in ("green", "yellow", "red", "grey")}
    agent2 = PaperAgent(out)
    md = [f"# 论文智能体卡片（经审计）· {paper.get('title', '')}", "",
          "这张卡片说明：这个论文智能体能回答什么、它的工具经过了哪些测试、论文在哪些地方不能直接相信。", "",
          "## 可执行性", "",
          f"- 复现等级：**{rep.get('level', 'not attempted')}**；重算数值 {n_gate} 个，其中 {n_ok} 个与论文或作者存档一致"
          + (f"（{n_ok / n_gate:.0%}）" if n_gate else ""),
          f"- 可执行工具：{'已启用（重跑论文分析、改变分析选择）' if ok_exec else '未启用 — ' + reason}",
          f"- 运行模式：{'可重跑' if ok_exec else READONLY + '；reproduce / whatif 用已记录的多重宇宙结果（完全匹配或最接近的宇宙）回答'}",
          f"- 自检：{st['passed']}/{st['total']} 通过（selftest.json）；服务方式：{st['serve_backend']}",
          f"- 结论灯号：🟢 {lights['green']} · 🟡 {lights['yellow']} · 🔴 {lights['red']} · ⚪ {lights['grey']}", "",
          "## 工具测试", "", "| 测试 | 结果 | 说明 |", "|---|---|---|"]
    md += [f"| {t['test']} | {'通过' if t['passed'] else '未通过'} | {str(t['detail'])[:160]} |" for t in tests]
    md += ["", "## 结论与失效条件", "", "| 结论 | 灯号 | 稳健性 / 在这些条件下不成立 |", "|---|---|---|"]
    for c in agent2.claims()["claims"]:
        extra = ""
        if c.get("robustness") is not None:
            extra = f"{c['robustness']:.0%} 的合理分析中成立；" + ("、".join(c.get("breaks_under") or []) or "无单项翻转")
        md.append(f"| {c['id']}: {str(c.get('text', ''))[:80]} | {LIGHT.get(c.get('light'), '⚪')} | {extra} |")
    reds = [f for f in doc.get("findings", []) if f.get("severity") in ("red", "yellow")][:8]
    if reds:
        md += ["", "## 使用前要知道的问题", ""]
        md += [f"- {LIGHT[f['severity']]} {f.get('type')} · {f.get('location')}: {str(f.get('evidence', ''))[:180]}" for f in reds]
    if chain and chain.get("steps"):
        md += ["", "## 追问演示链（DEMO_CHAIN.md）", ""]
        md += [f"{i}. {st_['q']} → {str(st_['answer'])[:160]}" for i, st_ in enumerate(chain["steps"], 1)]
    md += ["", "## 怎么用", "", "```bash",
           f"python {agent_dir / 'paper_agent.py'} claims --text",
           f"python {agent_dir / 'paper_agent.py'} claim C1",
           f"python {agent_dir / 'paper_agent.py'} whatif <choice>=<value>" + ("" if ok_exec else "   # 只读：最接近的已记录宇宙"),
           f"python {agent_dir / 'paper_agent.py'} demo      # 固定追问链",
           f"python {agent_dir / 'paper_agent.py'} selftest  # 每个工具跑一次",
           "```", "",
           "接入任何 MCP 客户端（Claude Code、Cursor 等）：把 `mcp_config.json` 的内容加入客户端的 MCP 配置"
           "（有 `mcp` 库时走 MCP；没有时 `serve` 自动退回 JSON-lines：每行一个 `{\"tool\": ..., \"args\": {...}}`）。"
           "每个回答都附带它依赖的结论灯号、复现等级和已知失效条件。", ""]
    (agent_dir / "AGENT_CARD.md").write_text("\n".join(md), encoding="utf-8")
    passed = sum(t["passed"] for t in tests)
    print(f"paper agent built: tests passed {passed}/{len(tests)}; executable tools "
          f"{'enabled' if ok_exec else 'disabled (' + reason + ') - read-only mode'}; selftest {st['passed']}/{st['total']}; "
          f"serve {st['serve_backend']}; demo {'written' if chain and chain.get('steps') else 'skipped'} "
          f"-> {agent_dir / 'AGENT_CARD.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

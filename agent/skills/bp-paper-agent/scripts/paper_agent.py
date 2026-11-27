#!/usr/bin/env python3
"""An audited paper agent: answer questions about a paper from the files of a BeyondPaper run.

Paper2Agent (Miao et al., Nature 2026) turns a paper into an agent whose tools reproduce the paper.
This agent does the same from a finished BeyondPaper run, and every answer also says how far the
paper can be trusted on that point: the claim's traffic light, whether its numbers passed the
reproduction gate, and the analysis choices under which it stops holding.

Two modes, chosen automatically:
  re-run     the multiverse adapter passed the build tests: reproduce / whatif re-run the analysis
  read-only  no adapter, or it failed its tests, or it cannot be loaded: reproduce / whatif answer from
             the recorded multiverse table (exact or nearest universe) and say "只读模式：不能重跑"

Commands (all print JSON; add --text for a short readable version):
    card                     the agent card (what this agent can and cannot do)
    claims                   every claim with its light and robustness
    claim C1                 everything known about one claim
    reproduce                run the paper's analysis with the paper's own choices and compare
                             with the recorded baseline
    whatif key=value ...     run the analysis with other choices (e.g. split=group seed=3) and say
                             whether the claim still holds (cached; --timeout s per re-run, default 60)
    findings [--min yellow]  findings at or above a severity
    failure-paths            what the paper did not write down: failure conditions, unreproduced
                             numbers, hidden conditions, open experiments
    precedents [ID]          verified precedents in other papers for this run's findings (atlas match)
    questions                questions for the authors
    next                     next experiments ranked by expected information gain
    selftest                 run every enabled tool once; write paper_agent/selftest.json
    demo                     fixed follow-up question chain from this run; write paper_agent/DEMO_CHAIN.md
    serve [--jsonl]          serve the tools over stdio: MCP (FastMCP) if installed, otherwise a
                             dependency-free JSON-lines loop ({"tool": ..., "args": {...}} per line)

Usage:
    python paper_agent.py --bp-out bp_out claim C1
    python paper_agent.py --bp-out bp_out whatif split=group
The run directory needs findings.json (build_passport.py).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

SEV_RANK = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
LIGHT_ZH = {"red": "红", "yellow": "黄", "grey": "灰", "green": "绿"}
SAFE = {"abs": abs, "min": min, "max": max, "round": round, "len": len, "math": math}
READONLY = "只读模式：不能重跑"
DEFAULT_TIMEOUT = 60.0


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def parse_value(v):
    if not isinstance(v, str):
        return v
    if v.strip().lower() in ("none", "null"):
        return None
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def show(v) -> str:
    return "none" if v is None else str(v)


def close(a, b, rtol=1e-4, atol=1e-9) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    if math.isnan(a) and math.isnan(b):
        return True
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def as_bool(v):
    if isinstance(v, bool) or v is None:
        return v
    s = str(v).strip().lower()
    return True if s == "true" else False if s == "false" else None


def as_float(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _load_adapter_fn(path: str):
    sys.path.insert(0, str(Path(path).parent))
    sp = importlib.util.spec_from_file_location("bp_adapter", path)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.run


def _prim(outputs) -> dict:
    if not isinstance(outputs, dict):
        raise TypeError("adapter.run must return a dict")
    return {k: v for k, v in outputs.items() if isinstance(v, (int, float, bool, str))}


def _child_run(adapter_path: str, config: dict, q) -> None:  # top level so that 'spawn' can pickle it
    try:
        q.put({"ok": True, "outputs": _prim(_load_adapter_fn(adapter_path)(dict(config)))})
    except BaseException as e:  # noqa: BLE001 - report everything to the parent
        q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


class RunTimeout(Exception):
    pass


class PaperAgent:
    def __init__(self, bp_out: Path, timeout: float = DEFAULT_TIMEOUT, use_cache: bool = True):
        self.out = bp_out.resolve()
        self.agent_dir = self.out / "paper_agent"
        self.timeout = timeout
        self.use_cache = use_cache
        self.manifest = load_json(self.agent_dir / "agent_manifest.json", {}) or {}
        self.findings_doc = load_json(self.out / "findings.json", {}) or {}
        self.ledger = load_json(self.out / "ledger.json", {}) or {}
        self.mv = load_json(self.out / "multiverse_summary.json")
        self.gate = load_json(self.out / "repro_status.json", {}) or {}
        self.fp = load_json(self.out / "failure_paths.json", {}) or {}
        self.nx = load_json(self.out / "next_experiments.json", {}) or {}
        self.prec = load_json(self.out / "precedents.json", {}) or {}
        self.findings = self.findings_doc.get("findings", [])
        self.by_id = {f.get("id"): f for f in self.findings}
        self._spec = None
        self._adapter = None
        self._rows = None
        self._broken = None  # reason the adapter could not be used at run time

    # ---------- mode ----------
    def mode(self) -> str:
        return "rerun" if self.tool_enabled("whatif")[0] and not self._broken else "readonly"

    def readonly_reason(self) -> str:
        if self._broken:
            return self._broken
        ok, why = self.tool_enabled("whatif")
        return "" if ok else (why or "no tested adapter")

    # ---------- resource layer (always available) ----------
    def claims(self) -> dict:
        rows = []
        mv_cid = ((self.mv or {}).get("claim") or {}).get("id")
        for c in self.findings_doc.get("claims", []) or self.ledger.get("claims", []):
            row = {"id": c.get("id"), "text": c.get("text"), "light": c.get("light", "grey"),
                   "location": c.get("location"), "n_findings": len(c.get("findings", []))}
            if mv_cid and c.get("id") == mv_cid:
                row["robustness"] = self.mv.get("robustness")
                row["breaks_under"] = [f"{b['fork']}={b['value']}" for b in self.mv.get("breaking_changes", [])]
            rows.append(row)
        return {"claims": rows, "mode": self.mode()}

    def claim(self, cid: str) -> dict:
        c = next((x for x in self.findings_doc.get("claims", []) if x.get("id") == cid), None)
        if c is None:
            c = next((x for x in self.ledger.get("claims", []) if x.get("id") == cid), None)
        if c is None:
            return {"error": f"no claim {cid}", "known": [x.get("id") for x in self.findings_doc.get("claims", [])]}
        linked = [self.by_id[i] for i in c.get("findings", []) if i in self.by_id]
        linked += [f for f in self.findings if cid in (f.get("claim_ids") or []) and f not in linked]
        linked.sort(key=lambda f: SEV_RANK.get(f.get("severity"), 9))
        ev = [e for e in self.ledger.get("evidence", []) if e.get("id") in (c.get("evidence") or [])]
        res = {"id": cid, "text": c.get("text"), "location": c.get("location"), "light": c.get("light", "grey"),
               "conditions_stated_in_paper": c.get("conditions"), "evidence": ev,
               "findings": [{"id": f.get("id"), "severity": f.get("severity"), "type": f.get("type"),
                             "location": f.get("location"), "evidence": f.get("evidence")} for f in linked]}
        mv_claim = (self.mv or {}).get("claim") or {}
        if self.mv and mv_claim.get("id") == cid:
            res["stress_test"] = {"claim_tested": mv_claim.get("text") or mv_claim.get("holds"),
                                  "robustness": self.mv.get("robustness"), "n_analyses": self.mv.get("n_ok"),
                                  "baseline_holds": self.mv.get("baseline_holds"),
                                  "breaks_under": self.mv.get("breaking_changes", [])}
        res["failure_paths"] = [p for p in self.fp.get("failure_paths", [])
                                if p.get("claim_id") == cid or cid in (p.get("claim_ids") or [])]
        ids = {f.get("id") for f in linked}
        res["precedents"] = [r for r in self.prec.get("links", []) if r.get("finding") in ids and r.get("precedents")]
        res["caveats"] = self.caveats(cid)
        return res

    def findings_at(self, min_sev: str = "yellow") -> dict:
        lim = SEV_RANK.get(min_sev, 1)
        return {"findings": [{k: f.get(k) for k in ("id", "severity", "type", "claim_ids", "location", "evidence")}
                             for f in self.findings if SEV_RANK.get(f.get("severity"), 9) <= lim]}

    def failure_paths(self) -> dict:
        return {k: self.fp.get(k, []) for k in ("failure_paths", "hidden_conditions", "open_questions")}

    def precedents(self, key: str | None = None) -> dict:
        links = self.prec.get("links", [])
        if not links:
            return {"precedents": [], "note": "precedents.json not found: run atlas.py match bp_out (bp-pitfall-atlas)"}
        if key:
            ids = {key}
            if key in {c.get("id") for c in self.findings_doc.get("claims", [])}:
                ids = {f.get("id") for f in self.findings if key in (f.get("claim_ids") or [])}
                c = next((x for x in self.findings_doc.get("claims", []) if x.get("id") == key), {})
                ids |= set(c.get("findings", []))
            links = [r for r in links if r.get("finding") in ids]
        return {"precedents": [r for r in links if r.get("mechanism")],
                "note": "A precedent shows the mechanism is real elsewhere; it does not prove this finding."}

    def questions(self) -> dict:
        qs = [{"finding": f.get("id"), "severity": f.get("severity"), "question": f.get("question_for_authors")}
              for f in sorted(self.findings, key=lambda f: SEV_RANK.get(f.get("severity"), 9))
              if f.get("question_for_authors") and f.get("severity") in ("red", "yellow")]
        return {"questions": qs}

    def next_experiments(self) -> dict:
        return {"hypotheses": self.nx.get("hypotheses", {}),
                "ranked": [{k: r.get(k) for k in ("id", "title", "eig_bits", "score", "cost")}
                           for r in self.nx.get("ranked", [])]}

    def caveats(self, cid: str | None = None) -> list[str]:
        out = []
        rep = (self.findings_doc.get("reproduction") or {})
        if rep.get("level"):
            out.append(f"Reproduction level of this paper: {rep['level']} {json.dumps(rep.get('counts', {}))}")
        if cid:
            c = next((x for x in self.findings_doc.get("claims", []) if x.get("id") == cid), {})
            if c.get("light") in ("red", "yellow"):
                out.append(f"Claim {cid} is {c['light']} ({LIGHT_ZH[c['light']]}): see its findings before relying on it.")
        if self.mv and self.mv.get("breaking_changes"):
            br = ", ".join(f"{b['fork']}={b['value']}" for b in self.mv["breaking_changes"])
            rob = self.mv.get("robustness")
            out.append(f"The tested claim flips under: {br}"
                       + (f" (robustness {rob:.0%})." if isinstance(rob, (int, float)) else "."))
        return out

    # ---------- recorded multiverse (read-only layer) ----------
    def mv_rows(self) -> list[dict]:
        if self._rows is None:
            p = self.out / "multiverse_results.csv"
            self._rows = []
            if p.exists():
                with open(p, encoding="utf-8") as fh:
                    self._rows = [r for r in csv.DictReader(fh)]
        return self._rows

    def fork_names(self) -> list[str]:
        if self.mv and self.mv.get("forks"):
            return list(self.mv["forks"])
        try:
            return list((self.spec().get("forks") or {}))
        except Exception:
            return []

    def recorded_baseline_cfg(self) -> dict:
        if self.mv and self.mv.get("baseline"):
            return {k: show(v) for k, v in self.mv["baseline"].items()}
        try:
            return {k: show(v) for k, v in self.baseline_config().items()}
        except Exception:
            rows = self.mv_rows()
            r0 = next((r for r in rows if r.get("universe") == "0"), rows[0] if rows else {})
            return {k: r0.get(k) for k in self.fork_names() if k in r0}

    def recorded_baseline(self) -> dict:
        rows = self.mv_rows()
        if not rows:
            return {}
        row = next((r for r in rows if r.get("universe") == "0"), rows[0])
        return {k[4:]: parse_value(v) for k, v in row.items() if k.startswith("out_") and v not in ("", None)}

    def nearest_universe(self, overrides: dict) -> dict:
        """Recorded universe that matches the requested choices; nearest to the paper's baseline otherwise."""
        rows = [r for r in self.mv_rows() if not r.get("error")]
        if not rows:
            return {"found": False, "note": "no recorded multiverse results (multiverse_results.csv) for this run"}
        names = self.fork_names()
        base = self.recorded_baseline_cfg()
        want = {**base, **{k: show(v) for k, v in overrides.items()}}
        unknown = [k for k in overrides if k not in names]

        def dist(r):
            req = sum(1 for k in overrides if k in names and r.get(k) != show(overrides[k]))
            oth = sum(1 for k in names if k not in overrides and r.get(k) != base.get(k))
            return (req, oth, int(r.get("universe") or 0))

        best = min(rows, key=dist)
        d = dist(best)
        diffs = {k: {"wanted": want.get(k), "recorded": best.get(k)} for k in names if best.get(k) != want.get(k)}
        return {"found": True, "exact": d[0] == 0 and d[1] == 0 and not unknown, "universe": best.get("universe"),
                "config": {k: best.get(k) for k in names}, "differs_from_request": diffs,
                "outputs": {k[4:]: parse_value(v) for k, v in best.items() if k.startswith("out_") and v not in ("", None)},
                "holds": as_bool(best.get("holds")), "effect": as_float(best.get("effect")),
                "unknown_choices": unknown,
                "available_choices": (self.mv or {}).get("forks") or {k: sorted({r.get(k) for r in rows}) for k in names}}

    def _readonly_whatif(self, overrides: dict, why: str) -> dict:
        nu = self.nearest_universe(overrides)
        base_row = self.nearest_universe({})
        res = {"mode": READONLY, "why_readonly": why, "changed": {k: show(v) for k, v in overrides.items()}}
        if not nu.get("found"):
            res["answer"] = (f"{READONLY}。本次运行没有记录多重宇宙结果，无法给出这组选择下的数值。"
                             + self._flip_hint(overrides))
            res["caveats"] = self.caveats(((self.mv or {}).get("claim") or {}).get("id"))
            return res
        res["recorded_universe"] = nu
        bh = base_row.get("holds") if base_row.get("found") else (self.mv or {}).get("baseline_holds")
        tag = "完全匹配的已记录宇宙" if nu["exact"] else "最接近的已记录宇宙"
        if nu.get("unknown_choices"):
            tag += f"（{', '.join(nu['unknown_choices'])} 不是本次分析的选择项）"
        verdict = ""
        if nu.get("holds") is not None:
            verdict = "结论成立" if nu["holds"] else "结论不成立"
            if bh is not None and nu["holds"] != bh:
                verdict += "（与论文自己的选择相比发生翻转）"
        diff = "；".join(f"{k}: 想要 {v['wanted']}，记录的是 {v['recorded']}" for k, v in nu["differs_from_request"].items())
        res["answer"] = (f"{READONLY}。{tag} #{nu['universe']}：{verdict or '未记录结论判定'}"
                         + (f"，效应 {nu['effect']:+.4g}" if nu.get("effect") is not None else "")
                         + (f"。与请求的差别：{diff}" if diff else "") + "。")
        if nu["outputs"] and base_row.get("found"):
            res["delta_vs_baseline"] = {k: as_float(v) - as_float(base_row["outputs"][k]) for k, v in nu["outputs"].items()
                                        if as_float(v) is not None and as_float(base_row["outputs"].get(k)) is not None}
        res["caveats"] = self.caveats(((self.mv or {}).get("claim") or {}).get("id"))
        return res

    def _flip_hint(self, overrides: dict) -> str:
        br = [b for b in (self.mv or {}).get("breaking_changes", []) if b.get("fork") in overrides]
        if br:
            return " 已记录：" + "；".join(f"{b['fork']}={b['value']} 时结论{'成立' if b.get('holds') else '不成立'}" for b in br)
        return " 要重跑需要可用的 adapter（bp_work/adapter.py）通过 build_paper_agent.py 的测试。"

    # ---------- tool layer (needs the adapter; enabled by build tests) ----------
    def tool_enabled(self, name: str) -> tuple[bool, str]:
        tools = self.manifest.get("tools", {})
        t = tools.get(name)
        if t is None:
            return False, "not built: run build_paper_agent.py first"
        return bool(t.get("enabled")), t.get("reason", "")

    def spec(self) -> dict:
        if self._spec is None:
            p = self.manifest.get("spec")
            if not p:
                raise RuntimeError("no multiverse spec recorded in the manifest")
            text = Path(p).read_text(encoding="utf-8")
            if p.endswith(".json"):
                self._spec = json.loads(text)
            else:
                import yaml
                self._spec = yaml.safe_load(text)
        return self._spec

    def adapter(self):
        if self._adapter is None:
            self._adapter = _load_adapter_fn(self.manifest.get("adapter"))
        return self._adapter

    def baseline_config(self) -> dict:
        s = self.spec()
        base = {k: parse_value(v) if isinstance(v, str) else v for k, v in (s.get("baseline") or {}).items()}
        for k, vals in (s.get("forks") or {}).items():
            if k not in base:
                base[k] = vals[0]
        return base

    def evaluate(self, outputs: dict) -> dict:
        claim = self.spec().get("claim", {})
        res = {"claim": claim.get("text") or claim.get("holds")}
        try:
            res["holds"] = bool(eval(claim["holds"], {"__builtins__": {}}, {**SAFE, **outputs}))
            if claim.get("effect"):
                res["effect"] = float(eval(claim["effect"], {"__builtins__": {}}, {**SAFE, **outputs}))
        except Exception as e:  # report, do not crash
            res["error"] = f"claim expression failed: {e}"
        return res

    def run(self, config: dict, timeout: float | None = None) -> dict:
        """Run the adapter. timeout=None/0: in this process (build tests); otherwise in a child process."""
        if not timeout or timeout <= 0:
            return _prim(self.adapter()(dict(config)))
        methods = mp.get_all_start_methods()
        ctx = mp.get_context("fork" if "fork" in methods else "spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_child_run, args=(self.manifest.get("adapter"), dict(config), q), daemon=True)
        p.start()
        try:
            msg = q.get(timeout=timeout)
        except Exception:
            p.terminate()
            p.join(2)
            raise RunTimeout(f"re-run did not finish within {timeout:g} s")
        p.join(2)
        if not msg.get("ok"):
            raise RuntimeError(msg.get("error"))
        return msg["outputs"]

    # cache ------------------------------------------------------------
    def _cache_path(self, kind: str, cfg: dict) -> Path:
        ad = self.manifest.get("adapter") or ""
        try:
            mt = Path(ad).stat().st_mtime if ad else 0
        except OSError:
            mt = 0
        key = json.dumps({"kind": kind, "cfg": {k: show(v) for k, v in sorted(cfg.items())}, "adapter": ad,
                          "adapter_mtime": mt, "built": self.manifest.get("built")}, sort_keys=True)
        return self.agent_dir / "cache" / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")

    def _cached_run(self, kind: str, cfg: dict):
        """(outputs, cached, seconds); raises RunTimeout / adapter errors."""
        cp = self._cache_path(kind, cfg)
        if self.use_cache:
            hit = load_json(cp)
            if hit and "outputs" in hit:
                return hit["outputs"], True, hit.get("seconds")
        t0 = time.time()
        outputs = self.run(cfg, timeout=self.timeout)
        sec = round(time.time() - t0, 2)
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"kind": kind, "config": {k: show(v) for k, v in cfg.items()},
                                      "outputs": outputs, "seconds": sec,
                                      "when": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, default=str),
                          encoding="utf-8")
        except OSError:
            pass
        return outputs, False, sec

    def reproduce(self) -> dict:
        gate = [{k: g.get(k) for k in ("name", "reported", "archived", "ours", "status")}
                for g in self.gate.get("items", [])]
        rec = self.recorded_baseline()
        if self.mode() == "readonly":
            nu = self.nearest_universe({})
            return {"mode": READONLY, "why_readonly": self.readonly_reason(),
                    "answer": f"{READONLY}。以下是本次运行记录的基线（论文自己的分析选择）结果和复现门结果。",
                    "recorded_baseline_config": self.recorded_baseline_cfg(), "recorded_baseline_outputs": rec,
                    "recorded_claim_holds": nu.get("holds") if nu.get("found") else (self.mv or {}).get("baseline_holds"),
                    "reproduction_gate": gate, "caveats": self.caveats()}
        cfg = self.baseline_config()
        try:
            outputs, cached, sec = self._cached_run("reproduce", cfg)
        except RunTimeout as e:
            r = self._readonly_whatif({}, str(e))
            r["timeout"] = True
            r["mode"] = "recorded (re-run timed out)"
            r["answer"] = (f"重跑超过 {self.timeout:g} 秒，先给出多重宇宙里已记录的同一组选择的结果"
                           f"（可用 --timeout 0 不限时重跑）：" + r["answer"].replace(READONLY + "。", "", 1))
            r["reproduction_gate"] = gate
            return r
        except Exception as e:  # adapter broke after the build: fall back, do not crash
            self._broken = f"adapter failed at run time: {e}"
            return self.reproduce()
        cmp = {k: {"now": outputs.get(k), "recorded": v, "match": close(outputs.get(k), v)} for k, v in rec.items()}
        n_ok = sum(1 for v in cmp.values() if v["match"])
        return {"mode": "rerun", "cached": cached, "seconds": sec, "config": {k: show(v) for k, v in cfg.items()},
                "outputs": outputs, "vs_recorded_baseline": cmp,
                "answer": f"已按论文自己的选择重跑：{n_ok}/{len(cmp)} 个输出与记录的基线一致。" if cmp else "已重跑（无记录基线可比）。",
                "claim": self.evaluate(outputs), "reproduction_gate": gate, "caveats": self.caveats()}

    def whatif(self, overrides: dict) -> dict:
        overrides = {k: parse_value(v) for k, v in overrides.items()}
        if self.mode() == "readonly":
            return self._readonly_whatif(overrides, self.readonly_reason())
        try:
            base_cfg = self.baseline_config()
            forks = {k: [show(x) for x in v] for k, v in (self.spec().get("forks") or {}).items()}
        except Exception as e:
            self._broken = f"spec unreadable at run time: {e}"
            return self._readonly_whatif(overrides, self._broken)
        notes = []
        for k, v in overrides.items():
            if k not in base_cfg:
                notes.append(f"'{k}' is not an analysis choice of this adapter; known: {sorted(base_cfg)}")
            elif show(v) not in forks.get(k, []):
                notes.append(f"{k}={show(v)} was not part of the tested multiverse ({k}: {forks.get(k)}); "
                             "the adapter accepts it, but no build test covers it")
        cfg = {**base_cfg, **overrides}
        try:
            outputs, cached, sec = self._cached_run("whatif", cfg)
        except RunTimeout as e:
            r = self._readonly_whatif(overrides, str(e))
            r["timeout"] = True
            r["mode"] = "recorded (re-run timed out)"
            r["answer"] = (f"重跑超过 {self.timeout:g} 秒，先给出多重宇宙里已记录的同一组选择的结果"
                           f"（可用 --timeout 0 不限时重跑）：" + r["answer"].replace(READONLY + "。", "", 1))
            return r
        except Exception as e:
            self._broken = f"adapter failed at run time: {e}"
            return self._readonly_whatif(overrides, self._broken)
        base_out = self.recorded_baseline()
        delta = {}
        for k, v in outputs.items():
            if k in base_out:
                try:
                    delta[k] = float(v) - float(base_out[k])
                except (TypeError, ValueError):
                    pass
        res = {"mode": "rerun", "cached": cached, "seconds": sec,
               "changed": {k: show(v) for k, v in overrides.items()}, "config": {k: show(v) for k, v in cfg.items()},
               "outputs": outputs, "baseline_outputs": base_out, "delta_vs_baseline": delta,
               "claim": self.evaluate(outputs), "notes": notes}
        base_eval = self.evaluate(base_out) if base_out else {}
        if "holds" in res["claim"] and "holds" in base_eval and res["claim"]["holds"] != base_eval["holds"]:
            res["verdict"] = (f"The claim {'holds' if res['claim']['holds'] else 'no longer holds'} under this change "
                              f"(it {'holds' if base_eval['holds'] else 'does not hold'} with the paper's choices).")
            res["answer"] = f"已重跑：改动后结论{'成立' if res['claim']['holds'] else '不成立'}（论文自己的选择下{'成立' if base_eval['holds'] else '不成立'}），发生翻转。"
        elif "holds" in res["claim"]:
            res["verdict"] = f"The claim {'still holds' if res['claim']['holds'] else 'still does not hold'} under this change."
            res["answer"] = f"已重跑：改动后结论仍{'成立' if res['claim']['holds'] else '不成立'}。"
        if res["claim"].get("effect") is not None:
            res["answer"] = res.get("answer", "已重跑。") + f"效应 {res['claim']['effect']:+.4g}。"
        res["caveats"] = self.caveats(((self.spec().get("claim") or {}).get("id")))
        return res

    def card(self) -> dict:
        p = self.agent_dir / "AGENT_CARD.md"
        return {"card": p.read_text(encoding="utf-8") if p.exists() else "AGENT_CARD.md not built yet",
                "tools": self.manifest.get("tools", {}), "mode": self.mode()}

    # ---------- dispatch shared by CLI, JSON-lines server and selftest ----------
    def call(self, tool: str, args: dict | None = None) -> dict:
        args = args or {}
        t = ALIASES.get(tool, tool)
        if t == "claims":
            return self.claims()
        if t == "claim":
            return self.claim(str(args.get("claim_id") or args.get("id") or "C1"))
        if t == "findings":
            return self.findings_at(args.get("min", "yellow"))
        if t == "failure-paths":
            return self.failure_paths()
        if t == "precedents":
            return self.precedents(args.get("id"))
        if t == "questions":
            return self.questions()
        if t == "next":
            return self.next_experiments()
        if t == "card":
            return self.card()
        if t == "reproduce":
            return self.reproduce()
        if t == "whatif":
            ch = args.get("changes") if isinstance(args.get("changes"), dict) else {k: v for k, v in args.items()}
            return self.whatif(ch)
        if t == "list_tools":
            return {"tools": sorted(set(ALIASES) | set(TOOL_NAMES)), "mode": self.mode(),
                    "resources": sorted(RESOURCES)}
        return {"error": f"unknown tool {tool!r}", "tools": sorted(TOOL_NAMES)}


TOOL_NAMES = ["claims", "claim", "findings", "failure-paths", "precedents", "questions", "next", "card",
              "reproduce", "whatif", "list_tools"]
ALIASES = {"list_claims": "claims", "get_claim": "claim", "what_if": "whatif", "failure_paths": "failure-paths",
           "questions_for_authors": "questions", "next_experiments": "next"}
RESOURCES = {"passport": "passport.md", "ledger": "ledger.json", "findings": "findings.json",
             "failure_paths": "failure_paths.md", "precedents": "precedents.md", "agent_card": "paper_agent/AGENT_CARD.md"}


def to_text(obj, indent=0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        return "\n".join(f"{pad}{k}: " + (("\n" + to_text(v, indent + 1)) if isinstance(v, (dict, list)) and v else str(v))
                         for k, v in obj.items())
    if isinstance(obj, list):
        return "\n".join(f"{pad}- " + (to_text(x, indent + 1).lstrip() if isinstance(x, (dict, list)) else str(x)) for x in obj)
    return pad + str(obj)


# ---------- serving ----------
def handle_request(agent: PaperAgent, req) -> dict:
    """One JSON-lines request -> one response. {"id":..,"tool":..,"args":{..}} or {"resource": name}."""
    rid = req.get("id") if isinstance(req, dict) else None
    try:
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
        if req.get("resource"):
            name = req["resource"]
            if name not in RESOURCES:
                return {"id": rid, "ok": False, "error": f"unknown resource {name!r}", "resources": sorted(RESOURCES)}
            p = agent.out / RESOURCES[name]
            return {"id": rid, "ok": p.exists(), "result": p.read_text(encoding="utf-8") if p.exists() else ""}
        res = agent.call(str(req.get("tool", "")), req.get("args") or {})
        return {"id": rid, "ok": "error" not in res, "result": res}
    except Exception as e:  # a bad request never kills the server
        return {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}


def serve_jsonl(agent: PaperAgent, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    print(f"BeyondPaper paper agent: JSON-lines stdio mode ({agent.mode()}). One request per line, e.g. "
          '{"id":1,"tool":"whatif","args":{"split":"group"}}; {"tool":"list_tools"}; Ctrl-D to stop.', file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {"id": None, "ok": False, "error": f"bad JSON: {e}"}
        else:
            if isinstance(req, dict) and req.get("tool") in ("quit", "exit"):
                break
            resp = handle_request(agent, req)
        stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
        stdout.flush()
    return 0


def mcp_backend():
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        return "mcp"
    except Exception:
        pass
    try:
        from fastmcp import FastMCP  # noqa: F401
        return "fastmcp"
    except Exception:
        return None


def serve(agent: PaperAgent, force_jsonl: bool = False) -> int:
    backend = None if force_jsonl else mcp_backend()
    if backend is None:
        if not force_jsonl:
            print("MCP library not installed (pip install mcp); falling back to JSON-lines stdio.", file=sys.stderr)
        return serve_jsonl(agent)
    if backend == "mcp":
        from mcp.server.fastmcp import FastMCP
    else:
        from fastmcp import FastMCP
    print(f"BeyondPaper paper agent: MCP stdio mode via {backend} ({agent.mode()}).", file=sys.stderr)
    title = ((agent.findings_doc.get("paper") or agent.ledger.get("paper") or {}).get("title") or "paper")[:60]
    mcp = FastMCP(f"BeyondPaper audited agent: {title}")

    @mcp.tool()
    def list_claims() -> dict:
        """Claims of the paper with traffic lights (red/yellow/grey/green) and robustness."""
        return agent.claims()

    @mcp.tool()
    def get_claim(claim_id: str) -> dict:
        """Evidence, findings, stress-test result, failure paths and precedents for one claim (e.g. 'C1')."""
        return agent.claim(claim_id)

    @mcp.tool()
    def reproduce() -> dict:
        """Re-run the paper's analysis with the paper's own choices (read-only mode: recorded baseline)."""
        return agent.reproduce()

    @mcp.tool()
    def what_if(changes: dict) -> dict:
        """Re-run with other choices, e.g. {"split": "group"} (read-only mode: nearest recorded universe)."""
        return agent.whatif(changes)

    @mcp.tool()
    def failure_paths() -> dict:
        """What the paper did not write down: failure conditions, unreproduced numbers, hidden conditions."""
        return agent.failure_paths()

    @mcp.tool()
    def precedents(finding_or_claim_id: str = "") -> dict:
        """Verified precedents in other papers for this run's findings (all, one finding, or one claim)."""
        return agent.precedents(finding_or_claim_id or None)

    @mcp.tool()
    def questions_for_authors() -> dict:
        """Questions for the authors raised by red and yellow findings."""
        return agent.questions()

    @mcp.tool()
    def next_experiments() -> dict:
        """Next experiments ranked by expected information gain."""
        return agent.next_experiments()

    for name, fname in RESOURCES.items():
        path = agent.out / fname

        def make(p=path):
            return lambda: p.read_text(encoding="utf-8") if p.exists() else ""
        mcp.resource(f"beyondpaper://{name}")(make())
    mcp.run()
    return 0


# ---------- selftest ----------
def selftest(agent: PaperAgent, quick: bool = False) -> dict:
    """Run every tool once; quick=True skips adapter re-runs (the build tests already ran them)."""
    results = []

    def t(name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append({"tool": name, "passed": bool(ok), "detail": str(detail)[:300], "seconds": round(time.time() - t0, 2)})

    claims = agent.claims()["claims"]
    cid = claims[0]["id"] if claims else "C1"
    t("claims", lambda: (bool(claims), f"{len(claims)} claims"))
    t("claim", lambda: ("error" not in agent.claim(cid), cid))
    t("findings", lambda: (True, f"{len(agent.findings_at('yellow')['findings'])} red/yellow"))
    t("failure-paths", lambda: (True, f"{len(agent.failure_paths()['failure_paths'])} failure paths"))
    t("precedents", lambda: (True, f"{len(agent.precedents()['precedents'])} linked findings"))
    t("questions", lambda: (True, f"{len(agent.questions()['questions'])} questions"))
    t("next", lambda: (True, f"{len(agent.next_experiments()['ranked'])} ranked experiments"))
    mode = agent.mode()
    if mode == "rerun" and quick:
        results.append({"tool": "reproduce/whatif", "passed": True, "detail": "skipped in quick mode (build tests T2/T3 ran them)",
                        "seconds": 0})
    else:
        def rep():
            r = agent.reproduce()
            if r.get("mode") == "rerun":
                m = r.get("vs_recorded_baseline", {})
                return all(v["match"] for v in m.values()), r.get("answer")
            return "answer" in r, r.get("answer")
        t("reproduce", rep)
        q = pick_whatif(agent)

        def wi():
            if not q:
                return True, "no fork to vary"
            r = agent.whatif(q)
            return "error" not in r and "answer" in r, f"{q}: {r.get('answer')}"
        t("whatif", wi)
    resp = handle_request(agent, {"id": 1, "tool": "list_claims"})
    t("jsonl round-trip", lambda: (resp.get("ok") is True and json.loads(json.dumps(resp, default=str)) is not None, "handle_request ok"))
    backend = mcp_backend()
    doc = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode, "readonly_reason": agent.readonly_reason(),
           "serve_backend": backend or "jsonl (MCP library not installed)", "quick": quick,
           "passed": sum(r["passed"] for r in results), "total": len(results), "results": results}
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    (agent.agent_dir / "selftest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


# ---------- demo chain ----------
def fragile_claim(agent: PaperAgent):
    cl = agent.claims()["claims"]
    if not cl:
        return None
    rank = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
    return min(cl, key=lambda c: (c.get("robustness") if isinstance(c.get("robustness"), (int, float)) else 2,
                                  rank.get(c.get("light"), 2), -c.get("n_findings", 0)))


def pick_whatif(agent: PaperAgent) -> dict:
    mv = agent.mv or {}
    if mv.get("breaking_changes"):
        b = mv["breaking_changes"][0]
        return {b["fork"]: b["value"]}
    base = mv.get("baseline") or {}
    best, gap = None, -1.0
    be = mv.get("baseline_effect")
    for k, vals in (mv.get("marginals") or {}).items():
        for v, s in vals.items():
            if v == show(base.get(k)) or s.get("mean_effect") is None:
                continue
            g = abs(s["mean_effect"] - be) if isinstance(be, (int, float)) else 1.0 - (s.get("holds_rate") or 0)
            if g > gap:
                best, gap = {k: v}, g
    if best:
        return best
    for k, vals in (mv.get("forks") or {}).items():
        for v in vals:
            if v != show(base.get(k)):
                return {k: v}
    return {}


def short(res: dict) -> str:
    if res.get("answer"):
        return str(res["answer"])
    return json.dumps(res, ensure_ascii=False, default=str)[:400]


def demo(agent: PaperAgent) -> dict:
    a = f"python {agent.agent_dir / 'paper_agent.py'} --bp-out {agent.out}"
    mode = agent.mode()
    steps = []
    fc = fragile_claim(agent)
    cid = fc["id"] if fc else "C1"
    if fc:
        rob = fc.get("robustness")
        ans = (f"{cid}（{LIGHT_ZH.get(fc.get('light'), '灰')}灯）：{str(fc.get('text', ''))[:120]}"
               + (f"；在 {rob:.0%} 的合理分析中成立" if isinstance(rob, (int, float)) else "")
               + (f"；在 {', '.join(fc.get('breaks_under') or [])} 下不成立" if fc.get("breaks_under") else "")
               + f"；关联发现 {fc.get('n_findings', 0)} 条。")
    else:
        ans = "本次运行没有记录结论。"
    steps.append({"q": "这篇论文里哪个结论最不牢靠？", "cmd": f"{a} claims --text", "answer": ans})
    w = pick_whatif(agent)
    if w:
        kv = " ".join(f"{k}={v}" for k, v in w.items())
        r = agent.whatif(w)
        steps.append({"q": "如果把 " + "、".join(f"{k} 改成 {v}" for k, v in w.items()) + "，结论还成立吗？", "cmd": f"{a} whatif {kv}", "answer": short(r),
                      "mode": r.get("mode"), "cached": r.get("cached")})
    else:
        steps.append({"q": "换一个分析选择，结论还成立吗？", "cmd": f"{a} whatif <choice>=<value>",
                      "answer": "本次运行没有多重宇宙选择项，无法回答。"})
    r = agent.reproduce()
    steps.append({"q": "按论文自己的分析选择重跑，能得到记录的结果吗？", "cmd": f"{a} reproduce", "answer": short(r),
                  "mode": r.get("mode")})
    c = agent.claim(cid) if fc else {}
    top = (c.get("findings") or [])[:2]
    prec = c.get("precedents") or []
    if top:
        ans = "；".join(f"{LIGHT_ZH.get(f.get('severity'), '')}灯 {f.get('type')} @ {f.get('location')}" for f in top)
        if prec:
            ans += "。失败机制库先例：" + "；".join(f"{p.get('finding')} → {p.get('mechanism')}（{', '.join(p.get('precedents')[:3])}）"
                                           for p in prec[:2])
        else:
            ans += "。失败机制库里没有对应先例。"
    else:
        ans = "这个结论没有关联的红/黄发现。"
    steps.append({"q": f"{cid} 的问题出在哪里？别的论文出过同样的问题吗？", "cmd": f"{a} claim {cid} && {a} precedents {cid}",
                  "answer": ans})
    nx = agent.next_experiments()["ranked"]
    if nx:
        n0 = nx[0]
        ans = f"{n0.get('title')}（{n0.get('id')}" + (f"，期望信息增益 {n0['eig_bits']:.3f} bits" if isinstance(n0.get("eig_bits"), (int, float)) else "") + "）"
    else:
        ans = "本次运行没有排序的下一步实验。"
    steps.append({"q": "下一步做哪个实验最能减少不确定性？", "cmd": f"{a} next --text", "answer": ans})
    doc = {"mode": mode, "readonly_reason": agent.readonly_reason(), "claim": cid, "steps": steps,
           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    title = ((agent.findings_doc.get("paper") or agent.ledger.get("paper") or {}).get("title") or "")
    md = [f"# 追问演示链 · {title}", "",
          f"模式：{'可重跑' if mode == 'rerun' else READONLY + '（' + agent.readonly_reason() + '）'}。"
          "每一步的命令都可以原样再执行；重跑结果已缓存，现场再问会立即返回。", ""]
    for i, s in enumerate(steps, 1):
        md += [f"## {i}. {s['q']}", "", "```bash", s["cmd"], "```", "", s["answer"], ""]
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    (agent.agent_dir / "DEMO_CHAIN.md").write_text("\n".join(md), encoding="utf-8")
    (agent.agent_dir / "DEMO_CHAIN.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bp-out", type=Path, default=None,
                    help="run folder (default: the folder above paper_agent/, or a positional bp_out for demo/selftest)")
    ap.add_argument("--text", action="store_true", help="readable output instead of JSON")
    ap.add_argument("command", choices=["card", "claims", "claim", "reproduce", "whatif", "findings", "failure-paths",
                                        "precedents", "questions", "next", "serve", "selftest", "demo"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--min", default="yellow", help="findings: minimum severity")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per re-run (0 = no limit, in-process)")
    ap.add_argument("--no-cache", action="store_true", help="always re-run, ignore cached what-if results")
    ap.add_argument("--jsonl", action="store_true", help="serve: force the JSON-lines stdio loop")
    ap.add_argument("--quick", action="store_true", help="selftest: skip adapter re-runs")
    a = ap.parse_args()
    bp_out = a.bp_out
    if bp_out is None and a.command in ("demo", "selftest") and a.args:
        bp_out = Path(a.args[0])
    if bp_out is None:
        bp_out = Path(__file__).resolve().parent.parent
    agent = PaperAgent(bp_out, timeout=a.timeout, use_cache=not a.no_cache)
    if a.command == "serve":
        return serve(agent, force_jsonl=a.jsonl)
    if a.command == "selftest":
        res = selftest(agent, quick=a.quick)
        print(json.dumps(res, ensure_ascii=False, indent=2) if not a.text else
              f"selftest {res['passed']}/{res['total']} passed · mode {res['mode']} · serve {res['serve_backend']}"
              f" -> {agent.agent_dir / 'selftest.json'}")
        return 0 if res["passed"] == res["total"] else 1
    if a.command == "demo":
        res = demo(agent)
        print((agent.agent_dir / "DEMO_CHAIN.md").read_text(encoding="utf-8"))
        return 0
    if a.command == "claim":
        if not a.args:
            ap.error("claim needs an id, e.g. claim C1")
        res = agent.claim(a.args[0])
    elif a.command == "precedents":
        res = agent.precedents(a.args[0] if a.args else None)
    elif a.command == "whatif":
        overrides = {}
        for kv in a.args:
            if "=" not in kv:
                ap.error(f"whatif takes key=value pairs, got {kv!r}")
            k, v = kv.split("=", 1)
            overrides[k] = parse_value(v)
        res = agent.whatif(overrides)
    else:
        res = {"card": agent.card, "claims": agent.claims, "reproduce": agent.reproduce,
               "findings": lambda: agent.findings_at(a.min), "failure-paths": agent.failure_paths,
               "questions": agent.questions, "next": agent.next_experiments}[a.command]()
    print(to_text(res) if a.text else json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0 if "error" not in res else 1


if __name__ == "__main__":
    sys.exit(main())

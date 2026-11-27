#!/usr/bin/env python3
"""Multiverse runner: re-run an analysis over a grid of defensible choices.

The spec (YAML or JSON) names an adapter file exposing `run(config) -> dict`, a baseline
(the paper's own choices), a set of forks, and the claim as expressions over the outputs:

    adapter: bp_work/adapter.py
    baseline: {split: random, seed: 0, outlier_z: none}
    forks: {split: [random, group], seed: [0, 1, 2], outlier_z: [none, 3.0]}
    claim: {id: C1, text: "...", holds: "mae_A < mae_B", effect: "mae_B - mae_A"}
    max_universes: 200

The baseline and every one-at-a-time deviation from it are always run; if the full grid is
larger than max_universes, the rest is sampled at random (seeded).

Usage:
    python multiverse.py spec.yaml --out bp_out [--jobs 4] [--seed 0] [--time-budget 900]

Time budget (v1.10.3): universes run in order (baseline and one-at-a-time deviations first). Once the budget
is spent no new universe starts, the ones still running are finished, and the results so far are written with
"timed_out": true in the summary. A progress line is printed after every universe (B12-v1102 polled a silent
run for 45 minutes).
Outputs: multiverse_results.csv, multiverse_summary.json, multiverse.findings.json
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
import random
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

SAFE = {"abs": abs, "min": min, "max": max, "round": round, "len": len, "math": math}
_ADAPTER = None


def load_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text)


def resolve(p: str, spec_path: Path) -> Path:
    cand = Path(p)
    if cand.is_absolute() or cand.exists():
        return cand.resolve()
    return (spec_path.parent / p).resolve()


def _load_adapter(path: str):
    global _ADAPTER
    if _ADAPTER is None:
        sys.path.insert(0, str(Path(path).parent))
        spec = importlib.util.spec_from_file_location("bp_adapter", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _ADAPTER = mod.run
    return _ADAPTER


def norm(v):
    """YAML 'none'/'null' strings and None are the same choice."""
    if isinstance(v, str) and v.strip().lower() in ("none", "null"):
        return None
    return v


def show(v) -> str:
    return "none" if v is None else str(v)


def run_one(adapter_path: str, config: dict) -> dict:
    t0 = time.time()
    try:
        outputs = _load_adapter(adapter_path)(dict(config))
        if not isinstance(outputs, dict):
            raise TypeError("adapter.run must return a dict")
        return {"outputs": outputs, "error": None, "runtime_s": time.time() - t0}
    except Exception as e:  # record and continue
        return {"outputs": {}, "error": f"{type(e).__name__}: {e}", "runtime_s": time.time() - t0}


def evaluate(expr: str, outputs: dict):
    return eval(expr, {"__builtins__": {}}, {**SAFE, **outputs})  # spec is authored by the analyst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0, help="seed for sampling universes when the grid is too large")
    ap.add_argument("--time-budget", type=float, default=900.0,
                    help="seconds; no new universe starts after this (0 = no limit)")
    args = ap.parse_args()

    spec = load_spec(args.spec)
    adapter = str(resolve(spec["adapter"], args.spec))
    forks = {k: [norm(v) for v in vals] for k, vals in spec["forks"].items()}
    names = list(forks)
    baseline = {k: norm(v) for k, v in (spec.get("baseline") or {}).items()}
    for k in names:
        if k not in baseline:
            baseline[k] = forks[k][0]
        elif baseline[k] not in forks[k]:
            print(f"note: baseline {k}={baseline[k]!r} added to its fork list", file=sys.stderr)
            forks[k].insert(0, baseline[k])
    fixed = {k: v for k, v in baseline.items() if k not in forks}  # non-forked settings passed through
    claim = spec["claim"]
    max_u = int(spec.get("max_universes", 200))

    def key_of(cfg):
        return tuple(show(cfg[k]) for k in names)

    base_cfg = {k: baseline[k] for k in names}
    must = [base_cfg] + [dict(base_cfg, **{k: v}) for k in names for v in forks[k] if v != baseline[k]]
    grid = [dict(zip(names, combo)) for combo in itertools.product(*(forks[k] for k in names))]
    seen = {key_of(c) for c in must}
    rest = [c for c in grid if key_of(c) not in seen]
    random.Random(args.seed).shuffle(rest)
    universes = must + rest[: max(0, max_u - len(must))]
    print(f"grid={len(grid)} running={len(universes)} (baseline + {len(must) - 1} one-at-a-time deviations)")

    configs = [{**fixed, **c} for c in universes]
    t0 = time.time()
    budget = args.time_budget if args.time_budget and args.time_budget > 0 else float("inf")
    done: dict[int, dict] = {}

    def progress(i: int, res: dict) -> None:
        state = "ok" if res["error"] is None else "error"
        print(f"[{len(done)}/{len(configs)}] universe {i} {state} {res['runtime_s']:.1f}s "
              f"elapsed={time.time() - t0:.0f}s", flush=True)

    if args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            pending, nxt = {}, 0
            while nxt < len(configs) or pending:
                while nxt < len(configs) and len(pending) < args.jobs and time.time() - t0 < budget:
                    pending[ex.submit(run_one, adapter, configs[nxt])] = nxt
                    nxt += 1
                if not pending:
                    break
                finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                for fut in finished:
                    i = pending.pop(fut)
                    done[i] = fut.result()
                    progress(i, done[i])
    else:
        for i, c in enumerate(configs):
            if time.time() - t0 >= budget:
                break
            done[i] = run_one(adapter, c)
            progress(i, done[i])
    timed_out = len(done) < len(configs)
    if timed_out:
        print(f"time budget {args.time_budget:.0f}s reached: {len(done)} of {len(configs)} universes run", flush=True)
    keep = sorted(done)
    universes = [universes[i] for i in keep]
    results = [done[i] for i in keep]
    must = [m for j, m in enumerate(must) if j in done]

    rows = []
    for i, (cfg, res) in enumerate(zip(universes, results)):
        row = {"universe": i, **{k: show(cfg[k]) for k in names}}
        holds = effect = None
        if res["error"] is None:
            try:
                holds = bool(evaluate(claim["holds"], res["outputs"]))
                effect = float(evaluate(claim["effect"], res["outputs"])) if claim.get("effect") else None
            except Exception as e:
                res["error"] = f"claim expression failed: {e}"
        row.update({f"out_{k}": v for k, v in res["outputs"].items() if isinstance(v, (int, float, bool))})
        row.update({"holds": holds, "effect": effect, "runtime_s": round(res["runtime_s"], 3), "error": res["error"]})
        rows.append(row)

    ok = [r for r in rows if r["error"] is None]
    base = rows[0]
    summary = {
        "claim": claim, "adapter": adapter, "forks": {k: [show(v) for v in vals] for k, vals in forks.items()},
        "baseline": {k: show(v) for k, v in base_cfg.items()},
        "n_grid": len(grid), "n_planned": len(configs), "n_run": len(rows), "n_ok": len(ok),
        "n_failed": len(rows) - len(ok), "timed_out": timed_out,
        "wall_time_s": round(time.time() - t0, 1),
        "baseline_holds": base["holds"], "baseline_effect": base["effect"], "baseline_error": base["error"],
        "robustness": (sum(r["holds"] for r in ok) / len(ok)) if ok else None,
        "marginals": {}, "breaking_changes": [],
    }
    for k in names:
        summary["marginals"][k] = {}
        for v in forks[k]:
            sub = [r for r in ok if r[k] == show(v)]
            effects = [r["effect"] for r in sub if r["effect"] is not None]
            summary["marginals"][k][show(v)] = {
                "n": len(sub),
                "holds_rate": (sum(r["holds"] for r in sub) / len(sub)) if sub else None,
                "mean_effect": (sum(effects) / len(effects)) if effects else None,
            }
    for i, cfg in enumerate(must[1:], start=1):
        r = rows[i]
        changed = next(k for k in names if cfg[k] != baseline[k])
        if r["error"] is None and base["holds"] is not None and r["holds"] != base["holds"]:
            summary["breaking_changes"].append({"fork": changed, "value": show(cfg[changed]),
                                                "holds": r["holds"], "effect": r["effect"]})

    findings = []
    cid = [claim["id"]] if claim.get("id") else []
    rob = summary["robustness"]
    if base["error"] is not None:
        findings.append({"module": "multiverse", "type": "rerun_failed", "severity": "grey", "claim_ids": cid,
                         "location": "baseline universe", "evidence": f"Baseline run failed: {base['error']}",
                         "source": "script:multiverse.py", "question_for_authors": None})
    elif not base["holds"]:
        findings.append({"module": "multiverse", "type": "rerun_mismatch", "severity": "red", "claim_ids": cid,
                         "location": "baseline universe",
                         "evidence": f"With the paper's own choices the claim '{claim.get('text', claim['holds'])}' "
                                     f"does not hold on re-run (effect = {base['effect']}).",
                         "source": "script:multiverse.py",
                         "question_for_authors": "Which settings or data version produced the reported result?"})
    else:
        breaks = "; ".join(f"{b['fork']}={b['value']}" for b in summary["breaking_changes"]) or "none"
        text = (f"Claim '{claim.get('text', claim['holds'])}' holds in {rob:.0%} of {len(ok)} universes. "
                f"Single changes that flip it: {breaks}."
                + (f" Time budget reached: {len(rows)} of {len(configs)} planned universes ran." if timed_out else ""))
        if rob >= 0.9 and not summary["breaking_changes"]:
            ftype, sev = "robust_conclusion", "green"
        elif rob < 0.5:
            ftype, sev = "fragile_conclusion", "red"
        else:
            ftype, sev = "fragile_conclusion", "yellow"
        findings.append({"module": "multiverse", "type": ftype, "severity": sev, "claim_ids": cid,
                         "location": "multiverse_summary.json", "evidence": text, "source": "script:multiverse.py",
                         "question_for_authors": ("Was the result checked under " + breaks + "?") if sev != "green" else None})

    args.out.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r}, key=lambda k: (
        0 if k == "universe" else 1 if k in names else 3 if k in ("holds", "effect", "runtime_s", "error") else 2, k))
    with open(args.out / "multiverse_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (args.out / "multiverse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "multiverse.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    rob_s = f"{rob:.0%}" if rob is not None else "n/a"
    print(f"ok={len(ok)}/{len(rows)} robustness={rob_s} baseline_holds={base['holds']} "
          f"breaking_changes={len(summary['breaking_changes'])} time={summary['wall_time_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

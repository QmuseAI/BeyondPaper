#!/usr/bin/env python3
"""Rank candidate follow-up experiments by expected information gain (EIG).

For hypotheses H with prior p(H) and an experiment with outcomes o and likelihoods
p(o | H):
    p(o)      = sum_H p(o | H) p(H)
    p(H | o)  = p(o | H) p(H) / p(o)
    EIG       = Entropy[p(H)] - sum_o p(o) Entropy[p(H | o)]     (bits; mutual information I(H; O))
    score     = EIG * (1 + w_novelty * novelty) * feasibility / cost ** cost_exponent

Usage:
    python rank_experiments.py bp_out/next_experiments.yaml --out bp_out
Outputs: next_experiments.json, next_experiments.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import textwrap
from pathlib import Path

import yaml


def entropy(ps) -> float:
    return -sum(p * math.log2(p) for p in ps if p > 0)


def likelihood_table(exp: dict, hyps: list[str], warnings: list[str]) -> dict[str, dict[str, float]]:
    outcomes = exp["outcomes"]
    table = {o: {h: float(v.get(h, float("nan"))) for h in hyps} for o, v in outcomes.items()}
    for h in hyps:
        col = [table[o][h] for o in table]
        if all(math.isnan(x) for x in col):
            others = [g for g in hyps if g != h and not any(math.isnan(table[o][g]) for o in table)]
            for o in table:
                table[o][h] = sum(table[o][g] for g in others) / len(others) if others else 1 / len(table)
            warnings.append(f"{exp['id']}: no likelihoods for {h}; using the mean of the other hypotheses (non-diagnostic)")
            continue
        if any(math.isnan(x) for x in col):
            for o in table:
                if math.isnan(table[o][h]):
                    table[o][h] = 0.0
            warnings.append(f"{exp['id']}: some likelihoods for {h} missing; set to 0")
        s = sum(table[o][h] for o in table)
        if s <= 0:
            raise ValueError(f"{exp['id']}: likelihoods for {h} sum to 0")
        if abs(s - 1) > 1e-6:
            warnings.append(f"{exp['id']}: likelihoods for {h} sum to {s:.3f}; normalised")
            for o in table:
                table[o][h] /= s
    return table


def analyse(exp: dict, prior: dict[str, float], weights: dict, warnings: list[str]) -> dict:
    hyps = list(prior)
    lik = likelihood_table(exp, hyps, warnings)
    h0 = entropy(prior.values())
    rows, expected_post_entropy = [], 0.0
    for o, l in lik.items():
        p_o = sum(l[h] * prior[h] for h in hyps)
        post = {h: (l[h] * prior[h] / p_o if p_o > 0 else 0.0) for h in hyps}
        expected_post_entropy += p_o * entropy(post.values())
        best = max(post, key=post.get)
        rows.append({"outcome": o, "p_outcome": p_o, "posterior": post, "favours": best, "p_favoured": post[best]})
    eig = max(0.0, h0 - expected_post_entropy)
    cost = float(exp.get("cost", 1.0))
    feas = float(exp.get("feasibility", 1.0))
    nov = float(exp.get("novelty", 0.0))
    score = eig * (1 + float(weights.get("novelty", 0.5)) * nov) * feas / max(cost, 1e-9) ** float(weights.get("cost_exponent", 1.0))
    return {**{k: v for k, v in exp.items() if k != "outcomes"}, "eig_bits": eig,
            "eig_share": eig / h0 if h0 > 0 else 0.0, "score": score, "outcomes": rows}


def card(rank: int, r: dict, hyp_text: dict[str, str]) -> str:
    lines = [f"### {rank}. {r['title']}  (`{r['id']}`)", "",
             f"- **Tests:** {', '.join(r.get('tests', []))}",
             f"- **Variable changed:** {r.get('variable', '—')}",
             f"- **Controls:** {r.get('controls', '—')}",
             f"- **Minimal sample:** {r.get('min_sample', '—')}",
             f"- **Cost:** {r.get('cost', 1)} · **Feasibility:** {r.get('feasibility', 1)} · **Novelty:** {r.get('novelty', 0)}",
             f"- **Expected information gain:** {r['eig_bits']:.3f} bits ({r['eig_share']:.0%} of current uncertainty) · **Score:** {r['score']:.3f}",
             f"- **Why:** {r.get('rationale', '—')}", "",
             "| Outcome | P(outcome) | Most supported afterwards | Posterior |", "|---|---|---|---|"]
    for o in r["outcomes"]:
        post = ", ".join(f"{h} {p:.2f}" for h, p in o["posterior"].items())
        lines.append(f"| {o['outcome']} | {o['p_outcome']:.2f} | {o['favours']}: {textwrap.shorten(hyp_text.get(o['favours'], ''), 60, placeholder='…')} | {post} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()

    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8"))
    hyps = spec["hypotheses"]
    prior = {h: float(v["prior"]) for h, v in hyps.items()}
    total = sum(prior.values())
    warnings: list[str] = []
    if abs(total - 1) > 1e-6:
        warnings.append(f"priors sum to {total:.3f}; normalised")
        prior = {h: p / total for h, p in prior.items()}
    weights = spec.get("weights", {})

    ranked = sorted((analyse(e, prior, weights, warnings) for e in spec["experiments"]),
                    key=lambda r: r["score"], reverse=True)
    result = {"prior_entropy_bits": entropy(prior.values()), "prior": prior,
              "hypotheses": {h: v.get("text", "") for h, v in hyps.items()},
              "literature": spec.get("literature", []), "ranked": ranked, "warnings": warnings}

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "next_experiments.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    hyp_text = result["hypotheses"]
    md = ["## 下一实验 / Next experiments", "",
          "**Hypotheses (prior):** " + "; ".join(f"{h} ({prior[h]:.2f}) {t}" for h, t in hyp_text.items()), ""]
    md += [card(i, r, hyp_text) for i, r in enumerate(ranked, start=1)]
    if warnings:
        md += ["", "> Input warnings: " + " · ".join(warnings)]
    (args.out / "next_experiments.md").write_text("\n".join(md), encoding="utf-8")

    for i, r in enumerate(ranked, start=1):
        print(f"{i}. {r['id']:<5} EIG={r['eig_bits']:.3f} bits  score={r['score']:.3f}  {r['title']}")
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

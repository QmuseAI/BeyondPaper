---
name: bp-next-experiment
description: Design and rank follow-up experiments for a paper (BeyondPaper stage 5) — state competing hypotheses, propose experiments with outcome likelihoods under each hypothesis, check the literature for follow-ups and contradictions, and rank by expected information gain per unit cost. Use after the consistency, ghost-variable and multiverse stages.
---

# bp-next-experiment — 下一实验设计

> **Where the scripts are.** Call `load_skill("bp-next-experiment")` first; SciencePal then places this skill at
> `/workspace/skills/bp-next-experiment/`. Set `S=/workspace/skills/bp-next-experiment/scripts` (all commands below use `$S`).


## 1. Competing hypotheses (2–4)
Write explanations that are all compatible with the evidence so far, e.g.
- H1: the method's advantage is real and transfers to new chemistries;
- H2: the advantage comes from leakage between related samples (random split);
- H3: the advantage depends on one dataset version.
Give each a prior (they must sum to 1). Base priors on stage 2–4 results and say why.

## 2. Literature check
Search (Survey agent or web) for: later papers citing this one, replications, failed
replications, and results that contradict a hypothesis. Record each source with a DOI or URL
in `next_experiments.yaml → literature`, with one line on which hypothesis it supports.
Adjust priors only with a stated reason.

## 3. Candidate experiments (3–6)
For each experiment give: hypothesis tested, variable changed, controls, minimal sample size
(with the rationale), cost (person-days or relative units), feasibility (0–1), novelty
(0–1, 1 = no one has done it), and a table of **outcome likelihoods p(outcome | H)**.
Each hypothesis's likelihoods over the outcomes must sum to 1. The likelihoods are the
heart of the ranking: justify the big ones in `rationale`.

Write `bp_out/next_experiments.yaml` (template: `$S/next_experiments.example.yaml`), then:

```bash
python $S/rank_experiments.py bp_out/next_experiments.yaml --out bp_out
```

It computes expected information gain (bits), the posterior after each outcome (“what each
result would mean”), and the score `EIG × (1 + w_novelty·novelty) × feasibility / cost`.
Outputs: `next_experiments.json`, `next_experiments.md` (experiment cards).

## Rules
- An experiment whose outcomes are equally likely under every hypothesis has EIG = 0;
  drop it however interesting it sounds.
- Prefer experiments that can be run with the released data and code (they are cheap and
  can be executed in the sandbox right away). If one can, run it and report the result.

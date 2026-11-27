---
name: bp-pitfall-atlas
description: Cross-paper atlas of verified failure mechanisms (BeyondPaper 前车之鉴) — before checking a paper, list the mechanisms that have actually gone wrong in published papers with similar methods and the rules the auditor learned from its own false alarms; after the run, link each red/yellow finding to verified precedents in other papers and export new cases for human curation. Use after the claim ledger (prior) and after build_passport.py / from_run.py (match) in every BeyondPaper run.
---

# bp-pitfall-atlas — 前车之鉴

> **Where the scripts are.** Call `load_skill("bp-pitfall-atlas")` first; SciencePal then places this skill at
> `/workspace/skills/bp-pitfall-atlas/`. Set `S=/workspace/skills/bp-pitfall-atlas/scripts`.

One audit says what went wrong in one paper. The atlas keeps every human-verified problem as a case of a
**mechanism** — test data used for selection, a variable left over from the previous loop, a 90% interval
reported as 95%, a copied CI — so the same mechanism is looked for in the next paper, in any field.
It also keeps the auditor's own verified mistakes (kind `auditor`): they are rules for every run.

Atlas files (`atlas/`): `mechanisms.json` (taxonomy, finding types, trigger patterns, detector, question for
the authors), `papers.json`, `cases.jsonl` (one verified case per line, with location and what the paper says
vs what the materials show).

## 1. Prior — after the claim ledger, before stage 2
```bash
python $S/atlas.py prior bp_out --work bp_work
```
Matches the paper text, the ledger and the code against each mechanism's triggers and writes
`bp_out/atlas_prior.md` / `.json`: the best-fitting mechanisms with their verified precedents, the detector
to run and the question to ask, plus the auditor rules that apply to these materials (e.g. R code → keep R's
`NA` as missing in any port; totals of table cells → write every cell and the sum).

- Read `atlas_prior.md` once. In stages 2–3, check every listed mechanism with its detector and say in
  `ghost_checklist.json` (or a finding) whether it applies; a mechanism that does not apply needs no finding.
- The auditor rules apply to the whole run.
- A match is a reason to look, not evidence. Never write a finding because a mechanism was listed.
- Cases from the paper under audit are left out (same DOI as `ledger.json → paper.doi`).

## 2. Match — after `build_passport.py` and `from_run.py`
```bash
python $S/atlas.py match bp_out
```
For each red/yellow finding: its mechanism, verified precedents in other papers and the fields they come from
→ `bp_out/precedents.md` / `.json`. Hand-written number findings from our own recount that resemble an
auditor mistake are listed under "需要先排除审计方自己的错": put those values through the reproduction gate
before they count. New candidate cases → `bp_out/atlas_candidates.jsonl` (status `candidate`).
Mention `precedents.md` in the final summary.

## 3. Curate — human step, outside the run
```bash
python $S/atlas.py --atlas my_atlas curate bp_out/atlas_candidates.jsonl --paper-key B12 \
    --verdict F3=confirmed,F5=rejected:port_semantics --note "why F5 was wrong" \
    --cite "Author et al. 2025, Journal" --doi 10.x/y --field "..." --code R
```
Only a person who has checked the paper and the materials confirms or rejects a candidate. Confirmed → paper
case; rejected → auditor case under the given auditor mechanism. `--atlas DIR` adds a writable atlas folder
that is searched before the built-in one.

## 4. Overview
```bash
python $S/atlas.py stats --md
```
Mechanism × paper table, and which mechanisms recur across fields.

## Rules
- Only `status: verified` cases are shown as precedents. The agent never writes `verified` itself.
- Precedents show that a mechanism is real and worth checking; they never raise a finding's severity.
- Neutral wording, as everywhere in BeyondPaper: “潜在不一致 / 需核实”.

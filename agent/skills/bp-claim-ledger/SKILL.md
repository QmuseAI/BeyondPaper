---
name: bp-claim-ledger
description: Build a claim → evidence → artifact ledger for a research paper (BeyondPaper stage 1). Use when dissecting a paper's conclusions, tracing which figure, table, data file and code supports each claim, or listing reported numbers and method parameters for later checks.
---

# bp-claim-ledger — 结论—证据账本

> **Where the scripts are.** Call `load_skill("bp-claim-ledger")` first; SciencePal then places this skill at
> `/workspace/skills/bp-claim-ledger/`. Set `S=/workspace/skills/bp-claim-ledger/scripts` (all commands below use `$S`).


## When to use
At the start of every BeyondPaper run, after the paper text is in `bp_work/paper.md`.

## Where am I?
At the start of every turn (and after the user says 继续), run
`python $S/bp_progress.py bp_out` — it lists which stages already have their files and prints the
NEXT stage with its skill and command. Continue from there.

## Finding the paper (v1.11.1)

When only a capsule or a citation is given, run once:
```bash
python /workspace/skills/bp-claim-ledger/scripts/find_paper.py --title "<title>" [--author Surname] [--doi 10.x/y]
```
A PDF of the paper supplied by the user (workspace or project files) is listed first and is the paper; then no search
is needed. Otherwise it queries Europe PMC, OpenAlex and Crossref one time, adds OSF preprint download links
(MetaArXiv/PsyArXiv DOIs) and caches `bp_work/paper_search.json`; a second call prints the cache. Do not query these
APIs with curl. No full text: one web search for "<title> <first author> pdf", at most 3 results opened. Still nothing:
use the capsule README / metadata / stored outputs as the paper, mark paper-level claims grey, and write that to
`bp_out/assumptions.md`.

## Steps
0. **Prepare in one call** (saves dozens of tool calls; do this before reading anything):

   ```bash
   python $S/prep_paper.py bp_work/paper.md --materials bp_work --out bp_work/prep && cat bp_work/prep/index.md
   ```

   It splits the paper into `bp_work/prep/sections/*.md` (≤ 8,000 characters each), lists every
   number with its context in `numbers.csv`, parameter sentences from the Methods in
   `methods_params.csv`, every material file in `inventory.csv`, and the authors' stored outputs
   (results/, logs, knitted reports, notebooks with outputs) in `stored_outputs.csv`. Read the
   section files you need once each; do not page through `paper.md` with repeated `sed`/`head` calls.
   Jupyter notebooks are flattened to `bp_work/prep/notebooks/*.md` (code cells numbered from 1,
   each with its output cut to 25 lines plus the last lines, which usually hold the final scores).
   Read those files; never `cat`, `sed` or `json.load`-and-print a raw `.ipynb` — one notebook can
   carry megabytes of printed output and stall the run.
   Take reported values from `numbers.csv`, parameters from `methods_params.csv`, artifact paths from
   `inventory.csv`, and keep `stored_outputs.csv` for the reproduction gate (`archived` values).
1. Read the abstract, introduction (last paragraph), results and conclusion. List the
   **main claims** (at most ~10). A claim is a statement the paper wants the reader to
   believe, e.g. “Model A predicts band gaps more accurately than B”.
2. For each claim, list the **evidence**: experiment, figure, table, statistic, or citation,
   with its location (`Fig. 3b`, `Table 2`, `Sec. 4.2, p. 7`).
3. For each piece of evidence, list the **artifacts** behind it: data files, code files,
   notebooks, supplementary tables. Set `available: false` if the paper refers to an artifact
   that is not provided.
4. Record **reported_values**: every number that a claim depends on (metric values, sample
   sizes, concentrations, p-values), each with name, value, unit, location and claim id.
   Record the same quantity once per location where it appears — duplicates across abstract,
   text, tables and figures are exactly what stage 2 cross-checks. **Always** record every sample
   size (n, N, number of compounds / patients / cells) at every location it is stated, using one
   name such as `n compounds`: a mismatch between abstract and Methods is a common, easy-to-miss flag.
   Values read off a figure (not printed as numbers) get `"approx": true`.
5. Record **methods.parameters**: every analysis parameter stated in the Methods
   (learning rate, split ratio, random seed, thresholds, software versions), with location.
6. Note **conditions**: the scope under which the paper says the claim holds.
7. Write `bp_out/ledger.json` following `$S/ledger.schema.json`, then run:

```bash
python $S/validate_ledger.py bp_out/ledger.json --root bp_work --out bp_out
```

It checks structure and referential integrity, checks that artifact paths exist under
`--root`, writes `ledger_graph.mmd` and `ledger.findings.json`.

## Rules
- Use the paper's own wording for claims (short quote or close paraphrase) and cite location.
- “未报告” for anything the paper does not state. Never fill gaps from general knowledge.
- Do not judge correctness here; only map what supports what.

## Minimal example

```json
{
  "paper": {"title": "…", "doi": "10.xxxx/xxxx"},
  "claims": [{"id": "C1", "text": "GNN outperforms RF on formation energy", "type": "main",
              "location": "Abstract", "evidence": ["E1"], "conditions": "Materials Project subset"}],
  "evidence": [{"id": "E1", "kind": "table", "location": "Table 2",
                "description": "MAE of GNN vs RF on test split", "artifacts": ["A1", "A2"]}],
  "artifacts": [{"id": "A1", "kind": "data", "path": "data/test_results.csv", "available": true},
                {"id": "A2", "kind": "code", "path": "code/evaluate.py", "available": true}],
  "reported_values": [{"id": "V1", "name": "MAE GNN test", "value": 0.081, "unit": "eV/atom",
                       "location": "Table 2", "claim_ids": ["C1"]},
                      {"id": "V2", "name": "MAE GNN test", "value": 0.081, "unit": "eV/atom",
                       "location": "Abstract", "claim_ids": ["C1"]}],
  "methods": {"parameters": [{"name": "random seed", "value": "42", "location": "Sec. 3.1"}]}
}
```

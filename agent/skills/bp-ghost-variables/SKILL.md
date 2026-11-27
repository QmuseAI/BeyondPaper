---
name: bp-ghost-variables
description: Find unreported variables and hidden analysis errors that could make a replication fail (BeyondPaper stage 3) — assess a reporting checklist (seeds, versions, splits, exclusions, reagent lots, instruments, passage numbers ...), statically scan Python/R/notebook code (unseeded randomness, preprocessing before the split, test data used for fitting or model selection, loops reading a leftover variable from an earlier loop, one object saved under two names, random splits on grouped data, unpinned dependencies), and check the released data files (identical tables under different names, test rows inside training/discovery files). Use after the claim ledger exists.
---

# bp-ghost-variables — 幽灵变量

> **Where the scripts are.** Call `load_skill("bp-ghost-variables")` first; SciencePal then places this skill at
> `/workspace/skills/bp-ghost-variables/`. Set `S=/workspace/skills/bp-ghost-variables/scripts` (all commands below use `$S`).


The question is not “did the authors make a mistake” but **“if someone else repeats
this, which missing information is most likely to give a different result?”**

## 1. Static code scan and data checks

```bash
python $S/deep_checks.py bp_work --paper bp_work/paper.md --out bp_out   # v1.11, run first
python $S/leakage_scan.py bp_work --out bp_out
python $S/data_checks.py bp_work --out bp_out
```

`deep_checks.py` (v1.11) turns checks that hand-written review found in one run and missed in the next into
script findings (`deep.findings.json`) and writes `code_facts.json`:
- column trace for R/Rmd (`read.csv` → drops → `colnames` → index slices → formulas): data columns never modelled;
- post-baseline covariates (best response, toxicity, adverse events, progression) used as survival predictors;
- results an Rmd computes but never prints or writes, and which objects each knitted notebook stores
  (`archive_objects`: read it before saying which table a notebook holds);
- events/censored per level of every binary survival covariate (`surv_groups`: check separation claims with it);
- per-fold arrays typed into scripts vs summary values plotted for the same model elsewhere;
- training scripts that the entry point (`run`, `run.sh`, Makefile) never calls while figures plot their models;
- the same metric names computed with sklearn averaging in one script and a binary count in another;
- layer sizes in stored weights (TensorFlow checkpoint index parsed without TensorFlow, .h5, .npz, .pt) vs the
  architecture in the paper (red: the paper and the authors' own model file disagree);
- hyper-parameters in the code vs the values in the paper (signature defaults are skipped);
- confusion counts in stored output recomputed (`confusion`).

`leakage_scan.py` (Python, notebooks, R) flags:

- `seed_missing`, `leakage_risk` (scaler/PCA/feature selection/imputer fitted before the split),
  `grouped_split_risk` (grey: random split while the code mentions patients, subjects, compositions,
  scaffolds, batches ...; a keyword match only. Check the data for a group column with repeated values
  and, if there is one, write your own yellow `grouped_split_risk` with the counts),
  `exclusion_rule` (candidate multiverse forks), `dependency_unpinned`;
- `test_set_reuse`: something is fitted on test-named data (`perm.fit(Xtest, ytest)`), or a loop
  refits the model for each value of a setting (number of features, a hyperparameter) and scores
  every candidate on the test set. If the reported setting was read off that sweep, the reported
  test performance is optimistic;
- `stale_loop_variable`: a loop reads the per-iteration variable of an earlier loop
  (`datR <- d[[i]]` but `escalc(..., data = datS)`): the analysis silently uses leftover data;
- `duplicate_output`: one object is written to two different files, often with an unused sibling
  (`pred_score4external_test` assigned, `pred_score4test` saved twice).

`data_checks.py` reads the tables in the materials (csv/tsv/xlsx) and flags
`duplicate_output` (files whose names describe different data, e.g. *test* vs *external-test*,
with identical content) and `split_overlap` (a training/discovery/full-cohort file that contains
the rows of a test-named file). For a `split_overlap`, open the code and check which file the
feature selection or tuning reads; if it reads the file that contains the test rows, raise the
finding to red and say so in the evidence.
It also flags `sample_size` when a csv has no header line (its first line is numeric data) but the code
reads it with `read_csv` / `read.csv` and the default header setting: the first observation becomes the
column names and every count is one short. When you recount predictions against such a file, align the
labels the way the code does (rows 2…n), or your own recount will be off by one row.

Notebook locations read `file.ipynb code cell K line L` (code cells counted from 1); the
flattened notebooks are in `bp_work/prep/notebooks/`. Static analysis over-reports: open each
flagged line and downgrade to grey if it is a false alarm. A confirmed `test_set_reuse`,
`stale_loop_variable` or `duplicate_output` stays yellow unless a reported number depends on it
(then red, with that number's claim id).

## 2. Reporting checklist

Read `$S/checklist.yaml`. For every item whose `applies_to` matches the paper, decide
`reported | partial | not_reported | n/a`, cite the location, and write
`bp_out/ghost_checklist.json`:

```json
[{"id": "M3", "status": "partial", "location": "Sec. 2.4", "note": "80/20 split, no mention of grouping by composition"}]
```

Then convert to findings (deterministic mapping by impact):

```bash
python $S/checklist_to_findings.py $S/checklist.yaml bp_out/ghost_checklist.json --out bp_out
```

## 3. Hand-off to the multiverse stage
Collect items with a `multiverse_fork` suggestion that are `partial` or `not_reported`,
plus `exclusion_rule` / `seed_missing` / `grouped_split_risk` flags. These are the forks
for `bp-multiverse`. Prefer forks where several choices are defensible. A confirmed
`test_set_reuse` or `split_overlap` is the strongest fork: rerun the selection on training data
only and report the test score both ways.

## 4. Questions for the authors
For each high-impact `not_reported` item, write one concrete question
(e.g. “Were compositions shared between training and test sets?”). They appear in the
relay package.

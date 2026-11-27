---
name: bp-consistency-check
description: Check a paper's internal consistency (BeyondPaper stage 2) — recompute p-values from reported test statistics, check estimates against their confidence intervals and p-values, GRIM-test means, cross-check sample sizes and the same number across abstract/text/tables/figures with unit and order-of-magnitude checks, and compare code parameters with the Methods. Use after bp-claim-ledger has produced bp_out/ledger.json.
---

# bp-consistency-check — 科研侦探

> **Where the scripts are.** Call `load_skill("bp-consistency-check")` first; SciencePal then places this skill at
> `/workspace/skills/bp-consistency-check/`. Set `S=/workspace/skills/bp-consistency-check/scripts` (all commands below use `$S`).


## Scripted checks (always run when inputs exist)

```bash
# 0. Unit conversion needs pint; install it once if the import fails
python -c "import pint" 2>/dev/null || pip install -q pint
# 1. Reported test statistics vs p-values (t, F, chi2, r, z in APA-like format)
python $S/statcheck_lite.py bp_work/paper.md --out bp_out
# 2. GRIM test for means of integer-valued data (Likert items, counts)
#    write bp_out/grim_input.csv with columns: label,mean,n,items,location   (mean as printed, e.g. 3.48)
python $S/grim.py bp_out/grim_input.csv --out bp_out
# 3. Effect estimates vs their 95% CIs and p-values (SMD, OR, HR, ...; Altman-Bland check)
python $S/ci_check.py bp_work/paper.md --si bp_work/si --out bp_out   # also writes ci_methods.json
# 4. Same quantity reported in several places / re-computed from data
python $S/numbers_crosscheck.py bp_out/ledger.json --recomputed bp_out/recomputed.json --paper bp_work/paper.md --out bp_out
# 5. Sample sizes and other counts read straight from the text (percent vs fraction, subgroup > total,
#    "k times N" arithmetic); writes counts_table.csv with every count mention
python $S/counts_check.py bp_work/paper.md --out bp_out
# 6. Every analysis method named in the Methods (Egger, trim-and-fill, BH/FDR, bootstrap, k-fold CV, LASSO,
#    REML, mixed models, ...) must have a matching call in the released code (comments do not count)
python $S/methods_code_check.py bp_work/paper.md --code bp_work --out bp_out   # also writes gui_tools.json
# 7. (v1.11.7) Extra studies with no trace in the paper or materials ("validated in a prospective cohort of 214
#    participants", "an individual-participant-data re-analysis of 312 patients"), and Abstract numbers the body
#    never states while a body sentence on the same quantity gives another value (sensitivity 0.950 vs 0.905)
python $S/claims_scan.py bp_work/paper.md --materials bp_work --out bp_out
```

`claims_scan.py` findings are yellow and stay in the report whichever model runs the stages. Read each one
once and add what you find (where the data are, which cohort the body number belongs to) to `llm.findings.json`
as a separate finding only if it adds a fact; do not restate it.

`ci_check.py` also flags intervals whose lower limit is printed above the upper one, including
intervals that stand alone in table cells, e.g. `(7.11 to 6.86)`.
It also recomputes a two-group Cohen's d / Hedges' g from the printed means and SDs (pooled SD) and flags a
reported value that is off by more than 0.10 and 25 % (yellow; within-subject designs are skipped).
For each `phantom_experiment` from `methods_code_check.py`, open the code once to confirm (the method
may be implemented under another name); keep it if not found, otherwise downgrade it to green.
When the same estimate is printed with two different intervals (e.g. "36% [20,59]" in the Abstract,
"36%, CI [19,57]" in the Results), look in the code for two interval methods (Wilson, Clopper-Pearson,
Sison-Glaz, bootstrap ...). If the code uses both and the paper or SI names both, the finding is grey
(disclosed method difference), not red. `ci_check.py` lists the interval methods the paper and plain-text
supplements name in `ci_methods.json`; `build_passport.py` uses it to grey two nearby intervals automatically.
When the Methods say an analysis was done in a point-and-click or web tool (MetaboAnalyst, GraphPad Prism,
SPSS, SIMCA, RevMan, ImageJ ...), `methods_code_check.py` writes a grey `missing_artifact` instead of a
`phantom_experiment` and lists the tool in `gui_tools.json`. Do not report such analyses as phantom
experiments yourself; ask for the project file or the exported settings.
`methods_code_check.py` also compares the confidence level: "95% CI" in the paper but 5th/95th
percentile bounds in the code (and no 2.5/97.5 anywhere) is a yellow `param_mismatch`. Open the
line: if it computes the reported intervals, keep it and attach the claim ids of those intervals.

**Counts.** Open `bp_out/counts_table.csv` and, for every noun with more than one count (participants,
articles, trials, items, ...), decide whether each count describes a clearly different set (a subgroup,
a round, an exclusion step). When the same set is given two different sizes in different sections,
report a yellow `sample_size` finding quoting both. The script's grey `near_singleton` pointers are the
first places to look.

**Reproduction gate.** A number from our own re-run counts against the paper only after it has been
checked against the authors' own stored output. Before re-computing, look for stored outputs
(`results/`, `output/`, logs, knitted HTML/PDF, saved notebooks with outputs, CSV tables behind figures)
and, for every value you re-compute, record the stored value next to it:

```json
[{"name": "external test AUROC", "value": 0.9387, "unit": null, "source": "bp_work/code/run.py (our re-run)",
  "archived": 0.939, "archive_source": "code/results/test_metrics.csv"}]
```

- `value` is our re-run (`null` if the code could not be run but a stored output exists — e.g. no R in the sandbox);
- `archived` / `archive_source` are the authors' stored output and its file (omit only if none exists);
- `"substitute": true` marks values re-computed with a port to another language or package;
- `"archive_code": "code/manuscript.Rmd:486"` names the line of the authors' code that writes the paper's number.
  When the stored table has several versions of a column (`consensus`, `consensus.1`, `mean`, `mean.1`,
  `x_final`), the first column you find is often an intermediate one (Round 1); open the code that writes the
  paper's table, compare with the column it uses, and give that line here. Without it the gate keeps the
  conflict yellow.

`numbers_crosscheck.py` compares paper / stored output / re-run and writes `bp_out/repro_status.json`:

| Status | Meaning | Finding |
|---|---|---|
| `reproduced` | our re-run matches the paper | none (counted green) |
| `archive_ok` | not re-run; stored output matches the paper | none (counted green) |
| `port_gap` | stored output matches the paper, our re-run does not | grey `port_gap` — our problem, not the paper's |
| `archive_mismatch` | the authors' stored output differs from the paper | red `archive_mismatch` (yellow if <5%) |
| `not_reproduced` | re-run differs and no stored output was recorded | yellow `rerun_mismatch`: only our re-run disagrees |

Never turn a `port_gap` into a paper finding, and never write an LLM finding that restates a value
gap the gate has classified. A count you make from the authors' raw data is a re-run too: put it in
`recomputed.json`, not in `llm.findings.json`. `build_passport.py` caps hand-written number findings
that cite a file at yellow.

`recomputed.json` is a JSON array of these records. A record whose `name` matches no ledger entry is never compared with the paper; when `numbers_crosscheck.py` prints `WARNING: ... match no ledger name`, rename the records as it suggests and run it again. Use exactly the same `name` as the matching entry
in `ledger.json → reported_values`. If the code is not seeded, run it with 3–5 seeds and add
`"range": [min, max]`; a reported value inside the range counts as reproduced.

## Judgement checks (write to `bp_out/llm.findings.json`) — a stage of its own, never skipped

`bp_progress.py` lists these checks as stage 2 "judgement checks"; `final_summary.py` refuses to finish the run
until both files exist. Write them with `judgement.py`, one short command per fact, the moment the fact is checked
(v1.11.8: B02-v1117p verified all four facts in long heredocs, the platform cut the turn as repetitive output, and
nothing was on disk):

```bash
python $S/judgement.py init --out bp_out          # creates both files, lists the script candidates to review
python $S/judgement.py note param_mismatch problem "Methods p.5 'five hundred trees' vs run.ipynb:73 n_estimators=100" --out bp_out
python $S/judgement.py add --type param_mismatch --severity yellow --location "Methods p.5 vs code/run.ipynb:73" \
    --evidence "Methods: 'five hundred classifier trees'; code: RandomForestClassifier(n_estimators=100)" --out bp_out
python $S/judgement.py note abstract_vs_body ok "Abstract 426 / 284 / 142 = Methods p.3; AUROC 0.920 = Results p.6" --out bp_out
python $S/judgement.py status --out bp_out        # prints 'judgement stage: done' when all five keys are recorded
```

Each note carries a verdict, `ok` or `problem`. A key noted `problem` needs at least one finding added with
`add` (the type decides the key, or pass `--key KEY`); the stage is not done until it has one. A cohort, survey,
comparison sample or re-analysis with no data file or code behind it is a `problem` for `phantom_experiment`: add
it as a yellow `phantom_experiment` finding, do not only describe it in the note (B07-v1118p wrote "no data file or
code for the 30-article comparison sample exists" in the note and the finding was lost).

Budget: about 15 tool calls for this stage. Do not explore data here; a value that needs re-computing from the
authors' files goes into `recomputed.json` in stage 4, and the note says so. The files are:

- `bp_out/llm.findings.json` — a JSON list of the findings from the five checks below (`[]` if there are none);
- `bp_out/judgement_checks.json` — what you compared for each check, with values and locations:

```json
{"param_mismatch": "n_estimators: Methods p.5 'a hundred trees' vs run.ipynb:73 n_estimators=100 (agree); L1 0.01 vs alpha=0.005 (F)",
 "figure_data_mismatch": "Fig 3b AUROC 0.920 re-computed from pred-score-for-external-test.xlsx -> recomputed.json",
 "phantom_experiment": "every cohort / survey / re-analysis in Results and Discussion matched to a data file or code: ...",
 "abstract_vs_body": "Abstract numbers: 0.950 (body 0.905, F), 426 (Methods 426), ...",
 "claims_scan": "claims_scan.findings.json #0 phantom 214: no data file for a fourth centre (kept); #1 ..."}
```

Do this stage in the turn the scripted checks finish, before the deep checks; a run without it misses the
errors only a reader catches (B02-v1116p/p2: no llm.findings.json, 0/4 and 1/4 injected errors found).

1. **param_mismatch** — for each entry in `methods.parameters`, search the code
   (`grep -rn`) for the corresponding setting. Report a red finding when code and text
   disagree (e.g. Methods: 5-fold CV, code: `n_splits=10`), citing `file:line` and the
   Methods location. Report grey if the parameter cannot be found in the code. Do not explain a
   conflict away by assuming one value was "considered" or "an alternative" unless the paper says so
   in those words; report it and let the authors explain.
2. **figure_data_mismatch** — for figures or tables that have a data file, re-compute the
   plotted or tabulated values from the file and add them to `recomputed.json`. If the
   figure itself must be read, describe what you read and mark the finding yellow, not red.
3. **phantom_experiment** — an experiment, dataset or validation described in the text
   with no corresponding code, data, figure, table or supplementary item. Yellow. When the
   materials hold the data and code of the paper's other analyses but nothing for this one
   (a "comparison sample of 30 articles", an extra cohort, a validation study), write it as
   `phantom_experiment` yellow, not as a grey `unverifiable_claim`.
4. **Abstract vs body** — `claims_scan.py` lists Abstract numbers that the body never states; beyond those, read every number in the Abstract (counts of items, participants, studies,
   patients; percentages; thresholds) and find the same quantity in the Results, tables and Discussion.
   A count with no noun after it ("38 reached consensus") is not caught by `counts_check.py`; compare it
   by hand. A threshold or definition in the Methods ("consensus = at least 70 %") is compared with the
   constant in the code (`>= 0.75`) and with the other places the paper states it.

5. **Script candidates** — read every `claims_scan.findings.json` entry and record in `judgement_checks.json` what
   you found for it (data file found / not found, which cohort the body number refers to).

## Rules
- Severity: red only when two concrete sources conflict; yellow when the conflict depends
  on an assumption (e.g. GRIM assumes integer data and no missing responses); grey when it
  cannot be checked.
- Quote both conflicting values and both locations in `evidence`.
- Never “correct” the paper; only report.

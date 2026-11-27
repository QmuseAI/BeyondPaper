---
name: bp-null-result
description: Export what a paper did not write down (BeyondPaper 失败路径 / 负结果卡片) — from a finished BeyondPaper run, collect the analysis choices under which the conclusion fails, the numbers that could not be reproduced, the unreported conditions and the open experiments into failure_paths.md/json; also record a lab's own unpublished negative result as a checkable card, and search these before someone repeats a dead end. Use at the end of a BeyondPaper run, when a user wants to record an attempt that did not beat its baselines, or asks whether an approach has already been tried.
---

# bp-null-result — 论文没写下来的东西

> **Where the scripts are.** Call `load_skill("bp-null-result")` first; SciencePal then places this skill at
> `/workspace/skills/bp-null-result/`. Set `S=/workspace/skills/bp-null-result/scripts` (all commands below use `$S`).

A paper keeps the analysis route that worked. The routes that failed, the numbers nobody could
reproduce and the conditions that were never reported stay out of it, and the next group walks
them again. This skill keeps them, with evidence, in a form others can search.

## A. From a BeyondPaper run (default; after `build_passport.py`)
```bash
python $S/from_run.py bp_out
```
Collects from the run, without new judgement:

1. **Failure paths** — single analysis changes that flip the main conclusion (`multiverse_summary.json`),
   values that failed the reproduction gate (`repro_status.json`), conclusions marked fragile.
2. **Hidden conditions** — unreported conditions and leakage risks rated red/yellow (ghost-variable stage).
3. **Open questions** — the top-ranked next experiments (`next_experiments.json`).

Writes `bp_out/failure_paths.md` (Chinese, every row points to a run file) and `bp_out/failure_paths.json`.
Mention `failure_paths.md` in the final summary. Do not add rows by hand: if something belongs there, it
must first exist in the run outputs.

## B. A lab's own unpublished negative result
For a method that did not beat its baselines, a failed replication, or an abandoned analysis that
never became a paper.

1. Copy code, results and notes into `bp_work/null/`. Every number must come from a results file, a log,
   or an explicit statement by the user (mark it `"source": "user statement"`).
2. Results table `bp_work/null/results.csv`, one row per run or cell: `condition`, `method`, `value`,
   optional `run`, and any other column as a condition axis (e.g. `cohort`, `n_samples`, `split`).
3. Card `bp_work/null/card.json` from `$S/card_template.json`. Required: `title`, `question`,
   `hypothesis`, `method`, `baselines`, `metric` (`name`, `direction`: `lower`/`higher`), `results.table`,
   `verdict.label` (`method_loses`, `no_difference`, `method_wins_only_when`, `inconclusive`), `why_stopped`.
   Strongly recommended: `conditions_not_tested` — where the method might still work.
4. Build:
   ```bash
   python $S/build_card.py bp_work/null/card.json --out bp_out
   ```
   It recomputes per condition the method against its best baseline (mean, spread, absolute and relative
   gap, win / tie / loss), checks the stated verdict against the data (red if unsupported), hashes the
   artifacts, and writes `null_result_card.md`, `null_summary.json`, `null.findings.json`, `card_index.json`,
   `null_gap.png`. Fix the card wording if the verdict is red; never change numbers.

## C. Search before trying
```bash
python $S/search_cards.py <folders with failure_paths.json / card_index.json> "random split leakage AUROC"
```
Ranks failure paths and cards from many runs by overlap with the query. Use it when a user asks whether
an analysis or method has already been tried, and cite the path id and its source file.

## Rules
- Describe what was tested, not whether an idea is good: "the AUROC claim does not hold when features are
  selected on training samples only", not "the biomarker panel does not work".
- Neutral wording as in the rest of BeyondPaper: potential inconsistency, needs verification.
- A null result against a weak baseline is uninformative; name baselines with version and settings.

---
name: bp-relay-package
description: Assemble the BeyondPaper research relay package (stages 6a–6 and the final message) — check every hand-written red/yellow flag with an executed command (verify_queue.py), merge all *.findings.json files, assign evidence traffic lights per claim, write passport.md and findings.json, and write the closing message from the files (final_summary.py). Use at the end of every BeyondPaper run.
---

# bp-relay-package — 研究接力包

> **Where the scripts are.** Call `load_skill("bp-relay-package")` first; SciencePal then places this skill at
> `/workspace/skills/bp-relay-package/`. Set `S=/workspace/skills/bp-relay-package/scripts` (all commands below use `$S`).


```bash
python $S/build_passport.py bp_out
```

Reads everything in `bp_out/` (ledger, all `*.findings.json`, `repro_status.json`, multiverse
summary and plots, next-experiment cards, environment) and writes:

- `bp_out/findings.json` — merged findings with ids `F1…`, counts, the reproduction level, and one
  traffic light per claim;
- `bp_out/passport.md` — the relay package:
  1. 可复现性护照 Reproducibility passport (reproduction level `full / partial / none / not attempted`
     and the reproduction-gate table: paper vs authors' stored output vs our re-run)
  2. 结论—证据账本 Claim–evidence ledger (with traffic lights)
  3. 数字、单位和图表异常 Anomalies
  4. 幽灵变量清单 Ghost variables
  5. 压力测试与失败条件地图 Stress test and failure map
  6. 最值得做的后续实验 Next experiments
  7. 给原作者的问题 Questions for the authors

Before it merges, `build_passport.py` re-checks severities and records every change in
`bp_out/severity_changes.json` (summarised at the top of `passport.md`):

- a red number finding written by the model (anything in `llm.findings.json`, module `llm`, or a source that
  mentions `llm`) that cites the authors' files and has not passed the reproduction gate → yellow;
  it goes back to red when verify.json upholds it with a check output that prints the authors' stored value
  (a reading of their stored output, not a recount and not our port) — so print the stored cell in that check;
- a hand-written red whose evidence shows only one value, or relies on a total without writing the cells and
  the sum → yellow;
- two overlapping intervals that differ by less than a fifth of their width → grey when the paper or
  `bp_work/si/` names interval methods (`ci_methods.json` from `ci_check.py`), otherwise red → yellow;
- a `phantom_experiment` about an analysis the Methods place in a point-and-click or web tool
  (`gui_tools.json` from `methods_code_check.py`) → grey;
- the same conflict written twice (same numbers in the evidence) → merged into one finding.

The marker block is also written to `bp_out/BP_FINDINGS_BLOCK.txt`, so the result survives a run that ends
without its closing message.

## One-page brief for a reviewer, editor or investor

```bash
python $S/build_brief.py bp_out --audience investor    # or reviewer / editor
```

Run it after `from_run.py` and `atlas.py match`. It writes `bp_out/brief_<audience>.md`: overall light and
reproduction level, the main claims with their lights, the conditions under which they fail, the materials
to ask for, the five questions for the authors, and the next checks. It only reorganises the run's files.

After running it, replace the `<!-- NARRATIVE -->` line at the top of `passport.md` with a
summary of at most 200 words in the user's language, then end the conversation with the output of
`final_summary.py bp_out` (five lines plus one line per red/yellow finding). The JSON marker block stays in
`bp_out/BP_FINDINGS_BLOCK.txt`; do not paste it into the message.

## 6a. Verification before the passport (v1.11)

```bash
python $S/archive_text.py bp_work           # v1.11.16: stored HTML / notebooks / PDFs / logs as grep-able text
                                            #   in bp_work/checks/archive_txt/ (one line per paragraph or table row)
python $S/verify_queue.py bp_out --init     # lists hand-written red/yellow flags + atlas mechanisms in bp_out/verify.json
# per flag: one command that tests it against the authors' files
<command> > bp_work/checks/V1.out 2>&1      # e.g. grep -n '1\.425' bp_work/checks/archive_txt/*.txt
# fill V1 in verify.json: check_cmd, check_output, verdict upheld|refuted|weakened, note, a/b for comparisons
python $S/verify_queue.py bp_out --check    # must print "complete"
```

`build_passport.py` keeps a hand-written flag red/yellow only when its verify.json entry has an existing check output
and the verdict `upheld`; a refuted flag stays in the passport as grey with the note. For a comparison, `a` and `b`
carry `value, what, dataset, metric, endpoint, population, split, table, file`; any field that differs makes the flag
grey (`different_quantity_declared`). The script also applies rules learnt from human-judged false flags
(severity_changes.json names each): different datasets or metrics compared, a value from our own port, a detail the
authors' files already document, an events-per-variable argument on univariate models, a Welch df that matches, a
confusion recount, a stored total larger than the claimed subset, the stored table being another endpoint
(code_facts.json archive_objects), a covariate count that the code trace contradicts, a separation claim the data
contradicts, a "missing" column that exists, the agent's own port file, unpinned dependencies while a Dockerfile pins
them, a multiverse fork that swaps the endpoint, row-index pseudo-groups, checklist items with no place in the code; v1.11.16: an omitted argument whose default is
what the paper states (`known_default`: metafor rma REML, lmer REML, t.test Welch, coxph Efron), a recount whose own
evidence shows the paper's number is another grouping of the same categories (`category_definition`). Cell-by-cell
archive_mismatch flags of one row are merged into one flag, and a model with mismatching cells in three or more rows
becomes one flag for the whole model (`same_row_cells`, `same_model_cells`).

## Final message

```bash
# first run the progress check of skill bp-claim-ledger (bp_progress.py bp_out)
python $S/final_summary.py bp_out           # paste the output unchanged as the last message
# v1.11.7: final_summary.py refuses to finish (exit 1, "运行未完成") while bp_out/llm.findings.json or
# bp_out/judgement_checks.json is missing, or llm.findings.json is newer than findings.json. Do the judgement checks
# of bp-consistency-check, re-run verify_queue.py --init / --check and build_passport.py, then final_summary.py again.
```
The summary is built from bp_out/ only. Never describe the state of the run from memory.

# Problems BeyondPaper found in published papers

While building and testing BeyondPaper (benchmark v1, 21–22 October 2026), the agent was run on
five published open-access papers whose authors released their code and data. **Besides the
problems we had planted ourselves, the agent found real inconsistencies in four of the five
papers as published.** We checked every one of them by hand against the paper and the authors'
own released files. This page lists them with exact locations so anyone can check them in a few
minutes.

How to read this list:

- Each item is an inconsistency between what the paper says and what the authors' own code,
  data or stored outputs say, or within the paper itself. Some may have an explanation we do not
  know; none of them, as far as we can tell, changes the main conclusion of its paper unless
  stated.
- "Found by" names the benchmark run (B01–B10, see `BENCHMARK_V1.md`): every paper was run twice,
  once as published ("clean") and once with planted problems ("injected"). Items marked
  **found by us** were noticed while we were checking the agent's flags; they were not flagged by
  the agent and are not counted in its results.
- Text versions: we used the full text from Europe PMC (P01, P02, P04) and the PsyArXiv preprint
  (P05). A later journal version or correction may already have fixed an item.
- Code and data: the Code Ocean capsules mirrored by CORE-Bench (train split).

---

## P01 — Chen et al., *Nature Communications* 15, 1657 (2024)

Metabolomic machine-learning models for gastric cancer diagnosis and prognosis.
DOI [10.1038/s41467-024-46043-y](https://doi.org/10.1038/s41467-024-46043-y) · CC BY 4.0 · text: PMC10891053

| # | What the paper says | What the materials show | Found by |
|---|---|---|---|
| 1.1 | Methods, *Diagnostic prediction model*: "We set the coefficient of the L1 constraint as 0.01". | `code/GC-diagnosis-model/run.ipynb`, cell 4: `args=ArgsClass(k=10, alpha=0.005)`, passed to `Lasso(alpha=self.args.alpha, normalize=True)` in `model.py` line 44. | B02, B06 (both runs) |
| 1.2 | Cohort 1 (n = 426) was split into a discovery set (n = 284) and a test set (n = 142), and LASSO was run "on the discovery dataset". | Cell 4 runs the 10,000-round feature selection on `discovery_set.xlsx`, which has **426 rows and contains all 142 test samples** (checked by `sample_id`). The ten features used by the final model were therefore chosen with data that include the test set. Our re-analysis (selection repeated on the 284 training samples only, 1,000 rounds): test-set AUROC 0.968 → 0.965, external test AUROC 0.939 → 0.891. | B02 |
| 1.3 | The prognostic model uses the top 28 metabolites by permutation importance; test-set AUROC 0.832, c-index 0.83. | `code/GC-prognosis-model/run.ipynb`: permutation importance is fitted on the **test set** (cell 6, `perm.fit(Xtest, ytest)`), then the number of features is chosen by sweeping 1…N features and reading the **test-set** c-index (cell 8); cell 9: "the best model prediction performance is achieved when the top 28 features are selected". The test set is used to pick the model it then evaluates. | B02 (sweep); cell 6 found by us |
| 1.4 | Results report the external test set (Cohort 2) separately. | `results/GC-diagnosis-model/GC-diagnosis-model-pred-score-for-external-test.xlsx` is identical to the test-set file (142 rows; `DataFrame.equals` is True; the external set has 95 samples). Cause: the last line of cell 13 writes `pred_score4test` instead of `pred_score4external_test`. The paper's numbers are not affected, but the released external-test predictions are the wrong file. | B06 |
| 1.5 | AUROCs are reported with "95% CI" (e.g. test set 0.967, 95% CI 0.944–0.987). | The `CI()` function in both notebooks (diagnosis cell 7, prognosis cell 15) takes the 5th and 95th percentiles of 1,000 bootstrap AUROCs; its own comment says "Computing the lower and upper bound of the 90% confidence interval". The plots label this interval "95%CI". | **found by us** |
| 1.6 | Sensitivities of the clinical tumour markers CA19-9, CA72-4, CEA (0.217, 0.317, 0.165). | Not reproducible from `Biomarker_clinical.xlsx` under the obvious definitions (we get 0.203, 0.270, 0.166 with non-missing n). *Unverified*: the cut-offs used may differ. | B06 |

## P02 — Stefanetti et al., *Wellcome Open Research* 5:84 (2020), version 1

Exercise in neuromuscular disorders: systematic review and meta-analyses.
DOI [10.12688/wellcomeopenres.15825.1](https://doi.org/10.12688/wellcomeopenres.15825.1) · CC BY 4.0 · text: PMC7331112

| # | What the paper says | What the materials show | Found by |
|---|---|---|---|
| 2.1 | Results: CK "did not change before versus after exercise training (SMD -0.06 [95% CI -0.23, -0.11]; p=0.46)". | The estimate −0.06 lies outside its own interval, and an interval that excludes 0 contradicts p = 0.46. Table 2 gives −0.23, **0.11**: a sign typo in the text. | B04; also `ci_check.py` |
| 2.2 | Results: "STS (seconds) (SMD -0.67 [95% CI -1.31, 0.03]; p=0.04)". | Table 2 gives **−0.50** for the same outcome. In both places the CI (−1.31, 0.03) includes 0 while p = 0.04. | B04 (value); `ci_check.py` (CI vs p) |
| 2.3 | Abstract: "I²=58·5%" for STS (seconds). | Table 2 and Results: 58.05%. | B04 |
| 2.4 | Table 3, 6MWT row: CI "(7.11 to 6.86)". | Lower bound above upper bound. | B04 |
| 2.5 | Funnel plots with trim and fill are reported for the exercise-vs-usual-care comparisons. | `code/funnel.R` line 17, inside the usual-care loop: `escalc(..., data=datS)` uses the **before–after** data frame left over from the previous loop instead of `datR`. The usual-care funnel plots are drawn from the wrong data. | B09 |
| 2.6 | Methods: publication bias "estimated using Egger's regression intercept". | The released code calls `funnel()` and `trimfill()` but never `regtest()` or any other Egger test; the only mention is a comment on line 1 of `funnel.R`. | B09 |
| 2.7 | Table 3, a second row: CI "(−15.19 to −32.4)". | Lower bound above upper bound, like 2.4. | B09-v1116p, B04-v11115p, B09-v11115p (v1.11) |

## P04 — Thibault et al., Delphi study on research-methods training in UK undergraduate psychology, *AMPPS* (2024)

DOI [10.1177/25152459231213808](https://doi.org/10.1177/25152459231213808) · CC BY 4.0 · text: PMC7617501

| # | What the paper says | What the materials show | Found by |
|---|---|---|---|
| 4.1 | Sensitivity analyses: "the **median** absolute change in the percentage of participants that rated an item as essential was 3.8%, and the **median** absolute difference in participants' rating of an item was 0.15" (similar wording for the instructor–academic and sample comparisons). | `code/manuscript.Rmd` lines 699–711 (the sensitivity table) compute `mean(abs(...))` for all three comparisons. From `results/sensitivity/round1_round2.csv`: mean 3.78% and 0.150 (the reported numbers), median 3.0% and 0.13. The statistic is a mean labelled as a median. | B05, B10 (both runs) |
| 4.2 | "Of the **47** items included in both Round 1 and Round 2 ...". | By the paper's own counts there are 45: 78 items − 7 added in Round 2 = 71 in Round 1; 71 − 26 that reached consensus = 45; 52 Round-2 items − 7 new = 45. `results/sensitivity/round1_round2.csv` has 45 rows. | B05, B10 (both runs) |

## P05 — Campbell et al., independent replication of Murray et al. (2002) Study 3 (2017)

Self-esteem, relationship threat and dependency regulation. Text: PsyArXiv preprint (submitted
version), DOI [10.31234/osf.io/5suda](https://doi.org/10.31234/osf.io/5suda).

| # | What the paper says | What the materials show | Found by |
|---|---|---|---|
| 5.1 | Table 3, State Self-Esteem model: Self-esteem b = .571, SE = .081, 95% CI (−.544, .032). | The CI is impossible for b = .571 with SE = .081 (it should be about .41 to .73), and it is the exact CI of the Self-esteem row of the Manipulation Check model in Table 2. Copied from the other table. | B03, B08 (both runs) |
| 5.2 | Results: experimental condition *M* = 4.83, *SD* = 1.25; control *M* = 4.24, *SD* = 1.42. | The authors' own knitted output (`results/main.html`, summary table) gives Control SD 1.25 and Experimental SD 1.42: the SDs are swapped. The swapped values were also typed into the effect-size call `mes(4.83, 4.24, 1.25, 1.42, 100, 102)`; with near-equal groups d = .44 barely changes. | B03, B08 (both runs) |
| 5.3 | Table 3 intercept 5.468. | `results/main.html`: 5.48639. | B08 |
| 5.5 | Methods, Participants: mean age *M* = 21.00 (*SD* = 4.44). | `results/main.html`, `stat.desc` of Age: mean 21.759, median 21.0, SD 4.438. The reported "mean" is the median. | B08-v11115p (v1.11) |
| 5.4 | Table 2, Inferred Number of Negative Items: Condition b = 1.623, intercept 3.442; footnote "df = ... 196". | The model in `results/main.html` (`lm(P24NumNeg ~ cSelfEsteem * dCond, data = newdata3)`) has 151 residual df and Condition b = 1.42536, intercept 3.54802. Table 2 cannot be regenerated from the released analysis; it was probably fitted on a different subset. | B08 |

## P03 — Hardwicke et al., *Royal Society Open Science* 8, 201494 (2021)

DOI [10.1098/rsos.201494](https://doi.org/10.1098/rsos.201494) · CC BY 4.0 · text: PMC7890505

No real problems found. The agent flagged that the same proportion (9/25) is given two different
95% CIs ([20, 59] in the Abstract, [19, 57] in the Results); the code shows these come from two
different interval methods (Sison–Glaz multinomial and Wilson), both of which are named in the
Supplementary Material. Minor and disclosed.

---

## Summary

| Paper | Items | Found in both runs | Found in one run | Found by us | Unverified |
|---|---|---|---|---|---|
| P01 | 6 | 1 | 2 | 1 (+ part of 1.3) | 1 |
| P02 | 6 | 0 | 6 | 0 | 0 |
| P03 | 0 (1 minor) | – | – | – | – |
| P04 | 2 | 2 | 0 | 0 | 0 |
| P05 | 4 | 2 | 2 | 0 | 0 |
| **Total** | **18** | **5** | **10** | **1** | **1** |

16 verified items were found by the agent; 1 more (1.5) was found by us while checking its flags,
and 1 (1.6) remains unverified. Of the 16, only 5 were found in both runs of the same paper: a
single run misses a lot, so two or three runs should be combined.

The most consequential items are 1.2 and 1.3 (the test set was used for feature or model selection,
so the reported test performance is optimistic; our re-analysis puts the external AUROC of the
diagnostic model at 0.891 instead of 0.939 when selection uses the training data only) and 2.5
(funnel plots drawn from the wrong data). The others are reporting inconsistencies that a reader
could trip over but that leave the conclusions in place.

---

## Update: v1.11 runs (18–19 November 2026)

The agent was rerun on all five papers with versions v1.11.6 to v1.11.15 on DeepSeek V4 Pro. Every
item above except 1.6 (unverified) was found in at least one of these runs, including 1.5, which
benchmark v1 had missed. Two items are new: 2.7 and 5.5. With version v1.11.15:

| Run | Paper | Items found |
|---|---|---|
| B02-v11115p2 (injected) | P01 | 1.1, 1.2, 1.3, 1.4, 1.5 |
| B04-v11115p (injected) | P02 | 2.1–2.6, 2.7 |
| B09-v11115p (clean) | P02 | 2.1–2.6, 2.7 |
| B05-v11115p (clean) | P04 | 4.1, 4.2 |
| B10-v11115p3 (injected) | P04 | 4.1 |
| B03-v11115p2 (clean) | P05 | 5.1–5.4 |
| B08-v11115p (injected) | P05 | 5.1–5.4, 5.5 |

Per-flag adjudication of these runs is in `08_评测/results/` (`evaluation/results/` in the repository).

## Final results: v1.11.16 (19 November 2026)

One run per benchmark; most steps ran on DeepSeek V4 Pro (when SciencePal switched models mid-run we stopped the run, switched back to V4 Pro and had V4 Pro recheck what was written during the switch).

| Run | Paper | Items found |
|---|---|---|
| B02-v11116p (injected) | P01 | 1.1–1.5 |
| B06-v11116p (clean) | P01 | 1.1–1.5 |
| B04-v11116p2 (injected) | P02 | 2.1–2.6 |
| B09-v11116p (clean) | P02 | 2.1–2.6 |
| B10-v11116p2 (injected) | P04 | – |
| B05-v11116p (clean) | P04 | 4.1 |
| B08-v11116p2 (injected) | P05 | 5.1 |
| B03-v11116p3 (clean) | P05 | 5.1, 5.2 |

Run-cumulative: 26/34 known items; no red flag was a false positive.

## Reproducing the checks

All files named above are in the authors' public capsules (Code Ocean, mirrored by
[CORE-Bench](https://huggingface.co/datasets/siegelz/core-bench)). Our verification scripts and the
re-analysis for 1.2 are in `benchmark/verify/`. The per-flag adjudication of every benchmark run is in
`benchmark/results_v1/`.

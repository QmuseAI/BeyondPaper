# BeyondPaper（论文之后）

An agent that audits published computational papers by **executing** them: it re-runs the authors' code and data in a sandbox, reconciles every re-computed number with the authors' own stored output before using it against the paper, checks the paper for internal inconsistencies and methods–code mismatches, stress-tests the main conclusions over a grid of defensible analysis choices, exports what the paper did not write down, and turns the finished run into a tested "paper agent" that can answer what-if questions by re-running the analysis.

BeyondPaper runs as a custom agent on [SciencePal](https://sciencepal.ai) (HKAI-Sci). It was built for the SciencePal Global AI4Science Hackathon 2026.

**Team:** Edge-of-Chaos — Jiapei Zhuang (project design, presentation and submission; Independent) and Yaocheng Chen (project design, technical implementation and experimental validation; Universidade de São Paulo).

## Results at a glance (v1.11.16, final)

These results were obtained under normal running conditions, with most steps run by DeepSeek V4 Pro. SciencePal switches models automatically during a run; whenever it did, we stopped the run, switched back to V4 Pro, continued, and had V4 Pro recheck what had been written during the switch. One run per paper variant; the list of runs is in `evaluation/README.md`.

- Benchmark: 5 published papers with public code and data, each as the original and as a version with 4 injected errors (10 runs). Injected errors found: 20 of 20 (9 as red flags). Known real issues found: 26 of 34 (counted per run). Red/yellow flags confirmed on manual adjudication: 48 of 48; no false red flags.
- Blind papers (never seen during development, 5 papers, one run each): 28 confirmed issues; red/yellow flags confirmed 28 of 31; 7 red flags, 6 confirmed and 1 unclear, none false. The unclear red flag comes from the multiverse check on the stroke paper: the claim that the network predicts a door-to-needle delay over 45 minutes with accuracy above 60% and F1 above 0.64 held in only 40% of 60 universes, and changing the random seed (0, 2, 3, 4), setting the regularisation coefficient to the 0.02 stated in the paper, or setting the learning rate to 0.5 each flipped it. This is a robustness question, not a factual error that can be checked against the authors' stored output; whether it counts against the paper depends on whether the authors checked several seeds, so it was left unclear and passed to the authors as a question.
- A sixth blind paper (B13, DAPPER trial, *Clin. Cancer Res.* 2023) is behind a paywall and its full text could not be obtained. BeyondPaper wrote a plan to continue from the abstract, supplement and code and stated that it needed the full text; the runs stalled and the sandbox failed repeatedly, so B13 is not counted. Its saved runs stay in `evaluation/results/`; see `evaluation/B13_PAPER_ACCESS.md`.
- Over all v1.11 runs, the agent found each of the 19 hand-verified issues in the five benchmark papers at least once.

**Model stability.** BeyondPaper performs well when the underlying model is stable (DeepSeek V4 Pro throughout). When the platform switches models back and forth mid-run, results become unstable and each run differs a little. In v1.11.16, the two runs that were switched to V4 Flash (B08-v11116p, B10-v11116p) each raised one false red flag, while stable reruns of the same papers raised none; two runs of the same paper and version found different numbers of known issues (3 and 1 of 4).

## Repository layout

| Path | Contents |
|---|---|
| `agent/system_prompt.md` | System prompt of the SciencePal custom agent (v1.11.16) |
| `agent/skills/` | The nine skills (SKILL.md + scripts), version v1.11.16 |
| `agent/changelog/` | Change logs v1.10 – v1.11.16: what each version changed and which run triggered it |
| `evaluation/` | Scoring scripts, answer keys for real issues, per-run findings and manual adjudication (`results/<run>/adjudication.csv`), summary tables (`scores/`) |
| `docs/` | Project description, training-process report, list of issues found in published papers, slides |

## Pipeline

| Stage | Skill | What it does |
|---|---|---|
| 1 | `bp-claim-ledger` | Claim → evidence → artifact ledger; paper preparation; progress tracking |
| 2 | `bp-consistency-check` | p-value recomputation, estimate vs CI, GRIM, cross-checking repeated numbers, reproduction gate, methods vs code, Cohen's d from M/SD |
| 3 | `bp-ghost-variables` | Reporting checklist, data-file checks (train/test overlap, duplicated outputs), leakage scan, literal-value checks |
| 4 | `bp-multiverse` | Multiverse / specification-curve re-runs over defensible choices |
| 5 | `bp-next-experiment` | Competing hypotheses, follow-up experiments ranked by expected information gain |
| 6 | `bp-relay-package` | Executed verification of every hand-written flag, severity review, passport, one-page briefs, final summary |
| 7 | `bp-null-result` | Failure paths: what the paper did not write down |
| 8 | `bp-paper-agent` | Tested tools (`reproduce`, `whatif`, `claims`, `next`) locked to the recorded baseline |
| – | `bp-pitfall-atlas` | Cross-paper atlas of verified failure mechanisms, used as a prior before each audit |

## Reproducing the scores

```bash
cd evaluation
python score_runs.py                 # all runs → scores/by_run.csv, scores/summary.md
python score_runs.py --models pro    # DeepSeek V4 Pro runs only
python score_runs.py --out scores_final --only B01-v11116p2,B02-v11116p,B03-v11116p3,B04-v11116p2,B05-v11116p,B06-v11116p,B07-v11116p,B08-v11116p2,B09-v11116p,B10-v11116p2,B11-v11116p,B12-v11116p,B14-v11116p,B15-v11116pr,B16-v11116p   # final v1.11.16 runs
python tools/replay_v11116.py <path to build_passport.py>   # replay severity rules on all adjudicated runs
```

Answer keys for the injected-error versions are not included in this repository; the injected paper versions are not distributed.

## Replays (no login needed)

One replay per paper: the v1.11.16 run used in the final results. The platform switches models mid-run; the replays show the switches and our switch back to DeepSeek V4 Pro.

| Id | Paper | Run | Link |
|---|---|---|---|
| B01 | Hardwicke et al. 2021, original | B01-v11116p2 | https://sciencepal.ai/share/4c003b8c-af6c-490a-b8db-558ba8a1bfd1 |
| B02 | Chen et al. 2024, injected: 4/4 injected, 5/5 real issues | B02-v11116p | https://sciencepal.ai/share/7ff05d0e-4531-49bc-81fe-b25b13df13da |
| B03 | Campbell et al. 2017, original: 2/4 real issues | B03-v11116p3 | https://sciencepal.ai/share/6035959d-1001-4aef-bee2-0b6331eba71e |
| B04 | Stefanetti et al. 2020, injected: 4/4 injected, 6/6 real issues | B04-v11116p2 | https://sciencepal.ai/share/7257fb89-021d-45df-845e-220f610edc78 |
| B05 | Thibault et al. 2024, original: a "median" that is a mean | B05-v11116p | https://sciencepal.ai/share/b30a2f54-137a-4631-9ac3-d4c967853c0e |
| B06 | Chen et al. 2024, original: 5/5 real issues | B06-v11116p | https://sciencepal.ai/share/01267973-29c2-4ca8-a391-ed9047913754 |
| B07 | Hardwicke et al. 2021, injected: 4/4 injected | B07-v11116p | https://sciencepal.ai/share/ddf373f3-eac5-4626-9ad7-ed4259fcd254 |
| B08 | Campbell et al. 2017, injected: 4/4 injected | B08-v11116p2 | https://sciencepal.ai/share/180d8de1-d0ef-48c0-afb9-feca3476137e |
| B09 | Stefanetti et al. 2020, original: 6/6 real issues | B09-v11116p | https://sciencepal.ai/share/9d38b9ca-bbbb-48cb-bb5a-6b5979ce6953 |
| B10 | Thibault et al. 2024, injected: 4/4 injected | B10-v11116p2 | https://sciencepal.ai/share/19a872a3-e379-4ca4-b257-caf1db051ac9 |
| B11 | Blind: ethics-application study | B11-v11116p | https://sciencepal.ai/share/9310ee1e-1af7-4a12-b9f6-70274c5f6aae |
| B12 | Blind: EEG seizure detection | B12-v11116p | https://sciencepal.ai/share/11fabef6-9114-48aa-91f6-3ea442c47d91 |
| B13 | Blind: DAPPER trial (paywalled; the agent wrote a plan and asked for the full text; not counted) | B13-v11116r2 | https://sciencepal.ai/share/41bb2166-cddb-4a9d-9b1f-345026584c2d |
| B14 | Blind: stroke door-to-needle neural network | B14-v11116p | https://sciencepal.ai/share/967402fa-4b64-446e-a92c-1704bb8eafe6 |
| B15 | Blind: *Nat. Commun.* 2025 (no author code) | B15-v11116pr | https://sciencepal.ai/share/ae6a2939-1ebe-466d-8cd7-ac76faa0df84 |
| B16 | Blind: EnviroPiNet, *Sci. Rep.* 2025 | B16-v11116p | https://sciencepal.ai/share/62d3c190-05fa-4523-b2b8-7339c19e29e9 |

Model-switch comparisons (same version, same paper):

| Run switched to V4 Flash | Stable rerun | Difference |
|---|---|---|
| B08-v11116p https://sciencepal.ai/share/e2db557b-306a-4e24-92b4-7971be4a42d2 | B08-v11116p2 https://sciencepal.ai/share/180d8de1-d0ef-48c0-afb9-feca3476137e | 1 false red flag vs none; known issues 3/4 vs 1/4 |
| B10-v11116p https://sciencepal.ai/share/9f17d67b-b461-4c3c-8804-3e7de16b1e24 | B10-v11116p2 https://sciencepal.ai/share/19a872a3-e379-4ca4-b257-caf1db051ac9 | 1 false red flag vs none |

Paper-agent follow-up on Chen et al. 2024 (end of the B06 replay, v1.11.16): `reproduce` matched all 9 recorded outputs; `whatif alpha=0.02` re-ran the analysis and moved external sensitivity from 0.683 to 0.921; `whatif k=15` barely changed it; `next` ranked nested re-selection of the feature count first. https://sciencepal.ai/share/01267973-29c2-4ca8-a391-ed9047913754

## Using the skills outside SciencePal

Each folder under `agent/skills/` is a self-contained skill (SKILL.md + Python scripts; numpy, scipy, pandas, scikit-learn, matplotlib, PyYAML, pint). Zip a folder (`zip -r bp-xxx.zip SKILL.md scripts`) to import it into SciencePal or another agent platform that loads SKILL.md-style skills, and use `agent/system_prompt.md` as the agent's instructions.

## Citation

See `CITATION.cff`. Please cite the Zenodo record once it has a DOI.

## License

Code: MIT (see `LICENSE`). Documentation, results and figures: CC BY 4.0. The audited papers belong to their authors and are cited, not redistributed.

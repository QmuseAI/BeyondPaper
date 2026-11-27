---
name: bp-multiverse
description: Stress-test a paper's conclusion by re-running its analysis over a grid of defensible choices (BeyondPaper stage 4, multiverse / specification-curve analysis) — seeds, data splits, outlier rules, statistical tests, preprocessing scope, hyperparameters — and report how often the claim holds and which single changes break it. Use when the paper's data and code can be run in the sandbox.
---

# bp-multiverse — 压力测试

> **Where the scripts are.** Call `load_skill("bp-multiverse")` first; SciencePal then places this skill at
> `/workspace/skills/bp-multiverse/`. Set `S=/workspace/skills/bp-multiverse/scripts` (all commands below use `$S`).


## 1. Reproduce the baseline first
Run the paper's own code as published. Record the result in `bp_out/recomputed.json` together with
the authors' stored output for the same quantity (`archived`, `archive_source`; see the reproduction
gate in `bp-consistency-check`), then re-run `numbers_crosscheck.py` so the gate classifies it. If the
code does not run, record a `rerun_failed` finding with the error and what you tried, still record any
stored outputs (`"value": null`), and stop this stage.

Only build the multiverse on a baseline that passed the gate (`reproduced`, or `port_gap` explained
in `bp_out/assumptions.md`). If the baseline is `not_reproduced`, say so in the multiverse summary:
robustness is then measured around our re-run, not around the paper's number.

## 2. Write an adapter
Create `bp_work/adapter.py` exposing one function:

```python
def run(config: dict) -> dict:
    """Run the paper's analysis once with the choices in `config`; return numeric outputs."""
```

Wrap the paper's code; do not rewrite its method. Each fork in `config` must map to one
change (seed, split type, outlier rule, test, preprocessing scope, hyperparameter).
Template: `$S/adapter_template.py`. Worked example: `examples/split_reversal/` in the
BeyondPaper repository.

## 3. Write the spec
`bp_work/multiverse_spec.yaml`:

```yaml
adapter: bp_work/adapter.py
baseline: {split: random, seed: 0, outlier_z: none}
forks:
  split: [random, group]
  seed: [0, 1, 2, 3, 4]
  outlier_z: [none, 3.0, 2.5]
claim:
  text: "Model A has lower MAE than model B"
  holds: "mae_A < mae_B"            # expression over adapter outputs
  effect: "mae_B - mae_A"           # signed effect size (positive = claim direction)
max_universes: 200
```

Run time: `multiverse.py` runs the baseline and the one-at-a-time deviations first and stops starting new
universes after `--time-budget` seconds (default 900); the summary then says `timed_out: true` and the finding
names how many of the planned universes ran. Run it once in the foreground; do not poll a background run.

Choose forks from `bp_out/ghost_forks.json` and the leakage scan. Only include choices a
careful researcher could defend; label each fork in `fork_notes` if useful. A fork changes how the
same claim is tested, never what the claim is about: do not fork the outcome, endpoint, population or
estimand (switching PFS to OS is a different claim, not a different analysis of this one).
If the baseline runs on your own port rather than the authors' code, say so in `fork_notes`;
`build_passport.py` then keeps a fragility finding at yellow at most.

## 4. Run and plot

```bash
python $S/multiverse.py bp_work/multiverse_spec.yaml --out bp_out [--jobs 4]
python $S/plot_multiverse.py bp_out/multiverse_results.csv --x split --y outlier_z --out bp_out
```

Outputs: `multiverse_results.csv`, `multiverse_summary.json`, `multiverse.findings.json`,
`multiverse_heatmap.png` (share of universes where the claim holds, per pair of forks),
`multiverse_curve.png` (specification curve: effect size per universe, sorted).

## 5. Interpret
- **Robustness** = share of universes where the claim holds.
- **Breaking changes** = single deviations from the baseline that flip the claim. These
  are the “failure conditions” of the relay package; explain each in one sentence.
- Report runtime, the number of universes, and any subsampling.

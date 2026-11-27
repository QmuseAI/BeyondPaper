"""Adapter template for bp-multiverse.

Wrap the paper's analysis so that each analysis choice is a key in `config`.
Return a flat dict of numbers; the spec's `holds` and `effect` expressions use these keys.
Keep it deterministic given `config` (use config["seed"] everywhere randomness appears).
"""
from __future__ import annotations


def run(config: dict) -> dict:
    seed = int(config.get("seed", 0))

    # 1. Load data exactly as the paper does (same files, same filters unless a fork changes them).
    # data = load_data(...)

    # 2. Apply forks, one change per key, e.g.
    # if config.get("outlier_z") not in (None, "none"):
    #     data = data[abs(zscore(data.y)) <= float(config["outlier_z"])]
    # splitter = make_splitter(config["split"], seed)

    # 3. Call the paper's own training / analysis code.
    # metric_a, metric_b = evaluate(data, splitter, seed)

    metric_a, metric_b = 0.0, 0.0  # replace
    return {"metric_A": metric_a, "metric_B": metric_b, "n": 0}

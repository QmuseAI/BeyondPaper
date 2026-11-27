#!/usr/bin/env python3
"""Plot multiverse results: robustness heatmap and specification curve.

  multiverse_heatmap.png  share of universes in which the claim holds, for each pair of
                          values of two forks (averaged over all other forks)
  multiverse_curve.png    specification curve: effect size of every universe, sorted,
                          with the analysis choices of each universe underneath

Usage:
    python plot_multiverse.py bp_out/multiverse_results.csv --x split --y outlier_z --out bp_out
    [--summary bp_out/multiverse_summary.json]   # for the claim text in titles
"""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# Palette (light mode): sequential blue ramp, categorical slots 1-2, chart chrome.
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SERIES_1, SERIES_2 = "#2a78d6", "#eb6834"
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)


def to_bool(s: pd.Series) -> pd.Series:
    return s.map(lambda v: {"True": True, "False": False, True: True, False: False}.get(v, np.nan))


def heatmap(df: pd.DataFrame, x: str, y: str, title: str, out: Path):
    ok = df[df["error"].isna() | (df["error"] == "")].copy()
    ok["holds_b"] = to_bool(ok["holds"]).astype(float)
    pv = ok.pivot_table(index=y, columns=x, values="holds_b", aggfunc="mean")
    counts = ok.pivot_table(index=y, columns=x, values="holds_b", aggfunc="count")
    cmap = LinearSegmentedColormap.from_list("bp_blue", BLUE_RAMP)

    width = max(4.8, 1.9 + 1.3 * pv.shape[1])
    fig, ax = plt.subplots(figsize=(width, 1.9 + 0.75 * pv.shape[0]), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    ax.spines["bottom"].set_visible(False)
    mesh = ax.pcolormesh(pv.values, cmap=cmap, vmin=0, vmax=1, edgecolors=SURFACE, linewidth=2)
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            v = pv.values[i, j]
            if np.isnan(v):
                continue
            ax.text(j + 0.5, i + 0.5, f"{v:.0%}\n(n={int(counts.values[i, j])})", ha="center", va="center",
                    fontsize=8, color="white" if v > 0.55 else INK)
    ax.set_xticks(np.arange(pv.shape[1]) + 0.5, [str(c) for c in pv.columns])
    ax.set_yticks(np.arange(pv.shape[0]) + 0.5, [str(r) for r in pv.index])
    ax.set_xlabel(x, color=INK_2)
    ax.set_ylabel(y, color=INK_2)
    ax.set_title(textwrap.fill(title, int(width * 9)), color=INK, fontsize=9, loc="left")
    cb = fig.colorbar(mesh, ax=ax, fraction=0.05, pad=0.03)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)
    cb.set_label("share of universes\nwhere the claim holds", color=INK_2, fontsize=7)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def spec_curve(df: pd.DataFrame, forks: list[str], title: str, out: Path):
    ok = df[(df["error"].isna() | (df["error"] == "")) & df["effect"].notna()].copy()
    if ok.empty:
        return False
    ok["effect"] = ok["effect"].astype(float)
    ok["holds_b"] = to_bool(ok["holds"])
    ok = ok.sort_values("effect").reset_index(drop=True)
    labels = [(f, v) for f in forks for v in sorted(ok[f].astype(str).unique())]

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, dpi=200,
                                   figsize=(max(6, 0.06 * len(ok) + 3), 3 + 0.22 * len(labels)),
                                   gridspec_kw={"height_ratios": [2.2, max(1.0, 0.22 * len(labels))]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (ax1, ax2):
        style(ax)
    xs = np.arange(len(ok))
    ax1.axhline(0, color=AXIS, linewidth=1)
    ax1.grid(axis="y", color=GRID, linewidth=0.6)
    held = (ok["holds_b"] == True).to_numpy()  # noqa: E712
    eff = ok["effect"].to_numpy()
    ax1.scatter(xs[held], eff[held], s=22, color=SERIES_1, edgecolors=SURFACE, linewidths=1,
                label="claim holds", zorder=3)
    ax1.scatter(xs[~held], eff[~held], s=26, color=SERIES_2, marker="X", edgecolors=SURFACE,
                linewidths=0.8, label="claim fails", zorder=3)
    if "universe" in ok and (ok["universe"].astype(int) == 0).any():
        b = int(np.flatnonzero(ok["universe"].astype(int).values == 0)[0])
        right = b > len(ok) / 2
        ax1.annotate("paper's choices", (b, eff[b]), xytext=(-14 if right else 14, -18), textcoords="offset points",
                     ha="right" if right else "left", va="top", fontsize=8, color=INK_2,
                     arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    ax1.set_ylabel("effect (claim direction > 0)", color=INK_2, fontsize=8)
    ax1.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper left")
    ax1.set_title(textwrap.fill(title, 90), color=INK, fontsize=9, loc="left")

    for row, (f, v) in enumerate(labels):
        on = (ok[f].astype(str) == v).to_numpy()
        ax2.scatter(xs[on], np.full(int(on.sum()), row), s=9, color=INK_2, linewidths=0)
    ax2.set_yticks(range(len(labels)), [f"{f} = {v}" for f, v in labels], fontsize=7)
    ax2.set_ylim(-0.7, len(labels) - 0.3)
    ax2.invert_yaxis()
    ax2.set_xlabel("universes, sorted by effect", color=INK_2, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("--x", required=True, help="fork shown on the x axis of the heatmap")
    ap.add_argument("--y", required=True, help="fork shown on the y axis of the heatmap")
    ap.add_argument("--summary", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()

    df = pd.read_csv(args.results, dtype=str, keep_default_na=False, na_values=[""])
    summary_path = args.summary or args.results.with_name("multiverse_summary.json")
    claim, forks = "claim", [c for c in (args.x, args.y)]
    if summary_path.exists():
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        claim = s["claim"].get("text") or s["claim"]["holds"]
        forks = list(s["forks"])
    args.out.mkdir(parents=True, exist_ok=True)
    heatmap(df, args.x, args.y, f"Where does it hold? {claim}", args.out / "multiverse_heatmap.png")
    made = spec_curve(df, forks, f"Specification curve: {claim}", args.out / "multiverse_curve.png")
    print("wrote multiverse_heatmap.png" + (" and multiverse_curve.png" if made else " (no effect column: curve skipped)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

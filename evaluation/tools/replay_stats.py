"""Precision and red counts by subset after replaying build_passport caps. Usage: python tools/replay_stats.py <build_passport.py>"""
import json, sys, csv, copy, importlib.util, collections, re
from pathlib import Path
R = Path(__file__).resolve().parent.parent / "results"; FD = R.parent / "facts"
spec = importlib.util.spec_from_file_location("bpp", sys.argv[1]); bpp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpp)
TRUE = {"real", "new_real", "injected"}
def capall(f, ctx):
    g = copy.deepcopy(f); ch = []; fn = ctx["file"]; gate = ctx["gate"]
    g = bpp.gate_cap(g, fn, ch); g = bpp.red_lint(g, fn, ch); g = bpp.notation_cap(g, ch)
    g = bpp.port_rerun_cap(g, ch, gate); g = bpp.rounding_cap(g, ch); g = bpp.last_digit_cap(g, ch)
    g = bpp.robustness_port_cap(g, ch, gate); g = bpp.subgroup_total_cap(g, ch)
    g = bpp.self_consistent_cap(g, ch, fn); g = bpp.positional_cap(g, ch, fn)
    if hasattr(bpp, "v111_caps"): g = bpp.v111_caps(g, ch, ctx)
    if f.get("type") == "grouped_split_risk" and str(f.get("source", "")).startswith("script:leakage") and g["severity"] in ("red", "yellow"):
        g["severity"] = "grey"
    return g
agg = collections.defaultdict(collections.Counter)
for d in sorted(R.glob("*/")):
    data = json.load(open(d / "findings.json")); F = data["findings"]; gate = ((data.get("reproduction") or {}).get("counts") or {})
    meta = json.load(open(d / "meta.json")); fp = FD / f"{meta['paper_id']}.json"; facts = json.load(open(fp)) if fp.exists() else {}
    lab = {r["finding_id"]: r["human_label"] for r in csv.DictReader(open(d / "adjudication.csv", encoding="utf-8-sig"))}
    pins = bool(re.search(r"(?i)(Dockerfile|environment\.yml|renv\.lock)[^.]{0,80}\bpin|Dockerfile[^.]{0,120}==\s*\d", " ".join(str(f.get("evidence", "")) for f in F)))
    tags = ["all"] + (["blind_v110x"] if meta["variant"] == "blind" and meta["version"].startswith("v1.10") else []) + (["v1105"] if meta["version"] == "v1.10.5" else [])
    for f in F:
        l = lab.get(f["id"])
        if not l or f["severity"] not in ("red", "yellow"): continue
        k = capall(f, {"file": f.get("file", ""), "index": None, "facts": facts, "gate": gate, "verify": None, "no_verify": True, "run_mentions_pins": pins})["severity"]
        for t in tags:
            c = agg[t]
            if l in TRUE: c["tp_before"] += 1; c["tp_after"] += k in ("red", "yellow")
            if l == "false_positive": c["fp_before"] += 1; c["fp_after"] += k in ("red", "yellow")
            if f["severity"] == "red": c["red_before"] += 1; c["red_after"] += k == "red"
            if f["severity"] == "red" and l == "false_positive": c["redfp_before"] += 1; c["redfp_after"] += k == "red"
            if f["severity"] == "red" and l in TRUE: c["redtp_before"] += 1; c["redtp_after"] += k == "red"
for t, c in agg.items():
    pb = c["tp_before"] / max(1, c["tp_before"] + c["fp_before"]); pa = c["tp_after"] / max(1, c["tp_after"] + c["fp_after"])
    print(f"{t}: precision {c['tp_before']}/{c['tp_before'] + c['fp_before']} ({pb:.0%}) -> {c['tp_after']}/{c['tp_after'] + c['fp_after']} ({pa:.0%}); "
          f"red {c['red_before']} -> {c['red_after']} (false {c['redfp_before']} -> {c['redfp_after']}, true {c['redtp_before']} -> {c['redtp_after']})")

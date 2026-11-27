"""Replay build_passport (v1.11.6: caps + merging + archive_lift for verified, gate-capped number findings) on every saved, judged run.

Usage: python tools/replay_v1116.py <path/to/build_passport.py> [-v] [--runs B16-v1111 ...]
Counts each final red/yellow item once. A final item is "true" if it or anything merged into it was judged
real/new_real/injected. Regressions: true originals that end up grey, or that disappear without being merged.
"""
import json, csv, copy, importlib.util, collections, argparse, re
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("bpp"); ap.add_argument("-v", action="store_true")
ap.add_argument("--runs", nargs="*"); ap.add_argument("--no-hand-merge", action="store_true")
a = ap.parse_args()
ROOT = Path(__file__).resolve().parent.parent
R, FD = ROOT / "results", ROOT / "facts"
spec = importlib.util.spec_from_file_location("bpp", a.bpp); bpp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpp)
TRUE = {"real", "new_real", "injected"}


def capall(f, ctx):
    g = copy.deepcopy(f); ch = []; fn = ctx["file"]; gate = ctx["gate"]
    g = bpp.gate_cap(g, fn, ch); g = bpp.red_lint(g, fn, ch); g = bpp.notation_cap(g, ch)
    g = bpp.port_rerun_cap(g, ch, gate); g = bpp.rounding_cap(g, ch); g = bpp.last_digit_cap(g, ch)
    g = bpp.robustness_port_cap(g, ch, gate); g = bpp.subgroup_total_cap(g, ch)
    g = bpp.self_consistent_cap(g, ch, fn); g = bpp.positional_cap(g, ch, fn)
    g = bpp.v111_caps(g, ch, ctx)
    # verify.json is not saved with a run; a saved verified_by means verify_queue upheld it with an existing check
    # output, so archive_lift is replayed without reading that output (check_text=None).
    if f.get("verified_by") and hasattr(bpp, "archive_lift"):
        g = bpp.archive_lift(g, ch, None, fn)
    if f.get("type") == "grouped_split_risk" and str(f.get("source", "")).startswith("script:leakage") and g["severity"] in ("red", "yellow"):
        g["severity"] = "grey"; ch.append({"reason": "keyword_group_grey"})
    return g, ch


T = collections.Counter(); bad = []; collapsed = []; lifted = []
for d in sorted(R.glob("*/")):
    if a.runs and d.name not in a.runs:
        continue
    if not (d / "adjudication.csv").exists():
        continue
    data = json.load(open(d / "findings.json")); F = data["findings"] if isinstance(data, dict) else data
    gate = ((data.get("reproduction") or {}).get("counts") or {}) if isinstance(data, dict) else {}
    meta = json.load(open(d / "meta.json"))
    fp = FD / f"{meta['paper_id']}.json"; facts = json.load(open(fp)) if fp.exists() else {}
    lab = {r["finding_id"]: r["human_label"] for r in csv.DictReader(open(d / "adjudication.csv", encoding="utf-8-sig"))}
    pins = bool(re.search(r"(?i)(Dockerfile|environment\.yml|renv\.lock)[^.]{0,80}\bpin|Dockerfile[^.]{0,120}==\s*\d",
                          " ".join(str(f.get("evidence", "")) for f in F)))
    items = []
    for f in F:
        ctx = {"file": f.get("file", ""), "index": None, "facts": facts, "gate": gate, "verify": None, "no_verify": True,
               "run_mentions_pins": pins}
        g, ch = capall(f, ctx)
        g["_orig"] = f["id"]; g["_was"] = f["severity"]; g["_why"] = [c.get("reason") for c in ch]
        if g["severity"] == "red" and f["severity"] != "red":
            lifted.append((d.name, f["id"], lab.get(f["id"])))
        g.setdefault("hand_written", bpp.is_hand_written(f, f.get("file", "")))
        items.append(g)
    items.sort(key=lambda x: (bpp.ORDER.get(x.get("severity"), 9), x.get("module", ""), x.get("type", "")))
    ch = []
    items = bpp.merge_near_duplicates(items, ch)
    items = bpp.consolidate_low_value(items, ch)
    if hasattr(bpp, "group_repeats"):
        items = bpp.group_repeats(items, ch, merge_hand_written=not a.no_hand_merge)
    c = collections.Counter()
    represented = {}
    for it in items:
        members = [it["_orig"]] + [m.get("_orig") for m in it.get("merged", []) if m.get("_orig")]
        # merged entries keep only summary fields; recover ids via location/type match
        for mid in members:
            represented[mid] = it
        if it["severity"] not in ("red", "yellow"):
            continue
        labs = [lab.get(m) for m in members if lab.get(m)]
        own = lab.get(it["_orig"])
        c["shown"] += 1
        if any(l in TRUE for l in labs):
            c["true"] += 1
            nt = sum(1 for l in labs if l in TRUE)
            if nt > 1:
                collapsed.append((d.name, [m for m in members if lab.get(m) in TRUE]))
        elif own == "false_positive" or (own in (None, "duplicate", "unclear") and "false_positive" in labs and own != "unclear"):
            c["fp"] += 1
            if a.v: print(f"  FP   {d.name} {it['_orig']} {it['type']} {it.get('source')} {str(it.get('evidence'))[:110]}")
        elif own == "unclear":
            c["unclear"] += 1
        elif own == "duplicate":
            c["dup"] += 1
            if a.v: print(f"  DUP  {d.name} {it['_orig']} {it['type']} {it.get('source')} {str(it.get('evidence'))[:90]}")
        else:
            c["unjudged"] += 1
    for f in F:
        if f["severity"] in ("red", "yellow") and lab.get(f["id"]) in TRUE:
            it = represented.get(f["id"])
            if it is None or it["severity"] not in ("red", "yellow"):
                bad.append((d.name, f["id"], f["type"], it and it["_why"]))
    T.update(c)
    print(f"{d.name:11} shown {c['shown']:3}  true {c['true']:2}  fp {c['fp']:2}  dup {c['dup']:2}  unclear {c['unclear']:2}")
print("TOTAL", dict(T), f"precision {T['true']}/{T['true'] + T['fp']} = {T['true'] / max(1, T['true'] + T['fp']):.3f}")
print("REGRESSIONS:", bad)
print("raised to red:", lifted)
print("distinct trues collapsed into one item:", collapsed)

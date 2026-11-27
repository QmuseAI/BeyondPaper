#!/usr/bin/env python3
"""Score saved BeyondPaper runs against injected and real-problem answer keys.

Usage:
  python score_runs.py --results results --keys keys --out scores [--count-grey] [--only B10-v18,B12-v19]

For every results/<run_id>/ that has findings.json and meta.json:
  - injected recall (red/yellow; grey too with --count-grey) and red-only recall, from keys/injected_<Bxx>.json
  - real-problem recall, from keys/real_<Pxx>.json (verified items only; mapping in keys/papers.json)
  - precision, from results/<run_id>/adjudication.csv

adjudication.csv is created on the first run with an auto_label for every red/yellow finding
(injected:<id>, real:<id>, known_fp:<id>, pre_existing, unlabeled). Fill human_label with one of
  injected | real | new_real | false_positive | duplicate | unclear
and run again. Precision = (injected + real + new_real) / (those + false_positive) over adjudicated
red/yellow findings; duplicate and unclear are left out. Red false positives are counted separately.
Human labels are kept when the script is re-run; auto_label is refreshed.

Outputs (all re-computable from results/ and keys/): scores/by_run.csv, scores/summary.md
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

# finding types that count as detecting an injection/real problem of a given type
COMPATIBLE = {
    "number_mismatch": {"number_mismatch", "figure_data_mismatch", "stat_inconsistency", "stat_decision_error",
                        "rerun_mismatch", "magnitude_error", "archive_mismatch", "sample_size"},
    "unit_prefix": {"unit_mismatch", "magnitude_error", "number_mismatch"},
    "sample_size": {"sample_size", "number_mismatch", "grim_inconsistency", "stat_inconsistency", "archive_mismatch"},
    "stat_inconsistency": {"stat_inconsistency", "stat_decision_error", "number_mismatch"},
    "seed_removed": {"seed_missing", "missing_info"},
    "figure_data_mismatch": {"figure_data_mismatch", "number_mismatch", "rerun_mismatch"},
    "param_mismatch": {"param_mismatch", "rerun_mismatch", "number_mismatch", "stat_inconsistency"},
    "phantom_experiment": {"phantom_experiment", "unsupported_claim", "missing_artifact", "unverifiable_claim",
                           "missing_info"},
}
DETECT = ("red", "yellow")
GOOD = {"injected", "real", "new_real"}
LABELS = GOOD | {"false_positive", "duplicate", "unclear"}
CSV_FIELDS = ["finding_idx", "finding_id", "severity", "type", "location", "evidence_short", "auto_label",
              "human_label", "note"]


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def findings_of(run_dir: Path) -> list[dict]:
    d = load_json(run_dir / "findings.json")
    return d.get("findings", []) if isinstance(d, dict) else (d or [])


def text_of(f: dict) -> str:
    return " ".join(str(f.get(k, "")) for k in ("evidence", "location", "question_for_authors")).lower()


def has_string(text: str, s: str) -> bool:
    s = str(s).lower().strip()
    if not s:
        return False
    if re.fullmatch(r"[−-]?[\d.,·]+%?", s):  # numbers: not part of a longer number
        left = "" if s[0] in ".-−" else r"(?<![\d.])"
        return re.search(left + re.escape(s) + r"(?![\d])", text) is not None
    return s in text


def matches(item: dict, f: dict, types: set[str]) -> bool:
    if f.get("type") not in types:
        return False
    t = text_of(f)
    if any(not has_string(t, s) for s in item.get("match_all", [])):
        return False
    strings = item.get("match_any") or item.get("match_strings") or []
    return any(has_string(t, s) for s in strings)


def same_flag(a: dict, b: dict) -> bool:
    return a.get("type") == b.get("type") and (a.get("location") == b.get("location")
                                               or str(a.get("evidence", ""))[:80] == str(b.get("evidence", ""))[:80])


def read_adjudication(p: Path) -> dict[str, dict]:
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8-sig") as fh:
        return {r["finding_idx"]: r for r in csv.DictReader(fh)}


def score_run(run_dir: Path, keys: Path, papers: dict, all_runs: dict, count_grey: bool,
              accept_auto: bool = False) -> dict:
    meta = load_json(run_dir / "meta.json") or {}
    findings = findings_of(run_dir)
    bid = meta.get("paper_id", "")
    pinfo = papers.get(bid, {})
    pkey = pinfo.get("paper", bid)
    variant = meta.get("variant") or pinfo.get("variant", "")
    keep = set(DETECT) | ({"grey"} if count_grey else set())
    dets = [(i, f) for i, f in enumerate(findings) if f.get("severity") in keep]

    inj_key = load_json(keys / f"injected_{bid}.json") if variant == "injected" else None
    real_key = load_json(keys / f"real_{pkey}.json") or {}
    injections = (inj_key or {}).get("injections", [])
    real_items = real_key.get("items", [])
    known_false = real_key.get("known_false", [])

    twin = pinfo.get("clean_twin")
    baseline = []
    if twin:
        for rid, (m, fs) in all_runs.items():
            if m.get("paper_id") == twin:
                baseline += [f for f in fs if f.get("severity") in keep]

    labels: dict[int, str] = {}
    inj_hit, inj_red = {}, {}
    for inj in injections:
        types = COMPATIBLE.get(inj["type"], {inj["type"]})
        for i, f in dets:
            if matches(inj, f, types):
                tag = f"injected:{inj['id']}"
                labels.setdefault(i, tag if inj["id"] not in inj_hit else tag + "(dup)")
                inj_hit.setdefault(inj["id"], i)
                if f.get("severity") == "red":
                    inj_red[inj["id"]] = i
    real_hit = {}
    for it in real_items:
        types = set(it.get("types", [])) or COMPATIBLE.get(it.get("mechanism", ""), set())
        for i, f in dets:
            if matches(it, f, types):
                tag = f"real:{it['id']}" + ("(unverified)" if it.get("status") != "verified" else "")
                labels.setdefault(i, tag if it["id"] not in real_hit else tag + "(dup)")
                real_hit.setdefault(it["id"], i)
    for kf in known_false:
        for i, f in dets:
            if i not in labels and matches(kf, f, set(kf.get("types", []))):
                labels[i] = f"known_fp:{kf['id']}"
    for i, f in dets:
        if i not in labels and any(same_flag(f, b) for b in baseline):
            labels[i] = "pre_existing"

    # adjudication file: only red/yellow (precision is over flags a reader would act on)
    adj_path = run_dir / "adjudication.csv"
    old = read_adjudication(adj_path)
    rows = []
    for i, f in enumerate(findings):
        if f.get("severity") not in DETECT:
            continue
        prev = old.get(str(i), {})
        if prev and prev.get("type") and prev.get("type") != f.get("type"):
            prev = {}  # findings list changed under this index: drop the stale human label
        human, note = (prev.get("human_label") or "").strip(), prev.get("note", "")
        auto = labels.get(i, "unlabeled")
        if accept_auto and not human and re.fullmatch(r"(injected|real):[\w.]+", auto):
            human, note = auto.split(":")[0], "accepted from auto_label"
        rows.append({
            "finding_idx": i, "finding_id": f.get("id", ""), "severity": f.get("severity"), "type": f.get("type"),
            "location": str(f.get("location", ""))[:120],
            "evidence_short": " ".join(str(f.get("evidence", "")).split())[:220],
            "auto_label": auto, "human_label": human, "note": note,
        })
    # keep human labels written for findings that are grey/green in this run (labels added for replays of later
    # versions, e.g. v1.11.7 swapped_pair / precision rules); they do not enter this run's scores
    kept = {str(r["finding_idx"]) for r in rows}
    for k, prev in sorted(old.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if k in kept or not (prev.get("human_label") or "").strip():
            continue
        idx = int(k) if str(k).isdigit() else -1
        if 0 <= idx < len(findings) and findings[idx].get("severity") not in DETECT \
                and (not prev.get("type") or prev.get("type") == findings[idx].get("type")):
            rows.append({fld: prev.get(fld, "") for fld in CSV_FIELDS})
    with open(adj_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    # a human label overrides the automatic match: an injection or real problem counts as detected only through
    # a finding that is unlabelled or labelled with that class (B10-v18: "median mean rating" matched item 4.1)
    human_of = {int(r["finding_idx"]): r["human_label"] for r in rows}
    note_of = {int(r["finding_idx"]): r.get("note", "") or "" for r in rows}

    def confirmed(items, kind, red_only=False):
        hit = {}
        for it in items:
            types = (COMPATIBLE.get(it["type"], {it["type"]}) if kind == "injected"
                     else set(it.get("types", [])) or COMPATIBLE.get(it.get("mechanism", ""), set()))
            for i, f in dets:
                if matches(it, f, types) and human_of.get(i, "") in ("", kind) \
                        and (not red_only or f.get("severity") == "red"):
                    hit.setdefault(it["id"], i)
            # a human label "<kind>" whose note starts with "<id>:" also counts (finding type outside COMPATIBLE,
            # e.g. B07-v1116p2 reports injected sample size 27 vs 25 as missing_info)
            for i, f in dets:
                r = note_of.get(i, "")
                if human_of.get(i, "") == kind and r.split(":", 1)[0].strip() == it["id"] \
                        and (not red_only or f.get("severity") == "red"):
                    hit.setdefault(it["id"], i)
        return hit

    inj_hit, inj_red = confirmed(injections, "injected"), confirmed(injections, "injected", red_only=True)
    real_hit = confirmed(real_items, "real")

    bad_labels = [r["human_label"] for r in rows if r["human_label"] and r["human_label"] not in LABELS]
    judged = [r for r in rows if r["human_label"] in LABELS - {"duplicate", "unclear"}]
    good = [r for r in judged if r["human_label"] in GOOD]
    false = [r for r in judged if r["human_label"] == "false_positive"]
    sev = defaultdict(int)
    for f in findings:
        sev[f.get("severity", "?")] += 1
    verified = [it for it in real_items if it.get("status") == "verified"]
    return {
        "run_id": run_dir.name, "paper_id": bid, "paper_key": pkey, "variant": variant,
        "version": meta.get("version", ""), "n_findings": len(findings),
        "red": sev["red"], "yellow": sev["yellow"], "grey": sev["grey"], "green": sev["green"],
        "inj_total": len(injections), "inj_detected": len(inj_hit), "inj_detected_red": len(inj_red),
        "inj_missed": " ".join(x["id"] for x in injections if x["id"] not in inj_hit),
        "real_total": len(verified), "real_detected": sum(it["id"] in real_hit for it in verified),
        "real_found": " ".join(it["id"] for it in verified if it["id"] in real_hit),
        "real_missed": " ".join(it["id"] for it in verified if it["id"] not in real_hit),
        "known_fp_auto": sum(v.startswith("known_fp") for v in labels.values()),
        "flags_red_yellow": len(rows), "adjudicated": len(judged),
        "unadjudicated": sum(1 for r in rows if not r["human_label"]),
        "true_flags": len(good), "false_flags": len(false),
        "red_false": sum(r["severity"] == "red" for r in false),
        "red_adjudicated": sum(r["severity"] == "red" for r in judged),
        "precision": f"{len(good) / len(judged):.2f}" if judged else "",
        "bad_labels": " ".join(sorted(set(bad_labels))),
        "_inj_types": [(x["type"], x["id"] in inj_hit, x["id"] in inj_red) for x in injections],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent
    ap.add_argument("--results", type=Path, default=here / "results")
    ap.add_argument("--keys", type=Path, default=here / "keys")
    ap.add_argument("--out", type=Path, default=here / "scores")
    ap.add_argument("--count-grey", action="store_true", help="grey findings also count as detections for recall")
    ap.add_argument("--only", default="", help="comma-separated run ids")
    ap.add_argument("--models", default="", help="comma-separated model classes from keys/run_models.json "
                    "(pro, glm, flash, unknown); default: all runs")
    ap.add_argument("--accept-auto", action="store_true",
                    help="copy auto labels injected:<id> / real:<id> into empty human_label (check them anyway)")
    args = ap.parse_args()

    papers = load_json(args.keys / "papers.json") or {}
    all_runs = {}
    for d in (sorted(p for p in args.results.iterdir() if p.is_dir()) if args.results.exists() else []):
        if (d / "findings.json").exists():
            all_runs[d.name] = (load_json(d / "meta.json") or {}, findings_of(d))
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    run_models = (load_json(args.keys / "run_models.json") or {}).get("runs", {})
    want = {s.strip() for s in args.models.split(",") if s.strip()}
    results = [score_run(args.results / rid, args.keys, papers, all_runs, args.count_grey, args.accept_auto)
               for rid in all_runs if (not only or rid in only)
               and (not want or run_models.get(rid, "unknown") in want)]
    for r in results:
        r["model"] = run_models.get(r["run_id"], "unknown")
    if not results:
        print(f"no runs with findings.json under {args.results}")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    fields = [k for k in results[0] if not k.startswith("_")]
    with open(args.out / "by_run.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    by_type = defaultdict(lambda: [0, 0, 0])
    for r in results:
        for t, hit, red in r["_inj_types"]:
            by_type[t][0] += 1
            by_type[t][1] += hit
            by_type[t][2] += red
    pct = lambda a, b: f"{a}/{b} ({a / b:.0%})" if b else "–"
    md = ["# BeyondPaper 评测分数", "",
          f"由 `score_runs.py` 根据 `results/` 和 `keys/` 生成；检出 = 红/黄{'/灰' if args.count_grey else ''}。"
          + (f"只含模型：{', '.join(sorted(want))}（keys/run_models.json）。" if want else "")
          + "精确率只统计已人工判定的红/黄条目。", "",
          "| 运行 | 论文 | 类型 | 版本 | 红/黄/灰/绿 | 注入检出（红） | 真实问题检出 | 已判定 | 精确率 | 红色误报 | 未判定 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        inj = f"{r['inj_detected']}/{r['inj_total']} ({r['inj_detected_red']})" if r["inj_total"] else "–"
        real = f"{r['real_detected']}/{r['real_total']}" + (f" 漏 {r['real_missed']}" if r["real_missed"] else "") \
            if r["real_total"] else "–"
        md.append(f"| {r['run_id']} | {r['paper_id']}/{r['paper_key']} | {r['variant']} | {r['version']} | "
                  f"{r['red']}/{r['yellow']}/{r['grey']}/{r['green']} | {inj} | {real} | "
                  f"{r['adjudicated']}/{r['flags_red_yellow']} | {r['precision'] or '–'} | "
                  f"{r['red_false']}/{r['red_adjudicated']} | {r['unadjudicated']} |")
    it, idet, ired = (sum(r[k] for r in results) for k in ("inj_total", "inj_detected", "inj_detected_red"))
    rt, rdet = sum(r["real_total"] for r in results), sum(r["real_detected"] for r in results)
    judged, good = sum(r["adjudicated"] for r in results), sum(r["true_flags"] for r in results)
    blind = [r for r in results if r["variant"] == "blind"]
    md += ["", "## 合计", "",
           f"- 注入问题检出：{pct(idet, it)}，其中标红 {pct(ired, it)}",
           f"- 已发表论文真实问题检出（按运行累计）：{pct(rdet, rt)}",
           f"- 精确率（已判定红/黄）：{pct(good, judged)}；红色误报 {sum(r['red_false'] for r in results)} 条",
           f"- 盲测论文红色误报：{sum(r['red_false'] for r in blind)} 条 / 已判定红色 "
           f"{sum(r['red_adjudicated'] for r in blind)} 条（{len(blind)} 次运行）",
           f"- 尚未判定的红/黄条目：{sum(r['unadjudicated'] for r in results)}", ""]
    by_model = defaultdict(list)
    for r in results:
        by_model[r["model"]].append(r)
    md += ["## 按模型（keys/run_models.json）", "", "| 模型 | 运行 | 注入检出（红） | 真实问题 | 精确率 | 红色误报 |", "|---|---|---|---|---|---|"]
    for mname, rs in sorted(by_model.items()):
        g = lambda k: sum(x[k] for x in rs)
        md.append(f"| {mname} | {len(rs)} | {g('inj_detected')}/{g('inj_total')} ({g('inj_detected_red')}) | "
                  f"{g('real_detected')}/{g('real_total')} | {pct(g('true_flags'), g('true_flags') + g('false_flags'))} | "
                  f"{g('red_false')}/{g('red_adjudicated')} |")
    md.append("")
    if by_type:
        md += ["## 按注入类型", "", "| 类型 | 注入 | 检出 | 标红 |", "|---|---|---|---|"]
        md += [f"| {t} | {n} | {h} | {red} |" for t, (n, h, red) in sorted(by_type.items())]
    bad = [r for r in results if r["bad_labels"]]
    if bad:
        md += ["", "## 无法识别的 human_label", ""] + [f"- {r['run_id']}: {r['bad_labels']}" for r in bad]
    (args.out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

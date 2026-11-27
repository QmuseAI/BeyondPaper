#!/usr/bin/env python3
"""BeyondPaper pitfall atlas (前车之鉴): failure mechanisms verified across published papers.

One audit says what went wrong in one paper. The atlas keeps every verified problem as an instance of a
mechanism (test data used for selection, a variable left over from the previous loop, a 90% interval
reported as 95%, ...), so the same mechanism can be looked for in the next paper, in any field, and every
new finding can point to where it happened before. Mistakes made by the auditor itself are kept too
(kind "auditor"): they become rules for the next run.

Atlas files (default: ../atlas next to this script; add more with --atlas DIR):
    mechanisms.json   taxonomy: finding types, trigger patterns, detector, question for authors
    papers.json       papers the cases come from
    cases.jsonl       one verified case per line (kind "paper" or "auditor")

Commands:
    prior OUT [--work bp_work] [--top 6]
        Before stage 3: rank mechanisms by how well they fit this paper (text + code) and list the verified
        cases behind each. Writes OUT/atlas_prior.md and OUT/atlas_prior.json.
    match OUT
        After build_passport.py: link each red/yellow finding to a mechanism and to verified cases in other
        papers. Writes OUT/precedents.md, OUT/precedents.json and OUT/atlas_candidates.jsonl (new cases
        waiting for human verification).
    stats [--md]
        Mechanism x paper table of the atlas.
    curate CANDIDATES --paper-key KEY --verdict F3=confirmed,F5=rejected[:mechanism] [--into DIR]
                      [--cite ... --doi ... --field ... --code ...]
        Human step: confirmed candidates become paper cases, rejected ones auditor cases.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ATLAS = HERE.parent / "atlas"
TEXT_EXT = {".md", ".txt", ".tex"}
CODE_EXT = {".r", ".rmd", ".py", ".ipynb", ".m", ".do", ".jl", ".sh", ".sas", ".sps"}
MAX_BYTES = 400_000  # per file read for trigger matching
FLAG = {"red", "yellow"}
NUMBER_TYPES = {"number_mismatch", "sample_size", "rerun_mismatch", "magnitude_error", "archive_mismatch"}


# ---------------------------------------------------------------- atlas loading
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_atlas(dirs: list[Path]) -> tuple[dict, dict, list[dict]]:
    mechs, papers, cases, seen = {}, {}, [], set()
    for d in dirs:
        for m in load_json(d / "mechanisms.json", {}).get("mechanisms", []):
            mechs.setdefault(m["id"], m)
        papers.update({k: v for k, v in load_json(d / "papers.json", {}).items() if k not in papers})
        p = d / "cases.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                c = json.loads(line)
                if c["id"] not in seen:
                    seen.add(c["id"])
                    cases.append(c)
    return mechs, papers, cases


def by_mechanism(cases: list[dict]) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for c in cases:
        if c.get("status") == "verified":
            out[c["mechanism"]].append(c)
    return out


def esc(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------- prior
def gather_text(work: Path, out: Path) -> tuple[str, str, list[str]]:
    """Paper text, code text and the list of material file names."""
    paper, code, names = [], [], []
    for p in [work / "paper.md", *sorted((work / "prep").glob("**/*.md"))] if (work / "prep").exists() else [work / "paper.md"]:
        if p.exists():
            paper.append(p.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES])
    ledger = load_json(out / "ledger.json", {})
    if ledger:
        paper.append(json.dumps(ledger, ensure_ascii=False)[:MAX_BYTES])
    if work.exists():
        for p in sorted(work.rglob("*")):
            if not p.is_file() or "prep" in p.relative_to(work).parts:
                continue
            names.append(str(p.relative_to(work)))
            if p.suffix.lower() == ".ipynb":  # code cells only; outputs can be megabytes
                nb = load_json(p, {})
                src = ["".join(c.get("source", [])) if isinstance(c.get("source"), list) else str(c.get("source", ""))
                       for c in nb.get("cells", []) if c.get("cell_type") == "code"] if isinstance(nb, dict) else []
                code.append("\n".join(src)[:MAX_BYTES])
            elif p.suffix.lower() in CODE_EXT:
                code.append(p.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES])
    return "\n".join(paper), "\n".join(code), names


def score_mechanism(m: dict, paper: str, code: str, files: str) -> list[str]:
    req = m.get("require_files")
    if req and not re.search(req, files, re.M):
        return []
    text = code if m.get("kind") == "auditor" and req else paper + "\n" + code
    hits = []
    for t in m.get("triggers", []):
        mt = re.search(t, text, re.I | re.M)
        if mt:
            hits.append(mt.group(0).strip()[:40])
    return hits


def same_paper_keys(papers: dict, out: Path, names: str = "") -> set[str]:
    ledger = load_json(out / "ledger.json", {})
    doi = ((ledger.get("paper") or {}).get("doi") or "").lower().strip() if isinstance(ledger, dict) else ""
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    same = {k for k, v in papers.items() if doi and (v.get("doi") or "").lower() in doi and v.get("doi")}
    # v1.11: capsules without a DOI are recognised by their Code Ocean id in the file names
    same |= {k for k, v in papers.items() if v.get("capsule") and f"capsule-{v['capsule']}" in names}
    return same


def short_cite(papers: dict, key: str) -> str:
    return (papers.get(key) or {}).get("cite", key)


def cmd_prior(args, mechs, papers, cases) -> int:
    out, work = args.out, args.work
    paper, code, names = gather_text(work, out)
    if not paper and not code:
        print(f"warning: no paper text or code found under {work}", file=sys.stderr)
    files = "\n".join(names)
    same = same_paper_keys(papers, out, files)  # never show a paper its own cases
    cases_by = by_mechanism([c for c in cases if c.get("kind") == "auditor" or c["paper"] not in same])
    ranked_paper, auditor = [], []
    for m in mechs.values():
        hits = score_mechanism(m, paper, code, files)
        n = len(cases_by.get(m["id"], []))
        if m.get("kind") == "auditor":
            if hits and n:
                auditor.append((m, hits, n))
            continue
        if len(hits) >= 2:
            ranked_paper.append((len(hits) + 0.5 * min(n, 4), m, hits, n))
    ranked_paper.sort(key=lambda x: -x[0])
    top = ranked_paper[: args.top]

    n_papers = len({c["paper"] for c in cases if c.get("kind") == "paper" and c.get("status") == "verified"})
    n_cases = sum(1 for c in cases if c.get("kind") == "paper" and c.get("status") == "verified")
    result = {"atlas": {"papers": n_papers, "verified_cases": n_cases},
              "mechanisms": [{"id": m["id"], "name_zh": m["name_zh"], "matched": hits, "cases": [c["id"] for c in cases_by.get(m["id"], [])],
                              "detector": m.get("detector"), "question": m.get("question")} for _, m, hits, _ in top],
              "auditor_rules": [{"id": m["id"], "name_zh": m["name_zh"], "matched": hits, "cases": [c["id"] for c in cases_by.get(m["id"], [])],
                                 "rule": m.get("detector")} for m, hits, _ in auditor]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "atlas_prior.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 前车之鉴：同类论文里真出过的错", "",
          f"失败机制库：{n_papers} 篇已发表论文、{n_cases} 条人工核实的案例。下面是与本文方法和材料最相关的机制，"
          "第 3 阶段逐条检查；匹配依据是本文正文和代码里出现的关键词，不代表本文有这个问题。", ""]
    if not top:
        md.append("没有匹配度足够的机制。")
    for i, (_, m, hits, n) in enumerate(top, 1):
        md += [f"## {i}. {m['name_zh']}（`{m['id']}`）", "", m.get("summary_zh") or m["summary"], "",
               f"- 本文中的线索：{', '.join('`' + esc(h) + '`' for h in hits)}",
               f"- 检查方法：{m.get('detector')}",
               f"- 要问作者：{m.get('question')}"]
        for c in cases_by.get(m["id"], [])[:3]:
            md.append(f"- 先例 {c['id']}（{esc(short_cite(papers, c['paper']))}）：论文写 “{esc(c['says'])}”；材料显示 {esc(c['shows'])}。影响：{esc(c['impact'])}")
        if not n:
            md.append("- 库里还没有已核实的先例。")
        md.append("")
    if auditor:
        md += ["## 审计方自己踩过的坑（本次运行必须遵守）", ""]
        for m, hits, n in auditor:
            cs = cases_by.get(m["id"], [])
            md.append(f"- **{m['name_zh']}**：{m.get('summary_zh') or m['summary']} 规则：{m.get('detector')}。先例：{', '.join(c['id'] for c in cs)}")
        md.append("")
    (out / "atlas_prior.md").write_text("\n".join(md), encoding="utf-8")
    print(f"atlas: {n_papers} papers, {n_cases} verified cases; prior mechanisms={len(top)} "
          f"[{', '.join(m['id'] for _, m, _, _ in top)}]; auditor rules={len(auditor)} -> {out / 'atlas_prior.md'}")
    return 0


# ---------------------------------------------------------------- match
def all_findings(out: Path) -> list[dict]:
    merged = load_json(out / "findings.json", {})
    if isinstance(merged, dict) and merged.get("findings"):
        return merged["findings"]
    items = []
    for f in sorted(out.glob("*.findings.json")):
        items += [x for x in load_json(f, []) if isinstance(x, dict)]
    for i, x in enumerate(items, 1):
        x.setdefault("id", f"F{i}")
    return items


CI_WORDS = re.compile(r"\bCI\b|confidence|interval|percentile|置信", re.I)
CENTRE_WORDS = re.compile(r"\bmedian\b|\bmean\b|average|中位数|均值", re.I)
COUNT_WORDS = re.compile(r"\b(items?|participants?|articles?|patients?|forms?|rows?|n ?=)\b", re.I)
MODEL_WORDS = re.compile(r"\bdf\b|degrees of freedom|coefficient|intercept|model", re.I)


def mechanism_for(f: dict, mechs: dict) -> str | None:
    t, ev = f.get("type"), f"{f.get('evidence', '')} {f.get('location', '')}"
    if t == "param_mismatch":
        if CI_WORDS.search(ev):
            return "interval_mislabel"
        if CENTRE_WORDS.search(ev):
            return "statistic_mislabel"
        return "methods_code_mismatch"
    if t == "stat_inconsistency" and re.search(r"90 ?%|95 ?%.*percentile|level", ev, re.I):
        return "interval_mislabel"
    if t in ("number_mismatch", "archive_mismatch"):
        if CENTRE_WORDS.search(ev) and re.search(r"median", ev, re.I):
            return "statistic_mislabel"
        if t == "archive_mismatch" and MODEL_WORDS.search(ev) and re.search(r"\bdf\b|degrees of freedom", ev, re.I):
            return "unregenerable_result"
        return "transcription_error"
    if t == "sample_size":
        return "count_arithmetic" if COUNT_WORDS.search(ev) else "transcription_error"
    for m in mechs.values():
        if m.get("kind") == "paper" and t in m.get("finding_types", []):
            return m["id"]
    return None


def resembles_auditor(f: dict) -> list[str]:
    """Auditor mechanisms this finding looks like (hand-written number findings from our own recount)."""
    out = []
    src = str(f.get("source", "")).lower()
    ours = (not src.startswith("script:") or "llm" in src or "+" in src or "not_reproduced" in str(f.get("evidence", ""))
            or f.get("hand_written") or str(f.get("module", "")).lower() == "llm")  # B10-v18: llm module, script source
    if f.get("type") in NUMBER_TYPES and (ours or f.get("type") == "rerun_mismatch"):
        ev = str(f.get("evidence", ""))
        if f.get("gate_capped") or re.search(r"\bdata (contain|give|show)|our (count|recount|re-?run)|re-?comput|rows?\b", ev, re.I):
            out.append("port_semantics")
        if re.search(r"\d+ ?\+ ?\d+|sum|total|combination", ev, re.I):
            out.append("aggregation_slip")
        if re.search(r"\d+ ?% |percent|proportion|\d+ ?/ ?\d+", ev, re.I):
            out.append("denominator_choice")
    return out


def cmd_match(args, mechs, papers, cases) -> int:
    out = args.out
    findings = [f for f in all_findings(out) if f.get("severity") in FLAG]
    ledger = load_json(out / "ledger.json", {})
    this = ledger.get("paper", {}) if isinstance(ledger, dict) else {}
    work = getattr(args, "work", None) or out.parent / "bp_work"
    names = "\n".join(str(p) for p in Path(work).rglob("*")) if Path(work).exists() else ""
    same = same_paper_keys(papers, out, names)
    cases_by = by_mechanism(cases)
    rows, cand = [], []
    for f in findings:
        mid = mechanism_for(f, mechs)
        prec = [c for c in cases_by.get(mid, []) if c.get("kind") == "paper" and c["paper"] not in same] if mid else []
        aud = resembles_auditor(f)
        rows.append({"finding": f.get("id"), "severity": f.get("severity"), "type": f.get("type"), "mechanism": mid,
                     "precedents": [c["id"] for c in prec], "fields": sorted({(papers.get(c["paper"]) or {}).get("field", c["paper"]) for c in prec}),
                     "resembles_auditor_pitfall": aud})
        if mid:
            cand.append({"id": f"cand-{f.get('id')}", "kind": "paper", "mechanism": mid, "paper": None,
                         "paper_meta": {"title": this.get("title"), "doi": this.get("doi")},
                         "location": f.get("location"), "says": None, "shows": f.get("evidence"),
                         "impact": None, "found_by": f"agent ({f.get('source', 'llm')})", "finding": f.get("id"),
                         "severity": f.get("severity"), "status": "candidate"})
    out.mkdir(parents=True, exist_ok=True)
    (out / "precedents.json").write_text(json.dumps({"paper": this, "links": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "atlas_candidates.jsonl").open("w", encoding="utf-8") as fh:
        for c in cand:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    linked = [r for r in rows if r["precedents"]]
    md = ["# 前车之鉴：本次发现在其他论文里的先例", "",
          f"{len(findings)} 条红/黄发现中，{len(linked)} 条属于失败机制库里已在其他已发表论文中核实过的机制。"
          "先例说明这类问题真实存在、值得核实，不说明本文的这一条一定成立。", "",
          "| 发现 | 灯号 | 机制 | 先例（其他论文） | 涉及领域 |", "|---|---|---|---|---|"]
    light = {"red": "🔴", "yellow": "🟡"}
    for r in rows:
        if not r["mechanism"]:
            continue
        name = (mechs.get(r["mechanism"]) or {}).get("name_zh", r["mechanism"])
        prec = ", ".join(r["precedents"][:4]) or "—"
        md.append(f"| {r['finding']} | {light.get(r['severity'], '')} | {esc(name)} | {prec} | {esc('; '.join(r['fields'][:3])) or '—'} |")
    warn = [r for r in rows if r["resembles_auditor_pitfall"]]
    if warn:
        md += ["", "## 需要先排除审计方自己的错", "",
               "下列发现是根据我们自己对作者数据的重算写下的，与库中审计方踩过的坑相似；"
               "先把数值放进 recomputed.json 过复现门，确认作者存档输出与论文不一致后才算论文问题。", ""]
        for r in warn:
            names = [(mechs.get(a) or {}).get("name_zh", a) for a in r["resembles_auditor_pitfall"]]
            md.append(f"- {r['finding']}：{'；'.join(names)}")
    md += ["", "新案例候选已写入 `atlas_candidates.jsonl`，人工核实后用 `atlas.py curate` 收入库中。"]
    (out / "precedents.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"flagged={len(findings)} with_mechanism={sum(1 for r in rows if r['mechanism'])} with_precedent={len(linked)} "
          f"auditor_lookalikes={len(warn)} candidates={len(cand)} -> {out / 'precedents.md'}")
    return 0


# ---------------------------------------------------------------- stats
def cmd_stats(args, mechs, papers, cases) -> int:
    ver = [c for c in cases if c.get("status") == "verified"]
    keys = sorted({c["paper"] for c in ver})
    table = defaultdict(Counter)
    for c in ver:
        table[c["mechanism"]][c["paper"]] += 1
    order = [m for m in mechs if m in table]
    if args.md:
        print("| 机制 | " + " | ".join(keys) + " | 合计 |")
        print("|---|" + "---|" * (len(keys) + 1))
        for m in order:
            print(f"| {mechs[m]['name_zh']} | " + " | ".join(str(table[m][k] or "") for k in keys) + f" | {sum(table[m].values())} |")
        print("\n" + "; ".join(f"{k}: {esc(short_cite(papers, k))} — {esc((papers.get(k) or {}).get('field', ''))}" for k in keys))
    else:
        for m in order:
            print(f"{m:28s} " + " ".join(f"{k}:{table[m][k]}" for k in keys if table[m][k]))
    kinds = Counter(c.get("kind") for c in ver)
    cross = [m for m in order if len({(papers.get(k) or {}).get('field') for k in table[m]}) > 1]
    print(f"\nverified cases={len(ver)} (paper={kinds['paper']}, auditor={kinds['auditor']}); papers={len(keys)}; "
          f"mechanisms seen in more than one field: {', '.join(cross) or 'none'}")
    return 0


# ---------------------------------------------------------------- curate
def cmd_curate(args, mechs, papers, cases) -> int:
    into = args.into
    into.mkdir(parents=True, exist_ok=True)
    cands = {}
    for line in args.candidates.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            cands[c["finding"]] = c
    pfile = into / "papers.json"
    plist = load_json(pfile, {})
    if args.paper_key not in plist and args.paper_key not in papers:
        if not args.cite:
            print(f"error: new paper key {args.paper_key}: give --cite, --doi, --field, --code", file=sys.stderr)
            return 2
        plist[args.paper_key] = {"cite": args.cite, "doi": args.doi, "field": args.field, "code": args.code}
        pfile.write_text(json.dumps(plist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    existing = {c["id"] for c in cases}
    added = []
    for item in args.verdict.split(","):
        fid, _, rest = item.partition("=")
        verdict, _, amech = rest.partition(":")
        c = cands.get(fid.strip())
        if not c:
            print(f"warning: {fid} not in candidates", file=sys.stderr)
            continue
        if verdict == "confirmed":
            new = {**c, "id": f"{args.paper_key}-{fid}", "paper": args.paper_key, "status": "verified",
                   "found_by": c.get("found_by")}
        elif verdict == "rejected":
            m = amech or "unclassified_false_alarm"
            if m not in mechs or mechs[m].get("kind") != "auditor":
                print(f"warning: {m} is not an auditor mechanism; using unclassified_false_alarm", file=sys.stderr)
                m = "unclassified_false_alarm"
            new = {"id": f"A-{args.paper_key}-{fid}", "kind": "auditor", "mechanism": m, "paper": args.paper_key,
                   "location": c.get("location"), "says": f"the agent flagged {c.get('severity')} ({c.get('mechanism')})",
                   "shows": args.note or "rejected on human check", "impact": "false alarm", "found_by": "us",
                   "script": None, "status": "verified"}
        else:
            print(f"warning: unknown verdict {verdict} for {fid}", file=sys.stderr)
            continue
        for k in ("paper_meta", "finding", "severity"):
            new.pop(k, None)
        if new["id"] in existing:
            print(f"skip {new['id']}: already in the atlas")
            continue
        added.append(new)
    with (into / "cases.jsonl").open("a", encoding="utf-8") as fh:
        for c in added:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"added {len(added)} case(s) to {into / 'cases.jsonl'}: {', '.join(c['id'] for c in added)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atlas", type=Path, action="append", default=[], help="extra atlas folder (searched before the built-in one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prior")
    p.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    p.add_argument("--work", type=Path, default=Path("bp_work"))
    p.add_argument("--top", type=int, default=6)
    p = sub.add_parser("match")
    p.add_argument("out", type=Path, nargs="?", default=Path("bp_out"))
    p = sub.add_parser("stats")
    p.add_argument("--md", action="store_true")
    p = sub.add_parser("curate")
    p.add_argument("candidates", type=Path)
    p.add_argument("--paper-key", required=True)
    p.add_argument("--verdict", required=True, help="F3=confirmed,F5=rejected:port_semantics")
    p.add_argument("--note", default=None, help="why the rejected flags were wrong")
    p.add_argument("--into", type=Path, default=None, help="atlas folder to write to (default: first --atlas, else built-in)")
    for k in ("cite", "doi", "field", "code"):
        p.add_argument(f"--{k}", default=None)
    args = ap.parse_args()
    dirs = [*args.atlas, DEFAULT_ATLAS]
    mechs, papers, cases = load_atlas(dirs)
    if not mechs:
        print(f"error: no mechanisms.json found in {', '.join(map(str, dirs))}", file=sys.stderr)
        return 2
    if args.cmd == "curate" and args.into is None:
        args.into = dirs[0]
    return {"prior": cmd_prior, "match": cmd_match, "stats": cmd_stats, "curate": cmd_curate}[args.cmd](args, mechs, papers, cases)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Search a library of BeyondPaper failure paths and null-result cards.

Reads every failure_paths.json (exported from BeyondPaper runs by from_run.py), card_index.json and
card.json under the given folders and ranks them by word overlap with the query (title, question,
keywords, method, baselines, domain; keywords and method count double).

Usage:
    python search_cards.py <folder> [<folder> ...] "query words" [--top 5] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WORD = re.compile(r"[a-z0-9]+|[一-鿿]", re.I)


def words(s) -> list[str]:
    if isinstance(s, list):
        s = " ".join(map(str, s))
    return [w.lower() for w in WORD.findall(str(s or ""))]


def record(path: Path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict) or not d.get("title"):
        return None
    method = d.get("method")
    if isinstance(method, dict):
        method = method.get("name")
    base = d.get("baselines") or []
    base = [b.get("name") if isinstance(b, dict) else b for b in base]
    return {"id": d.get("id"), "title": d.get("title"), "domain": d.get("domain"), "keywords": d.get("keywords") or [],
            "question": d.get("question"), "method": method, "baselines": base,
            "verdict": d.get("verdict", {}).get("label") if isinstance(d.get("verdict"), dict) else d.get("verdict"),
            "verdict_computed": d.get("verdict_computed"), "path": str(path)}


def failure_records(path: Path) -> list[dict]:
    """One search record per failure path exported by from_run.py."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    run = path.parent.parent.name if path.parent.name == "bp_out" else path.parent.name
    paper = (d.get("paper") or {}).get("title") or run
    out = []
    for p in d.get("failure_paths", []):
        if p.get("kind") == "analysis_choice":
            title = f"{paper}: '{p.get('claim')}' fails when {p['change']['fork']} = {p['change']['value']}"
            kw = [p["change"]["fork"], str(p["change"]["value"])]
        elif p.get("kind") == "reproduction":
            title = f"{paper}: '{p.get('quantity')}' {p.get('status')}"
            kw = [str(p.get("quantity"))]
        else:
            title = f"{paper}: fragile conclusion at {p.get('location')}"
            kw = []
        out.append({"id": f"{run}:{p.get('id')}", "title": title, "domain": "",
                    "keywords": kw + list(d.get("keywords", [])), "question": p.get("claim") or p.get("detail"),
                    "method": p.get("kind"), "baselines": [], "verdict": p.get("kind"), "verdict_computed": None,
                    "path": str(path)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("args", nargs="+", help="folders followed by the query")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    *folders, query = a.args
    if not folders:
        folders = ["."]
    recs, seen = [], set()
    for f in folders:
        for p in sorted(Path(f).rglob("*.json")):
            if p.name == "failure_paths.json":
                new = failure_records(p)
            elif p.name in ("card_index.json", "card.json"):
                r = record(p)
                new = [r] if r else []
            else:
                continue
            for r in new:
                if (r["id"], r["title"]) not in seen:
                    seen.add((r["id"], r["title"]))
                    recs.append(r)
    q = set(words(query))
    scored = []
    for r in recs:
        weak = words([r["title"], r["question"], r["domain"], r["baselines"]])
        strong = words([r["keywords"], r["method"]])
        score = sum(w in q for w in set(weak)) + 2 * sum(w in q for w in set(strong))
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    top = [dict(r, score=s) for s, r in scored[: a.top]]
    if a.json:
        print(json.dumps(top, ensure_ascii=False, indent=2))
    elif not top:
        print(f"No card matches '{query}' among {len(recs)} card(s).")
    else:
        for r in top:
            print(f"[{r['score']}] {r['id']} — {r['title']}\n     method: {r['method']} vs {', '.join(map(str, r['baselines']))}"
                  f" · verdict: {r['verdict']}" + (f" (data: {r['verdict_computed']})" if r.get("verdict_computed") else "")
                  + f"\n     {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

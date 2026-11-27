#!/usr/bin/env python3
"""Build an answer key for an injected paper by diffing it against its clean twin.

Usage:
  python make_injected_key.py --clean runs/B05/paper.md --injected runs/B10/paper.md --id B10 --out keys
  python make_injected_key.py --pairs-root runs --out keys        # all five benchmark pairs

One item per changed sentence: clean text, injected text, the tokens that are new in the injected
sentence (match strings), and a guessed injection type. Review the output by hand: a key is only
as good as its match strings.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

PAIRS = {"B07": "B01", "B02": "B06", "B08": "B03", "B04": "B09", "B10": "B05"}  # injected: clean
TOKEN = re.compile(r"[−-]?\d[\d,]*(?:[.·]\d+)?%?|[A-Za-zµμ_][\w.µμ-]*")
NUMERIC = re.compile(r"^[−-]?\d")


def sentences(text: str) -> list[str]:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    out = []
    for para in re.split(r"\n\s*\n|\n(?=[#|*-] )", text):
        para = " ".join(para.split())
        if para:
            out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(“\"])", para) if s.strip()]
    return out


def new_tokens(a: str, b: str) -> list[str]:
    ta, tb = TOKEN.findall(a), TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    toks = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("replace", "insert"):
            toks += tb[j1:j2]
    return list(dict.fromkeys(t for t in toks if len(t) > 1 or t.isdigit()))


def guess_type(clean: str, inj: str, toks: list[str]) -> str:
    if not clean:
        return "phantom_experiment"
    low = inj.lower()
    if any(u in t for t in toks for u in ("μM", "µM", "mM", "nM", "mg", "kg", "ms")):
        return "unit_prefix"
    if any(NUMERIC.match(t) for t in toks):
        if re.search(r"\bp\s*[=<>]", low) and any(re.search(r"p\s*[=<>]\s*" + re.escape(t), low) for t in toks):
            return "stat_inconsistency"
        if re.search(r"\b(n\s*=|participants|respondents|patients|samples|articles|subjects|items)\b", low):
            return "sample_size"
        return "number_mismatch"
    if re.search(r"fold|seed|random_state|iterations|trees|epochs", low):
        return "param_mismatch"
    return "number_mismatch"


def build(clean_md: Path, inj_md: Path, vid: str) -> dict:
    sa, sb = sentences(clean_md.read_text(encoding="utf-8")), sentences(inj_md.read_text(encoding="utf-8"))
    sm = difflib.SequenceMatcher(None, sa, sb, autojunk=False)
    items = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        a_list, b_list = sa[i1:i2], sb[j1:j2]
        if op == "insert" or not a_list:
            pairs = [("", b) for b in b_list]
        elif op == "delete":
            pairs = [(a, "") for a in a_list]
        else:  # pair each injected sentence with its most similar clean sentence
            pairs = []
            for b in b_list:
                best = max(a_list, key=lambda a: difflib.SequenceMatcher(None, a, b).ratio())
                r = difflib.SequenceMatcher(None, best, b).ratio()
                pairs.append((best if r > 0.5 else "", b))
        for a, b in pairs:
            toks = new_tokens(a, b) if b else []
            if b and not toks:
                continue
            nums = [t for t in toks if NUMERIC.match(t)]
            items.append({
                "id": f"I{len(items) + 1}",
                "type": guess_type(a, b, toks) if b else "deleted_sentence",
                "clean": a, "injected": b,
                "match_strings": (nums or toks)[:6],
                "review": "auto",
            })
    return {"variant_id": vid, "clean_file": str(clean_md), "injected_file": str(inj_md), "injections": items}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", type=Path)
    ap.add_argument("--injected", type=Path)
    ap.add_argument("--id")
    ap.add_argument("--pairs-root", type=Path, help="folder with Bxx/paper.md; builds all five pairs")
    ap.add_argument("--out", type=Path, default=Path("keys"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = []
    if args.pairs_root:
        jobs = [(args.pairs_root / c / "paper.md", args.pairs_root / i / "paper.md", i) for i, c in PAIRS.items()]
    elif args.clean and args.injected and args.id:
        jobs = [(args.clean, args.injected, args.id)]
    else:
        ap.error("give --clean/--injected/--id or --pairs-root")
    for c, i, vid in jobs:
        key = build(c, i, vid)
        p = args.out / f"injected_{vid}.json"
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            if any(x.get("review") != "auto" for x in old.get("injections", [])):
                print(f"{p} has hand-reviewed items; not overwritten (delete it to rebuild)")
                continue
        p.write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{vid}: {len(key['injections'])} changed sentences → {p}")
        for x in key["injections"]:
            print(f"  {x['id']} {x['type']}: {x['match_strings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

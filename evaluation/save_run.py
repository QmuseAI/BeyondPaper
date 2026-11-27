#!/usr/bin/env python3
"""Save one BeyondPaper run locally as results/<run_id>/findings.json + meta.json.

Input can be any of:
  - text copied from a SciencePal page or terminal, containing the
    <<<BP_FINDINGS_JSON>>> ... <<<END_BP_FINDINGS_JSON>>> block or a `cat bp_out/findings.json` dump;
  - bp_out/findings.json downloaded from the SciencePal workspace;
  - a bare JSON array of findings;
  - a page export {"variant", "url", "text"} (downloads/Bxx.json).

The largest parseable JSON object/array that contains findings wins; the last
marker block wins over earlier ones (earlier blocks are often the prompt quoting the markers).

Usage:
  python save_run.py <input> --run-id B10-v18 --paper B10 --variant injected --version v1.9 --out results
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

START, END = "<<<BP_FINDINGS_JSON>>>", "<<<END_BP_FINDINGS_JSON>>>"
SEVERITIES = ("red", "yellow", "grey", "green")


def is_findings_list(x) -> bool:
    return isinstance(x, list) and bool(x) and all(isinstance(f, dict) for f in x) \
        and sum(("severity" in f and "type" in f) for f in x) >= max(1, len(x) // 2)


def normalise(x) -> dict | None:
    """Return a findings.json-like dict, or None if x holds no findings."""
    if isinstance(x, dict):
        if is_findings_list(x.get("findings")):
            return x
        # page export: {"variant", "url", "text"}
        if isinstance(x.get("text"), str):
            return extract_from_text(x["text"])
        return None
    if is_findings_list(x):
        return {"findings": x}
    return None


def raw_decode_all(text: str):
    """Yield every JSON value that starts at a '{' or '[' and parses (largest first per start)."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            val, end = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        yield m.start(), end, val


def repair_truncated(chunk: str):
    """Last resort for a block cut off mid-way: drop findings after the last complete one."""
    i = chunk.find('"findings"')
    if i < 0:
        return None
    j = chunk.find("[", i)
    dec = json.JSONDecoder()
    items, pos = [], j + 1
    while True:
        while pos < len(chunk) and chunk[pos] in " \r\n\t,":
            pos += 1
        if pos >= len(chunk) or chunk[pos] != "{":
            break
        try:
            val, pos = dec.raw_decode(chunk, pos)
        except json.JSONDecodeError:
            break
        items.append(val)
    return {"findings": items, "_truncated": True} if is_findings_list(items) else None


def extract_from_text(text: str) -> dict | None:
    # 1. marker blocks, last one first
    blocks = []
    pos = 0
    while True:
        s = text.find(START, pos)
        if s < 0:
            break
        e = text.find(END, s)
        blocks.append(text[s + len(START): e if e >= 0 else len(text)])
        pos = s + len(START)
    for chunk in reversed(blocks):
        chunk = chunk.strip().strip("`").strip()
        if chunk.startswith("json"):
            chunk = chunk[4:]
        try:
            got = normalise(json.loads(chunk))
            if got:
                return got
        except json.JSONDecodeError:
            pass
        for _, _, val in raw_decode_all(chunk):
            got = normalise(val)
            if got:
                return got
        got = repair_truncated(chunk)
        if got:
            return got
    # 2. any JSON in the text: keep the one with most findings
    best = None
    for _, _, val in raw_decode_all(text):
        got = normalise(val) if not isinstance(val, dict) or "text" not in val else None
        if got and (best is None or len(got["findings"]) > len(best["findings"])):
            best = got
    if best:
        return best
    return repair_truncated(text)


def load_any(path: Path) -> dict | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        got = normalise(json.loads(raw))
        if got:
            return got
    except json.JSONDecodeError:
        pass
    return extract_from_text(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--paper", required=True, help="benchmark id, e.g. B10 or B12")
    ap.add_argument("--variant", required=True, choices=["clean", "injected", "blind"])
    ap.add_argument("--version", default="", help="skill/prompt version at start, e.g. v1.9")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--force", action="store_true", help="overwrite an existing findings.json")
    args = ap.parse_args()

    data = load_any(args.input)
    if not data:
        print(f"no findings found in {args.input}", file=sys.stderr)
        return 1
    findings = data["findings"]
    run_dir = args.out / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    fpath = run_dir / "findings.json"
    if fpath.exists() and not args.force:
        print(f"{fpath} exists; use --force to overwrite", file=sys.stderr)
        return 1
    fpath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = Counter(f.get("severity", "?") for f in findings)
    meta = {
        "run_id": args.run_id, "paper_id": args.paper, "variant": args.variant, "version": args.version,
        "saved": dt.date.today().isoformat(), "source_file": str(args.input), "note": args.note,
        "n_findings": len(findings), "truncated": bool(data.get("_truncated")),
        "counts": {s: counts.get(s, 0) for s in SEVERITIES},
        "counts_reported": data.get("counts"),
        "reproduction": (data.get("reproduction") or {}).get("level") if isinstance(data.get("reproduction"), dict)
        else data.get("reproduction"),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    c = meta["counts"]
    print(f"{args.run_id}: {len(findings)} findings · red {c['red']} · yellow {c['yellow']} · grey {c['grey']} · "
          f"green {c['green']}{' · TRUNCATED block' if meta['truncated'] else ''} → {fpath}")
    if data.get("counts") and isinstance(data["counts"], dict):
        rep = {k: data["counts"].get(k) for k in SEVERITIES if k in data["counts"]}
        if rep and any(rep[k] != c[k] for k in rep):
            print(f"  note: counts field in the file says {rep} (findings list may be filtered or truncated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

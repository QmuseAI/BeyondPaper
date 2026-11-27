#!/usr/bin/env python3
"""Validate a BeyondPaper claim ledger and derive traceability findings.

Usage:
    python validate_ledger.py bp_out/ledger.json --root bp_work --out bp_out

Outputs (in --out):
    ledger_graph.mmd        mermaid graph claim -> evidence -> artifact
    ledger.findings.json    unsupported / unverifiable claims, missing artifacts
Exit code 1 if the ledger is structurally broken (bad references, missing keys).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "ledger.schema.json"
DATA_KINDS = {"data", "table", "supplement"}
CODE_KINDS = {"code", "notebook"}


def finding(ftype, severity, location, evidence, claim_ids=None, question=None):
    return {
        "module": "ledger",
        "type": ftype,
        "severity": severity,
        "claim_ids": claim_ids or [],
        "location": location,
        "evidence": evidence,
        "source": "script:validate_ledger.py",
        "question_for_authors": question,
    }


def schema_errors(ledger: dict) -> list[str]:
    """Validate with jsonschema if installed; otherwise return an empty list."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in validator.iter_errors(ledger)]


def structural_errors(ledger: dict) -> list[str]:
    errors = []
    for key in ("paper", "claims", "evidence", "artifacts"):
        if key not in ledger:
            errors.append(f"missing top-level key '{key}'")
    if errors:
        return errors
    for section in ("claims", "evidence", "artifacts", "reported_values"):
        ids = [item.get("id") for item in ledger.get(section, [])]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            errors.append(f"duplicate ids in {section}: {sorted(dup)}")
        if None in ids:
            errors.append(f"an item in {section} has no id")
    ev_ids = {e["id"] for e in ledger["evidence"]}
    art_ids = {a["id"] for a in ledger["artifacts"]}
    claim_ids = {c["id"] for c in ledger["claims"]}
    for c in ledger["claims"]:
        for e in c.get("evidence", []):
            if e not in ev_ids:
                errors.append(f"claim {c['id']} references unknown evidence {e}")
    for e in ledger["evidence"]:
        for a in e.get("artifacts", []):
            if a not in art_ids:
                errors.append(f"evidence {e['id']} references unknown artifact {a}")
    for v in ledger.get("reported_values", []):
        for c in v.get("claim_ids", []):
            if c not in claim_ids:
                errors.append(f"reported value {v.get('id')} references unknown claim {c}")
    return errors


def same_stem(root: Path, path: str) -> list[str]:
    """Files under root with the same name stem as path (ledger path written with the wrong folder or extension)."""
    stem = Path(path).stem.lower()
    if not stem:
        return []
    try:
        return sorted(str(f.relative_to(root)) for f in root.rglob("*")
                      if f.is_file() and f.stem.lower() == stem)
    except OSError:
        return []


def trace_findings(ledger: dict, root: Path | None) -> list[dict]:
    out = []
    evidence = {e["id"]: e for e in ledger["evidence"]}
    artifacts = {a["id"]: a for a in ledger["artifacts"]}

    for a in ledger["artifacts"]:
        if not a.get("available", False):
            out.append(finding(
                "missing_artifact", "yellow", a.get("path") or a.get("url") or a["id"],
                f"Artifact {a['id']} ({a.get('kind')}) is referred to by the paper but not provided.",
                question=f"Can the {a.get('kind')} '{a.get('path') or a['id']}' be shared?"))
        elif root is not None and a.get("path") and not (root / a["path"]).exists():
            near = same_stem(root, a["path"])
            if near:
                print(f"PATH FIX: artifact {a['id']} '{a['path']}' not found; same file name at "
                      f"{', '.join(near[:3])}. Correct the path in the ledger (not reported as missing).")
                continue
            out.append(finding(
                "missing_artifact", "yellow", a["path"],
                f"Artifact {a['id']} is marked available but was not found under {root}."))

    for c in ledger["claims"]:
        ev = [evidence[e] for e in c.get("evidence", []) if e in evidence]
        if not ev:
            out.append(finding(
                "unsupported_claim", "yellow", c.get("location", ""),
                f"Claim {c['id']} has no evidence linked to it: \"{c['text'][:160]}\"",
                [c["id"]], "Which experiment, figure or table supports this claim?"))
            continue
        arts = [artifacts[a] for e in ev for a in e.get("artifacts", []) if a in artifacts]
        usable = [a for a in arts if a.get("available")]
        has_data = any(a.get("kind") in DATA_KINDS for a in usable)
        has_code = any(a.get("kind") in CODE_KINDS for a in usable)
        if not (has_data and has_code):
            missing = [k for k, ok in (("data", has_data), ("code", has_code)) if not ok]
            out.append(finding(
                "unverifiable_claim", "grey", c.get("location", ""),
                f"Claim {c['id']} cannot be re-computed: no available {' or '.join(missing)} behind its evidence.",
                [c["id"]]))
    return out


def mermaid(ledger: dict) -> str:
    def label(text: str, n: int = 48) -> str:
        text = " ".join(str(text).split())
        text = text if len(text) <= n else text[: n - 1] + "…"
        return text.replace('"', "'")

    lines = ["flowchart LR"]
    for c in ledger["claims"]:
        lines.append(f'  {c["id"]}["{c["id"]}: {label(c["text"])}"]')
        for e in c.get("evidence", []):
            lines.append(f"  {c['id']} --> {e}")
    for e in ledger["evidence"]:
        lines.append(f'  {e["id"]}["{e["id"]}: {label(e.get("location", e.get("kind", "")))}"]')
        for a in e.get("artifacts", []):
            lines.append(f"  {e['id']} --> {a}")
    missing = []
    for a in ledger["artifacts"]:
        lines.append(f'  {a["id"]}["{a["id"]}: {label(a.get("path") or a.get("url") or a.get("kind"))}"]')
        if not a.get("available", False):
            missing.append(a["id"])
    lines.append("  classDef missing stroke-dasharray: 4 3")
    if missing:
        lines.append(f"  class {','.join(missing)} missing")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--root", type=Path, default=None, help="folder that artifact paths are relative to")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    errors = structural_errors(ledger) + schema_errors(ledger)
    if errors:
        print("Ledger is not valid:", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    findings = trace_findings(ledger, args.root)
    (args.out / "ledger.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "ledger_graph.mmd").write_text(mermaid(ledger), encoding="utf-8")

    n_claims = len(ledger["claims"])
    print(f"claims={n_claims} evidence={len(ledger['evidence'])} artifacts={len(ledger['artifacts'])} "
          f"reported_values={len(ledger.get('reported_values', []))} findings={len(findings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

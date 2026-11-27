"""Plain-text copies of the authors' stored outputs, for grep in stage 6a (v1.11.16).

Knitted HTML, notebooks and PDFs hold the stored numbers but are hard to grep: one HTML line can be 200 kB,
and notebook outputs sit inside JSON. B03-v11115p2 spent a whole turn guessing character offsets in
results/main.html. This script writes one text file per stored output, one line per paragraph / table row /
output line, so a check is a single `grep -n` on bp_work/checks/archive_txt/.

Usage:
    python archive_text.py bp_work                # writes bp_work/checks/archive_txt/*.txt and lists them
    python archive_text.py bp_work --grep 1.42536  # same, then grep -n the pattern in every text file
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"bp_out", "checks", "__pycache__", ".git", "node_modules", "skills", "venv", ".venv"}
EXT = {".html", ".htm", ".ipynb", ".pdf", ".tex", ".log", ".out", ".Rout", ".md"}
BLOCK = re.compile(r"(?i)</?(?:p|div|tr|li|h[1-6]|pre|br|table|thead|tbody|section|code)\b[^>]*>")


def html_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<img\b[^>]*>", " ", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    raw = BLOCK.sub("\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def ipynb_text(raw: str) -> str:
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    out = []
    for k, cell in enumerate(nb.get("cells", []), start=1):
        src = "".join(cell.get("source", [])) if isinstance(cell.get("source"), list) else str(cell.get("source", ""))
        out.append(f"## cell {k} [{cell.get('cell_type')}]")
        out.extend("   " + ln for ln in src.splitlines())
        for o in cell.get("outputs", []) or []:
            txt = o.get("text") or (o.get("data") or {}).get("text/plain") or ""
            if isinstance(txt, list):
                txt = "".join(txt)
            if not txt and (o.get("data") or {}).get("text/html"):
                h = o["data"]["text/html"]
                txt = html_text("".join(h) if isinstance(h, list) else h)
            out.extend("=> " + ln for ln in str(txt).splitlines())
    return "\n".join(out)


def pdf_text(path: Path) -> str:
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
    try:
        import fitz  # PyMuPDF
        with fitz.open(path) as doc:
            return "\n".join(p.get_text() for p in doc)
    except Exception:
        return ""


def convert(root: Path, dest: Path) -> list[tuple[Path, Path, int]]:
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in EXT or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.suffix == ".md" and p.name in ("paper.md",):
            continue
        if p.stat().st_size > 60_000_000:
            continue
        try:
            if p.suffix == ".pdf":
                txt = pdf_text(p)
            else:
                raw = p.read_text(encoding="utf-8", errors="replace")
                txt = html_text(raw) if p.suffix in (".html", ".htm") else ipynb_text(raw) if p.suffix == ".ipynb" else raw
        except Exception as e:  # keep going: one unreadable file must not stop the rest
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        if not txt.strip():
            continue
        name = "__".join(p.relative_to(root).parts) + ".txt"
        out = dest / name
        out.write_text(txt, encoding="utf-8")
        done.append((p, out, txt.count("\n") + 1))
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work", nargs="?", default="bp_work")
    ap.add_argument("--grep", help="pattern (regular expression) to search in every text file, with line numbers")
    a = ap.parse_args()
    root = Path(a.work)
    dest = root / "checks" / "archive_txt"
    done = convert(root, dest)
    print(f"{len(done)} stored output(s) as text in {dest}:")
    for src, out, n in done[:60]:
        print(f"  {out.name}  ({n} lines)  <- {src}")
    if a.grep:
        rx = re.compile(a.grep)
        hits = 0
        for _, out, _ in done:
            for i, ln in enumerate(out.read_text(encoding="utf-8").splitlines(), start=1):
                if rx.search(ln):
                    print(f"{out.name}:{i}: {ln[:300]}")
                    hits += 1
                    if hits >= 200:
                        print("... (200 hits shown)")
                        return 0
        if not hits:
            print(f"no line matches {a.grep!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

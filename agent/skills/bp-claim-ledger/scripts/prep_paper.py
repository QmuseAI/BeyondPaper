#!/usr/bin/env python3
"""Prepare a paper and its materials for stage 1 in one call, so the agent reads short files
instead of paging through the whole paper and repository command by command.

Writes into --out (default bp_work/prep):
  sections/NN_<slug>.md   the paper split at its headings (long sections split into parts)
  index.md                section list with sizes + materials inventory + stored-output candidates
  numbers.csv             every number in the paper with section, line and context
                          (candidates for ledger.json → reported_values)
  methods_params.csv      sentences in Methods-like sections that state a parameter
  inventory.csv           every file under the materials root: path, kind, language, size
  stored_outputs.csv      files that look like the authors' stored outputs (for the reproduction gate)

Usage:
    python prep_paper.py bp_work/paper.md --materials bp_work [--out bp_work/prep] [--max-section-chars 8000]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
PAGE = re.compile(r"<!--\s*page\s*(\d+)\s*-->", re.I)
NUM = re.compile(r"(?<![\w.])([-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d*\.\d+(?:[eE][-+]?\d+)?|[-−]?\d+(?:[eE][-+]?\d+)?)(\s*(?:%|‰|[a-zA-Zµμ°/]+(?:/[a-zA-Z]+)?))?")
STAT = re.compile(r"\b(p|P|t|F|r|z|χ2|chi2|OR|HR|RR|SMD|AUC|AUROC|CI|SD|SE|n|N|M|I2|I²|R2|R²|β|b|d)\s*(?:\([^)]{0,12}\))?\s*[=<>≤≥]\s*[-−]?\.?\d")
PARAM = re.compile(r"(seed|learning rate|epoch|batch|fold|split|threshold|cut-?off|alpha|lambda|penalt|regulari|iteration|bootstrap|"
                   r"version|package|software|R \d|python|significance|α|exclude|exclusion|criteria|imput|normali[sz]|"
                   r"standardi[sz]|random_state|n_estimators|max_depth|k\s*=|tolerance|consensus|\d+\s*%)", re.I)
METHOD_SEC = re.compile(r"method|material|procedure|analys|statistic|experimental|design|participants|data", re.I)
CODE_EXT = {".py": "python", ".r": "R", ".rmd": "R", ".qmd": "R/python", ".ipynb": "notebook", ".jl": "julia",
            ".m": "matlab", ".do": "stata", ".sas": "sas", ".sh": "shell", ".cpp": "c++", ".c": "c"}
DATA_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet", ".h5", ".h5ad", ".rds", ".rdata", ".sav", ".dta", ".txt", ".npy", ".npz", ".mat"}
OUTPUT_HINT = re.compile(r"(^|/)(results?|outputs?|figures?|figs|tables?|logs?|reports?|knit|rendered|final)(/|$)|\.(html|log|out)$", re.I)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "section"


def split_sections(lines: list[str]):
    """Return list of (title, start_line, end_line) using markdown headings; preamble is 'front'."""
    marks = [(i, HEADING.match(l).group(2)) for i, l in enumerate(lines) if HEADING.match(l)]
    if not marks or marks[0][0] > 0:
        marks = [(0, "front")] + marks
    out = []
    for k, (i, title) in enumerate(marks):
        j = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        out.append((title, i, j))
    return out


def flatten_notebook(path: Path, rel: str, max_lines: int = 25, max_chars: int = 2500) -> str:
    """Code cells numbered from 1 (code cells only), each followed by its outputs, cut to a readable size."""
    nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    join = lambda x: "".join(x) if isinstance(x, list) else (x or "")
    lines = [f"# {rel}", "", "Code cells are numbered from 1, counting code cells only. Long outputs are cut; "
             "the omitted size is stated.", ""]
    k = 0
    for c in nb.get("cells", []):
        if c.get("cell_type") == "markdown":
            text = join(c.get("source")).strip()
            if text:
                lines += ["> " + l for l in text.splitlines()[:6]] + [""]
            continue
        if c.get("cell_type") != "code":
            continue
        k += 1
        lines += [f"## Code cell {k}" + (f" (execution count {c.get('execution_count')})" if c.get("execution_count") else ""),
                  "", "```python", join(c.get("source")).rstrip(), "```"]
        texts, images = [], 0
        for o in c.get("outputs", []):
            t = o.get("output_type")
            if t == "stream":
                texts.append(join(o.get("text")))
            elif t in ("execute_result", "display_data"):
                data = o.get("data", {})
                if "text/plain" in data:
                    texts.append(join(data["text/plain"]) + "\n")
                elif any(k_.startswith("image/") for k_ in data):
                    images += 1
            elif t == "error":
                texts.append(f"ERROR {o.get('ename')}: {o.get('evalue')}\n")
        text = "".join(texts)
        if text.strip():
            out_lines = text.rstrip().splitlines()
            shown = "\n".join(out_lines[:max_lines])[:max_chars]
            note = ""
            if len(out_lines) > max_lines or len(text) > max_chars:
                tail = "\n".join(out_lines[-5:])[-800:]
                note = (f"\n[... output cut: {len(out_lines):,} lines, {len(text):,} characters in total. Last lines:]\n"
                        + tail)
            lines += ["", "Output:", "```", shown + note, "```"]
        if images:
            lines += ["", f"[{images} image output(s)]"]
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper", type=Path)
    ap.add_argument("--materials", type=Path, default=None, help="folder with code/data/results (e.g. bp_work)")
    ap.add_argument("--out", type=Path, default=Path("bp_work/prep"))
    ap.add_argument("--max-section-chars", type=int, default=8000)
    args = ap.parse_args()

    text = args.paper.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    out = args.out
    (out / "sections").mkdir(parents=True, exist_ok=True)
    for old in (out / "sections").glob("*.md"):
        old.unlink()

    # page number per line
    page, pages = None, []
    for l in lines:
        m = PAGE.search(l)
        if m:
            page = int(m.group(1))
        pages.append(page)

    index_rows, files = [], []
    n = 0
    for title, i, j in split_sections(lines):
        body = "\n".join(lines[i:j]).strip()
        if not body:
            continue
        parts = [body]
        if len(body) > args.max_section_chars:  # split long sections at paragraph breaks
            parts, cur = [], ""
            for para in body.split("\n\n"):
                if cur and len(cur) + len(para) > args.max_section_chars:
                    parts.append(cur)
                    cur = ""
                cur += para + "\n\n"
            parts.append(cur)
        for p, chunk in enumerate(parts, 1):
            n += 1
            name = f"{n:02d}_{slug(title)}" + (f"_part{p}" if len(parts) > 1 else "") + ".md"
            (out / "sections" / name).write_text(f"<!-- lines {i + 1}-{j} · page {pages[i] if i < len(pages) else '?'} -->\n"
                                                 + chunk.strip() + "\n", encoding="utf-8")
            files.append(name)
            index_rows.append((name, title, i + 1, j, len(chunk)))

    # numbers and statistics with context
    sec_of_line = {}
    for title, i, j in split_sections(lines):
        for k in range(i, j):
            sec_of_line[k] = title
    num_rows = []
    for k, l in enumerate(lines):
        if HEADING.match(l) or PAGE.search(l):
            continue
        for m in NUM.finditer(l):
            raw = m.group(1)
            if re.fullmatch(r"(19|20)\d\d", raw) and not m.group(2):
                continue  # years
            a, b = max(0, m.start() - 70), min(len(l), m.end() + 50)
            num_rows.append({"section": sec_of_line.get(k, ""), "line": k + 1, "page": pages[k],
                             "value": raw.replace("−", "-").replace(",", ""), "unit_or_suffix": (m.group(2) or "").strip(),
                             "is_statistic": bool(STAT.search(l[max(0, m.start() - 20): m.end()])),
                             "context": l[a:b].replace("\n", " ").strip()})
    with open(out / "numbers.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["section", "line", "page", "value", "unit_or_suffix", "is_statistic", "context"])
        w.writeheader()
        w.writerows(num_rows)

    # parameter sentences in method-like sections
    param_rows = []
    for title, i, j in split_sections(lines):
        if not METHOD_SEC.search(title):
            continue
        block = " ".join(lines[i:j])
        for sent in re.split(r"(?<=[.;])\s+", block):
            if PARAM.search(sent) and re.search(r"\d", sent) and 20 < len(sent) < 600:
                param_rows.append({"section": title, "sentence": sent.strip()})
    with open(out / "methods_params.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["section", "sentence"])
        w.writeheader()
        w.writerows(param_rows)

    # materials inventory and stored-output candidates
    inv, stored, langs = [], [], {}
    if args.materials and args.materials.exists():
        root = args.materials
        skip = {out.resolve()}
        for p in sorted(root.rglob("*")):
            if not p.is_file() or any(s in p.resolve().parents for s in skip) or "/." in str(p) or "__pycache__" in str(p):
                continue
            rel = str(p.relative_to(root))
            ext = p.suffix.lower()
            kind = "code" if ext in CODE_EXT else ("data" if ext in DATA_EXT else ("figure" if ext in {".png", ".jpg", ".jpeg", ".pdf", ".svg", ".tif", ".tiff"} else "other"))
            lang = CODE_EXT.get(ext, "")
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
            size = p.stat().st_size
            inv.append({"path": rel, "kind": kind, "language": lang, "bytes": size})
            if OUTPUT_HINT.search(rel) and kind != "code":
                stored.append({"path": rel, "kind": kind, "bytes": size})
            elif ext == ".ipynb":
                try:
                    has_out = '"outputs": [\n' in p.read_text(encoding="utf-8", errors="ignore") or '"output_type"' in p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    has_out = False
                if has_out:
                    stored.append({"path": rel, "kind": "notebook with outputs", "bytes": size})
    # notebooks flattened to markdown: numbered code cells, outputs cut to a readable size
    nb_files = []
    if args.materials and args.materials.exists():
        nb_dir = out / "notebooks"
        for p in sorted(args.materials.rglob("*.ipynb")):
            if any(s in p.resolve().parents for s in {out.resolve()}) or ".ipynb_checkpoints" in str(p):
                continue
            rel = str(p.relative_to(args.materials))
            try:
                md_nb = flatten_notebook(p, rel)
            except (ValueError, OSError) as e:
                md_nb = f"# {rel}\n\nCould not read notebook: {e}\n"
            nb_dir.mkdir(parents=True, exist_ok=True)
            target = nb_dir / (re.sub(r"[^\w.-]+", "__", rel) + ".md")
            target.write_text(md_nb, encoding="utf-8")
            nb_files.append((rel, target.name, md_nb.count("\n## Code cell ")))
    with open(out / "inventory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "kind", "language", "bytes"])
        w.writeheader()
        w.writerows(inv)
    with open(out / "stored_outputs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "kind", "bytes"])
        w.writeheader()
        w.writerows(stored)

    md = [f"# Prep index · {args.paper.name}", "",
          f"{len(lines)} lines, {len(text)} characters, {len(files)} section files, {len(num_rows)} numbers "
          f"({sum(r['is_statistic'] for r in num_rows)} next to a statistic), {len(param_rows)} parameter sentences.", "",
          "## Sections (read these files, not the whole paper)", "", "| File | Heading | Lines | Chars |", "|---|---|---|---|"]
    md += [f"| sections/{f} | {t[:60]} | {a}-{b} | {c} |" for f, t, a, b, c in index_rows]
    md += ["", "## Materials", ""]
    if inv:
        kinds = {}
        for r in inv:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        md += [f"- files: {len(inv)} ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})",
               f"- code languages: {', '.join(f'{k} ({v})' for k, v in sorted(langs.items())) or 'none found'}",
               f"- stored-output candidates (authors' results, logs, knitted reports): {len(stored)} — see stored_outputs.csv; "
               "use them as `archived` values in recomputed.json"]
        if any(l.startswith("R") for l in langs) :
            md.append("- R code present: check `R --version`; if R is missing, compare with stored outputs first and mark ports `substitute: true`")
    else:
        md.append("- no materials folder given")
    if nb_files:
        md += ["", "## Notebooks (read these, never cat or sed the .ipynb: raw notebooks can hold megabytes of output)", "",
               "| Notebook | Flattened file | Code cells |", "|---|---|---|"]
        md += [f"| {r} | notebooks/{t} | {n} |" for r, t, n in nb_files]
    md += ["", "Files: numbers.csv, methods_params.csv, inventory.csv, stored_outputs.csv"
           + (", notebooks/*.md" if nb_files else "")]
    (out / "index.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"sections={len(files)} numbers={len(num_rows)} params={len(param_rows)} files={len(inv)} "
          f"stored_outputs={len(stored)} -> {out / 'index.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

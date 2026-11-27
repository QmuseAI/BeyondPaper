#!/usr/bin/env python3
"""Checks on the released data and result files themselves (not the code).

  duplicate_output  two tables with different file names hold the same content
                    (e.g. the "external test" predictions are a copy of the "test" predictions)
  split_overlap     a file named as test / validation / external data shares rows with a file that is
                    not named as test data (training, discovery, full cohort); if feature selection or
                    tuning read that file, the test samples took part in it

  header_row_dropped  a csv whose first line is already data (all numeric) is read by the code with the
                    default header setting (pandas read_csv, R read.csv / read_csv), so the first
                    observation becomes the column names and every count is one short
                    (B14-v191: 61 validation rows scored as 60, 221 training rows as 220)

Rows are compared on the columns both files share (numbers rounded to 6 decimals), so an index column or
re-ordered columns do not hide an overlap. Reads csv, tsv, txt (tab or comma), xlsx and xls (first sheet).
Requires pandas (and openpyxl for xlsx). Files over --max-mb are skipped.

Usage:
    python data_checks.py bp_work --out bp_out
Outputs: data_checks.csv, data.findings.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

TABLE_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}
TEST_WORD = re.compile(r"(?<![a-z])(test|testing|valid|validation|holdout|hold[-_ ]out|external|replication)(?![a-z])"
                       r"|(xtest|ytest|x_test|y_test|xval|x_val)", re.I)
ROLE_WORDS = {"train", "training", "test", "testing", "valid", "validation", "external", "internal", "discovery",
              "holdout", "derivation", "development", "replication", "xtest", "ytest", "xtrain", "ytrain",
              "pre", "post", "before", "after", "baseline", "followup", "case", "cases", "control", "controls"}
ID_COL = re.compile(r"(?i)^(unnamed: ?\d+|index|id|row|rownames|x|\.\.\.1)$")
SKIP_DIRS = {"bp_out", "prep", "__pycache__", ".git", "node_modules"}


def mk(ftype, severity, location, evidence, question=None):
    return {"module": "ghost", "type": ftype, "severity": severity, "claim_ids": [], "location": location,
            "evidence": evidence, "source": "script:data_checks.py", "question_for_authors": question}


def roles(rel: str) -> frozenset:
    return frozenset(set(re.split(r"[^a-z0-9]+", Path(rel).stem.lower())) & ROLE_WORDS)


def read_table(p: Path):
    import pandas as pd
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(p)
    if ext == ".tsv":
        return pd.read_csv(p, sep="\t", low_memory=False)
    with open(p, encoding="utf-8", errors="ignore") as fh:
        head = fh.readline()
    sep = "\t" if head.count("\t") > head.count(",") else ","
    if head.count(sep) == 0:
        return None
    return pd.read_csv(p, sep=sep, low_memory=False, encoding_errors="ignore")


def norm(df):
    import pandas as pd
    df = df.loc[:, [c for c in df.columns if not ID_COL.match(str(c).strip())]]
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].round(6)
    return out


def content_hash(df) -> str:
    import pandas as pd
    h = pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values
    return hashlib.md5(h.tobytes() + "|".join(map(str, df.columns)).encode()).hexdigest()


def row_hashes(df, cols) -> list[int]:
    import pandas as pd
    return list(pd.util.hash_pandas_object(df[cols].reset_index(drop=True), index=False).values)


CODE_EXT = {".py", ".r", ".rmd", ".qmd", ".ipynb"}
NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
READ_CALL = re.compile(r"(read_csv|read_table|read\.csv|read\.table|read_delim|read_tsv|fread|loadtxt|genfromtxt)\s*\(")


def code_texts(root: Path) -> dict[str, list[str]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in CODE_EXT or any(s in p.parts for s in SKIP_DIRS):
            continue
        try:
            if p.suffix.lower() == ".ipynb":
                nb = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                src = "\n".join("".join(c.get("source", "")) if isinstance(c.get("source"), list) else c.get("source", "")
                                for c in nb.get("cells", []) if c.get("cell_type") == "code")
            else:
                src = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError, AttributeError):
            continue
        out[str(p.relative_to(root))] = src.splitlines()
    return out


def header_row_dropped(root: Path, max_mb: float) -> list[dict]:
    """csv files without a header line that the code reads as if the first line were a header."""
    code = code_texts(root)
    found = []
    for p in sorted(root.rglob("*")):
        if (not p.is_file() or p.suffix.lower() not in (".csv", ".txt", ".tsv") or any(s in p.parts for s in SKIP_DIRS)
                or p.stat().st_size == 0 or p.stat().st_size > max_mb * 1e6):
            continue
        with open(p, encoding="utf-8-sig", errors="ignore") as fh:
            first = fh.readline().strip()
            n_lines = 1 + sum(1 for line in fh if line.strip())
        sep = "\t" if first.count("\t") > first.count(",") else ","
        cells = [c.strip().strip('"') for c in first.split(sep)]
        if len(cells) < 2 or not all(NUMERIC.match(c) for c in cells):
            continue
        name = p.name
        for path, lines in code.items():
            for k, line in enumerate(lines, 1):
                if name not in line and p.stem not in line:
                    continue
                window = " ".join(lines[max(0, k - 2):k + 2])
                m = READ_CALL.search(window)
                if not m:
                    continue
                fn = m.group(1)
                if fn in ("loadtxt", "genfromtxt", "fread"):
                    continue  # numpy loadtxt/genfromtxt and data.table::fread do not take the first line as names here
                if re.search(r"header\s*=\s*(None|FALSE|F\b|False|0\b)|col_names\s*=\s*(FALSE|F\b)|names\s*=", window):
                    continue
                found.append(mk("sample_size", "yellow", f"{path}:{k}",
                                f"The first line of {p.relative_to(root)} is already data ({first[:60]}), but {path}:{k} reads it "
                                f"with {fn}() and the default header setting, so that observation becomes the column names. "
                                f"The file has {n_lines} data lines; the code sees {n_lines - 1}. Every count, metric and "
                                "split built on this table is one row short.",
                                f"Is the first row of {name} meant to be data? With the default header setting it is dropped."))
                break
            else:
                continue
            break
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="materials folder (data, results, code)")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    ap.add_argument("--max-mb", type=float, default=40)
    ap.add_argument("--min-shared-cols", type=int, default=3)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        import pandas  # noqa: F401
    except ImportError:
        (a.out / "data.findings.json").write_text("[]", encoding="utf-8")
        print("pandas not installed: data checks skipped")
        return 0

    tables, errors = {}, []
    for p in sorted(a.root.rglob("*")):
        if (not p.is_file() or p.suffix.lower() not in TABLE_EXT or any(s in p.parts for s in SKIP_DIRS)
                or any(part.startswith(".") for part in p.relative_to(a.root).parts)
                or p.stat().st_size > a.max_mb * 1e6 or p.stat().st_size == 0):
            continue
        rel = str(p.relative_to(a.root))
        try:
            df = read_table(p)
        except Exception as e:  # unreadable file: note it, keep going
            errors.append(f"{rel}: {type(e).__name__}")
            continue
        if df is None:
            continue
        df = norm(df)
        if df.shape[0] < 3 or df.shape[1] < 2:
            continue
        tables[rel] = df

    rows, findings = [], []

    # 1. identical content under different file names
    by_hash = {}
    for rel, df in tables.items():
        try:
            by_hash.setdefault(content_hash(df), []).append(rel)
        except Exception as e:  # unhashable cell types: skip this table
            errors.append(f"{rel}: {type(e).__name__}")
    for group in by_hash.values():
        role_sets = {roles(r) for r in group if roles(r)}
        if len(group) > 1 and len(role_sets) > 1:
            shape = tables[group[0]].shape
            rows.append({"check": "duplicate_output", "file_a": group[0], "file_b": "; ".join(group[1:]),
                         "detail": f"{shape[0]}x{shape[1]} identical"})
            findings.append(mk("duplicate_output", "yellow", " = ".join(group),
                               f"{len(group)} files whose names describe different data "
                               f"({' / '.join(sorted({'+'.join(sorted(r)) for r in role_sets}))}) have identical content "
                               f"({shape[0]} rows x {shape[1]} columns): {', '.join(group)}. One of them is the wrong file.",
                               "Which of these files holds the intended content?"))

    # 2. test-named file whose rows sit inside a larger non-test file (one finding per larger file)
    test_files = [r for r in tables if TEST_WORD.search(Path(r).stem)]
    other_files = [r for r in tables if r not in test_files]
    hits: dict[str, list[tuple[str, int, int, int]]] = {}
    for t in test_files:
        dt = tables[t]
        for o in other_files:
            do = tables[o]
            cols = [c for c in dt.columns if c in do.columns]
            if len(cols) < a.min_shared_cols or len(do) <= len(dt):
                continue
            if dt[cols].drop_duplicates().shape[0] < 0.5 * len(dt):
                continue
            try:
                ht = set(row_hashes(dt, cols))
                ho = set(row_hashes(do, cols))
            except Exception:
                continue
            shared = len(ht & ho)
            if shared >= 3 and shared >= 0.05 * len(ht):
                hits.setdefault(o, []).append((t, shared, len(ht), len(cols)))
                rows.append({"check": "split_overlap", "file_a": t, "file_b": o,
                             "detail": f"{shared}/{len(ht)} test rows found in {o} ({len(do)} rows), {len(cols)} shared columns"})
    for o, lst in hits.items():
        lst.sort(key=lambda x: (-x[1], len(x[0])))
        t, shared, n, ncols = lst[0]
        also = f" (also in {len(lst) - 1} other test-named file(s): {', '.join(x[0] for x in lst[1:4])})" if len(lst) > 1 else ""
        findings.append(mk("split_overlap", "yellow", f"{o} contains {t}",
                           f"{o} ({len(tables[o])} rows) contains {shared} of the {n} rows of {t}{also}; compared on "
                           f"{ncols} shared columns. If {o} was used for feature selection, tuning or preprocessing, "
                           "the test samples took part in it. Check which file each step of the code reads.",
                           f"Was {Path(o).name} (which contains the test samples) used for feature selection or tuning?"))

    # 3. headerless csv read with a header (first observation lost)
    for f in header_row_dropped(a.root, a.max_mb):
        findings.append(f)
        rows.append({"check": "header_row_dropped", "file_a": f["location"], "file_b": "", "detail": f["evidence"][:160]})

    with open(a.out / "data_checks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["check", "file_a", "file_b", "detail"])
        w.writeheader()
        w.writerows(rows)
    (a.out / "data.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tables read={len(tables)} (test-named {len(test_files)}) duplicates="
          f"{sum(f['type'] == 'duplicate_output' for f in findings)} overlaps={sum(f['type'] == 'split_overlap' for f in findings)} "
          f"header_rows_dropped={sum(f['type'] == 'sample_size' for f in findings)}"
          + (f" unreadable={len(errors)}" if errors else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

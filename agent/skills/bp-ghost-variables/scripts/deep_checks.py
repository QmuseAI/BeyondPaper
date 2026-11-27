#!/usr/bin/env python3
"""Deterministic checks for problems that hand-written review finds in one run and misses in the next (v1.11).

Each check is a script, so its finding appears in every run. It also writes `bp_out/code_facts.json`, which
`build_passport.py` uses to test hand-written findings against the materials.

Checks
  r_trace          R/Rmd: follows `read.csv` -> column drops -> `colnames` -> index slices -> model formulas and
                   lists data columns that never enter any model (B13: `covariates[-1]` and
                   `covariates[2:length(covariates)]` skip Age and Gender; 39 of 41 features tested)
  post_baseline    covariates measured after baseline (best response, toxicity, adverse events, progression) used
                   to predict survival (B13: best RECIST response as a PFS predictor)
  rmd_outputs      results computed in an Rmd but never printed or written; which objects a knitted notebook
                   stores (B13: `os_results` is never printed; the stored table is `pfs_results`)
  surv_groups      events / censored per level of every binary survival covariate (checks separation claims)
  literal_arrays   per-fold arrays typed into scripts against summary scores typed elsewhere for the same model
                   (B12: mean of fig_5.py Acc4 = 93.10, fig_6.py GRU accuracy = 92.76)
  pipeline         training scripts the entry point (`run`, `run.sh`, Makefile) never calls while figure scripts
                   plot numbers for the models they train (B12: SGRU_GRU_Kfold.py)
  metric_defs      the same metric names (Se, PPV, recall, precision) computed with sklearn averaging in one script
                   and with a binary confusion count in another (B12)
  weights          layer sizes read from stored model weights (TF checkpoint index, .h5, .npz, .pt) against the
                   architecture written in the paper (B14: paper 4-6-1, checkpoint W1 5x4, W2 7x5, W3 1x7 -> 4-5-7-1)
  params           hyper-parameters in the code against the values the paper states (B14: L2 0.002 in nn.py,
                   0.02 in the paper; Chen: alpha=0.005 vs "L1 coefficient 0.01")
  confusion        stored confusion counts recomputed (TP, predicted positive, actual positive, total, correct)
  inventory        file list, table headers, pinned environment (Dockerfile / environment.yml / renv.lock)

Usage
  python deep_checks.py bp_work --paper bp_work/paper.md --out bp_out
  (root = folder holding the authors' code/, data/, results/ - the unpacked capsule or bp_work)
Outputs: bp_out/deep.findings.json, bp_out/code_facts.json
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import math
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {"bp_out", "bp_work_prep", "prep", "__pycache__", ".git", ".ipynb_checkpoints", "node_modules", "skills",
             "_refs", "paper_agent", ".vscode", ".idea", "History", ".history", "checks"}
# v1.11.2: editor history copies (B16: code/.vscode/User/History/*.py) and the agent's own copy of this script
# (B16: deep_checks_py311.py) are not the authors' code.
AGENT_FILE = re.compile(r"(?i)(^|/)(repro_|adapter|port_|bp_|_bp|beyondpaper|whatif_|check_|deep_checks|recount)[^/]*$")
TABLE_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}


def finding(ftype, severity, location, evidence, question=None, check=None, claim_ids=None):
    return {"module": "ghost", "type": ftype, "severity": severity, "claim_ids": claim_ids or [],
            "location": location, "evidence": evidence, "source": "script:deep_checks.py",
            "question_for_authors": question, "check": check}


def walk(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            yield p


def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def read_text(p: Path, limit: int = 3_000_000) -> str:
    try:
        return p.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------ inventory
def table_header(p: Path) -> list[str]:
    if p.suffix.lower() in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb.worksheets[0]
            for row in ws.iter_rows(max_row=1, values_only=True):
                return [str(c) for c in row if c is not None]
        except Exception:
            return []
        return []
    try:
        head = p.open("r", encoding="utf-8-sig", errors="replace").readline()
    except OSError:
        return []
    delim = "\t" if head.count("\t") > head.count(",") else ","
    return next(csv.reader([head], delimiter=delim), [])


def inventory(root: Path) -> dict:
    files, headers, pinned = [], {}, []
    for p in walk(root):
        r = rel(p, root)
        files.append(r)
        if p.suffix.lower() in TABLE_EXT and p.stat().st_size < 200_000_000:
            h = table_header(p)
            if h:
                headers[r] = h
        if p.name in ("Dockerfile", "environment.yml", "environment.yaml", "renv.lock", "requirements.txt",
                      "Pipfile.lock", "poetry.lock", "conda.yaml", "postInstall"):
            txt = read_text(p, 200_000)
            if re.search(r"==\s*\d|=\s*\d+\.\d|@\d|:\s*\d+\.\d|\bFROM\s+\S+:\S+|r-[\w.]+=\d|install\.packages|"
                         r"remotes::install_version|\"Version\"", txt):
                pinned.append(r)
        # v1.11.5: a Code Ocean export names its archived image in REPRODUCING.md
        # ("registry.codeocean.com/published/<id>:v3"); the environment is pinned even without a lock file (B07-v1114 F3)
        if p.name.upper().startswith("REPRODUCING") and re.search(r"registry\.[\w.-]+/[\w./-]+:[\w.-]+",
                                                                   read_text(p, 200_000)):
            pinned.append(r)
    code = [f for f in files if f.lower().endswith((".py", ".r", ".rmd", ".ipynb", ".m", ".jl", ".sh", ".do", ".sas"))
            and not AGENT_FILE.search(f) and not f.startswith(("checks/", "bp_work/checks/"))]
    return {"files": files, "headers": headers, "env_pinned": pinned,
            "agent_files": [f for f in files if AGENT_FILE.search(f)], "author_code": code[:200],
            "n_author_code": len(code)}


def find_file(root: Path, path_in_code: str, files: list[str]) -> Path | None:
    """Map a path written in the code ('/data/x.csv', '../data/x.csv') to a file in the materials."""
    name = Path(path_in_code.replace("\\", "/")).name
    tail = "/".join(Path(path_in_code.replace("\\", "/")).parts[-2:])
    cands = [f for f in files if f.endswith(tail)] or [f for f in files if Path(f).name == name]
    if not cands:
        return None
    cands.sort(key=lambda f: (0 if "/data/" in f or f.startswith("data/") else 1, len(f)))
    return root / cands[0]


# ------------------------------------------------------------------------------------------------ R trace
R_READ = re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<-|=)\s*(?:read\.csv|read_csv|read\.table|read\.delim|fread)\s*\("
                    r"\s*(?:file\s*=\s*)?['\"]([^'\"]+)['\"]([^)]*)\)", re.M)
R_ASSIGN = re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<-|=)\s*(.+?)\s*$")


def r_names(header: list[str]) -> list[str]:
    """R's check.names: syntactic names; an empty first header becomes X."""
    out, seen = [], defaultdict(int)
    for i, h in enumerate(header):
        n = re.sub(r"[^\w.]", ".", h.strip()) if h.strip() else "X"
        if re.match(r"^[\d_]", n) or (n.startswith(".") and len(n) > 1 and n[1].isdigit()):
            n = "X" + n
        if seen[n]:
            n = f"{n}.{seen[n]}"
        seen[n] += 1
        out.append(n)
    return out


def r_index(vec: list, expr: str, env: dict) -> list | None:
    """Evaluate the index expression in vec[expr] for the patterns analysis scripts use."""
    e = expr.strip()
    e = re.sub(r"length\(\s*([A-Za-z.][\w.]*)\s*\)", lambda m: str(len(env.get(m.group(1), vec) or [])), e)
    e = re.sub(r"ncol\(\s*([A-Za-z.][\w.]*)\s*\)", lambda m: str(len(env.get(m.group(1), vec) or [])), e)
    m = re.fullmatch(r"-\s*c?\(?\s*([\d\s,:]+)\)?", e)
    if m:
        drop = set()
        for part in m.group(1).split(","):
            part = part.strip()
            if ":" in part:
                a, b = part.split(":")
                drop |= set(range(int(eval_int(a)), int(eval_int(b)) + 1))
            elif part:
                drop.add(int(part))
        return [v for i, v in enumerate(vec, start=1) if i not in drop]
    m = re.fullmatch(r"\(?\s*([\d+\-\s]+)\s*\)?\s*:\s*\(?\s*([\d+\-\s]+)\s*\)?", e)
    if m:
        a, b = eval_int(m.group(1)), eval_int(m.group(2))
        if a is None or b is None:
            return None
        return vec[a - 1:b] if a <= b else list(reversed(vec[b - 1:a]))
    m = re.fullmatch(r"\d+", e)
    if m:
        k = int(e)
        return vec[k - 1:k]
    return None


def eval_int(s: str):
    s = s.strip()
    if re.fullmatch(r"[\d+\-\s]+", s):
        try:
            return int(eval(s, {"__builtins__": {}}))  # digits and +/- only
        except Exception:
            return None
    return None


def r_trace(root: Path, inv: dict) -> tuple[list[dict], dict]:
    findings, facts = [], {}
    for p in walk(root):
        if p.suffix.lower() not in (".r", ".rmd", ".qmd") or AGENT_FILE.search(rel(p, root)):
            continue
        text = read_text(p)
        lines = text.splitlines()
        frames, vecs, models = {}, {}, []
        for ln, line in enumerate(lines, start=1):
            code = line.split("#", 1)[0]
            m = R_READ.match(code)
            if m:
                f = find_file(root, m.group(2), inv["files"])
                hdr = table_header(f) if f else []
                if hdr:
                    no_header = re.search(r"header\s*=\s*(F|FALSE)\b", m.group(3))
                    frames[m.group(1)] = {"cols": r_names(hdr) if not no_header else [f"V{i+1}" for i in range(len(hdr))],
                                          "file": rel(f, root), "line": ln, "ops": []}
                continue
            m = R_ASSIGN.match(code)
            if not m:
                continue
            lhs, rhs = m.group(1), m.group(2)
            mm = re.fullmatch(r"([A-Za-z.][\w.]*)\s*\[\s*,?\s*([^\]]+?)\s*\]", rhs)
            if mm and mm.group(1) in frames:
                new = r_index(frames[mm.group(1)]["cols"], mm.group(2), {})
                if new is not None:
                    fr = dict(frames[mm.group(1)])
                    fr["ops"] = fr["ops"] + [f"line {ln}: {code.strip()}"]
                    fr["cols"] = new
                    frames[lhs] = fr
                continue
            mm = re.fullmatch(r"(?:colnames|names)\(\s*([A-Za-z.][\w.]*)\s*\)", rhs)
            if mm and mm.group(1) in frames:
                vecs[lhs] = {"vals": list(frames[mm.group(1)]["cols"]), "frame": mm.group(1),
                             "ops": [f"line {ln}: {code.strip()}"]}
                continue
            mm = re.fullmatch(r"([A-Za-z.][\w.]*)\s*\[\s*([^\]]+?)\s*\]", rhs)
            if mm and mm.group(1) in vecs:
                new = r_index(vecs[mm.group(1)]["vals"], mm.group(2), {k: v["vals"] for k, v in vecs.items()})
                if new is not None:
                    vecs[lhs] = {"vals": new, "frame": vecs[mm.group(1)]["frame"],
                                 "ops": vecs[mm.group(1)]["ops"] + [f"line {ln}: {code.strip()}"]}
                continue
            # univariate formulas: sapply(vec[...], function(x) as.formula(paste('surv~', x)))
            mm = re.search(r"sapply\(\s*([A-Za-z.][\w.]*)\s*(?:\[\s*([^\]]+?)\s*\])?\s*,\s*function\s*\(\s*\w+\s*\)"
                           r"\s*as\.formula\(\s*paste0?\(\s*['\"]([^'\"]+)['\"]", rhs)
            if mm and mm.group(1) in vecs:
                base = vecs[mm.group(1)]
                used = base["vals"] if not mm.group(2) else r_index(base["vals"], mm.group(2),
                                                                    {k: v["vals"] for k, v in vecs.items()})
                if used is not None:
                    models.append({"file": rel(p, root), "line": ln, "outcome": mm.group(3).replace("~", "").strip(),
                                   "covariates": used, "frame": base["frame"],
                                   "ops": base["ops"] + [f"line {ln}: {code.strip()[:160]}"]})
        if not models:
            continue
        by_frame = defaultdict(list)
        for mo in models:
            by_frame[mo["frame"]].append(mo)
        for fr_name, mos in by_frame.items():
            fr = frames.get(fr_name)
            if not fr:
                continue
            used = set().union(*[set(mo["covariates"]) for mo in mos])
            skipped = [c for c in fr["cols"] if c not in used]
            n_used = len(mos[0]["covariates"])
            key = rel(p, root)
            facts.setdefault("r_trace", {})[key] = {
                "data_file": fr["file"], "columns_after_drops": len(fr["cols"]), "tested": n_used,
                "skipped": skipped, "outcomes": [mo["outcome"] for mo in mos], "steps": mos[0]["ops"]}
            if skipped:
                steps = "; ".join(mos[0]["ops"])
                findings.append(finding(
                    "param_mismatch", "yellow", f"{key}:{mos[0]['line']}",
                    f"Column trace (deep_checks.py): {fr['file']} has {len(fr['cols'])} columns after the code's own "
                    f"drops ({'; '.join(fr['ops']) or 'none'}); the model formulas use {n_used}. Never modelled: "
                    f"{', '.join(skipped)}. Steps: {steps}. Outcomes: {', '.join(mo['outcome'] for mo in mos)}.",
                    f"Were {', '.join(skipped)} meant to be tested? The index steps skip them without comment.",
                    check=f"trace of {key}"))
            post = [c for c in mos[0]["covariates"]
                    if re.search(r"(?i)best.?resp|response|recist|toxic|adverse|\bae\b|progress|relapse|grade|"
                                 r"post.?treat|on.?treat", c)]
            surv = [mo["outcome"] for mo in mos if re.search(r"(?i)surv|os|pfs|time|event", mo["outcome"])]
            if post and surv:
                facts.setdefault("post_baseline", {})[key] = post
                findings.append(finding(
                    "leakage_risk", "yellow", f"{key}:{mos[0]['line']}",
                    f"Post-baseline covariates (deep_checks.py): {', '.join(post)} are measured during or after "
                    f"treatment but enter the survival models ({', '.join(surv)}) as baseline predictors. A patient "
                    "must survive long enough to have a best response or a toxicity grade (immortal-time / "
                    "outcome-dependent covariate), so their hazard ratios are biased toward benefit.",
                    "Were these covariates handled with a landmark analysis or as time-dependent covariates?",
                    check=f"trace of {key}"))
    return findings, facts


# ------------------------------------------------------------------------------------------------ Rmd outputs
RESULT_NAME = re.compile(r"(?i)(result|results|res|table|tbl|summary|model|fit|output|estimates|hr)\b|_res$|_results$|"
                         r"_table$|_tab$|_summary$")


def rmd_outputs(root: Path, inv: dict) -> tuple[list[dict], dict]:
    findings, facts = [], {}
    for p in walk(root):
        if p.suffix.lower() not in (".rmd", ".qmd"):
            continue
        text = read_text(p)
        chunks = re.findall(r"```\{r([^}]*)\}\n(.*?)```", text, re.S)
        assigned, printed, written = {}, [], set()
        for header, body in chunks:
            label = header.strip().split(",")[0].strip() or "unnamed"
            lines = [l.split("#", 1)[0].rstrip() for l in body.splitlines()]
            for l in lines:
                m = re.match(r"^([A-Za-z.][\w.]*)(?:\$\w+)?\s*(?:<-|=)\s*", l)
                if m:  # top level only: names assigned inside function bodies are indented
                    assigned.setdefault(m.group(1), label)
                m = re.match(r"^\s*(?:print|kable|knitr::kable|DT::datatable|datatable|View|paged_table|"
                             r"rmarkdown::paged_table|head|summary)\(\s*([A-Za-z.][\w.]*)", l)
                if m:
                    printed.append(m.group(1))
                m = re.search(r"(?:write\.csv|write\.table|write_csv|saveRDS|save|writexl::write_xlsx|"
                              r"openxlsx::write\.xlsx|write\.xlsx)\(\s*([A-Za-z.][\w.]*)", l)
                if m:
                    written.add(m.group(1))
                if re.fullmatch(r"\s*([A-Za-z.][\w.]*)\s*", l or "") and l.strip() in assigned:
                    printed.append(l.strip())
        stem = p.stem
        notebooks = [f for f in inv["files"] if Path(f).name.startswith(stem + ".") and
                     re.search(r"\.(nb\.html|html|pdf|md|docx)$", f) and not f.endswith(p.name)]
        key = rel(p, root)
        facts.setdefault("archive_objects", {})
        for nb in notebooks:
            facts["archive_objects"][nb] = sorted(set(printed), key=printed.index)
        facts.setdefault("rmd_outputs", {})[key] = {"printed": sorted(set(printed)), "written": sorted(written),
                                                     "notebooks": notebooks}
        # v1.11.5: an object read in inline R (`r timeSummary %>% pull(min)`) is printed into the text (B07-v1114 F4)
        inline = " ".join(re.findall(r"`r\s([^`]+)`", text))
        lost = [n for n, lab in assigned.items() if RESULT_NAME.search(n) and n not in printed and n not in written
                and not re.search(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", inline)
                and not any(re.search(r"\b" + re.escape(n) + r"\b", b) for _, b in chunks
                            if re.search(r"(?:print|kable|write\.\w+|save\w*)\([^)]*\b" + re.escape(n) + r"\b", b))]
        # keep result objects that are the end product of their chunk (not intermediates reused later)
        lost = [n for n in lost if not any(re.search(r"(?:<-|=)[^\n]*\b" + re.escape(n) + r"\b", b)
                                           for lab, b in chunks if lab.strip().split(",")[0].strip() != assigned[n])]
        if lost and printed:
            stored = ", ".join(sorted(set(printed)))
            findings.append(finding(
                "missing_artifact", "yellow", key,
                f"Rmd outputs (deep_checks.py): {', '.join(lost)} computed in chunk(s) "
                f"{', '.join(sorted({assigned[n] for n in lost}))} but never printed or written; the knitted output "
                f"({', '.join(notebooks) or 'none found'}) can only hold what is printed: {stored}. The stored "
                f"result for {', '.join(lost)} is therefore not in the materials.",
                f"Can the {', '.join(lost)} table be added to the stored output?", check=f"chunk scan of {key}"))
    return findings, facts


# ------------------------------------------------------------------------------------------------ survival groups
def surv_groups(root: Path, inv: dict, facts: dict) -> dict:
    """Events and censored per level of binary covariates, rows aligned by position (as R's Surv() + data= does)."""
    out = {}
    try:
        import pandas as pd
    except ImportError:
        return out
    for p in walk(root):
        if p.suffix.lower() not in (".r", ".rmd", ".qmd"):
            continue
        text = read_text(p)
        reads = {m.group(1): find_file(root, m.group(2), inv["files"]) for m in R_READ.finditer(text)}
        for m in re.finditer(r"([A-Za-z.][\w.]*)\s*<-\s*Surv\(\s*time\s*=\s*([A-Za-z.][\w.]*)\$([\w.]+)\s*,\s*"
                             r"event\s*=\s*([A-Za-z.][\w.]*)\$([\w.]+)", text):
            sf = reads.get(m.group(2))
            if not sf:
                continue
            try:
                sd = pd.read_csv(sf)
                sd.columns = r_names(list(sd.columns))
            except Exception:
                continue
            ev = m.group(5)
            if ev not in sd.columns:
                continue
            for fname, ff in reads.items():
                if ff is None or ff == sf:
                    continue
                try:
                    fd = pd.read_csv(ff)
                    fd.columns = r_names(list(fd.columns))
                except Exception:
                    continue
                if len(fd) != len(sd):
                    continue
                tab = {}
                for c in fd.columns:
                    vals = fd[c].dropna().unique()
                    if 1 < len(vals) <= 2:
                        g = {}
                        for v in sorted(vals, key=str):
                            mask = fd[c] == v
                            e = int(sd.loc[mask.values, ev].sum())
                            g[str(v)] = {"n": int(mask.sum()), "events": e, "censored": int(mask.sum()) - e}
                        tab[c] = g
                out[f"{m.group(1)} ({m.group(5)})"] = {"data": rel(ff, root), "survival": rel(sf, root), "binary": tab}
    return out


# ------------------------------------------------------------------------------------------------ literal arrays
def literal_value(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def array_of(node):
    """Return (values, scale) for [..], np.array([..]) and 100*np.array([..])."""
    scale = 1.0
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for a, b in ((node.left, node.right), (node.right, node.left)):
            k = literal_value(a)
            if isinstance(k, (int, float)):
                scale, node = float(k), b
                break
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("array", "asarray") \
            and node.args:
        node = node.args[0]
    v = literal_value(node)
    if isinstance(v, (list, tuple)) and len(v) >= 3 and all(isinstance(x, (int, float)) for x in v):
        return [float(x) * scale for x in v], scale
    return None, None


def decimals(x: float) -> int:
    s = repr(x)
    return len(s.split(".")[1]) if "." in s and "e" not in s else 0


def literal_arrays(root: Path) -> tuple[list[dict], dict]:
    arrays, labels, scalars = {}, {}, []
    for p in walk(root):
        if p.suffix != ".py" or AGENT_FILE.search(rel(p, root)):
            continue
        try:
            tree = ast.parse(read_text(p))
        except SyntaxError:
            continue
        names_tuple, title = None, ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                tgt = node.targets[0].id
                vals, _ = array_of(node.value)
                if vals:
                    arrays[(rel(p, root), tgt)] = {"values": vals, "line": node.lineno,
                                                   "mean": sum(vals) / len(vals)}
                v = literal_value(node.value)
                if isinstance(v, tuple) and v and all(isinstance(x, str) for x in v) and tgt.lower() in (
                        "names", "labels", "models", "methods", "classifiers"):
                    names_tuple = v
                if isinstance(v, tuple) and v and all(isinstance(x, (tuple, list)) for x in v) and tgt.lower() in (
                        "scores", "values", "results", "data", "acc", "metrics"):
                    scalars.append({"file": rel(p, root), "line": node.lineno, "rows": v, "var": tgt})
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "DataFrame" \
                    and node.args and isinstance(node.args[0], ast.Dict):
                for k, val in zip(node.args[0].keys, node.args[0].values):
                    if isinstance(k, ast.Constant) and isinstance(val, ast.Name):
                        labels[(rel(p, root), val.id)] = str(k.value)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "title" and node.args:
                t = literal_value(node.args[0])
                if isinstance(t, str):
                    title = t
        for s in scalars:
            if s["file"] == rel(p, root):
                s["names"] = names_tuple
                s["title"] = title
    facts = {"arrays": {f"{f}:{v}": {"label": labels.get((f, v)), "mean": round(a["mean"], 4), "n": len(a["values"])}
                        for (f, v), a in arrays.items()}}
    findings = []
    by_label = defaultdict(list)
    for (f, v), a in arrays.items():
        if labels.get((f, v)):
            by_label[labels[(f, v)]].append((f, v, a))
    for s in scalars:
        if not s.get("names") or len(s["names"]) != len(s["rows"]):
            continue
        for name, row in zip(s["names"], s["rows"]):
            cands = by_label.get(str(name), [])
            if not cands or not row:
                continue
            val = float(row[0])  # first column is the accuracy-like summary of the per-fold arrays
            d = decimals(val)
            exact = [c for c in cands if round(c[2]["mean"], d) == round(val, d)]
            if exact:
                continue
            # an array that matches no other summary value of this model is the one this value should summarise
            others = [float(r[0]) for t in scalars for nm, r in zip(t.get("names") or (), t["rows"])
                      if str(nm) == str(name) and r and t is not s]
            free = [c for c in cands if not any(round(c[2]["mean"], decimals(o)) == round(o, decimals(o)) for o in others)]
            if len(free) == 1 and abs(free[0][2]["mean"] - val) <= 1.0:
                f, v, a = free[0]
                findings.append(finding(
                    "figure_data_mismatch", "yellow", f"{s['file']}:{s['line']} vs {f}:{a['line']}",
                    f"Literal arrays (deep_checks.py): {s['file']} plots {name} {s['var']}[...][0] = {val} "
                    f"({s.get('title') or 'no title'}); the per-fold values typed into {f} ({v}, labelled {name}) "
                    f"average {a['mean']:.2f} over {len(a['values'])} folds. The other {name} summary values match "
                    "their arrays exactly, so this one does not come from the stored per-fold results.",
                    "Which run produced the plotted value, and why does it differ from the mean of the per-fold values?",
                    check=f"mean of {f}:{v}"))
    return findings, facts


# ------------------------------------------------------------------------------------------------ pipeline coverage
def pipeline(root: Path, inv: dict) -> tuple[list[dict], dict]:
    entries = [root / f for f in inv["files"] if Path(f).name in ("run", "run.sh", "Makefile", "main.sh", "run_all.sh",
                                                                  "reproduce.sh", "run.py")]
    if not entries:
        return [], {}
    called, globs = set(), []
    for e in entries:
        txt = read_text(e)
        called |= set(re.findall(r"([\w\-./]+\.(?:py|R|r|Rmd|ipynb|sh|m|jl))", txt))
        globs += re.findall(r"([\w\-]*\*[\w\-]*\.(?:py|R|r))", txt)
    called_names = {Path(c).name for c in called}
    scripts = [root / f for f in inv["files"] if f.endswith((".py", ".R", ".r", ".Rmd", ".ipynb"))
               and "code" in Path(f).parts and not AGENT_FILE.search(f)]
    import fnmatch
    not_run = [s for s in scripts if s.name not in called_names and not any(fnmatch.fnmatch(s.name, g) for g in globs)]
    findings, facts = [], {"entry": [rel(e, root) for e in entries], "not_run": [rel(s, root) for s in not_run]}
    run_out = " ".join(read_text(root / f) for f in inv["files"] if f.startswith(("results", "result", "output")) and
                       Path(f).suffix in ("", ".txt", ".log", ".out"))
    fig_text = " ".join(read_text(root / f) for f in inv["files"] if re.search(r"(?i)fig|plot", Path(f).name)
                        and f.endswith(".py"))
    model_re = r"\b(?:[A-Z][A-Za-z]*GRU|[A-Z]?LSTM|SGRU|GRU|CNN|Transformer|ResNet|XGB\w*|RandomForest\w*|SVC|SVM)\b"
    run_scripts = [root / f for f in inv["files"] if Path(f).name in called_names]
    covered = set()
    for s in run_scripts:
        covered |= set(re.findall(model_re, read_text(s)))
    cands = []
    for s in not_run:
        txt = read_text(s)
        if not re.search(r"\.fit\(|model\.fit|train\(|\.compile\(|cross_val|KFold", txt):
            continue
        models = set(re.findall(model_re, s.name.replace("_", " ") + " " + txt[:20000]))
        plotted = sorted(m for m in models - covered if re.search(r"['\"]" + re.escape(m) + r"['\"]", fig_text))
        if plotted and not re.search(re.escape(s.stem), run_out):
            in_name = sum(m in s.name for m in plotted)
            cands.append((in_name, len(plotted), s, plotted))
    if cands:
        cands.sort(key=lambda c: (-c[0], -c[1], c[2].name))
        _, _, s, plotted = cands[0]
        also = [rel(c[2], root) for c in cands[1:]]
        findings.append(finding(
            "phantom_experiment", "yellow", rel(s, root),
            f"Pipeline coverage (deep_checks.py): {rel(s, root)} trains {', '.join(plotted)}, which no script of the "
            f"entry point ({', '.join(facts['entry'])}) trains; the entry point never runs it and the stored output has "
            f"no lines from it. The figure scripts plot numbers for {', '.join(plotted)} as typed-in literals, so those "
            "numbers cannot be traced to a stored run." + (f" Also never run: {', '.join(also)}." if also else ""),
            f"Can the log or results of {s.name} be added to the capsule?", check="entry-point scan"))
    return findings, facts


# ------------------------------------------------------------------------------------------------ metric definitions
def metric_defs(root: Path) -> tuple[list[dict], dict]:
    avg, binary = [], []
    for p in walk(root):
        if p.suffix != ".py" or AGENT_FILE.search(rel(p, root)):
            continue
        t = read_text(p)
        for m in re.finditer(r"(recall_score|precision_score|f1_score)\([^)]*average\s*=\s*['\"](macro|weighted|micro)",
                             t):
            avg.append(f"{rel(p, root)}:{t[:m.start()].count(chr(10)) + 1} {m.group(1)} average='{m.group(2)}'")
        if re.search(r"(?i)\b(?:self\.)?TP\s*/\s*\(\s*(?:self\.)?TP\s*\+\s*(?:self\.)?F[NP]\s*\)", t):
            binary.append(rel(p, root))
    if avg and binary:
        return [finding("param_mismatch", "yellow", f"{avg[0].split(' ')[0]} vs {binary[0]}",
                        f"Metric definitions (deep_checks.py): sensitivity/precision come from sklearn with "
                        f"{avg[0].split(' ', 1)[1]} in one pipeline ({'; '.join(avg[:3])}) and from a binary "
                        f"TP/(TP+FN), TP/(TP+FP) count for one class in another ({', '.join(binary[:3])}). Values "
                        "with the same name are not the same quantity, so a table comparing them across methods is "
                        "not like for like.",
                        "Were Se/PPV computed with the same definition for every method compared?",
                        check="regex scan")], {"averaged": avg, "binary": binary}
    return [], {}


# ------------------------------------------------------------------------------------------------ stored weights
def varint(b: bytes, i: int):
    shift = val = 0
    while i < len(b):
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7
    raise ValueError


def tf_index_shapes(p: Path) -> dict:
    """Tensor shapes from a TensorFlow checkpoint .index (an SSTable of BundleEntryProto), no TensorFlow needed."""
    b = p.read_bytes()
    out, key, i = {}, b"", 0
    try:
        while i < len(b) - 20:
            shared, i = varint(b, i)
            nonshared, i = varint(b, i)
            vlen, i = varint(b, i)
            if shared > len(key) or nonshared > 200 or vlen > 10000:
                break
            key = key[:shared] + b[i:i + nonshared]
            i += nonshared
            val = b[i:i + vlen]
            i += vlen
            if key:
                dims, j = [], 0
                while j < len(val):
                    tag, j = varint(val, j)
                    field, wt = tag >> 3, tag & 7
                    if wt == 0:
                        _, j = varint(val, j)
                    elif wt == 2:
                        ln, j = varint(val, j)
                        sub = val[j:j + ln]
                        j += ln
                        if field == 2:
                            k = 0
                            while k < len(sub):
                                t2, k = varint(sub, k)
                                if t2 >> 3 == 2 and t2 & 7 == 2:
                                    l2, k = varint(sub, k)
                                    dim = sub[k:k + l2]
                                    k += l2
                                    d, q = 0, 0
                                    while q < len(dim):
                                        t3, q = varint(dim, q)
                                        if t3 >> 3 == 1:
                                            d, q = varint(dim, q)
                                        else:
                                            break
                                    dims.append(d)
                                else:
                                    break
                    elif wt == 5:
                        j += 4
                    elif wt == 1:
                        j += 8
                    else:
                        break
                out[key.decode("utf-8", "replace")] = dims
    except (ValueError, IndexError):
        pass
    return {k: v for k, v in out.items() if v}


def weight_shapes(root: Path, inv: dict) -> dict:
    shapes = {}
    for f in inv["files"]:
        p = root / f
        try:
            if f.endswith(".index") and ("chkp" in f or "ckpt" in f or "model" in f):
                for k, v in tf_index_shapes(p).items():
                    shapes[f"{f}:{k}"] = v
            elif f.endswith((".h5", ".hdf5", ".keras")):
                import h5py  # noqa
                with h5py.File(p, "r") as h:
                    h.visititems(lambda n, o: shapes.__setitem__(f"{f}:{n}", list(o.shape))
                                 if hasattr(o, "shape") and len(o.shape) == 2 else None)
            elif f.endswith(".npz"):
                import numpy as np
                for k, v in np.load(p).items():
                    if v.ndim == 2:
                        shapes[f"{f}:{k}"] = list(v.shape)
            elif f.endswith((".pt", ".pth")):
                import torch  # noqa
                sd = torch.load(p, map_location="cpu")
                if isinstance(sd, dict):
                    for k, v in sd.items():
                        if hasattr(v, "shape") and len(v.shape) == 2:
                            shapes[f"{f}:{k}"] = list(v.shape)
        except Exception:
            continue
    return shapes


def layer_chain(shapes: dict) -> list[int] | None:
    """Chain 2-D weight matrices into layer sizes (W1 5x4, W2 7x5, W3 1x7 -> 4-5-7-1; either orientation)."""
    mats = [(k, v) for k, v in shapes.items() if len(v) == 2 and re.search(r"(?i)(^|[:/_])(w|weight|kernel|dense)", k)]
    if len(mats) < 2:
        return None
    mats.sort(key=lambda kv: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", kv[0])])
    dims = [v for _, v in mats]
    for orient in ("out_in", "in_out"):
        chain = []
        ok = True
        for d in dims:
            a, b = (d[1], d[0]) if orient == "out_in" else (d[0], d[1])
            if chain and chain[-1] != a:
                ok = False
                break
            if not chain:
                chain.append(a)
            chain.append(b)
        if ok:
            return chain
    return None


ARCH = re.compile(r"(?<![\d.])(\d{1,4}(?:\s*[-–]\s*\d{1,4}){2,6})(?![\d.])")


def weights(root: Path, inv: dict, paper: str) -> tuple[list[dict], dict]:
    shapes = weight_shapes(root, inv)
    chain = layer_chain(shapes)
    facts = {"weight_shapes": shapes, "layers": chain}
    if not chain or not paper:
        return [], facts
    findings = []
    for m in ARCH.finditer(paper):
        ctx = paper[max(0, m.start() - 160):m.end() + 160]
        if not re.search(r"(?i)layer|neuron|node|network|architecture|unit|topolog|hidden", ctx):
            continue
        claimed = [int(x) for x in re.split(r"\s*[-–]\s*", m.group(1))]
        if claimed == chain:
            continue
        if claimed[0] != chain[0] or claimed[-1] != chain[-1]:
            continue  # not the same network (different input or output size)
        page = paper[:m.start()].count("<!-- page")
        # v1.11.4: built outside the f-string; a nested f'{k.split(':')}' is a SyntaxError before Python 3.12
        # and stopped the whole script in the SciencePal sandbox (B13-v1113)
        shape_txt = ", ".join("%s %sx%s" % (k.split(":")[-1], v[0], v[1]) for k, v in shapes.items() if len(v) == 2)
        findings.append(finding(
            "archive_mismatch", "red", f"paper p.{page or '?'} ('{m.group(1)}') vs {', '.join(sorted({k.split(':')[0] for k in shapes}))}",
            f"Stored weights (deep_checks.py): the paper describes a {m.group(1)} network; the authors' stored "
            f"weights have shapes {shape_txt}, "
            f"i.e. layers {'-'.join(map(str, chain))}. Paper text and the authors' own model file disagree.",
            "Which architecture produced the reported results?", check="checkpoint index parse"))
        break
    return findings, facts


# ------------------------------------------------------------------------------------------------ hyper-parameters
PARAMS = {
    # v1.11.3: the value group came second, so the name ("alpha") was read as the value and the L1 scan never ran
    # (B06: Methods "coefficient of the L1 constraint as 0.01", code ArgsClass(k=10, alpha=0.005)). C is an inverse
    # strength and l1_ratio a mixing weight, so neither is compared with a stated L1 coefficient.
    "l1": (r"\b(?:alpha|lasso_alpha|l1_alpha|l1_penalty)\s*=\s*(\d*\.?\d+(?:[eE]-?\d+)?)\b",
           r"(?i)\bL1\b|lasso|sparsity|L1[- ]regulari"),
    "l2": (r"([\d.eE\-]+)\s*\*\s*(?:\w*regulari\w*|l2_loss)|\b(?:lambd|lambda_?|reg_lambda|weight_decay|l2|beta)\s*=\s*([\d.eE\-]+)",
           r"(?i)\bL2\b|regulari[sz]ation (?:parameter|coefficient|strength|constant)|\blambda\b|λ|weight decay"),
    "learning_rate": (r"\b(?:learning_rate|lr|eta)\s*=\s*([\d.eE\-]+)", r"(?i)learning rate"),
    "epochs": (r"\b(?:num_epochs|epochs|n_epochs|max_iter)\s*=\s*(\d+)", r"(?i)\bepochs?\b|iterations"),
    "n_estimators": (r"\bn_estimators\s*=\s*(\d+)", r"(?i)\btrees\b|n_estimators|estimators"),
    "max_depth": (r"\bmax_depth\s*=\s*(\d+)", r"(?i)max(?:imum)? depth|tree depth"),
    "folds": (r"\bn_splits\s*=\s*(\d+)|\bcv\s*=\s*(\d+)", r"(?i)(\d+)[- ]fold"),
    "dropout": (r"\bDropout\(\s*([\d.]+)|\bdropout\s*=\s*([\d.]+)", r"(?i)dropout"),
    "batch_size": (r"\bbatch_size\s*=\s*(\d+)", r"(?i)batch size"),
}


COMMENTED = re.compile(r'(?:^|[\s"\'])#(?![0-9a-fA-F]{6}\b)')  # line comment before the match (.py .r .rmd .ipynb .jl)
COMMENT_MARK = {".m": re.compile(r"(?:^|\s)%")}
STOP_EPOCH = re.compile(r"(?i)(?:stop\w*|converg\w*|plateau\w*|best|minimum|lowest)\D{0,20}?\bepochs?\s*\d+|"
                        r"\bat epoch\s*\d+|patience")
TREE_MODEL = re.compile(r"(?i)estimators|boost|XGB|LightGBM|CatBoost|random forest|\btrees?\b|GBR|GBM")
OTHER_MODEL = re.compile(r"(?i)auto-?encoder|reconstruction|decoder|discriminator|\bVAE\b|\bGAN\b")
PLOT_CALL = re.compile(r"\b(?:plt|ax\w*|sns|axes)\.|\b(?:plot|scatter|fill_between|hist|barh?|axvline|axhline|imshow|"
                       r"errorbar|legend|annotate|text|geom_\w+)\s*\(")
CITATION = re.compile(r"\^?\[\d+(?:\s*[,\u2013-]\s*\d+)*\]|<sup>[^<]{1,20}</sup>|\^\d+(?:[,\u2013-]\d+)*|"
                      r"[\u00b9\u00b2\u00b3\u2070-\u2079]+")
UNITS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
WORD_NUM_RE = re.compile(r"(?i)\b(?:(a|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|"
                         r"seventy|eighty|ninety)[\s-]+)?(hundred|thousand)\b|\b(ten|twenty|thirty|forty|fifty|sixty|"
                         r"seventy|eighty|ninety|three|five)\b")


def word_numbers(s: str) -> list[tuple[float, int]]:
    """Numbers written in words, with their position: "five hundred" = 500 (v1.11.7: B02-v1116p/p2 read "five
    hundred trees" as "hundred" = 100, equal to the code, and skipped the Chen 2024 injection I4)."""
    out = []
    for m in WORD_NUM_RE.finditer(s):
        if m.group(2):
            out.append((float(UNITS.get((m.group(1) or "a").lower(), 1) * (100 if m.group(2).lower() == "hundred" else 1000)),
                        m.start()))
        else:
            out.append((float(UNITS[m.group(3).lower()]), m.start()))
    return out


def params(root: Path, paper: str) -> tuple[list[dict], dict]:
    code_vals = defaultdict(list)
    for p in walk(root):
        if p.suffix.lower() not in (".py", ".r", ".rmd", ".ipynb", ".m", ".jl") or AGENT_FILE.search(rel(p, root)):
            continue
        t = read_text(p)
        for name, (code_re, _) in PARAMS.items():
            for m in re.finditer(code_re, t):
                line_start = t.rfind("\n", 0, m.start()) + 1
                prev = t[max(0, line_start - 400):m.start()]
                if re.search(r"\bdef\s+\w+\s*\([^)]*$", prev) or re.search(r"function\s*\([^)]*$", prev):
                    continue  # a default in a signature, not the value the call uses
                line = t[line_start:t.find("\n", m.end()) if t.find("\n", m.end()) >= 0 else len(t)]
                # v1.11.4: a commented-out setting is not the value the code uses (B16-v1113 F12 compared the paper
                # with "#best tanh' with dropout_rate=0.1 ,learning_rate = 0.01" at R2_FeedBi_N.py:56)
                cm = COMMENT_MARK.get(p.suffix.lower(), COMMENTED)
                if cm.search(t[line_start:m.start()]):
                    continue
                if name == "l1" and (PLOT_CALL.search(line) or p.suffix.lower() in (".r", ".rmd")):
                    continue  # alpha= is also a plot's transparency, and glmnet's alpha is the L1/L2 mix, not a strength
                v = next((g for g in reversed(m.groups()) if g and re.match(r"^-?[\d.]", g)), None)
                try:
                    code_vals[name].append((float(v), f"{rel(p, root)}:{t[:m.start()].count(chr(10)) + 1}",
                                            t[m.start():m.end()]))
                except (TypeError, ValueError):
                    pass
    facts = {k: [(v, loc) for v, loc, _ in vs] for k, vs in code_vals.items()}
    findings = []
    if not paper:
        return findings, facts
    sentences = re.split(r"(?<=[.;。])\s+", paper)
    for name, (_, paper_re) in PARAMS.items():
        cv = {v for v, _, _ in code_vals.get(name, [])}
        if not cv or len(cv) > 2:
            continue
        for s in sentences:
            m = re.search(paper_re, s)
            if not m:
                continue
            # citation markers are not values (B06-v1110: "Gini impurity^[79]" read as n_estimators = 79)
            s_clean = CITATION.sub(" ", s)
            m = re.search(paper_re, s_clean)
            if not m:
                continue
            # a value written in words just before the keyword ("a hundred trees", "five hundred classifier trees")
            wn = [v for v, pos in word_numbers(s_clean) if 0 <= m.start() - pos <= 40]
            if wn and any(math.isclose(wn[-1], c) for c in cv):
                continue  # equals the code ("a hundred trees" = 100)
            s = s_clean
            window = s[m.start():m.start() + 120]
            nums = [float(x) for x in re.findall(r"(?<![\w.])(\d*\.\d+|\d+)(?![\w.%])", window)]
            # a number written just before the keyword is its value ("100 estimators and a fixed random seed (42)", B16)
            before = re.search(r"(?<![\w.])(\d*\.\d+|\d+)\s+(?:[A-Za-z-]+\s+){0,2}$", s[:m.start()])
            if before and name in ("n_estimators", "epochs", "batch_size"):
                nums = [float(before.group(1))]
            elif wn and name in ("n_estimators", "epochs", "batch_size") and wn[-1] >= 10:
                nums = [wn[-1]]
            if "<!-- page" in window or window.count("\n") > 3:
                continue  # a table or page break, not a sentence that states the setting (B16: epochs = 13)
            if name == "folds":
                nums = [float(x) for x in re.findall(r"(\d+)[- ]fold", s)]
            if name == "epochs" and STOP_EPOCH.search(s[max(0, m.start() - 40):m.end() + 20]):
                continue  # "early stopping at epoch 21" is where training stopped, not the epoch setting (B16-v1113 F13)
            if OTHER_MODEL.search(s) and not any(OTHER_MODEL.search(loc) for _, loc, _ in code_vals[name]):
                continue  # the sentence describes another model (an autoencoder's learning rate, B16-v1113 F12)
            if name in ("learning_rate", "epochs", "dropout", "batch_size") and TREE_MODEL.search(s) and \
                    not any(TREE_MODEL.search(loc) for _, loc, _ in code_vals[name]):
                continue  # a boosting model's learning rate ("100 estimators, a learning rate of 0.1") vs a network's
            if name in ("l2", "learning_rate"):
                # v1.11.4: only a number right after the keyword is its value. B14-v1113 F8 read "45" (from ">45 min")
                # out of a sentence that names L2 regularisation without a value; the stated lambda 0.02 came later.
                kw = (r"(?i)(?:\bL2\b|lambda|λ|regulari[sz]ation (?:parameter|coefficient|strength|constant)|"
                      r"weight decay)" if name == "l2" else r"(?i)learning rate")
                nums = [float(x) for x in re.findall(kw + r"[^\d<>]{0,25}?(\d*\.\d+(?:[eE]-?\d+)?|\d+(?:[eE]-?\d+)?)\b",
                                                     s[m.start():m.start() + 300])]
            if name == "l1":
                # the value of a penalty, not a count near the word LASSO ("LASSO ... selected 10 metabolites")
                nums = [float(x) for x in re.findall(r"(?i)(?:coefficient|penalty|alpha|lambda|λ|strength|constraint|"
                                                     r"regulari[sz]ation(?: parameter)?)\D{0,25}?(\d*\.\d+|\d+(?:e-?\d+)?)\b",
                                                     window)]
            nums = [n for n in nums if n not in (1.0, 2.0) or name in ("folds",)]
            if name in ("epochs", "n_estimators", "batch_size", "max_depth", "folds"):
                nums = [n for n in nums if n == int(n)]  # "iterations ... learning rate of 0.15" is not an epoch count
            if len(nums) != 1:
                continue
            pv = nums[0]
            if any(math.isclose(pv, c, rel_tol=1e-6) for c in cv):
                continue
            if name == "epochs" and pv > 0 and any(c > 0 and (pv / c in (10, 0.1)) for c in cv):
                pass
            v, loc, snippet = code_vals[name][0]
            page = paper[:paper.find(s)].count("<!-- page") if s in paper else 0
            findings.append(finding(
                "param_mismatch", "yellow", f"paper p.{page or '?'} vs {loc}",
                f"Hyper-parameter scan (deep_checks.py): the paper says \"{s.strip()[:200]}\" ({name} = {pv:g}); "
                f"the code sets `{snippet.strip()}` at {loc} ({name} = {v:g}). Same parameter, different value.",
                f"Which {name} produced the reported results, {pv:g} or {v:g}?", check="regex scan"))
            break
    return findings, facts


# ------------------------------------------------------------------------------------------------ seeds
SEED_RE = re.compile(r"random_state\s*=\s*\d+|np\.random\.seed\(\s*\d+|random\.seed\(\s*\d+|set\.seed\(\s*\d+|"
                     r"torch\.manual_seed\(\s*\d+|tf\.(?:compat\.v1\.)?(?:random\.)?set_(?:random_)?seed\(\s*\d+|"
                     r"seed\s*=\s*\d+|rng\s*=\s*np\.random\.default_rng\(\s*\d+")


def seeds(root: Path, inv: dict) -> dict:
    """Where the authors' code fixes a random seed (B06-v1110 F5 said 'without a fixed seed'; run.ipynb sets
    random_state=12306). Keys are files, values the distinct seed statements."""
    out = {}
    for f in inv["files"]:
        if not f.lower().endswith((".py", ".r", ".rmd", ".ipynb", ".m", ".jl")) or AGENT_FILE.search(f):
            continue
        hits = sorted({re.sub(r"\s+", "", h) for h in SEED_RE.findall(read_text(root / f, 5_000_000))})
        if hits:
            out[f] = hits[:8]
    return out


# ------------------------------------------------------------------------------------------------ confusion counts
def confusion(root: Path, inv: dict) -> dict:
    out = {}
    for f in inv["files"]:
        if not f.startswith(("results", "result", "output")):
            continue
        t = read_text(root / f, 2_000_000)
        g = {k: int(v) for k, v in re.findall(r"(?m)^\s*(EQUAL|TOTAL POSITIVE|TRUE POSITIVE|ACTUAL POSITIVE|TOTAL)\s*=\s*(\d+)",
                                              t)}
        if {"TRUE POSITIVE", "TOTAL POSITIVE", "ACTUAL POSITIVE", "TOTAL"} <= set(g):
            tp, pp, ap, n = g["TRUE POSITIVE"], g["TOTAL POSITIVE"], g["ACTUAL POSITIVE"], g["TOTAL"]
            fp, fn = pp - tp, ap - tp
            tn = n - tp - fp - fn
            out[f] = {"TP": tp, "FP": fp, "FN": fn, "TN": tn, "correct": tp + tn, "total": n,
                      "stored_correct": g.get("EQUAL"), "consistent": g.get("EQUAL") in (None, tp + tn)}
    return out


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="materials folder (code/, data/, results/)")
    ap.add_argument("--paper", type=Path, default=None, help="paper text (bp_work/paper.md)")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()
    root = args.root
    if not root.exists():
        print(f"no such folder: {root}", file=sys.stderr)
        return 1
    paper = read_text(args.paper) if args.paper and args.paper.exists() else ""
    inv = inventory(root)
    facts = {"inventory": {"n_files": len(inv["files"]), "env_pinned": inv["env_pinned"],
                           "agent_files": inv["agent_files"], "n_author_code": inv["n_author_code"]},
             "headers": inv["headers"]}
    findings: list[dict] = []
    steps = [("r_trace", lambda: r_trace(root, inv)), ("rmd_outputs", lambda: rmd_outputs(root, inv)),
             ("literal_arrays", lambda: literal_arrays(root)), ("pipeline", lambda: pipeline(root, inv)),
             ("metric_defs", lambda: metric_defs(root)), ("weights", lambda: weights(root, inv, paper)),
             ("params", lambda: params(root, paper))]
    ran = []
    for name, fn in steps:
        try:
            fs, fa = fn()
            findings += fs
            facts[name] = fa if name not in ("r_trace", "rmd_outputs") else None
            if name in ("r_trace", "rmd_outputs"):
                facts.update(fa)
            ran.append(f"{name}:{len(fs)}")
        except Exception as e:  # one failing check must not stop the others
            ran.append(f"{name}:error {type(e).__name__}: {e}")
    try:
        facts["surv_groups"] = surv_groups(root, inv, facts)
    except Exception as e:
        ran.append(f"surv_groups:error {e}")
    try:
        facts["seeds"] = seeds(root, inv)
    except Exception as e:
        ran.append(f"seeds:error {e}")
    try:
        facts["confusion"] = confusion(root, inv)
        for f, c in facts["confusion"].items():
            if not c["consistent"]:
                findings.append(finding("stat_inconsistency", "yellow", f,
                                        f"Confusion counts (deep_checks.py): TP {c['TP']}, FP {c['FP']}, FN {c['FN']}, "
                                        f"TN {c['TN']} give {c['correct']} correct of {c['total']}; the file prints "
                                        f"{c['stored_correct']}.", "Which count is right?", check="recount"))
    except Exception as e:
        ran.append(f"confusion:error {e}")
    facts = {k: v for k, v in facts.items() if v not in (None, {}, [])}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "deep.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "code_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str),
                                              encoding="utf-8")
    print(f"deep_checks: {len(findings)} findings -> {args.out / 'deep.findings.json'}; facts -> "
          f"{args.out / 'code_facts.json'}")
    print("  " + "  ".join(ran))
    for f in findings:
        print(f"  [{f['severity']}] {f['type']} {f['location']}: {f['evidence'][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

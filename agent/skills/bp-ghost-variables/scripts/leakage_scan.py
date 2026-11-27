#!/usr/bin/env python3
"""Static scan of analysis code for reproducibility and leakage risks.

Python files are parsed with `ast`; R files and notebooks get lighter regex checks.

Flags
  seed_missing         RNG / estimator / splitter used without a seed (random_state)
  leakage_risk         scaler, PCA, imputer or feature selector fitted before the train/test split
  grouped_split_risk   random split while the code mentions grouped entities (patients, compositions, ...);
                       grey, a keyword match only
  exclusion_rule       data-exclusion steps (dropna, quantile / z-score filters): candidate multiverse forks
  dependency_unpinned  requirements without pinned versions, or no dependency file at all
  test_set_reuse       a model, selector or importance estimator fitted on test-named data, or a loop that
                       sweeps a setting and scores every candidate on the test set (model selection on test data)
  stale_loop_variable  a loop reads the per-iteration variable of an earlier loop (value left over from that loop)
  duplicate_output     the same object is saved to two different output files

Notebooks: locations read "file.ipynb code cell K line L" (K counts code cells only, from 1).

Static analysis over-reports. Every flag cites file:line; check it by hand.

Usage:
    python leakage_scan.py bp_work/code --out bp_out
Outputs: leakage.findings.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

NEEDS_RANDOM_STATE = {
    "train_test_split", "ShuffleSplit", "StratifiedShuffleSplit", "GroupShuffleSplit",
    "RandomForestClassifier", "RandomForestRegressor", "ExtraTreesClassifier", "ExtraTreesRegressor",
    "GradientBoostingClassifier", "GradientBoostingRegressor", "HistGradientBoostingClassifier",
    "HistGradientBoostingRegressor", "MLPClassifier", "MLPRegressor", "SGDClassifier", "SGDRegressor",
    "KMeans", "MiniBatchKMeans", "TSNE", "UMAP", "RandomizedSearchCV", "XGBClassifier", "XGBRegressor",
    "LGBMClassifier", "LGBMRegressor", "CatBoostClassifier", "CatBoostRegressor", "resample",
    "permutation_importance", "IsolationForest",
}
NEEDS_RANDOM_STATE_IF_SHUFFLE = {"KFold", "StratifiedKFold", "RepeatedKFold"}
SEED_SETTERS = {"seed", "manual_seed", "set_seed", "seed_everything", "manual_seed_all", "set_random_seed"}
UNSEEDED_RNG = re.compile(
    r"^(np\.random|numpy\.random|random|torch)\.(rand|randn|randint|choice|shuffle|permutation|normal|uniform|"
    r"random|sample|randperm|binomial|poisson)$")
PREPROCESSORS = {
    "StandardScaler", "MinMaxScaler", "RobustScaler", "MaxAbsScaler", "Normalizer", "QuantileTransformer",
    "PowerTransformer", "PCA", "TruncatedSVD", "SelectKBest", "SelectPercentile", "SelectFromModel", "RFE",
    "RFECV", "VarianceThreshold", "SimpleImputer", "KNNImputer", "IterativeImputer", "TfidfVectorizer",
    "CountVectorizer", "SMOTE", "RandomOverSampler", "RandomUnderSampler",
}
SPLIT_CLASSES = {"KFold", "StratifiedKFold", "GroupKFold", "StratifiedGroupKFold", "RepeatedKFold",
                 "ShuffleSplit", "StratifiedShuffleSplit", "GroupShuffleSplit", "LeaveOneGroupOut",
                 "LeavePGroupsOut", "TimeSeriesSplit", "PredefinedSplit"}
# Functions that split data or evaluate on held-out folds. `.split()` counts only on a splitter object,
# so str.split() in parsing code is not mistaken for a train/test split.
SPLITTERS = {"train_test_split", "cross_val_score", "cross_validate", "cross_val_predict",
             "GridSearchCV", "RandomizedSearchCV"} | SPLIT_CLASSES
GROUP_AWARE = {"GroupKFold", "GroupShuffleSplit", "LeaveOneGroupOut", "LeavePGroupsOut", "StratifiedGroupKFold",
               "TimeSeriesSplit", "PredefinedSplit"}
RANDOM_SPLITS = {"train_test_split", "KFold", "StratifiedKFold", "ShuffleSplit", "StratifiedShuffleSplit"}
GROUP_WORDS = re.compile(
    r"\b(patient|patients|subject|subjects|donor|donors|participant|scaffold|cluster_id|composition|formula|"
    r"material_id|mp_id|mpid|family|families|site|hospital|batch|plate|mouse|mice|animal|individual|user_id|"
    r"session)\b",
    re.I)
EXCLUSION_CALLS = {"dropna", "drop_duplicates", "quantile", "zscore", "query"}
TEST_NAME = re.compile(r"(?i)(test|valid|holdout|hold_out|external)")
TEST_INDEX_NAME = re.compile(r"(?i)(test|val|valid|te)_?(idx|index|ind|ids)")
FIT_CALLS = {"fit", "fit_transform", "fit_resample"}
SCORE_CALLS = {"score", "predict", "predict_proba", "decision_function", "evaluate", "roc_auc_score",
               "accuracy_score", "f1_score", "r2_score", "mean_squared_error", "mean_absolute_error",
               "concordance_index", "concordance_index_censored", "average_precision_score", "log_loss",
               "balanced_accuracy_score", "matthews_corrcoef"}
SAVE_CALLS = {"to_csv", "to_excel", "to_parquet", "to_pickle", "to_json", "to_feather", "savetxt", "save",
              "dump", "write_csv", "write.csv", "saveRDS", "write.table", "write_xlsx"}


def dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{dotted(node.func)}()"
    return "?"


def leaf(name: str) -> str:
    return name.rstrip("()").split(".")[-1]


def mk(ftype, severity, location, evidence, question=None):
    return {"module": "ghost", "type": ftype, "severity": severity, "claim_ids": [], "location": location,
            "evidence": evidence, "source": "script:leakage_scan.py", "question_for_authors": question}


class Scanner(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.var_class: dict[str, str] = {}
        self.calls: list[tuple[int, str, ast.Call]] = []
        self.seeded = False

    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.Call):
            cls = leaf(dotted(node.value.func))
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.var_class[t.id] = cls
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        name = dotted(node.func)
        self.calls.append((node.lineno, name, node))
        if leaf(name) in SEED_SETTERS:
            self.seeded = True
        if leaf(name) == "default_rng" and (node.args or node.keywords):
            self.seeded = True
        self.generic_visit(node)


def kw(node: ast.Call, key: str):
    for k in node.keywords:
        if k.arg == key:
            return k.value
    return None


def is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def arg_names(node: ast.Call) -> list[str]:
    """Names (including the base of subscripts/attributes) passed as arguments."""
    names = []
    for a in list(node.args) + [k.value for k in node.keywords]:
        base = a
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            base = base.value
        if isinstance(base, ast.Name):
            names.append(base.id)
    return names


def is_test_name(name: str) -> bool:
    return bool(TEST_NAME.search(name)) and "train" not in name.lower()


def loop_is_resampling(loop: ast.For) -> bool:
    """CV / repeated-split loops define their own test fold; they are not test-set reuse."""
    it = ast.dump(loop.iter)
    if re.search(r"attr='split'|KFold|ShuffleSplit|LeaveOne|train_test_split|bootstrap|resample", it):
        return True
    for n in ast.walk(loop):
        if isinstance(n, ast.Call) and leaf(dotted(n.func)) in ({"train_test_split", "split"} | SPLIT_CLASSES):
            return True
        if isinstance(n, ast.Subscript):
            sl = n.slice
            for m in ast.walk(sl):
                if isinstance(m, ast.Name) and TEST_INDEX_NAME.search(m.id):
                    return True
    return False


def per_iteration_vars(loop) -> dict[str, int]:
    """Variables assigned from an expression indexed by the loop variable (x <- data[[i]])."""
    if not isinstance(loop.target, ast.Name):
        return {}
    var = loop.target.id
    out = {}
    for n in ast.walk(loop):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            for m in ast.walk(n.value):
                if isinstance(m, ast.Subscript) and any(isinstance(q, ast.Name) and q.id == var for q in ast.walk(m.slice)):
                    out.setdefault(n.targets[0].id, n.lineno)
                    break
    return out


def scan_python(path: Path, rel: str, source: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [mk("rerun_failed", "grey", f"{rel}:{e.lineno}", f"Could not parse Python file: {e.msg}")]
    sc = Scanner(rel)
    sc.visit(tree)
    out = []

    # --- seeds ---
    for line, name, node in sc.calls:
        lf = leaf(name)
        if lf in NEEDS_RANDOM_STATE and kw(node, "random_state") is None and kw(node, "seed") is None:
            out.append(mk("seed_missing", "yellow", f"{rel}:{line}", f"{lf}(...) called without random_state.",
                          "Which random seed(s) were used for the reported results?"))
        elif lf in NEEDS_RANDOM_STATE_IF_SHUFFLE and is_true(kw(node, "shuffle")) and kw(node, "random_state") is None:
            out.append(mk("seed_missing", "yellow", f"{rel}:{line}", f"{lf}(shuffle=True) without random_state."))
        elif UNSEEDED_RNG.match(name.rstrip("()")) and not sc.seeded:
            out.append(mk("seed_missing", "yellow", f"{rel}:{line}", f"{name}(...) used and no global seed is set in this file."))
        elif lf == "default_rng" and not (node.args or node.keywords):
            out.append(mk("seed_missing", "yellow", f"{rel}:{line}", "np.random.default_rng() created without a seed."))

    # --- preprocessing fitted before the split ---
    def is_split_call(name: str) -> bool:
        if leaf(name) in SPLITTERS:
            return True
        if leaf(name) == "split" and "." in name:
            receiver = name.rsplit(".", 1)[0]
            cls = leaf(receiver) if receiver.endswith("()") else sc.var_class.get(receiver, "")
            return cls in SPLIT_CLASSES
        return False

    split_lines = [line for line, name, _ in sc.calls if is_split_call(name)]
    first_split = min(split_lines) if split_lines else None
    if first_split is not None:
        for line, name, node in sc.calls:
            if leaf(name) not in ("fit", "fit_transform", "fit_resample") or line >= first_split:
                continue
            receiver = name.rsplit(".", 1)[0] if "." in name else ""
            cls = leaf(receiver) if receiver.endswith("()") else sc.var_class.get(receiver, "")
            if cls in PREPROCESSORS:
                out.append(mk("leakage_risk", "yellow", f"{rel}:{line}",
                              f"{cls}.{leaf(name)} is called before the first split (line {first_split}); "
                              "if it sees test data, information leaks into training.",
                              "Were preprocessing steps fitted on the training data only?"))

    # --- random split on grouped data ---
    used = {leaf(n) for _, n, _ in sc.calls}
    random_split = sorted(used & RANDOM_SPLITS)
    if random_split and not (used & GROUP_AWARE):
        words = sorted({m.group(1).lower() for m in GROUP_WORDS.finditer(source)})
        if words:
            line = min(l for l, n, _ in sc.calls if leaf(n) in RANDOM_SPLITS)
            # v1.10.5: grey. A keyword is not evidence of repeated groups (B06-v1101/v1103: one sample per
            # participant, both false). Yellow needs a group column with repeated values, checked in stage 3
            # and written as an llm finding (B12-v1103 F10: 500 recordings x 23 chunks).
            out.append(mk("grouped_split_risk", "grey", f"{rel}:{line}",
                          f"Random split ({', '.join(random_split)}) while the code mentions {', '.join(words[:6])}; "
                          "samples sharing a group may fall on both sides of the split (keyword match only: check "
                          "whether the data has a group column with repeated values).",
                          "Were samples from the same group kept within one side of the split?"))

    # --- exclusion rules (multiverse candidates), one finding per file ---
    excl = [(line, leaf(name)) for line, name, _ in sc.calls if leaf(name) in EXCLUSION_CALLS]
    if excl:
        listed = ", ".join(f"{fn}@{line}" for line, fn in excl[:12])
        out.append(mk("exclusion_rule", "grey", f"{rel}:{excl[0][0]}",
                      f"{len(excl)} data-exclusion step(s): {listed}. Candidate forks for the multiverse stage."))

    # --- test data used for fitting or for choosing a setting ---
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
    resampling = [l for l in loops if loop_is_resampling(l)]
    in_resampling = {id(n) for l in resampling for n in ast.walk(l)}
    for line, name, node in sc.calls:
        if leaf(name) in FIT_CALLS and id(node) not in in_resampling:
            first = arg_names(node)[:1]
            if first and is_test_name(first[0]):
                out.append(mk("test_set_reuse", "yellow", f"{rel}:{line}",
                              f"{name}({first[0]}, ...) fits on test-named data. If the result feeds feature "
                              "selection, tuning or the final model, the test set is no longer independent.",
                              "Was any model, selector or importance estimate fitted on the test set?"))
    for loop in loops:
        if loop in resampling or not isinstance(loop.iter, ast.Call) or leaf(dotted(loop.iter.func)) != "range":
            continue
        fits = [n for n in ast.walk(loop) if isinstance(n, ast.Call) and leaf(dotted(n.func)) in FIT_CALLS]
        scored = [n for n in ast.walk(loop) if isinstance(n, ast.Call) and leaf(dotted(n.func)) in SCORE_CALLS
                  and any(is_test_name(a) for a in arg_names(n))]
        if fits and scored:
            out.append(mk("test_set_reuse", "yellow", f"{rel}:{loop.lineno}",
                          f"Loop at line {loop.lineno} refits a model for each value of a setting and scores every "
                          f"candidate on test-named data (line {scored[0].lineno}). If the reported setting was picked "
                          "from this sweep, the test set was used for model selection and its score is optimistic.",
                          "How was the reported setting chosen: on training/validation data or on the test set?"))

    # --- a loop reading the per-iteration variable of an earlier loop ---
    top_loops = [n for n in tree.body if isinstance(n, ast.For)]
    seen: dict[str, tuple[int, str]] = {}
    for loop in top_loops:
        assigned = {t.id for n in ast.walk(loop) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        mine = per_iteration_vars(loop)
        for n in ast.walk(loop):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in seen and n.id not in assigned and mine:
                ln, src_loop = seen[n.id]
                out.append(mk("stale_loop_variable", "yellow", f"{rel}:{n.lineno}",
                              f"The loop at line {loop.lineno} reads {n.id}, the per-iteration variable of the loop at "
                              f"line {src_loop} (assigned at line {ln}); this loop's own per-iteration variable is "
                              f"{', '.join(sorted(mine))}. It uses the value left over from the earlier loop.",
                              f"Should line {n.lineno} use {sorted(mine)[0]} instead of {n.id}?"))
                break
        for v, ln in mine.items():
            seen[v] = (ln, loop.lineno)

    # --- the same object saved to two different files ---
    saves: dict[str, list[tuple[int, str]]] = {}
    for line, name, node in sc.calls:
        if leaf(name) in SAVE_CALLS and "." in name:
            receiver = name.rsplit(".", 1)[0]
            path_arg = next((a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)), None)
            if path_arg and receiver.isidentifier():
                saves.setdefault(receiver, []).append((line, path_arg))
    loaded = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    stored = {t.id: n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    for receiver, items in saves.items():
        paths = sorted({p for _, p in items})
        if len(paths) > 1:
            unused = [v for v in stored if v not in loaded and v != receiver and v[:6] == receiver[:6]]
            extra = f" {unused[0]} is assigned (line {stored[unused[0]]}) but never used — probably the object meant for one of them." if unused else ""
            out.append(mk("duplicate_output", "yellow", f"{rel}:{items[-1][0]}",
                          f"{receiver} is written to {len(paths)} different files: {', '.join(paths)}.{extra}",
                          "Are the released output files the intended ones?"))
    return out


RMD_FENCE = re.compile(r"^\s*```+\s*\{?\s*r\b", re.I)
RMD_END = re.compile(r"^\s*```+\s*$")
INLINE_R = re.compile(r"`r ([^`]*)`")


def rmd_code_only(source: str) -> str:
    """v1.11.6: keep only R code chunks and inline `r ...` code of an R Markdown/Quarto file, blanking prose line by line
    so line numbers stay the same. Prose such as "the final sample (see Table 3)" matched the RNG pattern
    (B10-v1114 F1: seed_missing on manuscript.Rmd:912, a paragraph of the Results)."""
    lines, inside = [], False
    for line in source.split("\n"):
        if not inside and RMD_FENCE.match(line):
            inside = True
            lines.append("")
        elif inside and RMD_END.match(line):
            inside = False
            lines.append("")
        elif inside:
            lines.append(line)
        else:
            lines.append("; ".join(INLINE_R.findall(line)))
    return "\n".join(lines)


def strip_r_comments(source: str) -> str:
    return "\n".join(re.sub(r"#.*$", "", ln) if "#" in ln and not re.search(r"[\"'][^\"']*#", ln) else ln
                     for ln in source.split("\n"))


def scan_r(rel: str, source: str) -> list[dict]:
    out = []
    if rel.lower().endswith((".rmd", ".qmd")):
        source = rmd_code_only(source)
    code = strip_r_comments(source)
    uses_rng = re.search(r"\b(sample|rnorm|runif|rbinom|createDataPartition|initial_split|randomForest|ranger|kmeans)\s*\(",
                         code)
    if uses_rng and not re.search(r"\bset\.seed\s*\(", code):
        line = code.count("\n", 0, uses_rng.start()) + 1
        out.append(mk("seed_missing", "yellow", f"{rel}:{line}", "Random functions used and set.seed() never called."))
    # dplyr::filter()/subset() are mostly ordinary row selection; report one grey note per file
    # (like the Python scan) and list only calls that look like exclusions (NA removal, outlier cut-offs).
    excl = []
    for m in re.finditer(r"\b(na\.omit|drop_na|filter|subset)\s*\(", source):
        line = source.count("\n", 0, m.start()) + 1
        args = source[m.end(): m.end() + 160].split(")")[0]
        if m.group(1) in ("na.omit", "drop_na") or re.search(r"is\.na|sd\s*\(|scale\s*\(|quantile|IQR|outlier|exclu|abs\s*\(", args):
            excl.append((line, m.group(1)))
    if excl:
        listed = ", ".join(f"{fn}@{line}" for line, fn in excl[:12])
        out.append(mk("exclusion_rule", "grey", f"{rel}:{excl[0][0]}",
                      f"{len(excl)} data-exclusion step(s): {listed}. Candidate forks for the multiverse stage."))
    out += r_stale_loops(rel, source)
    return out


R_ID = r"[A-Za-z.][\w.]*"


def r_strip(source: str) -> str:
    """Blank out comments and string literals, keeping line numbers and offsets."""
    out, i, n = [], 0, len(source)
    while i < n:
        c = source[i]
        if c == "#":
            j = source.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif c in "\"'":
            j = i + 1
            while j < n and source[j] != c:
                j += 2 if source[j] == "\\" else 1
            out.append(c + re.sub(r"[^\n]", " ", source[i + 1:j]) + (c if j < n else "")); i = j + 1
        else:
            out.append(c); i += 1
    return "".join(out)


def r_loops(code: str) -> list[tuple[str, int, int]]:
    """(loop variable, body start, body end) for each for-loop with a braced body."""
    loops = []
    for m in re.finditer(r"\bfor\s*\(\s*(" + R_ID + r")\s+in\b", code):
        brace = code.find("{", m.end())
        if brace < 0:
            continue
        depth, j = 0, brace
        while j < len(code):
            depth += {"{": 1, "}": -1}.get(code[j], 0)
            if depth == 0:
                break
            j += 1
        loops.append((m.group(1), brace, j))
    return loops


def r_stale_loops(rel: str, source: str) -> list[dict]:
    code = r_strip(source)
    loops = r_loops(code)
    out, seen = [], {}
    for k, (var, a, b) in enumerate(loops):
        body = code[a:b]
        nested = any(a2 > a and b2 < b for _, a2, b2 in loops)
        mine = {}
        for m in re.finditer(r"(?m)^\s*(" + R_ID + r")\s*(?:<<?-|=)\s*(.*)$", body):
            if re.search(r"\[\[?\s*" + re.escape(var) + r"\s*[\],]", m.group(2)):
                mine.setdefault(m.group(1), a + m.start(1))
        assigned = {m.group(1) for m in re.finditer(r"(?m)^\s*(" + R_ID + r")\s*(?:<<?-|=)(?!=)", body)}
        if mine and not nested:
            for name, (pos, loop_pos) in seen.items():
                if name in assigned:
                    continue
                use = re.search(r"(?<![\w.$])" + re.escape(name) + r"(?![\w.(])", body)
                if use:
                    line = code.count("\n", 0, a + use.start()) + 1
                    loop_line = code.count("\n", 0, a) + 1
                    out.append(mk("stale_loop_variable", "yellow", f"{rel}:{line}",
                                  f"The loop starting at line {loop_line} reads {name}, the per-iteration variable of the "
                                  f"loop at line {code.count(chr(10), 0, loop_pos) + 1}; this loop's own per-iteration "
                                  f"variable is {', '.join(sorted(mine))}. It uses the value left over from the earlier loop.",
                                  f"Should line {line} use {sorted(mine)[0]} instead of {name}?"))
                    break
        for name, pos in mine.items():
            seen[name] = (pos, a)
    return out


def notebook_source(path: Path) -> tuple[str, list[int]]:
    """Code cells joined with one blank line between them; also the first joined line of each cell."""
    nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    cells = [c for c in nb.get("cells", []) if c.get("cell_type") == "code"]
    parts, starts, line = [], [], 1
    for c in cells:
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else src
        body = "\n".join("" if l.lstrip().startswith(("%", "!")) else l for l in src.splitlines())
        starts.append(line)
        parts.append(body)
        line += body.count("\n") + 2
    return "\n\n".join(parts), starts


def to_cell_locations(findings: list[dict], rel: str, starts: list[int]) -> list[dict]:
    def cell(n: int) -> str:
        if not starts:
            return f"line {n}"
        k = max([i for i, s in enumerate(starts) if s <= n] or [0])
        return f"code cell {k + 1} line {n - starts[k] + 1}"
    for f in findings:
        f["location"] = re.sub(re.escape(rel + " (cells joined)") + r":(\d+)",
                               lambda m: f"{rel} {cell(int(m.group(1)))}", f["location"])
        for key in ("evidence", "question_for_authors"):
            if f.get(key):
                f[key] = re.sub(r"\bline (\d+)\b", lambda m: cell(int(m.group(1))), f[key])
    return findings


def scan_dependencies(root: Path) -> list[dict]:
    files = [p for pat in ("requirements*.txt", "environment*.yml", "environment*.yaml", "pyproject.toml",
                           "setup.py", "renv.lock", "DESCRIPTION", "Pipfile.lock", "poetry.lock")
             for p in root.rglob(pat)]
    # v1.10.3: a Dockerfile that pins a base image tag or package versions is a dependency specification
    # (B13-v1102: environment/Dockerfile pins R 4.2.0, survival 3.3-1, pandas 2.0.0 ...; reported as "none found").
    # v1.10.4: the scan is often pointed at code/ while the capsule keeps environment/Dockerfile one level up (B14-v1103)
    roots = [root] + ([root.parent] if root.parent != root and (root.parent / "environment").is_dir() else [])
    if len(roots) > 1:
        files += [p for pat in ("requirements*.txt", "environment*.yml", "environment*.yaml", "renv.lock")
                  for p in (root.parent / "environment").glob(pat)]
    for df in [p for r in roots for p in (r.rglob("Dockerfile*") if r == root else (r / "environment").glob("Dockerfile*"))
               if p.is_file()]:
        text = df.read_text(encoding="utf-8", errors="replace")
        pinned_base = re.search(r"^\s*FROM\s+\S+:(?!latest\b)[\w.\-]+", text, re.M | re.I)
        pinned_pkgs = re.search(r"[\w.\-]+\s*(?:==|=)\s*\d[\w.\-]*", text)
        if pinned_base or pinned_pkgs:
            files.append(df)
    if not files:
        return [mk("dependency_unpinned", "yellow", root.name + "/",
                   "No dependency specification found (requirements.txt, environment.yml, renv.lock, ...).",
                   "Which software and library versions were used?")]
    out = []
    for req in [f for f in files if f.name.startswith("requirements")]:
        loose = []
        for raw in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#")[0].strip()
            if line and not line.startswith(("-", "git+", "http")) and "==" not in line and "@" not in line:
                loose.append(line)
        if loose:
            out.append(mk("dependency_unpinned", "grey", str(req.relative_to(root)),
                          f"{len(loose)} unpinned requirement(s): {', '.join(loose[:8])}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code_dir", type=Path)
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    args = ap.parse_args()

    root = args.code_dir
    findings = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if (not path.is_file() or any(p.startswith(".") for p in parts)
                or any(p in ("site-packages", "venv", "env", "__pycache__", "node_modules") for p in parts)):
            continue
        rel = str(path.relative_to(root))
        suffix = path.suffix.lower()
        try:
            if suffix == ".py":
                findings += scan_python(path, rel, path.read_text(encoding="utf-8", errors="replace"))
            elif suffix == ".ipynb":
                src, starts = notebook_source(path)
                findings += to_cell_locations(scan_python(path, rel + " (cells joined)", src), rel, starts)
            elif suffix in (".r", ".rmd", ".qmd"):
                findings += scan_r(rel, path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:  # keep scanning other files
            findings.append(mk("rerun_failed", "grey", rel, f"Scanner error: {e}"))
    findings += scan_dependencies(root)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "leakage.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    by_type = {}
    for f in findings:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    print(f"files scanned under {root}; findings by type: {by_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

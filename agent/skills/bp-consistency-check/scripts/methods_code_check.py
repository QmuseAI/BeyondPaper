#!/usr/bin/env python3
"""Check that every analysis method named in the Methods has a matching call in the released code.

A paper often says "publication bias was assessed with Egger's test" or "p-values were corrected
with Benjamini-Hochberg"; whether the code actually does it is rarely checked. This script finds
known method names in Methods-like sections and greps the code (R, Python, Stata, notebooks) for the
functions that implement them. A method with no matching call is a yellow phantom_experiment
(described, but not in the released code) for the agent to confirm.

It also checks the confidence level: if the paper reports 95% CIs but the code takes the 5th/95th
percentiles of bootstrap estimates (a 90% interval) and never 2.5/97.5, that is a yellow param_mismatch.
Notebooks are read as their code cells only (outputs are ignored).

Many analyses are run in point-and-click or web tools (MetaboAnalyst, GraphPad Prism, SPSS, SIMCA, RevMan,
ImageJ, ...), so no script can exist for them. When the paragraph that names a method also names such a
tool, or the Methods say that the statistics were done in it, a method missing from the code is a grey
missing_artifact (no script to check; ask for the project file or exported settings), not a yellow
phantom_experiment (B06-v17: six yellow findings for analyses the Methods place in MetaboAnalyst).
The tools found are written to gui_tools.json; build_passport.py uses it to grey hand-written
phantom_experiment findings about the same analyses.

Usage:
    python methods_code_check.py bp_work/paper.md --code bp_work --out bp_out
Outputs: methods_code.csv, methods_code.findings.json, gui_tools.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# method label, regex in the paper text, regex in the code
METHODS = [
    ("Egger's test", r"\begger", r"regtest|egger"),
    ("Begg's test", r"\bbegg", r"ranktest|begg"),
    ("trim-and-fill", r"trim[- ]and[- ]fill", r"trimfill|trim_fill|trim.and.fill"),
    ("funnel plot", r"funnel plot", r"funnel"),
    ("REML estimator", r"\bREML\b|restricted maximum likelihood", r"REML|reml"),
    ("DerSimonian-Laird", r"dersimonian|\bDL\b estimator", r"[\"']DL[\"']|dersimonian"),
    ("Bonferroni correction", r"bonferroni", r"bonferroni"),
    ("Holm correction", r"\bholm\b", r"holm"),
    ("Benjamini-Hochberg / FDR", r"benjamini|hochberg|false discovery rate|\bFDR\b", r"[\"']BH[\"']|fdr|benjamini|fdr_bh"),
    ("bootstrap", r"bootstrap", r"boot|bootstrap|resample"),
    ("k-fold cross-validation", r"cross[- ]validation|\d+[- ]fold", r"KFold|StratifiedKFold|GroupKFold|cross_val|cv\s*=|trainControl|cv\.glmnet|createFolds|n_splits"),
    ("LASSO", r"\blasso\b|L1[- ](?:penalt|regulari)", r"Lasso|glmnet|penalty\s*=\s*[\"']l1|alpha\s*=\s*1\b"),
    ("random forest", r"random forest", r"RandomForest|randomForest|ranger"),
    ("support vector machine", r"support vector|\bSVM\b", r"SVC|SVR|svm\("),
    ("gradient boosting / XGBoost", r"xgboost|gradient boost|lightgbm", r"xgb|XGB|GradientBoosting|lgb|lightgbm|catboost"),
    ("logistic regression", r"logistic regression", r"LogisticRegression|binomial|logit"),
    ("Cox regression", r"\bcox\b", r"coxph|CoxPH|lifelines"),
    ("Kaplan-Meier", r"kaplan[- ]meier", r"survfit|KaplanMeier"),
    ("linear mixed model", r"mixed[- ]effects? model|linear mixed|multilevel model", r"lmer|glmer|MixedLM|lme\(|mixedlm"),
    ("t-test", r"\bt[- ]test", r"t\.test|ttest"),
    ("chi-square test", r"chi[- ]?squared? test|χ2 test", r"chisq|chi2|rma\(|QE"),
    ("Mann-Whitney / Wilcoxon", r"mann[- ]whitney|wilcoxon", r"wilcox|mannwhitney|wilcoxon|ranksums"),
    ("ANOVA", r"\banova\b|analysis of variance", r"aov\(|anova|f_oneway|anova_lm"),
    ("multiple imputation", r"multiple imputation|\bMICE\b", r"mice|IterativeImputer|impute"),
    ("SMOTE / oversampling", r"\bsmote\b|oversampl", r"SMOTE|oversample|RandomOverSampler"),
    ("permutation importance", r"permutation importance", r"permutation_importance|PermutationImportance|perm"),
    ("SHAP", r"\bshap\b", r"shap"),
    ("propensity score", r"propensity score", r"matchit|propensity|PSM"),
    ("ROC / AUC", r"\bROC\b|\bAUROC\b|\bAUC\b", r"roc_auc|roc_curve|roc\(|pROC|auc"),
    ("standardisation of features", r"standardi[sz]ed (?:features|predictors|variables|covariates)|z[- ]score", r"StandardScaler|scale\(|zscore"),
]
# point-and-click / web tools; (label, regex, code-file suffixes that mean the tool was scripted)
GUI_TOOLS = [
    ("MetaboAnalyst", r"metabo\s?analyst", ()),
    ("GraphPad Prism", r"graph\s?pad|\bprism\s+(?:\d|software|version|v\d)", ()),
    ("SPSS", r"\bSPSS\b", (".sps",)),
    ("SIMCA", r"\bSIMCA(?:-P)?\b", ()),
    ("JMP", r"\bJMP\b", (".jsl",)),
    ("Minitab", r"\bminitab\b", ()),
    ("Galaxy", r"\bgalaxy\s+(?:platform|server|web|project)|usegalaxy", ()),
    ("JASP", r"\bJASP\b", ()),
    ("jamovi", r"\bjamovi\b", ()),
    ("RevMan", r"\brevman\b|review\s+manager", ()),
    ("Comprehensive Meta-Analysis", r"comprehensive\s+meta[- ]analysis", ()),
    ("MedCalc", r"\bmedcalc\b", ()),
    ("OriginPro", r"\borigin\s?pro\b|\borigin\s+(?:\d{4}|software)", ()),
    ("Statistica", r"\bstatistica\b", ()),
    ("Stata", r"\bstata\b", (".do", ".ado")),
    ("SAS", r"\bSAS\b(?:\s+(?:software|version|institute|\d))", (".sas",)),
    ("G*Power", r"g\s?\*\s?power", ()),
    ("ImageJ / Fiji", r"\bimagej\b|\bfiji\b", (".ijm",)),
    ("FlowJo", r"\bflowjo\b", ()),
    ("Cytoscape", r"\bcytoscape\b", ()),
    ("Metascape", r"\bmetascape\b", ()),
    ("DAVID", r"\bDAVID\b(?:\s+(?:bioinformatics|database|v\d|\d))?", ()),
    ("STRING", r"\bSTRING\s+(?:database|v\d|\d|network)", ()),
    ("Ingenuity Pathway Analysis", r"[Ii]ngenuity|\bIPA\b", ()),
    ("Perseus", r"\bperseus\b", ()),
    ("MaxQuant", r"\bmaxquant\b", ()),
    ("Skyline", r"\bskyline\b", ()),
    ("Progenesis", r"\bprogenesis\b", ()),
    ("Compound Discoverer", r"compound\s+discoverer", ()),
    ("XCMS Online", r"xcms\s+online", ()),
    ("GNPS", r"\bGNPS\b", ()),
    ("MZmine", r"\bmzmine\b", ()),
    ("NVivo / ATLAS.ti / MAXQDA", r"\bnvivo\b|atlas\.ti|\bmaxqda\b", ()),
    ("Weka / Orange / KNIME", r"\bweka\b|\borange\s+(?:data\s+mining|\d)|\bknime\b", ()),
    ("GSEA desktop", r"\bGSEA\b\s+(?:software|desktop|v\d|\d)", ()),
    ("Partek", r"\bpartek\b", ()),
    ("QuPath", r"\bqupath\b", ()),
]
GLOBAL_STATS = re.compile(r"(?:statistic|analys[ie]s|tests?)\b[^.]{0,80}\b(?:perform|conduct|carri|done|run|calculat|comput)"
                          r"[^.]{0,40}\b(?:using|with|in|by)\b"
                          # v1.10.3: "GraphPad Prism, R, SPSS ... were used to conduct tests" (Chen et al. 2024)
                          r"|\b(?:were|was)\s+(?:used|employed|applied)\s+(?:to|for)\s+(?:conduct|perform|run|carry\s+out|do)?"
                          r"[^.]{0,30}\b(?:tests?|analys[ie]s|statistic)", re.I)


def find_gui_tools(text: str, code_suffixes: set[str]) -> list[dict]:
    """GUI/web tools named in the Methods, each with the paragraph it appears in."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for label, pat, scripted in GUI_TOOLS:
        if scripted and code_suffixes & set(scripted):
            continue  # the tool's own script files are in the materials, so it was not point-and-click
        hits = [p for p in paras if re.search(pat, p, re.I if label not in ("DAVID", "STRING", "SAS", "JMP", "JASP", "SPSS", "GNPS", "IPA") else 0)]
        if not hits:
            continue
        glob = any(GLOBAL_STATS.search(p) and re.search(pat, p, re.I) for p in hits)
        out.append({"tool": label, "paragraphs": hits, "global": glob,
                    "context": " ".join(hits[0].split())[:240]})
    return out


METHOD_HEAD = re.compile(r"^#{1,4}\s+.*(method|material|statistic|analys|procedure|data|model)", re.I)
ANY_HEAD = re.compile(r"^#{1,4}\s+")
CODE_EXT = {".py", ".r", ".rmd", ".qmd", ".ipynb", ".do", ".m", ".jl", ".sas", ".sh"}


def methods_text(paper: str) -> tuple[str, bool]:
    lines, keep, out, found = paper.splitlines(), False, [], False
    for l in lines:
        if ANY_HEAD.match(l):
            keep = bool(METHOD_HEAD.match(l))
            found = found or keep
        if keep:
            out.append(l)
    return ("\n".join(out), True) if found else (paper, False)


CI90 = re.compile(r"(?<![\d.])0?\.05\s*\*|\[\s*int\(\s*0?\.05|percentile\([^)]*,\s*\[?\s*5(?:\.0)?\s*[,\])]"
                  r"|quantile\([^)]*(?<![\d.])0?\.05\b|probs\s*=\s*c\(\s*0?\.05|conf(?:idence)?[._]?level\s*=\s*0?\.90?\b")
CI95 = re.compile(r"(?<![\d.])0?\.025\b|(?<![\d.])0?\.975\b|97\.5|percentile\([^)]*\b2\.5"
                  r"|conf(?:idence)?[._]?level\s*=\s*0?\.95|conf\.int|confint\(")


AGENT_FILE = re.compile(r"(?i)^(repro_|adapter|port_|bp_|_bp|beyondpaper|whatif_|check_|deep_checks|recount|verify_|"
                        r"multiverse|V\d+\b)")
AGENT_PART = {"checks", "paper_agent", ".vscode", "History", "bp_out", "_bp"}


def read_code(p: Path) -> str:
    if p.suffix.lower() == ".ipynb":
        try:
            nb = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            return "\n".join("".join(c.get("source", "")) if isinstance(c.get("source"), list) else c.get("source", "")
                             for c in nb.get("cells", []) if c.get("cell_type") == "code")
        except (ValueError, AttributeError):
            return ""
    return p.read_text(encoding="utf-8", errors="ignore")


def code_lines(src: str):
    for k, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "//", "%")):
            yield k, line.split(" #")[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper", type=Path)
    ap.add_argument("--code", type=Path, required=True, help="folder that contains the released code")
    ap.add_argument("--out", type=Path, default=Path("bp_out"))
    a = ap.parse_args()

    text, sectioned = methods_text(a.paper.read_text(encoding="utf-8", errors="replace"))
    files = [p for p in a.code.rglob("*") if p.is_file() and p.suffix.lower() in CODE_EXT and "bp_out" not in p.parts
             and not AGENT_PART.intersection(p.parts) and not AGENT_FILE.search(p.name)]
    # v1.11.4: the agent's own scripts are not the authors' code. B15-v1113 has no released code at all, yet F1 said
    # "no k-fold call in 2 code file(s)" - the two files were the agent's (deep_checks.py, which grey these flags when
    # there is no author code, had stopped on a SyntaxError).
    code = {str(p.relative_to(a.code)) + (" (code cells joined)" if p.suffix.lower() == ".ipynb" else ""): read_code(p)
            for p in files}
    rows, findings = [], []
    gui = find_gui_tools(text, {p.suffix.lower() for p in files})
    gui_methods: dict[str, list[str]] = {}
    for label, pat_text, pat_code in METHODS:
        m = re.search(pat_text, text, re.I)
        if not m:
            continue
        ctx = " ".join(text[max(0, m.start() - 80): m.end() + 80].split())
        hits = []
        for path, src in code.items():
            for k, line in code_lines(src):
                if re.search(pat_code, line):
                    hits.append(f"{path}:{k}")
                    break
        tools = [g["tool"] for g in gui
                 if g["global"] or any(re.search(pat_text, para, re.I) for para in g["paragraphs"])]
        rows.append({"method": label, "paper_context": ctx, "code_hits": "; ".join(hits[:5]), "found_in_code": bool(hits)})
        if not hits and tools:
            for t in tools:
                gui_methods.setdefault(t, []).append(label)
            findings.append({"module": "consistency", "type": "missing_artifact", "severity": "grey", "claim_ids": [],
                             "location": "Methods" if sectioned else "paper text",
                             "evidence": f"The paper names {label} (\"…{ctx}…\"); the Methods place this analysis in "
                                         f"{', '.join(tools)}, a point-and-click or web tool, so there is no script to compare. "
                                         f"Not a phantom experiment: the analysis cannot be re-run from the released materials.",
                             "source": "script:methods_code_check.py",
                             "question_for_authors": f"Can you share the {tools[0]} project file or the exported settings "
                                                     f"used for the {label}?"})
            continue
        if files and not hits:
            findings.append({"module": "consistency", "type": "phantom_experiment", "severity": "yellow", "claim_ids": [],
                             "location": "Methods" if sectioned else "paper text",
                             "evidence": f"The paper names {label} (\"…{ctx}…\"), but no matching call (/{pat_code}/) was found "
                                         f"outside comments in {len(files)} code file(s).",
                             "source": "script:methods_code_check.py",
                             "question_for_authors": f"Where in the released code is the {label} performed?"})
    # confidence level: 95% in the paper, 5th/95th percentiles in the code
    full = a.paper.read_text(encoding="utf-8", errors="replace")
    m95 = re.search(r"95\s*%\s*(?:CI|C\.I\.|confidence|credible|bootstrap)", full, re.I)
    if m95 and files:
        ci90 = [(path, k, line.strip()) for path, src in code.items() for k, line in code_lines(src) if CI90.search(line)]
        ci95 = [(path, k) for path, src in code.items() for k, line in code_lines(src) if CI95.search(line)]
        if ci90 and not ci95:
            path, k, line = ci90[0]
            more = f" (and {len(ci90) - 1} similar line(s))" if len(ci90) > 1 else ""
            rows.append({"method": "confidence level", "paper_context": m95.group(0),
                         "code_hits": "; ".join(f"{p}:{n}" for p, n, _ in ci90[:5]), "found_in_code": False})
            findings.append({"module": "consistency", "type": "param_mismatch", "severity": "yellow", "claim_ids": [],
                             "location": f"{path}:{k}",
                             "evidence": f"The paper reports \"{m95.group(0)}\" intervals, but the code computes the interval "
                                         f"with 5%/95% bounds (`{line[:120]}`){more}, which is a 90% interval; no 2.5%/97.5% "
                                         "bounds appear anywhere in the code. Check whether the reported intervals are 90% intervals.",
                             "source": "script:methods_code_check.py",
                             "question_for_authors": "Are the reported confidence intervals 95% or 90% intervals?"})
    # v1.11.9: summary statistic named in the paper vs the one the code computes. Thibault et al. (Delphi) report a
    # "median absolute change" that manuscript.Rmd computes as mean(abs(...)); no run found it.
    stat_mis = [(pw, cw) for pw, cw in (("median", "mean"), ("mean", "median"))
                if re.search(rf"\b{pw}\s+absolute\s+(?:change|difference|deviation|error)", full, re.I)]
    for pw, cw in stat_mis:
        if not files:
            break
        wrong = [(path, k, line.strip()) for path, src in code.items() for k, line in code_lines(src)
                 if re.search(rf"\b{cw}\s*\(\s*(?:np\.)?abs\s*\(", line)]
        right = [1 for src in code.values() for _, line in code_lines(src)
                 if re.search(rf"\b{pw}\s*\(\s*(?:np\.)?abs\s*\(|\bMAD\b|median_abs", line)]
        if wrong and not right:
            m = re.search(rf"(?:[^.]|\.(?=\d))*\b{pw}\s+absolute\s+(?:change|difference|deviation|error)"
                          rf"(?:[^.]|\.(?=\d))*", full, re.I)
            path, k, line = wrong[0]
            more = f" (and {len(wrong) - 1} similar line(s))" if len(wrong) > 1 else ""
            findings.append({"module": "consistency", "type": "stat_inconsistency", "severity": "yellow", "claim_ids": [],
                             "location": f"paper vs {path}:{k}",
                             "evidence": f"The paper reports a {pw} absolute change (\"{' '.join(m.group(0).split())[:220]}\"), "
                                         f"but the code computes {cw}(abs(...)) (`{line[:120]}`){more} and never "
                                         f"{pw}(abs(...)). The reported statistic may be mislabelled.",
                             "source": "script:methods_code_check.py",
                             "question_for_authors": f"Are the reported values {pw}s or {cw}s of the absolute changes?"})
    # v1.11.11: a percentage threshold defined in the text ("consensus as at least 70% of participants") that no
    # comparison in the code uses, while the code compares the same kind of share against a nearby cut-off (>= 0.75).
    # B10-v1119p: the definition was changed in the text and no run reported it.
    thr_txt = re.compile(r"(?i)\b(?:at least|no less than|more than|greater than|exceeding|above|≥|>=)\s*(\d{1,2}(?:\.\d+)?)\s*%")
    thr_ctx = re.compile(r"(?i)consensus|agreement|threshold|cut-?off|criterion|defined|definition")
    share_line = re.compile(r"(?i)perc|pct|prop|percent|share|agree|consensus|essential|ratio|rate")
    cmp_code = re.compile(r"(?:>=|<=|>|<)\s*(0?\.\d+|\d{1,2}(?:\.\d+)?)\b")
    if files:
        code_vals, code_hits = set(), []
        for path, src in code.items():
            for k, line in code_lines(src):
                if not share_line.search(line):
                    continue
                for mm in cmp_code.finditer(line):
                    v = float(mm.group(1))
                    v = v / 100 if v > 1 else v
                    if 0 < v < 1:
                        code_vals.add(round(v, 4))
                        code_hits.append((path, k, line.strip(), v))
        for sent in re.split(r"(?<=[.;])\s+", full):
            if not thr_ctx.search(sent):
                continue
            for mt in thr_txt.finditer(sent):
                t = round(float(mt.group(1)) / 100, 4)
                if t in code_vals or not code_hits:
                    continue
                near = [h for h in code_hits if abs(h[3] - t) <= 0.15]
                if not near:
                    continue
                path, k, line, v = near[0]
                findings.append({"module": "consistency", "type": "param_mismatch", "severity": "yellow",
                                 "claim_ids": [], "location": f"paper vs {path}:{k}",
                                 "evidence": f"The paper defines a threshold of {mt.group(0).strip()} (\"{' '.join(sent.split())[:220]}\"), "
                                             f"but no comparison in the code uses {t:g}; the code compares at {v:g} "
                                             f"(`{line[:120]}`). Check which threshold produced the reported results.",
                                 "source": "script:methods_code_check.py",
                                 "question_for_authors": f"Was the threshold {mt.group(1)}% or {v:g}?"})
                break
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "methods_code.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["method", "paper_context", "code_hits", "found_in_code"])
        w.writeheader()
        w.writerows(rows)
    (a.out / "methods_code.findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    (a.out / "gui_tools.json").write_text(json.dumps(
        {"tools": [{"tool": g["tool"], "global": g["global"], "context": g["context"],
                    "methods": gui_methods.get(g["tool"], [])} for g in gui]}, ensure_ascii=False, indent=2), encoding="utf-8")
    n_phantom = sum(f["type"] == "phantom_experiment" for f in findings)
    print(f"methods named={len(rows)} code files={len(files)} not found in code={n_phantom}"
          + (f" point-and-click tools: {', '.join(g['tool'] for g in gui)}" if gui else "")
          + ("" if files else "  (no code files: nothing to compare)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

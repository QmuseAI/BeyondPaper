#!/usr/bin/env python3
"""Find the paper behind a capsule or a citation once, with a cache (v1.11).

B13-v1105 and B12-v1105 spent 11-13M tokens re-issuing the same Europe PMC / OpenAlex / Crossref queries with
curl until the platform cut the turn. This script runs each query once, writes everything to
bp_work/paper_search.json, and on a second call prints the cache without touching the network.

v1.11.1: a PDF of the paper that the user put in the workspace or the project files is used first (no network);
OSF preprints (MetaArXiv, PsyArXiv, SocArXiv: DOI 10.31222/10.31234/10.31235/10.31219/osf.io/<id>) get their direct
download link; when nothing is found, one web search is allowed (B14: the author hosts the PDF on his own site).

Usage:
    python find_paper.py --title "..." [--author Surname] [--doi 10.x/y] [--work bp_work] [--refresh]
Prints: best match, open-access status, full-text links (PMC / preprint / publisher), and what to do next.
Exit code 0 when a record was found, 2 when nothing matched (use the capsule README/metadata as the paper text,
mark paper-level claims grey and say so in bp_out/assumptions.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "BeyondPaper/1.11 (research audit; mailto:noreply@example.org)"}


def get(url: str, timeout: int = 20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # network errors are recorded, never retried in a loop
        return {"_error": f"{type(e).__name__}: {e}"}


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def sim(a: str, b: str) -> float:
    A, B = set(norm(a).split()), set(norm(b).split())
    return len(A & B) / max(1, len(A | B))


SKIP_PDF_DIRS = {"bp_out", "bp_work_prep", "skills", "node_modules", ".git", "results", "result", "output", "outputs",
                 "figures", "figs", "code", "data", "environment", "_refs"}
OSF_DOI = re.compile(r"(?i)10\.(?:31222|31234|31235|31219|31730|31236|31237)/(?:osf\.io/)?([a-z0-9]{5})\b|osf\.io/([a-z0-9]{5})\b")


def local_pdfs(roots: list[Path]) -> list[str]:
    """PDFs that look like a paper the user supplied: top levels of the workspace and project files."""
    out = []
    for r in roots:
        if not r.exists():
            continue
        for p in list(r.glob("*.pdf")) + list(r.glob("*/*.pdf")) + list(r.glob("*/*/*.pdf")):
            parts = {x.lower() for x in p.relative_to(r).parts[:-1]}
            if parts & SKIP_PDF_DIRS or p.stat().st_size < 20_000:
                continue
            out.append(str(p))
    return sorted(set(out))


def osf_links(*texts) -> list[str]:
    ids = {next(g for g in m.groups() if g).lower() for t in texts if t for m in OSF_DOI.finditer(str(t))}
    return [f"https://osf.io/{i}/download" for i in sorted(ids)]


def search(title: str, author: str | None, doi: str | None) -> dict:
    res = {"query": {"title": title, "author": author, "doi": doi}, "sources": {}}
    q = f'TITLE:"{title}"' if title else ""
    if doi:
        q = f'DOI:"{doi}"'
    if author and not doi:
        q += f" AND AUTH:{author}"
    epmc = get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&resultType=core&pageSize=5&query="
               + urllib.parse.quote(q))
    res["sources"]["europepmc"] = [{"title": r.get("title"), "doi": r.get("doi"), "pmid": r.get("pmid"),
                                    "pmcid": r.get("pmcid"), "isOpenAccess": r.get("isOpenAccess"),
                                    "source": r.get("source"), "year": r.get("pubYear"),
                                    "authors": r.get("authorString")}
                                   for r in (epmc.get("resultList", {}) or {}).get("result", [])] \
        if "_error" not in epmc else epmc
    oa_url = ("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi)) if doi else \
        ("https://api.openalex.org/works?per-page=5&search=" + urllib.parse.quote(title or ""))
    oa = get(oa_url)
    items = [oa] if doi and "_error" not in oa else oa.get("results", []) if "_error" not in oa else []
    res["sources"]["openalex"] = [{"title": w.get("title"), "doi": w.get("doi"),
                                   "oa_status": (w.get("open_access") or {}).get("oa_status"),
                                   "oa_url": (w.get("open_access") or {}).get("oa_url"),
                                   "year": w.get("publication_year")} for w in items] or ({"_error": oa["_error"]}
                                                                                         if "_error" in oa else [])
    if not doi:
        cr = get("https://api.crossref.org/works?rows=5&query.bibliographic=" + urllib.parse.quote(
            (title or "") + (" " + author if author else "")))
        res["sources"]["crossref"] = [{"title": (w.get("title") or [""])[0], "doi": w.get("DOI"),
                                       "type": w.get("type"), "year": (w.get("issued", {}).get("date-parts") or [[None]])[0][0]}
                                      for w in (cr.get("message", {}) or {}).get("items", [])] \
            if "_error" not in cr else cr
    # best match
    cands = []
    for src, rows in res["sources"].items():
        if isinstance(rows, list):
            for r in rows:
                s = 1.0 if doi and r.get("doi") and doi.lower() in str(r.get("doi")).lower() else sim(title or "", r.get("title") or "")
                cands.append((s, src, r))
    cands.sort(key=lambda x: -x[0])
    res["best"] = {"score": round(cands[0][0], 2), "source": cands[0][1], **cands[0][2]} if cands and cands[0][0] >= 0.5 else None
    links = []
    for s, src, r in cands:
        if s < 0.5:
            continue
        if r.get("pmcid"):
            links.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{r['pmcid']}/")
        if r.get("oa_url"):
            links.append(r["oa_url"])
        if r.get("source") == "PPR" and r.get("doi"):
            links.append("https://doi.org/" + r["doi"])
    links += osf_links(doi, *[r.get("doi") for _, _, r in cands if _ >= 0.5])
    res["full_text_links"] = sorted(set(links))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default=None)
    ap.add_argument("--doi", default=None)
    ap.add_argument("--work", type=Path, default=Path("bp_work"))
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--look-in", nargs="*", type=Path, default=[Path("."), Path("/workspace"), Path("/mnt/project-files"),
                                                                Path("/workspace/project_files")],
                    help="folders searched for a PDF of the paper supplied by the user")
    a = ap.parse_args()
    pdfs = local_pdfs(a.look_in)
    if pdfs:
        print("PDF(s) supplied in the workspace / project files - use these as the paper, no search needed:")
        for x in pdfs:
            print("  " + x)
        print("Next: convert the one whose title matches (pdftotext -layout <pdf> bp_work/paper.txt, or the pdf skill) "
              "into bp_work/paper.md with <!-- page N --> markers.")
        if not a.title and not a.doi:
            return 0
    cache = a.work / "paper_search.json"
    if cache.exists() and not a.refresh:
        res = json.loads(cache.read_text(encoding="utf-8"))
        print("(cached; the search ran before - do not query these APIs again)")
    else:
        res = search(a.title, a.author, a.doi)
        a.work.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    b = res.get("best")
    if pdfs:
        print("(record search below is for the citation only; the paper text comes from the supplied PDF)")
    links = res.get("full_text_links") or osf_links(a.doi)
    if not b:
        if links:
            print("No catalogue record, but a preprint download link: " + ", ".join(links))
            return 0
        if not pdfs:
            print("No matching record. You may run ONE web search for '<title> <first author> pdf' and open at most 3 "
                  "results; if one is the paper (author page, institutional repository, preprint), download it once "
                  "(curl -L -o bp_work/paper.pdf URL). Otherwise use the capsule README / metadata / stored outputs as "
                  "the paper text, mark paper-level claims grey, and write this to bp_out/assumptions.md.")
        return 2
    print(f"Best match ({b['source']}, score {b['score']}): {b.get('title')} | doi {b.get('doi')} | {b.get('year')}")
    oa = [r for rows in res["sources"].values() if isinstance(rows, list) for r in rows
          if r.get("isOpenAccess") == "Y" or r.get("oa_status") not in (None, "closed")]
    print("Open access: " + ("yes" if oa else "no record of an open-access copy"))
    print("Full-text links: " + (", ".join(res["full_text_links"]) or "none"))
    if not res["full_text_links"] and not pdfs:
        print("Next: no open full text in the catalogues. You may run ONE web search for '<title> <first author> pdf' and "
              "open at most 3 results (author page, repository, preprint). Nothing found: use the capsule README / "
              "metadata as the paper text (paper-level claims grey). Do not repeat the catalogue query.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

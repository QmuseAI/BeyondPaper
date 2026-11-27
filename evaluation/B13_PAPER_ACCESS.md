# B13: not used in the v1.11.16 benchmark

Paper: Salawu A, et al. "Safety, Immunologic, and Clinical Activity of Durvalumab in Combination with Olaparib or Cediranib in Advanced Leiomyosarcoma: Results of the DAPPER Clinical Trial." Clinical Cancer Research 29(20):4128–4138 (2023). doi:10.1158/1078-0432.CCR-23-1137. PubMed 37566240.
Materials given to the agent: Code Ocean capsule-3894632 (Caryn Geady) and the journal's supplementary material 6879438.zip (Methods S1, Figure S1, Tables S1–S3).

## Decision
B13 is not used. The paper is paywalled and cannot be downloaded; runs stalled while looking for the full text and the SciencePal sandbox crashed several times, so there is no valid result. The blind results count B11, B12, B14, B15 and B16 only.

## What BeyondPaper did when the paper was missing
- It unpacked the capsule and the supplementary files, took the title and authors from the capsule metadata, and ran find_paper.py once (Europe PMC timed out, OpenAlex/Crossref returned 429; no full text).
- It then wrote out its plan instead of guessing: build bp_work/paper.md from the abstract, Supplementary Methods S1 and the capsule README/metadata; record in bp_out/assumptions.md that there is no full text and that every paper-level conclusion is grey; then run prep_paper.py → ledger → stages 2–7 → final_summary.py.
- It stated what it needed to go on: the paper's full text (it could not download it), or a confirmation to run on the capsule alone, and a sandbox that executes commands.
- It did not mark any stage as done and gave no judgement on the paper's conclusions without the text.

## Why the full text is unavailable
Checked 2026-11-19: the journal page shows only the abstract (purchase or subscription required); PubMed lists no free PMC copy.

## Runs attempted (not counted in any score)
B13-v11116p (sandbox archived, output lost), B13-v11116p2 / p2g, B13-v11116p3, B13-v11116r2, r3, r4 (stopped by sandbox failures before final_summary). Their files stay in scoring/results for the record.

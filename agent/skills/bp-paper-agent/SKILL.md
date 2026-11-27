---
name: bp-paper-agent
description: Turn a finished BeyondPaper run into an audited paper agent (stage 8) — tested tools that re-run the paper's analysis with its own or other choices ("what if we split by patient?"), plus claims, findings and failure paths, each answer carrying the claim's traffic light, the reproduction status and the conditions under which the claim breaks; also exported as an MCP server. Use after from_run.py, and whenever the user asks a follow-up question about a paper that already has a bp_out/ folder.
---

# bp-paper-agent — 论文智能体（经审计）

> **Where the scripts are.** Call `load_skill("bp-paper-agent")` first; SciencePal then places this skill at
> `/workspace/skills/bp-paper-agent/`. Set `S=/workspace/skills/bp-paper-agent/scripts`.

Paper2Agent (Miao et al., *Nature* 2026) showed that a paper can become an agent whose tools
reproduce the paper. Its tests take the paper as ground truth, so an error in the paper becomes
part of the agent. This stage builds the agent from what BeyondPaper has checked: tools are
tested against our recorded re-run, and every answer says how far the paper can be trusted on
that point.

## 1. Build (end of every full run, after `from_run.py`)

```bash
python $S/build_paper_agent.py bp_out --spec bp_work/multiverse_spec.yaml
cat bp_out/paper_agent/AGENT_CARD.md
```

It copies the runtime to `bp_out/paper_agent/`, runs four tests (resources present; the adapter
with the paper's choices gives the recorded baseline; two runs give the same numbers; every
recorded breaking change flips the claim again) and enables re-runs only if the baseline and
determinism tests pass. Otherwise the agent is **read-only**: `reproduce` / `whatif` answer from
`multiverse_results.csv` (the exact recorded universe, or the nearest one with the differences
listed) and every such answer starts with "只读模式：不能重跑". Never present a read-only answer as a re-run.

The build then runs `selftest --quick` and writes the fixed follow-up chain `DEMO_CHAIN.md`
(most fragile claim → what-if that flips it → reproduce → findings and precedents → next experiment),
which also fills the what-if cache.

Outputs: `AGENT_CARD.md` (mode, test results, claims with their failure conditions, problems to know
before use, the demo chain), `agent_manifest.json`, `tool_tests.json`, `selftest.json`,
`DEMO_CHAIN.md/json`, `cache/`, `mcp_config.json` (Claude Code / Cursor).

## 2. Answer follow-up questions with the agent (ASK MODE)

When the user asks about a paper that already has `bp_out/`, answer through the agent, not from
memory:

```bash
A="python bp_out/paper_agent/paper_agent.py --bp-out bp_out"
$A claims --text            # which conclusions, which lights
$A claim C2                 # evidence, findings, stress test, failure paths for one claim
$A whatif split=group       # re-run with another analysis choice; does the claim still hold?
$A reproduce                # re-run with the paper's own choices
$A failure-paths            # what the paper did not write down
$A questions                # questions for the authors
$A next                     # next experiments ranked by information gain
$A precedents C1            # verified precedents in other papers (atlas match)
$A demo                     # re-print / rebuild DEMO_CHAIN.md
$A selftest --text          # every tool once -> selftest.json (check this instead of starting a server)
$A serve                    # MCP if `mcp` is installed, else JSON-lines stdio: {"tool":"whatif","args":{"split":"group"}}
```

Re-runs are cached in `paper_agent/cache/` (same question → instant answer; `--no-cache` to force) and
limited by `--timeout` (default 60 s). A timeout is an answer: the agent says so and falls back to the
nearest recorded universe.

Rules:
- Quote numbers only from these outputs, with the file they come from.
- Say which mode answered (`mode`: re-run, cached re-run, read-only, timeout).
- Always pass on the `caveats` and the `notes` (a choice outside the tested multiverse is run, but
  no build test covers it: say so).
- If a question needs a choice the adapter does not expose, say which code change it would need;
  if the user agrees, extend `bp_work/adapter.py` with one new key, re-run `multiverse.py` and
  `build_paper_agent.py`, then answer.
- A what-if that flips a claim is a new failure path: add it to `bp_out/llm.findings.json`
  (type `fragile_conclusion`, the claim id, the command you ran) and re-run `build_passport.py`
  and `from_run.py`.

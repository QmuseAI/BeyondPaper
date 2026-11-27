# BeyondPaper v1.10 — 误报闸门、一页纸简报、论文智能体演示稳定性（2026-11-16）

未部署到 SciencePal。当前 5 个运行（B12、B11-v191、B13、B14、B03-v191）用的是 v1.9.1；等它们跑完再部署，
这样 v1.9.1 和 v1.10 的差别可以在同一批运行上比较（build_passport.py 是确定性的，可以拿 v1.9.1 运行的 bp_out 离线重算）。

## 误报（审计计划第 7、8 项）

起因：B10-v18 的 8 条红灯里，5 条是智能体手写、却把 `source` 标成 `script:numbers_crosscheck.py` 的 archive_mismatch，
v1.9.1 的 gate_cap 只看 source，全部漏过；另有 3 组同一冲突写了两遍。

- `build_passport.py`
  - 手写判定：`llm.findings.json` 里的条目、module 为 `llm`、source 含 `llm` 或 `+`，一律算手写，不论 source 写的是哪个脚本。
  - 红灯检查（red lint）：手写的数字类红灯，证据里少于两个数值 → 黄；依赖"合计/总数"却没写出各项相加 → 黄。
  - 区间方法：证据里两个区间重叠、端点差不到区间宽度的 1/5 → 论文或 `bp_work/si/` 写明了区间算法（`ci_methods.json`）时为灰，否则红降黄（P03 的 [20, 59] vs [19, 57]）。
  - 网页/图形界面工具：Methods 说在 MetaboAnalyst、GraphPad Prism、SPSS 等工具里做的分析，相关的 phantom_experiment → 灰（B06-v17 的 6 条黄灯）。
  - 近似重复合并：同一类型族、证据里的数值重合 ≥ 60% → 合并为一条，保留最严重的一条，claim_ids 取并集。
  - 每一次改动写进 `severity_changes.json`，passport.md 开头列出各类改动的条数；剩余手写红灯 ≥ 3 条时提示人工核对。
  - 结果块同时写到 `bp_out/BP_FINDINGS_BLOCK.txt`（平台经常不输出最后那条消息）。
  - B10-v18 按新规则重算：红灯 8 → 2（注入命中的 34 vs 38，加多重宇宙的脆弱结论），两条注入命中仍在（113 降为黄）。
- `ci_check.py`：新增 `--si`，列出论文和纯文本补充材料里写明的区间算法（Wilson、Clopper-Pearson、Sison-Glaz、BCa 等）→ `ci_methods.json`。
- `methods_code_check.py`：识别 30 多种图形界面/网页工具；方法所在段落提到这类工具（或 Methods 写"统计分析在 X 中完成"）而代码里没有 → 灰色 missing_artifact（问作者要项目文件或导出的参数），不再是黄色 phantom_experiment；工具列表写到 `gui_tools.json`。代码里有该工具自己的脚本（.do、.sas、.sps、.ijm）时不算。
- `atlas.py`：`resembles_auditor` 同样把 module 为 llm 的条目当作手写。

## 一页纸简报（第 1 项）

- 新脚本 `bp-relay-package/scripts/build_brief.py bp_out --audience investor|reviewer|editor` → `brief_<audience>.md`：
  总体灯号和复现等级、主要结论及其灯号、在什么条件下失效（多重宇宙翻转、论文与存档不一致、未报告条件）、
  要对方补充的材料、给作者的 5 个问题（按严重度、是否关联结论、是否脚本产生排序）、下一步验证。只重新组织已有文件。
- `bp_progress.py` 新增 "briefs" 阶段；提示词第 6 阶段最后跑 investor 和 reviewer 两份。

## 论文智能体（第 3 项）

- `serve` 不再依赖 FastMCP：有 `mcp`/`fastmcp` 就用，没有就用 JSON-lines 标准输入输出循环（`--jsonl` 强制）。
- 只读模式：没有 adapter 或 adapter 没过测试时，`reproduce`/`whatif` 从 `multiverse_results.csv` 里找完全匹配或最近的宇宙回答，开头写"只读模式：不能重跑"。
- what-if 结果缓存（`paper_agent/cache/`），每次重跑有超时（默认 60 s），超时时退回最近的已记录宇宙。
- 新命令：`selftest`（每个工具跑一遍 → `selftest.json`，含可用的服务模式）、`demo`（固定 5 步追问链 → `DEMO_CHAIN.md`）、`precedents`。
- `build_paper_agent.py` 建完后自动跑 `selftest --quick` 和 `demo`，AGENT_CARD.md 写明模式、自检结果和追问链。
- 本地用玩具 adapter 测过：重跑模式 4/4 构建测试、自检 9/9；删掉 adapter 后只读模式自检 10/10；无多重宇宙时追问链仍能生成。真正的 MCP 模式没测（本地没装 `mcp`）。

## 提示词 v1.10

- `llm.findings.json` 里的一切都算手写，不要把脚本结果抄进去。
- 第 2 阶段：`ci_check.py --si bp_work/si`；网页工具里做的分析不算 phantom experiment。
- 第 6 阶段最后：`build_brief.py` investor + reviewer。
- 第 7 阶段：读 `selftest.json` 和 `DEMO_CHAIN.md`，总结里写明智能体是重跑模式还是只读模式。
- 结束时 `cat bp_out/BP_FINDINGS_BLOCK.txt` 输出结果块。

## 评测（第 9 项）

新目录 `BeyondPaper_bench/scoring/`（见其中 README）：`save_run.py`（从页面文字/终端输出/下载的 findings.json/旧的 downloads/Bxx.json 提取结果）、
`make_injected_key.py`（原样 vs 注入逐句对比生成注入答案，5 对论文各 4 处，已人工核对）、`keys/real_P0x.json`（17 条已核实真实问题 + 已知误报）、
`score_runs.py`（注入召回、红色召回、真实问题召回、基于 adjudication.csv 的精确率和红色误报数）。替代 `BeyondPaper/benchmark/score.py`。

## 部署清单（等当前运行结束后）

替换 5 个技能：bp-consistency-check、bp-relay-package、bp-paper-agent、bp-claim-ledger、bp-pitfall-atlas；系统提示词换成 system_prompt_v1.10.md。
其余 4 个技能（ghost-variables、null-result、multiverse、next-experiment）不变。

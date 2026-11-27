# BeyondPaper v1.11.8（2026-11-19）

依据：v1.11.7 的 4 次运行。判定还没做，下面只针对运行怎么断掉的。

| 问题 | 来自 | 改动 |
|---|---|---|
| 判断检查在长 heredoc 里反复查数据，平台按“重复输出”截断，两份文件都没写就交了部分结果 | B02-v1117p（四个事实都已核实：500 vs 100 树、L1 0.01 vs 0.005、214 人前瞻队列、摘要 0.950） | 新脚本 `bp-consistency-check/scripts/judgement.py`：`init` 建文件并列出脚本候选，`note KEY "..."` 记录一项检查，`add --type --severity --location --evidence` 写一条发现，`status` 显示还差什么；每条一句短命令，核实一条落盘一条。SKILL.md 给判断阶段约 15 次工具调用的预算，要重算的数放到阶段 4 的 recomputed.json |
| 写成 TODO 的占位也能过判断门 | — | bp_progress.py、final_summary.py、judgement.py 都不认含 TODO / pending 的记录；`note` 会替换占位，不会接在后面 |
| 最终消息贴整块 findings JSON（30 KB），被平台按重复输出截断 | B04-v1117p | `final_summary.py` 只打印五行摘要和每条红/黄一行，JSON 块只留在 `bp_out/BP_FINDINGS_BLOCK.txt`；`build_passport.py` 默认不再打印 JSON 块（`--print-block` 可打开）；提示词“Final message”和 Batch mode 段改为不贴 JSON |
| 平台对完全相同的命令返回缓存结果（"Historical completed result reused"），模型一直重发同一条命令直到停 | B03-v1117p 阶段 4 | 提示词 Don't loop 段：遇到这句就改命令（换输出文件名或加 `; date +%s`），不要重发；消息保持简短，不重复已写过的段落 |

## 验证
- 回放全部已判定运行（tools/replay_v1117.py）：REGRESSIONS []，结果与 v1.11.7 相同（属实 239、误报 35、红色误报 2）。
- judgement.py 端到端：init → note（TODO 被替换）→ add → 五项齐全后 status 显示 done，final_summary 正常收尾，消息只有摘要和红/黄列表。

## 要替换
- 技能包：bp-consistency-check（judgement.py、SKILL.md）、bp-relay-package（build_passport.py、final_summary.py、SKILL.md）、bp-claim-ledger（bp_progress.py）。bp-ghost-variables 仍用 v1.11.7。
- 系统提示词：system_prompt_v1.11.8.md（三处：阶段 2 末尾、Don't loop、Batch mode 结尾）。

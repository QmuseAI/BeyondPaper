# BeyondPaper v1.10.3（2026-11-17）

根据第三批 5 次运行（B03-v1101、B06-v1101、B12-v1102、B13-v1102、B14-v1102）修问题，不加新功能。改了 5 个技能包和系统提示词，其余 4 个技能包与 v1.10.2 相同。

## 脚本

| 文件 | 改动 | 起因 |
|---|---|---|
| `bp-consistency-check/scripts/ci_check.py` | 论文里的 HTML 表格先展平再找"估计值（区间）"；表注写明"95% CI presented in parentheses"时，表格单元里不带 CI 字样的"b (lo, hi)"也按置信区间检查 | B03：Campbell 表 3 的 .571 (−.544, .032) 没被读到（已知问题 5.1）；修后报红。其余 8 篇基准论文的输出不变 |
| `bp-consistency-check/scripts/methods_code_check.py` | 认出"GraphPad Prism、R、SPSS … were used to conduct tests"这类全局说明；这些工具里做的分析不再算"没有代码的实验" | B06：Chen 的 Cox 回归报成 phantom_experiment（2 条黄色误报） |
| `bp-ghost-variables/scripts/leakage_scan.py` | 固定了基础镜像标签或包版本的 Dockerfile 算作依赖说明 | B13、B06：Dockerfile 固定了版本，却报"没有依赖文件" |
| `bp-multiverse/scripts/multiverse.py` | `--time-budget`（默认 900 秒）：先跑基线和单点改动，超时后不再开新宇宙，已跑完的照常写出并注明；每跑完一个宇宙打印一行进度 | B12：多重宇宙后台跑了 45 分钟以上，智能体每 55 秒轮询一次 |
| `bp-relay-package/scripts/build_passport.py` | 论文百分数与作者打印的百分数相差不超过 1 个百分点 → 灰（截断与四舍五入；计数不受影响） | B14：62.86 打印成 62%，论文写 63%（两次运行共 4 条黄色误报） |
| 同上 | 复现闸门记录了移植偏差时，多重宇宙得出的"结论脆弱"最多黄灯 | B14-v1102 F4、B10-v18 F8：多重宇宙跑在我们自己的移植上 |
| `bp-paper-agent/scripts/build_paper_agent.py` | 追问演示链每次重跑的时限 60 → 300 秒（结果会缓存，现场提问直接返回真实重跑） | B06：演示链的两次重跑都超时 |
| `bp-paper-agent/scripts/paper_agent.py` | 重跑超时时说明用的是哪一条已记录结果，不再同时出现"可重跑"和"只读模式：不能重跑" | 同上 |

## 说明文件与提示词

| 文件 | 改动 | 起因 |
|---|---|---|
| `bp-multiverse/SKILL.md` | 分叉只改分析选择，不改结论本身的终点、结局、人群或估计量；基线跑在移植上要写明；时间预算的用法 | B13：把 PFS 换成 OS 当作一个分叉 |
| `system_prompt_v1.10.3.md` | 阶段 4 时间上限改成硬性：15 次调用内复现不了基线就记下数值、跳过多重宇宙、同一回合进入阶段 5 | B03 两次停在 R 移植上 |
| 同上 | 作者存档里有和论文一样的表格时，先把主表的每个系数、区间、截距连同存档值写进 `recomputed.json`（`value: null`），不需要任何运行环境 | B03：表 3 截距 5.468 vs 存档 5.486（已知问题 5.3）没查 |
| 同上 | 拿论文数字和存档表比之前，先找到代码里产生这个数的那一行，用它用的那一列 | B10：拿第一轮那一列的 28 去比论文的 34 = 26 + 8 |
| 同上 | 说某个指标只在一部分行上算之前，先找到写出存档输出的脚本 | B14-v1102 F24：把训练时的监控切片当成了报告的验证集 |
| 同上 | 从数据里说出的计数要由脚本算出交叉表、存到 `bp_out/tables/` 并引用 | B13-v1102 F3：删失人数数错 |
| 同上 | 阶段 7 一定调用 `load_skill("bp-paper-agent")`，失败重试一次 | B14-v1102 第一次没加载到 |
| 同上 | 九个 `bp-*` 技能都已配置：脚本不在 `/workspace/skills/` 下只说明本会话还没加载，先 `load_skill`（重试一次）再说缺失 | B12-v1102 没加载就把 from_run.py、atlas.py 记成缺失，failure_paths.md、precedents.md 没生成 |

## 离线验证

新的两条降级规则在 `scoring/results/` 里 12 次已保存的运行上重算：

| 运行 | 红/黄 旧 → v1.10.3 | 变化（括号里是人工判定） |
|---|---|---|
| B14-v1102 | 4/21 → 3/20 | F4 红 → 黄（待定）；F5、F6 黄 → 灰（误报） |
| B14-v1101 | 3/9 → 3/7 | F4、F5 黄 → 灰（误报） |
| B10-v18 | 8/10 → 7/11 | F8 红 → 黄（误报） |
| B06-v1101 | 2/24 → 1/25 | F2 红 → 黄（属实，已知问题 1.3；仍以黄灯报出） |
| 其余 8 次 | 不变 | |

核实属实的条目没有一条丢失；B14 的 64 vs 62、71 vs 69 相差 2 个百分点，保持原样。

脚本改动的单独检查：

- `ci_check.py`：Campbell 原样版和注入版都报出 5.1；其余 8 篇论文的输出与旧版相同。
- `methods_code_check.py`：Chen 的 Cox 回归从 phantom_experiment（黄）变为 missing_artifact（灰），90% / 95% 区间那条（已知问题 1.5）不变。
- `leakage_scan.py`：B12、B13、B14 的 capsule 不再报"没有依赖文件"；没有依赖文件的目录照常报出。
- `multiverse.py`：玩具适配器上，时间预算到点后写出 8/12 个宇宙并注明；不限时时 12/12。

提示词的新规则只能在线验证。

## 本目录内容

完整一套：9 个技能包加系统提示词。新改的是 `bp-consistency-check`、`bp-ghost-variables`、`bp-multiverse`、`bp-paper-agent`、`bp-relay-package`；`bp-claim-ledger`、`bp-next-experiment`、`bp-null-result`、`bp-pitfall-atlas` 与 v1.10.2 相同。

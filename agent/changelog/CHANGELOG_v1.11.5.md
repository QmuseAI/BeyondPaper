# BeyondPaper v1.11.5（2026-11-18）

依据：v1.11.4 的注入版结果 B07、B04、B02（B02 注入 4/4 全中）。B07（Hardwicke 2021，4 处注入）。v1.11.4 检出 2/4（I1 红、I2 黄），另外两处其实都被智能体找到了，但被压成了灰；另有 2 条误报。

| 问题 | 来自 | 改动 |
|---|---|---|
| 论文里同一个数两处不一致，被核对阶段判成"不同的量"压灰 | B07-v1114 F22（注入 I3：Results 写 879 个目标值，摘要和 Sample 写 789） | build_passport.verify_gate：两边的 what/metric/endpoint/population 都相同时，不再因为 "table" 字段不同（Results 对 Abstract）判成不同的量。论文自身两处不一致本来就在不同的表或段落 |
| 论文报了结果的额外样本在材料里完全没有，被写成灰色的"无法复算" | B07-v1114 F23（注入 I4："30 篇无开放数据徽章文章的对照样本，21 篇（70%）有重大差异"） | build_passport 新增 phantom_promote：智能体自己判灰的 unverifiable_claim/missing_artifact/missing_info，证据里点名"某某样本/队列/实验 of N"且写明材料里没有数据或代码，而材料里有作者其他分析的代码时，改为 phantom_experiment 黄灯。SKILL.md 也写明这种情况要报 phantom_experiment |
| Code Ocean 导出包的 REPRODUCING.md 写了归档镜像，仍报"依赖没锁定" | B07-v1114 F3 | deep_checks 的 inventory 把写有 `registry...:vN` 镜像的 REPRODUCING.md 记为环境已锁定，build_passport 现有规则据此标灰 |
| 发表偏倚检验（Egger）在 meta 分析脚本里找不到，被当成"统计软件里做的"压灰 | B04-v1114 F11（P02 已核实真问题 2.6：Methods 写了 Egger 检验，代码从没跑） | STAT_TEST 去掉 Egger、Begg、trim-and-fill、funnel plot：它们和 meta 分析在同一个脚本里做，脚本里没有就是真缺口 |
| 点名网页/点选工具（MetaboAnalyst、SPSS 里做的 Cox）的分析，手写成"缺少工件"报黄 | B02-v1114 F14/F15（B06 历次同类判误报） | gui_cap 也处理 missing_artifact |
| 按批次分层抽样被当成分组泄漏 | B02-v1114 F12（B06 历次 4 次判误报） | 手写 grouped_split_risk 写明按 batch 分层、且没有同一受试者多样本时标灰 |
| Rmd 里用行内 R（`` `r timeSummary %>% pull(min)` ``）输出的对象被当成"算了没打印" | B07-v1114 F4 | deep_checks.rmd_outputs 把行内 R 引用的对象算作已输出 |

## 验证
- 回放 41 次已判定运行：判对的条目一条没丢；没有新增误报。
- phantom_promote 在全部历史运行的灰色条目上扫过一遍：一条都不会被误升；B07 的 F23 会升黄，F24（"结论依赖补充材料里的逐篇说明"）不会。
- deep_checks：B07 材料上不再报 timeSummary、环境记为已锁定；B13 胶囊上 3 条真问题照报；v1.11.3 版在 Python 3.11 上直接崩溃（已在 v1.11.4 修好）。
- 按这些改动，B07-v1114 的 4 处注入会全部检出（2 处红/黄原样保留，I3 升回黄，I4 升为黄），2 条误报消失。

## 要替换的技能包
bp-relay-package（build_passport.py 已更新（含 Egger 改动））、bp-ghost-variables（deep_checks.py 75f36cf08e7c）、bp-consistency-check（SKILL.md）。提示词不变。

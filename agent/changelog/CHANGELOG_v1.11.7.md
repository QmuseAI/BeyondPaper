# BeyondPaper v1.11.7（2026-11-18）

目标：把只靠模型“手写判断”才能抓到的问题改成脚本必做或脚本直接查，减少平台中途换模型带来的不稳定；同时修掉三条压错颜色的规则。

| 问题 | 来自 | 改动 |
|---|---|---|
| 模型被换掉后不写 `llm.findings.json`，判断检查整段跳过 | B02-v1116p、B02-v1116p2（Chen 注入版两次 Pro 运行只找到 0/4、1/4） | `bp_progress.py` 新增阶段 2 “judgement checks”，必须有 `llm.findings.json`（列表，没有发现写 `[]`）和 `judgement_checks.json`（五项检查各写比较了什么），不能用 skipped.json 跳过；verify 和 relay package 阶段要求比 `llm.findings.json` 新。`final_summary.py` 在这两个文件缺失、或 `llm.findings.json` 比 `findings.json` 新时拒绝收尾（exit 1，打印“运行未完成”和下一步）。`build_passport.py` 缺文件时警告，并把 `judgement_checks` 写进 findings.json |
| 论文里多出来的研究（前瞻队列、IPD 再分析、追踪调查、在线重复实验）只能靠模型发现 | Chen I2、Stefanetti I4、Campbell I3、Delphi I4 | 新脚本 `claims_scan.py`：摘要/结果/讨论里描述额外研究、带人数、人数在全文和材料里都不再出现、描述短语别处没有、没有引文的句子，报黄 `phantom_experiment` |
| 摘要数字与正文不一致只能靠模型 | Chen I1（摘要 0.950，正文 0.905）、Hardwicke I1（摘要 34%，表 24%）、Delphi I1（摘要 38） | `claims_scan.py`：摘要里有指标词的数字，正文和表格都没出现，而正文同一指标、共享两个以上内容词的句子给了同类的另一个数，报黄 `number_mismatch`；“64%”与“0.64”视为同一数 |
| 正文人数与作者数据表的合计数位颠倒 | Stefanetti I1（1850 vs File 1 “Total patients (N)” 1805） | `claims_scan.py`：材料里 CSV/TSV 标为 Total 的行，与正文同名词计数数位颠倒（或非末位改一位且论文没给出该合计）时报黄 `sample_size` |
| “five hundred trees” 被读成 hundred = 100，与代码相同而跳过 | Chen I4 | `deep_checks.py` 按词组解析英文数字（five hundred = 500），与代码 `n_estimators=100` 比较，报黄 `param_mismatch` |
| `percent_rounding` 把位数不同的写错压成灰 | B09-v1116p F17（摘要 I2 58.5% vs 58.05%） | `build_passport.py` 只在两者是同一数值的两种精度（四舍五入或截断，或两个整数相差 1）时标灰；58.5 vs 58.05 保持原色 |
| 整数舍入报红 | B09-v1116p F2（Table 3 32.21 m vs Discussion 32 m） | 根因：`numbers_crosscheck.py` 用 `repr(float(32))` 取小数位，32 被当成 32.0。改为按原始写法取位数；`build_passport.py` 新增 `precision_cap`（同单位、位数不同且舍入可解释 → 灰） |
| “论文值在存档里也出现过”把两组 SD 写反压成灰 | B03-v1116p F14/F15、B08-v1116p F19/F20（Campbell 5.2） | `numbers_crosscheck.py` 和 `build_passport.py` 都检测互换：两个量的论文值恰好是对方的存档值 → 合并为一条红 `archive_mismatch`（swapped_pair） |
| 拿作者中间表的列去比最终表 | B10-v1116p F1/F3、B10-v18 F1/F5/F7（0.main_table.csv 的 consensus / consensus.1、mean / mean.1） | 存档表里被比较的列有多个版本（x / x.1 / x_final / x_r2 …）且没有给出生成论文数字的代码行（`recomputed.json` 新字段 `archive_code`）时，红降为黄（versioned_columns） |
| “Almost 90%” 对 87% 报红 | B10-v1116p F4 | 一处用 almost / about / nearly / over 等给出近似值、相差不到 10% → 灰（approximate_phrase） |

## 验证
- 回放全部已判定运行（`tools/replay_v1117.py`，含新规则；被 v1.11.6 `percent_rounding` 压灰的条目先恢复原色再过新规则）：REGRESSIONS: []；属实条目 236 → 239；误报 37 → 35；红色误报 6 → 2；精确率 0.864 → 0.872。新升红的 5 条（B03-v1115 F14、B03-v1116p F14、B08-v1116p F19、B09-v1116p F17/F18）补了人工标签（真问题 5.2、2.3 或其重复）。
- `claims_scan.py` 在 10 篇基准论文（5 原样 + 5 注入）、B14/B15/B16 和另一篇 PDF 转文本上：注入版命中 Chen I1、I2，Stefanetti I1、I4，Hardwicke I1，Campbell I3，Delphi I1；原样版和盲测论文 0 条。
- `deep_checks.py` 参数扫描在 10 份材料上新旧对比：只在 Chen 注入版多出 I4（500 vs 100）。
- `numbers_crosscheck.py` 合成测试：SD 互换 → 一条红；32 vs 32.21 不报；58.5 vs 58.05 红；“Almost 90%” 灰；B10 材料上 consensus / consensus.1 比较 → 黄。
- 判断检查门：缺文件时 bp_progress 显示 todo、final_summary 退出 1；文件齐全后正常收尾。
- 全部改动脚本 py_compile 通过。

## 要替换的技能包（提示词不变，仍为 v1.11.1）
bp-consistency-check（新增 claims_scan.py；numbers_crosscheck.py；SKILL.md）、bp-ghost-variables（deep_checks.py）、bp-relay-package（build_passport.py、final_summary.py、SKILL.md）、bp-claim-ledger（bp_progress.py）。

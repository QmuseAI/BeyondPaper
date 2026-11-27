# BeyondPaper v1.11.6（2026-11-18）

依据：B08-v1114、B10-v1114、B01-v1115、B03-v1115 四次运行的判定结果。

| 问题 | 来自 | 改动 |
|---|---|---|
| 核对阶段已经打印出作者存档值、确认冲突的手写数字条目，仍被复现门压成黄 | B03-v1115 F7–F12（Table 2 模型不可重现、Table 3 截距 5.468 vs 5.486、SD 写反、平均年龄、复制的 CI，全部为真），B08-v1114 F9 | build_passport 新增 archive_lift：gate_capped 的手写数字条目，verify.json 判 upheld、核对输出文件存在且打印出证据里的数值、证据指向作者存档输出（results/、.html/.txt/.log 等）而不是我们的移植或重数时，恢复为红，原因记 archive_verified；恢复后再过一遍 red_lint |
| 文中 Cohen's d 与两组 M/SD 对不上没人查 | B08-v1114 漏检注入 I2（d 写成 .64，按 M/SD 和作者 mes() 存档都是 0.44） | ci_check 新增 effect_size_findings：同一句里两组 "M = x, SD = y" 后跟 d/g，按合并 SD 重算；相差 > 0.10 且 > 25% 时报黄；附近出现 paired / within-subject / repeated measures / d_z / d_av 时跳过 |
| R Markdown 正文里的 "sample (" 被当成随机函数，报"未设种子" | B10-v1114 F1（manuscript.Rmd:912 是 Results 段落） | leakage_scan 对 .Rmd/.qmd 只扫代码块和行内 `r ...`，正文逐行清空（行号不变）；种子检查前去掉 R 注释 |
| 清单项 G4 证据写"No outlier rule stated or applied"，没被 checklist_nothing_done 盖住 | B03-v1115 F3 | NOTHING_REMOVED 增加"no outlier rule/definition … applied/implemented/in the code" |
| 论文说"去掉 2 名起疑参与者结果不变"，作者代码里就有这行（注释掉），被报成信息缺失 | B08-v1114 F12、B03-v1115 F6 | v111_caps 新增 runnable_alternative：missing_info/unverifiable_claim/missing_artifact 证据同时写到 commented-out 和"结果不变"时标灰 |
| 摘要里的计数（"38 reached consensus"，后面没有名词）和方法里的阈值（70% vs 代码 0.75）没人比 | B10-v1114 漏检 I1、I2 | bp-consistency-check SKILL.md 判断检查新增第 4 条"Abstract vs body"：摘要每个数要在正文/表格里找到同一量；方法里的阈值与代码常量、文中其他写法比对 |

## 验证
- 回放全部已判定运行（tools/replay_v1116.py，含 archive_lift 模拟）：REGRESSIONS: []；误报 30 → 27（去掉 B03-v1115 F3、F6，B08-v1114 F12）；升为红的 7 条全是真问题或真问题的重复（B03-v1115 F7–F12，B08-v1114 F9）。
- archive_lift 端到端测试：存档读数条目（核对输出含 5.48639099）恢复为红；比较对象是我们移植（adapter.py）的条目保持黄。
- ci_check 在 9 篇 paper.md 和 4 篇 PDF 转文本上跑新旧两版：只在 B08 多出 1 条（d = .64 vs 0.44，注入 I2），其余论文输出完全相同；B03 原样版（d = .44）不报。
- leakage_scan：B05 材料上 seed_missing 消失；B03 材料输出不变。
- 3 个包均 py_compile 通过。

## 要替换的技能包
bp-relay-package（build_passport.py 9fcd40f2b6a7，SKILL.md）、bp-consistency-check（ci_check.py 44f691470c52，SKILL.md）、bp-ghost-variables（leakage_scan.py 0593c6adaf1e）。提示词不变。

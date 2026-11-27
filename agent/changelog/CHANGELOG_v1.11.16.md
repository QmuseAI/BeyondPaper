# BeyondPaper v1.11.16（2026-11-19）

依据 v1.11.15 的 Pro 运行（B01、B03、B09、B10、B05）里人工判定出的问题：

| 问题 | 来自 | 改动 |
|---|---|---|
| 同一模型/同一行的存档不一致按单元格逐条报，25 条红/黄里 20 条是三处问题的重复 | B03-v11115p2（5.4 拆成 15 条，5.1 拆成 2 条） | `build_passport.py` 新增 `group_archive_cells`：numbers_crosscheck 的 archive_mismatch 按（存档文件、表+模型）分组；同一行的单元格合成一条；同一模型三行以上不一致时整个模型合成一条（"报告的模型不是存档里的那个"），列出全部单元格，保留最高等级 |
| 说 rma() 没传 method= 就不是 REML（metafor 默认就是 REML） | B09-v11115p F14/F15 | `build_passport.py` 新增 `default_arg_cap`（known_default）：手写条目怪罪省略参数、而该函数默认值正是论文写的（metafor rma REML、lme4 lmer REML、t.test Welch、coxph Efron、meta 包 method.tau REML）→ 灰；提示词要求先写出默认值 |
| 自己重数 30 vs 论文 37，其实 37 = 29+1+7 是同一组类别的另一种合并 | B01-v11115p F1 | `build_passport.py` 新增 `category_sum_cap`（category_definition）：手写数量条目的证据里有两个成立的加法式、共用加数、和不同 → 灰（口径问题，问作者）；提示词要求先试其他合并方式 |
| 阶段 6a 在 results/main.html 里反复猜字符偏移，空转一整轮 | B03-v11115p2 | 新脚本 `bp-relay-package/scripts/archive_text.py`：把存档 HTML、notebook、PDF、log 转成每段/每行一条的文本放到 bp_work/checks/archive_txt/，6a 用 grep -n 一次核对；提示词和 SKILL.md 写入 |
| 年龄"均值 21.00"实为中位数只被 B08 报出、B03 没报；"47 items in both rounds"（应为 45）B05 报出、B10 没报 | B03-v11115p2、B10-v11115p3 | 提示词：论文写的均值/中位数要在存档里两种都查；论文自己说的条目/记录数一律进 reported_values 并带存档计数 |

## 回放（tools/replay_v11116.py，87 次已判定运行）
- v1.11.15 规则：显示 817 条，真 402，误报 44，重复 196，精确率 0.901。
- v1.11.16 规则：显示 771 条，真 402，误报 40，重复 154，精确率 0.910。
- REGRESSIONS: []；没有两个不同的真问题被合进同一条。
- B03-v11115p2：25 → 9 条；B08-v11115p：23 → 14 条；B01-v11115p 误报 1 → 0；B09-v11115p 误报 1 → 0。

## 要替换
bp-relay-package（build_passport.py、archive_text.py、SKILL.md）；提示词 v1.11.16。其余 8 个技能包与 v1.11.15 相同。

# BeyondPaper v1.11.2（2026-11-17，已打包，未部署）

依据：v1.11.1 在 SciencePal 上跑的 7 次运行（B06、B11–B16）。这一版修五个问题。

| 问题 | 来自 | 改动 |
|---|---|---|
| 核对阶段把真问题降成灰灯 | B14-v1111：λ 0.02 对 0.002、网络结构 4-6-1 对 4-5-7-1 等 5 条，被 different_quantity_declared 误降 | `build_passport.verify_gate`：论文值和存档输出本来就在不同的"表"里，所以只有两个值都来自论文时才比较 table；param_mismatch 不比较 dataset；遇到 "-" 或者一方包含另一方，按相同处理 |
| 小数和百分数之间不换算 | B14-v1111：F1 0.64 对存档 62%，被当成单位不同，直接降成灰灯 | `numbers_crosscheck.convert`：先把小数换成百分数，再比较。0.68 对 68% 判为一致，0.64 对 62% 照常报出 |
| 代码设了种子却说没设 | B06-v1111 F4："random splits without seed" | `NO_SEED` 补上 "without seed" 等写法；代码里设了种子的，标灰 |
| 没有作者代码时乱报 | B15-v1111：胶囊没公开，却报"代码里找不到随机森林""没有依赖文件" | `deep_checks` 统计作者代码文件数（n_author_code）。为 0 时，methods_code_check 报的 phantom_experiment 和 dependency_unpinned 都标灰，缺代码这件事只由 ledger 报一次 |

| 超参扫描误报 | B16-v1111 F25–F27：拿编辑器历史文件（.vscode/User/History）、智能体自己复制的 deep_checks_py311.py 当作作者代码；"100 estimators and a fixed random seed (42)" 被读成 42；跨页的数字表被读成 epochs = 13 | `deep_checks`：跳过 .vscode、History、checks 目录，以及 deep_checks*/recount* 文件；参数名前面紧挨着数字时取前面的那个；遇到跨页或表格时不比较 |

回放全部 39 次历史运行：判对的条目一条也没被压下去，精确率从 127/206 升到 127/146。deep_checks 的改动不能回放，改用 4 个胶囊（B11–B14）重跑核对：deep.findings.json 和改动前完全一样。

要替换的技能包：bp-relay-package（build_passport.py）、bp-consistency-check（numbers_crosscheck.py）、bp-ghost-variables（deep_checks.py）。提示词不变。

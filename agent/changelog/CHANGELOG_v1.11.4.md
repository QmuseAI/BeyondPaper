# BeyondPaper v1.11.4（2026-11-17）

依据：v1.11.3 在 SciencePal 上的 5 次运行（B06、B13、B14、B15、B16，均已判定）里剩下的 9 条误报和 1 个脚本崩溃。

## v1.11.3 这一轮的结果

| 运行 | 模型 | 红/黄 | 真问题 | 误报 | 重复 | 不确定 |
|---|---|---|---|---|---|---|
| B06-v1113 原样 | V4 Pro，中途切 Flash | 2/6 | 5（已知真实问题 5/5 全中） | 2（都是红灯） | 0 | 1 |
| B13-v1113 盲测 | V4 Pro，中途切 Flash | 0/5 | 3 | 1 | 0 | 1 |
| B14-v1113 盲测 | V4 Pro | 2/16 | 8 | 2 | 2 | 6 |
| B15-v1113 盲测，无代码 | V4 Pro，中途切 Flash | 0/3 | 1 | 2 | 0 | 0 |
| B16-v1113 盲测 | V4 Pro | 5/18 | 12 | 2 | 4 | 5 |
| 合计 | | 9/48 | 29 | 9 | 6 | 13 |

精确率 29/38 = 76%。盲测论文的 7 条红灯全部是真问题（B16 的 5 条都对着 PDF 和胶囊核实过）。B06 的 L1 系数问题第一次由脚本报出。B16 的红/黄从 v1.11.1 的 40 条降到 23 条。

## 改动

| 问题 | 来自 | 改动 |
|---|---|---|
| deep_checks.py 在 Python 3.11 及以下直接报 SyntaxError，整个脚本不运行 | B13-v1113（智能体自己在副本里修了才跑起来） | 存储权重那段 f-string 里嵌套了同种引号（3.12 才允许），改成先拼好字符串。三个包的所有脚本都用 Python 3.11 编译检查过 |
| 全研究人数对比发现队列人数，报了 2 条红灯 | B06-v1113 F1/F2：论文 702 = 389 GC + 313 NGC，discovery_set.xlsx 是 Cohort 1（426 = 145 + 281），论文里也写了 "145 GC patients" | numbers_crosscheck：人数类的存档值如果在论文里作为某一组的人数出现过（"145 GC patients"、"n = 426"），标灰并引用原句；同一个存档文件的其他人数条目一起标灰。需要论文文本，默认读 bp_work/paper.md，SKILL.md 里加了 `--paper` |
| 参数扫描比较了注释掉的代码行 | B16-v1113 F12（`#best tanh' with ... learning_rate = 0.01`） | deep_checks：`#` 注释后面的赋值不算 |
| 参数扫描把别的模型的参数拿来比 | B16-v1113 F12（自编码器的学习率 0.001）；重跑时又出现梯度提升的 "100 estimators, a learning rate of 0.1" | 句子讲的是自编码器/重构/GAN，或者是树模型/boosting，而代码那行不是，跳过 |
| "early stopping at epoch 21" 当成了 epoch 设置 | B16-v1113 F13 | epoch 前后 40 字内出现 stopping/converged/best/patience，跳过；epoch 数只取整数 |
| L2 系数读成了 "45"（来自 ">45 min"） | B14-v1113 F8 | L2、学习率只取紧跟关键词（lambda、regularization parameter、learning rate 等）后面的数，在整句里找。B14 论文上现在由脚本报出真正的问题：lambda 0.02 对代码 0.002 |
| 论文自己两处只差最后一位（0.95 对 0.94）给了红灯 | B16-v1113 F1（是真问题，但红灯过重） | numbers_crosscheck：同位数小数只差最后一位，红改黄并说明可能是同一个数两种舍入 |
| 清单证据答非所问 | B13-v1113 F3（W4 细胞系条目，证据讲的是盲法，临床试验里没有细胞系） | build_passport：G3/G4/M7/W1/W2/W4/W6 这几个话题明确的条目，证据里一个相关词都没有就标灰 |
| 代码根本没去除离群值，却报"没写离群值规则" | B14-v1113 F6 | G4 条目的证据写明"没有过滤/没有去除"，标灰 |
| 没有作者代码，却在智能体自己的 2 个脚本里找 k 折交叉验证、依赖文件 | B15-v1113 F1/F2（deep_checks 崩溃，"没有作者代码"规则拿不到 code_facts） | deep_checks 修好后规则恢复；methods_code_check 另外跳过智能体自己的文件（checks/、paper_agent/、repro_*、check_*、deep_checks、V1.py 等） |

## 验证

- 回放 39 次已判定运行（build_passport 部分）：判对的条目一条都没有被降灰或合并掉；红/黄 346 → 340，误报 25 → 23，精确率 86.1% → 87.1%。
- 回放不了的部分（numbers_crosscheck、deep_checks）在材料上直接跑：
  - deep_checks，B06 材料：只报 L1 0.01 对 0.005（真问题），和 v1.11.3 一样。
  - deep_checks，B16 胶囊 + 论文：原来 2 条误报没了，新报 1 条真问题（Table S4 写 200 个 epoch，代码 n_epochs = 50）。
  - deep_checks，B14 论文 + nn.py：原来的 "45" 没了，改报 lambda 0.02 对 0.002（真问题）。
  - numbers_crosscheck，B06 论文 + 两条人数存档：两条都变灰；训练准确率 60 对 65 仍是红灯。
  - methods_code_check，B06 材料：90% 置信区间那条真问题照报，没有新增条目。
- 按这些结果，v1.11.3 这一轮的 9 条误报在 v1.11.4 下全部消失，29 条真问题都保留。

## 要替换的技能包

三个都要换：bp-relay-package（build_passport.py b742c707fea3）、bp-consistency-check（numbers_crosscheck.py 18968722063c、methods_code_check.py 810aa78bd258，SKILL.md 加了 --paper）、bp-ghost-variables（deep_checks.py ccfc2c000569）。提示词不变。

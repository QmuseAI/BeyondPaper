# BeyondPaper v1.11.3（2026-11-17）

依据：v1.11.1 在 SciencePal 上的 7 次运行（B06、B11–B16）判定完以后还剩下的误报和重复条目。包含 v1.11.2 的全部改动（v1.11.2 没有单独部署）。

## 新改动

| 问题 | 来自 | 改动 |
|---|---|---|
| 同一处代码问题报了十几遍 | B16-v1111：同一个 StandardScaler 调用报 16 条，测试集上 fit 报 8 条（两个脚本 × 四行 × 胶囊和智能体复制的两份）；B06-v1110 每条测试集复用报两遍 | `build_passport.group_repeats`：leakage_scan、methods_code_check、data_checks 的同类条目每次运行合成一条，其余位置写进 "Same pattern at N more place(s)"，同名文件同一行只算一次。手写条目如果是同类的 phantom_experiment / duplicate_output / split_overlap，或者点名了同一个被 fit 的对象（scalerT.fit_transform(X_testl)），或者和脚本条目文字基本相同，也并进去。leakage/test_set_reuse 的手写条目只要提到第二种机制（标签、特征选择、PCC 权重等）就单独保留 |
| 多重宇宙重跑没达到阈值 | B14-v1101 F3、B14-v1111 F16（精确率 62.x 没到 "≥63%"） | multiverse 的 rerun_mismatch 一律标灰：历史上 6 条全是误报，重跑结果在复现部分单独报告 |
| 把未来工作当成需要证据的结论 | B11-v1111 F1（"…is unknown; a future study could compare…"） | validate_ledger 的 unsupported_claim，如果句子是开放问题或未来工作，标灰 |
| 统计检验在代码里找不到 | B06-v1101 F4（Cox 在 SPSS/R 里做）、B13-v1111 F1（Wilcoxon 用于补充材料里的 IHC 数据） | methods_code_check 报的 Cox、KM、t 检验、卡方、Mann-Whitney、ANOVA、多重比较校正等找不到时标灰；模型（RF、SVR、XGBoost 等）仍是黄灯 |
| bootstrap 只是随机森林内部抽样 | B16-v1111 F4 | 论文里 bootstrap 出现在随机森林/树的描述中时标灰 |
| 清单证据本身说明没问题 | B14-v1111 F5（"computed on the training file only… consistent with the split"） | checklist 的 missing_info，证据写明只在训练集上拟合、在每折内拟合等，标灰 |
| 清单证据答非所问 | B13-v1111 F4（G1 数据来源条目，证据讲的是种子） | 条目问的不是种子而证据讲种子，标灰；种子问题由 seed_missing 单独报 |
| L1 系数比较从来没跑过 | B06 真实问题 1.1（论文 L1 系数 0.01，代码 alpha=0.005）只有手写检查偶尔能发现，v1.10.3 之后 4 次漏了 3 次 | `deep_checks.params`：正则把参数名当成了数值，l1 一直被跳过；改为取数值组。只比较 Python 里的 alpha（R 的 glmnet alpha 是 L1/L2 混合比例，画图的 alpha 是透明度，都跳过）；论文这边只取紧跟 coefficient/penalty/lambda 等词的数（不再把 "LASSO 选出 10 个代谢物" 的 10 当成系数）。B06 材料上重跑：报出 1 条黄灯，正好是 0.01 对 0.005 |

## 回放验证

- 全部 35 次已判定运行回放（包括合并步骤）：判对的条目一条都没有被降灰或被合并掉，也没有两条不同的真问题被合成一条。
- 显示出来的红/黄灯从 350 条降到 286 条，其中重复从 97 条降到 38 条；判错从 19 条降到 12 条；精确率 124/143 → 127/139（91%）。
- 剩下 12 条判错：B16 的 3 条和 B06-v1110 F7 已由 v1.11.1/v1.11.2 的 deep_checks 修掉（不能回放，用 B16 胶囊加智能体副本、编辑器历史文件模拟重跑，0 条）；其余 8 条来自 v1.8–v1.10.1 的旧运行，属于 v1.11 核对阶段处理的手写或存档比较条目，回放时无法执行。
- deep_checks：B06、B16 材料上重跑，只有 B06 多出 L1 那一条真问题。

## 要替换的技能包

bp-relay-package（build_passport.py）、bp-consistency-check（numbers_crosscheck.py，v1.11.2 改动）、bp-ghost-variables（deep_checks.py）。提示词不变。

build_passport.py e3efca0becae · deep_checks.py 8f771c2b0d27

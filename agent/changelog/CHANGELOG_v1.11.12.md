# BeyondPaper v1.11.12（2026-11-19）

依据：B10-v1119p（Delphi 注入，Pro，跳过阶段 4 后跑完）：注入 2/4，真问题 2/2（4.1、4.2 首次报出），精确率 1.00；漏 I2、I3。

| 问题 | 来自 | 改动 |
|---|---|---|
| "Our final sample comprised 113 participants"（别处三次写 103）只给灰；Hardwicke "789 values reported in 27 articles" 同样 | B10 I3、B07 I2 | `counts_check.py`：只出现一次、与常见计数相差 ≤10% 的人数，若所在句子是在定义样本（final sample / in total / comprised / were included …）→ 黄；其他仍灰 |
| 方法写"共识 = 至少 70%"，代码用 `>= 0.75`，没有一次运行报出 | B10 I2 | `methods_code_check.py`：正文里带 consensus/threshold/cut-off/defined 的"at least N%"若不出现在任何代码比较中，而代码对比例类变量用了相差 ≤15 个百分点的阈值 → 黄 param_mismatch |

## 验证
- 10 篇基准论文 + 外部论文：两条新规则只在 Delphi 注入版（70% vs 0.75；113 vs 103）和 Hardwicke 注入版（27 vs 25）上报出；原样版和其他论文 0 条。

## 要替换
bp-consistency-check。

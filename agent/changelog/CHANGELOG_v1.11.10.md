# BeyondPaper v1.11.10（2026-11-19）

依据：B07-v1119p / B07-v1119p2（Hardwicke 注入，两次都是 3/4，漏 I2）。

| 问题 | 来自 | 改动 |
|---|---|---|
| "789 values reported in 27 articles"（数据和正文其他两处都是 25 篇）没报出；没跑通作者 R 代码时没有存档值可比 | B07-v1119p I2 | `claims_scan.py` 新增 `ids_vs_text()`：Abstract/Methods/Results 里只出现一次的"数 + 名词"，与作者数据表 ID 列的不同值个数（articleID 25）相差 ≤20%，且论文别处写了该个数 → 黄 sample_size；已作为 phantom 报出的句子不重复报 |
| 37 个重算值用了自己的名字（repro_withAI_pct…），36 个对不上台账名，全部没和论文比 | B07-v1119p2 | `numbers_crosscheck.py`：未匹配 ≥5 且多于匹配数时打印 WARNING，并按数值列出对应的台账名，提示改名后重跑；SKILL.md 写明 |

## 验证
- claims_scan 在 10 篇基准论文 + 外部论文上：B07 注入版 4/4 全部由脚本报出；原样版和其余论文新增 0 条。
- numbers_crosscheck 合成用例：6 条未匹配中 3 条按数值给出台账名。

## 要替换
bp-consistency-check。提示词不变。

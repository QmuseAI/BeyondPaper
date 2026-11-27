# BeyondPaper v1.11.9（2026-11-19）

依据：B04-v1117p、B07-v1118p、B02-v1117p、B10-v1117p 的判定。

| 问题 | 来自 | 改动 |
|---|---|---|
| 摘要用中点作小数点（"I²=58·5%"），claims_scan 读不到，漏掉 2.3 | B04-v1117p | `claims_scan.py` 先把数字之间的 `·` 换成小数点 |
| 手写条目拿我们移植代码的数当论文问题（"our port … gives I2 53.4%"） | B04-v1117p F13 | `build_passport.py` 新增 `port_restated_cap` → 灰 |
| "879 target values" 与 "789" 同一数量的数字颠倒，未报 | B07-v1118p I3 | `claims_scan.py` 新增 `transposed()`：同一名词、≥3 位、数字相同顺序不同 → 黄 |
| "A comparison sample of 30 articles" 未报：裸数 30 在别处出现（"between 1 and 30"），代码里有 "comparison" 一词 | B07-v1118p I4 | phantom 检查改为按"数 + 名词"短语判断是否在别处出现；材料里要出现完整触发短语才算有依据。Delphi 的 "follow-up survey of 46 heads" 也随之报出 |
| 判断阶段笔记写了"没有数据和代码"，却没写成条目 | B07-v1118p | `judgement.py note KEY ok\|problem "…"`：记为 problem 的项必须有对应条目（`add`，按类型或 `--key` 归属），否则阶段不算完成；`bp_progress.py`、`final_summary.py` 用同一规则（旧格式笔记按否定措辞判为 problem） |
| "27 articles" 与存档 25 被当作单位不同而灰 | B07-v1118p I2 | `numbers_crosscheck.py`：只有一侧写了名词单位时视为同一单位（10 的幂差距仍按单位问题处理） |
| "n = 426 … discovery (n = 284) and test (n = 152)" 加和不符，未报 | B02-v1117p I3 | `counts_check.py` 新增 `check_split_sum`：句中先给总数、再用 into/split/randomised 等分成几份，加和不等 → 黄 |
| 方法写 70%、代码 0.75 的共识阈值，核对已 upheld，却因论文与代码措辞不同被判"不是同一个量"而灰 | B10-v1117p I2 | `build_passport.py`：param_mismatch 一侧是论文、一侧是代码时只比较 endpoint |
| 论文写 "median absolute change"，代码算 mean(abs(...)) | Delphi 真问题 4.1（历次未报） | `methods_code_check.py` 新增中位数/均值标签检查 → 黄 stat_inconsistency |

## 验证
- claims_scan 在 10 篇基准论文 + 4 篇外部论文上：注入版新增命中 B07 I3/I4、B10 I4、B04 IPD；原样版 0 条。
- counts_check 加和检查、methods_code_check 中位数检查：只在对应论文上命中，其余论文 0 条。
- 回放全部已判定运行：REGRESSIONS []；属实 258，误报 35，红色误报 2。
- 新运行：B02-v1117p 注入 3/4、真问题 5/5、精确率 1.00；B10-v1117p 注入 2/4（I2 被上面的灰化规则吞掉，I4 未报）。

## 要替换
bp-consistency-check、bp-relay-package、bp-claim-ledger。提示词阶段 2 一句改为 "note each check as ok or problem and add a finding for every problem"。

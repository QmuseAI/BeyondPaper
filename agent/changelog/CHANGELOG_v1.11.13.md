# BeyondPaper v1.11.13（2026-11-19）

依据：B02-v11111p（胃癌代谢组注入，Pro，一次跑完）：注入 4/4，真问题 5/5，精确率 0.64。5 个误报都来自同一处：账本里登记的数据路径写错了文件夹或扩展名（data/diagnosis/Xtrain.csv，实际是 data/GC-diagnosis-model/Xtrain.xlsx），`validate_ledger.py` 就报"标为可用但找不到"的黄灯。

| 问题 | 来自 | 改动 |
|---|---|---|
| 账本路径写错 → 5 条黄色 missing_artifact | B02-v11111p F14–F18 | `validate_ledger.py`：路径不存在时先在 bp_work 下找同名文件（不看文件夹和扩展名）；找到就打印 PATH FIX 提示改账本，不再报缺失；真找不到才报黄 |

## 要替换
bp-claim-ledger。

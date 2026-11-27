# BeyondPaper v1.11.11（2026-11-19）

| 问题 | 来自 | 改动 |
|---|---|---|
| 多重宇宙阶段反复改适配器、复现不了作者基线，一跑一个多小时（B03 用掉 6800 万 token），每轮结束都说"需要新沙箱" | B03-v1117p、B10-v1119p | `bp_progress.py`：阶段 4 从第一次被列为 NEXT 起计时，超过 40 分钟仍没有 multiverse_summary.json 就自动记入 skipped.json 并进入阶段 5 |
| 数据没变、同一条命令输出相同，智能体当成"缓存故障"结束回合 | B07-v11110p、B08-v11110p、B10-v1119p | `bp_progress.py` 打印 NEXT 后加一句：相同数据得到相同输出是正常的，直接运行 NEXT，不要结束回合要新会话；提示词同一处也写明（11-18 已改） |

## 验证
- 模拟：阶段 0–3 完成、阶段 4 计时 41 分钟 → 自动 skipped，NEXT 变为阶段 5，skipped.json 写明原因。
- build_paper_agent.py、build_passport.py 在没有 multiverse 文件时照常运行（已有分支处理）。

## 要替换
bp-claim-ledger。

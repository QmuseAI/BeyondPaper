# 评测打分（scoring）

## v1.11.16 最终结果（决赛使用）

> GitHub 版不含注入版答案（`keys/injected_*.json`），注入召回的数字在这里复算不出来，其余都能复算；`scores_final/` 是完整答案下的结果。

以下结果在正常运行条件下取得，绝大部分步骤由 DeepSeek V4 Pro 完成。SciencePal 平台会在运行中自动切换模型；遇到切换时，我们停下任务、切回 V4 Pro 继续，并让 V4 Pro 重新核对切换期间写下的结论。

每篇论文取一次运行：

- 训练集（B01–B10）：B01-v11116p2、B02-v11116p、B03-v11116p3、B04-v11116p2、B05-v11116p、B06-v11116p、B07-v11116p、B08-v11116p2、B09-v11116p、B10-v11116p2
- 盲测（B11、B12、B14、B15、B16）：B11-v11116p、B12-v11116p、B14-v11116p、B15-v11116pr、B16-v11116p
- B13 不计入：论文正文在付费墙后、下载不到，运行卡在找正文、沙箱多次崩溃，没有有效结果。见 `B13_PAPER_ACCESS.md`。B13 已保存的两次结果（`results/B13-v11116p2`、`results/B13-v11116p2g`）保留在这里，`meta.json` 标了 `excluded`。

盲测里 1 条待定的红灯（`results/B14-v11116p` 的 F2）：待定的那条红灯来自卒中论文的多重宇宙检查：论文说网络在验证集上预测"进门到溶栓超过 45 分钟"的准确率高于 60%、F1 高于 0.64；智能体换了合理的分析选择重跑 60 种组合，这个结论只在 40% 的组合里成立，换随机种子（0、2、3、4）、正则化系数取论文写的 0.02、学习率取 0.5，单独改任何一项都会让它不成立。这是稳健性问题，不是能对照作者存档判对错的事实错误；算不算论文的错，要看作者有没有在多个种子上验证过，所以判为待定，智能体把它作为问题交给作者（"这个结果在这些种子和参数下检查过吗？"）。

模型稳定性：BeyondPaper 在模型稳定（全程 DeepSeek V4 Pro）时表现很好；平台中途换模型时结果不稳定，每次运行都有一点偏差。对比 `B08-v11116p`/`B08-v11116p2`、`B10-v11116p`/`B10-v11116p2`：前一次被平台换到 V4 Flash，各有 1 条红色误报；模型稳定的重跑没有。

复算：

```bash
python score_runs.py --out scores_final --only B01-v11116p2,B02-v11116p,B03-v11116p3,B04-v11116p2,B05-v11116p,B06-v11116p,B07-v11116p,B08-v11116p2,B09-v11116p,B10-v11116p2,B11-v11116p,B12-v11116p,B14-v11116p,B15-v11116pr,B16-v11116p
```

结果在 `scores_final/summary.md`：注入问题检出 20/20（标红 9）；已发表论文真实问题 26/34；精确率 76/79（96%）；红色误报 0 条；盲测红色误报 0 条。`results/` 里还保留了其他运行（含以前的版本），`scores/` 是全部运行的分数。

每次运行的结果存到本地 `results/<run_id>/`，分数由脚本从 `results/` 和 `keys/` 重新算出，决赛材料里的数字都能复算。

## 结果从哪里来

任选一种，存成文本文件即可：

- 智能体最后输出的 `<<<BP_FINDINGS_JSON>>> … <<<END_BP_FINDINGS_JSON>>>` 块（连同前后文字整段复制也行）；
- SciencePal 工作区文件面板里下载的 `bp_out/findings.json`（最后没输出结果块时用这个）；
- 终端里 `cat bp_out/findings.json` 的输出；
- 以前导出的页面文件 `downloads/Bxx.json`。

脚本会取最后一个结果块；块被截断时只保留完整的条目，并在 `meta.json` 里记 `truncated: true`。

## 三步

```bash
# 1. 存
python save_run.py B10_page.txt --run-id B10-v18 --paper B10 --variant injected --version v1.9

# 2. 打分（第一次会在 results/B10-v18/ 生成 adjudication.csv）
python score_runs.py

# 3. 打开 adjudication.csv，逐条填 human_label，再跑一次 score_runs.py
```

`--paper` 用基准编号（B01–B12），`--variant` 是 clean / injected / blind。

## adjudication.csv

每条红/黄一行。`auto_label` 是脚本按答案自动给的建议：

- `injected:I2`：命中注入问题 I2（`(dup)` 表示同一问题的重复条目）
- `real:1.3`：命中已核实的真实问题 1.3
- `known_fp:A-B11-denom`：像智能体以前犯过的错（失败机制库里的 auditor 案例）
- `pre_existing`：原样版本里也有同一条
- `unlabeled`：答案里没有，需要人看

`human_label` 只能填：`injected`、`real`、`new_real`（答案里没有、核实后是真问题）、`false_positive`、`duplicate`、`unclear`。

精确率 = (injected + real + new_real) / (这三类 + false_positive)，`duplicate` 和 `unclear` 不计入。红色误报单独统计，盲测论文的红色误报是查错可信度最直接的数字。

`--accept-auto` 会把 `injected:`/`real:` 的建议直接填进空的 `human_label`（备注 "accepted from auto_label"），省时间，但仍要扫一遍。重跑脚本不会覆盖已填的 `human_label`。

## 答案

- `keys/injected_Bxx.json`：注入版和原样版逐句对比得到（`make_injected_key.py`），每篇 4 处，已人工核对过类型和匹配词。
- `keys/real_Pxx.json`：已发表论文里核实过的真实问题，来自 `FINDINGS_IN_PUBLISHED_PAPERS.md` 和失败机制库；`known_false` 是已知的智能体误报。
- `keys/papers.json`：基准编号 → 论文、原样/注入、对应的原样版。新盲测论文（B12 起）在这里加一行；核实出真实问题后新建 `real_B12.json`。

匹配规则：条目类型兼容，且证据/位置/问题里出现某个匹配词（数字按整数匹配，`38` 不会命中 `138`）。自动匹配只是建议，最终以 `human_label` 为准。

## 输出

- `scores/by_run.csv`：每次运行一行
- `scores/summary.md`：表格和合计

替代了 `BeyondPaper/benchmark/score.py`（那个只算注入召回）。

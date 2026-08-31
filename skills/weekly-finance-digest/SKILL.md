---
name: weekly-finance-digest
description: 每周财务摘要（定时任务或手动触发）。数据层→计算层→呈现层自动生成周报。
version: 3.0.0
license: MIT
metadata:
  tags: [finance, 财务, 周报, cron]
  related_skills: [finance-overview, expense-analysis, global-ledger]
---

# 每周财务摘要

## When to Use

- Cron 定时任务（每周日）自动触发。
- 用户手动要求："这周花了多少钱？"/"本周财务摘要"/"和上周比有什么变化？"

## 前置规则

1. 不修改任何原始账单文件。
2. 不执行付款/转账/投资操作。
3. 所有金额注明币种、数据日期、来源文件。
4. 无法确认的信息标记"待确认"并询问。

## 工作流程

### 第 1 步：确保数据层已就绪

若 `results/raw/global_bill/global_ledger.csv` 不存在，先运行：

```bash
billweave ledger --workspace <工作目录>
```

### 第 2 步：运行计算层脚本（严禁模型心算）

```bash
billweave weekly --workspace <工作目录>
```

对比本周（近 7 天）与上周的：收入、支出、净结余、类别变动。
结果保存到 `results/raw/calculation_results/weekly_*.json`。

### 第 3 步：调用呈现层

```bash
billweave render \
  --latest \
  --template templates/每周财务摘要.md.j2 \
  --output reports/每周财务摘要
```

若报"找不到模板"，跳过渲染直接按 JSON 汇报。

### 第 4 步：汇报

- 先看 `是否有显著变化`：
  - **false** → "本周无显著变化"，简要给出收入/支出/净结余。
  - **true** → 说明变动最大的类别、变化百分比。
- 给出本周/上周对比表（引用 `本周汇总`/`上周汇总`）。
- 如有待确认交易影响本周数据，提示用户（本期仅统计已确认交易）。

## 常见坑

- 周报口径为滚动 7 天，汇报时如实说明统计区间。
- 必须等数据层生成 CSV 后再跑计算层。
- 变化百分比基于上期绝对值计算，上期为 0 时 ±100% 属正常。

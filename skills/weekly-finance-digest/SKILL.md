---
name: weekly-finance-digest
description: 每周财务摘要（定时任务或手动触发）。数据层→计算层→呈现层自动生成周报（按年账本，含待确认口径 + 已确认对照）。
version: 3.1.0
license: MIT
metadata:
  tags: [finance, 财务, 周报, cron, 按年]
  related_skills: [finance-overview, expense-analysis, global-ledger, financial-export-parsing]
---

# 每周财务摘要

## When to Use

- Cron 定时任务（每周日）自动触发。
- 用户手动要求："这周花了多少钱？"/"本周财务摘要"/"和上周比有什么变化？"

## 前置规则（必须遵守）

1. 只读取 `<工作目录>` 目录内的文件，不读取其他目录。
2. 不修改、覆盖、删除任何原始文件（账单、余额、合同都算原始文件）。
3. 不索取银行卡密码、短信验证码、私钥或支付权限。
4. 不执行任何付款、转账、投资、借款、交易、报税操作——本分析只做计算和建议，实际资金操作由用户自己执行。
5. 所有金额注明币种、数据日期、来源文件。
6. 无法确认的账户、交易、用途标记"待确认"并询问用户，不猜测。
7. 需要汇率换算时先询问用户使用什么汇率和日期，不自行补充。

## 工作流程

### 第 0 步：阅读 README

先阅读项目根 `README.md`，确认工作目录结构、文件地图与接入真实账单的方式（不确定的信息不得当作事实；用户明确提供的信息优先于档案）。

### 第 1 步：确保数据层已就绪

若 `results/raw/global_bill/global_ledger_{year}.csv` 不存在（按年账本），先运行：

```bash
billweave ledger --workspace <工作目录>
```

### 第 2 步：运行计算层脚本（严禁模型心算）

```bash
billweave weekly --workspace <工作目录> --year <年>
```

`--year` 默认当前年。对比本周（近 7 天）与上周的：收入、支出、净结余、类别变动。结果保存到 `results/raw/calculation_results/weekly_{year}_*.json`。

> **口径升级（v1.2.0）**：本期统计**含待确认交易**（钱已真实发生，类别未定不影响金额），summary 同时附"已确认 N 笔"对照字段。汇报时如实说明统计口径。

### 第 3 步：调用呈现层

```bash
billweave render \
  --latest \
  --finance-dir <工作目录> \
  --template templates/每周财务摘要.md.j2 \
  --output reports/每周财务摘要
```

`--latest` 自动选取最新的 weekly JSON。若报"找不到模板"，跳过渲染直接按 JSON 汇报。

### 第 4 步：汇报

- 先看 `是否有显著变化`：
  - **false** → "本周无显著变化"，简要给出收入/支出/净结余。
  - **true** → 说明变动最大的类别、变化百分比。
- 给出本周/上周对比表（引用 `本周汇总`/`上周汇总`）。
- 如有影响本周数据的待确认交易，提示用户本周为"含待确认口径"，并列出已确认笔数；用户终审后重跑数据层即可更新。

## 常见坑

- 周报口径为滚动 7 天，汇报时如实说明统计区间。
- 必须等数据层生成 CSV 后再跑计算层。
- 变化百分比基于上期绝对值计算，上期为 0 时 ±100% 属正常。
- 新接入账单时先跑 `billweave inspect-match --workspace <工作目录>` 排查表头识别。

---
name: finance-overview
description: 用户询问财务概况/总资产/负债/现金时使用。数据层→计算层→呈现层全自动生成财务概览报告（按年账本，含待确认口径）。
version: 3.1.0
license: MIT
metadata:
  tags: [finance, 财务, 概览, 资产负债, 按年]
  related_skills: [expense-analysis, weekly-finance-digest, global-ledger, financial-export-parsing]
---

# 财务概览

## When to Use

用户询问整体财务状况时，如：
- "我总共有多少钱？"
- "财务概览"
- "帮我看看资产和负债"

## 前置规则

1. 不修改任何原始账单文件。
2. 不执行付款/转账/投资操作。
3. 所有金额注明币种、数据日期、来源文件。
4. 无法确认的信息标记"待确认"并询问用户。
5. 汇率换算前先询问汇率和日期。

## 工作流程

### 第 1 步：确保数据层已就绪

若 `results/raw/global_bill/global_ledger_{year}.csv` 不存在（按年账本，旧版无年份产物首次运行时会自动归档到 `results/_旧版/`），先运行：

```bash
billweave ledger --workspace <工作目录>
```

### 第 2 步：运行计算层脚本（严禁模型心算）

```bash
billweave overview --workspace <工作目录> --year <年>
```

`--year` 默认当前年。自动计算：现金、可立即使用资金、其他资产、负债、净资产、健康评估。结果保存到 `results/raw/calculation_results/overview_{year}_*.json`。

> **口径升级（v1.2.0）**：月均支出统计**含待确认交易**（钱已真实发生，类别未定不影响金额），summary 同时附"已确认"对照字段。与旧版"仅统计已确认"略有差异，更接近真实现金流。

### 第 3 步：调用呈现层

```bash
billweave render \
  --latest \
  --finance-dir <工作目录> \
  --template templates/财务概览.md.j2 \
  --output reports/财务概览
```

`--latest` 自动选取最新的 overview JSON。若报"找不到模板"，跳过渲染直接按 JSON 汇报。

### 第 4 步：汇报

用普通人能看懂的话回答：
- 现金有多少（`现金合计`）；
- 可立即使用资金（`可立即使用资金合计`）；
- 还有哪些资产（`其他资产合计`）；
- 有多少负债（`负债合计`）；
- 财务健康评估（`健康评估` 4 指标）。

若有未确认交易，按金额从大到小逐条询问用户类别。汇报时如引用汇总数字，注明该数字来自"含待确认口径"还是"已确认口径"（v1.2.0 起两套口径并存）。

## 常见坑

- 现金 = 不受限余额；定期/理财/基金计为"其他资产"。
- 健康评估由确定性规则计算，LLM 只翻译不重新判断。
- 必须等数据层生成 CSV 后再跑计算层。
- 新接入账单时先跑 `billweave inspect-match --workspace <工作目录>` 排查表头识别，避免脏数据污染账本。

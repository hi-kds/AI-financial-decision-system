---
name: global-ledger
description: 全局账本:汇总各平台账单、去重分类、待确认询问。任务执行后自动更新,用户问"总共花了多少"时使用。
version: 3.0.0
license: MIT
metadata:
  tags: [finance, 账本, 汇总, 分类]
  related_skills: [finance-overview, expense-analysis, weekly-finance-digest]
---

# 全局账本（数据层服务）

## When to Use

- 用户问"总共花了多少/收入多少/这个月账目"时。
- 财务概览 / 重大支出分析 / 每周摘要执行前，若账本过期，先运行更新。

## 前置规则

1. 不修改、覆盖、删除任何原始账单文件。
2. 不执行付款/转账/投资操作。
3. 所有金额注明币种、数据日期、来源文件。
4. 无法确认的交易标记"待确认"并询问用户。

## 本 Skill 定位

**数据层服务**：只负责清洗、去重、分类、输出标准化数据，不生成用户可见的报告。

输出文件（`results/raw/global_bill/`）：
| 文件 | 内容 |
|------|------|
| `global_ledger.csv` | 全量交易（已确认+待确认），计算层的唯一数据源 |
| `summary.json` | 汇总：总交易数/已确认数/待确认数/收入/支出 |
| `pending_queue.csv` | 待确认交易队列 |
| `removed_records.csv` | 被去重剔除的记录 |
| `confirm_records.json` | 用户确认记录（持久化，重跑自动套用） |

## 工作流程

### 第 0 步：读取去重规则

阅读 `docs/dedup-rules.md`，核对本次分析是否涉及特殊规则。

### 第 1 步：运行数据层脚本

```bash
billweave ledger --workspace <工作目录>
```

脚本自动完成：递归读取 `bills/` → 剔除不计收支 → 去重 → 关键词分类 → 输出 CSV/JSON/summary。

### 第 2 步：向用户汇报摘要

从 `results/raw/global_bill/summary.json` 读取：
- 账本概况：总交易数 / 已确认数 / 待确认数 / 被剔除数
- 收支：总收入 / 总支出 / 净结余
- 类别分布
- 待确认队列：列出 `pending_queue.csv` 中的交易，逐条询问用户

### 第 3 步：处理待确认队列

```bash
billweave ledger \
  --workspace <工作目录> \
  --confirm "日期|金额|类别" --confirm "日期|金额|类别"
```

格式：`日期|金额|类别`，确认记录写入 `confirm_records.json`，下次重跑自动归类。

确认完重跑一次数据层脚本即更新。

## 去重规则摘要（以 docs/dedup-rules.md 为准）

1. **平台互转**：同一天 1 收 1 支、跨平台、含"提现/充值"、金额相近 → 合并记手续费，原两笔不计收支。
2. **跨平台结算**：同一天 2 支出、跨平台、付款方式含银行卡、金额一致 → 保留微信/支付宝侧，剔除银行侧。
3. **退款**：平台标注"不计收支"→ 标记 neutral，剔除不录入。
4. **资金移动**：定存/理财/基金/还款等 → 类别"资金移动"，不计入总收入/总支出。

## 常见坑

- 脚本幂等：同一批账单重复运行不会重复追加。
- 支付宝"不计收支"交易一律剔除。
- 微信"商品"列可能是"商户单号xxx"，已自动回退用"交易对方"。
- 招行 PDF 无表格线，金额列可信，摘要可能合并。
- **确认类别只存在于 `confirm_records.json`，不要手工编辑 CSV**。

---
name: expense-analysis
description: 用户考虑重大支出/大额消费/要不要付款/怎么付款时使用。先问4个前提,运行计算层脚本,调用呈现层生成四方案分析报告（按年账本）。
version: 3.1.0
license: MIT
metadata:
  tags: [finance, 财务, 支出分析, 方案对比, 按年]
  related_skills: [finance-overview, weekly-finance-digest, global-ledger, financial-export-parsing]
---

# 重大支出分析

## When to Use

用户询问重大支出决策，如：
- "这笔钱要不要付？"
- "怎么付划算？"
- "帮我分析这个大额支出"
- "分几期好还是一次付清？"

## 前置规则（必须遵守）

1. 只读取 `<工作目录>` 目录内的文件，不读取其他目录。
2. 不修改、覆盖、删除任何原始文件（账单、余额、合同都算原始文件）。
3. 不索取银行卡密码、短信验证码、私钥或支付权限。
4. 不执行任何付款、转账、投资、借款、交易、报税操作——本分析只做计算和建议，实际资金操作由用户自己执行。
5. 所有金额注明币种、数据日期、来源文件。
6. 无法确认的账户、交易、用途标记"待确认"并询问用户，不猜测。
7. 需要汇率换算时先询问用户使用什么汇率和日期，不自行补充。

## 工作流程（数据层 → 计算层 → 呈现层）

### 第 0 步：阅读 README 并问 4 个前提

先阅读项目根 `README.md`，确认工作目录结构、文件地图与接入真实账单的方式；然后若用户未主动提供以下前提，逐一询问（不要替用户假设）：
1. 准备支付多少钱？
2. 使用什么币种？
3. 计划在哪一天支付？
4. 希望账户里至少保留多少钱（安全线）？

### 第 1 步：确保数据层已就绪

若 `results/raw/global_bill/global_ledger_{year}.csv` 不存在或账单有更新，先运行：

```bash
billweave ledger --workspace <工作目录>
```

### 第 2 步：运行计算层脚本（严禁模型心算）

```bash
billweave scenario \
  --amount <金额> --currency <币种> --pay-date <YYYY-MM-DD> --safety-line <安全线> \
  --workspace <工作目录> --year <年>
```

`--year` 默认当前年。结果保存到 `results/raw/calculation_results/scenario_{year}_*.json`。
**所有数字引用 JSON，LLM 不做任何计算。**

### 第 3 步：调用呈现层生成报告

```bash
billweave render \
  --latest \
  --finance-dir <工作目录> \
  --template templates/重大支出分析.md.j2 \
  --output reports/重大支出分析_$(date +%Y%m%d)
```

`--latest` 自动选取最新的 scenario JSON。
若报"找不到模板"，跳过渲染直接按 JSON 汇报。

### 第 4 步：汇报

用普通人能看懂的话，每种方案分别说明：
- 付款后还剩多少可用资金；
- 未来 30 天和 90 天会不会低于安全线；
- 哪些已确定支出可能受影响；
- 计算使用了哪些资料（引用 JSON 中的 `data_summary`）；
- 还缺少哪些信息（引用 JSON 中的 `缺失信息`）。

最后给出明确推荐，**实际付款由用户自己执行**。

## 常见坑

- 必须等数据层生成 CSV 后再跑计算层。
- 金额一律以脚本输出为准，禁止 LLM 心算。
- 新接入账单时先跑 `billweave inspect-match --workspace <工作目录>` 排查表头识别。

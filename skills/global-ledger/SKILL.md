---
name: global-ledger
description: 全局账本:汇总各平台账单、6 级优先级去重分类、按年输出、待确认AI推测+用户终审。任务执行后自动更新,用户问"总共花了多少"或要季度账本时使用。
version: 3.3.0
license: MIT
metadata:
  tags: [finance, 账本, 汇总, 分类, 按年]
  related_skills: [finance-overview, expense-analysis, weekly-finance-digest, financial-export-parsing]
---

# 全局账本（数据层服务）

## When to Use

- 用户问"总共花了多少/收入多少/这个月账目"时，运行本流程查看账本。
- 财务概览 / 重大支出分析 / 每周摘要 执行前，若账本过期，先运行本流程更新。
- 处理待确认交易（用户回答"这笔是什么消费"后确认归类）。
- 用户接入新平台账单后，先用 `billweave inspect-match` 排查表头识别情况。

## 前置规则（必须遵守）

1. 只读取 `<工作目录>` 下的账单/余额文件，不索取银行卡密码、短信验证码、私钥或支付权限。
2. 不修改、覆盖、删除任何原始文件（账单、余额、合同都算原始文件）。
3. 不执行任何付款、转账、投资、借款、交易、报税操作。
4. 所有金额注明币种、数据日期、来源文件。
5. 无法确认的交易标记"待确认"并询问用户，不猜测。
6. 需要汇率换算时先询问用户，不自行补充。

---

## 本 Skill 定位

**数据层服务**：只负责清洗、6 级优先级去重、分类、按年输出标准化数据文件，**不生成用户可见的报告**（报告由各编排型 skill 的呈现层生成）。

### 按年输出

数据层按年切分账本，每年的产物独立存放在 `results/raw/global_bill/`：

| 文件 | 内容 |
|---|---|
| `global_ledger_{year}.csv` | 当年全量交易（已确认+待确认，带"待确认"列），计算层的唯一交易数据源 |
| `global_ledger_{year}.json` | 相同数据的 JSON 版本 |
| `summary_{year}.json` | 当年汇总：总交易数/已确认数/待确认数/剔除数/收入/支出/净结余/按类别/按平台（含"已确认 vs 含待确认"对照） |
| `pending_queue_{year}.csv` | 当年待确认交易队列（供人工/Agent 逐条确认） |
| `removed_records_{year}.csv` | 当年被去重剔除的记录（含剔除原因，可追溯） |
| `confirm_records.json` | 用户确认记录（持久化，跨年共用，重跑自动套用） |

> **从 1.1.x 升级**：首次运行 `billweave ledger` 时，旧版无年份产物（`global_ledger.csv`、`summary.json` 等）会自动归档到 `results/_旧版/<时间戳>/`，新按年产物随后自动生成，不会丢数据。

---

## 工作流程

### 第 0 步：读取特殊情况（必须，防漏）

- 读取 `<工作目录>/README.md` 的"特殊情况"部分（如有），逐条核对本次分析是否涉及；已由脚本自动处理的说明即可。
- 去重规则的唯一事实源是 `<工作目录>/docs/dedup-rules.md`（开源版）或 `deduplication.md`（本地），规则变更时同步更新该文件（脚本逻辑与文档保持一致）。

### 第 1 步：运行数据层脚本（所有计算由脚本完成，禁止心算）

```bash
billweave ledger --workspace <工作目录>
```

脚本自动完成：递归读取 `bills/` 下所有平台账单（支持 csv/xlsx/pdf 混合）→ 6 级优先级去重（退款1→退款2→平台互转→跨平台结算→交易关闭→资金移动兜底）→ 关键词自动分类 → 待确认标记 → **按年输出** CSV/JSON/summary。

### 第 2 步：AI 推测待确认交易类别（核心）

从 `results/raw/global_bill/pending_queue_{year}.csv` 读取当年待确认队列（每行含 `日期|平台|金额|项目|AI推测类别`），**逐条由 AI 推测类别**：

- 依据：商户名/项目文本 + 金额量级 + 日期上下文（如"深圳通/车城通/MTR→交通"、"HEYTEA/肠粉/哈根达斯→餐饮"、"双床房→居住"、"美发→服务"）。
- 将推测结果写入该行 `AI推测类别` 列（**不要**直接确认，用户保留终审权）。
- 无法推测的（如"无名商户/订单付款"）留空或填"不确定"。

写入方式：用 Python 脚本更新 CSV 的 `AI推测类别` 列（按 `日期|平台|金额` 匹配），或直接重写该 CSV（列头不变）。**不修改 `confirm_records.json`**。重跑一次数据层脚本确认列保留（脚本幂等，不覆盖 AI 推测列）。

### 第 3 步：生成可视化（用户终审界面）

AI 推测写入后，**先渲染可视化**，用户后续基于可视化终审（可异步，不要求立即回答）：

1. 渲染全局账本（待确认队列的"类别"列已直接显示 AI 推测值，带"待确认"标签；留空的显示"其他"）：
   ```bash
   billweave render \
     --latest \
     --finance-dir <工作目录> \
     --template templates/全局账本.md.j2 \
     --output reports/全局账本_<YYYYMMDD>
   ```
   若模板需要同时注入 `summary_{year}.json` 与 `global_ledger_{year}.json`，用 `--extra` 指定。

2. 如需季度视图，先跑 `billweave quarter --year <年>`，再渲染季度账本。
3. 向用户简要汇报：已推测 X 笔（交通/餐饮/购物…分布）+ 留空 Y 笔（含大额重点核实项），请用户查看可视化后终审。

### 第 4 步：用户终审后固化

用户确认类别后（口头说"X 改成 Y"或"都对了"），用脚本 `--confirm`（可一次多笔），**不要手工编辑文件**：

```bash
billweave ledger \
  --workspace <工作目录> \
  --confirm "2026-06-06|13.67|餐饮" --confirm "2026-06-09|480|交通"
```

- 格式：`日期|金额|类别`，金额匹配取绝对值；同日期同金额多笔时只确认第一笔（脚本会提示）。
- 确认记录写入 `confirm_records.json`，以后重跑自动归类，不再询问。
- 确认完重跑一次数据层脚本，CSV/JSON/summary 即更新。

**注意**：AI 推测仅供用户参考，**最终类别必须经用户终审**后才写入 confirm_records；用户未批准的推测不固化。

### 第 4.5 步：确认后自动触发概览更新（如用户在做财务概览复盘）

用户终审确认 → 数据层重跑后，若当前处于"财务概览"语境（用户关心概览/建议），**自动**执行以下流程（无需用户额外要求）：

1. **运行计算层（概览）**：
   ```bash
   billweave overview --workspace <工作目录> --year <年>
   ```
2. **LLM 重新生成财务建议**：读取最新 `results/raw/calculation_results/overview_*.json`，基于最新确认数据生成建议，写入 `results/raw/calculation_results/overview_advice_<YYYYMMDD>.json`（格式与要求同 finance-overview skill 第 2.5 步）。
3. **重新渲染概览报告**：
   ```bash
   billweave render \
     --latest \
     --finance-dir <工作目录> \
     --template templates/财务概览.md.j2 \
     --output reports/财务概览_<YYYYMMDD> \
     --extra results/raw/calculation_results/overview_advice_<YYYYMMDD>.json
   ```
4. **简要汇报变化**：告知用户哪些数据因确认而变化（如分类调整导致某类别支出增减），附上最新概览报告链接。

> 此步骤确保确认后财务概览和建议始终反映最新数据状态。若用户仅在核对账本明细（不涉概览），可跳过本步以省 token。

### 第 5 步：用户要查看账本时

用户想直接看账本明细（"总共花了多少"的明细），用呈现层生成视图（汇总读 `summary_{year}.json`，交易明细经 `--extra` 注入 `global_ledger_{year}.json`）：

```bash
billweave render \
  --latest \
  --finance-dir <工作目录> \
  --template templates/全局账本.md.j2 \
  --output reports/全局账本_<YYYYMMDD>
```

> 旧版直接调 `python .../render.py --input summary.json --extra global_ledger.json` 的写法仍可用（向后兼容），但开源版推荐用 `billweave render --latest` 风格，CLI 会自动转发 `--finance-dir`。

### 第 6 步：用户要看某季度账本时（按年账本切片）

用户问"这个季度花了多少/季度账单"时，从当年全局账本按季度切片生成（UI 与全局账本一致，仅范围为本季度）：

```bash
# 1. 计算层：季度切片（默认当前季度；历史季度加 --quarter 2026Q2；
#    --year 2026 自动补齐当年已过季度——缺失创建、当前季度更新、历史已存在跳过）
billweave quarter --workspace <工作目录> --year <年> [--quarter <YYYYQX>]

# 2. 渲染 Markdown 版
billweave render \
  --latest \
  --finance-dir <工作目录> \
  --template templates/季度账本.md.j2 \
  --output reports/<YYYY>年Q<X>季度账单
```

- 产物命名固定为 `results/reports/<YYYY>年Q<X>季度账单.html/.md`（如 `2026年Q3季度账单`）。
- 待确认/剔除记录均按交易日期归入对应季度；汇总附"已确认 vs 含待确认"对照字段。

### 第 7 步：排障入口（新接入账单时）

新接入平台账单（如某银行新出的导出格式）后，**先跑 `billweave inspect-match`** 看看脚本"看到了什么"——表头是否识别正确、字段命中情况、收支分布、平台归类。一切正常再跑数据层，避免脏数据污染账本。

```bash
billweave inspect-match --workspace <工作目录> [--samples 2]
```

- `--samples N`：每个平台抽样 N 行原始记录打印（默认 2）。
- 仅读取账单，不写任何产物；输出到 stdout 供 Agent 分析。

---

## 去重与特殊情况规则（以 docs/dedup-rules.md 为准，此处摘要）

**6 级执行优先级（从高到低，串行执行；高优先级已匹配的交易不再进入低优先级）**：

1. **支付宝退款1**：支付宝端"不计收支"+ 当天银行卡等额收入 → 剔双方
2. **支付宝退款2**：支付宝支出 + 当天银行卡等额支出 + 14 天内有一笔同额支付宝不计收支 → 剔双方
3. **平台互转**：微信/支付宝端中性锚点 + 当天银行端对应交易
   - 提现：金额相差 ≤ 0.25%，手续费 = 较大 - 较小记为支出
   - 充值：金额完全相等，无费
4. **跨平台结算**：同日 2 笔支出、跨平台、支付方式含银行卡、金额一致 → 保留微信/支付宝侧，剔除银行侧
5. **交易关闭**：状态含"交易关闭"（未成交）不计收支，不参与平台互转配对
6. **资金移动（兜底）**：定存/理财/基金/还款等资产搬家；类别记"资金移动"，不计收支

> 详细判定与处理规则见 `docs/dedup-rules.md`。

## 常见坑

- 脚本幂等：同一批账单重复运行不会重复追加（指纹去重）。
- 支付宝"不计收支"交易（亲密花/花呗/红包抵扣/余额宝等）一律剔除不录入；新版会先尝试匹配退款1/退款2，再决定剔除。
- 微信账单"商品"列可能是"商户单号xxx"无意义值，脚本已自动回退用"交易对方"。
- 支付宝导出 CSV 是 GBK 编码，脚本已自动探测；不要手动改编码。
- balance.xlsx 可能是横表（日期+账户名），脚本已支持。
- 招行 PDF 无表格线，脚本走文本行解析，金额列可信，摘要/对手信息可能合并，汇报时说明来源。
- **确认类别只存在于 `confirm_records.json`，不要手工编辑 CSV**；下次重跑脚本会以确认记录为准自动归类。
- 规则变更后：确认记录保留，重跑数据层即可；旧版无年份产物（如 `global_ledger.csv`、`summary.json`）归档到 `results/_旧版/<时间戳>/`，不原地修改。
- **新版统计口径含待确认**：钱已真实发生（金额可信），仅类别未定不影响汇总；summary 同时给出"已确认"和"含待确认"两套口径供对比。

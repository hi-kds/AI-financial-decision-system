---
name: global-ledger
description: 全局账本:汇总各平台账单、去重分类、待确认AI推测+用户终审。任务执行后自动更新,用户问"总共花了多少"或要季度账单时使用。
version: 3.1.0
author: Hermes Agent
license: MIT
metadata:
  tags: [finance, 账本, 汇总, 分类]
  related_skills: [finance-overview, expense-analysis, weekly-finance-digest, feishu-bookkeeping]
---

# 全局账本（数据层服务）

## When to Use

- 用户问"总共花了多少/收入多少/这个月账目"时，运行本流程查看账本。
- 财务概览 / 重大支出分析 / 每周摘要 执行前，若账本过期，先运行本流程更新。
- 处理待确认交易（用户回答"这笔是什么消费"后确认归类）。

## 前置规则（必须遵守）

1. 只读取 `D:\Hermes\finance` 目录内的文件。
2. 不修改、覆盖、删除任何原始文件（账单、余额、合同都算原始文件）。
3. 不索取银行卡密码、短信验证码、私钥或支付权限。
4. 不执行任何付款、转账、投资、借款、交易、报税操作。
5. 所有金额注明币种、数据日期、来源文件。
6. 无法确认的交易标记"待确认"并询问用户，不猜测。
7. 需要汇率换算时先询问用户，不自行补充。

---

## 本 Skill 定位

**数据层服务**：只负责清洗、去重、分类、输出标准化数据文件，**不生成用户可见的报告**（报告由各编排型 skill 的呈现层生成）。

输出文件（`results/raw/global_bill/`）：
| 文件 | 内容 |
|---|---|
| `global_ledger.csv` | 全量交易（已确认+待确认，带"待确认"列），计算层的唯一交易数据源 |
| `global_ledger.json` | 相同数据的 JSON 版本 |
| `summary.json` | 汇总：总交易数/已确认数/待确认数/剔除数/收入/支出/净结余/按类别/按平台 |
| `pending_queue.csv` | 待确认交易队列（供人工/Agent 逐条确认） |
| `removed_records.csv` | 被去重剔除的记录（含剔除原因，可追溯） |
| `confirm_records.json` | 用户确认记录（持久化，重跑自动套用） |

---

## 工作流程

### 第 0 步：读取特殊情况（必须，防漏）

- 读取 `D:\Hermes\finance\README.md` 的"特殊情况"部分，逐条核对本次分析是否涉及；已由脚本自动处理的说明即可。
- 去重规则的唯一事实源是 `D:\Hermes\finance\deduplication.md`，规则变更时同步更新该文件（脚本逻辑与文档保持一致）。

### 第 1 步：运行数据层脚本（所有计算由脚本完成，禁止心算）

```bash
python D:/Hermes/finance/scripts/01_data_layer/global_ledger.py --finance-dir D:/Hermes/finance
```

脚本自动完成：递归读取 `bills/` 下所有平台账单（支持 csv/xlsx/pdf 混合）→ 剔除不计收支（neutral）→ 平台互转去重（差额记手续费）→ 跨平台结算去重 → 关键词自动分类 → 待确认标记 → 输出 CSV/JSON/summary。

### 第 2 步：AI 推测待确认交易类别（核心）

从 `results/raw/global_bill/pending_queue.csv` 读取待确认队列（每行含 `日期|平台|金额|项目|AI推测类别`），**逐条由 AI 推测类别**：

- 依据：商户名/项目文本 + 金额量级 + 日期上下文（如"深圳通/车城通/MTR→交通"、"HEYTEA/肠粉/哈根达斯→餐饮"、"双床房→居住"、"美发→服务"）。
- 将推测结果写入该行 `AI推测类别` 列（**不要**直接确认，用户保留终审权）。
- 无法推测的（如"无名商户/订单付款"）留空或填"不确定"。

写入方式：用 Python 脚本更新 CSV 的 `AI推测类别` 列（按 `日期|平台|金额` 匹配），或直接重写该 CSV（列头不变）。**不修改 `confirm_records.json`**。重跑一次数据层脚本确认列保留（脚本幂等，不覆盖 AI 推测列）。

### 第 3 步：生成可视化（用户终审界面）

AI 推测写入后，**先渲染可视化**，用户后续基于可视化终审（可异步，不要求立即回答）：

1. 渲染全局账本（待确认队列的"类别"列已直接显示 AI 推测值，带"待确认"标签；留空的显示"其他"）：
   ```bash
   python D:/Hermes/finance/scripts/03_render_layer/render.py \
     --input D:/Hermes/finance/results/raw/global_bill/summary.json \
     --extra D:/Hermes/finance/results/raw/global_bill/global_ledger.json \
     --template D:/Hermes/finance/template/全局账本.html.j2 \
     --output D:/Hermes/finance/results/reports/全局账本_<YYYYMMDD>
   ```
2. 如需季度视图，同样渲染季度账本（先跑 quarter_calc.py）。
3. 向用户简要汇报：已推测 X 笔（交通/餐饮/购物…分布）+ 留空 Y 笔（含大额重点核实项），请用户查看可视化后终审。

### 第 4 步：用户终审后固化

用户确认类别后（口头说"X 改成 Y"或"都对了"），用脚本 `--confirm`（可一次多笔），**不要手工编辑文件**：

```bash
python D:/Hermes/finance/scripts/01_data_layer/global_ledger.py \
  --finance-dir D:/Hermes/finance \
  --confirm "2026-06-06|13.67|餐饮" --confirm "2026-06-09|480|交通"
```

- 格式：`日期|金额|类别`，金额匹配取绝对值；同日期同金额多笔时只确认第一笔（脚本会提示）。
- 确认记录写入 `confirm_records.json`，以后重跑自动归类，不再询问。
- 确认完重跑一次数据层脚本，CSV/JSON/summary 即更新。

**注意**：AI 推测仅供用户参考，**最终类别必须经用户终审**后才写入 confirm_records；用户未批准的推测不固化。

### 第 5 步：用户要查看账本时

用户想直接看账本明细（"总共花了多少"的明细），用呈现层生成视图（汇总读 summary.json，交易明细经 `--extra` 注入 global_ledger.json）：

渲染 HTML 直出版：

```bash
python D:/Hermes/finance/scripts/03_render_layer/render.py \
  --input D:/Hermes/finance/results/raw/global_bill/summary.json \
  --extra D:/Hermes/finance/results/raw/global_bill/global_ledger.json \
  --template D:/Hermes/finance/template/全局账本.html.j2 \
  --output D:/Hermes/finance/results/reports/全局账本_<YYYYMMDD>
```

再渲染 Markdown 版（可选）：

```bash
python D:/Hermes/finance/scripts/03_render_layer/render.py \
  --input D:/Hermes/finance/results/raw/global_bill/summary.json \
  --extra D:/Hermes/finance/results/raw/global_bill/global_ledger.json \
  --template D:/Hermes/finance/template/全局账本.md.j2 \
  --output D:/Hermes/finance/results/reports/全局账本_<YYYYMMDD>
```

（模板缺失时直接按 CSV/JSON 汇报。）

### 第 6 步：用户要看某季度账本时（季度切片）

用户问"这个季度花了多少/季度账单"时，从全局账本按季度切片生成（UI 与全局账本一致，仅范围为本季度）：

```bash
# 1. 计算层：季度切片（默认当季；历史季度加 --quarter 2026Q2）
python D:/Hermes/finance/scripts/02_calc_layer/quarter_calc.py --finance-dir D:/Hermes/finance [--quarter 2026Q2]

# 2. 渲染 HTML 直出版
python D:/Hermes/finance/scripts/03_render_layer/render.py \
  --input D:/Hermes/finance/results/raw/calculation_results/quarterly_<季度>.json \
  --template D:/Hermes/finance/template/季度账本.html.j2 \
  --output D:/Hermes/finance/results/<YYYY>年Q<X>季度账单

# 3. 渲染 Markdown 版（可选）
python D:/Hermes/finance/scripts/03_render_layer/render.py \
  --input D:/Hermes/finance/results/raw/calculation_results/quarterly_<季度>.json \
  --template D:/Hermes/finance/template/季度账本.md.j2 \
  --output D:/Hermes/finance/results/<YYYY>年Q<X>季度账单
```

- 产物命名固定为 `results/YYYY年QX季度账单.html/.md`（如 `2026年Q3季度账单`）。
- 待确认/剔除记录均按交易日期归入对应季度；汇总只计已确认。

---

## 去重与特殊情况规则（以 D:\Hermes\finance\deduplication.md 为准，此处摘要）

1. 平台互转：同一天 1 收 1 支、跨平台、微信/支付宝侧含"提现/充值"、金额较大≤较小×101% → 合并记手续费（差额），原两笔不计收支。
2. 跨平台结算：同一天 2 支出、跨平台、微信/支付宝侧付款方式含银行卡、金额一致 → 按数量保留微信/支付宝侧，剔除银行侧。
3. 支付宝退款：收/支栏"不计收支"→ 读取阶段标记 neutral，剔除不录入。
4. 资金移动：定存/理财/基金/申购/赎回/提现/充值/还款等 → 类别"资金移动"，不计入总收入/总支出。
5. 退款绑定逻辑：用户自写（TODO 接入点），接入前退款按收入/待确认处理（临时状态）。
6. 特殊情况（数量对不上、无法唯一确定）：不自动处理，原样保留，汇报时重点复核并询问用户。

## 常见坑

- 脚本幂等：同一批账单重复运行不会重复追加（指纹去重）。
- 支付宝"不计收支"交易（亲密花/花呗/红包抵扣/余额宝等）一律剔除不录入。
- 微信账单"商品"列可能是"商户单号xxx"无意义值，脚本已自动回退用"交易对方"。
- 支付宝导出 CSV 是 GBK 编码，脚本已自动探测；不要手动改编码。
- balance.xlsx 可能是横表（日期+账户名），脚本已支持。
- 招行 PDF 无表格线，脚本走文本行解析，金额列可信，摘要/对手信息可能合并，汇报时说明来源。
- **确认类别只存在于 `confirm_records.json`，不要手工编辑 CSV**；下次重跑脚本会以确认记录为准自动归类。
- 规则变更后：确认记录保留，重跑数据层即可；旧版产物（如 results 根目录的季度账单 .md/.html）归档到 `results/_旧版/`，不原地修改。

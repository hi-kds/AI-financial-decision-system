# billweave

> 本地化、可审计的个人财务分析工具，专为微信/支付宝/银行账单格式设计。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**"把你的账单文件扔进去，输出四份标准化报告"**——数据层去重分类 → 计算层算数 → 渲染层出报告。

## ✨ 特性

| | |
|---|---|
| **三层分离** | 数据层 / 计算层 / 呈现层各自独立，中间产物 JSON/CSV 全部可审计 |
| **跨平台去重** | 自动识别提现/充值配对、银行卡替付结算、退款标记 |
| **多格式解析** | CSV（GBK）、Excel（含元信息行）、PDF（无表格线文本排版）——自动适配编码和表头位置 |
| **待确认队列** | 无法归类到默认类别的交易自动进入待确认，逐条确认后永久记住 |
| **100% 本地** | 无外部 API 调用、无网络请求、不上传任何数据 |
| **Agent 友好** | 天然适配 Hermes/Claude/任意 Agent 编排为 cron 定时任务 |

## 🏗️ 架构

```
工作目录/
├── bills/              ← 你的原始账单（支持 csv/xlsx/pdf）
│   ├── 微信/
│   ├── 支付宝/
│   └── 招商银行/
├── balance/            ← 余额快照（xlsx/csv/pdf）
├── debt/               ← 债务记录
├── results/            ← 自动生成（git 忽略）
│   ├── raw/
│   │   ├── global_bill/           ← 数据层输出（CSV + JSON）
│   │   └── calculation_results/   ← 计算层输出（JSON）
│   └── reports/                   ← 渲染层输出（MD + HTML）
├── templates/          ← 内置报告模板（Jinja2）
└── config.yaml         ← 工作区配置（可选）
```

### 三层管线

1. **数据层** `billweave ledger` — 读取所有账单 → 去重 → 关键词分类 → 输出标准化交易清单
2. **计算层** `billweave overview` / `billweave weekly` / `billweave scenario` — 从标准清单计算各类指标
3. **呈现层** `billweave render` — Jinja2 模板 → Markdown + HTML 报告

每层只读前层输出，不做隐式依赖。任何中间文件损坏不影响其他层。

## 🚀 快速上手

### 安装

```bash
pip install billweave
```

### 生成合成样例（跳过导入账单）

```bash
# 生成虚拟账单用于测试
python -c "
import subprocess, sys
subprocess.run([sys.executable, '-m', 'billweave.sample'], check=True)
"

# 跑一遍完整管线
billweave --workspace . ledger && \
billweave --workspace . overview && \
billweave --workspace . render --latest
```

### 接入真实账单

把你的微信/支付宝导出文件和银行 PDF 按约定目录存放，运行同样的命令即可。

```
bills/                          # 根目录下放账单
├── 微信/                       # 平台名 = 子目录名
│   ├── 微信支付_20260701.xlsx  # 可以是任意文件名
│   └── 微信支付_20260801.csv
├── 支付宝/
│   └── 支付宝明细.csv
└── 招商银行/
    └── 招行流水.pdf
```

### 生成报告

```bash
# 概览报告（总资产、负债、健康评估）
billweave overview --workspace <路径>

# 周报（本周 vs 上周）
billweave weekly --workspace <路径>

# 重大支出方案（一次付 vs 分期 vs 推迟 vs 暂不付）
billweave scenario --amount 10000 --pay-date 2026-12-01 --safety-line 5000 --workspace <路径>

# 统一渲染所有最新 JSON 为报告
billweave render --latest --workspace <路径>
```

## 📋 支持的账单格式

| 平台 | 格式 | 已知问题 | 处理方式 |
|------|------|--------|----------|
| 微信支付 | xlsx | 前 17 行元信息 | 自动检测表头（关键词评分） |
| 支付宝 | CSV (GBK) | 非 UTF-8 编码 | GBK→UTF-8 自动转换 |
| 招商银行 | PDF | 无表格线、纯文本排版 | 按列宽分桶解析 |
| 余额快照 | xlsx/csv | 横表/竖表两种布局 | 自动检测表头结构 |

详细规则见 [docs/dedup-rules.md](docs/dedup-rules.md)。

## 🎯 适用场景

- **个人记账**：把每月导出的账单往里丢，自动生成可视化报告
- **预算规划**：用 scenario 模块做大额消费前的现金流压力测试
- **Agent 集成**：配合 Hermes/AutoGPT 等 Agent 设为每周自动账本（cron）
- **开发者**：参考去重逻辑和解析层代码，处理你自己的财务数据源

## ⚠️ 免责声明

本工具为辅助分析工具，分类基于规则和关键词匹配。请在使用前自行核对关键金额，作者对使用本工具产生的任何结果不承担责任。

## 📄 License

MIT License — 随意使用、修改、分发，但请保留版权说明。

## 💡 贡献

欢迎提交 issue 和 PR。特别是：
- 新增账单格式解析支持
- 模板美化
- 错误修复和性能优化
- 翻译（英文 README 等）

## 👏 特别鸣谢

`billweave` 的诞生离不开开源社区的滋养，特别感谢以下优秀项目：

- **[Jinja2](https://jinja.palletsprojects.com/)** — 提供强大而优雅的报告渲染引擎。
- **[pdfplumber](https://github.com/jsvine/pdfplumber)** — 解决无表格线 PDF 账单文本排版与分桶解析的关键利器。
- **[pandas](https://pandas.pydata.org/)** — 为复杂的账单数据清洗、字段对齐与去重提供底层支持。
- **[Rich](https://github.com/Textualize/rich)** — 让终端命令行交互与日志输出更加美观易读。

同时也感谢所有提交 Issue、PR 以及测试反馈的开源伙伴！
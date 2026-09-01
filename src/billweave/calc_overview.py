#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
overview_calc.py —— 计算层：财务概览（资产负债汇总 + 未来支出 + 未确认交易）

用法：
  python calc_overview.py --finance-dir . [--currency CNY]

输入：
  - 数据层输出：results/raw/global_bill/global_ledger.csv（已去重、已分类、已确认状态）
  - 余额和债务：balance/、debt/ 原始文件（临时过渡，后续可统一纳入数据层）

输出：
  - JSON 文件到 results/raw/calculation_results/overview_<timestamp>.json
  - 包含：现金、可用资金、资产、负债、未来三个月确定支出、未确认交易等

计算由脚本完成，LLM 不得心算。所有金额注明币种、数据日期和来源文件。
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

from billweave import common as fc


def load_global_ledger(csv_path):
    """
    从数据层 CSV 读取交易记录，返回列表。
    字段：日期,平台,类别,收支类型,金额,币种,状态,备注,待确认
    """
    txs = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                amt = float(row['金额'])
            except ValueError:
                continue
            txs.append({
                '日期': row['日期'],
                '平台': row['平台'],
                '类别': row['类别'],
                '收支类型': row['收支类型'],
                '金额': amt,
                '币种': row['币种'],
                '状态': row.get('状态', ''),
                '备注': row.get('备注', ''),
                '待确认': row['待确认'].strip() == '是',
            })
    return txs


def load_balances_and_debts(finance_dir, currency):
    """
    读取余额和债务（原始文件），返回：
      - 现金合计（不受限）
      - 其他资产合计（受限或定期/投资类，按账户名关键词判断）
      - 负债合计
      - 详细余额行、详细债务行（供数据来源披露）
    """
    # ---- 余额 ----
    raw_balances = fc.load_balances(finance_dir)
    # 按账户取最新（如果有重复）
    bal_map = {}
    for b in raw_balances:
        acct = b["账户"]
        prev = bal_map.get(acct)
        if prev is None or (b["数据日期"] and b["数据日期"] > prev["数据日期"]):
            bal_map[acct] = b
    balance_rows = list(bal_map.values())

    cash_total = 0.0
    asset_total = 0.0
    # 关键词用于识别"其他资产"（定期、投资、理财等）
    ASSET_KEYWORDS = ("定期", "理财", "基金", "股票", "债券", "投资", "死期", "存单", "国债")

    for b in balance_rows:
        if b["币种"] != currency:
            continue
        amt = b["金额"]
        acct_lower = b["账户"].lower()
        # 受限原因不为空，或账户名含资产关键词 → 归为"其他资产"
        if b.get("受限原因") or any(kw in acct_lower for kw in ASSET_KEYWORDS):
            asset_total += amt
        else:
            cash_total += amt

    # ---- 债务 ----
    raw_debts = fc.load_debts(finance_dir)
    debt_map = {}
    for d in raw_debts:
        cred = d["债权人"]
        prev = debt_map.get(cred)
        if prev is None or (d["数据日期"] and d["数据日期"] > prev["数据日期"]):
            debt_map[cred] = d
    debt_rows = list(debt_map.values())
    debt_total = sum(d["金额"] for d in debt_rows if d["币种"] == currency)

    # ---- 现金/资产账户明细分类（供呈现层直接循环，模板不做判断） ----
    cash_accounts = []
    asset_accounts = []
    for b in balance_rows:
        if b["币种"] != currency:
            continue
        entry = {
            "账户": b["账户"],
            "金额": b["金额"],
            "币种": b["币种"],
            "数据日期": b.get("数据日期", ""),
            "来源": b.get("来源", ""),
        }
        if b.get("受限原因") or any(kw in b["账户"].lower() for kw in ASSET_KEYWORDS):
            asset_accounts.append(entry)
        else:
            cash_accounts.append(entry)

    # ---- 多币种透明化：收集所有币种的余额合计（不静默丢弃） ----
    all_currency_balances = defaultdict(float)
    for b in balance_rows:
        all_currency_balances[b["币种"]] += b["金额"]

    return (cash_total, asset_total, debt_total, balance_rows, debt_rows,
            dict(all_currency_balances), cash_accounts, asset_accounts)


def calculate_monthly_expense(txs, currency, window_days=180):
    """
    月均支出：优先取最近 window_days 天（默认180天≈6个月，与设计文档"安全线基于过去6个月固定支出"一致）
    内支出的日均值 × 30.44；若窗口内无支出，回退到全部历史。
    口径：含待确认交易（钱已真实发生，类别未定不影响金额）。
    返回 None 表示数据不足（无任何历史支出）。
    """
    today = date.today()
    cutoff = today - timedelta(days=window_days)
    expenses = []  # (日期, 支出金额绝对值)
    for tx in txs:
        if tx["币种"] != currency or tx["金额"] >= 0:
            continue
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        expenses.append((d, abs(tx["金额"])))

    if not expenses:
        return None

    in_window = [(d, a) for d, a in expenses if d >= cutoff]
    if in_window:
        total = sum(a for _, a in in_window)
        span_days = max((today - min(d for d, _ in in_window)).days, 1)
    else:
        total = sum(a for _, a in expenses)
        span_days = max((max(d for d, _ in expenses) - min(d for d, _ in expenses)).days, 1)

    months = max(span_days / 30.44, 1.0)
    return round(total / months, 2)


def assess_health(cash, available, assets, debts, future_expenses_total, monthly_expense):
    """
    财务健康评估 4 指标（确定性规则计算，LLM 只负责翻译，不重新判断）：
      1. 应急储备覆盖月数    = 现金 ÷ 月均支出
      2. 负债率              = 负债 ÷ (现金 + 其他资产)
      3. 未来三月支出覆盖率  = 可用资金 ÷ 未来三月确定支出
      4. 净资产              = 现金 + 其他资产 − 负债
    """
    health = []

    # 1. 应急储备覆盖月数
    if monthly_expense and monthly_expense > 0:
        months = round(cash / monthly_expense, 1)
        status = "健康" if months >= 3 else ("需关注" if months >= 1 else "偏紧")
        health.append({
            "指标": "应急储备覆盖月数",
            "数值": f"{months} 个月",
            "参考区间": "3–6 个月",
            "状态": status,
            "计算依据": f"现金 {cash:.2f} ÷ 月均支出 {monthly_expense:.2f}",
        })
    else:
        health.append({
            "指标": "应急储备覆盖月数",
            "数值": "数据不足",
            "参考区间": "3–6 个月",
            "状态": "数据不足",
            "计算依据": "无可计算月均支出（无历史支出数据）",
        })

    # 2. 负债率
    total_assets = cash + assets
    if total_assets > 0:
        ratio = round(debts / total_assets * 100, 1)
        status = "健康" if ratio < 50 else ("需关注" if ratio <= 70 else "偏紧")
        health.append({
            "指标": "负债率",
            "数值": f"{ratio}%",
            "参考区间": "<50%",
            "状态": status,
            "计算依据": f"负债 {debts:.2f} ÷ 资产 {total_assets:.2f}",
        })
    else:
        health.append({
            "指标": "负债率",
            "数值": "数据不足",
            "参考区间": "<50%",
            "状态": "数据不足",
            "计算依据": "资产合计为 0",
        })

    # 3. 未来三月支出覆盖率
    if future_expenses_total > 0:
        cov = round(available / future_expenses_total * 100, 1)
        status = "健康" if cov >= 100 else ("需关注" if cov >= 80 else "偏紧")
        health.append({
            "指标": "未来三月支出覆盖率",
            "数值": f"{cov}%",
            "参考区间": "≥100%",
            "状态": status,
            "计算依据": f"可用资金 {available:.2f} ÷ 未来三月支出 {future_expenses_total:.2f}",
        })
    else:
        health.append({
            "指标": "未来三月支出覆盖率",
            "数值": "无未来支出",
            "参考区间": "≥100%",
            "状态": "—",
            "计算依据": "未来三个月无确定支出",
        })

    # 4. 净资产
    net = round(cash + assets - debts, 2)
    health.append({
        "指标": "净资产",
        "数值": f"{net:.2f}",
        "参考区间": "—",
        "状态": "—",
        "计算依据": f"现金 {cash:.2f} + 资产 {assets:.2f} − 负债 {debts:.2f}",
    })

    return health


def main():
    ap = argparse.ArgumentParser(description="财务概览：计算资产、负债、未来支出、未确认交易")
    ap.add_argument("--finance-dir", default=None, help="finance 数据根目录")
    ap.add_argument("--currency", default="CNY", help="目标币种（默认 CNY）")
    ap.add_argument("--output", help="输出 JSON 文件路径（默认自动生成到 results/raw/calculation_results/）")
    ap.add_argument("--year", type=int, default=date.today().year, help="账本年份(默认当前年)")
    args = ap.parse_args()
    if not args.finance_dir:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."

    today = date.today()
    currency = args.currency.strip().upper()
    three_months_later = today + timedelta(days=90)

    # ---- 1. 读取数据层输出的全局账本（按年切分） ----
    global_ledger_csv = os.path.join(args.finance_dir, "results", "raw", "global_bill", f"global_ledger_{args.year}.csv")
    if not os.path.exists(global_ledger_csv):
        sys.stderr.write(f"错误：找不到数据层输出文件 {global_ledger_csv}，请先运行 billweave ledger\n")
        sys.exit(1)

    all_txs = load_global_ledger(global_ledger_csv)

    # ---- 2. 从全局账本中提取未来支出和未确认交易 ----
    future_expenses = []       # 未来三个月已确认的支出
    unconfirmed_txs = []       # 所有待确认交易
    future_unconfirmed = []    # 未来三个月待确认交易（提示用户）

    for tx in all_txs:
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue

        # 只关注目标币种
        if tx["币种"] != currency:
            continue

        if tx["待确认"]:
            unconfirmed_txs.append(tx)
            if d <= three_months_later and d >= today:
                future_unconfirmed.append(tx)
            continue

        # 已确认的支出（金额为负），且在三个月内
        if tx["金额"] < 0 and d <= three_months_later and d >= today:
            future_expenses.append({
                "日期": tx["日期"],
                "平台": tx["平台"],
                "类别": tx["类别"],
                "金额": abs(tx["金额"]),          # 转为正数表示支出金额
                "币种": tx["币种"],
                "备注": tx["备注"],
            })

    # ---- 3. 读取余额和债务（原始文件，临时过渡） ----
    (cash_total, asset_total, debt_total, balance_rows, debt_rows,
     all_currency_balances, cash_accounts, asset_accounts) = load_balances_and_debts(
        args.finance_dir, currency
    )

    # 可用资金 = 现金（不受限），与设计文档中“有多少资金可以马上使用”对应
    available_funds = cash_total

    # ---- 3.5 健康评估：月均支出 + 4 指标 ----
    monthly_expense = calculate_monthly_expense(all_txs, currency)
    health_assessment = assess_health(
        cash_total,
        available_funds,
        asset_total,
        debt_total,
        sum(e["金额"] for e in future_expenses),
        monthly_expense,
    )

    # ---- 4. 组装最终 JSON ----
    result = {
        "生成日期": today.isoformat(),
        "数据日期": today.isoformat(),
        "目标币种": currency,
        "财务概览": {
            "现金合计": round(cash_total, 2),
            "可立即使用资金合计": round(available_funds, 2),
            "其他资产合计": round(asset_total, 2),
            "负债合计": round(debt_total, 2),
            "净资产": round(cash_total + asset_total - debt_total, 2),
        },
        "未来三个月确定支出": [
            {
                "日期": e["日期"],
                "平台": e["平台"],
                "类别": e["类别"],
                "金额": e["金额"],
                "币种": e["币种"],
                "备注": e["备注"],
            }
            for e in sorted(future_expenses, key=lambda x: x["日期"])
        ],
        "未来三个月确定支出合计": round(sum(e["金额"] for e in future_expenses), 2),
        "未确认交易（全部）": [
            {
                "日期": tx["日期"],
                "平台": tx["平台"],
                "类别": tx["类别"],
                "金额": tx["金额"],
                "币种": tx["币种"],
                "备注": tx["备注"],
            }
            for tx in sorted(unconfirmed_txs, key=lambda x: x["日期"])
        ],
        "未确认交易总数": len(unconfirmed_txs),
        "未确认交易合计金额": round(sum(tx["金额"] for tx in unconfirmed_txs), 2),
        "健康评估": health_assessment,
        "月均支出（近6个月窗口）": monthly_expense,
        "所有币种余额": {k: round(v, 2) for k, v in all_currency_balances.items()},
        "资产构成": {
            "总资产": round(cash_total + asset_total, 2),
            "现金占比": round(cash_total / (cash_total + asset_total) * 100, 1) if (cash_total + asset_total) > 0 else None,
            "其他资产占比": round(asset_total / (cash_total + asset_total) * 100, 1) if (cash_total + asset_total) > 0 else None,
        },
        "现金账户明细": cash_accounts,
        "资产账户明细": asset_accounts,
        "未来三个月待确认交易": [
            {
                "日期": tx["日期"],
                "平台": tx["平台"],
                "类别": tx["类别"],
                "金额": tx["金额"],
                "币种": tx["币种"],
                "备注": tx["备注"],
            }
            for tx in sorted(future_unconfirmed, key=lambda x: x["日期"])
        ],
        "数据来源": {
            "全局账本": global_ledger_csv,
            "余额快照": [{"账户": b["账户"], "金额": b["金额"], "币种": b["币种"], "日期": b["数据日期"], "受限原因": b.get("受限原因", "")} for b in balance_rows],
            "债务记录": [{"债权人": d["债权人"], "金额": d["金额"], "币种": d["币种"], "日期": d["数据日期"]} for d in debt_rows],
        },
        "缺失/待补充信息": [
            "如需多币种换算，请提供汇率及基准日期。",
            "定期/投资类资产的明细估值（如净值、市值）需用户手动确认。",
            "部分余额账户如存在未入账交易，可能影响实时可用资金。",
        ] + (
            [f"存在非目标币种余额（未计入本报告汇总）：" + "、".join(
                f"{k} {v:.2f}" for k, v in all_currency_balances.items() if k != currency
            ) + "。如需纳入分析请指定 --currency 或提供汇率。"]
            if any(k != currency for k in all_currency_balances) else []
        ),
    }

    # ---- 5. 输出 JSON 文件 ----
    if args.output:
        out_path = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(args.finance_dir, "results", "raw", "calculation_results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"overview_{timestamp}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    sys.stderr.write(f"✅ 财务概览计算完成，结果已保存至：{out_path}\n")
    sys.stderr.write(f"   - 现金: {cash_total:.2f} {currency}\n")
    sys.stderr.write(f"   - 可用资金: {available_funds:.2f} {currency}\n")
    sys.stderr.write(f"   - 其他资产: {asset_total:.2f} {currency}\n")
    sys.stderr.write(f"   - 负债: {debt_total:.2f} {currency}\n")
    sys.stderr.write(f"   - 未来三月确定支出: {sum(e['金额'] for e in future_expenses):.2f} {currency}\n")
    sys.stderr.write(f"   - 待确认交易: {len(unconfirmed_txs)} 笔\n")


if __name__ == "__main__":
    main()
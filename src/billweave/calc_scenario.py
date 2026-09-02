#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_calc.py —— 计算层：重大支出分析（四种支付方案现金流对比）

用法:
  python scenario_calc.py --amount 10000 --currency CNY \\
      --pay-date 2026-09-15 --safety-line 5000 [--finance-dir .]

输入：
  - 从数据层读取 results/raw/global_bill/global_ledger.csv（已去重、已分类）
  - 余额和债务从 balance/、debt/ 原始文件读取（临时过渡，后续可统一）

输出：
  - JSON 文件到 results/raw/calculation_results/scenario_<timestamp>_<amount>_<currency>.json
  - 所有金额注明币种、数据日期和来源文件

计算由脚本完成，LLM 不得心算。
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta

from billweave import common as fc


def load_global_ledger(csv_path):
    """
    从数据层 CSV 读取交易记录，返回列表，每项含日期、平台、类别、金额、币种、待确认。
    CSV 字段：日期,平台,类别,收支类型,金额,币种,状态,备注,待确认
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
                '金额': amt,
                '币种': row['币种'],
                '待确认': row['待确认'].strip() == '是',
                '备注': row['备注'],
            })
    return txs


def load_balances_and_debts(finance_dir, currency):
    """读取余额和债务（原始文件），返回可用现金和负债合计（目标币种）。"""
    balances = {}
    # load_balances 已统一按账户取最新日期快照；下方二次去重仅作防御（幂等，不改变结果）
    for b in fc.load_balances(finance_dir):
        acct = b["账户"]
        prev = balances.get(acct)
        if prev is None or (b["数据日期"] and b["数据日期"] > prev["数据日期"]):
            balances[acct] = b
    cash_available = 0.0
    for b in balances.values():
        if b["币种"] == currency and not b.get("受限原因"):
            cash_available += b["金额"]

    debts = {}
    for db in fc.load_debts(finance_dir):
        cred = db["债权人"]
        prev = debts.get(cred)
        if prev is None or (db["数据日期"] and db["数据日期"] > prev["数据日期"]):
            debts[cred] = db
    debt_total = sum(db["金额"] for db in debts.values() if db["币种"] == currency)

    return cash_available, debt_total, list(balances.values()), list(debts.values())


def analyze_scenario(label, payments, cash_available, expenses, safety_line, today):
    """
    计算单个方案的现金流影响。
    payments: [(pay_date: date, amount: float), ...]
    expenses: 已确定的未来支出列表 [{'日期': date, '金额': float, '项目': str, ...}, ...]
    返回：方案详情字典
    """
    horizon30 = today + timedelta(days=30)
    horizon90 = today + timedelta(days=90)

    total_paid = sum(a for d, a in payments)
    cash_after = cash_available - total_paid

    def min_balance(horizon):
        events = []
        for d, a in payments:
            if today < d <= horizon:
                events.append((d, -a))
        for e in expenses:
            d = e["日期"]
            if today < d <= horizon:
                events.append((d, -e["金额"]))  # e["金额"]为支出（负值），此处减负即加绝对值
        events.sort(key=lambda x: x[0])
        cur = cash_available
        m = cur
        for _, chg in events:
            cur += chg
            m = min(m, cur)
        return round(m, 2)

    min30 = min_balance(horizon30)
    min90 = min_balance(horizon90)
    below30 = min30 < safety_line
    below90 = min90 < safety_line

    # 受影响支出：在90天内导致余额低于安全线的已确定支出
    affected = []
    bal = cash_available
    # 合并所有事件：付款 + 已确定支出（支出为负）
    all_events = [(d, -a, "付款") for d, a in payments]
    for e in expenses:
        all_events.append((e["日期"], -e["金额"], f"支出:{e.get('项目','')}"))
    all_events.sort(key=lambda x: (x[0], 1 if x[2] == "付款" else 0))
    for d, chg, ev_label in all_events:
        bal += chg
        if d <= horizon90 and bal < safety_line:
            affected.append({
                "日期": str(d),
                "事件": ev_label,
                "金额变化": round(chg, 2),
                "当时余额": round(bal, 2),
                "低于安全线差额": round(safety_line - bal, 2),
            })

    return {
        "方案": label,
        "付款计划": [{"日期": str(d), "金额": round(a, 2)} for d, a in payments],
        "付款后立即可用资金": round(cash_after, 2),
        "未来30天最低余额": min30,
        "未来30天是否低于安全线": {"是": below30, "差额": round(safety_line - min30, 2)},
        "未来90天最低余额": min90,
        "未来90天是否低于安全线": {"是": below90, "差额": round(safety_line - min90, 2)},
        "可能受影响的已确定支出": affected,
    }


def main():
    ap = argparse.ArgumentParser(description="重大支出分析：四种支付方案现金流对比")
    ap.add_argument("--amount", required=True, type=float, help="准备支付金额")
    ap.add_argument("--currency", required=True, help="币种，如 CNY / USD")
    ap.add_argument("--pay-date", required=True, help="计划支付日期 YYYY-MM-DD")
    ap.add_argument("--safety-line", required=True, type=float, help="账户安全余额线（至少保留多少钱）")
    ap.add_argument("--finance-dir", default=None, help="finance 数据根目录")
    ap.add_argument("--output", help="输出 JSON 文件路径（默认自动生成到 results/raw/calculation_results/）")
    ap.add_argument("--year", type=int, default=date.today().year, help="账本年份(默认当前年)")
    args = ap.parse_args()
    if not args.finance_dir:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."

    today = date.today()
    currency = args.currency.strip().upper()
    pay_date = datetime.strptime(args.pay_date, "%Y-%m-%d").date()
    if pay_date < today:
        pay_date = today

    # ---- 读取数据层输出的全局账本（按年切分） ----
    global_ledger_csv = os.path.join(args.finance_dir, "results", "raw", "global_bill", f"global_ledger_{args.year}.csv")
    if not os.path.exists(global_ledger_csv):
        sys.stderr.write(f"错误：找不到数据层输出文件 {global_ledger_csv}，请先运行 billweave ledger\n")
        sys.exit(1)

    all_txs = load_global_ledger(global_ledger_csv)

    # ---- 提取未来确定支出（已确认，支出，日期>=今天，币种匹配） ----
    future_expenses = []
    for tx in all_txs:
        if tx["待确认"]:
            continue  # 待确认的不计入确定性支出
        if tx["金额"] >= 0:
            continue  # 收入不计
        if tx["币种"] != currency:
            continue
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            future_expenses.append({
                "日期": d,
                "项目": tx["备注"] or tx["类别"],
                "金额": abs(tx["金额"]),  # 支出金额取绝对值，内部统一为正数（表示支出金额）
                "平台": tx["平台"],
                "类别": tx["类别"],
            })

    # ---- 读取余额和债务（原始文件，临时过渡） ----
    cash_available, debt_total, balance_rows, debt_rows = load_balances_and_debts(args.finance_dir, currency)

    # ---- 现金流模拟 ----
    amount = args.amount
    safety = args.safety_line

    # 分3次付款金额
    p1 = round(amount / 3.0, 2)
    p2 = round(amount / 3.0, 2)
    p3 = round(amount - p1 - p2, 2)

    scenarios = [
        analyze_scenario("方案一：现在一次付清", [(pay_date, amount)], cash_available, future_expenses, safety, today),
        analyze_scenario("方案二：分3次支付（每次间隔30天）",
                         [(pay_date, p1), (pay_date + timedelta(days=30), p2),
                          (pay_date + timedelta(days=60), p3)],
                         cash_available, future_expenses, safety, today),
        analyze_scenario("方案三：推迟90天支付",
                         [(pay_date + timedelta(days=90), amount)], cash_available, future_expenses, safety, today),
        analyze_scenario("方案四：暂时不支付", [], cash_available, future_expenses, safety, today),
    ]

    # ---- 组装最终结果 ----
    result = {
        "生成日期": str(today),
        "数据日期": str(today),
        "inputs": {
            "准备支付金额": amount,
            "币种": currency,
            "计划支付日期": args.pay_date,
            "账户安全余额线": safety,
        },
        "data_summary": {
            "目标币种": currency,
            "可立即使用资金（目标币种）": round(cash_available, 2),
            "未来确定支出（目标币种）": [
                {"日期": str(e["日期"]), "项目": e["项目"], "金额": e["金额"], "平台": e["平台"]}
                for e in future_expenses
            ],
            "负债合计（目标币种）": round(debt_total, 2),
            "余额数据源": [{"账户": b["账户"], "金额": b["金额"], "币种": b["币种"], "日期": b["数据日期"]} for b in balance_rows],
            "债务数据源": [{"债权人": d["债权人"], "金额": d["金额"], "币种": d["币种"]} for d in debt_rows],
        },
        "scenarios": scenarios,
        "假设说明": [
            "所有金额均以目标币种计量，未涉及汇率换算。",
            "未来支出从数据层全局账本中提取（已去重、已分类、已确认）。",
            "安全线比较基于当前可用现金及未来支付计划。",
            "未考虑投资回报、通货膨胀等因素。",
        ],
        "缺失信息": [
            "若存在多币种，需用户提供汇率及基准日期。",
            "合同条款、税务影响、收款方信息等需用户补充。",
        ],
    }

    # ---- 输出 JSON 文件 ----
    if args.output:
        out_path = args.output
    else:
        # 自动生成路径：results/raw/calculation_results/scenario_<timestamp>_<amount>_<currency>.json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(args.finance_dir, "results", "raw", "calculation_results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"scenario_{timestamp}_{amount}_{currency}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 输出路径到 stderr 方便调用者捕获
    sys.stderr.write(f"✅ 计算完成，结果已保存至：{out_path}\n")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_calc.py —— 计算层：财务摘要（本周 vs 上周收支对比）

用法：
  python weekly_calc.py --finance-dir . [--currency CNY]

输入：
  - 数据层输出：results/raw/global_bill/global_ledger.csv（已去重、已分类、已确认状态）

输出：
  - JSON 文件到 results/raw/calculation_results/weekly_<timestamp>.json
  - 包含：本周/上周的收入、支出、净结余、类别汇总、变动百分比

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


def get_week_ranges(today):
    """
    返回本周和上周的起止日期（左闭右开区间）。
    本周：从今天往前推 7 天（不含今天），即 [today-7, today)
    上周：从今天往前推 14 天到 7 天，即 [today-14, today-7)
    这样在任何一天运行都能得到一致的滚动周对比。
    """
    this_start = today - timedelta(days=7)
    this_end = today
    last_start = today - timedelta(days=14)
    last_end = today - timedelta(days=7)
    return {
        "本周": {"开始": this_start, "结束": this_end},
        "上周": {"开始": last_start, "结束": last_end},
    }


def filter_txs_by_range(txs, start_date, end_date, currency):
    """
    按日期范围 [start_date, end_date) 和币种过滤交易。
    返回收入列表、支出列表（金额取绝对值）、以及净额。
    """
    filtered = []
    for tx in txs:
        if tx["币种"] != currency:
            continue
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_date <= d < end_date:
            filtered.append(tx)

    incomes = [t for t in filtered if t["金额"] > 0]
    expenses = [t for t in filtered if t["金额"] < 0]
    return incomes, expenses


def summarize_period(incomes, expenses):
    """
    对一组交易做汇总统计。
    返回：总收入、总支出、净结余、按类别汇总（收入/支出分列）
    """
    total_income = sum(t["金额"] for t in incomes)
    total_expense = sum(-t["金额"] for t in expenses)  # 转为正数
    net = total_income - total_expense

    # 按类别汇总收入
    cat_income = defaultdict(float)
    for t in incomes:
        cat_income[t["类别"]] += t["金额"]

    # 按类别汇总支出（金额取绝对值）
    cat_expense = defaultdict(float)
    for t in expenses:
        cat_expense[t["类别"]] += -t["金额"]

    return {
        "总收入": round(total_income, 2),
        "总支出": round(total_expense, 2),
        "净结余": round(net, 2),
        "交易笔数": len(incomes) + len(expenses),
        "按类别_收入": {k: round(v, 2) for k, v in cat_income.items()},
        "按类别_支出": {k: round(v, 2) for k, v in cat_expense.items()},
    }


def calc_change(current, previous, label):
    """计算两个数值的变化（绝对值和百分比）。"""
    diff = current - previous
    if previous == 0:
        pct = None if current == 0 else 100.0 if current > 0 else -100.0
    else:
        pct = round((diff / abs(previous)) * 100, 1)
    return {
        "当前": current,
        "上期": previous,
        "变动绝对值": round(diff, 2),
        "变动百分比": pct,
    }


def main():
    ap = argparse.ArgumentParser(description="财务摘要：本周 vs 上周收支对比")
    ap.add_argument("--finance-dir", default=None, help="finance 数据根目录")
    ap.add_argument("--currency", default="CNY", help="目标币种（默认 CNY）")
    ap.add_argument("--output", help="输出 JSON 文件路径（默认自动生成到 results/raw/calculation_results/）")
    ap.add_argument("--year", type=int, default=date.today().year, help="账本年份(默认当前年)")
    args = ap.parse_args()
    if not args.finance_dir:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."

    today = date.today()
    currency = args.currency.strip().upper()

    # ---- 1. 读取数据层输出（按年切分） ----
    global_ledger_csv = os.path.join(args.finance_dir, "results", "raw", "global_bill", f"global_ledger_{args.year}.csv")
    if not os.path.exists(global_ledger_csv):
        sys.stderr.write(f"错误：找不到数据层输出文件 {global_ledger_csv}，请先运行 billweave ledger\n")
        sys.exit(1)

    all_txs = load_global_ledger(global_ledger_csv)

    # ---- 2. 口径：收支统计含待确认交易（钱已真实发生，不因类别未定而不计入） ----
    #    待确认交易类别用 AI 推测或"其他"；另保留已确认数供对照
    confirmed_txs = all_txs
    n_confirmed = sum(1 for t in all_txs if not t["待确认"])

    # ---- 3. 获取本周和上周的日期范围 ----
    ranges = get_week_ranges(today)

    # ---- 4. 分别统计 ----
    this_incomes, this_expenses = filter_txs_by_range(
        confirmed_txs, ranges["本周"]["开始"], ranges["本周"]["结束"], currency
    )
    last_incomes, last_expenses = filter_txs_by_range(
        confirmed_txs, ranges["上周"]["开始"], ranges["上周"]["结束"], currency
    )

    this_summary = summarize_period(this_incomes, this_expenses)
    last_summary = summarize_period(last_incomes, last_expenses)

    # ---- 5. 计算变化 ----
    changes = {
        "总收入": calc_change(this_summary["总收入"], last_summary["总收入"], "收入"),
        "总支出": calc_change(this_summary["总支出"], last_summary["总支出"], "支出"),
        "净结余": calc_change(this_summary["净结余"], last_summary["净结余"], "净结余"),
    }

    # ---- 6. 计算各类别变化（合并所有类别） ----
    all_cats = set(this_summary["按类别_收入"].keys()) | set(this_summary["按类别_支出"].keys()) | \
               set(last_summary["按类别_收入"].keys()) | set(last_summary["按类别_支出"].keys())

    category_changes = {}
    for cat in all_cats:
        # 收入变化（本期收入 - 上期收入）
        cur_inc = this_summary["按类别_收入"].get(cat, 0.0)
        prev_inc = last_summary["按类别_收入"].get(cat, 0.0)
        # 支出变化（本期支出 - 上期支出），注意支出为正数
        cur_exp = this_summary["按类别_支出"].get(cat, 0.0)
        prev_exp = last_summary["按类别_支出"].get(cat, 0.0)

        inc_change = calc_change(cur_inc, prev_inc, f"收入-{cat}")
        exp_change = calc_change(cur_exp, prev_exp, f"支出-{cat}")

        category_changes[cat] = {
            "收入": {"当前": cur_inc, "上期": prev_inc, "变动": inc_change["变动绝对值"]},
            "支出": {"当前": cur_exp, "上期": prev_exp, "变动": exp_change["变动绝对值"]},
            "净变动": round((cur_inc - cur_exp) - (prev_inc - prev_exp), 2),
        }

    # ---- 7. 判断是否有显著变化（用于呈现层决定是否输出"本周无显著变化"） ----
    # 如果收入变动 < 5% 且 支出变动 < 5%，则判定为无显著变化（阈值可调）
    THRESHOLD = 5.0
    has_significant_change = False
    for key in ["总收入", "总支出"]:
        pct = changes[key]["变动百分比"]
        if pct is not None and abs(pct) >= THRESHOLD:
            has_significant_change = True
            break
    # 如果收入或支出为 0，且当前与上期差异超过 10 元，也视为显著
    if not has_significant_change:
        if abs(changes["总收入"]["变动绝对值"]) > 10 or abs(changes["总支出"]["变动绝对值"]) > 10:
            has_significant_change = True

    # ---- 8. 组装最终 JSON ----
    # 8.1 本周最大收支 Top3（收入按金额降序、支出按绝对值降序）
    this_incomes_sorted = sorted(this_incomes, key=lambda t: t["金额"], reverse=True)[:3]
    this_expenses_sorted = sorted(this_expenses, key=lambda t: -t["金额"])[:3]
    income_top = [{"项目": t.get("备注", "") or t["类别"], "金额": round(t["金额"], 2), "币种": t["币种"]} for t in this_incomes_sorted]
    expense_top = [{"项目": t.get("备注", "") or t["类别"], "金额": round(-t["金额"], 2), "币种": t["币种"]} for t in this_expenses_sorted]

    # 8.2 未来支出（已确认、支出、日期>=今天，按日期升序）
    future_expenses = []
    for tx in confirmed_txs:
        if tx["金额"] >= 0 or tx["币种"] != currency:
            continue
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            future_expenses.append({
                "项目": tx.get("备注", "") or tx["类别"],
                "金额": round(-tx["金额"], 2),
                "币种": tx["币种"],
                "日期": tx["日期"],
            })
    future_expenses.sort(key=lambda x: x["日期"])

    # 8.3 本周待确认交易（待确认=是 且 日期在本周范围）
    this_pending = []
    for tx in all_txs:
        if not tx["待确认"] or tx["币种"] != currency:
            continue
        try:
            d = datetime.strptime(tx["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if ranges["本周"]["开始"] <= d < ranges["本周"]["结束"]:
            this_pending.append({
                "类型": "待确认",
                "描述": tx.get("备注", "") or tx["类别"],
                "金额": round(tx["金额"], 2),
                "币种": tx["币种"],
                "来源文件": tx["平台"],
                "说明": "类别无法自动确定，需用户确认",
            })
    this_pending.sort(key=lambda x: x["金额"])

    # 8.4 长期未更新账户（余额快照中数据日期距今超过30天的账户）
    stale_accounts = []
    try:
        for b in fc.load_balances(args.finance_dir):
            d = b.get("数据日期")
            if not d:
                continue
            try:
                ddate = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue
            days = (today - ddate).days
            if days > 30:
                stale_accounts.append({"账户": b["账户"], "日期": d, "天数": days})
    except Exception as e:
        sys.stderr.write(f"⚠️  余额快照读取失败（长期未更新账户板块置空）：{e}\n")
    stale_accounts.sort(key=lambda x: -x["天数"])

    result = {
        "生成日期": today.isoformat(),
        "目标币种": currency,
        "对比周期": {
            "本周": {"开始": ranges["本周"]["开始"].isoformat(), "结束": ranges["本周"]["结束"].isoformat()},
            "上周": {"开始": ranges["上周"]["开始"].isoformat(), "结束": ranges["上周"]["结束"].isoformat()},
        },
        "本周汇总": this_summary,
        "上周汇总": last_summary,
        "变化汇总": changes,
        "类别变动": category_changes,
        "是否有显著变化": has_significant_change,
        "本周最大收支": {"收入Top3": income_top, "支出Top3": expense_top},
        "未来支出": future_expenses,
        "本周待确认交易": this_pending,
        "长期未更新账户": stale_accounts,
        "数据来源": {
            "全局账本": global_ledger_csv,
            "已确认交易数": n_confirmed,
            "本周交易数": len(this_incomes) + len(this_expenses),
            "上周交易数": len(last_incomes) + len(last_expenses),
        },
        "注意": [
            f"收支统计含待确认交易（类别未终审，按 AI 推测或'其他'计入），共 {len(confirmed_txs)} 笔；其中已确认 {n_confirmed} 笔。",
            "变化百分比基于上期绝对值计算，若上期为0则显示 ±100% 或 None。",
            "如无显著变化（变动 < 5% 且变动金额 < 10），呈现层可直接显示'本周无显著变化'。",
        ],
    }

    # ---- 9. 输出 JSON 文件 ----
    if args.output:
        out_path = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(args.finance_dir, "results", "raw", "calculation_results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"weekly_{timestamp}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    sys.stderr.write(f"✅ 财务摘要计算完成，结果已保存至：{out_path}\n")
    sys.stderr.write(f"   本周: 收入 {this_summary['总收入']:.2f}  支出 {this_summary['总支出']:.2f}  净结余 {this_summary['净结余']:.2f}\n")
    sys.stderr.write(f"   上周: 收入 {last_summary['总收入']:.2f}  支出 {last_summary['总支出']:.2f}  净结余 {last_summary['净结余']:.2f}\n")
    sys.stderr.write(f"   显著变化: {'是' if has_significant_change else '否'}\n")


if __name__ == "__main__":
    main()
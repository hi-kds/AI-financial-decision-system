#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calc_quarter.py —— 计算层：季度账本（从全局账本按季度切片）

用法：
  python -m billweave.calc_quarter --finance-dir . [--quarter 2026Q3] [--year 2026]

输入（按年账本）：
  - results/raw/global_bill/global_ledger_{year}.csv（全量交易，含待确认）
  - results/raw/global_bill/removed_records_{year}.csv

输出：
  - results/raw/calculation_results/quarterly_<季度>.json
  - 含：季度汇总、支出类别占比、该季度交易记录、已剔除记录
    （待确认队列由交易记录中"待确认=是"过滤）

季度范围：
  Q1: 01-01~03-31  Q2: 04-01~06-30  Q3: 07-01~09-30  Q4: 10-01~12-31
  不传 --quarter 时自动取当前日期所在季度。

口径：汇总（总收入/总支出/净结余）含待确认交易，并另列"已确认总收入/总支出/净结余"对照。
计算由脚本完成，LLM 不得心算。
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

QUARTER_RANGES = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}


def parse_quarter(q):
    """解析 '2026Q3' / '2026q3' → (year, q) 或 None"""
    m = re.match(r"^(\d{4})[Qq]([1-4])$", q.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def current_quarter(d):
    return d.year, (d.month - 1) // 3 + 1


def quarter_bounds(year, q):
    start_s, end_s = QUARTER_RANGES[q]
    start = datetime.strptime(f"{year}-{start_s}", "%Y-%m-%d").date()
    end = datetime.strptime(f"{year}-{end_s}", "%Y-%m-%d").date()
    return start, end


def load_ledger(csv_path):
    """读取全量全局账本 CSV，返回交易列表（金额转 float，待确认转 bool）"""
    txs = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                amt = float(row["金额"])
            except (ValueError, KeyError):
                continue
            txs.append({
                "日期": row["日期"],
                "平台": row["平台"],
                "类别": row["类别"],
                "金额": amt,
                "币种": row["币种"],
                "状态": row.get("状态", ""),
                "备注": row.get("备注", ""),
                "待确认": row["待确认"].strip() == "是",
            })
    return txs


def load_removed(csv_path):
    """读取剔除记录 CSV"""
    rows = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                amt = float(row["金额"])
            except (ValueError, KeyError):
                continue
            rows.append({
                "日期": row["日期"],
                "平台": row["平台"],
                "金额": amt,
                "币种": row["币种"],
                "原因": row["剔除原因"],
                "项目": row.get("项目", ""),
            })
    return rows


def expense_category_share(confirmed_txs):
    """支出类别占比（前5大 + 其余合并'其余'，含累计占比断点，供环形图）
    合并名用"其余"，避免与真实"其他"类别重名。"""
    cat_net = defaultdict(float)
    for t in confirmed_txs:
        if t["金额"] < 0:
            cat_net[t["类别"]] += t["金额"]
    exp_cats = [(k, abs(v)) for k, v in cat_net.items()]
    exp_cats.sort(key=lambda x: -x[1])
    total_exp = sum(a for _, a in exp_cats)
    pct_items = []
    if total_exp > 0:
        top = exp_cats[:5]
        rest = sum(a for _, a in exp_cats[5:])
        if rest > 0.005:
            top.append(("其余", rest))  # 合并名用"其余"，避免与真实"其他"类别重名
        cum = 0.0
        for name, amt in top:
            p = round(amt / total_exp * 100, 1)
            cum += p
            pct_items.append({"类别": name, "金额": round(amt, 2), "占比": p, "累计占比": round(cum, 1)})
    return pct_items


def build_quarter(finance_dir, year, q, today=None, render=False):
    """计算某年某季度账本并输出 JSON。
    输入: global_ledger_{year}.csv + removed_records_{year}.csv (按年账本)。
    输出: results/raw/calculation_results/quarterly_{year}Q{q}.json
    render=True 时调用 billweave.render 渲染季度 HTML（开源版默认不开启）。"""
    today = today or date.today()
    start, end = quarter_bounds(year, q)
    q_label = f"{year}Q{q}"
    q_name = f"{year}年Q{q}季度"

    ledger_csv = os.path.join(finance_dir, "results", "raw", "global_bill", f"global_ledger_{year}.csv")
    removed_csv = os.path.join(finance_dir, "results", "raw", "global_bill", f"removed_records_{year}.csv")
    if not os.path.exists(ledger_csv):
        raise FileNotFoundError(f"错误：找不到 {ledger_csv}，请先运行 billweave ledger")

    all_txs = load_ledger(ledger_csv)

    # ---- 按季度过滤 ----
    in_quarter = []
    for t in all_txs:
        try:
            d = datetime.strptime(t["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= d <= end:
            t["_d"] = d
            in_quarter.append(t)
    in_quarter.sort(key=lambda x: (x["_d"], x["金额"]))

    confirmed = [t for t in in_quarter if not t["待确认"]]
    pending = [t for t in in_quarter if t["待确认"]]

    # ---- 剔除记录按季度过滤 ----
    removed = load_removed(removed_csv)
    removed_in_q = []
    for r in removed:
        try:
            d = datetime.strptime(r["日期"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= d <= end:
            removed_in_q.append(r)
    removed_in_q.sort(key=lambda x: x["日期"])

    # ---- 组装 JSON (口径: 含待确认 + 已确认对照) ----
    total_income = sum(t["金额"] for t in in_quarter if t["金额"] > 0)
    total_expense = sum(-t["金额"] for t in in_quarter if t["金额"] < 0)
    conf_income = sum(t["金额"] for t in confirmed if t["金额"] > 0)
    conf_expense = sum(-t["金额"] for t in confirmed if t["金额"] < 0)
    result = {
        "季度": q_label,
        "季度名称": q_name,
        "生成日期": today.isoformat(),
        "季度范围": {"开始": start.isoformat(), "结束": end.isoformat()},
        "总收入": round(total_income, 2),
        "总支出": round(total_expense, 2),
        "净结余": round(total_income - total_expense, 2),
        "已确认总收入": round(conf_income, 2),
        "已确认总支出": round(conf_expense, 2),
        "已确认净结余": round(conf_income - conf_expense, 2),
        "已确认交易数": len(confirmed),
        "待确认数": len(pending),
        "被剔除数": len(removed_in_q),
        "支出类别占比": expense_category_share(in_quarter),
        "已剔除记录": removed_in_q,
        "交易记录": [
            {
                "日期": t["日期"], "平台": t["平台"], "类别": t["类别"],
                "金额": round(t["金额"], 2), "币种": t["币种"],
                "状态": t["状态"], "备注": t["备注"], "待确认": t["待确认"],
            }
            for t in in_quarter
        ],
        "数据来源": {
            "全局账本": ledger_csv,
            "说明": "季度账本为当年全局账本的季度切片，UI 与全局账本一致，仅数据范围不同。",
        },
    }

    out_dir = os.path.join(finance_dir, "results", "raw", "calculation_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"quarterly_{q_label}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ---- 可选渲染季度可视化 ----
    if render:
        try:
            import subprocess
            # 开源版通过 billweave.render 模块渲染（用户需自行指定模板）
            subprocess.run(
                [sys.executable, "-m", "billweave.render",
                 "--input", out_path, "--template",
                 os.path.join(finance_dir, "templates", "季度账本.html.j2"),
                 "--output", os.path.join(finance_dir, "results", "reports", f"{q_name}账单")],
                capture_output=True
            )
        except Exception as e:
            sys.stderr.write(f"警告: 季度可视化渲染失败: {e}\n")

    sys.stderr.write(f"✅ {q_name}账本计算完成：{out_path}\n")
    sys.stderr.write(f"   - 范围: {start} ~ {end}\n")
    sys.stderr.write(f"   - 已确认: {len(confirmed)} 笔 | 收入 {total_income:.2f} | 支出 {total_expense:.2f} | 净结余 {total_income - total_expense:.2f}\n")
    sys.stderr.write(f"   - 待确认: {len(pending)} 笔 | 剔除: {len(removed_in_q)} 笔\n")
    return result


def build_year_quarters(finance_dir, year, today=None):
    """当年已过季度自动创建/更新：季度账本缺失则创建；当前季度总是更新；历史已存在季度跳过。"""
    today = today or date.today()
    cur_y, cur_q = current_quarter(today)
    if year > cur_y:
        sys.stderr.write(f"⚠️ {year} 年尚未开始，跳过\n")
        return []
    max_q = cur_q if year == cur_y else 4
    made = []
    for q in range(1, max_q + 1):
        out_dir = os.path.join(finance_dir, "results", "raw", "calculation_results")
        out_path = os.path.join(out_dir, f"quarterly_{year}Q{q}.json")
        exists = os.path.exists(out_path)
        if exists and not (year == cur_y and q == cur_q):
            continue  # 历史季度已存在 → 跳过（不重算）
        build_quarter(finance_dir, year, q, today)
        made.append(f"{year}Q{q}")
    return made


def main():
    ap = argparse.ArgumentParser(description="计算层：季度账本（按年全局账本切片）")
    ap.add_argument("--finance-dir", default=None,
                    help="finance 数据根目录（默认 $BILLWEAVE_DATA_DIR 或 .）")
    ap.add_argument("--quarter", default=None, help="季度，如 2026Q3；不传则取当前所在季度")
    ap.add_argument("--year", type=int, default=None,
                    help="年份：自动创建/更新该年已过季度(缺失创建,当季更新)")
    ap.add_argument("--output", help="输出 JSON 路径（默认 quarterly_<季度>.json）")
    args = ap.parse_args()

    if args.finance_dir is None:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."

    today = date.today()

    if args.year:
        made = build_year_quarters(args.finance_dir, args.year, today)
        sys.stderr.write(f"   - 本年生成/更新季度: {made or '无(均已存在且非当前季度)'}\n")
        return
    if args.quarter:
        parsed = parse_quarter(args.quarter)
        if parsed is None:
            sys.stderr.write(f"错误：无法解析季度 '{args.quarter}'，应为 YYYYQN 格式（如 2026Q3）\n")
            sys.exit(1)
        year, q = parsed
    else:
        year, q = current_quarter(today)
    result = build_quarter(args.finance_dir, year, q, today)

    # --output 仅在单季度模式下生效
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        sys.stderr.write(f"   - 副本已写入：{args.output}\n")


if __name__ == "__main__":
    main()

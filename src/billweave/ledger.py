#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
global_ledger.py —— 数据层：清洗、去重、分类、输出干净账本

职责（纯数据层）：
  1. 递归读取 bills/ 下所有平台账单（微信/支付宝/银行等）
  2. 执行去重规则（平台互转、跨平台结算、不计收支）
  3. 自动分类（关键词），不确定的标记为待确认
  4. 输出标准化 CSV 和 JSON 到 results/raw/global_bill/
  5. 支持 --confirm 手动指定待确认交易的类别
  6. 不生成任何 Markdown 或 HTML（呈现层职责）

用法：
  python ledger.py --finance-dir . [--confirm "2026-08-31|12.34|餐饮"]

输出：
  results/raw/global_bill/global_ledger.csv
  results/raw/global_bill/global_ledger.json
  results/raw/global_bill/removed_records.csv
  results/raw/global_bill/pending_queue.csv
  results/raw/global_bill/summary.json
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

from billweave import common as fc

# 支出类别关键词（顺序即优先级）
CATEGORY_RULES = [
    ("居住", ["房租", "水电", "燃气", "物业", "宽带", "住房", "公寓", "宿舍"]),
    ("餐饮", ["外卖", "美团", "饿了么", "餐厅", "食堂", "早餐", "午餐", "晚餐",
              "奶茶", "咖啡", "瑞幸", "蜜雪", "麦当劳", "肯德基", "烧烤", "火锅",
              "小吃", "面包", "蛋糕", "便利店", "超市", "水果", "买菜", "菜场", "餐饮"]),
    ("交通", ["滴滴", "打车", "出租车", "地铁", "公交", "高铁", "火车", "机票",
              "加油", "停车", "共享单车", "骑行", "高速", "12306", "铁路", "出行", "运输"]),
    ("购物", ["淘宝", "天猫", "京东", "拼多多", "优衣库", "迪卡侬", "数码", "手机",
              "耳机", "服饰", "箱包", "app store", "apple", "小米", "华为", "衣服",
              "裤子", "鞋", "百货", "商场", "电商", "旗舰店"]),
    ("教育", ["学费", "书本", "图书", "课程", "培训", "考试", "报名", "教材", "考研", "学校"]),
    ("医疗", ["医院", "药店", "药房", "挂号", "诊所", "体检", "口腔", "买药"]),
    ("娱乐", ["游戏", "steam", "王者", "原神", "视频", "会员", "网易云", "qq音乐",
              "腾讯视频", "爱奇艺", "哔哩", "b站", "电影", "ktv", "icloud", "icould",
              "音乐", "订阅", "漫展", "周边"]),
    ("通讯", ["话费", "联通", "移动", "电信", "流量", "网费"]),
    ("快递", ["顺丰", "快递", "运费", "菜鸟", "驿站", "邮政", "散单"]),
    ("转账", ["转账", "红包", "发给", "零钱转账", "提现", "充值", "定存", "理财", "还款"]),
    ("服务", ["deepseek", "api", "云服务", "服务器", "工具", "软件", "app"]),
]

# 收入特征词（用于分类）
INCOME_WORDS = ("工资", "红包", "收款", "汇入", "转入", "网联收款", "奖学金", "补助")

# 平台互转识别关键词
PLATFORM_TRANSFER_WORDS = ("提现", "充值")
PLATFORM_TRANSFER_TOLERANCE = 1.01
APP_PLATFORMS = ("微信", "支付宝")

# 确认记录文件（存储用户对待确认交易的分类）
CONFIRM_FILE = "confirm_records.json"


def classify(tx):
    """返回 (类别, 是否有把握)"""
    text = ((tx["项目"] or "") + " " + (tx.get("对方") or "") + " " + (tx.get("状态") or "")).lower()
    if tx["类型"] == "income" or any(w in text for w in INCOME_WORDS):
        return "收入", True
    for cat, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return cat, True
    return "其他", False


def match_platform_transfers(txs):
    """平台互转去重，返回 (保留的交易, 生成的手续费交易, 被剔除的记录)"""
    by_date = defaultdict(list)
    for t in txs:
        by_date[t["日期"]].append(t)

    matched_ids, fee_txs, removed = set(), [], []
    for d, cands in by_date.items():
        flagged = []
        for t in cands:
            text = (t["项目"] or "") + (t.get("对方") or "")
            if t["平台"] in APP_PLATFORMS and any(w in text for w in PLATFORM_TRANSFER_WORDS):
                flagged.append(t)
        if not flagged:
            continue
        incomes_all = [t for t in cands if t["类型"] == "income"]
        expenses_all = [t for t in cands if t["类型"] == "expense"]
        for anchor in flagged:
            if id(anchor) in matched_ids:
                continue
            other_side = expenses_all if anchor["类型"] == "income" else incomes_all
            best, best_diff = None, None
            for cand in other_side:
                if id(cand) in matched_ids or cand["平台"] == anchor["平台"]:
                    continue
                big = max(abs(anchor["金额"]), abs(cand["金额"]))
                small = min(abs(anchor["金额"]), abs(cand["金额"]))
                if small <= 0 or big > small * PLATFORM_TRANSFER_TOLERANCE:
                    continue
                diff = round(big - small, 2)
                if best_diff is None or diff < best_diff:
                    best, best_diff = cand, diff
            if best is not None:
                matched_ids.add(id(anchor))
                matched_ids.add(id(best))
                inc = anchor if anchor["类型"] == "income" else best
                exp = best if anchor["类型"] == "income" else anchor
                reason = f"平台互转({exp['平台']}↔{inc['平台']},提现/充值),按差额记手续费"
                removed.append({**inc, "剔除原因": reason})
                removed.append({**exp, "剔除原因": reason})
                if best_diff >= 0.01:
                    fee_txs.append({
                        "日期": d,
                        "平台": f"{exp['平台']}/{inc['平台']}",
                        "类型": "expense",
                        "项目": "平台互转手续费",
                        "金额": -best_diff,
                        "币种": inc["币种"],
                        "对方": "",
                        "备注": "",
                        "支付方式": "",
                        "状态": "",
                    })
    kept = [t for t in txs if id(t) not in matched_ids]
    return kept, fee_txs, removed


def match_cross_platform_settlement(txs):
    """跨平台结算去重，返回 (保留的交易, 被剔除的记录)"""
    groups = defaultdict(list)
    for t in txs:
        if t["类型"] == "expense":
            groups[(t["日期"], round(abs(t["金额"]), 2))].append(t)

    removed_ids, removed = set(), []
    for (d, amt), members in groups.items():
        app_side = [m for m in members
                    if m["平台"] in APP_PLATFORMS and fc.is_bank_card_payment(m.get("支付方式", ""))]
        bank_side = [m for m in members if m["平台"] not in APP_PLATFORMS]
        n = min(len(app_side), len(bank_side))
        for m in bank_side[:n]:
            removed_ids.add(id(m))
            removed.append({**m, "剔除原因": "跨平台结算重复(微信/支付宝已用银行卡支付,保留该记录)"})
    kept = [t for t in txs if id(t) not in removed_ids]
    return kept, removed


def load_confirm_records(confirm_dir):
    """加载之前确认的记录"""
    path = os.path.join(confirm_dir, CONFIRM_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_confirm_records(confirm_dir, records):
    path = os.path.join(confirm_dir, CONFIRM_FILE)
    os.makedirs(confirm_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)


def build_ledger(finance_dir, output_dir, confirm_args=None):
    """主流程：清洗→去重→分类→输出数据文件"""
    today = date.today().isoformat()
    confirm_args = confirm_args or []

    # 1. 读取所有账单
    txs = fc.load_transactions(finance_dir, "bills")

    # 2. 剔除不计收支（neutral）
    active = [t for t in txs if t["类型"] != "neutral"]
    removed = [{**t, "剔除原因": "不计收支(不录入账本)"} for t in txs if t["类型"] == "neutral"]

    # 3. 去重规则一：平台互转
    active, fee_txs, removed_transfer = match_platform_transfers(active)
    removed.extend(removed_transfer)

    # 4. 去重规则二：跨平台结算
    active, removed_settle = match_cross_platform_settlement(active)
    removed.extend(removed_settle)

    # 5. 添加手续费交易（视为普通交易）
    active.extend(fee_txs)

    # 6. 加载已有的确认记录
    confirm_records = load_confirm_records(output_dir)

    # 7. 分类 + 待确认处理
    kept = []
    pending = []  # 未确认类别
    for tx in active:
        cat, confident = classify(tx)
        tx["类别"] = cat
        # 检查是否已有确认记录（用日期+金额+平台作为唯一键）
        key = f"{tx['日期']}|{abs(tx['金额']):.2f}|{tx['平台']}"
        if key in confirm_records:
            tx["类别"] = confirm_records[key]
            tx["待确认"] = False
            kept.append(tx)
        elif confident:
            tx["待确认"] = False
            kept.append(tx)
        else:
            tx["待确认"] = True
            pending.append(tx)

    # 8. 处理 --confirm 参数（手动确认，立即生效）
    for c in confirm_args:
        parts = [p.strip() for p in c.split("|")]
        if len(parts) < 3:
            print(f"警告: 忽略无效确认格式 '{c}'，应为 '日期|金额|类别'", file=sys.stderr)
            continue
        c_date, c_cat = parts[0], parts[2]
        try:
            c_amt = abs(float(parts[1]))
        except ValueError:
            print(f"警告: 忽略无效金额 '{parts[1]}'", file=sys.stderr)
            continue
        # 在 pending 中查找
        matched = None
        for tx in pending:
            if tx["日期"] == c_date and abs(tx["金额"]) == c_amt:
                matched = tx
                break
        if matched:
            matched["类别"] = c_cat
            matched["待确认"] = False
            confirm_records[f"{matched['日期']}|{abs(matched['金额']):.2f}|{matched['平台']}"] = c_cat
            kept.append(matched)
            pending.remove(matched)
            print(f"确认: {c_date} ¥{c_amt} → {c_cat}", file=sys.stderr)
        else:
            print(f"警告: 未找到匹配的待确认交易: {c}", file=sys.stderr)

    # 9. 保存确认记录（供后续运行使用）
    save_confirm_records(output_dir, confirm_records)

    # 10. 去重：避免同一交易在本次运行中重复加入（已通过 id 去重，但仍可能残留，做全局去重）
    seen = set()
    final_kept = []
    for tx in kept:
        key = f"{tx['日期']}|{tx['金额']:.2f}|{tx['平台']}|{tx['项目'][:20]}"
        if key not in seen:
            seen.add(key)
            final_kept.append(tx)

    # 11. 准备输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 全量交易 = 已确认 + 待确认（都带"待确认"标记，供计算层区分）
    # 注意：待确认交易必须进 CSV/JSON，否则计算层（如 overview 的"未确认交易"）
    #       永远看不到它们；汇总统计仍只用已确认部分（见第 16 节）
    all_txs_out = final_kept + pending

    # 12. 输出 CSV（全量交易）
    csv_path = os.path.join(output_dir, "global_ledger.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["日期", "平台", "类别", "收支类型", "金额", "币种", "状态", "备注", "待确认"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for tx in sorted(all_txs_out, key=lambda x: x["日期"]):
            writer.writerow({
                "日期": tx["日期"],
                "平台": tx["平台"],
                "类别": tx["类别"],
                "收支类型": "支出" if tx["金额"] < 0 else "收入",
                "金额": tx["金额"],
                "币种": tx["币种"],
                "状态": tx.get("状态", ""),
                "备注": tx.get("项目", ""),
                "待确认": "是" if tx.get("待确认", False) else "否",
            })

    # 13. 输出 JSON（全量交易）
    json_path = os.path.join(output_dir, "global_ledger.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_txs_out, f, ensure_ascii=False, indent=2, default=str)

    # 14. 输出剔除记录（用于追溯）
    removed_csv = os.path.join(output_dir, "removed_records.csv")
    with open(removed_csv, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["日期", "平台", "金额", "币种", "剔除原因", "项目"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in removed:
            writer.writerow({
                "日期": r["日期"],
                "平台": r["平台"],
                "金额": r["金额"],
                "币种": r["币种"],
                "剔除原因": r["剔除原因"],
                "项目": r.get("项目", ""),
            })

    # 15. 输出待确认队列（单独 CSV）
    pending_csv = os.path.join(output_dir, "pending_queue.csv")
    with open(pending_csv, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["日期", "平台", "金额", "币种", "项目", "当前类别(待确认)"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for tx in sorted(pending, key=lambda x: x["日期"]):
            writer.writerow({
                "日期": tx["日期"],
                "平台": tx["平台"],
                "金额": tx["金额"],
                "币种": tx["币种"],
                "项目": tx.get("项目", ""),
                "当前类别(待确认)": tx["类别"],  # 目前是"其他"或默认
            })

    # 16. 生成汇总统计 JSON
    # 注意：汇总只统计已确认交易（final_kept），待确认交易不计入收支
    summary = {
        "生成日期": today,
        "总交易数（去重后，含待确认）": len(all_txs_out),
        "已确认交易数": len(final_kept),
        "被剔除数": len(removed),
        "待确认数": len(pending),
        "总收入": sum(t["金额"] for t in final_kept if t["金额"] > 0),
        "总支出": sum(-t["金额"] for t in final_kept if t["金额"] < 0),
        "净结余": sum(t["金额"] for t in final_kept),
        "按类别汇总": {},
        "按平台汇总": {},
    }
    # 按类别
    cat_net = defaultdict(float)
    cat_cnt = defaultdict(int)
    for tx in final_kept:
        cat = tx["类别"]
        cat_net[cat] += tx["金额"]
        cat_cnt[cat] += 1
    summary["按类别汇总"] = {k: {"净额": v, "笔数": cat_cnt[k]} for k, v in cat_net.items()}
    # 按平台
    plat_net = defaultdict(float)
    plat_cnt = defaultdict(int)
    for tx in final_kept:
        p = tx["平台"]
        plat_net[p] += tx["金额"]
        plat_cnt[p] += 1
    summary["按平台汇总"] = {p: {"净额": v, "笔数": plat_cnt[p]} for p, v in plat_net.items()}

    # 16.1 支出类别占比（供环形图使用：单类占比 + 累计占比断点；前5大 + 其余合并"其他"）
    exp_cats = [(k, abs(v)) for k, v in cat_net.items() if v < 0]
    exp_cats.sort(key=lambda x: -x[1])
    total_exp = sum(a for _, a in exp_cats)
    pct_items = []
    if total_exp > 0:
        top = exp_cats[:5]
        rest = sum(a for _, a in exp_cats[5:])
        if rest > 0.005:
            top.append(("其他", rest))
        cum = 0.0
        for name, amt in top:
            p = round(amt / total_exp * 100, 1)
            cum += p
            pct_items.append({"类别": name, "金额": round(amt, 2), "占比": p, "累计占比": round(cum, 1)})
    summary["支出类别占比"] = pct_items

    # 16.2 已剔除记录（供呈现层"三、已剔除"表格直接使用）
    summary["已剔除记录"] = [
        {
            "日期": r["日期"],
            "平台": r["平台"],
            "金额": r["金额"],
            "币种": r["币种"],
            "原因": r["剔除原因"],
            "项目": r.get("项目", ""),
        }
        for r in removed
    ]

    summary_json = os.path.join(output_dir, "summary.json")
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 17. 输出简要信息到 stderr（供用户查看）
    print(f"✅ 数据层处理完成", file=sys.stderr)
    print(f"   - 交易总数（去重后，含待确认）: {len(all_txs_out)}", file=sys.stderr)
    print(f"   - 已确认交易: {len(final_kept)}", file=sys.stderr)
    print(f"   - 待确认交易: {len(pending)}", file=sys.stderr)
    print(f"   - 已剔除记录: {len(removed)}", file=sys.stderr)
    print(f"   - 输出目录: {output_dir}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="数据层：生成全局账本（CSV/JSON）")
    ap.add_argument("--finance-dir", default=None,
                    help="finance 数据根目录")
    ap.add_argument("--output-dir", default=None,
                    help="输出目录（默认 finance/results/raw/global_bill）")
    ap.add_argument("--confirm", action="append", default=[], metavar="日期|金额|类别",
                    help="确认待确认交易类别，可多次指定")
    args = ap.parse_args()
    if not args.finance_dir:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."

    if args.output_dir is None:
        args.output_dir = os.path.join(args.finance_dir, "results", "raw", "global_bill")

    build_ledger(args.finance_dir, args.output_dir, args.confirm)


if __name__ == "__main__":
    main()
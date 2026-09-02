#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger.py —— 数据层：清洗、去重、分类、输出干净账本

职责（纯数据层）：
  1. 递归读取 bills/ 下所有平台账单（微信/支付宝/银行等）
  2. 按优先级执行去重规则（退款1>退款2>平台互转>跨平台结算>交易关闭>资金移动兜底）
  3. 自动分类（关键词），不确定的标记为待确认
  4. 按年输出标准化 CSV 和 JSON 到 results/raw/global_bill/
  5. 支持 --confirm 手动指定待确认交易的类别
  6. 不生成任何 Markdown 或 HTML（呈现层职责）

用法：
  python -m billweave.ledger --finance-dir . [--confirm "2026-08-31|12.34|餐饮"]

输出（按年切分，文件名带年份后缀）：
  results/raw/global_bill/global_ledger_{year}.csv
  results/raw/global_bill/global_ledger_{year}.json
  results/raw/global_bill/removed_records_{year}.csv
  results/raw/global_bill/pending_queue_{year}.csv
  results/raw/global_bill/summary_{year}.json

旧版无年份产物（global_ledger.csv 等）首次运行时会被自动归档到
results/_旧版/<时间戳>/，升级用户不会丢数据。
"""

import argparse
import csv
import glob
import json
import os
import re
import shutil
import sys
import time
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
# 提现匹配容差 0.25%；手续费=较大(微信/支付宝)-较小(银行),记为支出
PLATFORM_TRANSFER_TOLERANCE = 0.0025
APP_PLATFORMS = ("微信", "支付宝")

# 平台互转锚点: 微信/支付宝 中性 且 对方/项目/支付方式 指向银行卡(提现到卡/充值/退款回卡)
# 用于识别"钱在不同平台间搬家"的锚点,配对外部才不误配(不带银行卡指向的中性,如账户存取/红包,不参与)
BANK_KEYWORDS = ("银行", "储蓄卡", "信用卡", "借记卡", "招商", "招行", "工行", "建行", "中行", "农行", "农商", "邮政")

# 确认记录文件（存储用户对待确认交易的分类）
CONFIRM_FILE = "confirm_records.json"


def classify(tx):
    """返回 (类别, 是否有把握)"""
    # 平台互转手续费: 固定归类"手续费", 不再走待确认
    if (tx.get("项目") or "") == "平台互转手续费":
        return "手续费", True
    text = ((tx["项目"] or "") + " " + (tx.get("对方") or "") + " " + (tx.get("状态") or "")).lower()
    if tx["类型"] == "income" or any(w in text for w in INCOME_WORDS):
        return "收入", True
    for cat, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return cat, True
    return "其他", False


def _is_transfer_anchor(t):
    """平台互转锚点: 微信/支付宝 且 中性(提现/充值/退款回卡等资产移动)。
    不限定银行卡指向——靠'当天银行端有对应金额'来配对,涵盖支付宝充值逻辑。
    交易关闭的中性交易(未真实成交)不参与配对,其本身按不计收支剔除。"""
    if t["平台"] not in APP_PLATFORMS or t["类型"] != "neutral":
        return False
    if "交易关闭" in (t.get("状态") or ""):
        return False
    return True


def match_alipay_refunds(txs):
    """支付宝退款去重(退款1+退款2 共享原始交易池——退款2 需判断"其后"的同额不计收支,
    故不能把已被退款1剔除的交易从池中拿走)。
    退款1: 支付宝 中性(不计收支) + 当天银行卡等额收入 → 剔双方
    退款2: 支付宝 支出 + 当天银行卡等额支出 + 其后≤14天内有一笔同额支付宝不计收支
          (同日须时间戳更晚; 必须是"不计收支"类型) → 剔双方
    返回 (保留的交易, 剔除记录)。"""
    by_date = defaultdict(list)
    for t in txs:
        by_date[t["日期"]].append(t)
    ali_all = [t for t in txs if t["平台"] == "支付宝"]
    ali_neutral_all = [t for t in ali_all if t["类型"] == "neutral"]
    removed_ids, removed = set(), []

    def _d(s):
        try:
            return date.fromisoformat(s)
        except (TypeError, ValueError):
            return None

    # --- 退款1: 支付宝中性 ↔ 当天银行卡等额收入 ---
    for d, cands in by_date.items():
        bank = [t for t in cands if t["平台"] not in APP_PLATFORMS]
        for a in [t for t in cands if t["平台"] == "支付宝" and t["类型"] == "neutral"]:
            if id(a) in removed_ids:
                continue
            for c in bank:
                if id(c) in removed_ids or c["类型"] != "income":
                    continue
                if abs(c["金额"]) == abs(a["金额"]):
                    removed_ids.add(id(a)); removed_ids.add(id(c))
                    reason = "支付宝退款1(支付宝端↔银行卡侧等额收入),剔双方"
                    removed.append({**a, "剔除原因": reason})
                    removed.append({**c, "剔除原因": reason})
                    break

    # --- 退款2: 支付宝支出 + 当天银行卡等额支出 + 其后≤14天有同额支付宝不计收支 ---
    for a in [t for t in ali_all if t["类型"] == "expense"]:
        if id(a) in removed_ids:
            continue
        a_date = _d(a.get("日期") or "")
        if a_date is None:
            continue
        later_neutral = []
        for n in ali_neutral_all:
            if abs(n["金额"]) != abs(a["金额"]):
                continue
            n_date = _d(n.get("日期") or "")
            if n_date is None:
                continue
            diff = (n_date - a_date).days
            if diff < 0 or diff > 14:
                continue
            if diff == 0 and (n.get("时间") or "") <= (a.get("时间") or ""):
                continue  # 同日必须时间更晚
            later_neutral.append(n)
        if not later_neutral:
            continue  # 其后≤14天无同额不计收支(退款) → 不构成退款2
        for c in by_date.get(a["日期"], []):
            if c["平台"] in APP_PLATFORMS:
                continue
            if id(c) in removed_ids or c["类型"] != "expense":
                continue
            if abs(c["金额"]) == abs(a["金额"]):
                removed_ids.add(id(a)); removed_ids.add(id(c))
                reason = "支付宝退款2(支付宝支出↔银行卡等额支出,其后≤14天有同额不计收支),剔双方"
                removed.append({**a, "剔除原因": reason})
                removed.append({**c, "剔除原因": reason})
                break

    kept = [t for t in txs if id(t) not in removed_ids]
    return kept, removed


def match_platform_transfers(txs):
    """平台互转去重: 微信/支付宝端"中性"锚点 ↔ 当天银行端对应交易,排除多记。
    提现 → 银行端收入(金额差≤0.25%,手续费=较大-较小记支出); 充值 → 银行端支出(金额完全相等,无费)。
    全局贪心匹配(差额最小优先,income略优),避免先到先得错配。
    返回 (保留的交易, 手续费交易, 被剔除的银行端记录)。未配对锚点仍按不计收支由外部剔除。"""
    by_date = defaultdict(list)
    for t in txs:
        by_date[t["日期"]].append(t)
    anchors = [a for a in txs if _is_transfer_anchor(a)]
    if not anchors:
        return txs, [], []

    # 回溯: 不加"提现/充值"方向判定。锚点=微信/支付宝中性, 双向匹配银行端
    # (银行收入→0.25%容差+差额费; 银行支出→完全相等无费), 方向由银行端类型决定
    candidates = []
    for a in anchors:
        for c in by_date.get(a["日期"], []):
            if c["平台"] in APP_PLATFORMS:
                continue
            big = max(abs(a["金额"]), abs(c["金额"]))
            small = min(abs(a["金额"]), abs(c["金额"]))
            if c["类型"] == "income":
                if small > 0 and (big - small) <= small * PLATFORM_TRANSFER_TOLERANCE:
                    fee = round(big - small, 2)
                    candidates.append((a, c, fee, "income"))
            else:
                if abs(c["金额"]) == abs(a["金额"]):
                    candidates.append((a, c, 0.0, "expense"))

    candidates.sort(key=lambda x: (x[2], 0 if x[3] == "income" else 1, x[0]["金额"]))
    matched_anchor_ids, matched_bank_ids = set(), set()
    fee_txs, removed_bank = [], []
    for a, c, fee, direction in candidates:
        if id(a) in matched_anchor_ids or id(c) in matched_bank_ids:
            continue
        matched_anchor_ids.add(id(a)); matched_bank_ids.add(id(c))
        removed_bank.append({**c, "剔除原因": f"平台互转({a['平台']}端↔银行端{c['平台']},忽略银行{'收入' if direction=='income' else '支出'}不计收支)"})
        if direction == "income" and fee >= 0.01:
            fee_txs.append({
                "日期": a["日期"], "平台": f"{a['平台']}/{c['平台']}", "类型": "expense",
                "项目": "平台互转手续费", "金额": -fee, "币种": a["币种"] or c["币种"],
                "对方": "", "备注": "", "支付方式": "", "状态": "",
            })
    kept = [t for t in txs if id(t) not in matched_bank_ids]
    return kept, fee_txs, removed_bank


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
            data = json.load(f)
            if isinstance(data, list):  # 防御: 误写为数组时按空 dict 处理
                return {}
            return data
    return {}


def save_confirm_records(confirm_dir, records):
    path = os.path.join(confirm_dir, CONFIRM_FILE)
    os.makedirs(confirm_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)


def build_ledger(finance_dir, output_dir, confirm_args=None, confirm_files=None, default_ai=False):
    """主流程：清洗→去重→分类→按年输出数据文件"""
    today = date.today().isoformat()
    confirm_args = confirm_args or []
    confirm_files = confirm_files or []

    # 1. 读取所有账单
    txs = fc.load_transactions(finance_dir, "bills")

    # 2. 按优先级去重: 退款1 > 退款2 > 平台互转 > 跨平台结算 > 交易关闭(资金移动兜底)
    removed = []

    # 优先级1+2: 支付宝退款1/退款2 (共享原始池, 退款2要求"其后有同额不计收支")
    txs, rem_refund = match_alipay_refunds(txs)
    removed.extend(rem_refund)

    # 优先级3: 平台互转 (微信/支付宝中性锚点 ↔ 银行端; 手续费=较大-较小)
    txs, fee_txs, rem_pt = match_platform_transfers(txs)
    removed.extend(rem_pt)

    # 优先级4: 跨平台结算 (微信/支付宝用银行卡付款 + 银行侧重复, 剔银行侧)
    txs, rem_cs = match_cross_platform_settlement(txs)
    removed.extend(rem_cs)

    # 优先级5: 交易关闭 (未成交 → 中性剔除)
    closed = [t for t in txs if "交易关闭" in (t.get("状态") or "")]
    removed.extend({**t, "剔除原因": "交易关闭(未成交),不计收支"} for t in closed)
    txs = [t for t in txs if "交易关闭" not in (t.get("状态") or "")]

    # 优先级6: 其余中性(资金移动/不计收支) 剔除
    neut = [t for t in txs if t["类型"] == "neutral"]
    removed.extend({**t, "剔除原因": "不计收支(不录入账本)"} for t in neut)
    active = [t for t in txs if t["类型"] != "neutral"]

    # 手续费交易视为普通交易
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

    # 8.5 处理 --confirm-file（用户标记 CSV 批量确认，与 --confirm 同优先级、同固化机制）
    #     在第 10 节 final_kept 生成前执行，移入 kept 的交易会随后被计入全量输出；
    #     在第 12 节按年分组前执行，by_year_pending/kept 会自动反映最新状态。
    if confirm_files:
        n, warns = apply_confirm_file(pending, kept, confirm_records, confirm_files)
        for w in warns:
            print(f"警告: {w}", file=sys.stderr)
        if n:
            print(f"确认文件: 固化 {n} 笔", file=sys.stderr)

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

    # 11.5 待确认交易的"类别"列直接显示 AI 推测值（仍带"待确认"标记，未终审不固化）
    #     读取源：该年的 pending_queue_<year>.csv（无年份旧文件已按年化归档），按 日期|平台|金额 匹配；
    #     "不确定"/留空 保持"其他"。重跑幂等保留。
    def _load_ai_speculation(year):
        """读该年待确认队列的 AI 推测类别，返回 {日期|平台|金额: 类别}（排除'不确定'）。"""
        m = {}
        p = os.path.join(output_dir, f"pending_queue_{year}.csv")
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    val = (row.get("AI推测类别") or "").strip()
                    if val and val != "不确定":
                        m[f"{row['日期']}|{row['平台']}|{row['金额']}"] = val
        return m

    # 12. 按年分组输出（一年一个全局账本）
    by_year = defaultdict(list)
    for tx in all_txs_out:
        by_year[tx["日期"][:4]].append(tx)
    by_year_kept = defaultdict(list)
    for tx in final_kept:
        by_year_kept[tx["日期"][:4]].append(tx)
    by_year_pending = defaultdict(list)
    for tx in pending:
        by_year_pending[tx["日期"][:4]].append(tx)
    by_year_removed = defaultdict(list)
    for r in removed:
        by_year_removed[r["日期"][:4]].append(r)

    # 迁移: 读旧全局 pending_queue.csv 的 AI 推测(首次按年运行时保留)
    legacy_ai = {}
    legacy_pend = os.path.join(output_dir, "pending_queue.csv")
    if os.path.exists(legacy_pend):
        try:
            with open(legacy_pend, newline="", encoding="utf-8-sig") as f:
                legacy_ai = {f"{r['日期']}|{r['平台']}|{r['金额']}": r.get("AI推测类别", "")
                             for r in csv.DictReader(f)}
        except Exception:
            pass

    csv_fields = ["日期", "平台", "类别", "收支类型", "金额", "币种", "状态", "备注", "待确认"]
    years = sorted(by_year.keys())
    for year in years:
        txs_y = by_year[year]
        kept_y = by_year_kept.get(year, [])
        pend_y = by_year_pending.get(year, [])
        rem_y = by_year_removed.get(year, [])

        # 11.5b 待确认类别回填 AI 推测（该年队列文件；txs_y/pend_y 同对象，CSV/JSON/汇总自动一致）
        ai_map = _load_ai_speculation(year)
        for tx in pend_y:
            k = f"{tx['日期']}|{tx['平台']}|{tx['金额']}"
            if k in ai_map:
                tx["类别"] = ai_map[k]

        # 11.5c --default-ai: 用户未标记且未拒绝的交易，若 AI 有具体推测(类别≠其他)则按推测固化
        #     （用户填"不确定"的行已打 _user_skip 标记，不参与自动固化）
        if default_ai:
            for tx in pend_y[:]:
                if tx.get("_user_skip"):
                    continue
                if tx["类别"] != "其他":
                    confirm_records[f"{tx['日期']}|{abs(tx['金额']):.2f}|{tx['平台']}"] = tx["类别"]
                    tx["待确认"] = False
                    kept_y.append(tx)
                    pend_y.remove(tx)

        # 12a. 交易 CSV
        csv_path = os.path.join(output_dir, f"global_ledger_{year}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields)
            writer.writeheader()
            for tx in sorted(txs_y, key=lambda x: x["日期"]):
                writer.writerow({
                    "日期": tx["日期"], "平台": tx["平台"], "类别": tx["类别"],
                    "收支类型": "支出" if tx["金额"] < 0 else "收入",
                    "金额": tx["金额"], "币种": tx["币种"],
                    "状态": tx.get("状态", ""), "备注": tx.get("项目", ""),
                    "待确认": "是" if tx.get("待确认", False) else "否",
                })

        # 12b. 交易 JSON
        with open(os.path.join(output_dir, f"global_ledger_{year}.json"), "w", encoding="utf-8") as f:
            json.dump(txs_y, f, ensure_ascii=False, indent=2, default=str)

        # 12c. 剔除记录
        with open(os.path.join(output_dir, f"removed_records_{year}.csv"), "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["日期", "平台", "金额", "币种", "剔除原因", "项目"])
            writer.writeheader()
            for r in rem_y:
                writer.writerow({"日期": r["日期"], "平台": r["平台"], "金额": r["金额"],
                                 "币种": r["币种"], "剔除原因": r["剔除原因"], "项目": r.get("项目", "")})

        # 12d. 待确认队列(带 AI推测列, 幂等保留)
        pend_fields = ["日期", "平台", "金额", "币种", "项目", "AI推测类别", "当前类别(待确认)"]
        pend_csv = os.path.join(output_dir, f"pending_queue_{year}.csv")
        rows_out = []
        for tx in sorted(pend_y, key=lambda x: x["日期"]):
            rows_out.append({"日期": tx["日期"], "平台": tx["平台"], "金额": tx["金额"],
                             "币种": tx["币种"], "项目": tx.get("项目", ""),
                             "AI推测类别": tx.get("AI推测类别", ""), "当前类别(待确认)": tx["类别"]})
        old_ai = dict(legacy_ai)
        if os.path.exists(pend_csv):
            try:
                with open(pend_csv, newline="", encoding="utf-8-sig") as f:
                    old_ai.update({f"{r['日期']}|{r['平台']}|{r['金额']}": r.get("AI推测类别", "")
                                   for r in csv.DictReader(f)})
            except Exception:
                pass
        for r in rows_out:
            k = f"{r['日期']}|{r['平台']}|{r['金额']}"
            if k in old_ai and old_ai[k]:
                r["AI推测类别"] = old_ai[k]
        with open(pend_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=pend_fields)
            writer.writeheader()
            for r in rows_out:
                writer.writerow(r)

        # 12e. 年度汇总 (口径含待确认 + 已确认对照)
        income_all = sum(t["金额"] for t in txs_y if t["金额"] > 0)
        expense_all = sum(-t["金额"] for t in txs_y if t["金额"] < 0)
        income_conf = sum(t["金额"] for t in kept_y if t["金额"] > 0)
        expense_conf = sum(-t["金额"] for t in kept_y if t["金额"] < 0)
        summary = {
            "年份": year,
            "生成日期": today,
            "总交易数（去重后，含待确认）": len(txs_y),
            "已确认交易数": len(kept_y),
            "被剔除数": len(rem_y),
            "待确认数": len(pend_y),
            "总收入": round(income_all, 2),
            "总支出": round(expense_all, 2),
            "净结余": round(income_all - expense_all, 2),
            "已确认总收入": round(income_conf, 2),
            "已确认总支出": round(expense_conf, 2),
            "已确认净结余": round(income_conf - expense_conf, 2),
            "按类别汇总": {},
            "按平台汇总": {},
        }
        cat_net = defaultdict(float); cat_cnt = defaultdict(int)
        for tx in txs_y:
            cat_net[tx["类别"]] += tx["金额"]; cat_cnt[tx["类别"]] += 1
        summary["按类别汇总"] = {k: {"净额": v, "笔数": cat_cnt[k]} for k, v in cat_net.items()}
        plat_net = defaultdict(float); plat_cnt = defaultdict(int)
        for tx in txs_y:
            plat_net[tx["平台"]] += tx["金额"]; plat_cnt[tx["平台"]] += 1
        summary["按平台汇总"] = {p: {"净额": v, "笔数": plat_cnt[p]} for p, v in plat_net.items()}
        exp_cats = sorted(((k, abs(v)) for k, v in cat_net.items() if v < 0), key=lambda x: -x[1])
        total_exp = sum(a for _, a in exp_cats)
        pct_items = []
        if total_exp > 0:
            top = exp_cats[:5]
            rest = sum(a for _, a in exp_cats[5:])
            if rest > 0.005:
                top.append(("其余", rest))  # 合并名用"其余"，避免与真实"其他"类别重名
            cum = 0.0
            for name, amt in top:
                p = round(amt / total_exp * 100, 1); cum += p
                pct_items.append({"类别": name, "金额": round(amt, 2), "占比": p, "累计占比": round(cum, 1)})
        summary["支出类别占比"] = pct_items
        summary["已剔除记录"] = [
            {"日期": r["日期"], "平台": r["平台"], "金额": r["金额"], "币种": r["币种"],
             "原因": r["剔除原因"], "项目": r.get("项目", "")} for r in rem_y
        ]
        with open(os.path.join(output_dir, f"summary_{year}.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    # 12f. 11.5c(--default-ai)在循环内新增了确认记录, 此处统一落盘(9 节的保存早于它)
    save_confirm_records(output_dir, confirm_records)

    # 17. 输出简要信息
    print("✅ 数据层处理完成（按年输出）", file=sys.stderr)
    for year in years:
        print(f"   - {year} 年账本: {len(by_year[year])} 笔 | 已确认 {len(by_year_kept.get(year, []))} | 待确认 {len(by_year_pending.get(year, []))} | 剔除 {len(by_year_removed.get(year, []))}", file=sys.stderr)
    print(f"   - 输出目录: {output_dir}", file=sys.stderr)


def apply_confirm_file(pending, kept, confirm_records, files):
    """读取用户标记 CSV(列含: 日期|平台|金额|用户标记类别)批量确认。
    '用户标记类别' 为空或'不确定'的行跳过。返回 (固化笔数, 警告列表)。
    与 --confirm 同固化机制: 写入 confirm_records(日期|金额|平台), 交易移入 kept。
    用户填"不确定"= 明确拒绝 AI 推测 → 在对应待确认交易上打 _user_skip 标记，
    供 --default-ai 使用(用户已拒绝的不再自动固化)。"""
    n = 0
    warns = []
    for path in files:
        if not os.path.exists(path):
            warns.append(f"确认文件不存在: {path}")
            continue
        try:
            with open(path, newline="", encoding=fc.detect_encoding(path)) as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            warns.append(f"确认文件读取失败 {path}: {e}")
            continue
        for row in rows:
            cat = (row.get("用户标记类别") or "").strip()
            if not cat or cat == "不确定":
                # 用户填"不确定" = 明确拒绝 AI 推测，标记为跳过，--default-ai 不得自动固化
                if cat == "不确定":
                    d0 = (row.get("日期") or "").strip()
                    p0 = (row.get("平台") or "").strip()
                    try:
                        a0 = abs(float(row.get("金额")))
                    except (TypeError, ValueError):
                        a0 = None
                    for tx in pending:
                        if (a0 is not None and tx["日期"] == d0
                                and abs(tx["金额"]) == a0 and tx["平台"] == p0):
                            tx["_user_skip"] = True
                            break
                continue
            d = (row.get("日期") or "").strip()
            plat = (row.get("平台") or "").strip()
            try:
                amt = abs(float(row.get("金额")))
            except (TypeError, ValueError):
                warns.append(f"金额无效,跳过: {d}|{row.get('金额')}|{cat}")
                continue
            matched = None
            for tx in pending:
                if tx["日期"] == d and abs(tx["金额"]) == amt and tx["平台"] == plat:
                    matched = tx
                    break
            if not matched:
                warns.append(f"未找到匹配的待确认交易(可能已确认): {d}|{plat}|{amt} → {cat}")
                continue
            matched["类别"] = cat
            matched["待确认"] = False
            confirm_records[f"{matched['日期']}|{abs(matched['金额']):.2f}|{matched['平台']}"] = cat
            kept.append(matched)
            pending.remove(matched)
            n += 1
    return n, warns


def export_pending_mark(output_dir, confirm_dir, force=False):
    """生成待确认标记 CSV 到 confirm_dir(列: 日期,平台,金额,币种,项目,AI推测类别,用户标记类别)，
    供用户手动填写'用户标记类别'列完成终审。已存在且非 force 时跳过(保留用户已填内容)。"""
    os.makedirs(confirm_dir, exist_ok=True)
    made = 0
    for gl_csv in sorted(glob.glob(os.path.join(output_dir, "global_ledger_*.csv"))):
        year = os.path.basename(gl_csv).replace("global_ledger_", "").replace(".csv", "")
        out = os.path.join(confirm_dir, f"待确认标记_{year}.csv")
        if os.path.exists(out) and not force:
            print(f"跳过(已存在,可能含用户填写): {out}", file=sys.stderr)
            continue
        pend = []
        with open(gl_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("待确认") == "是":
                    pend.append(r)
        if not pend:
            print(f"  {year} 年无待确认交易,跳过", file=sys.stderr)
            continue
        ai = {}
        pq = os.path.join(output_dir, f"pending_queue_{year}.csv")
        if os.path.exists(pq):
            with open(pq, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    ai[f"{r['日期']}|{r['平台']}|{r['金额']}"] = (r.get("AI推测类别") or "").strip()
        fields = ["日期", "平台", "金额", "币种", "项目", "AI推测类别", "用户标记类别"]
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in sorted(pend, key=lambda x: x["日期"]):
                k = f"{r['日期']}|{r['平台']}|{r['金额']}"
                w.writerow({
                    "日期": r["日期"], "平台": r["平台"], "金额": r["金额"],
                    "币种": r["币种"], "项目": r["备注"],
                    "AI推测类别": ai.get(k, ""), "用户标记类别": "",
                })
        print(f"生成待确认标记文件: {out} ({len(pend)} 笔)", file=sys.stderr)
        made += 1
    if made == 0:
        print("无新标记文件生成(全部已存在或用 --force 重建)", file=sys.stderr)


def _archive_legacy(finance_dir, output_dir):
    """归档无年份旧产物(global_ledger.csv 等)与旧季度报告到 results/_旧版/<时间戳>/。"""
    archive = os.path.join(finance_dir, "results", "_旧版", time.strftime("%Y%m%d_%H%M%S"))
    legacy = [
        os.path.join(output_dir, "global_ledger.csv"),
        os.path.join(output_dir, "global_ledger.json"),
        os.path.join(output_dir, "summary.json"),
        os.path.join(output_dir, "removed_records.csv"),
        os.path.join(output_dir, "pending_queue.csv"),
    ]
    for f in legacy:
        if os.path.exists(f):
            os.makedirs(archive, exist_ok=True)
            shutil.move(f, os.path.join(archive, os.path.basename(f)))
    for pat in ("*年Q*季度账单.html", "*年Q*季度账单.md"):
        for f in glob.glob(os.path.join(finance_dir, "results", pat)):
            os.makedirs(archive, exist_ok=True)
            shutil.move(f, os.path.join(archive, os.path.basename(f)))


def main():
    ap = argparse.ArgumentParser(description="数据层：生成全局账本（按年输出）")
    ap.add_argument("--finance-dir", default=None, help="finance 数据根目录（默认 $BILLWEAVE_DATA_DIR 或 .）")
    ap.add_argument("--output-dir", default=None,
                    help="输出目录（默认 finance/results/raw/global_bill）")
    ap.add_argument("--confirm", action="append", default=[], metavar="日期|金额|类别",
                    help="确认待确认交易类别，可多次指定")
    ap.add_argument("--confirm-file", action="append", default=[], metavar="CSV路径",
                    help="从用户标记 CSV 批量确认(列: 日期,平台,金额,用户标记类别)，可多次指定")
    ap.add_argument("--default-ai", action="store_true",
                    help="配合 --confirm-file: 用户未标记的交易若 AI 有具体推测则自动按推测固化(用户填'不确定'的不固化)")
    ap.add_argument("--export-pending-mark", action="store_true",
                    help="生成待确认标记 CSV 到 --confirm-dir，供用户手动填写'用户标记类别'列")
    ap.add_argument("--confirm-dir", default=None,
                    help="待确认标记 CSV 目录(默认 <finance-dir>/confirm)")
    ap.add_argument("--force", action="store_true",
                    help="--export-pending-mark 时强制重建已存在的标记文件(覆盖用户已填内容，慎用)")
    # --no-auto 仅为向后兼容保留（开源版默认不自动渲染，本开关恒为真）
    ap.add_argument("--no-auto", action="store_true", help="(已无副作用,开源版默认不自动渲染)")
    args = ap.parse_args()

    if args.finance_dir is None:
        args.finance_dir = os.environ.get("BILLWEAVE_DATA_DIR") or "."
    if args.output_dir is None:
        args.output_dir = os.path.join(args.finance_dir, "results", "raw", "global_bill")
    if args.confirm_dir is None:
        args.confirm_dir = os.path.join(args.finance_dir, "confirm")

    if args.default_ai and not args.confirm_file:
        print("警告: --default-ai 需配合 --confirm-file 使用, 本次忽略", file=sys.stderr)

    build_ledger(args.finance_dir, args.output_dir, args.confirm, args.confirm_file, args.default_ai)
    _archive_legacy(args.finance_dir, args.output_dir)
    if args.export_pending_mark:
        export_pending_mark(args.output_dir, args.confirm_dir, args.force)


if __name__ == "__main__":
    main()

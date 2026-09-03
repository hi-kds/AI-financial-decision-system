#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke test —— 确保 billweave 核心函数行为正确，未来不会回归。

跑法：
    cd D:/开源项目/billweave
    PYTHONPATH=src python -m pytest tests/test_smoke.py -v

或直接：
    PYTHONPATH=src python tests/test_smoke.py
"""
import os
import sys

# 让 src/ 在 import 路径里
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest


# ---------- common.py: classify_income_expense 决策树 ----------

from billweave import common as fc


def test_classify_neutral_words_first():
    """不计收支关键词先于收支判断，避免被误判。"""
    row = {"收/支": "不计收支", "金额": "100.00"}
    assert fc.classify_income_expense(row) == "neutral"


def test_classify_expense_word():
    row = {"收/支": "支出", "金额": "50.00"}
    assert fc.classify_income_expense(row) == "expense"


def test_classify_income_word():
    row = {"收/支": "收入", "金额": "100.00"}
    assert fc.classify_income_expense(row) == "income"


def test_classify_no_type_amount_signed_negative_is_expense():
    """类型列读不到时，amount_signed=True → 按金额符号判（银行流水）。"""
    row = {"金额": "-100.00"}  # 无"收/支"列
    assert fc.classify_income_expense(row, amount_signed=True) == "expense"


def test_classify_no_type_amount_signed_positive_is_income():
    row = {"金额": "100.00"}
    assert fc.classify_income_expense(row, amount_signed=True) == "income"


def test_classify_no_type_amount_unsigned_is_neutral():
    """类型列读不到时，amount_signed=False（微信/支付宝） → 归中性。"""
    row = {"金额": "100.00"}
    assert fc.classify_income_expense(row, amount_signed=False) == "neutral"


def test_classify_no_type_no_amount_is_neutral():
    """金额解析失败 → neutral（不再返回 unknown）。"""
    row = {"金额": ""}
    assert fc.classify_income_expense(row, amount_signed=True) == "neutral"


def test_file_amount_signed_detects_negative():
    rows = [{"金额": "100.00"}, {"金额": "-50.00"}]
    assert fc._file_amount_signed(rows) is True


def test_file_amount_signed_all_positive():
    rows = [{"金额": "100.00"}, {"金额": "50.00"}]
    assert fc._file_amount_signed(rows) is False


def test_file_amount_signed_no_amounts():
    rows = [{"金额": ""}, {"金额": "abc"}]
    assert fc._file_amount_signed(rows) is False


# ---------- common.py: extract_transaction 时间字段 ----------

def test_extract_transaction_has_time_field():
    """extract_transaction 应在 base 加 '时间' 字段（保留原始日期字符串）。"""
    row = {"交易时间": "2026-08-10 09:05:12", "金额": "500.00", "收/支": "支出"}
    tx = fc.extract_transaction("/tmp/fake.csv", row, "支付宝", amount_signed=False)
    assert tx is not None
    assert tx["日期"] == "2026-08-10"
    assert tx["时间"] == "2026-08-10 09:05:12"
    assert tx["平台"] == "支付宝"
    assert tx["金额"] == -500.00  # 支出统一为负


def test_extract_transaction_income_positive():
    row = {"交易时间": "2026-08-05", "金额": "120.00", "收/支": "收入"}
    tx = fc.extract_transaction("/tmp/fake.csv", row, "微信", amount_signed=False)
    assert tx is not None
    assert tx["类型"] == "income"
    assert tx["金额"] == 120.00


def test_extract_transaction_neutral_keeps_original_amount():
    """中性交易金额不取绝对值，原样保留（供 ledger 去重阶段按金额匹配）。"""
    row = {"交易时间": "2026-08-10", "金额": "500.00", "收/支": "不计收支"}
    tx = fc.extract_transaction("/tmp/fake.csv", row, "支付宝", amount_signed=False)
    assert tx is not None
    assert tx["类型"] == "neutral"
    assert tx["金额"] == 500.00  # 原样保留，不取绝对值


# ---------- ledger.py: 6 级去重核心函数 ----------

from billweave import ledger


def _mktx(date_s, platform, type_, amount, status="", time_s=""):
    """构造一个标准化交易 dict（测试用）。"""
    return {
        "日期": date_s, "时间": time_s or date_s, "平台": platform,
        "类型": type_, "项目": "test", "金额": amount,
        "币种": "CNY", "状态": status, "对方": "", "备注": "", "支付方式": "",
    }


def test_is_transfer_anchor_wx_neutral():
    assert ledger._is_transfer_anchor(_mktx("2026-08-10", "微信", "neutral", 500.00)) is True


def test_is_transfer_anchor_alipay_neutral():
    assert ledger._is_transfer_anchor(_mktx("2026-08-10", "支付宝", "neutral", 500.00)) is True


def test_is_transfer_anchor_bank_neutral_is_false():
    """银行端中性不是锚点（锚点只限微信/支付宝）。"""
    assert ledger._is_transfer_anchor(_mktx("2026-08-10", "招商银行", "neutral", 500.00)) is False


def test_is_transfer_anchor_closed_is_false():
    """交易关闭的中性不参与配对。"""
    t = _mktx("2026-08-10", "微信", "neutral", 500.00, status="交易关闭")
    assert ledger._is_transfer_anchor(t) is False


def test_is_transfer_anchor_expense_is_false():
    """非中性（支出/收入）不是锚点。"""
    assert ledger._is_transfer_anchor(_mktx("2026-08-10", "微信", "expense", -500.00)) is False


def test_classify_platform_transfer_fee_fixed():
    """平台互转手续费固定归类'手续费'，不再走待确认。"""
    cat, confident = ledger.classify({"项目": "平台互转手续费", "类型": "expense", "金额": -1.50})
    assert cat == "手续费"
    assert confident is True


def test_match_alipay_refunds_refund1():
    """退款1: 支付宝中性 + 当天银行卡等额收入 → 剔双方。"""
    txs = [
        _mktx("2026-08-10", "支付宝", "neutral", 100.00),
        _mktx("2026-08-10", "招商银行", "income", 100.00),
        _mktx("2026-08-10", "微信", "expense", -50.00),
    ]
    kept, removed = ledger.match_alipay_refunds(txs)
    assert len(kept) == 1  # 只剩微信支出
    assert len(removed) == 2  # 支付宝中性 + 招行收入都被剔除


def test_match_platform_transfers_withdrawal_with_fee():
    """提现: 微信中性锚点 + 招行收入（金额差 ≤ 0.25%）→ 剔银行端，差额记手续费。"""
    txs = [
        _mktx("2026-08-10", "微信", "neutral", 500.00),       # 微信端提现 500
        _mktx("2026-08-10", "招商银行", "income", 499.00),    # 银行端到账 499（差 1 元，≤ 0.25%）
    ]
    kept, fee_txs, removed_bank = ledger.match_platform_transfers(txs)
    assert len(kept) == 1  # 微信锚点保留（中性，由外部按不计收支剔除）
    assert len(removed_bank) == 1  # 银行端被剔除
    assert len(fee_txs) == 1  # 手续费交易
    assert fee_txs[0]["金额"] == -1.00  # 500 - 499 = 1 元手续费
    assert fee_txs[0]["项目"] == "平台互转手续费"


def test_match_platform_transfers_recharge_no_fee():
    """充值: 微信中性锚点 + 招行支出（金额完全相等）→ 剔银行端，无手续费。"""
    txs = [
        _mktx("2026-08-10", "支付宝", "neutral", 500.00),    # 支付宝端充值 500
        _mktx("2026-08-10", "招商银行", "expense", -500.00), # 银行端扣款 500（完全相等）
    ]
    kept, fee_txs, removed_bank = ledger.match_platform_transfers(txs)
    assert len(kept) == 1  # 支付宝锚点保留
    assert len(removed_bank) == 1  # 银行端被剔除
    assert len(fee_txs) == 0  # 无手续费


def test_match_cross_platform_settlement_removes_bank_side():
    """跨平台结算: 微信用银行卡付款 + 银行同日同额支出 → 剔银行侧。"""
    txs = [
        _mktx("2026-08-15", "微信", "expense", -24.00, status="支付成功"),
        _mktx("2026-08-15", "招商银行", "expense", -24.00),
    ]
    # 微信侧支付方式需含"银行卡"才识别为跨平台结算候选
    txs[0]["支付方式"] = "招商银行储蓄卡(1234)"
    kept, removed = ledger.match_cross_platform_settlement(txs)
    assert len(kept) == 1  # 微信侧保留
    assert len(removed) == 1  # 银行侧剔除


# ---------- 问题2：余额快照按账户取最新日期（load_balances 统一口径） ----------

import csv as _csv
import tempfile
from billweave import common as fc


def _write_balance_csv(tmp, name, rows):
    """在 tmp/balance 下写一个竖表余额 CSV。"""
    bal_dir = os.path.join(tmp, "balance")
    os.makedirs(bal_dir, exist_ok=True)
    path = os.path.join(bal_dir, name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["账户", "金额", "币种", "日期"])
        for r in rows:
            w.writerow(r)
    return path


def test_load_balances_takes_latest_date_per_account():
    """同一账户多个日期快照 → load_balances 只返回最新日期那一行。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write_balance_csv(tmp, "余额.csv", [
            ["微信", "100.00", "CNY", "2026-08-30"],
            ["微信", "150.00", "CNY", "2026-09-02"],
        ])
        rows = fc.load_balances(tmp)
        wx = [r for r in rows if r["账户"] == "微信"]
        assert len(wx) == 1
        assert wx[0]["数据日期"] == "2026-09-02"
        assert wx[0]["金额"] == 150.00


def test_load_balances_keeps_distinct_accounts():
    """不同账户各自保留最新快照，互不影响。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write_balance_csv(tmp, "余额.csv", [
            ["微信", "100.00", "CNY", "2026-08-30"],
            ["微信", "150.00", "CNY", "2026-09-02"],
            ["招商银行", "5000.00", "CNY", "2026-09-02"],
        ])
        rows = fc.load_balances(tmp)
        accts = {r["账户"]: r["数据日期"] for r in rows}
        assert accts == {"微信": "2026-09-02", "招商银行": "2026-09-02"}


# ---------- 问题3：apply_confirm_file 批量确认 ----------

def _mktx_pending(date_s, platform, amount, category="其他"):
    return {
        "日期": date_s, "时间": date_s, "平台": platform,
        "类型": "expense", "项目": "test", "金额": amount,
        "币种": "CNY", "状态": "支付成功", "对方": "", "备注": "", "支付方式": "",
        "类别": category, "待确认": True,
    }


def test_apply_confirm_file_confirms_marked():
    """用户标记 CSV 中填了类别 → 交易固化进 kept，并写入 confirm_records。"""
    with tempfile.TemporaryDirectory() as tmp:
        mark = os.path.join(tmp, "标记.csv")
        with open(mark, "w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["日期", "平台", "金额", "币种", "项目", "AI推测类别", "用户标记类别"])
            w.writerow(["2026-08-10", "微信", "12.34", "CNY", "测试", "餐饮", "交通"])
            w.writerow(["2026-08-11", "支付宝", "5.00", "CNY", "测试2", "购物", ""])  # 空 → 跳过
            w.writerow(["2026-08-12", "微信", "8.00", "CNY", "测试3", "餐饮", "不确定"])  # 拒绝 → 跳过+标记
        pending = [
            _mktx_pending("2026-08-10", "微信", -12.34),
            _mktx_pending("2026-08-11", "支付宝", -5.00),
            _mktx_pending("2026-08-12", "微信", -8.00),
        ]
        kept = []
        records = {}
        n, warns = ledger.apply_confirm_file(pending, kept, records, [mark])
        assert n == 1  # 只固化 1 笔（第 1 笔填了"交通"）
        assert kept[0]["类别"] == "交通"
        assert not kept[0]["待确认"]
        assert "2026-08-10|12.34|微信" in records and records["2026-08-10|12.34|微信"] == "交通"
        # 未标记/拒绝的交易仍在 pending
        assert len(pending) == 2
        # 用户填"不确定"的被打 _user_skip
        assert any(t["日期"] == "2026-08-12" and t.get("_user_skip") for t in pending)


def test_apply_confirm_file_warns_unmatched():
    """CSV 中找不到匹配交易 → 警告列表给出提示，不影响其他固化。"""
    with tempfile.TemporaryDirectory() as tmp:
        mark = os.path.join(tmp, "标记.csv")
        with open(mark, "w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["日期", "平台", "金额", "用户标记类别"])
            w.writerow(["2026-09-01", "微信", "99.00", "购物"])  # 待确认中无此笔
        pending = [_mktx_pending("2026-08-10", "微信", -12.34)]
        kept = []
        records = {}
        n, warns = ledger.apply_confirm_file(pending, kept, records, [mark])
        assert n == 0
        assert any("未找到匹配" in w for w in warns)


# ---------- calc_overview: 固定资产/固定支出读取（同步自本地版） ----------

import json as _json
from billweave import calc_overview as co


def test_load_fixed_assets_sums_valuation():
    """fixed_assets.json 读取：汇总"估值"字段，负值/异常忽略。"""
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "confirm")
        os.makedirs(cf, exist_ok=True)
        with open(os.path.join(cf, "fixed_assets.json"), "w", encoding="utf-8") as f:
            _json.dump([
                {"资产类型": "电子设备", "名称描述": "笔记本", "估值": 4500.0, "币种": "CNY", "估值日期": "2026-08-30"},
                {"资产类型": "交通工具", "名称描述": "自行车", "估值": 600.0, "币种": "CNY", "估值日期": "2026-08-30"},
                {"资产类型": "已售", "名称描述": "旧手机", "估值": -100.0, "币种": "CNY", "估值日期": "2026-01-01"},
            ], f, ensure_ascii=False)
        assets, total = co.load_fixed_assets(tmp)
        assert len(assets) == 3
        assert total == 5100.0  # 4500 + 600，负估值忽略


def test_load_fixed_assets_missing_returns_empty():
    assert co.load_fixed_assets(tempfile.mkdtemp()) == ([], 0.0)


def test_load_fixed_expenses_reads_confirm_file():
    """fixed_expenses.json 读取：confirm/ 下顶层数组原样返回。"""
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "confirm")
        os.makedirs(cf, exist_ok=True)
        with open(os.path.join(cf, "fixed_expenses.json"), "w", encoding="utf-8") as f:
            _json.dump([{"名称": "房租", "日期": "2026-10-01", "金额": 2500.0, "币种": "CNY"}], f, ensure_ascii=False)
        data = co.load_fixed_expenses(tmp)
        assert len(data) == 1
        assert data[0]["名称"] == "房租"


def test_load_fixed_expenses_missing_returns_empty():
    assert co.load_fixed_expenses(tempfile.mkdtemp()) == []


if __name__ == "__main__":
    # 不依赖 pytest 也能跑
    sys.exit(pytest.main([__file__, "-v"]))

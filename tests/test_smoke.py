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


if __name__ == "__main__":
    # 不依赖 pytest 也能跑
    sys.exit(pytest.main([__file__, "-v"]))

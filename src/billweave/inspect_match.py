#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_match.py —— 账单匹配体检工具（排障用，不参与正式流水线）

用途：
  新导入一份账单后，先跑它，确认脚本到底"看到了什么"：
    1. 表头行被定位在哪一行（前 40 行打分最高的那行）
    2. 每个内部字段(日期/金额/类型/...)命中的是哪个实际列名
    3. 哪些字段没命中 —— 这就是需要到 billweave.common.ALIASES 里补别名的
    4. 收支判定结果分布(income / expense / neutral)
    5. 每平台前 N 条标准化后的样例

用法：
  python -m billweave.inspect_match                    # 扫描 $BILLWEAVE_DATA_DIR 或 .
  python -m billweave.inspect_match --samples 5        # 每平台打印 5 条样例
  python -m billweave.inspect_match --finance-dir <路径>

依赖：openpyxl（读 xlsx）、pdfplumber（读 pdf）；缺哪个就跳过哪种格式。
"""

import argparse
import collections
import io
import os
import sys

# 强制 stdout 用 utf-8 输出（避免 Windows 控制台 GBK 报错）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from billweave import common as fc

# 账单场景下关心的内部字段
BILL_FIELDS = ["日期", "金额", "类型", "项目", "对方", "支付方式", "平台", "状态", "币种"]


def matched_column(headers, field):
    """返回该内部字段在 headers 中命中的第一个别名；未命中返回 None。"""
    for alias in fc.ALIASES.get(field, [field]):
        if alias in headers:
            return alias
    return None


def inspect_file(path, finance_dir, samples):
    rel = os.path.relpath(path, finance_dir)
    print("=" * 78)
    print("文件:", rel)

    headers, rows = fc.read_table(path)
    if not rows:
        # 横表(余额快照常见)不走 read_table,单独提示
        wide = fc.try_read_wide(path) if path.lower().endswith((".xlsx", ".xlsm", ".csv")) else []
        if wide:
            print("  read_table 无结果,但检测到横表 %d 个单元格 —— 由 try_read_wide 处理,属正常" % len(wide))
        else:
            print("  !! 未解析出任何数据行:表头定位失败(前 40 行关键词得分 < 2),或文件无表格结构")
        return

    print("  解析到 %d 行" % len(rows))
    print("  原始表头: %s" % headers)

    misses = []
    for f in BILL_FIELDS:
        hit = matched_column(headers, f)
        if hit is None:
            misses.append(f)
        print("    %-6s -> %s" % (f, hit or "【未命中】"))
    if misses:
        print("    ⚠ 未命中字段: %s —— 需到 billweave.common.ALIASES 补列名"
              % "、".join(misses))

    signed = fc._file_amount_signed(rows)
    print("  amount_signed(金额列含负数,用于无收支列时的兜底) =", signed)

    counter = collections.Counter()
    shown = 0
    for r in rows:
        tx = fc.extract_transaction(path, r, os.path.basename(os.path.dirname(path)), signed)
        if not tx:
            continue
        counter[tx["类型"]] += 1
        if shown < samples:
            shown += 1
            print("    样例: 日期=%s 类型=%-7s 项目=%s 金额=%s 对方=%s 支付=%s"
                  % (tx["日期"], tx["类型"], tx["项目"][:22], tx["金额"], tx["对方"][:16], tx["支付方式"][:20]))
    print("  收支判定分布:", dict(counter) or "(无有效行)")


def main():
    ap = argparse.ArgumentParser(description="账单匹配体检工具：定位表头/字段命中/收支分布")
    ap.add_argument("--finance-dir", default=None, help="finance 根目录（默认 $BILLWEAVE_DATA_DIR 或 .）")
    ap.add_argument("--dir", default=None, help="等价于 --finance-dir（向后兼容）")
    ap.add_argument("--samples", type=int, default=3, help="每个文件打印的样例条数")
    args = ap.parse_args()

    finance_dir = args.finance_dir or args.dir or os.environ.get("BILLWEAVE_DATA_DIR") or "."

    for folder, label in (("bills", "账单"), ("balance", "余额"), ("debt", "债务")):
        print("\n\n########## %s (%s) ##########" % (label, folder))
        root = os.path.join(finance_dir, folder)
        files = fc.find_files(root)
        if not files:
            print("  (目录为空)")
        for p in files:
            inspect_file(p, finance_dir, args.samples)

    print("\n\n########## balance/debt 走专用分支的结果 ##########")
    print("load_balances:", fc.load_balances(finance_dir) or "(空)")
    print("load_debts  :", fc.load_debts(finance_dir) or "(空)")


if __name__ == "__main__":
    main()

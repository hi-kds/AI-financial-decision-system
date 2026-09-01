#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Billweave CLI 入口

用法：
  billweave ledger    --workspace <路径>             数据层：生成全局账本（按年）
  billweave overview  --workspace <路径> [--year N]  计算层：财务概览
  billweave weekly    --workspace <路径> [--year N]  计算层：本周摘要
  billweave scenario  --amount N ...                 计算层：重大支出分析
  billweave quarter   --workspace <路径> [--year N | --quarter 2026Q3]  计算层：季度账本
  billweave render    --latest --workspace <路径>     渲染层：生成报告
  billweave sample    --workspace <路径>             生成合成样例数据
  billweave inspect-match --workspace <路径>         账单匹配体检（排障工具）
"""
import argparse
import subprocess
import sys


def _module_main(module_name, extra_args=None):
    """运行 billweave 子模块的 __main__ 入口。"""
    cmd = [sys.executable, "-m", f"billweave.{module_name}"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, check=False).returncode


def main():
    parser = argparse.ArgumentParser(
        prog="billweave",
        description="Billweave — 本地化、可审计的个人财务分析工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ledger
    sp = subparsers.add_parser("ledger", help="数据层：生成全局账本（按年输出）")
    sp.add_argument("--workspace", default=".", help="工作目录（默认当前目录）")
    sp.add_argument("--confirm", action="append", default=[], metavar="日期|金额|类别",
                    help="确认待确认交易类别，可多次指定")

    # overview
    sp = subparsers.add_parser("overview", help="计算层：财务概览")
    sp.add_argument("--workspace", default=".", help="工作目录")
    sp.add_argument("--year", type=int, default=None, help="账本年份（默认当前年）")

    # weekly
    sp = subparsers.add_parser("weekly", help="计算层：本周摘要")
    sp.add_argument("--workspace", default=".", help="工作目录")
    sp.add_argument("--year", type=int, default=None, help="账本年份（默认当前年）")

    # scenario
    sp = subparsers.add_parser("scenario", help="计算层：重大支出分析")
    sp.add_argument("--workspace", default=".", help="工作目录")
    sp.add_argument("--amount", type=float, required=False, help="支出金额")
    sp.add_argument("--pay-date", default=None, help="支付日期")
    sp.add_argument("--safety-line", type=float, default=None, help="安全线")
    sp.add_argument("--year", type=int, default=None, help="账本年份（默认当前年）")

    # quarter
    sp = subparsers.add_parser("quarter", help="计算层：季度账本")
    sp.add_argument("--workspace", default=".", help="工作目录")
    sp.add_argument("--year", type=int, default=None,
                    help="年份：自动创建/更新该年已过季度（缺失创建，当季更新）")
    sp.add_argument("--quarter", default=None, help="指定季度，如 2026Q3")

    # render
    sp = subparsers.add_parser("render", help="渲染层：生成报告")
    sp.add_argument("--workspace", default=".", help="工作目录")
    sp.add_argument("--latest", action="store_true", help="渲染最新 JSON")
    sp.add_argument("--template", default=None, help="指定模板")

    # sample
    sp = subparsers.add_parser("sample", help="生成合成样例数据")
    sp.add_argument("--workspace", default=".", help="输出目录（默认当前目录）")

    # inspect-match（调试工具）
    sp = subparsers.add_parser("inspect-match", help="账单匹配体检：定位表头/字段命中/收支分布（排障用）")
    sp.add_argument("--workspace", default=".", help="工作目录（默认当前目录）")
    sp.add_argument("--samples", type=int, default=3, help="每个文件打印的样例条数")

    # 解析
    args, remaining = parser.parse_known_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # sample 子命令：直接调用 sample.generate()
    if args.command == "sample":
        from billweave.sample import generate
        generate(args.workspace)
        sys.exit(0)

    # inspect-match 子命令：直接调用 inspect_match.main 的逻辑（按 --finance-dir 转发）
    if args.command == "inspect-match":
        forward = ["--finance-dir", args.workspace, "--samples", str(args.samples)]
        forward.extend(remaining)
        sys.exit(_module_main("inspect_match", forward))

    # 其他子命令：转发到对应模块
    module_map = {
        "ledger": "ledger",
        "overview": "calc_overview",
        "weekly": "calc_weekly",
        "scenario": "calc_scenario",
        "quarter": "calc_quarter",
        "render": "render",
    }

    module_name = module_map.get(args.command)
    if module_name is None:
        parser.print_help()
        sys.exit(1)

    # 构建转发参数
    forward_args = []
    if hasattr(args, "workspace"):
        forward_args.extend(["--finance-dir", args.workspace])
    if hasattr(args, "latest") and args.latest:
        forward_args.append("--latest")
    if hasattr(args, "template") and args.template:
        forward_args.extend(["--template", args.template])
    if hasattr(args, "amount") and args.amount is not None:
        forward_args.extend(["--amount", str(args.amount)])
    if hasattr(args, "pay_date") and args.pay_date is not None:
        forward_args.extend(["--pay-date", args.pay_date])
    if hasattr(args, "safety_line") and args.safety_line is not None:
        forward_args.extend(["--safety-line", str(args.safety_line)])
    if hasattr(args, "year") and args.year is not None:
        forward_args.extend(["--year", str(args.year)])
    if hasattr(args, "quarter") and args.quarter is not None:
        forward_args.extend(["--quarter", args.quarter])
    if hasattr(args, "confirm") and args.confirm:
        for c in args.confirm:
            forward_args.extend(["--confirm", c])

    # 透传未识别的参数
    forward_args.extend(remaining)

    rc = _module_main(module_name, forward_args)
    sys.exit(rc)


if __name__ == "__main__":
    main()


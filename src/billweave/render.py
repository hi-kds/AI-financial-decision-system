#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py —— 呈现层：JSON 数据 → Markdown + HTML 报告

用法:
  # 通用用法
  python render.py --input results/raw/calculation_results/overview_20260831.json \\
                   --template template/财务概览.md.j2 \\
                   --output reports/财务概览_20260831

  # 或通过 skill 编排调用

输入：
  - 计算层输出的 JSON（如 overview_*.json、weekly_*.json、scenario_*.json）
  - 对应的 Jinja2 模板（*.md.j2）

输出：
  - 同一个目录下生成 .md 和 .html 文件

特性：
  - 完全由数据驱动，不做任何计算
  - 所有数据和计算逻辑在计算层完成
  - 呈现层仅负责"模板填充 + HTML 美化"
  - 沿用原有 CSS 样式体系（与之前保持一致）
"""

import glob
import json
import os
import sys
import argparse
from datetime import datetime

# 尝试导入模板引擎
try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    print("错误：需要安装 jinja2：pip install jinja2", file=sys.stderr)
    sys.exit(1)

try:
    import markdown
except ImportError:
    print("错误：需要安装 markdown：pip install markdown", file=sys.stderr)
    sys.exit(1)


# ============================================================
# CSS 样式（延续原有风格，仅做微调，数据完全由 JSON 驱动）
# ============================================================
CSS = """
:root {
  --bg: #F5F6F3;
  --surface: #FFFFFF;
  --ink: #1E2523;
  --ink-soft: #5C6660;
  --ink-faint: #97A19A;
  --line: #DADFDA;
  --positive: #2F6F4F;
  --negative: #A83A32;
  --pending: #B8862F;
  --pending-bg: #FBF3E3;
  --c0: #2F6F4F;
  --c1: #97A19A;
  --c2: #B8862F;
  --c3: #5C6660;
  --c4: #A83A32;
  --c5: #8A9A5B;
  --c6: #4A6FA5;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--sans);
}
.page {
  max-width: 880px;
  margin: 0 auto;
  padding: 48px 32px 96px;
}
.masthead {
  border-bottom: 1px solid var(--line);
  padding-bottom: 20px;
  margin-bottom: 28px;
}
h1 {
  font-size: 26px;
  margin: 0 0 6px;
  font-weight: 600;
}
h2 {
  font-size: 16px;
  margin: 34px 0 12px;
  font-weight: 600;
  border-left: 3px solid var(--ink);
  padding-left: 10px;
}
h3 {
  font-size: 14px;
  margin: 24px 0 8px;
  font-weight: 600;
}
blockquote {
  margin: 0 0 20px;
  padding: 10px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink-faint);
  color: var(--ink-soft);
  font-size: 13px;
  border-radius: 0 6px 6px 0;
}
blockquote p {
  margin: 4px 0;
}
p {
  line-height: 1.7;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 8px 0 24px;
  background: var(--surface);
}
th {
  text-align: left;
  font-weight: 500;
  color: var(--ink-soft);
  font-size: 11px;
  letter-spacing: 0.03em;
  border-bottom: 2px solid var(--ink);
  padding: 8px;
  background: var(--bg);
}
td {
  padding: 8px;
  border-bottom: 1px solid var(--line);
}
tr:hover td {
  background: #FAFBF9;
}
strong {
  font-weight: 600;
}
hr {
  border: none;
  border-top: 1px solid var(--line);
  margin: 28px 0;
}
code {
  font-family: var(--mono);
  font-size: 12px;
  background: var(--bg);
  padding: 1px 5px;
  border-radius: 3px;
}

/* -- 卡片：Hero（由模板生成） -- */
.hero {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border: 1px solid var(--line);
  margin: 20px 0 28px;
  background: var(--surface);
  border-radius: 6px;
  overflow: hidden;
}
.hero-item {
  padding: 18px 20px;
  border-right: 1px solid var(--line);
}
.hero-item:last-child {
  border-right: none;
}
.hero-label {
  font-size: 12px;
  color: var(--ink-soft);
  margin-bottom: 6px;
}
.hero-value {
  font-family: var(--mono);
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}
.hero-value.pos {
  color: var(--positive);
}
.hero-value.neg {
  color: var(--negative);
}
.hero-unit {
  font-size: 11px;
  color: var(--ink-faint);
  margin-top: 4px;
}

/* -- 环图（由模板生成） -- */
.donut-wrap {
  display: flex;
  align-items: center;
  gap: 30px;
  border: 1px solid var(--line);
  padding: 22px 26px;
  margin: 0 0 28px;
  background: var(--surface);
  border-radius: 6px;
  flex-wrap: wrap;
}
.donut {
  width: 150px;
  height: 150px;
  border-radius: 50%;
  position: relative;
  flex-shrink: 0;
}
.donut-hole {
  position: absolute;
  inset: 24px;
  background: var(--surface);
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.donut-center .val {
  font-family: var(--mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.donut-center .lbl {
  font-size: 9px;
  color: var(--ink-soft);
  margin-top: 2px;
}
.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 13px;
  flex: 1;
  min-width: 170px;
}
.legend li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
}
.legend li:last-child {
  border-bottom: none;
}
.legend .dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.legend .amt {
  margin-left: auto;
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  color: var(--ink-soft);
}

/* -- 待确认行高亮 -- */
tr.pending td {
  background: var(--pending-bg);
}
tr.pending td:first-child {
  box-shadow: inset 3px 0 0 var(--pending);
}
tr.pending td:nth-child(7) {
  color: var(--pending);
  font-weight: 600;
}
h2.pending-sec {
  border-left-color: var(--pending);
  color: var(--pending);
}
.pending-banner {
  background: var(--pending-bg);
  border: 1px solid var(--pending);
  border-left: 4px solid var(--pending);
  padding: 10px 16px;
  font-size: 13px;
  color: var(--pending);
  border-radius: 0 6px 6px 0;
  margin: 0 0 24px;
  font-weight: 500;
}

/* -- 健康标签 -- */
.health-tag {
  font-size: 11px;
  font-family: var(--mono);
  padding: 1px 7px;
  border: 1px solid currentColor;
  border-radius: 2px;
  white-space: nowrap;
}
.health-tag.ok {
  color: var(--positive);
}
.health-tag.watch {
  color: var(--pending);
}
.health-tag.tight {
  color: var(--negative);
}
.health-tag.na {
  color: var(--ink-faint);
}

@media (max-width: 640px) {
  .hero {
    grid-template-columns: repeat(1, 1fr);
  }
  .hero-item {
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  table, th, td {
    font-size: 12px;
  }
  .page {
    padding: 28px 14px 56px;
  }
}
"""

PALETTE = ["#2F6F4F", "#97A19A", "#B8862F", "#5C6660", "#A83A32", "#8A9A5B", "#4A6FA5"]

# 模板文件名（去掉 .md.j2）→ 计算层 JSON 文件前缀
# 用于 --latest 自动选取最新计算结果
PREFIX_MAP = {
    "财务概览": "overview",
    "每周财务摘要": "weekly",
    "财务摘要": "weekly",
    "重大支出分析": "scenario",
    "支出分析": "scenario",
}


# ============================================================
# 渲染函数
# ============================================================
def render_report(json_path, template_path, output_base, extra_path=None):
    """
    核心渲染流程：
      1. 读取 JSON 数据（+ 可选附加 JSON）
      2. 用 Jinja2 渲染模板（.html/.md 直出，或 .md.j2 → MD + HTML）
      3. 输出文件
    """
    # ---- 1. 读取数据 ----
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    extra = {}
    if extra_path:
        if not os.path.exists(extra_path):
            sys.stderr.write(f"错误：找不到附加文件 {extra_path}\n")
            sys.exit(1)
        with open(extra_path, "r", encoding="utf-8") as f:
            extra = json.load(f)

    # ---- 2. 准备 Jinja2 环境 ----
    template_dir = os.path.dirname(template_path) or "."
    template_name = os.path.basename(template_path)
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # 注册自定义过滤器（供模板使用）
    env.filters["currency"] = lambda v: f"¥{v:,.2f}" if v is not None else "—"
    env.filters["pct"] = lambda v: f"{v:.1f}%" if v is not None else "—"

    try:
        template = env.get_template(template_name)
    except Exception as e:
        sys.stderr.write(f"错误：无法加载模板 {template_path}：{e}\n")
        sys.exit(1)

    # ---- 3. 渲染（Jinja2） ----
    # 注入调色板供模板生成图表；_data 用于访问含特殊字符(全角括号等)的 key；
    # _extra 为附加 JSON（--extra 指定，如全局账本的 global_ledger.json）
    data["_palette"] = PALETTE
    try:
        rendered = template.render(_data=data, _extra=extra, **data)
    except Exception as e:
        sys.stderr.write(f"错误：模板渲染失败：{e}\n")
        sys.exit(1)

    # ---- 4. 按模板类型输出 ----
    tname = template_name.lower()

    if tname.endswith(".html") or tname.endswith(".html.j2"):
        # HTML 直出：模板自带完整 CSS/结构（设计稿模式），直接输出 .html
        html_path = f"{output_base}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        sys.stderr.write(f"✅ 报告生成完成：\n")
        sys.stderr.write(f"   HTML: {html_path}\n")
        return

    if tname.endswith(".md") or tname.endswith(".md.j2"):
        # Markdown 直出：模板即 .md 内容，直接输出 .md
        md_path = f"{output_base}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        sys.stderr.write(f"✅ 报告生成完成：\n")
        sys.stderr.write(f"   MD  : {md_path}\n")
        return

    # ---- 5. 旧逻辑兼容：.md.j2 → 渲染 MD → markdown 转 HTML（内嵌 CSS 包装） ----
    md_path = f"{output_base}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    body = markdown.markdown(rendered, extensions=["tables", "fenced_code", "sane_lists"])
    title = os.path.basename(output_base)
    html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{title}</title>\n"
        f"  <style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f'<div class="page">\n{body}\n</div>\n'
        "</body>\n"
        "</html>"
    )
    html_path = f"{output_base}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    sys.stderr.write(f"✅ 报告生成完成：\n")
    sys.stderr.write(f"   MD  : {md_path}\n")
    sys.stderr.write(f"   HTML: {html_path}\n")


# ============================================================
# 命令行入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(
        description="呈现层：JSON 数据 + Jinja2 模板 → MD + HTML 报告"
    )
    ap.add_argument("--input", "-i",
                    help="计算层输出的 JSON 文件路径（与 --latest 二选一）")
    ap.add_argument("--latest", action="store_true",
                    help="自动从 calculation_results 目录中按修改时间选取最新的 JSON（与 --input 二选一）")
    ap.add_argument("--calc-dir", default=None,
                    help="--latest 模式下扫描的目录（默认 $BILLWEAVE_DATA_DIR/results/raw/calculation_results 或 ./data/...）")
    ap.add_argument("--extra", default=None,
                    help="附加 JSON 文件，以 _extra 变量注入模板（如全局账本模板需要同时读 summary.json 与 global_ledger.json）")
    ap.add_argument("--template", "-t", required=True,
                    help="Jinja2 模板文件路径（*.md.j2）")
    ap.add_argument("--output", "-o", required=True,
                    help="输出文件前缀（不含扩展名），如 reports/财务概览_20260831")
    args = ap.parse_args()

    # ---- 输入解析：--input 或 --latest 二选一 ----
    if args.latest and args.input:
        sys.stderr.write("错误：--input 与 --latest 不能同时使用\n")
        sys.exit(1)
    if not args.latest and not args.input:
        sys.stderr.write("错误：必须指定 --input 或 --latest\n")
        sys.exit(1)

    if args.latest:
        calc_dir = args.calc_dir or os.path.join(os.environ.get("BILLWEAVE_DATA_DIR") or "./data", "results", "raw", "calculation_results")
        template_name = os.path.basename(args.template)
        # 剥掉常见模板后缀(.html.j2/.html/.md.j2/.md/.j2)后查前缀映射
        for suffix in (".html.j2", ".md.j2", ".html", ".md", ".j2"):
            template_name = template_name.replace(suffix, "")
        prefix = PREFIX_MAP.get(template_name)
        if prefix is None:
            sys.stderr.write(f"错误：无法从模板名 '{template_name}' 推断 JSON 前缀，"
                             f"请在 {list(PREFIX_MAP.keys())} 中，或改用 --input 显式指定文件\n")
            sys.exit(1)
        files = glob.glob(os.path.join(calc_dir, f"{prefix}_*.json"))
        if not files:
            sys.stderr.write(f"错误：{calc_dir} 下找不到 {prefix}_*.json，请先运行对应计算层脚本\n")
            sys.exit(1)
        args.input = max(files, key=os.path.getmtime)
        sys.stderr.write(f"ℹ️  --latest 自动选取：{args.input}\n")

    # 检查输入文件是否存在
    if not os.path.exists(args.input):
        sys.stderr.write(f"错误：找不到输入文件 {args.input}\n")
        sys.exit(1)
    if not os.path.exists(args.template):
        sys.stderr.write(f"错误：找不到模板文件 {args.template}\n")
        sys.exit(1)

    # 确保输出目录存在
    out_dir = os.path.dirname(args.output)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    render_report(args.input, args.template, args.output, args.extra)


if __name__ == "__main__":
    main()
"""晨报 HTML 渲染器（轻量）。复用 terminal/html_report.py 的 CSS/markdown 引擎，
不复用 lenses/debate/oprms 抽取逻辑（与深度分析 markdown 强耦合，见 html_report.py:1181-1438）。"""
import html
from pathlib import Path
from typing import List, Dict
from terminal.html_report import CSS, md_to_html   # CSS:23-520, md_to_html:547-659

# 复用 CSS 有 overflow-x:hidden(html_report.py:56)，列多会截断 → 包一层可横向滚动容器
EXTRA_CSS = ".table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 0 16px;}"

def dicts_to_html_table(rows: List[Dict[str, str]], columns: List[str]) -> str:
    head = "".join("<th>{}</th>".format(html.escape(str(c))) for c in columns)
    body = "".join(
        "<tr>" + "".join("<td>{}</td>".format(html.escape(str(r.get(c, "")))) for c in columns) + "</tr>"
        for r in rows
    )
    return ('<div class="table-wrap"><table class="portfolio-table">'
            '<thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>').format(head, body)

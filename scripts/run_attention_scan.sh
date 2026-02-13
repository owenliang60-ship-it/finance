#!/bin/bash
# ============================================================
# 注意力雷达 — 每周扫描（一键运行）
#
# 用法：
#   ./scripts/run_attention_scan.sh          # 完整扫描
#   ./scripts/run_attention_scan.sh news     # 只跑新闻
#   ./scripts/run_attention_scan.sh gt       # 只跑 Google Trends
#   ./scripts/run_attention_scan.sh score    # 只算分+出报告
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="$PROJECT_ROOT/.venv/bin/python3"

cd "$PROJECT_ROOT"

# 确保关键词已初始化
echo ">>> 初始化关键词..."
"$PYTHON" scripts/scan_attention.py --seed-keywords

MODE="${1:-full}"

case "$MODE" in
  news)
    echo ">>> 扫描 Finnhub 新闻..."
    "$PYTHON" scripts/scan_attention.py --news-only
    echo ">>> 计算评分 + 生成报告..."
    "$PYTHON" scripts/scan_attention.py --score-only
    ;;
  gt)
    echo ">>> 扫描 Google Trends（约5分钟）..."
    "$PYTHON" scripts/scan_attention.py --gt-only
    echo ">>> 计算评分 + 生成报告..."
    "$PYTHON" scripts/scan_attention.py --score-only
    ;;
  score)
    echo ">>> 计算评分 + 生成报告..."
    "$PYTHON" scripts/scan_attention.py --score-only
    ;;
  full|*)
    echo ">>> 全量扫描：News → GT → 评分 → 报告"
    echo "    （Reddit 等 API 批复后自动启用）"
    "$PYTHON" scripts/scan_attention.py
    ;;
esac

# 打开最新报告
REPORT=$(ls -t "$PROJECT_ROOT/data/attention/report_"*.html 2>/dev/null | head -1)
if [ -n "$REPORT" ]; then
  echo ""
  echo ">>> 报告已生成：$REPORT"
  echo ">>> 正在打开..."
  open "$REPORT"
else
  echo ">>> 未找到报告文件"
fi

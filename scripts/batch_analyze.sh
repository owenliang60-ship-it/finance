#!/usr/bin/env bash
#
# 批量深度分析 — 每只股票启用独立 Claude Code session
#
# 用法:
#   ./scripts/batch_analyze.sh                    # 分析所有未分析的股票
#   ./scripts/batch_analyze.sh AAPL MSFT GOOG     # 只分析指定股票
#   ./scripts/batch_analyze.sh --parallel 3       # 3 路并行
#   ./scripts/batch_analyze.sh --depth standard   # 用 standard 深度 (更快更便宜)
#   ./scripts/batch_analyze.sh --model sonnet     # 用 sonnet 模型 (更快更便宜)
#   ./scripts/batch_analyze.sh --dry-run          # 只打印计划，不执行
#
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs/batch_analyze"
PRICE_DIR="$PROJECT_DIR/data/price"
COMPANIES_DIR="$PROJECT_DIR/data/companies"

# 默认参数
PARALLEL=1
DEPTH="full"
MODEL="opus"
DRY_RUN=false
MAX_BUDGET=25         # 每只股票最大美元预算 (opus full depth 中文长报告)
SPECIFIC_TICKERS=()

# 排除列表 (benchmark + 非普通股 + 已排除)
EXCLUDE="SPY QQQ BNH BNJ TBB GEGGL BRK-A CRM INTU NOW CAT DE HON PH UNP ADP"

# ── 参数解析 ──────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --parallel|-j)  PARALLEL="$2"; shift 2 ;;
        --depth)        DEPTH="$2"; shift 2 ;;
        --model)        MODEL="$2"; shift 2 ;;
        --dry-run)      DRY_RUN=true; shift ;;
        --budget)       MAX_BUDGET="$2"; shift 2 ;;
        --help|-h)
            head -8 "$0" | tail -7
            exit 0
            ;;
        *)  SPECIFIC_TICKERS+=("$1"); shift ;;
    esac
done

# ── 构建股票列表 ──────────────────────────────────────
if [[ ${#SPECIFIC_TICKERS[@]} -gt 0 ]]; then
    TICKERS=("${SPECIFIC_TICKERS[@]}")
else
    # 用 Python 从 SQLite 提取过滤后的股票池 (应用 sector/industry/perm 排除规则)
    VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
    POOL_OUTPUT=$("$VENV_PYTHON" -c "
import sqlite3, sys
sys.path.insert(0, '$PROJECT_DIR')
from config.settings import DATA_DIR, EXCLUDED_SECTORS, EXCLUDED_INDUSTRIES, PERMANENTLY_EXCLUDED
conn = sqlite3.connect(str(DATA_DIR / 'valuation.db'))
rows = conn.execute('SELECT symbol, sector, industry FROM companies ORDER BY symbol').fetchall()
conn.close()
pool = []
for sym, sector, industry in rows:
    if sym in PERMANENTLY_EXCLUDED or sym in ('SPY','QQQ'):
        continue
    if sector and sector in EXCLUDED_SECTORS:
        continue
    if industry and industry in EXCLUDED_INDUSTRIES:
        continue
    pool.append(sym)
print(' '.join(pool))
" 2>/dev/null)

    # 过滤掉已有分析的 (有 oprms.json 的)
    ALL_TICKERS=()
    for ticker in $POOL_OUTPUT; do
        if [[ -f "$COMPANIES_DIR/$ticker/oprms.json" ]]; then
            continue
        fi
        ALL_TICKERS+=("$ticker")
    done
    TICKERS=("${ALL_TICKERS[@]}")
fi

# ── 日志目录 ──────────────────────────────────────────
mkdir -p "$LOG_DIR"
BATCH_ID="$(date +%Y%m%d_%H%M%S)"
PROGRESS_FILE="$LOG_DIR/${BATCH_ID}_progress.log"

# ── 打印计划 ──────────────────────────────────────────
echo "═══════════════════════════════════════════════"
echo "  未来资本 · 批量深度分析"
echo "═══════════════════════════════════════════════"
echo "  日期:       $(date +%Y-%m-%d)"
echo "  Batch ID:   $BATCH_ID"
echo "  待分析:     ${#TICKERS[@]} 只股票"
echo "  深度:       $DEPTH"
echo "  模型:       $MODEL"
echo "  并行度:     $PARALLEL"
echo "  每只预算:   \$$MAX_BUDGET"
echo "  预估总费用: ~\$$(( ${#TICKERS[@]} * MAX_BUDGET )) (上限)"
echo "  日志目录:   $LOG_DIR"
echo "═══════════════════════════════════════════════"
echo ""
echo "股票列表:"
printf '  %s' "${TICKERS[@]}"
echo ""
echo ""

if $DRY_RUN; then
    echo "[DRY RUN] 以上为计划，未执行任何分析。"
    exit 0
fi

# ── 分析提示词模板 ──────────────────────────────────────
generate_prompt() {
    local ticker="$1"
    local depth="$2"
    cat <<'PROMPT_HEADER'
# ⚠️ 铁律：全部输出必须使用中文。所有分析、备忘录、存入数据库的内容——一律中文。英文仅用于专有名词（公司名、指标名）。

# ⚠️ 质量要求：
# - 每个 Lens 分析必须写 500-800 字，包含 3-4 个明确的子板块（如护城河/单元经济/管理层/结论）
# - Investment Memo 必须写 800+ 字，包含完整的 Executive Summary、Variant View、Investment Thesis、Kill Conditions、Action Plan
# - 辩论每轮 Bull 和 Bear 各 100-150 字，5 轮共 1000-1500 字
# - Alpha 每个维度（红队/周期/赌注）必须写 200-400 字
# - 不要偷懒用一句话概括整个 lens，那不是分析，那是标题

PROMPT_HEADER

    cat <<PROMPT
你是未来资本 AI 交易台的首席分析师，现在要对 ${ticker} 执行 ${depth} 深度分析。

你的分析报告将直接呈交给管理数百万美元投资组合的决策者，质量必须达到机构级研报水准。

严格按以下步骤执行，全部完成后退出：

## 步骤 1: 数据采集
用 Bash 工具执行 Python 代码来调用 analyze_ticker:
\`\`\`python
from terminal.commands import analyze_ticker
result = analyze_ticker('${ticker}', depth='${depth}', price_days=120)
\`\`\`
仔细阅读返回的 data（财务数据、技术指标、宏观环境）和 context_summary。

## 步骤 2: Stage 0 宏观简报（中文）
阅读 result['macro_briefing_prompt']，用中文写出完整的宏观叙事：
- 当前市场在交易什么叙事（1-2个）
- 对该股票所在行业的影响（TAILWIND/NEUTRAL/HEADWIND）
- 可操作的结论

## 步骤 3: 5 Lens 深度分析（中文，每个 500-800 字）
阅读 result['lens_prompts'] 中的 5 个提示词，用中文依次写出完整分析。

每个 Lens 必须包含：
- **核心评估**（该视角下的关键判断，带评级）
- **数据支撑**（引用具体财务数据、比率、增速）
- **风险/机会识别**（该视角下最大的 2-3 个风险或机会）
- **结论**（明确的买/卖/持有判断 + 一句话理由）

绝对禁止用一句话概括整个 Lens。如果你的 Lens 分析少于 300 字，说明你在偷懒。

## 步骤 4: 三大矛盾 + 5 轮辩论（中文）
从 5 个 Lens 中提炼 3 个核心矛盾（tensions），然后进行 5 轮 Bull vs Bear 辩论：
- 每轮围绕一个主题（估值、增长、竞争、宏观、终极问题）
- Bull 和 Bear 各 100-150 字，必须有具体论据，不能空泛
- 最终裁判给出明确结论

## 步骤 5: 投资备忘录（中文，800+ 字）
按 result['memo_skeleton'] 的完整格式写出 Investment Memo：
- Executive Summary（含 variant view、target IRR、action price、一段话 thesis）
- Variant View（市场共识 vs 我们的看法 vs 证据）
- Investment Thesis（3 个关键力量）
- Kill Conditions（3-5 个可观察的止损触发器）
- Action Plan（建仓/加仓/满仓/止损 的具体价位和仓位表格）

## 步骤 6: OPRMS 评级
给出 DNA (S/A/B/C) 和 Timing (S/A/B/C)，附上详细理由（各 50-100 字）。
计算最终仓位：Total × DNA_cap × Timing_coeff × Regime_mult。

## 步骤 7: Layer 2 Alpha 第二层思考（中文，每维度 200-400 字）
依次执行：
- **红队试炼**: 找出牛方论点中最脆弱的 3 个假设，每个给出致命性评分 (1-10) 和具体摧毁逻辑
- **周期钟摆**: 情绪/商业/技术三个周期各给出定位 (1-10 分) 和证据
- **非对称赌注**: 列出 3-4 个情景（含概率、目标价、回报率），计算加权期望回报，给出最优入场点

## 步骤 8: 持久化到 Company DB
用 Bash 工具执行 Python 代码，调用以下函数将分析结果存入 data/companies/${ticker}/:
- save_meta(): 公司基本信息
- save_oprms(): OPRMS 评级（包含 dna, dna_label, timing, timing_label, timing_coeff, position_pct, verdict, verdict_rationale, analysis_depth, evidence 等完整字段）
- save_kill_conditions(): Kill Conditions（每个含 description, metric, threshold, status）
- save_memo(): 完整的投资备忘录文本
- save_analysis(): 每个 Lens 的完整分析文本（5 次调用）
- save_debate(): 辩论摘要
- save_alpha_package(): Alpha 完整数据

⚠️ 所有存入的文本内容必须是中文。

所有步骤完成后，打印 "✅ ${ticker} 分析完成" 和结果摘要表格，然后退出。
PROMPT
}

# ── 单只股票分析函数 ──────────────────────────────────
analyze_one() {
    local ticker="$1"
    local log_file="$LOG_DIR/${BATCH_ID}_${ticker}.log"
    local start_time=$(date +%s)

    echo "[$(date +%H:%M:%S)] ▶ 开始分析 $ticker ..." | tee -a "$PROGRESS_FILE"

    local prompt
    prompt="$(generate_prompt "$ticker" "$DEPTH")"

    # 启动独立 Claude Code session
    if claude -p "$prompt" \
        --model "$MODEL" \
        --permission-mode bypassPermissions \
        --max-budget-usd "$MAX_BUDGET" \
        --no-session-persistence \
        > "$log_file" 2>&1; then
        local end_time=$(date +%s)
        local elapsed=$(( end_time - start_time ))
        echo "[$(date +%H:%M:%S)] ✅ $ticker 完成 (${elapsed}s)" | tee -a "$PROGRESS_FILE"
    else
        local end_time=$(date +%s)
        local elapsed=$(( end_time - start_time ))
        echo "[$(date +%H:%M:%S)] ❌ $ticker 失败 (${elapsed}s) — 查看 $log_file" | tee -a "$PROGRESS_FILE"
    fi
}

export -f analyze_one generate_prompt
export LOG_DIR BATCH_ID PROGRESS_FILE DEPTH MODEL MAX_BUDGET PROJECT_DIR

# ── 执行 ──────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] 批量分析开始 (${#TICKERS[@]} 只股票, 并行度 $PARALLEL)" | tee "$PROGRESS_FILE"
echo "" | tee -a "$PROGRESS_FILE"

if [[ $PARALLEL -eq 1 ]]; then
    # 串行执行
    for ticker in "${TICKERS[@]}"; do
        analyze_one "$ticker"
    done
else
    # 并行执行 (需要 GNU parallel 或 xargs -P)
    if command -v parallel &>/dev/null; then
        printf '%s\n' "${TICKERS[@]}" | parallel -j "$PARALLEL" analyze_one
    else
        printf '%s\n' "${TICKERS[@]}" | xargs -P "$PARALLEL" -I{} bash -c 'analyze_one "{}"'
    fi
fi

echo ""
echo "═══════════════════════════════════════════════"
echo "  批量分析完成"
echo "  进度日志: $PROGRESS_FILE"
echo "  详细日志: $LOG_DIR/${BATCH_ID}_*.log"
echo "═══════════════════════════════════════════════"

# ── 统计 ──────────────────────────────────────────────
DONE=$(grep -c "✅" "$PROGRESS_FILE" 2>/dev/null || echo 0)
FAIL=$(grep -c "❌" "$PROGRESS_FILE" 2>/dev/null || echo 0)
echo "  成功: $DONE / 失败: $FAIL / 总计: ${#TICKERS[@]}"

#!/usr/bin/env bash
#
# 未来资本 · Auto Deep Analysis — Shell-Orchestrated Multi-Agent Pipeline
#
# 用法:
#   ./scripts/auto_deep_analyze.sh AAPL MSFT GOOG      # 分析 3 只股票
#   ./scripts/auto_deep_analyze.sh --dry-run AAPL       # 只预览计划
#   ./scripts/auto_deep_analyze.sh --budget 25 TSLA     # 每只预算 $25
#   ./scripts/auto_deep_analyze.sh --skip-heptabase AAPL # 跳过 Heptabase 同步
#   ./scripts/auto_deep_analyze.sh --skip-db AAPL       # 跳过 DB 存储
#
# 每只股票 ~25-30 分钟，14 个独立 claude -p 调用
# 使用 deep_analyze_ticker() 预生成的同一套 prompt 文件，质量与手动一致
#
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs/batch_analyze"
BATCH_ID="$(date +%Y%m%d_%H%M%S)"

# Worktree safety: .venv might not exist in worktree, resolve to main repo
if [[ -f "$PROJECT_DIR/.venv/bin/python3" ]]; then
    VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
else
    # Fallback: find main repo via git worktree list
    MAIN_REPO=$(cd "$PROJECT_DIR" && git worktree list --porcelain | head -1 | sed 's/^worktree //')
    if [[ -f "$MAIN_REPO/.venv/bin/python3" ]]; then
        VENV_PYTHON="$MAIN_REPO/.venv/bin/python3"
        PROJECT_DIR="$MAIN_REPO"
    else
        echo "❌ 找不到 Python venv (.venv/bin/python3)"
        exit 1
    fi
fi

# 默认参数
DRY_RUN=false
SKIP_HEPTABASE=false
SKIP_DB=false
BUDGET_PER_TICKER=31
TICKERS=()

# Claude -p 通用 flags
COMMON_FLAGS="--permission-mode bypassPermissions --no-session-persistence"

# ── 参数解析 ──────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)        DRY_RUN=true; shift ;;
        --budget)
            if [[ $# -lt 2 ]]; then echo "❌ --budget requires a value"; exit 1; fi
            BUDGET_PER_TICKER="$2"; shift 2 ;;
        --skip-heptabase) SKIP_HEPTABASE=true; shift ;;
        --skip-db)        SKIP_DB=true; shift ;;
        --help|-h)
            head -10 "$0" | tail -9
            exit 0
            ;;
        *)  TICKERS+=("$1"); shift ;;
    esac
done

if [[ ${#TICKERS[@]} -eq 0 ]]; then
    echo "❌ 请指定至少一个股票代码"
    echo "用法: ./scripts/auto_deep_analyze.sh AAPL MSFT GOOG"
    exit 1
fi

# ── 日志目录 ──────────────────────────────────────────
mkdir -p "$LOG_DIR"
PROGRESS_LOG="$LOG_DIR/${BATCH_ID}_progress.log"

# ── 日志函数 ──────────────────────────────────────────
log() {
    local msg="[$(date +%H:%M:%S)] $1"
    echo "$msg" | tee -a "$PROGRESS_LOG"
}

log_ticker() {
    local ticker="$1" msg="$2"
    local ticker_log="$LOG_DIR/${BATCH_ID}_${ticker}.log"
    echo "[$(date +%H:%M:%S)] $msg" | tee -a "$PROGRESS_LOG" >> "$ticker_log"
}

# ── 打印计划 ──────────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
echo "  未来资本 · Auto Deep Analysis (v3 Multi-Agent)"
echo "═══════════════════════════════════════════════════════"
echo "  日期:       $(date +%Y-%m-%d)"
echo "  Batch ID:   $BATCH_ID"
echo "  待分析:     ${#TICKERS[@]} 只股票: ${TICKERS[*]}"
echo "  每只预算:   \$$BUDGET_PER_TICKER"
echo "  预估总费用: ~\$$(( ${#TICKERS[@]} * BUDGET_PER_TICKER )) (上限)"
echo "  Heptabase:  $(if $SKIP_HEPTABASE; then echo "跳过"; else echo "同步"; fi)"
echo "  DB Save:    $(if $SKIP_DB; then echo "跳过"; else echo "存储"; fi)"
echo "  日志目录:   $LOG_DIR"
echo ""
echo "  Pipeline: Phase 0a (setup) → Phase 0b (research+profiler) → Phase 1 (5 lens) →"
echo "            Phase 2 (synthesis) → Phase 3 (alpha) → Phase 4 (compile+save)"
echo "═══════════════════════════════════════════════════════"
echo ""

if $DRY_RUN; then
    echo "[DRY RUN] 以上为计划，未执行任何分析。"
    echo ""
    echo "每只股票将执行 14 个 claude -p 调用:"
    echo "  Phase 0b: 4× research agents + 1× profiler (parallel, sonnet, \$1/ea)"
    echo "  Phase 1:  5× lens agents (parallel, opus, \$3/ea)"
    echo "  Phase 2:  1× synthesis agent (opus, \$5)"
    echo "  Phase 3:  1× alpha agent (opus, \$5)"
    echo "  Phase 4:  1-2× save agents (haiku, \$1/ea)"
    exit 0
fi

# ── 工具函数 ──────────────────────────────────────────

# 运行单个 claude -p agent，带日志
run_agent() {
    local name="$1"
    local model="$2"
    local budget="$3"
    local prompt="$4"
    local log_file="$5"

    claude -p "$prompt" \
        --model "$model" \
        $COMMON_FLAGS \
        --max-budget-usd "$budget" \
        > "$log_file" 2>&1
    return $?
}

# 运行 claude -p agent，prompt 从文件读取
run_agent_from_file() {
    local name="$1"
    local model="$2"
    local budget="$3"
    local prompt_file="$4"
    local log_file="$5"

    local prompt
    prompt="阅读 ${prompt_file} 中的完整指令并执行。所有输出必须使用中文。"

    claude -p "$prompt" \
        --model "$model" \
        $COMMON_FLAGS \
        --max-budget-usd "$budget" \
        > "$log_file" 2>&1
    return $?
}

# 检查文件是否存在且字数达标
check_file_quality() {
    local file="$1"
    local min_chars="${2:-1500}"

    if [[ ! -f "$file" ]]; then
        return 1
    fi

    local chars
    chars=$(wc -m < "$file" 2>/dev/null || echo 0)
    chars=$(echo "$chars" | tr -d ' ')

    if [[ "$chars" -lt "$min_chars" ]]; then
        return 1
    fi
    return 0
}

# 等待多个文件出现（带超时）
wait_for_files() {
    local timeout_secs="$1"
    shift
    local files=("$@")
    local start_time=$(date +%s)

    while true; do
        local all_exist=true
        for f in "${files[@]}"; do
            if [[ ! -f "$f" ]]; then
                all_exist=false
                break
            fi
        done

        if $all_exist; then
            return 0
        fi

        local elapsed=$(( $(date +%s) - start_time ))
        if [[ "$elapsed" -ge "$timeout_secs" ]]; then
            return 1
        fi

        sleep 10
    done
}

# ── 分析单只股票 ──────────────────────────────────────

analyze_ticker() {
    local ticker="$1"
    local ticker_start=$(date +%s)
    local ticker_log="$LOG_DIR/${BATCH_ID}_${ticker}.log"

    log "▶ ════ $ticker 开始 ════"

    # ─── Phase 0a: Python Setup ───────────────────────
    log "  [$ticker] Phase 0a: 数据采集 + prompt 生成..."

    local setup_json
    setup_json=$("$VENV_PYTHON" -c "
import json, sys
sys.path.insert(0, '$PROJECT_DIR')
from terminal.commands import deep_analyze_ticker
setup = deep_analyze_ticker('$ticker')
batch = {
    'symbol': setup['symbol'],
    'research_dir': setup['research_dir'],
    'research_queries': setup['research_queries'],
    'profiler_prompt_path': setup.get('profiler_prompt_path', ''),
    'lens_prompt_paths': setup['lens_prompt_paths'],
    'gemini_prompt_path': setup['gemini_prompt_path'],
    'synthesis_prompt_path': setup['synthesis_prompt_path'],
    'alpha_prompt_path': setup['alpha_prompt_path'],
    'latest_price': setup.get('data', {}).get('latest_price'),
}
print(json.dumps(batch))
" 2>>"$ticker_log")

    if [[ -z "$setup_json" ]]; then
        log "  [$ticker] ❌ Phase 0a 失败 — 数据采集错误"
        return 1
    fi

    # Parse JSON fields
    local rd symbol
    rd=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['research_dir'])")
    symbol=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['symbol'])")
    local latest_price
    latest_price=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d.get('latest_price','N/A'))")

    log "  [$ticker] Phase 0a 完成 — price: \$$latest_price, research_dir: $(basename "$rd")"

    # Parse lens prompt paths
    local lens_names=() lens_prompt_files=() lens_output_files=()
    local n_lenses
    n_lenses=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(len(d['lens_prompt_paths']))")

    for i in $(seq 0 $((n_lenses - 1))); do
        local lname lpath loutput
        lname=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['lens_prompt_paths'][$i]['lens_name'])")
        lpath=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['lens_prompt_paths'][$i]['prompt_path'])")
        loutput=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['lens_prompt_paths'][$i]['output_path'])")
        lens_names+=("$lname")
        lens_prompt_files+=("$lpath")
        lens_output_files+=("$loutput")
    done

    local profiler_prompt_path gemini_prompt_path synthesis_prompt_path alpha_prompt_path
    profiler_prompt_path=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d.get('profiler_prompt_path',''))")
    gemini_prompt_path=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['gemini_prompt_path'])")
    synthesis_prompt_path=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['synthesis_prompt_path'])")
    alpha_prompt_path=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['alpha_prompt_path'])")

    # Parse research queries
    local q_earnings q_competitive q_street
    q_earnings=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['research_queries']['earnings'])")
    q_competitive=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['research_queries']['competitive'])")
    q_street=$(echo "$setup_json" | "$VENV_PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['research_queries']['street'])")

    # ─── Phase 0b: Research + Profiler Agents (parallel) ─────────
    log "  [$ticker] Phase 0b: 4 research agents + 1 profiler 启动 (parallel)..."

    # Earnings agent
    run_agent "research-earnings" "sonnet" 1 \
        "Use WebSearch to research: ${q_earnings}. Write a 500-800 word structured summary in 中文 covering: revenue/EPS/guidance trends, management key quotes, Q&A insights, forward guidance. Write the complete summary to: ${rd}/earnings.md" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_earnings.log" &
    local pid_earnings=$!

    # Competitive agent
    run_agent "research-competitive" "sonnet" 1 \
        "Use WebSearch to research: ${q_competitive}. Write a 500-800 word structured comparison in 中文 covering: market share, competitive moves, metrics table, moat assessment. Write the complete summary to: ${rd}/competitive.md" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_competitive.log" &
    local pid_competitive=$!

    # Street agent
    run_agent "research-street" "sonnet" 1 \
        "Use WebSearch to research: ${q_street}. Write a 500-800 word structured summary in 中文 covering: analyst ratings distribution, PT range, bull/bear top 3 arguments, recent changes. Write the complete summary to: ${rd}/street.md" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_street.log" &
    local pid_street=$!

    # Gemini contrarian agent
    run_agent "gemini-contrarian" "sonnet" 1 \
        "Read the file ${gemini_prompt_path}. Call mcp__dual-llm__gemini_think with the file content as the question parameter, system_prompt='You are a contrarian short-seller. Find the weakest points. Be specific with numbers and historical analogs.', model='gemini-2.5-flash'. Write the complete Gemini response to: ${rd}/gemini_contrarian.md" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_gemini.log" &
    local pid_gemini=$!

    # Company Profiler agent (parallel with research agents)
    local pid_profiler=""
    if [[ -n "$profiler_prompt_path" ]] && [[ -f "$profiler_prompt_path" ]]; then
        run_agent_from_file "profiler" "sonnet" 1 \
            "$profiler_prompt_path" \
            "$LOG_DIR/${BATCH_ID}_${ticker}_profiler.log" &
        pid_profiler=$!
    fi

    # Wait for research + profiler agents (timeout 8 min)
    local research_timeout=480
    local research_start=$(date +%s)
    local research_done=false

    # Build PID list (profiler may be empty)
    local all_phase0b_pids="$pid_earnings $pid_competitive $pid_street $pid_gemini"
    if [[ -n "$pid_profiler" ]]; then
        all_phase0b_pids="$all_phase0b_pids $pid_profiler"
    fi

    while true; do
        local all_done=true
        for pid in $all_phase0b_pids; do
            if kill -0 "$pid" 2>/dev/null; then
                all_done=false
                break
            fi
        done

        if $all_done; then
            research_done=true
            break
        fi

        local elapsed=$(( $(date +%s) - research_start ))
        if [[ "$elapsed" -ge "$research_timeout" ]]; then
            log "  [$ticker] ⚠️ Research timeout (${research_timeout}s) — killing remaining agents"
            for pid in $all_phase0b_pids; do
                kill "$pid" 2>/dev/null || true
            done
            break
        fi
        sleep 5
    done

    # Check which research files exist
    local research_files=("earnings.md" "competitive.md" "street.md" "gemini_contrarian.md")
    local research_ok=0
    for rf in "${research_files[@]}"; do
        if [[ -f "$rd/$rf" ]]; then
            research_ok=$((research_ok + 1))
        fi
    done

    # Check profiler output
    local profiler_ok="N/A"
    if [[ -n "$pid_profiler" ]]; then
        if check_file_quality "$rd/company_profile.md" 800; then
            profiler_ok="✓"
        else
            profiler_ok="✗"
            log "  [$ticker] ⚠️ company_profile.md 缺失或过短 (<800 chars)"
        fi
    fi

    log "  [$ticker] Phase 0b 完成 — $research_ok/4 research files, profiler: $profiler_ok"

    # ─── Phase 1: Five Lens Analysis (parallel) ───────
    log "  [$ticker] Phase 1: 5 lens agents 启动 (parallel)..."

    local lens_pids=()
    for i in $(seq 0 $((n_lenses - 1))); do
        local slug
        slug=$(echo "${lens_names[$i]}" | tr '[:upper:]/ ' '[:lower:]__' | sed 's/[^a-z0-9_]//g')

        run_agent_from_file "lens-${slug}" "opus" 3 \
            "${lens_prompt_files[$i]}" \
            "$LOG_DIR/${BATCH_ID}_${ticker}_lens_${slug}.log" &
        lens_pids+=($!)
    done

    # Wait for all lens agents (timeout 10 min)
    local lens_timeout=600
    local lens_start=$(date +%s)

    while true; do
        local all_done=true
        for pid in "${lens_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                all_done=false
                break
            fi
        done

        if $all_done; then
            break
        fi

        local elapsed=$(( $(date +%s) - lens_start ))
        if [[ "$elapsed" -ge "$lens_timeout" ]]; then
            log "  [$ticker] ⚠️ Lens timeout (${lens_timeout}s) — killing remaining agents"
            for pid in "${lens_pids[@]}"; do
                kill "$pid" 2>/dev/null || true
            done
            break
        fi
        sleep 10
    done

    # Quality gate: check lens file quality
    local lens_count=0
    local retry_needed=()
    for i in $(seq 0 $((n_lenses - 1))); do
        local output_file="${lens_output_files[$i]}"
        if check_file_quality "$output_file" 1500; then
            lens_count=$((lens_count + 1))
        else
            retry_needed+=("$i")
        fi
    done

    log "  [$ticker] Phase 1 初检 — $lens_count/5 lens 达标"

    # Retry underperforming lenses (once each)
    if [[ ${#retry_needed[@]} -gt 0 ]]; then
        log "  [$ticker] Phase 1 重试 ${#retry_needed[@]} 个 lens..."
        local retry_pids=()
        for i in "${retry_needed[@]}"; do
            local slug
            slug=$(echo "${lens_names[$i]}" | tr '[:upper:]/ ' '[:lower:]__' | sed 's/[^a-z0-9_]//g')

            local retry_prompt="你的上一次输出太短（不到 500 字），请认真执行完整分析。阅读 ${lens_prompt_files[$i]} 中的完整指令并执行。所有输出必须使用中文，不少于 500 字。"

            run_agent "lens-${slug}-retry" "opus" 3 \
                "$retry_prompt" \
                "$LOG_DIR/${BATCH_ID}_${ticker}_lens_${slug}_retry.log" &
            retry_pids+=($!)
        done

        # Wait for retries (timeout 8 min)
        local retry_start=$(date +%s)
        while true; do
            local all_done=true
            for pid in "${retry_pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    all_done=false
                    break
                fi
            done
            if $all_done; then break; fi
            local elapsed=$(( $(date +%s) - retry_start ))
            if [[ "$elapsed" -ge 480 ]]; then
                for pid in "${retry_pids[@]}"; do kill "$pid" 2>/dev/null || true; done
                break
            fi
            sleep 10
        done

        # Recheck
        lens_count=0
        for i in $(seq 0 $((n_lenses - 1))); do
            if [[ -f "${lens_output_files[$i]}" ]]; then
                lens_count=$((lens_count + 1))
            fi
        done
    fi

    log "  [$ticker] Phase 1 完成 — $lens_count/5 lens files"

    if [[ "$lens_count" -lt 3 ]]; then
        log "  [$ticker] ❌ 少于 3 个 lens 完成，跳过该 ticker"
        return 1
    fi

    # ─── Phase 2: Synthesis ───────────────────────────
    log "  [$ticker] Phase 2: Synthesis agent 启动..."

    run_agent_from_file "synthesis" "opus" 5 \
        "$synthesis_prompt_path" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_synthesis.log"

    # Check synthesis outputs
    local synthesis_ok=true
    for sf in "debate.md" "memo.md" "oprms.md"; do
        if [[ ! -f "$rd/$sf" ]]; then
            synthesis_ok=false
            log "  [$ticker] ⚠️ 缺失 $sf"
        fi
    done

    if ! $synthesis_ok; then
        log "  [$ticker] ❌ Synthesis 不完整，停止该 ticker"
        return 1
    fi

    log "  [$ticker] Phase 2 完成 — debate + memo + oprms ✓"

    # ─── Phase 3: Alpha ───────────────────────────────
    log "  [$ticker] Phase 3: Alpha agent 启动..."

    run_agent_from_file "alpha" "opus" 5 \
        "$alpha_prompt_path" \
        "$LOG_DIR/${BATCH_ID}_${ticker}_alpha.log"

    # Check alpha outputs
    local alpha_ok=true
    for af in "alpha_red_team.md" "alpha_cycle.md" "alpha_bet.md"; do
        if [[ ! -f "$rd/$af" ]]; then
            alpha_ok=false
            log "  [$ticker] ⚠️ 缺失 $af"
        fi
    done

    if ! $alpha_ok; then
        log "  [$ticker] ⚠️ Alpha 不完整，继续编译..."
    else
        log "  [$ticker] Phase 3 完成 — red_team + cycle + bet ✓"
    fi

    # ─── Phase 4a: Compile Report ─────────────────────
    log "  [$ticker] Phase 4a: 编译报告..."

    local report_path
    report_path=$("$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from terminal.deep_pipeline import compile_deep_report
from pathlib import Path
path = compile_deep_report('$ticker', Path('$rd'))
print(path)
" 2>>"$ticker_log")

    if [[ -n "$report_path" ]] && [[ -f "$report_path" ]]; then
        local report_chars
        report_chars=$(wc -m < "$report_path" | tr -d ' ')
        log "  [$ticker] Phase 4a 完成 — report: ${report_chars} chars"
    else
        log "  [$ticker] ⚠️ 报告编译失败"
    fi

    # ─── Phase 4b: Save to Company DB ─────────────────
    if ! $SKIP_DB; then
        log "  [$ticker] Phase 4b: DB Save agent 启动..."

        local db_save_prompt
        db_save_prompt="你是一个数据持久化助手。请执行以下步骤将 ${ticker} 的分析结果存入 Company DB。

使用 Bash 工具执行 Python 代码，工作目录在 ${PROJECT_DIR}：

\`\`\`python
import sys
sys.path.insert(0, '${PROJECT_DIR}')
from pathlib import Path
from terminal.company_db import save_oprms, save_memo, save_analysis, save_kill_conditions, save_alpha_package, save_meta

rd = Path('${rd}')

# 1. Save memo
memo_path = rd / 'memo.md'
if memo_path.exists():
    save_memo('${ticker}', memo_path.read_text(encoding='utf-8'), 'investment')
    print('✓ memo saved')

# 2. Save each lens analysis
import glob
for lens_file in sorted(rd.glob('lens_*.md')):
    lens_name = lens_file.stem.replace('lens_', '')
    save_analysis('${ticker}', lens_name, lens_file.read_text(encoding='utf-8'))
    print(f'✓ lens {lens_name} saved')

# 3. Parse and save OPRMS
oprms_path = rd / 'oprms.md'
if oprms_path.exists():
    text = oprms_path.read_text(encoding='utf-8')
    import re
    # Extract DNA
    dna_match = re.search(r'资产基因.*?:\s*\*?\*?([SABC])\b', text)
    dna = dna_match.group(1) if dna_match else 'B'
    # Extract Timing
    timing_match = re.search(r'时机系数.*?:\s*\*?\*?([SABC])\b', text)
    timing = timing_match.group(1) if timing_match else 'B'
    # Extract timing coefficient
    coeff_match = re.search(r'系数[：:]\s*(\d+\.?\d*)', text)
    timing_coeff = float(coeff_match.group(1)) if coeff_match else 0.5
    # Extract evidence lines
    evidence = []
    in_evidence = False
    for line in text.split('\n'):
        if '证据' in line and ('清单' in line or '列表' in line):
            in_evidence = True
            continue
        if in_evidence:
            if line.strip().startswith(('1.','2.','3.','4.','5.','6.','7.','8.','- ')):
                evidence.append(line.strip().lstrip('0123456789.-) '))
            elif line.startswith('#') or (line.strip() == '' and len(evidence) > 0):
                in_evidence = False
    # Extract investment bucket
    bucket_match = re.search(r'投资桶[：:]\s*\*?\*?(.*?)(?:\*?\*?\s*$)', text, re.MULTILINE)
    bucket = bucket_match.group(1).strip() if bucket_match else 'Watch'

    dna_caps = {'S': 25, 'A': 15, 'B': 7, 'C': 2}
    position_pct = round(dna_caps.get(dna, 7) * timing_coeff, 1)

    oprms_dict = {
        'dna': dna,
        'timing': timing,
        'timing_coeff': timing_coeff,
        'evidence': evidence,
        'investment_bucket': bucket,
        'position_pct': position_pct,
        'analysis_depth': 'deep',
    }
    save_oprms('${ticker}', oprms_dict)
    print(f'✓ OPRMS saved: DNA={dna} Timing={timing} coeff={timing_coeff} pos={position_pct}%')

# 4. Save alpha package
alpha_data = {}
for alpha_name in ['red_team', 'cycle', 'bet']:
    alpha_file = rd / f'alpha_{alpha_name}.md'
    if alpha_file.exists():
        alpha_data[alpha_name] = alpha_file.read_text(encoding='utf-8')
if alpha_data:
    save_alpha_package('${ticker}', alpha_data)
    print(f'✓ alpha package saved ({len(alpha_data)} components)')

# 5. Parse and save kill conditions from memo
memo_text = memo_path.read_text(encoding='utf-8') if memo_path.exists() else ''
conditions = []
in_kill = False
for line in memo_text.split('\n'):
    if 'Kill Condition' in line or '触杀条件' in line or '止损触发' in line:
        in_kill = True
        continue
    if in_kill:
        if line.strip().startswith(('1.','2.','3.','4.','5.','- ','* ')):
            desc = line.strip().lstrip('0123456789.-)*  ')
            if desc:
                conditions.append({'description': desc, 'status': 'active'})
        elif line.startswith('#') and len(conditions) > 0:
            in_kill = False
if conditions:
    save_kill_conditions('${ticker}', conditions)
    print(f'✓ kill conditions saved ({len(conditions)})')

print('\\n✅ DB save complete for ${ticker}')
\`\`\`

执行上面的代码，不要修改任何内容，直接复制粘贴执行。"

        if run_agent "db-save" "haiku" 1 \
            "$db_save_prompt" \
            "$LOG_DIR/${BATCH_ID}_${ticker}_db_save.log"; then
            log "  [$ticker] Phase 4b 完成 — DB save ✓"
        else
            log "  [$ticker] ⚠️ Phase 4b DB save 可能失败，查看日志"
        fi
    fi

    # ─── Phase 4c: Heptabase Sync ─────────────────────
    if ! $SKIP_HEPTABASE; then
        log "  [$ticker] Phase 4c: Heptabase sync agent 启动..."

        local summary_path="$rd/report_summary.md"

        if [[ -f "$summary_path" ]]; then
            local hb_prompt="你是一个 Heptabase 同步助手。请执行以下操作：

1. 读取文件 ${summary_path}
2. 调用 mcp__heptabase__save_to_note_card，将文件内容作为 card content（第一行 H1 会成为卡片标题）
3. 调用 mcp__heptabase__append_to_journal，内容为：
   ## ${ticker} 深度分析完成
   - 日期：$(date +%Y-%m-%d)
   - 类型：Auto Deep Analysis (batch)
   - 完整报告：${report_path}

如果 MCP 调用失败，重试一次。仍失败则打印错误信息。"

            if run_agent "heptabase" "haiku" 1 \
                "$hb_prompt" \
                "$LOG_DIR/${BATCH_ID}_${ticker}_heptabase.log"; then
                log "  [$ticker] Phase 4c 完成 — Heptabase sync ✓"
            else
                log "  [$ticker] ⚠️ Phase 4c Heptabase sync 可能失败，查看日志"
            fi
        else
            log "  [$ticker] ⚠️ report_summary.md 不存在，跳过 Heptabase"
        fi
    fi

    # ─── Summary ──────────────────────────────────────
    local ticker_end=$(date +%s)
    local ticker_elapsed=$(( ticker_end - ticker_start ))
    local ticker_mins=$(( ticker_elapsed / 60 ))
    local ticker_secs=$(( ticker_elapsed % 60 ))

    # Extract OPRMS from file for summary
    local oprms_summary="N/A"
    if [[ -f "$rd/oprms.md" ]]; then
        oprms_summary=$(head -20 "$rd/oprms.md" | grep -E '(DNA|Timing|仓位)' | head -3 | tr '\n' ' ')
    fi

    log "  [$ticker] ✅ 完成 — ${ticker_mins}m${ticker_secs}s"
    log "  [$ticker] OPRMS: $oprms_summary"
    log "  [$ticker] Report: $report_path"
    log ""

    return 0
}

# ── 主循环（串行） ────────────────────────────────────
BATCH_START=$(date +%s)
DONE=0
FAIL=0

log "批量分析开始 (${#TICKERS[@]} 只股票, 串行执行)"
log ""

for ticker in "${TICKERS[@]}"; do
    if analyze_ticker "$ticker"; then
        DONE=$((DONE + 1))
    else
        FAIL=$((FAIL + 1))
    fi
done

BATCH_END=$(date +%s)
BATCH_ELAPSED=$(( BATCH_END - BATCH_START ))
BATCH_MINS=$(( BATCH_ELAPSED / 60 ))

# ── 最终统计 ──────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  批量分析完成"
echo "═══════════════════════════════════════════════════════"
echo "  成功: $DONE / 失败: $FAIL / 总计: ${#TICKERS[@]}"
echo "  总耗时: ${BATCH_MINS} 分钟"
echo "  进度日志: $PROGRESS_LOG"
echo "  详细日志: $LOG_DIR/${BATCH_ID}_*.log"
echo "═══════════════════════════════════════════════════════"

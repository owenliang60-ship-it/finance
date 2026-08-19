#!/bin/bash
# 静态引用检查：扫描遗留 Core 股票池入口（pool_manager.get_symbols /
# UNIVERSE_FILE / universe.json）在功能代码中的残留引用。
#
# 背景（R11 两阶段软退役，Stop G）：退役门槛两条都要满足——
#   1) data/pool/legacy_calls.log 连续 4 周零新增（运行期埋点，见 pool_manager.py）
#   2) 本脚本 exit 0（静态兜底，覆盖运行期埋点抓不到的 dormant 路径）
#
# 扫描范围：src/ scripts/ terminal/ backtest/（仅 .py / .sh，天然排除 .md 等文档）
# 排除：pool_manager.py 自身（定义处）、本脚本自身、archive/ 路径下的归档代码、
#      纯注释行（# 开头）、docstring 边界行（"""/''' 开头）。
#
# 匹配用 \<...\> 单词边界（GNU/BSD grep 均支持，非 GNU-only），
# 避免子串误命中一批本项目引入、打算长期保留、与遗留 Core 池无关的新构造：
#   target_symbols / get_symbols_with_market_cap_at（含 "get_symbols" 子串但不是它）
#   BROAD_UNIVERSE_FILE / EXTENDED_UNIVERSE_FILE（含 "UNIVERSE_FILE" 子串但不是它）
#   extended_universe.json / broad_universe.json（含 "universe.json" 子串但不是它）
# 单词边界仍无法区分「同名不同 owner」的方法（如 MarketStore.get_symbols(table)），
# 故额外用 .get_symbols( / def get_symbols(self 显式排除该方法。
#
# 用法: bash scripts/check_core_references.sh
# 零引用 → exit 0；有引用 → 打印清单 + exit 1

set -u

PATTERN='\<get_symbols\>|\<UNIVERSE_FILE\>|\<universe\.json'
SCAN_DIRS="src scripts terminal backtest"
SELF_PATH="src/data/pool_manager.py"
CHECKER_PATH="scripts/check_core_references.sh"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# NOTE: the per-line filtering logic lives in a function (not inlined in the
# while-loop below) because macOS's stock bash 3.2 fails to parse a bare
# `case ... esac` when it appears textually inside a `$(...)` command
# substitution (a known old-bash parser limitation) — calling a function
# defined outside the substitution sidesteps it.
_is_noise_line() {
    # returns 0 (true) if the line should be excluded
    file="$1"
    content="$2"

    case "$file" in
        "$SELF_PATH"|"$CHECKER_PATH") return 0 ;;
        */archive/*) return 0 ;;
    esac

    stripped=$(printf '%s' "$content" | sed -e 's/^[[:space:]]*//')
    case "$stripped" in
        '#'*|'"""'*|"'''"*) return 0 ;;
    esac

    # 同名不同 owner 的方法（见文件头注释），单词边界无法区分，显式排除
    case "$content" in
        *.get_symbols\(*|*"def get_symbols(self"*) return 0 ;;
    esac

    return 1
}

matches=$(grep -rEn --include='*.py' --include='*.sh' "$PATTERN" $SCAN_DIRS 2>/dev/null | \
    while IFS=: read -r file line content; do
        if _is_noise_line "$file" "$content"; then
            continue
        fi
        printf '%s:%s:%s\n' "$file" "$line" "$content"
    done)

if [ -z "$matches" ]; then
    echo "check_core_references: 0 functional references found. Stop G static-check condition met."
    exit 0
fi

count=$(printf '%s\n' "$matches" | wc -l | tr -d ' ')
echo "check_core_references: $count functional reference(s) to legacy Core pool entry point found:"
printf '%s\n' "$matches"
exit 1

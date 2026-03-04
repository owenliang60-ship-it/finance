#!/bin/bash
# Finance 工作区云端同步 (P3: 所有权模型)
#
# 所有权规则:
#   company.db    — 本地独占写入，push 到云端
#   market.db     — 云端独占写入，pull 到本地
#   universe.json — 双端各有新增，merge 取并集
#   fundamental/  — 云端生成，pull 到本地
#
# 用法: ./sync_to_cloud.sh [--pull|--push|--sync|--status]

set -e

LOCAL_DIR="/Users/owen/CC workspace/Finance"
REMOTE_HOST="aliyun"
REMOTE_DIR="/root/workspace/Finance"
REMOTE="$REMOTE_HOST:$REMOTE_DIR"
PYTHON="$LOCAL_DIR/.venv/bin/python"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 健康检查 ──
health_check_local() {
    info "本地健康检查..."
    cd "$LOCAL_DIR"
    local rc=0
    "$PYTHON" -c "
from src.data.data_health import health_check
r = health_check()
print(r.summary())
exit(0 if r.level != 'FAIL' else 1)
" || rc=$?
    if [ "$rc" -ne 0 ]; then
        error "健康检查未通过，中止操作"
        exit 1
    fi
    info "健康检查通过"
}

# ── 安全检查: source 不能比 dest 缩超 50% ──
# 用法: check_file_size <local_file> <remote_file> <label> <direction>
#   direction: pull (source=remote, dest=local) | push (source=local, dest=remote)
check_file_size() {
    local local_file="$1"
    local remote_file="$2"
    local label="$3"
    local direction="${4:-pull}"

    local local_size=0
    if [ -f "$local_file" ]; then
        local_size=$(stat -f%z "$local_file" 2>/dev/null || stat -c%s "$local_file" 2>/dev/null || echo 0)
    fi
    local remote_size
    remote_size=$(ssh "$REMOTE_HOST" "stat -c%s '$remote_file' 2>/dev/null || echo 0")

    # 确定 source/dest 大小
    local source_size dest_size
    if [ "$direction" = "push" ]; then
        source_size=$local_size
        dest_size=$remote_size
    else
        source_size=$remote_size
        dest_size=$local_size
    fi

    # dest 为 0 = 首次传输，跳过检查
    if [ "$dest_size" -eq 0 ] || [ "$source_size" -eq 0 ]; then
        return 0
    fi

    local ratio=$((source_size * 100 / dest_size))
    if [ "$ratio" -lt 50 ]; then
        error "$label 大小异常: source ${source_size}B vs dest ${dest_size}B (${ratio}%)，中止"
        exit 1
    fi
}

# ── Pull: 从云端拉取数据 ──
pull_from_cloud() {
    info "=== Pull: 从云端拉取数据 ==="

    # 1. SSH WAL checkpoint market.db
    info "云端 WAL checkpoint market.db..."
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && python3 -c \"
import sqlite3
conn = sqlite3.connect('data/market.db')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
print('WAL checkpoint OK')
\""

    # 2. rsync market.db 云端→本地 (+ 文件大小安全检查)
    info "拉取 market.db..."
    check_file_size "$LOCAL_DIR/data/market.db" "$REMOTE_DIR/data/market.db" "market.db" "pull"
    rsync -avz "$REMOTE/data/market.db" "$LOCAL_DIR/data/market.db"

    # 3. rsync fundamental/ 云端→本地 (--delete 清理云端已删除的过期文件)
    info "拉取 fundamental/..."
    rsync -avz --delete "$REMOTE/data/fundamental/" "$LOCAL_DIR/data/fundamental/"

    # 4. universe.json merge: 云端→本地
    info "合并 universe.json (云端→本地)..."
    scp "$REMOTE/data/pool/universe.json" "/tmp/universe_cloud.json"
    cd "$LOCAL_DIR"
    "$PYTHON" -c "
from src.data.pool_manager import merge_universe
added = merge_universe('/tmp/universe_cloud.json')
print(f'合并完成: 新增 {added} 个 symbol')
"
    rm -f /tmp/universe_cloud.json

    # 5. 本地健康检查
    health_check_local

    info "=== Pull 完成 ==="
}

# ── Push: 推送数据到云端 ──
push_to_cloud() {
    info "=== Push: 推送数据到云端 ==="

    # 1. 本地健康检查
    health_check_local

    # 2. rsync company.db 本地→云端 (+ 文件大小安全检查)
    info "推送 company.db..."
    check_file_size "$LOCAL_DIR/data/company.db" "$REMOTE_DIR/data/company.db" "company.db" "push"
    rsync -avz "$LOCAL_DIR/data/company.db" "$REMOTE/data/company.db"

    # 3. universe.json merge: 本地→云端
    info "合并 universe.json (本地→云端)..."
    scp "$LOCAL_DIR/data/pool/universe.json" "$REMOTE_HOST:/tmp/universe_local.json"
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && python3 -c \"
from src.data.pool_manager import merge_universe
added = merge_universe('/tmp/universe_local.json')
print(f'合并完成: 新增 {added} 个 symbol')
\" && rm -f /tmp/universe_local.json"

    # 4. 拉回合并后的 universe.json (通过 merge 确保只增不减)
    info "拉回合并后的 universe.json..."
    scp "$REMOTE/data/pool/universe.json" "/tmp/universe_merged_cloud.json"
    cd "$LOCAL_DIR"
    "$PYTHON" -c "
from src.data.pool_manager import merge_universe
added = merge_universe('/tmp/universe_merged_cloud.json')
print(f'本地 merge 完成: 新增 {added} 个 symbol')
"
    rm -f /tmp/universe_merged_cloud.json

    # 5. 云端验证
    info "云端验证..."
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && python3 -c \"
from src.data.pool_manager import get_symbols
import sqlite3
symbols = get_symbols()
conn = sqlite3.connect('data/market.db')
row = conn.execute('SELECT MAX(date) FROM daily_price').fetchone()
conn.close()
latest = row[0] if row else 'N/A'
print(f'股票池: {len(symbols)} 只')
print(f'market.db 最新日期: {latest}')
print('验证通过')
\""

    info "=== Push 完成 ==="
}

# ── Status: 显示双端状态 ──
show_status() {
    info "=== 双端状态 ==="

    # 本地
    echo ""
    info "--- 本地 ---"
    cd "$LOCAL_DIR"
    "$PYTHON" -c "
from src.data.pool_manager import get_symbols
import sqlite3, os
symbols = get_symbols()
conn = sqlite3.connect('data/market.db')
row = conn.execute('SELECT MAX(date) FROM daily_price').fetchone()
conn.close()
latest = row[0] if row else 'N/A'
cdb_size = os.path.getsize('data/company.db') / 1024 / 1024
mdb_size = os.path.getsize('data/market.db') / 1024 / 1024
print(f'  股票池: {len(symbols)} 只')
print(f'  market.db: {mdb_size:.1f}MB, 最新日期: {latest}')
print(f'  company.db: {cdb_size:.1f}MB')
"

    # 云端
    echo ""
    info "--- 云端 ---"
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && python3 -c \"
from src.data.pool_manager import get_symbols
import sqlite3, os
symbols = get_symbols()
conn = sqlite3.connect('data/market.db')
row = conn.execute('SELECT MAX(date) FROM daily_price').fetchone()
conn.close()
latest = row[0] if row else 'N/A'
cdb_size = os.path.getsize('data/company.db') / 1024 / 1024
mdb_size = os.path.getsize('data/market.db') / 1024 / 1024
print(f'  股票池: {len(symbols)} 只')
print(f'  market.db: {mdb_size:.1f}MB, 最新日期: {latest}')
print(f'  company.db: {cdb_size:.1f}MB')
\""

    echo ""
    info "=== 状态查询完成 ==="
}

# ── 帮助 ──
show_help() {
    echo "Finance 云端同步 (P3 所有权模型)"
    echo ""
    echo "用法: ./sync_to_cloud.sh [--pull|--push|--sync|--status]"
    echo ""
    echo "  --pull    从云端拉取 market.db + fundamental/ + 合并 universe.json"
    echo "  --push    推送 company.db + 合并 universe.json 到云端"
    echo "  --sync    先 pull 再 push，完整双向同步"
    echo "  --status  显示双端状态 (池数量、market.db 日期)"
    echo ""
    echo "所有权规则:"
    echo "  company.db    本地 -> 云端 (push)"
    echo "  market.db     云端 -> 本地 (pull)"
    echo "  universe.json 双向合并 (merge)"
    echo "  fundamental/  云端 -> 本地 (pull)"
    echo ""
    echo "代码同步已改用 git push + 云端 06:25 自动 git pull"
}

# ── 主入口 ──
case "${1:-}" in
    --pull)
        pull_from_cloud
        ;;
    --push)
        push_to_cloud
        ;;
    --sync)
        pull_from_cloud
        echo ""
        push_to_cloud
        ;;
    --status)
        show_status
        ;;
    --code|--data|--all)
        error "--code/--data/--all 已废弃，请使用 --pull/--push/--sync"
        echo ""
        show_help
        exit 1
        ;;
    *)
        show_help
        exit 0
        ;;
esac

echo ""
info "同步完成!"

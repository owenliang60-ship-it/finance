#!/bin/bash
# Finance 工作区云端同步
# 用法: ./sync_to_cloud.sh [--code|--data|--push|--pull|--sync]
# --push: 推代码+数据到云端 (等同 --all)
# --pull: 从云端拉最新价格和基本面到本地
# --sync: 先 pull 再 push，完整双向同步
# 注意: 云端路径仍为 /root/workspace/Finance (保持 cron 兼容)

set -e

LOCAL_DIR="/Users/owen/CC workspace/Finance"
REMOTE="aliyun:/root/workspace/Finance"
PYTHON="$LOCAL_DIR/.venv/bin/python"

sync_code() {
    echo "📦 同步代码..."
    rsync -avz --delete \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        "$LOCAL_DIR/src/" "$REMOTE/src/"
    rsync -avz --delete \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        "$LOCAL_DIR/scripts/" "$REMOTE/scripts/"
    rsync -avz "$LOCAL_DIR/config/settings.py" "$REMOTE/config/"

    # 分析引擎
    rsync -avz --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$LOCAL_DIR/terminal/" "$REMOTE/terminal/"
    rsync -avz --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$LOCAL_DIR/knowledge/" "$REMOTE/knowledge/"
    rsync -avz --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$LOCAL_DIR/tests/" "$REMOTE/tests/"

    # 依赖文件
    rsync -avz "$LOCAL_DIR/requirements.txt" "$REMOTE/"

    echo "✅ 代码同步完成"
}

sync_data() {
    echo "📊 同步数据..."
    rsync -avz "$LOCAL_DIR/data/fundamental/" "$REMOTE/data/fundamental/"
    rsync -avz "$LOCAL_DIR/data/pool/" "$REMOTE/data/pool/"
    rsync -avz "$LOCAL_DIR/data/company.db" "$REMOTE/data/company.db"
    # 量价数据通常云端自己更新，除非需要可以取消注释
    # rsync -avz "$LOCAL_DIR/data/price/" "$REMOTE/data/price/"
    echo "✅ 数据同步完成"
}

verify_cloud() {
    echo "🔍 验证云端..."
    ssh aliyun "cd /root/workspace/Finance && python3 -c \"
from config.settings import FMP_API_KEY
from src.data.pool_manager import get_symbols, sync_db_pool
from terminal.pipeline import collect_data
print(f'API Key: OK')
print(f'股票池: {len(get_symbols())} 只')
synced = sync_db_pool()
print(f'DB pool synced: {synced} companies')
print(f'Pipeline: OK')
\""
    echo "✅ 云端验证通过"
}

pull_data() {
    echo "📥 从云端拉取最新数据..."
    # 价格数据 (云端 cron 每日更新)
    rsync -avz "$REMOTE/data/price/" "$LOCAL_DIR/data/price/"
    # 基本面 (云端周六更新)
    rsync -avz "$REMOTE/data/fundamental/" "$LOCAL_DIR/data/fundamental/"
    # 股票池 (云端周六更新)
    rsync -avz "$REMOTE/data/pool/" "$LOCAL_DIR/data/pool/"
    # company.db (云端 IV cron 每日写入)
    rsync -avz "$REMOTE/data/company.db" "$LOCAL_DIR/data/company.db"
    echo "✅ 本地数据已更新到云端最新版本"
}

health_check_local() {
    echo "🔍 推送前健康检查..."
    cd "$LOCAL_DIR"
    "$PYTHON" -c "from src.data.data_health import health_check; r=health_check(); print(r.summary()); exit(0 if r.level != 'FAIL' else 1)"
    if [ $? -ne 0 ]; then
        echo "❌ 健康检查未通过，中止推送"
        exit 1
    fi
    echo "✅ 健康检查通过"
}

push_all() {
    health_check_local
    sync_code
    sync_data
    verify_cloud
}

case "${1:-}" in
    --code)
        sync_code
        ;;
    --data)
        sync_data
        ;;
    --pull)
        pull_data
        ;;
    --push|--all)
        push_all
        ;;
    --sync)
        pull_data
        echo ""
        push_all
        ;;
    *)
        echo "用法: ./sync_to_cloud.sh [--code|--data|--push|--pull|--sync]"
        echo ""
        echo "  --pull   从云端拉取最新价格/基本面到本地"
        echo "  --push   推送代码+数据到云端 (等同 --all)"
        echo "  --sync   先 pull 再 push，完整双向同步"
        echo "  --code   只推代码"
        echo "  --data   只推数据"
        exit 0
        ;;
esac

echo ""
echo "同步完成!"

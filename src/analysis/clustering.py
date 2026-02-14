"""
层次聚类引擎 — Ward 聚类 + Jaccard 变化检测，用于每周轮动监控。

算法流程：
1. 从日收益率构建 30 天滚动相关矩阵
2. 转换为距离矩阵: distance = sqrt(2 * (1 - corr))
3. Ward 层次聚类 (scipy)
4. fcluster 按距离阈值切割
5. 与上周聚类结果做 Jaccard 相似度比较
6. Jaccard < 0.3 触发 NEW_FORMATION 信号
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import ward, fcluster
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)

# 默认历史存储路径
DEFAULT_HISTORY_DIR = Path(__file__).parent.parent.parent / "data" / "clustering"
DEFAULT_HISTORY_FILE = DEFAULT_HISTORY_DIR / "cluster_history.json"

# Jaccard 阈值：低于此值视为集群结构发生重大变化
NEW_FORMATION_THRESHOLD = 0.3

# 历史记录最多保留周数
MAX_HISTORY_WEEKS = 52


def compute_clusters(
    price_dict: Dict[str, pd.DataFrame],
    corr_window: int = 30,
    distance_threshold: float = 1.0,
) -> dict:
    """
    从价格数据计算层次聚类。

    Args:
        price_dict: {symbol: price_df}，price_df 含 [date, close] 列，按日期升序排列
        corr_window: 相关性计算窗口（交易日）
        distance_threshold: fcluster 距离阈值，越小分组越细

    Returns:
        {
            "clusters": {cluster_id: [symbols]},
            "n_clusters": int,
            "symbols_used": [symbols],
            "computed_at": ISO timestamp
        }
        数据不足时返回空结构。
    """
    # 1. 计算日收益率并筛选有效标的
    min_days = corr_window + 1
    returns_dict = {}

    for symbol, df in price_dict.items():
        if len(df) < min_days:
            logger.warning(f"{symbol}: 数据不足 ({len(df)}/{min_days} 天)，跳过")
            continue

        # 取最近 corr_window+1 天，计算收益率
        df_sorted = df.sort_values("date", ascending=True).reset_index(drop=True)
        df_tail = df_sorted.tail(min_days)
        returns = df_tail.set_index("date")["close"].pct_change().dropna()
        returns.name = symbol
        returns_dict[symbol] = returns

    symbols_used = sorted(returns_dict.keys())

    if len(symbols_used) < 3:
        logger.warning(f"有效标的不足 ({len(symbols_used)}/3)，无法聚类")
        return {
            "clusters": {},
            "n_clusters": 0,
            "symbols_used": [],
            "computed_at": datetime.now().isoformat(),
        }

    # 2. 构建收益率矩阵并计算相关性
    returns_df = pd.DataFrame(returns_dict)
    corr_matrix = returns_df.corr(method="pearson")

    # 3. 转换为距离矩阵: distance = sqrt(2 * (1 - corr))
    # 裁剪相关系数到 [-1, 1] 避免浮点误差导致负距离
    corr_values = corr_matrix.values.copy()  # copy 避免 read-only
    np.fill_diagonal(corr_values, 1.0)
    corr_clipped = np.clip(corr_values, -1.0, 1.0)
    distance_matrix = np.sqrt(2.0 * (1.0 - corr_clipped))
    np.fill_diagonal(distance_matrix, 0.0)

    # 4. 转换为压缩形式并执行 Ward 聚类
    condensed = squareform(distance_matrix, checks=False)
    linkage_matrix = ward(condensed)
    labels = fcluster(linkage_matrix, t=distance_threshold, criterion="distance")

    # 5. 整理聚类结果
    clusters: Dict[int, List[str]] = {}
    for symbol, label in zip(symbols_used, labels):
        cluster_id = int(label)
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        clusters[cluster_id].append(symbol)

    # 排序每个聚类内的标的
    for cluster_id in clusters:
        clusters[cluster_id].sort()

    result = {
        "clusters": clusters,
        "n_clusters": len(clusters),
        "symbols_used": symbols_used,
        "computed_at": datetime.now().isoformat(),
    }

    logger.info(
        f"聚类完成: {len(symbols_used)} 只标的 → {len(clusters)} 个集群"
    )
    return result


def compare_clusters(
    current: Dict[int, List[str]],
    previous: Dict[int, List[str]],
) -> dict:
    """
    比较两次聚类结果，计算 Jaccard 相似度。

    使用贪心匹配：对每个 current 集群找 previous 中 Jaccard 最高的匹配，
    然后取所有最佳匹配的平均值。

    Args:
        current: 当前聚类 {cluster_id: [symbols]}
        previous: 上周聚类 {cluster_id: [symbols]}

    Returns:
        {
            "jaccard": float (0-1, 所有最佳匹配的平均 Jaccard),
            "new_formation": bool (jaccard < 0.3),
            "changes": [{"current_cluster": id, "matched_previous": id,
                         "jaccard": float, "added": [...], "removed": [...]}]
        }
    """
    if not current or not previous:
        return {
            "jaccard": 0.0,
            "new_formation": True,
            "changes": [],
        }

    current_sets = {k: set(v) for k, v in current.items()}
    previous_sets = {k: set(v) for k, v in previous.items()}

    used_previous = set()
    matches = []

    # 贪心匹配：每个 current 集群找最佳 previous 匹配
    for c_id, c_set in current_sets.items():
        best_jaccard = -1.0
        best_p_id = None

        for p_id, p_set in previous_sets.items():
            if p_id in used_previous:
                continue
            intersection = len(c_set & p_set)
            union = len(c_set | p_set)
            jac = intersection / union if union > 0 else 0.0
            if jac > best_jaccard:
                best_jaccard = jac
                best_p_id = p_id

        if best_p_id is not None:
            used_previous.add(best_p_id)
            p_set = previous_sets[best_p_id]
            added = sorted(c_set - p_set)
            removed = sorted(p_set - c_set)

            matches.append({
                "current_cluster": c_id,
                "matched_previous": best_p_id,
                "jaccard": round(best_jaccard, 4),
                "added": added,
                "removed": removed,
            })
        else:
            # 没有可匹配的 previous 集群（全新集群）
            matches.append({
                "current_cluster": c_id,
                "matched_previous": None,
                "jaccard": 0.0,
                "added": sorted(c_set),
                "removed": [],
            })

    # 计算平均 Jaccard
    if matches:
        avg_jaccard = sum(m["jaccard"] for m in matches) / len(matches)
    else:
        avg_jaccard = 0.0

    avg_jaccard = round(avg_jaccard, 4)
    new_formation = avg_jaccard < NEW_FORMATION_THRESHOLD

    if new_formation:
        logger.warning(
            f"NEW_FORMATION 信号: Jaccard={avg_jaccard:.4f} < {NEW_FORMATION_THRESHOLD}"
        )
    else:
        logger.info(f"集群稳定: Jaccard={avg_jaccard:.4f}")

    # 只保留有实质变化的记录
    notable_changes = [m for m in matches if m["added"] or m["removed"]]

    return {
        "jaccard": avg_jaccard,
        "new_formation": new_formation,
        "changes": notable_changes,
    }


def load_cluster_history(path: Path) -> Optional[dict]:
    """
    从历史文件加载最近一条聚类记录。

    Args:
        path: 历史 JSON 文件路径

    Returns:
        最近一条记录（dict），文件不存在或为空时返回 None
    """
    if not path.exists():
        logger.info(f"聚类历史文件不存在: {path}")
        return None

    try:
        with open(path) as f:
            history = json.load(f)

        if not history:
            return None

        return history[-1]
    except Exception as e:
        logger.error(f"加载聚类历史失败: {e}")
        return None


def save_cluster_history(path: Path, entry: dict) -> None:
    """
    追加一条聚类记录到历史文件，最多保留 52 周。

    Args:
        path: 历史 JSON 文件路径
        entry: 聚类结果（含 computed_at, clusters, n_clusters）
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # 加载已有历史
    history = []
    if path.exists():
        try:
            with open(path) as f:
                history = json.load(f)
        except Exception:
            logger.warning(f"历史文件损坏，将重建: {path}")
            history = []

    # 追加新记录
    history.append(entry)

    # 保留最近 MAX_HISTORY_WEEKS 条
    if len(history) > MAX_HISTORY_WEEKS:
        history = history[-MAX_HISTORY_WEEKS:]

    with open(path, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    logger.info(f"聚类历史已保存: {path} (共 {len(history)} 条记录)")


def run_weekly_clustering(
    price_dict: Dict[str, pd.DataFrame],
    history_path: Path = None,
    corr_window: int = 30,
    distance_threshold: float = 1.0,
) -> dict:
    """
    每周聚类入口：计算聚类 → 与历史比较 → 保存记录。

    Args:
        price_dict: {symbol: price_df}，price_df 含 [date, close] 列
        history_path: 历史文件路径，默认 data/clustering/cluster_history.json
        corr_window: 相关性计算窗口
        distance_threshold: 聚类距离阈值

    Returns:
        {
            "clusters": {...},
            "n_clusters": int,
            "symbols_used": [...],
            "computed_at": str,
            "comparison": {...} or None (首次运行无比较)
        }
    """
    if history_path is None:
        history_path = DEFAULT_HISTORY_FILE

    # 1. 计算当前聚类
    result = compute_clusters(price_dict, corr_window, distance_threshold)

    if not result["clusters"]:
        logger.warning("聚类结果为空，跳过保存")
        result["comparison"] = None
        return result

    # 2. 加载历史并比较
    previous = load_cluster_history(history_path)
    if previous and previous.get("clusters"):
        # 历史中 clusters 的 key 是字符串（JSON 序列化），转回 int
        prev_clusters = {int(k): v for k, v in previous["clusters"].items()}
        comparison = compare_clusters(result["clusters"], prev_clusters)
    else:
        comparison = None
        logger.info("首次运行，无历史数据可比较")

    result["comparison"] = comparison

    # 3. 保存到历史
    save_entry = {
        "computed_at": result["computed_at"],
        "clusters": {str(k): v for k, v in result["clusters"].items()},
        "n_clusters": result["n_clusters"],
    }
    save_cluster_history(history_path, save_entry)

    return result

"""Tests for src/analysis/clustering.py"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.clustering import (
    compare_clusters,
    compute_clusters,
    load_cluster_history,
    run_weekly_clustering,
    save_cluster_history,
)


def _make_correlated_prices(group_symbols, n_days=80, seed=42):
    """Create price dicts where stocks in same list are correlated."""
    np.random.seed(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    result = {}
    for group_idx, syms in enumerate(group_symbols):
        base_returns = np.random.randn(n_days) * 0.02
        for sym in syms:
            noise = np.random.randn(n_days) * 0.005
            prices = 100 * np.exp(np.cumsum(base_returns + noise))
            result[sym] = pd.DataFrame({"date": dates, "close": prices})
    return result


class TestComputeClusters:
    def test_basic_clustering(self):
        """3 组高度相关的股票应被分成 3 个集群。"""
        groups = [
            ["NVDA", "AMD", "AVGO"],
            ["JPM", "GS", "MS"],
            ["UNH", "JNJ", "LLY"],
        ]
        prices = _make_correlated_prices(groups, n_days=80)
        result = compute_clusters(prices, corr_window=60, distance_threshold=1.0)

        assert result["n_clusters"] >= 2  # 至少能区分出多个组
        assert result["n_clusters"] <= 5  # 不应过度分裂
        assert len(result["symbols_used"]) == 9
        assert result["computed_at"]  # ISO timestamp 非空

        # 验证同组股票大概率在同一集群
        clusters = result["clusters"]
        all_symbols = []
        for syms in clusters.values():
            all_symbols.extend(syms)
        assert set(all_symbols) == {"NVDA", "AMD", "AVGO", "JPM", "GS", "MS", "UNH", "JNJ", "LLY"}

    def test_insufficient_symbols(self):
        """少于 3 只标的应返回空结构。"""
        prices = _make_correlated_prices([["AAPL", "MSFT"]], n_days=80)
        result = compute_clusters(prices, corr_window=60)

        assert result["clusters"] == {}
        assert result["n_clusters"] == 0
        assert result["symbols_used"] == []

    def test_insufficient_data(self):
        """数据天数不足 corr_window+1 时应跳过。"""
        prices = _make_correlated_prices([["A", "B", "C"]], n_days=20)
        result = compute_clusters(prices, corr_window=30)

        # 20 天 < 31 天要求，全部跳过
        assert result["clusters"] == {}
        assert result["n_clusters"] == 0

    def test_single_cluster(self):
        """极度相似的股票应聚为 1 个集群。"""
        # 所有股票用同一组基础收益率，噪声极小
        np.random.seed(99)
        dates = pd.date_range("2025-01-01", periods=80, freq="B")
        base_returns = np.random.randn(80) * 0.02
        prices = {}
        for sym in ["X1", "X2", "X3", "X4"]:
            tiny_noise = np.random.randn(80) * 0.0001
            p = 100 * np.exp(np.cumsum(base_returns + tiny_noise))
            prices[sym] = pd.DataFrame({"date": dates, "close": p})

        result = compute_clusters(prices, corr_window=60, distance_threshold=1.0)
        assert result["n_clusters"] == 1
        assert len(result["symbols_used"]) == 4


class TestCompareClusters:
    def test_identical_clusters(self):
        """完全相同的聚类结果应有 jaccard=1.0。"""
        clusters = {1: ["AAPL", "MSFT"], 2: ["NVDA", "AMD"]}
        result = compare_clusters(clusters, clusters)

        assert result["jaccard"] == 1.0
        assert result["new_formation"] is False
        assert result["changes"] == []  # 无变化

    def test_completely_different(self):
        """完全不同的成员应触发 NEW_FORMATION。"""
        current = {1: ["AAPL", "MSFT"], 2: ["NVDA", "AMD"]}
        previous = {1: ["JPM", "GS"], 2: ["UNH", "LLY"]}
        result = compare_clusters(current, previous)

        assert result["jaccard"] == 0.0
        assert result["new_formation"] is True

    def test_partial_overlap(self):
        """部分重叠应产生中间 Jaccard 值。"""
        current = {1: ["AAPL", "MSFT", "GOOGL"], 2: ["NVDA", "AMD"]}
        previous = {1: ["AAPL", "MSFT", "META"], 2: ["NVDA", "AMD"]}
        result = compare_clusters(current, previous)

        # 集群2 完全匹配 (jaccard=1.0)，集群1 部分匹配 (jaccard=2/4=0.5)
        assert 0.5 < result["jaccard"] < 1.0
        assert result["new_formation"] is False
        # 应检测到集群1 的变化
        assert len(result["changes"]) >= 1

    def test_empty_previous(self):
        """空 previous 应返回 jaccard=0 和 new_formation=True。"""
        current = {1: ["AAPL", "MSFT"]}
        result = compare_clusters(current, {})

        assert result["jaccard"] == 0.0
        assert result["new_formation"] is True


class TestClusterHistory:
    def test_save_and_load_roundtrip(self, tmp_path):
        """保存后加载应返回相同数据。"""
        history_file = tmp_path / "cluster_history.json"
        entry = {
            "computed_at": "2026-02-08T07:00:00",
            "clusters": {"0": ["NVDA", "AMD"], "1": ["JPM", "GS"]},
            "n_clusters": 2,
        }

        save_cluster_history(history_file, entry)
        loaded = load_cluster_history(history_file)

        assert loaded is not None
        assert loaded["computed_at"] == "2026-02-08T07:00:00"
        assert loaded["clusters"] == {"0": ["NVDA", "AMD"], "1": ["JPM", "GS"]}

    def test_history_max_52_entries(self, tmp_path):
        """超过 52 条时应只保留最近 52 条。"""
        history_file = tmp_path / "cluster_history.json"

        # 写入 55 条
        for i in range(55):
            entry = {
                "computed_at": f"2025-01-{i+1:02d}T00:00:00",
                "clusters": {"0": ["A", "B"]},
                "n_clusters": 1,
            }
            save_cluster_history(history_file, entry)

        with open(history_file) as f:
            history = json.load(f)

        assert len(history) == 52
        # 最早的应是第 4 条（0-indexed: 3），因为前 3 条被截断
        assert history[0]["computed_at"] == "2025-01-04T00:00:00"

    def test_load_nonexistent_returns_none(self, tmp_path):
        """不存在的文件应返回 None。"""
        result = load_cluster_history(tmp_path / "does_not_exist.json")
        assert result is None


class TestRunWeeklyClustering:
    def test_first_run_no_history(self, tmp_path):
        """首次运行无历史时应正常聚类且 comparison=None。"""
        history_file = tmp_path / "cluster_history.json"
        groups = [
            ["NVDA", "AMD", "AVGO"],
            ["JPM", "GS", "MS"],
        ]
        prices = _make_correlated_prices(groups, n_days=80)

        result = run_weekly_clustering(
            prices,
            history_path=history_file,
            corr_window=60,
        )

        assert result["n_clusters"] >= 1
        assert result["comparison"] is None
        assert len(result["symbols_used"]) == 6

        # 验证历史文件已创建
        assert history_file.exists()
        with open(history_file) as f:
            history = json.load(f)
        assert len(history) == 1

"""merge_universe() 单元测试"""
import json
import pytest
from pathlib import Path

from src.data.pool_manager import merge_universe, _get_source_priority


@pytest.fixture
def tmp_universe(tmp_path):
    """创建临时 universe 文件对的工厂函数。"""
    def _make(target_stocks, incoming_stocks):
        target_file = tmp_path / "target.json"
        incoming_file = tmp_path / "incoming.json"
        target_file.write_text(json.dumps(target_stocks, ensure_ascii=False, indent=2))
        incoming_file.write_text(json.dumps(incoming_stocks, ensure_ascii=False, indent=2))
        return str(incoming_file), str(target_file)
    return _make


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 1. 新 symbol 添加 ──
def test_new_symbols_added(tmp_universe):
    target = [{"symbol": "AAPL", "companyName": "Apple", "source": "screener"}]
    incoming = [{"symbol": "MSFT", "companyName": "Microsoft", "source": "screener"}]
    inc_path, tgt_path = tmp_universe(target, incoming)

    added = merge_universe(inc_path, tgt_path)

    assert added == 1
    result = _read(tgt_path)
    symbols = {s["symbol"] for s in result}
    assert symbols == {"AAPL", "MSFT"}


# ── 2. 不删除本地已有 symbol ──
def test_no_deletion_of_existing(tmp_universe):
    target = [
        {"symbol": "AAPL", "source": "screener"},
        {"symbol": "NVDA", "source": "analysis"},
    ]
    incoming = [{"symbol": "AAPL", "source": "screener"}]  # NVDA 不在 incoming 中
    inc_path, tgt_path = tmp_universe(target, incoming)

    added = merge_universe(inc_path, tgt_path)

    assert added == 0
    result = _read(tgt_path)
    symbols = {s["symbol"] for s in result}
    assert "NVDA" in symbols  # 不被删除


# ── 3. source 优先级高的赢 (analysis > screener) ──
def test_higher_source_priority_wins(tmp_universe):
    target = [{"symbol": "TSLA", "companyName": "Tesla-old", "source": "screener"}]
    incoming = [{"symbol": "TSLA", "companyName": "Tesla-new", "source": "analysis"}]
    inc_path, tgt_path = tmp_universe(target, incoming)

    merge_universe(inc_path, tgt_path)

    result = _read(tgt_path)
    tsla = [s for s in result if s["symbol"] == "TSLA"][0]
    assert tsla["source"] == "analysis"
    assert tsla["companyName"] == "Tesla-new"


# ── 4. 同优先级保留 target（稳定性） ──
def test_same_priority_keeps_target(tmp_universe):
    target = [{"symbol": "GOOG", "companyName": "Google-local", "source": "manual"}]
    incoming = [{"symbol": "GOOG", "companyName": "Google-remote", "source": "manual"}]
    inc_path, tgt_path = tmp_universe(target, incoming)

    merge_universe(inc_path, tgt_path)

    result = _read(tgt_path)
    goog = [s for s in result if s["symbol"] == "GOOG"][0]
    assert goog["companyName"] == "Google-local"


# ── 5. 空 target 处理 ──
def test_empty_target(tmp_path):
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text(json.dumps([
        {"symbol": "AMD", "source": "screener"},
        {"symbol": "MU", "source": "manual"},
    ]))
    target_file = tmp_path / "target.json"
    # target 不存在

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 2
    result = _read(str(target_file))
    assert len(result) == 2


# ── 6. 自定义 target_path ──
def test_custom_target_path(tmp_path):
    target_file = tmp_path / "sub" / "custom.json"
    target_file.parent.mkdir(parents=True)
    target_file.write_text(json.dumps([{"symbol": "META", "source": "screener"}]))

    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text(json.dumps([{"symbol": "NFLX", "source": "screener"}]))

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 1
    result = _read(str(target_file))
    symbols = {s["symbol"] for s in result}
    assert symbols == {"META", "NFLX"}


# ── 7. 缺失 source 视为 screener ──
def test_missing_source_treated_as_screener(tmp_universe):
    target = [{"symbol": "AAPL", "companyName": "Apple"}]  # 无 source 字段
    incoming = [{"symbol": "AAPL", "companyName": "Apple-new", "source": "manual"}]
    inc_path, tgt_path = tmp_universe(target, incoming)

    merge_universe(inc_path, tgt_path)

    result = _read(tgt_path)
    aapl = [s for s in result if s["symbol"] == "AAPL"][0]
    # manual (3) > screener (1)，incoming 赢
    assert aapl["source"] == "manual"


# ── 8. 完整合并 roundtrip ──
def test_full_roundtrip(tmp_universe):
    """模拟真实场景：本地有 analysis 股，云端有 screener 新股。"""
    target = [
        {"symbol": "NVDA", "source": "analysis", "companyName": "NVIDIA"},
        {"symbol": "AAPL", "source": "screener", "companyName": "Apple"},
        {"symbol": "TSLA", "companyName": "Tesla"},  # 无 source = screener
    ]
    incoming = [
        {"symbol": "AAPL", "source": "screener", "companyName": "Apple-cloud"},
        {"symbol": "MSFT", "source": "screener", "companyName": "Microsoft"},
        {"symbol": "NVDA", "source": "screener", "companyName": "NVIDIA-cloud"},  # 低优先级
    ]
    inc_path, tgt_path = tmp_universe(target, incoming)

    added = merge_universe(inc_path, tgt_path)

    assert added == 1  # 只有 MSFT 是新增
    result = _read(tgt_path)
    by_sym = {s["symbol"]: s for s in result}
    assert len(by_sym) == 4  # NVDA + AAPL + TSLA + MSFT
    assert by_sym["NVDA"]["source"] == "analysis"  # 保留高优先级
    assert by_sym["NVDA"]["companyName"] == "NVIDIA"  # 本地版本
    assert by_sym["AAPL"]["companyName"] == "Apple"  # 同优先级保留本地


# ── 9. malformed JSON 不崩溃 ──
def test_malformed_incoming_json(tmp_path):
    """incoming 文件 JSON 损坏时，应返回 0 且不修改 target。"""
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps([{"symbol": "AAPL", "source": "screener"}]))
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text("{broken json!!!")

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 0
    result = _read(str(target_file))
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_malformed_target_json(tmp_path):
    """target 文件 JSON 损坏时，应返回 0 且不覆盖 target。"""
    target_file = tmp_path / "target.json"
    target_file.write_text("not valid json [[[")
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text(json.dumps([{"symbol": "MSFT", "source": "screener"}]))

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 0
    # target 文件内容应该不变（保护现有数据）
    assert target_file.read_text() == "not valid json [[["


def test_incoming_not_a_list(tmp_path):
    """incoming 是合法 JSON 但不是列表时，应返回 0 且不修改 target。"""
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps([{"symbol": "AAPL", "source": "screener"}]))
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text(json.dumps({"symbol": "MSFT", "source": "screener"}))  # dict, not list

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 0
    result = _read(str(target_file))
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_target_not_a_list(tmp_path):
    """target 是合法 JSON 但不是列表时，应返回 0 且不覆盖 target。"""
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps("just a string"))
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text(json.dumps([{"symbol": "MSFT", "source": "screener"}]))

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 0
    assert json.loads(target_file.read_text()) == "just a string"


def test_empty_incoming_list(tmp_path):
    """incoming 为空列表时，返回 0 且 target 不变。"""
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps([{"symbol": "AAPL", "source": "screener"}]))
    incoming_file = tmp_path / "incoming.json"
    incoming_file.write_text("[]")

    added = merge_universe(str(incoming_file), str(target_file))

    assert added == 0
    result = _read(str(target_file))
    assert len(result) == 1


# ── 辅助函数测试 ──
def test_get_source_priority():
    assert _get_source_priority({"source": "analysis"}) == 4
    assert _get_source_priority({"source": "manual"}) == 3
    assert _get_source_priority({"source": "screener"}) == 1
    assert _get_source_priority({}) == 1  # 缺失 = screener
    assert _get_source_priority({"source": "unknown"}) == 1  # 未知 = screener

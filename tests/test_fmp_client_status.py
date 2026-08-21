"""FMPClient.get_dataset_with_status 状态区分测试（ok / provider_empty / fetch_failed）。

不接触网络：client_with_response 直接替换 client._request；
client_with_5xx 只替换 requests.get 和 time.sleep，走真实重试循环以验证 attempts 计数。
"""
import time as time_module

import pytest
import requests as requests_module

from src.data.fmp_client import FMPClient


@pytest.fixture
def client_with_response():
    """工厂 fixture：给定一个 payload，返回 _request 被固定为该 payload 的 client。"""
    def _make(payload):
        client = FMPClient(api_key="test_key")
        client._request = lambda endpoint, params=None, **_kw: payload
        return client
    return _make


@pytest.fixture
def client_with_5xx(monkeypatch):
    """走真实 _request 重试循环，mock 底层 requests.get 恒定返回 500，并暴露 attempts 计数。"""
    client = FMPClient(api_key="test_key")
    client.attempts = 0

    class FakeResp:
        status_code = 500
        text = "internal server error"

    def fake_get(*args, **kwargs):
        client.attempts += 1
        return FakeResp()

    monkeypatch.setattr(requests_module, "get", fake_get)
    monkeypatch.setattr(time_module, "sleep", lambda *_a, **_kw: None)
    return client


def test_ok(client_with_response):
    data, status = client_with_response([{"date": "2026-06-30"}]).get_dataset_with_status("income", "AAPL")
    assert status == "ok" and data


def test_provider_empty(client_with_response):
    data, status = client_with_response([]).get_dataset_with_status("income", "GHOST")
    assert status == "provider_empty" and data == []


def test_fetch_failed_on_none(client_with_response):
    data, status = client_with_response(None).get_dataset_with_status("income", "AAPL")
    assert status == "fetch_failed"


def test_5xx_retries_then_failed(client_with_5xx):
    data, status = client_with_5xx.get_dataset_with_status("income", "AAPL")
    assert status == "fetch_failed" and client_with_5xx.attempts == 3


def test_unknown_kind_raises_value_error(client_with_response):
    with pytest.raises(ValueError):
        client_with_response([]).get_dataset_with_status("nonsense", "AAPL")


@pytest.mark.parametrize("kind,expected_endpoint,expects_period", [
    ("profile", "profile", False),
    ("income", "income-statement", True),
    ("balance", "balance-sheet-statement", True),
    ("cashflow", "cash-flow-statement", True),
    ("ratios", "ratios", False),
])
def test_kind_maps_to_expected_endpoint_and_params(kind, expected_endpoint, expects_period):
    client = FMPClient(api_key="test_key")
    captured = {}

    def fake_request(endpoint, params=None, **_kw):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return []

    client._request = fake_request
    client.get_dataset_with_status(kind, "AAPL", limit=8)

    assert captured["endpoint"] == expected_endpoint
    assert captured["params"]["symbol"] == "AAPL"
    if kind == "profile":
        assert "limit" not in captured["params"]
    else:
        assert captured["params"]["limit"] == 8
    if expects_period:
        assert captured["params"]["period"] == "quarter"
    else:
        assert "period" not in captured["params"]

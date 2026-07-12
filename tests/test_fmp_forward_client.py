"""FMP forward client 测试：secret 安全日志 + 实例级限速 + 3 个新 endpoint 契约。

CANARY 是运行时拼接的假 key 探针（plan Task 1 要求），不是真实凭证；
断言任何日志文本不得出现该字面值。
"""
import logging
import time
from unittest.mock import Mock

import pytest
import requests

import sys

from src.data.fmp_client import FMPClient

# src/data/__init__.py 导出同名单例 fmp_client，会遮蔽包属性；从 sys.modules 拿真模块
fmp_client_module = sys.modules["src.data.fmp_client"]

# 假 key 探针；运行时拼接以免触发 credential 扫描器的硬编码告警
CANARY = "canary_" + "leak_probe"


# ========== Task 1: secret-safe logging + instance rate limit ==========

def test_request_exception_log_redacts_apikey(monkeypatch, caplog):
    client = FMPClient(api_key=CANARY, call_interval=0)
    exc = requests.RequestException(
        f"boom https://financialmodelingprep.com/stable/x?symbol=AAPL&apikey={CANARY}"
    )
    monkeypatch.setattr(fmp_client_module.requests, "get", Mock(side_effect=exc))
    with caplog.at_level(logging.ERROR, logger="src.data.fmp_client"):
        assert client._request("x", {"symbol": "AAPL"}) is None
    assert CANARY not in caplog.text
    assert "apikey=***" in caplog.text


def test_http_error_body_and_params_repr_redact_literal_key(monkeypatch, caplog):
    client = FMPClient(api_key=CANARY, call_interval=0)
    response = Mock(status_code=500, text=f"upstream apikey={CANARY}")
    monkeypatch.setattr(fmp_client_module.requests, "get", Mock(return_value=response))
    with caplog.at_level(logging.ERROR, logger="src.data.fmp_client"):
        assert client._request("x", {"debug": {"apikey": CANARY}}) is None
    assert CANARY not in caplog.text


def test_instances_use_independent_call_intervals(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fmp_client_module.time, "sleep", sleeps.append)
    slow = FMPClient(api_key="x", call_interval=2.0)
    fast = FMPClient(api_key="x", call_interval=0.25)
    slow._last_call_time = time.time()
    fast._last_call_time = time.time()
    slow._rate_limit()
    fast._rate_limit()
    assert sleeps[0] > sleeps[1]


def test_default_call_interval_unchanged():
    """现有调用方不传 call_interval 时保持全局 2s 默认。"""
    from config.settings import API_CALL_INTERVAL

    client = FMPClient(api_key="x")
    assert client.call_interval == API_CALL_INTERVAL


def test_forward_interval_env_override():
    """FMP_FORWARD_API_CALL_INTERVAL 存在于 settings 且可被 forward CLI 显式传入。"""
    from config import settings

    assert hasattr(settings, "FMP_FORWARD_API_CALL_INTERVAL")
    client = FMPClient(api_key="x", call_interval=settings.FMP_FORWARD_API_CALL_INTERVAL)
    assert client.call_interval == settings.FMP_FORWARD_API_CALL_INTERVAL

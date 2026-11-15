import hashlib
import hmac
import json
from urllib.parse import parse_qs

import pytest
import requests

from trading.momentum_ls01.binance import (
    BinanceClient, Credentials, Endpoints, ExchangeError, RateLimitState,
    load_credentials, sign_query,
)


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data = {} if data is None else data
        self.status_code = status
        self.headers = {'X-MBX-USED-WEIGHT-1M': '1', **(headers or {})}

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def client(*responses, **kwargs):
    transport = Transport(*responses)
    cli = BinanceClient(transport=transport, limiter=kwargs.pop('limiter', RateLimitState(limit=100)),
                        clock=kwargs.pop('clock', lambda: 1000.0), **kwargs)
    return cli, transport


def test_hmac_rfc4231_vector():
    assert sign_query('Hi There', bytes.fromhex('0b' * 20)) == (
        'b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7')


def test_credentials_permissions_and_redacted_repr(tmp_path):
    path = tmp_path / 'demo.json'
    path.write_text(json.dumps({'api_key': 'private-key', 'api_secret': 'private-secret'}))
    path.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        load_credentials(path)
    path.chmod(0o600)
    creds = load_credentials(path)
    assert creds.api_key == 'private-key'
    assert 'private' not in repr(creds)
    path.write_text('{ invalid private-secret')
    with pytest.raises(ValueError) as err:
        load_credentials(path)
    assert 'private' not in str(err.value)


def test_public_get_needs_no_key_and_has_bounded_timeout():
    cli, transport = client(Response({'serverTime': 1}))
    assert cli.request('GET', Endpoints.TIME) == {'serverTime': 1}
    args, kwargs = transport.calls[0]
    assert args == ('GET', 'https://demo-fapi.binance.com/fapi/v1/time')
    assert kwargs['timeout'] == (5, 10)
    assert kwargs['headers'] == {}
    assert kwargs['allow_redirects'] is False


def test_signed_exact_payload_boolean_encoding_and_clock_sync():
    cli, transport = client(Response({'serverTime': 1000200}), Response({'orderId': 7}),
                            credentials=Credentials('key', 'secret'), allow_demo_writes=True)
    cli.sync_clock()
    assert cli.request('POST', Endpoints.ORDER, {'reduceOnly': True, 'quantity': '1.20'}, signed=True) == {'orderId': 7}
    query = transport.calls[-1][1]['data']
    unsigned, signature = query.rsplit('&signature=', 1)
    assert signature == hmac.new(b'secret', unsigned.encode(), hashlib.sha256).hexdigest()
    parsed = parse_qs(unsigned)
    assert parsed == {'reduceOnly': ['true'], 'quantity': ['1.20'], 'timestamp': ['1000200'], 'recvWindow': ['5000']}


@pytest.mark.parametrize('env,enabled', [('prod', False), ('prod', True), ('demo', False)])
def test_write_disabled_before_transport(env, enabled):
    cli, transport = client(env=env, allow_demo_writes=enabled)
    with pytest.raises(ExchangeError) as err:
        cli.request('POST', Endpoints.ORDER)
    assert err.value.outcome == 'NOT_SENT'
    assert transport.calls == []


@pytest.mark.parametrize('response,expected,code', [
    (Response({'code': -2022, 'msg': 'private-secret'}, 400), 'REJECTED', -2022),
    (Response({'code': -1007}, 504), 'UNKNOWN', -1007),
    (Response({'code': -1007}, 400), 'UNKNOWN', -1007),
    (Response({}, 408), 'UNKNOWN', None),
    (Response(ValueError('invalid secret response')), 'UNKNOWN', None),
    (requests.Timeout('url?signature=secret'), 'UNKNOWN', None),
    (requests.ConnectionError('key secret'), 'UNKNOWN', None),
])
def test_uncertain_outcomes_never_retry_or_expose_server_text(response, expected, code):
    cli, transport = client(response)
    with pytest.raises(ExchangeError) as err:
        cli.request('GET', Endpoints.TIME)
    assert (err.value.outcome, err.value.code) == (expected, code)
    assert len(transport.calls) == 1
    assert 'secret' not in str(err.value)
    assert err.value.__cause__ is None


def test_exchange_info_limit_bootstrap_and_header_threshold():
    limit = RateLimitState()
    cli, transport = client(Response({'rateLimits': [{'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 80}]}, headers={'X-MBX-USED-WEIGHT-1M': '60'}), Response(), limiter=limit)
    cli.request('GET', Endpoints.EXCHANGE_INFO)
    assert limit.limit == 80
    with pytest.raises(ExchangeError, match='weight'):
        cli.request('GET', Endpoints.KLINES)
    cli.request('GET', Endpoints.TIME, protective=True)
    assert len(transport.calls) == 2


def test_unknown_limit_blocks_nonbootstrap():
    cli, transport = client(limiter=RateLimitState())
    with pytest.raises(ExchangeError, match='limit'):
        cli.request('GET', Endpoints.KLINES)
    assert not transport.calls


@pytest.mark.parametrize('status,headers,wait', [(429, {}, 60), (429, {'Retry-After': '7'}, 7), (418, {'Retry-After': '120'}, 120)])
def test_shared_backoff_even_for_protection(status, headers, wait):
    now = [1000.0]
    state = RateLimitState(limit=100)
    cli, transport = client(Response({'code': -1003}, status, headers), limiter=state, clock=lambda: now[0])
    with pytest.raises(ExchangeError) as err:
        cli.request('GET', Endpoints.TIME)
    assert err.value.retry_after == wait
    other, second = client(Response(), limiter=state, clock=lambda: now[0])
    with pytest.raises(ExchangeError):
        other.request('GET', Endpoints.TIME, protective=True)
    assert not second.calls
    now[0] += wait
    other.request('GET', Endpoints.TIME, protective=True)
    if status == 418:
        assert state.new_orders_paused  # resume must be explicit in the owner


def test_skew_blocks_additions_but_protective_call_uses_corrected_timestamp():
    cli, transport = client(Response({'serverTime': 1002000}), Response(), credentials=Credentials('k', 's'), allow_demo_writes=True)
    cli.sync_clock()
    with pytest.raises(ExchangeError, match='clock'):
        cli.request('POST', Endpoints.ORDER, signed=True)
    cli.request('POST', Endpoints.ORDER, {'reduceOnly': True}, signed=True, protective=True)
    assert len(transport.calls) == 2


def test_signed_requires_sync_and_disallows_reserved_signing_fields():
    cli, transport = client(credentials=Credentials('k', 's'))
    with pytest.raises(ExchangeError, match='clock'):
        cli.request('GET', Endpoints.ACCOUNT, signed=True)
    with pytest.raises(ValueError):
        cli.request('GET', Endpoints.TIME, {'signature': 'fake'})
    assert not transport.calls


def test_algo_response_keeps_child_order_and_parent_status_separate():
    payload = {'algoId': 1, 'algoStatus': 'FINISHED', 'actualOrderId': '42'}
    cli, _ = client(Response({'serverTime': 1000000}), Response(payload), credentials=Credentials('k', 's'))
    cli.sync_clock()
    assert cli.request('GET', Endpoints.ALGO_ORDER, {'algoId': 1}, signed=True) == payload


def test_weight_header_after_minute_rollover_is_not_discarded():
    state = RateLimitState(limit=100)
    state.reserve(119.5, 1, False)
    state.observe({'X-MBX-USED-WEIGHT-1M': '80'}, 120.1)
    with pytest.raises(ExchangeError, match='weight'):
        state.reserve(120.2, 1, False)


def test_unsigned_private_endpoint_and_arbitrary_host_are_rejected():
    cli, transport = client()
    with pytest.raises(ExchangeError):
        cli.request('GET', Endpoints.ACCOUNT)
    with pytest.raises(ValueError):
        cli.request('GET', 'https://other.example/fapi/v1/time')
    assert not transport.calls


def test_418_without_deadline_blocks_until_manual_intervention():
    cli, _ = client(Response({'code': -1003}, 418))
    with pytest.raises(ExchangeError):
        cli.request('GET', Endpoints.TIME)
    assert cli.limiter.blocked_until == float('inf')


def test_shared_limiter_reserves_atomically_without_holding_lock_during_http():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(2)
    state = RateLimitState(limit=100)

    class ConcurrentTransport:
        def request(self, *args, **kwargs):
            barrier.wait(timeout=2)
            reply = Response({'serverTime': 1000000})
            reply.headers = {}   # no measured weight: both reservations must survive
            return reply

    clients = [BinanceClient(limiter=state, transport=ConcurrentTransport(), clock=lambda: 1000.0)
               for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda cli: cli.request('GET', Endpoints.TIME), clients))
    assert results == [{'serverTime': 1000000}] * 2
    assert state.used_weight == 2


def test_all_market_last_price_has_public_endpoint_and_weight_two():
    reply = Response([{'symbol': 'BTCUSDT', 'price': '10', 'time': 1000000}])
    reply.headers = {}   # no measured weight: the local reservation is what remains
    cli, _ = client(reply)
    assert cli.request('GET', Endpoints.TICKER_PRICE)[0]['price'] == '10'
    assert cli.limiter.used_weight == 2


def test_production_writes_need_their_own_explicit_switch():
    cli, transport = client(Response({'serverTime': 1000200}), Response({'orderId': 9}), env='prod',
                            credentials=Credentials('key', 'secret'), allow_prod_writes=True)
    cli.sync_clock()
    assert cli.request('POST', Endpoints.ORDER, {'quantity': '1'}, signed=True) == {'orderId': 9}
    assert transport.calls[-1][0][1].startswith('https://fapi.binance.com/')


@pytest.mark.parametrize('env,flags', [('prod', dict(allow_demo_writes=True)), ('demo', dict(allow_prod_writes=True))])
def test_a_write_switch_never_unlocks_the_other_environment(env, flags):
    cli, transport = client(env=env, **flags)
    with pytest.raises(ExchangeError) as err:
        cli.request('POST', Endpoints.ORDER)
    assert err.value.outcome == 'NOT_SENT' and transport.calls == []

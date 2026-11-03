"""M13 Telegram delivery: ledger, pool diff, message text, deliver_week."""
import json

import pytest

from src.data import prosperity_delivery as d


# --- T2: ledger -----------------------------------------------------------------------------

def test_missing_ledger_reads_empty(tmp_path):
    assert d.read_ledger(tmp_path, '2026-11-14') == {'as_of': '2026-11-14', 'items': {}}


def test_ledger_round_trip_and_atomic_failure_keeps_old(tmp_path, monkeypatch):
    first = {'as_of': '2026-11-14', 'items': {'web': {'sent_at': '2026-11-14T08:40:00Z'}}}
    d.write_ledger(tmp_path, first)
    assert d.read_ledger(tmp_path, '2026-11-14') == first

    def broken(*a, **kw):
        raise OSError('disk full')
    monkeypatch.setattr(d.os, 'replace', broken)
    with pytest.raises(OSError):
        d.write_ledger(tmp_path, {'as_of': '2026-11-14', 'items': {}})
    assert d.read_ledger(tmp_path, '2026-11-14') == first
    assert [p.name for p in d.ledger_path(tmp_path, 'x').parent.iterdir()] == ['2026-11-14.json']


def test_previous_ledger_is_latest_before_as_of(tmp_path):
    for as_of in ('2026-10-31', '2026-11-07', '2026-11-21'):
        d.write_ledger(tmp_path, {'as_of': as_of, 'items': {}, 'pool_members': [as_of]})
    assert d.previous_ledger(tmp_path, '2026-11-14')['as_of'] == '2026-11-07'
    assert d.previous_ledger(tmp_path, '2026-10-31') is None


def test_previous_ledger_skips_weeks_without_pool_members(tmp_path):
    d.write_ledger(tmp_path, {'as_of': '2026-10-31', 'items': {}, 'pool_members': ['A']})
    d.write_ledger(tmp_path, {'as_of': '2026-11-07', 'items': {}})          # pool text never sent
    assert d.previous_ledger(tmp_path, '2026-11-14')['as_of'] == '2026-10-31'


# --- T2: pool diff and text -----------------------------------------------------------------

def test_pool_diff():
    assert d.pool_diff(None, ['A', 'B']) == ([], [])
    assert d.pool_diff(['A', 'B', 'C'], ['D', 'B', 'A']) == (['D'], ['C'])


def test_md_escape():
    assert d.md_escape('BRK_B *x* [y] `z`') == 'BRK\\_B \\*x\\* \\[y] \\`z\\`'


def test_web_caption_budget_warning():
    ok = d.web_caption(as_of='2026-11-14', strict=55, full=194, pool_count=36, size_bytes=12_000_000)
    assert ok.startswith('景气榜 2026-11-14') and 'STRICT 55' in ok and 'FULL 194' in ok and '入池 36' in ok
    assert '⚠' not in ok
    big = d.web_caption(as_of='2026-11-14', strict=55, full=194, pool_count=36, size_bytes=12_000_001)
    assert '⚠ 网页 12.0 MB，超出 12 MB 预算' in big
    assert len(big) <= 1024


def test_pool_text_lists_changes():
    text = d.pool_text(as_of='2026-11-14', previous_as_of='2026-11-07',
                       added=['BRK_B', 'MU'], removed=['XOM'], members=36, reason=None)
    assert '*景气精选池 2026-11-14*' in text
    assert '入池 36 只' in text
    assert '新进（2）: BRK\\_B, MU' in text and '移出（1）: XOM' in text
    assert '对比 2026-11-07' in text


def test_pool_text_first_week_and_unavailable():
    first = d.pool_text(as_of='2026-11-14', previous_as_of=None, added=[], removed=[], members=36, reason=None)
    assert '无上周对比' in first
    same = d.pool_text(as_of='2026-11-14', previous_as_of='2026-11-07', added=[], removed=[],
                       members=36, reason=None)
    assert '与上周相同' in same
    bad = d.pool_text(as_of='2026-11-14', previous_as_of=None, added=[], removed=[], members=0,
                      reason='prosperity_pool_stale')
    assert '池文件不可用（prosperity\\_pool\\_stale）' in bad and '新进' not in bad

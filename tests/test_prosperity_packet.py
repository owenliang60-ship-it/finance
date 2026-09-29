from dataclasses import replace

from src.data.market_store import MarketStore
from terminal.prosperity.packet import build_packet, packet_leaks
from tests.prosperity_fixtures import closes_series, er, hist, seed_db

EARN = [er("2025-03-31", "2025-04-30", 1.0, 0.9), er("2025-06-30", "2025-07-30", 1.1, 1.0),
        er("2025-09-30", "2025-10-29", 1.2, 1.1), er("2025-12-31", "2026-02-19", 1.3, 1.2)]


def _usd_history():
    return replace(hist(earnings=EARN), closes=closes_series("2025-06-01", "2026-03-06", 100.0),
                   market_caps=[("2026-03-06", 3e10)],
                   profile={"sector": "Technology", "industry": "Semiconductors"})


BENCH = closes_series("2025-06-01", "2026-03-06", 500.0, drift=0.0005, phase=0.4)


def test_packet_for_usd_name_has_aligned_inputs_and_no_leaks():
    p = build_packet(_usd_history(), "2026-03-07", mode="replay", membership_basis="approximate_mcap",
                     benchmark_closes=BENCH)
    assert p.current_fiscal == "2025-12-31" and p.quarter_bucket == "2025Q4" and p.data_age_days == 66
    assert p.eps[-1].fiscal_date == "2025-12-31"
    assert (p.consensus.pre_announce.source, p.consensus.pre_announce.value) == ("vendor_estimate", 1.2)
    assert p.pit == {"statements": "approximate", "street_eps": "approximate", "consensus": "approximate"}
    assert p.pit_basis == "approximate" and p.sector == "Technology"
    assert p.beta is not None and p.price_date == "2026-03-06" and p.market_cap_asof == 3e10
    assert packet_leaks(p) == []


def test_live_mode_labels_every_source_live():
    p = build_packet(_usd_history(), "2026-03-07", mode="live", observed_at="2026-03-07", membership_basis="approximate_mcap",
                     benchmark_closes=BENCH)
    assert p.pit_basis == "live" and set(p.pit.values()) == {"live"}


def test_non_usd_reporter_gets_unit_unverified_but_keeps_ratios():
    h = _usd_history()
    for table in (h.income, h.balance, h.cashflow):
        for r in table:
            r["reported_currency"] = "EUR"
    p = build_packet(h, "2026-03-07", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert "unit_unverified" in p.flags and p.consensus.pre_announce.value is None
    assert p.consensus.missing_reasons["pre_announce"] == "unit_unverified"
    assert p.quarters[-1].revenue == 100.0 and p.beta is None


def test_leak_detector_catches_future_price_date():
    p = build_packet(_usd_history(), "2026-03-07", mode="replay", membership_basis="approximate_mcap",
                     benchmark_closes=BENCH)
    assert packet_leaks(replace(p, price_date="2026-03-09"))


def test_build_packets_reuses_cache_and_isolates_symbol_errors(tmp_path, monkeypatch):
    import terminal.prosperity.inputs as inputs
    store = MarketStore(db_path=seed_db(tmp_path / "m.db"), read_only=True)
    calls, real = [], inputs.load_history

    def spy(store, symbol, *, with_vintage):
        calls.append(symbol)
        if symbol == "BBB":
            raise ValueError("boom")
        return real(store, symbol, with_vintage=with_vintage)

    monkeypatch.setattr(inputs, "load_history", spy)
    cache = {}
    packets, meta = inputs.build_packets(store, "2026-09-26", mode="live", observed_at="2026-09-26", cache=cache,
                                         with_beta=False)
    assert [p.symbol for p in packets] == ["AAA"] and meta["errors"] == {"BBB": "ValueError: boom"}
    assert meta["members_resolved"] == 2 and meta["membership_basis"] == "extended_membership"
    inputs.build_packets(store, "2026-09-19", mode="replay", cache=cache, with_beta=False)
    assert calls.count("AAA") == 1

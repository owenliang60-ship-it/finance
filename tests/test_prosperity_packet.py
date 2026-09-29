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


def test_consensus_without_any_estimate_is_not_labelled_strict():
    h = _usd_history()
    h.earnings = [dict(r, eps_estimated=None) for r in EARN]
    p = build_packet(h, "2026-03-07", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert p.consensus.pre_announce.source is None and p.pit["consensus"] == "approximate"


KLAC_CLOSES = [("2026-06-10", 2400.0), ("2026-06-11", 2411.64), ("2026-06-12", 254.54), ("2026-06-15", 256.0)]   # market.db


def test_unadjusted_price_split_is_rescaled_in_memory():
    h = replace(_usd_history(), closes=KLAC_CLOSES, splits=[{"date": "2026-06-12", "numerator": 10.0, "denominator": 1.0}])
    p = build_packet(h, "2026-06-11", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert abs(p.price_asof - 241.164) < 1e-9 and "price_split_rescaled" in p.flags
    adjusted = replace(h, closes=[("2026-06-11", 241.16), ("2026-06-12", 254.54)])
    q = build_packet(adjusted, "2026-06-11", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert q.price_asof == 241.16 and "price_split_rescaled" not in q.flags


def test_replayed_price_and_ttm_eps_share_the_post_split_unit():
    # Codex M4 review F1 (KLAC, 2024-12-31): price 63.012 was divided by 10 while TTM EPS mixed units (18.753)
    from tests.test_prosperity_street_eps import KLAC
    from tests.prosperity_fixtures import gaap_rows
    closes = [("2024-12-30", 630.12), ("2024-12-31", 630.12)] + KLAC_CLOSES
    base = replace(_usd_history(), **{k: v for k, v in vars(hist(rows=[
        ("2024-03-31", "2024", "Q1", "2024-05-01 16:00:00"), ("2024-06-30", "2024", "Q2", "2024-07-31 16:00:00"),
        ("2024-09-30", "2024", "Q3", "2024-10-30 16:00:00")])).items() if k in ("income", "balance", "cashflow")})
    statements = {r["date"]: r for r in base.income}
    income = [dict(statements.get(g["date"], {}), **g) for g in gaap_rows(KLAC, 10.0, "2024-06-30")]
    h = replace(base, earnings=KLAC, income=income, closes=closes,
                splits=[{"date": "2026-06-12", "numerator": 10.0, "denominator": 1.0}])
    p = build_packet(h, "2024-12-31", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert abs(p.price_asof - 63.012) < 1e-9 and "price_split_rescaled" in p.flags
    assert abs(p.consensus.ttm_eps - (0.616 + 0.526 + 0.660 + 0.733)) < 1e-9


def test_non_finite_split_metadata_is_ignored_not_fatal():
    # Codex M4 review F6: numerator=inf used to raise OverflowError and drop the whole symbol
    bad = [{"date": "2026-06-12", "numerator": float("inf"), "denominator": 1.0},
           {"date": "2026-06-12", "numerator": 10.0, "denominator": float("nan")}]
    h = replace(_usd_history(), closes=KLAC_CLOSES, splits=bad)
    p = build_packet(h, "2026-06-11", mode="replay", membership_basis="approximate_mcap", benchmark_closes=[])
    assert p.price_asof == 2411.64 and "price_split_rescaled" not in p.flags


def test_requested_non_members_are_reported(tmp_path):
    import terminal.prosperity.inputs as inputs
    store = MarketStore(db_path=seed_db(tmp_path / "m.db"), read_only=True)
    packets, meta = inputs.build_packets(store, "2026-09-26", mode="live", observed_at="2026-09-26",
                                         symbols=["AAA", "ZZZ"], with_beta=False)
    assert [p.symbol for p in packets] == ["AAA"] and meta["requested_not_members"] == ["ZZZ"]

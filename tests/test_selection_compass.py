import pytest
import pandas as pd


def _ema_price_frame(*, last_close=110.0, rows=40, end="2026-09-03"):
    return pd.DataFrame({
        "date": pd.bdate_range(end=end, periods=rows),
        "close": [100.0] * (rows - 1) + [last_close],
    })


@pytest.mark.parametrize("last_close,passes", [(110.0, True), (100.0, False), (90.0, False)])
def test_ema30_requires_current_close_strictly_above_inclusive_ema(last_close, passes):
    from terminal.selection_compass import _evaluate_ema30

    result = _evaluate_ema30(_ema_price_frame(last_close=last_close), as_of="2026-09-03")

    assert result["ready"] is True
    assert result["passes"] is passes
    assert result["close"] == last_close
    assert result["ema30"] == pytest.approx(100 + (2 / 31) * (last_close - 100), abs=1e-12)


@pytest.mark.parametrize("date_index", [False, True])
def test_ema30_sorts_dates_and_matches_independent_recursive_calculation(date_index):
    from terminal.selection_compass import _evaluate_ema30

    frame = _ema_price_frame(rows=60)
    frame["close"] = [100 + i / 3 + (i % 4) for i in range(60)]
    expected = frame["close"].iloc[0]
    for close in frame["close"].iloc[1:]:
        expected = expected + (2 / 31) * (close - expected)
    frame = frame.iloc[::-1]
    if date_index:
        frame = frame.set_index("date")

    result = _evaluate_ema30(frame, as_of="2026-09-03")

    assert result["ready"] and result["passes"]
    assert result["ema30"] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("defect", ["missing_close", "nan", "inf", "nonpositive", "short", "duplicate", "invalid_date", "stale", "future"])
def test_ema30_invalid_prices_are_not_ready(defect):
    from terminal.selection_compass import _evaluate_ema30

    frame = _ema_price_frame()
    if defect == "missing_close":
        frame = frame.drop(columns="close")
    elif defect in {"nan", "inf", "nonpositive"}:
        frame.loc[10, "close"] = {"nan": float("nan"), "inf": float("inf"), "nonpositive": 0.0}[defect]
    elif defect == "short":
        frame = frame.tail(29)
    elif defect == "duplicate":
        frame.loc[0, "date"] = frame.loc[1, "date"]
    elif defect == "invalid_date":
        frame.loc[0, "date"] = pd.NaT
    elif defect == "stale":
        frame = _ema_price_frame(end="2026-09-02")
    elif defect == "future":
        frame = _ema_price_frame(end="2026-09-04")

    assert _evaluate_ema30(frame, as_of="2026-09-03") == {
        "ready": False, "passes": False, "close": None, "ema30": None,
    }

from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
from terminal.selection_compass import (
    _eps_leg,
    _evaluate_cagr_growth,
    _evaluate_eps_pair,
    _evaluate_rvol,
    _raw_inputs_ready,
    scan_selection_compass,
)


@pytest.mark.parametrize(
    ("current", "comparison", "passes", "growth", "turnaround"),
    [
        (1.20, 1.00, True, 0.20, False),
        (1.199, 1.00, False, 0.199, False),
        (0.50, -0.25, True, None, True),
        (0.50, 0.00, True, None, True),
        (-0.50, -1.00, False, None, False),
    ],
)
def test_eps_leg_frozen_business_rules(
    current, comparison, passes, growth, turnaround
):
    result = _eps_leg(current, comparison)

    assert result["passes"] is passes
    assert result["growth"] == pytest.approx(growth) if growth is not None else result["growth"] is None
    assert result["turnaround"] is turnaround


@pytest.mark.parametrize(
    ("current", "prior", "yoy", "passes"),
    [
        (1.20, 1.00, 1.00, True),
        (1.199, 0.90, 1.00, False),
        (1.30, 1.10, 1.00, False),
    ],
)
def test_eps_yoy_and_qoq_must_both_pass(current, prior, yoy, passes):
    result = _evaluate_eps_pair(current=current, prior=prior, yoy=yoy)

    assert result["passes"] is passes
    assert result["eps_yoy_growth"] == pytest.approx((current - yoy) / abs(yoy))
    assert result["eps_qoq_growth"] == pytest.approx((current - prior) / abs(prior))


@pytest.mark.parametrize(
    ("revenue", "net_income", "passes", "average"),
    [
        (0.10, 0.10, True, 0.10),
        (0.199, 0.00, False, 0.0995),
        (None, 0.30, False, None),
        (0.30, None, False, None),
    ],
)
def test_cagr_growth_uses_exact_average_and_rejects_missing(
    revenue, net_income, passes, average
):
    result = _evaluate_cagr_growth(revenue, net_income)

    assert result["passes"] is passes
    if average is None:
        assert result["growth_avg_4q"] is None
    else:
        assert result["growth_avg_4q"] == pytest.approx(average)


def _price_frame(*, end="2026-09-03", rows=127, reverse=False):
    dates = pd.bdate_range(end=end, periods=rows)
    baseline = [90.0, 110.0] * 60
    tail = [100.0, 100.0, 100.0, 150.0, 100.0, 125.0, 100.0]
    volumes = (baseline + tail)[-rows:]
    frame = pd.DataFrame({
        "date": dates, "volume": volumes,
        "close": [100.0 + i / 10 for i in range(rows)],
    })
    return frame.iloc[::-1].reset_index(drop=True) if reverse else frame


def test_rvol_uses_max_of_recent_seven_and_reports_trigger_date():
    result = _evaluate_rvol(
        _price_frame(reverse=True),
        as_of="2026-09-03",
    )

    assert result["ready"] is True
    assert result["passes"] is True
    assert result["rvol_max_7d"] >= 2.0
    assert result["rvol_trigger_date"] == "2026-08-31"


def test_rvol_accepts_existing_date_index_price_frame_contract():
    frame = _price_frame().set_index("date")

    result = _evaluate_rvol(frame, as_of="2026-09-03")

    assert result["ready"] is True
    assert result["passes"] is True


@pytest.mark.parametrize(
    "frame,as_of",
    [
        (_price_frame(rows=126), "2026-09-03"),
        (_price_frame(end="2026-09-02"), "2026-09-03"),
    ],
)
def test_rvol_not_ready_for_insufficient_or_stale_price_frame(frame, as_of):
    result = _evaluate_rvol(frame, as_of=as_of)

    assert result == {
        "ready": False,
        "passes": False,
        "rvol_max_7d": None,
        "rvol_trigger_date": None,
    }


def _income_rows():
    return [
        {
            "date": "2026-06-30",
            "period": "Q2",
            "fiscal_year": "2026",
            "eps_diluted": 1.60,
            "revenue": 150.0,
            "net_income": 45.0,
        },
        {
            "date": "2026-03-31",
            "period": "Q1",
            "fiscal_year": "2026",
            "eps_diluted": 1.20,
            "revenue": 130.0,
            "net_income": 35.0,
        },
        {
            "date": "2025-12-31",
            "period": "Q4",
            "fiscal_year": "2025",
            "eps_diluted": 1.10,
            "revenue": 120.0,
            "net_income": 32.0,
        },
        {
            "date": "2025-09-30",
            "period": "Q3",
            "fiscal_year": "2025",
            "eps_diluted": 1.00,
            "revenue": 100.0,
            "net_income": 30.0,
        },
        {
            "date": "2025-06-30",
            "period": "Q2",
            "fiscal_year": "2025",
            "eps_diluted": 1.00,
            "revenue": 95.0,
            "net_income": 25.0,
        },
    ]


@pytest.mark.parametrize(
    ("row_index", "field"),
    [
        (0, "eps_diluted"),
        (1, "eps_diluted"),
        (4, "eps_diluted"),
        (0, "revenue"),
        (3, "net_income"),
    ],
)
def test_raw_inputs_not_ready_when_required_eps_or_cagr_endpoint_is_null(
    row_index, field
):
    rows = _income_rows()
    rows[row_index][field] = None

    assert _raw_inputs_ready(rows)["ready"] is False


def test_raw_inputs_require_exact_yoy_period_and_prior_fiscal_year():
    rows = _income_rows()
    rows[4]["period"] = "Q1"

    assert _raw_inputs_ready(rows)["ready"] is False


def test_raw_inputs_reject_quarter_gap_above_shared_maximum():
    rows = _income_rows()
    rows[0]["date"] = (
        pd.Timestamp(rows[1]["date"])
        + pd.Timedelta(days=FUNDAMENTAL_QUARTER_GAP_MAX_DAYS + 1)
    ).date().isoformat()

    assert _raw_inputs_ready(rows)["ready"] is False


@pytest.mark.parametrize(
    ("row_index", "period"),
    [
        (1, "Q2"),  # duplicate current label instead of prior Q1
        (3, "Q2"),  # skip/misorder the Q-3 label instead of Q3
    ],
)
def test_raw_inputs_require_sequential_fiscal_quarter_labels(row_index, period):
    rows = _income_rows()
    rows[row_index]["period"] = period

    assert _raw_inputs_ready(rows)["ready"] is False


def test_nonpositive_net_income_base_is_ready_but_cagr_fails_business_gate():
    rows = _income_rows()
    rows[3]["net_income"] = -10.0

    raw = _raw_inputs_ready(rows)

    assert raw["ready"] is True
    assert _evaluate_cagr_growth(0.20, None)["passes"] is False


class _FakeStore:
    def __init__(self, *, incomes, metrics, coverage):
        self.incomes = incomes
        self.metrics = metrics
        self.coverage = coverage

    def get_income(self, symbol, limit=20):
        return self.incomes.get(symbol, [])[:limit]

    def get_metrics(self, symbol, limit=8):
        return self.metrics.get(symbol, [])[:limit]

    def get_coverage(self, dataset):
        assert dataset == "income_quarterly"
        return self.coverage


def _metric(*, date="2026-06-30", revenue_cagr=0.09, net_income_cagr=0.09):
    return {
        "date": date,
        "revenue_cagr_4q": revenue_cagr,
        "net_income_cagr_4q": net_income_cagr,
    }


def _store_for(symbols):
    return _FakeStore(
        incomes={symbol: _income_rows() for symbol in symbols},
        metrics={symbol: [_metric()] for symbol in symbols},
        coverage={symbol: "ok" for symbol in symbols},
    )


def _betas(symbols, value=1.25):
    return {symbol: value for symbol in symbols}


def test_coverage_uses_exact_deduplicated_extended_denominator():
    symbols = ["AAA", "BBB", "AAA"]
    store = _store_for({"AAA", "BBB"})
    prices = {symbol: _price_frame() for symbol in {"AAA", "BBB"}}

    result = scan_selection_compass(
        store=store,
        symbols=symbols,
        as_of="2026-09-03",
        price_frames=prices,
        market_cap_observations={},
        beta_observations=_betas({"AAA", "BBB"}),
    )

    assert result["available"] is True
    assert result["coverage"] == {
        "fundamental_ready": {"covered": 2, "total": 2, "ratio": 1.0},
        "rvol_ready": {"covered": 2, "total": 2, "ratio": 1.0},
        "ema30_ready": {"covered": 2, "total": 2, "ratio": 1.0},
        "beta_ready": {"covered": 2, "total": 2, "ratio": 1.0},
    }
    assert result["hits"] == []


@pytest.mark.parametrize("defect", ["status", "stale", "metrics_mismatch"])
def test_fundamental_readiness_rejects_status_staleness_and_metrics_mismatch(defect):
    store = _store_for(["AAA"])
    if defect == "status":
        store.coverage["AAA"] = "fetch_failed"
    elif defect == "stale":
        for row in store.incomes["AAA"]:
            row["date"] = (pd.Timestamp(row["date"]) - pd.Timedelta(days=250)).date().isoformat()
        store.metrics["AAA"][0]["date"] = store.incomes["AAA"][0]["date"]
    else:
        store.metrics["AAA"][0]["date"] = "2026-03-31"

    result = scan_selection_compass(
        store=store,
        symbols=["AAA"],
        as_of="2026-09-03",
        price_frames={"AAA": _price_frame()},
        market_cap_observations={},
        beta_observations=_betas(["AAA"]),
    )

    assert result["available"] is False
    assert result["reason"] == "fundamental_coverage_below_threshold"
    assert result["coverage"]["fundamental_ready"]["covered"] == 0
    assert result["coverage"]["rvol_ready"]["covered"] == 1
    assert result["hits"] == []


def test_fundamental_and_rvol_readiness_are_measured_separately():
    store = _store_for(["FUND", "RVOL"])
    store.coverage["RVOL"] = "fetch_failed"

    result = scan_selection_compass(
        store=store,
        symbols=["FUND", "RVOL"],
        as_of="2026-09-03",
        price_frames={"RVOL": _price_frame()},
        market_cap_observations={},
        beta_observations=_betas(["FUND", "RVOL"]),
    )

    assert result["available"] is False
    assert result["coverage"]["fundamental_ready"] == {
        "covered": 1,
        "total": 2,
        "ratio": 0.5,
    }
    assert result["coverage"]["rvol_ready"] == {
        "covered": 1,
        "total": 2,
        "ratio": 0.5,
    }
    assert result["hits"] == []


@pytest.mark.parametrize(("covered", "available"), [(19, True), (18, False)])
def test_coverage_threshold_is_fail_closed_below_95_percent(covered, available):
    symbols = [f"S{i:02d}" for i in range(20)]
    store = _store_for(symbols)
    for symbol in symbols[covered:]:
        store.coverage[symbol] = "fetch_failed"
    prices = {symbol: _price_frame() for symbol in symbols}

    result = scan_selection_compass(
        store=store,
        symbols=symbols,
        as_of="2026-09-03",
        price_frames=prices,
        market_cap_observations={},
        beta_observations=_betas(symbols),
    )

    assert result["available"] is available
    assert result["coverage"]["fundamental_ready"]["ratio"] == pytest.approx(
        covered / 20
    )
    assert result["hits"] == []


def _passing_store(symbols):
    store = _store_for(symbols)
    store.metrics = {
        symbol: [_metric(revenue_cagr=0.20, net_income_cagr=0.20)]
        for symbol in symbols
    }
    return store


def _turnaround_store():
    store = _passing_store(["BE"])
    # Real BE shape (USD millions), newest first: revenue dips in the middle.
    for row, revenue, income in zip(
        store.incomes["BE"],
        [1065.365, 751.054, 777.683, 519.048, 401.242],
        [196.290, 70.653, 1.091, -23.093, -42.619],
    ):
        row.update(revenue=revenue, net_income=income)
    store.incomes["BE"][0]["eps_diluted"] = 0.62
    store.incomes["BE"][1]["eps_diluted"] = 0.23
    store.incomes["BE"][4]["eps_diluted"] = -0.18
    store.metrics["BE"] = [_metric(revenue_cagr=0.2708578, net_income_cagr=None)]
    return store


def _scan_turnaround(store, frame=None):
    return scan_selection_compass(
        store=store, symbols=["BE"], as_of="2026-09-03",
        price_frames={"BE": _price_frame() if frame is None else frame},
        market_cap_observations={"BE": {"date": "2026-09-03", "market_cap": 1e10}},
        beta_observations={"BE": 1.0},
    )


def test_turnaround_allows_interior_decline_and_undefined_cagr():
    result = _scan_turnaround(_turnaround_store())
    assert result["available"] is True
    hit, = result["hits"]
    assert hit["growth_route"] == "turnaround"
    assert hit["net_income_cagr_4q"] is None
    assert hit["growth_avg_4q"] is None
    assert hit["eps_yoy_turnaround"] is True
    assert hit["eps_qoq_growth"] == pytest.approx(0.62 / 0.23 - 1)


@pytest.mark.parametrize("comparison_income", [-10, 0])
def test_turnaround_in_oldest_target_quarter_counts(comparison_income):
    store = _turnaround_store()
    for row, income in zip(store.incomes["BE"], [45, 35, 32, 30, comparison_income]):
        row["net_income"] = income
    store.metrics["BE"] = [_metric(revenue_cagr=0.01, net_income_cagr=0.01)]
    hit, = _scan_turnaround(store)["hits"]
    assert hit["growth_route"] == "turnaround"


@pytest.mark.parametrize("field,latest", [
    ("revenue", 519.048), ("revenue", 500),
    ("net_income", -23.093), ("net_income", -25), ("net_income", -1),
])
def test_turnaround_requires_both_endpoints_up_and_current_profit_no_cagr_fallback(field, latest):
    store = _turnaround_store()
    store.incomes["BE"][0][field] = latest
    store.metrics["BE"] = [_metric(revenue_cagr=0.50, net_income_cagr=0.50)]
    result = _scan_turnaround(store)
    assert result["available"] is True
    assert result["hits"] == []


@pytest.mark.parametrize("cagr,passes", [(0.099, False), (0.10, True)])
def test_no_turnaround_retains_cagr_threshold(cagr, passes):
    store = _passing_store(["BE"])
    store.metrics["BE"] = [_metric(revenue_cagr=cagr, net_income_cagr=cagr)]
    result = _scan_turnaround(store)
    assert bool(result["hits"]) is passes
    if passes:
        assert result["hits"][0]["growth_route"] == "cagr"
        assert result["hits"][0]["growth_avg_4q"] == pytest.approx(cagr)


def test_turnaround_positive_latest_income_must_exceed_positive_start():
    store = _turnaround_store()
    for row, income in zip(store.incomes["BE"], [45, 35, -10, 50, 40]):
        row["net_income"] = income
    store.metrics["BE"] = [_metric(revenue_cagr=0.50, net_income_cagr=0.50)]
    assert _scan_turnaround(store)["hits"] == []


@pytest.mark.parametrize("index,value", [(1, None), (2, float("nan")), (4, float("inf"))])
def test_unknown_turnaround_history_is_not_ready(index, value):
    store = _turnaround_store()
    store.incomes["BE"][index]["net_income"] = value
    result = _scan_turnaround(store)
    assert result["available"] is False
    assert result["coverage"]["fundamental_ready"]["covered"] == 0


@pytest.mark.parametrize("gate", ["eps_yoy", "eps_qoq", "rvol", "ema30"])
def test_turnaround_does_not_bypass_other_gates(gate):
    store = _turnaround_store()
    frame = _price_frame()
    if gate == "eps_yoy":
        store.incomes["BE"][4]["eps_diluted"] = 0.60
    elif gate == "eps_qoq":
        store.incomes["BE"][1]["eps_diluted"] = 0.60
    elif gate == "rvol":
        frame["volume"] = [90.0, 110.0] * 63 + [100.0]
    else:
        frame["close"] = 100.0
    result = _scan_turnaround(store, frame)
    assert result["available"] is True
    assert result["hits"] == []


@pytest.mark.parametrize(
    "observation",
    [
        None,
        {"date": "2026-08-26", "market_cap": 1_000_000_000},
        {"date": "2026-09-04", "market_cap": 1_000_000_000},
    ],
)
def test_missing_stale_or_future_market_cap_for_a_hit_fails_closed(observation):
    observations = {} if observation is None else {"AAA": observation}

    result = scan_selection_compass(
        store=_passing_store(["AAA"]),
        symbols=["AAA"],
        as_of="2026-09-03",
        price_frames={"AAA": _price_frame()},
        market_cap_observations=observations,
        beta_observations={"AAA": 1.0},
    )

    assert result["available"] is False
    assert result["reason"] == "market_cap_unavailable"
    assert result["hits"] == []
    assert result["market_cap_unavailable_symbols"] == ["AAA"]


def test_market_cap_at_seven_day_freshness_boundary_is_accepted():
    result = scan_selection_compass(
        store=_passing_store(["AAA"]),
        symbols=["AAA"],
        as_of="2026-09-03",
        price_frames={"AAA": _price_frame()},
        market_cap_observations={
            "AAA": {"date": "2026-08-27", "marketCap": 1_000_000_000}
        },
        beta_observations={"AAA": 1.0},
    )

    assert result["available"] is True
    assert result["hits"][0]["marketCap"] == 1_000_000_000


def test_hits_sort_by_market_cap_desc_then_symbol_for_ties():
    symbols = ["CCC", "AAA", "BBB"]
    observations = {
        "AAA": {"date": "2026-09-03", "market_cap": 1_000_000_000},
        "BBB": {"date": "2026-09-03", "market_cap": 2_000_000_000},
        "CCC": {"date": "2026-09-03", "market_cap": 2_000_000_000},
    }

    result = scan_selection_compass(
        store=_passing_store(symbols),
        symbols=symbols,
        as_of="2026-09-03",
        price_frames={symbol: _price_frame() for symbol in symbols},
        market_cap_observations=observations,
        beta_observations=_betas(symbols),
    )

    assert result["available"] is True
    assert [hit["symbol"] for hit in result["hits"]] == ["BBB", "CCC", "AAA"]
    assert [hit["marketCap"] for hit in result["hits"]] == [
        2_000_000_000,
        2_000_000_000,
        1_000_000_000,
    ]


def test_scanner_adds_strict_ema30_gate_and_keeps_rvol_gate():
    symbols = ["ABOVE", "EQUAL", "BELOW", "NO_RVOL"]
    prices = {symbol: _price_frame() for symbol in symbols}
    for symbol, final in [("ABOVE", 110), ("EQUAL", 100), ("BELOW", 90)]:
        prices[symbol]["close"] = [100.0] * 126 + [final]
    prices["NO_RVOL"]["volume"] = [100.0] * 127

    result = scan_selection_compass(
        store=_passing_store(symbols), symbols=symbols, as_of="2026-09-03",
        price_frames=prices,
        market_cap_observations={s: {"date": "2026-09-03", "market_cap": 1e10} for s in symbols},
        beta_observations=_betas(symbols),
    )

    assert result["available"] is True
    assert [hit["symbol"] for hit in result["hits"]] == ["ABOVE"]
    assert result["hits"][0]["close"] == 110
    assert result["hits"][0]["ema30"] == pytest.approx(100 + 20 / 31)
    assert result["coverage"]["ema30_ready"] == {"covered": 4, "total": 4, "ratio": 1.0}


@pytest.mark.parametrize("covered,available", [(19, True), (18, False)])
def test_ema30_price_coverage_is_separate_and_fails_closed_below_95_percent(covered, available):
    symbols = [f"S{i:02d}" for i in range(20)]
    prices = {symbol: _price_frame() for symbol in symbols}
    for symbol in symbols[covered:]:
        prices[symbol] = prices[symbol].drop(columns="close")

    result = scan_selection_compass(
        store=_store_for(symbols), symbols=symbols, as_of="2026-09-03",
        price_frames=prices, market_cap_observations={}, beta_observations=_betas(symbols),
    )

    assert result["available"] is available
    assert result["coverage"]["fundamental_ready"]["covered"] == 20
    assert result["coverage"]["rvol_ready"]["covered"] == 20
    assert result["coverage"]["ema30_ready"]["covered"] == covered
    assert result["hits"] == []
    if not available:
        assert result["reason"] == "ema30_coverage_below_threshold"


def test_beta_gate_accepts_exact_one_and_rejects_below_one():
    symbols = ["EXACT", "LOW"]
    result = scan_selection_compass(
        store=_passing_store(symbols), symbols=symbols, as_of="2026-09-03",
        price_frames={s: _price_frame() for s in symbols},
        market_cap_observations={s: {"date": "2026-09-03", "market_cap": 1e10}
                                 for s in symbols},
        beta_observations={"EXACT": 1.0, "LOW": 0.999},
    )
    assert result["available"] is True
    assert [hit["symbol"] for hit in result["hits"]] == ["EXACT"]
    assert result["hits"][0]["beta_6m"] == 1.0


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), "bad"])
def test_invalid_beta_excludes_symbol_but_95_percent_coverage_remains_available(invalid):
    symbols = [f"S{i:02d}" for i in range(20)]
    betas = _betas(symbols)
    betas[symbols[-1]] = invalid
    result = scan_selection_compass(
        store=_passing_store(symbols), symbols=symbols, as_of="2026-09-03",
        price_frames={s: _price_frame() for s in symbols},
        market_cap_observations={s: {"date": "2026-09-03", "market_cap": 1e10}
                                 for s in symbols},
        beta_observations=betas,
    )
    assert result["available"] is True
    assert result["coverage"]["beta_ready"] == {
        "covered": 19, "total": 20, "ratio": 0.95,
    }
    assert len(result["hits"]) == 19
    assert symbols[-1] not in {hit["symbol"] for hit in result["hits"]}


def test_beta_coverage_below_95_percent_fails_closed():
    symbols = [f"S{i:02d}" for i in range(20)]
    result = scan_selection_compass(
        store=_passing_store(symbols), symbols=symbols, as_of="2026-09-03",
        price_frames={s: _price_frame() for s in symbols},
        market_cap_observations={}, beta_observations=_betas(symbols[:18]),
    )
    assert result["available"] is False
    assert result["reason"] == "beta_coverage_below_threshold"
    assert result["hits"] == []

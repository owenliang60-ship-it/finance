"""T20 B4 — RS backtest CLI exposes the two established extended variants."""


def test_universe_choices_include_extended_true_and_eligible_extended():
    from scripts.run_rs_backtest import build_parser

    parser = build_parser()
    assert parser.parse_args(["--universe", "extended_true"]).universe == "extended_true"
    assert parser.parse_args(["--universe", "eligible_extended"]).universe == "eligible_extended"

import json, pathlib, pytest

from src.data.security_master import classify_security, resolve_share_classes

FIX = pathlib.Path(__file__).parent / "fixtures" / "fmp_profiles"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_contract_real_payload_fields():
    p = _load("AAPL.json")
    rec = classify_security(p)
    assert rec.eligible and rec.reason == "ok" and rec.cik


def test_adr_is_eligible():
    rec = classify_security(_load("TSM.json"))
    assert rec.eligible is True and rec.is_adr in (True, False)  # ADR 标志仅记录，绝不 block


def test_etf_blocked():
    p = _load("AAPL.json"); p = dict(p, symbol="SOXX", isEtf=True)
    assert classify_security(p).reason == "etf"


def test_share_class_override_wins():
    goog, googl = _load("GOOG.json"), _load("GOOGL.json")
    recs = [classify_security(goog), classify_security(googl)]
    out = {r.symbol: r for r in resolve_share_classes(
        recs, overrides={goog["cik"]: "GOOG"},          # Boss 拍板：Alphabet 主类 = GOOG
        profiles_by_symbol={"GOOG": goog, "GOOGL": googl})}
    assert out["GOOG"].eligible is True
    assert out["GOOGL"].reason == "secondary_share_class" and out["GOOGL"].share_class_of == "GOOG"


def test_explicit_override_can_resolve_known_name_mismatch_group():
    common = {"symbol": "GS", "companyName": "The Goldman Sachs Group, Inc.",
              "cik": "886982", "exchange": "NYSE", "isEtf": False,
              "isFund": False, "marketCap": 300}
    preferred = {"symbol": "GS-PA", "companyName": "Goldman Sachs PFD 1/1000 C",
                 "cik": "886982", "exchange": "NYSE", "isEtf": False,
                 "isFund": False, "marketCap": 200}
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(common), classify_security(preferred)],
        overrides={"886982": "GS"},
        profiles_by_symbol={"GS": common, "GS-PA": preferred})}

    assert out["GS"].eligible is True and out["GS"].reason == "ok"
    assert out["GS-PA"].reason == "non_common_instrument"
    assert out["GS-PA"].share_class_of is None


@pytest.mark.parametrize("profile", [
    {
        "symbol": "MER-PK",
        "companyName": (
            "Bank of America Corp 6.45 % Notes 2018-15.12.66 "
            "Income Capital Obligations"
        ),
        "cik": "1382664", "exchange": "NYSE", "isEtf": False,
        "isFund": False, "marketCap": 189_605_812_000,
    },
    {
        "symbol": "SATA", "companyName": "Strive Inc. Perp. Pfd. Series A",
        "cik": "1920406", "exchange": "NASDAQ", "isEtf": False,
        "isFund": False, "marketCap": 69_810_603_859,
    },
    {
        "symbol": "FITB-PM", "companyName": "Fifth Third Bancorp",
        "cik": "35527", "exchange": "NYSE", "isEtf": False,
        "isFund": False, "marketCap": 51_408_207_397,
    },
])
def test_explicit_non_common_instruments_are_blocked_before_grouping(profile):
    rec = classify_security(profile)
    assert rec.eligible is False
    assert rec.reason == "non_common_instrument"


def test_production_overrides_cover_audited_common_equity_groups():
    overrides = json.loads((pathlib.Path(__file__).parents[1]
                            / "config" / "share_class_overrides.json").read_text())
    expected = {
        "0000005513": "UNM", "0000011544": "WRB", "0000072903": "XEL",
        "0000092122": "SO", "0000732717": "T", "0000769218": "AEG",
        "0000798941": "FCNCA", "0000811156": "CMS", "0000874766": "HIG",
        "0000886982": "GS", "0000936340": "DTE", "0001001085": "BN",
        "0001050446": "MSTR", "0001137774": "PRU", "0001166691": "CMCSA",
        "0001267238": "AIZ", "0001326160": "DUK", "0001390777": "BNY",
        "0001404912": "KKR", "0001406234": "BIP", "0001474432": "P",
        "0001506307": "KMI", "0001533232": "BEP", "0001560385": "FWONK",
        "0002089271": "HONA",
    }
    assert expected.items() <= overrides.items()
    assert overrides["0001527469"] is None  # Athene group has no common equity


def test_null_override_blocks_group_without_common_equity_even_as_singleton():
    preferred = {
        "symbol": "ATHS", "companyName": "Athene Holding Ltd. 7.250% Fixed",
        "cik": "1527469", "exchange": "NYSE", "isEtf": False,
        "isFund": False, "marketCap": 20_000_000_000,
    }
    out = resolve_share_classes(
        [classify_security(preferred)], overrides={"1527469": None},
        profiles_by_symbol={"ATHS": preferred})

    assert out[0].eligible is False
    assert out[0].reason == "non_common_instrument"


def test_no_override_falls_to_mktcap_then_needs_review():
    a = {"symbol": "XX-A", "companyName": "Xx Inc.", "cik": "9",
         "exchange": "NYSE", "isEtf": False, "isFund": False}
    b = dict(a, symbol="XX-B")
    recs = [classify_security(a), classify_security(b)]
    out = {r.symbol: r for r in resolve_share_classes(recs, overrides={},
           profiles_by_symbol={"XX-A": a, "XX-B": b})}
    assert {out["XX-A"].reason, out["XX-B"].reason} == {"needs_review_primary"}  # 无数据不拍板


def test_same_cik_different_name_is_identity_conflict():
    a = {"symbol": "AA", "companyName": "Alpha Inc.", "cik": "7",
         "exchange": "NYSE", "isEtf": False, "isFund": False}
    b = {"symbol": "BB", "companyName": "Beta Corp.", "cik": "7",
         "exchange": "NYSE", "isEtf": False, "isFund": False}
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(a), classify_security(b)], overrides={},
        profiles_by_symbol={"AA": a, "BB": b})}
    assert out["AA"].reason == out["BB"].reason == "identity_conflict"


def test_missing_profile_blocks_when_not_etf_or_fund():
    p = {"symbol": "ZZZ", "exchange": "NYSE", "isEtf": False, "isFund": False}
    rec = classify_security(p)
    assert rec.eligible is False and rec.reason == "missing_profile"


def test_mktcap_tiebreak_picks_primary_without_override():
    a = {"symbol": "YY-A", "companyName": "Yy Corp.", "cik": "11",
         "exchange": "NYSE", "isEtf": False, "isFund": False, "marketCap": 100}
    b = {"symbol": "YY-B", "companyName": "Yy Corp.", "cik": "11",
         "exchange": "NYSE", "isEtf": False, "isFund": False, "marketCap": 50}
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(a), classify_security(b)], overrides={},
        profiles_by_symbol={"YY-A": a, "YY-B": b})}
    assert out["YY-A"].reason == "ok" and out["YY-A"].eligible is True
    assert out["YY-B"].reason == "secondary_share_class" and out["YY-B"].share_class_of == "YY-A"


def test_share_class_primary_uses_liquidity_before_issuer_level_market_cap():
    """FMP copies issuer-level market cap onto preferred/depositary classes.

    Production evidence: FITB-PM had a larger reported marketCap than FITB,
    while FITB traded roughly 180x more shares. The common listing must win.
    """
    profiles = {
        "FITB": {
            "symbol": "FITB", "companyName": "Fifth Third Bancorp",
            "cik": "35527", "exchange": "NASDAQ", "isEtf": False,
            "isFund": False, "marketCap": 49_394_004_000,
            "averageVolume": 6_455_520,
        },
        "FITB-PM": {
            "symbol": "FITB-PM", "companyName": "Fifth Third Bancorp",
            "cik": "35527", "exchange": "NYSE", "isEtf": False,
            "isFund": False, "marketCap": 51_408_207_397,
            "averageVolume": 35_709,
        },
        "FITBI": {
            "symbol": "FITBI", "companyName": "Fifth Third Bancorp",
            "cik": "35527", "exchange": "NASDAQ", "isEtf": False,
            "isFund": False, "marketCap": 51_027_235_354,
            "averageVolume": 34_845,
        },
        "FITBM": {
            "symbol": "FITBM", "companyName": "Fifth Third Bancorp",
            "cik": "35527", "exchange": "NASDAQ", "isEtf": False,
            "isFund": False, "marketCap": 23_563_958_782,
            "averageVolume": 57_582,
        },
    }
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(p) for p in profiles.values()], overrides={},
        profiles_by_symbol=profiles)}

    assert out["FITB"].eligible is True and out["FITB"].reason == "ok"
    assert out["FITB-PM"].reason == "non_common_instrument"
    assert out["FITBI"].share_class_of == "FITB"
    assert out["FITBM"].share_class_of == "FITB"


def test_coreweave_class_descriptor_names_normalize_equal_not_identity_conflict():
    # Real FMP payload (main repo, symbol CRWV, cik 0001769628): companyName
    # "CoreWeave, Inc. Class A Common Stock". The suffix descriptor sits
    # mid-string, not at the end — a tail-only strip leaves the two classes
    # with different normalized names and misfires into identity_conflict.
    a = {"symbol": "CRWV", "companyName": "CoreWeave, Inc. Class A Common Stock",
         "cik": "1769628", "exchange": "NASDAQ", "isEtf": False, "isFund": False,
         "marketCap": 100}
    b = {"symbol": "CRWV-B", "companyName": "CoreWeave, Inc. Class B Common Stock",
         "cik": "1769628", "exchange": "NASDAQ", "isEtf": False, "isFund": False,
         "marketCap": 50}
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(a), classify_security(b)], overrides={},
        profiles_by_symbol={"CRWV": a, "CRWV-B": b})}
    assert out["CRWV"].reason == "ok" and out["CRWV"].eligible is True
    assert out["CRWV-B"].reason == "secondary_share_class" and out["CRWV-B"].share_class_of == "CRWV"


def test_wise_group_plc_class_descriptor_names_normalize_equal_not_identity_conflict():
    # Real FMP payload (main repo, symbol WSE, cik 0002099039): companyName
    # "Wise Group plc Class A Ordinary Shares".
    a = {"symbol": "WSE", "companyName": "Wise Group plc Class A Ordinary Shares",
         "cik": "2099039", "exchange": "NASDAQ", "isEtf": False, "isFund": False,
         "marketCap": 100}
    b = {"symbol": "WSE-B", "companyName": "Wise Group plc Class B Ordinary Shares",
         "cik": "2099039", "exchange": "NASDAQ", "isEtf": False, "isFund": False,
         "marketCap": 50}
    out = {r.symbol: r for r in resolve_share_classes(
        [classify_security(a), classify_security(b)], overrides={},
        profiles_by_symbol={"WSE": a, "WSE-B": b})}
    assert out["WSE"].reason == "ok" and out["WSE"].eligible is True
    assert out["WSE-B"].reason == "secondary_share_class" and out["WSE-B"].share_class_of == "WSE"


def test_volavg_is_primary_signal_even_when_reported_market_cap_is_lower():
    # FMP marketCap is issuer-level and can differ nonsensically across share
    # classes. The most liquid listing is therefore the primary signal; mktCap
    # is only a fallback when volume is absent/tied.
    a = {"symbol": "ZA", "companyName": "Zz Corp.", "cik": "42",
         "exchange": "NYSE", "isEtf": False, "isFund": False,
         "marketCap": 100, "averageVolume": 200}
    b = {"symbol": "ZB", "companyName": "Zz Corp.", "cik": "42",
         "exchange": "NYSE", "isEtf": False, "isFund": False,
         "marketCap": 100, "averageVolume": 300}
    c = {"symbol": "ZC", "companyName": "Zz Corp.", "cik": "42",
         "exchange": "NYSE", "isEtf": False, "isFund": False,
         "marketCap": 50, "averageVolume": 500}
    recs = [classify_security(a), classify_security(b), classify_security(c)]
    out = {r.symbol: r for r in resolve_share_classes(
        recs, overrides={}, profiles_by_symbol={"ZA": a, "ZB": b, "ZC": c})}
    assert out["ZC"].reason == "ok" and out["ZC"].eligible is True
    assert out["ZA"].reason == "secondary_share_class" and out["ZA"].share_class_of == "ZC"
    assert out["ZB"].reason == "secondary_share_class" and out["ZB"].share_class_of == "ZC"

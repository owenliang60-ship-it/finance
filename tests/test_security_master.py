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


def test_volavg_tiebreak_restricted_to_mktcap_leaders():
    # A and B are tied at the top mktCap; C has a strictly lower mktCap but
    # the highest volAvg of all three. The brief's cascade is nested: volAvg
    # only breaks a tie AMONG the mktCap leaders, so C (not an mktCap leader)
    # must not win primary despite having the largest volAvg overall.
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
    assert out["ZB"].reason == "ok" and out["ZB"].eligible is True
    assert out["ZA"].reason == "secondary_share_class" and out["ZA"].share_class_of == "ZB"
    assert out["ZC"].reason == "secondary_share_class" and out["ZC"].share_class_of == "ZB"

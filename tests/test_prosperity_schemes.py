import ast
from dataclasses import replace
from pathlib import Path

import pytest

from terminal.prosperity.schemes import (
    EXEMPT_INDUSTRIES, EXFIN_EXCLUDED_INDUSTRIES, FACTOR_SPECS, SCHEMES, SCORING_FACTORS,
    check_scheme_hash, get_scheme, industry_matches, scheme_hash, validate_scheme)
from terminal.prosperity.version import CODE_VERSION_EXCLUDED, CODE_VERSION_SOURCES, REPO_ROOT, code_version

F1_WEIGHTS = {"revenue_accel": .22, "eps_accel": .13, "revenue_yoy": .10, "eps_sue": .15, "growth_4q": .05,
              "gm_yoy": .12, "gm_level": .03, "fcf_margin_yoy": .10, "surprise": .05, "revision": .05}


def test_first_batch_is_the_four_north_star_schemes():
    assert sorted(SCHEMES) == ["F0", "F1", "F1-exfin", "F1-rank"]
    for s in SCHEMES.values():
        validate_scheme(s)
        assert abs(sum(w for _, w in s.weights) - 1.0) < 1e-9


def test_f1_weights_and_family_totals_match_north_star():
    s = get_scheme("F1")
    assert dict(s.weights) == pytest.approx(F1_WEIGHTS)
    assert set(F1_WEIGHTS) == set(SCORING_FACTORS)
    fam = {}
    for f, w in s.weights:
        fam[FACTOR_SPECS[f].family] = fam.get(FACTOR_SPECS[f].family, 0.0) + w
    assert fam == pytest.approx({"accel": .35, "growth": .30, "quality": .25, "expectation": .10})
    assert {f for f, spec in FACTOR_SPECS.items() if spec.params_timing == "weekly"} == {"surprise", "revision"}


def test_variants_differ_only_where_the_north_star_says():
    f1 = get_scheme("F1")
    rank = get_scheme("F1-rank")
    assert rank.transform == "rank_z" and rank.weights == f1.weights
    exfin = get_scheme("F1-exfin")
    assert exfin.weights == f1.weights and exfin.universe_exclude_industries == EXFIN_EXCLUDED_INDUSTRIES
    b = {"revenue_accel": .40, "revenue_yoy": .14, "gm_yoy": .14, "gm_level": .05, "fcf_margin_yoy": .14}
    assert dict(get_scheme("F0").weights) == pytest.approx({k: v / .87 for k, v in b.items()})
    assert get_scheme("F0").gates == ("net_margin_down", "new_listing")          # D-4 已确认


def test_hash_tracks_config_not_changelog_or_description():
    s = get_scheme("F1")
    h = scheme_hash(s)
    assert len(h) == 16 and h == scheme_hash(replace(s))
    assert scheme_hash(replace(s, changes=s.changes + (("2026-10-01", "typo"),), description="x")) == h
    assert scheme_hash(replace(s, grade_strict=66.0)) != h
    specs = {**FACTOR_SPECS, "gm_yoy": replace(FACTOR_SPECS["gm_yoy"], exempt_industries=())}
    assert scheme_hash(s, specs) != h                  # exemptions are part of the scheme identity


def test_hashes_are_distinct_and_readers_fail_closed():
    assert len({scheme_hash(s) for s in SCHEMES.values()}) == 4
    assert check_scheme_hash("F1", scheme_hash(get_scheme("F1"))) is get_scheme("F1")
    with pytest.raises(ValueError):
        check_scheme_hash("F1", "0" * 16)
    with pytest.raises(KeyError):
        get_scheme("F9")


def test_validate_rejects_bad_configs():
    s = get_scheme("F1")
    for bad in (replace(s, weights=s.weights + (("inventory_days", .01),)),
                replace(s, weights=(("revenue_accel", .5), ("revenue_yoy", .4))),
                replace(s, transform="minmax"),
                replace(s, weights=(("revenue_accel", .80), ("surprise", .20)))):   # expectation family > .10
        with pytest.raises(ValueError):
            validate_scheme(bad)


def test_industry_patterns_keep_payment_networks_and_exchanges():
    assert industry_matches("Banks - Regional", EXEMPT_INDUSTRIES)
    assert industry_matches("REIT - Specialty", EXEMPT_INDUSTRIES)
    assert not industry_matches("Independent Power Producers", EXEMPT_INDUSTRIES)     # D-3 已确认
    assert industry_matches("Independent Power Producers", EXFIN_EXCLUDED_INDUSTRIES)
    for kept in ("Financial - Credit Services", "Financial - Data & Stock Exchanges", "Real Estate - Services"):
        assert not industry_matches(kept, EXFIN_EXCLUDED_INDUSTRIES)
    assert not industry_matches(None, EXEMPT_INDUSTRIES)


def _tree(root: Path):
    for rel in CODE_VERSION_SOURCES:
        path = root / rel / "a.py" if not rel.endswith(".py") else root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")


def test_code_version_covers_package_and_external_sources(tmp_path):
    _tree(tmp_path)
    v1 = code_version(tmp_path)
    assert len(v1) == 16 and code_version(tmp_path) == v1
    (tmp_path / "terminal/prosperity/notes.md").write_text("ignored")
    assert code_version(tmp_path) == v1
    for rel in ("src/data/prosperity_quality.py", "src/data/metrics_calculator.py", "src/data/market_store.py"):
        before = code_version(tmp_path)
        (tmp_path / rel).write_text((tmp_path / rel).read_text() + "y = 2\n")
        assert code_version(tmp_path) != before, rel      # alignment and membership code move the version


def test_code_version_fails_closed_on_missing_source(tmp_path):
    _tree(tmp_path)
    (tmp_path / "src/data/fundamental_value_checks.py").unlink()
    with pytest.raises(FileNotFoundError):
        code_version(tmp_path)


def _local_imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):                  # includes function-local imports
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (a.name for a in node.names)


def test_every_local_import_is_hashed_or_explicitly_excluded():
    """A new engine dependency must be added to the hash or excluded with a reason; never silently missed."""
    def covered(rel):
        return rel in CODE_VERSION_EXCLUDED or any(rel == s or rel.startswith(s + "/") for s in CODE_VERSION_SOURCES)

    files = [p for s in CODE_VERSION_SOURCES
             for p in ((REPO_ROOT / s).rglob("*.py") if not s.endswith(".py") else [REPO_ROOT / s])]
    missing = set()
    for path in files:
        for mod in _local_imports(path):
            if mod.split(".")[0] not in ("src", "terminal", "config", "scripts"):
                continue
            rel = mod.replace(".", "/")
            rel = rel + ".py" if (REPO_ROOT / (rel + ".py")).exists() else rel
            if not covered(rel):
                missing.add(f"{path.relative_to(REPO_ROOT)} -> {rel}")
    assert not missing, sorted(missing)

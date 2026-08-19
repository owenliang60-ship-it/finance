"""T20 B5 — legacy fundamental batch APIs may not infer the retired Core."""
import pytest

import src.data.fundamental_fetcher as ff


@pytest.mark.parametrize("name", [
    "update_profiles",
    "update_ratios",
    "update_income",
    "update_balance_sheets",
    "update_cash_flows",
    "update_all_fundamentals",
])
def test_batch_entrypoints_require_explicit_symbols(name):
    fn = getattr(ff, name)
    with pytest.warns(DeprecationWarning), \
         pytest.raises(ValueError, match="explicit symbols required"):
        fn(None)


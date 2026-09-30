"""M7 S1: row-by-row check of our shared statement factors against the original site's 47 boards.

North star layer 2 "S1 对拍工具"; plan docs/plans/2026-09-30-prosperity-m7-s1-comparison.md.
The original site's output is private: it stays in gitignored paths, and nothing this
module returns for the committed summary carries a site or engine value.
Pure functions: no database reads, no file writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

# (original site key, engine key); both sides in % or pp
FACTORS: Tuple[Tuple[str, str], ...] = (
    ("revenue_yoy_pct", "revenue_yoy"),
    ("revenue_yoy_accel", "revenue_accel"),
    ("gm_pct", "gm_level"),
    ("gm_yoy_pp", "gm_yoy"),
    ("fcf_margin_yoy_pp", "fcf_margin_yoy"),
    ("net_margin_yoy_pp", "net_margin_yoy"),
)
SYMBOL_MAP = {"SQ": "XYZ"}                      # Block renamed its ticker to XYZ in 2025
# Same issuer and statements as BRK.B, which sits on every board BRK.A does after 2021
SKIP = {"BRK.A": "duplicate_share_class"}
# dq.repairs field → input series; unknown fields are kept as-is and never explain a difference
REPAIR_FIELDS = {"revenue": "rev", "gross_margin": "gm", "fcf_margin": "fcfm", "net_margin": "nim"}
_SERIES = (("rev", "rev_abs_series"), ("gm", "gm_series"), ("fcfm", "fcfm_series"), ("nim", "nim_series"))


@dataclass(frozen=True)
class SeriesQuarter:
    fiscal_date: str
    rev: Optional[float]          # millions
    gm: Optional[float]           # gross margin, %
    fcfm: Optional[float]         # FCF margin, %
    nim: Optional[float]          # net margin, %


@dataclass(frozen=True)
class SiteRow:
    board: str
    site_symbol: str
    symbol: str
    latest_q: str
    filed: Optional[str]
    values: Mapping[str, Optional[float]]            # engine key → site value
    quarters: Tuple[SeriesQuarter, ...]              # oldest first
    repairs: Tuple[Tuple[str, str], ...]             # (quarter, mapped field)
    skip_reason: Optional[str]


def our_symbol(site_symbol: str) -> str:
    return SYMBOL_MAP.get(site_symbol, site_symbol.replace(".", "-"))


def _at(values, i):
    return values[i] if values is not None and i < len(values) else None


def load_site_rows(data: Mapping) -> List[SiteRow]:
    out = []
    for board in data["boards"]:
        for row in board["rows"]:
            dates = [d[:10] for d in row.get("q_series") or []]
            quarters = tuple(SeriesQuarter(d, **{name: _at(row.get(key), i) for name, key in _SERIES})
                             for i, d in enumerate(dates))
            repairs = tuple((r["quarter"][:10], REPAIR_FIELDS.get(r["field"], r["field"]))
                            for r in (row.get("dq") or {}).get("repairs") or [])
            sym = row["symbol"]
            out.append(SiteRow(board=board["asof"][:10], site_symbol=sym, symbol=our_symbol(sym),
                               latest_q=row["latest_q"][:10], filed=row.get("filed"),
                               values={engine: row.get(site) for site, engine in FACTORS},
                               quarters=quarters, repairs=repairs, skip_reason=SKIP.get(sym)))
    return out

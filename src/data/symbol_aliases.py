"""Symbol aliases: fold vendor codes that are the same company into one code.

FMP keeps market-cap history under both the pre-rename and post-rename
tickers (ABC/COR, ANTM/ELV, ...), so an as-of membership built from
`historical_market_cap` counts such companies twice. `config/symbol_aliases.json`
lists each alias with the code that stands for the company; readers map
through `resolve_alias`. Physical rows in market.db are never rewritten.

Kinds:
  rename / dual_listing / duplicate — the alias always maps to canonical.
  merger — before `effective_date` the alias is the company and the
           canonical code's history is vendor backfill (dropped); from the
           effective date the alias folds into the survivor.
"""
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ALIAS_KINDS = frozenset({"rename", "dual_listing", "duplicate", "merger"})
ALIAS_STATUSES = frozenset({"verified", "pending_verification"})
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_symbol_aliases(config_dir=DEFAULT_CONFIG_DIR) -> List[Dict[str, Any]]:
    path = Path(config_dir) / "symbol_aliases.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ValueError("symbol_aliases.json needs schema_version 1")
    entries = doc.get("aliases")
    if not isinstance(entries, list):
        raise ValueError("symbol_aliases.json needs an aliases list")
    aliases = set()
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("invalid alias entry: {!r}".format(e))
        for key in ("alias", "canonical", "kind", "status", "reviewed_at", "evidence"):
            if not isinstance(e.get(key), str) or not e[key]:
                raise ValueError("alias entry missing {}: {!r}".format(key, e))
        if e["alias"] == e["canonical"] or e["alias"] in aliases:
            raise ValueError("alias {} self-mapped or listed twice".format(e["alias"]))
        if e["kind"] not in ALIAS_KINDS or e["status"] not in ALIAS_STATUSES:
            raise ValueError("alias {} has bad kind/status".format(e["alias"]))
        try:
            date.fromisoformat(e["reviewed_at"])
            if e["kind"] == "merger" or "effective_date" in e:
                date.fromisoformat(e["effective_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("alias {} has a bad or missing date".format(e["alias"])) from exc
        aliases.add(e["alias"])
    chained = aliases & {e["canonical"] for e in entries}
    if chained:
        raise ValueError("canonical codes must not themselves be aliases: {}".format(sorted(chained)))
    return entries


def resolve_alias(symbol: str, as_of: str,
                  aliases: List[Dict[str, Any]]) -> Optional[str]:
    """The code that stands for `symbol`'s company at `as_of`; None = drop the row."""
    for e in aliases:
        merger_pending = e["kind"] == "merger" and as_of < e["effective_date"]
        if symbol == e["alias"]:
            return symbol if merger_pending else e["canonical"]
        if symbol == e["canonical"] and merger_pending:
            return None
    return symbol

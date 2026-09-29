"""Batch entry: resolve members, load (cached) histories, build packets with per-symbol isolation."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.data.market_store import MarketStore
from terminal.prosperity.config import STRICT_STATEMENTS_FROM
from terminal.prosperity.loader import load_benchmark_closes, load_history, resolve_members
from terminal.prosperity.packet import build_packet
from terminal.prosperity.types import InputPacket

BENCHMARK_KEY = ("__benchmark__", False)


def build_packets(store: MarketStore, as_of: str, *, mode: str, observed_at: Optional[str] = None,
                  symbols: Optional[Sequence[str]] = None, cache: Optional[Dict] = None,
                  with_beta: bool = True) -> Tuple[List[InputPacket], Dict[str, Any]]:
    cache = {} if cache is None else cache
    members, basis, unverified = resolve_members(store, as_of)
    requested = None if symbols is None else {s.strip() for s in symbols if s.strip()}
    wanted = [s for s in members if requested is None or s in requested]
    with_vintage = mode == "replay" and as_of[:10] >= STRICT_STATEMENTS_FROM
    load_s = build_s = 0.0
    if BENCHMARK_KEY not in cache:
        cache[BENCHMARK_KEY] = load_benchmark_closes(store)
    packets, errors = [], {}
    for sym in wanted:
        try:
            key = (sym, with_vintage)
            if key not in cache:
                t0 = time.perf_counter()
                cache[key] = load_history(store, sym, with_vintage=with_vintage)
                load_s += time.perf_counter() - t0
            t0 = time.perf_counter()
            packets.append(build_packet(cache[key], as_of, mode=mode, membership_basis=basis,
                                        benchmark_closes=cache[BENCHMARK_KEY], observed_at=observed_at,
                                        identity_unverified=sym in unverified, with_beta=with_beta))
            build_s += time.perf_counter() - t0
        except Exception as exc:   # isolate one bad symbol; surfaced via meta["errors"] and CLI exit 3
            errors[sym] = f"{type(exc).__name__}: {exc}"
    return packets, {"members_resolved": len(members), "membership_basis": basis, "errors": errors,
                     "requested_not_members": sorted((requested or set()) - set(members)),
                     "load_seconds": round(load_s, 3), "build_seconds": round(build_s, 3)}

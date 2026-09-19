# Crypto turnover pool: strict majority of window days

Boss changed the requirement on 2026-09-19: a coin must rank in daily USDT turnover Top100 on strictly more than half of ALL calendar days in its window. Minimum counts are 4/7, 8/14 and 16/30. This supersedes the every-day intersection rule described in the earlier implementation audit. No deployment or Telegram delivery was authorized/performed.

## Change

Replace intersection with frequency counting, retaining per-day completeness/duplicate guards and current tradability. Missing whole daily rankings still fail; the denominator is never shortened to available/listed days. Pools can exceed 100 and need not be nested; the existing union-of-pools 4h fetch handles both. Four metric definitions, positive-return/slope gate, full-window price requirements and 40/20/20/20 weights are unchanged.

Schema v2 identifies the new policy and includes each period's `min_top100_days` and each member's `top100_day_counts`. User-facing messages show the exact minimum; old version-one report artifacts are preserved separately. Updated plan removes the obsolete nested-pool/at-most-100-fetch assumption.

## Verification

- RED: 6 failed / 23 passed before the implementation change (strict 4/8/16 boundaries, occasional misses, non-nested/oversized pools, schema/message).
- Local relevant crypto suite: **132 passed in 1.05s**.
- Cloud temporary overlay, Python3.10: **132 passed in 3.71s**. No production file changes.
- Full local suite: **3708 passed,12 failed,4 skipped** in246.09s. All12 failing node IDs exactly match the previously verified clean baseline (breadth input snapshots and morning-report expectations).
- Main-thread `/cr` of source, direct callers, tests and plan: no unresolved findings. `py_compile` and `git diff --check` passed. No agents/DSH calls were needed for this narrow approved revision.

## Same-data effect, cutoff 2026-09-19 08:00 China time

| Window | Old pool | New pool | Valid prices | Uptrends |
|---|---:|---:|---:|---:|
| 7 days | 59 | 89 | 89 | 62 |
| 14 days | 50 | 82 | 81 | 24 |
| 30 days | 41 | 79 | 77 | 47 |

The three-pool union contains100 symbols, so41 new 4h histories were fetched using the SAME fixed endpoint as the previous run; no new daily-volume/metadata calls. The captured15,745 candidate-day observations and3000 daily Top100 rows were reused. An independent standard-library/45-digit Decimal verifier checked majority counts, exact pools,247 valid coin-windows, component percentiles, scores and Top10 membership. Composite maximum error is0; three unavailable windows remained unavailable (14d PONS;30d MARSCOIN/牛来) rather than being shortened.

Human previews and JSON/evidence: `reports/crypto_trend/2026-09-19-majority/`. Private helper/run evidence: `work/crypto-trend-pool/majority-20260919/`. The full source-data capture remains in the previously documented cloud temporary analysis directory. This is a same-cutoff comparison, not a live intraday ranking or original PIT metadata vintage.

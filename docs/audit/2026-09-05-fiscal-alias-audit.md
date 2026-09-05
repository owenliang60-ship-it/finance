# Fiscal-quarter alias audit and reviewed repair

Date: 2026-09-05

Status: current-quarter repair completed in production at 09:04 CST; Boss confirmed overall four-quarter growth, and the turnaround rule is deployed and production-verified at 10:20 CST (`3b68433`).

## Scope and cause

Before repair, all three raw current statement tables have 27 duplicate
`(symbol,fiscal_year,period)` groups across nine symbols; derived metrics have
12 duplicate groups. Current Extended contains six affected names:
GSK, HD, LITE, MDT, SNDK, WDC. The other three are PSTG, STRC and STRF and are
outside current base; their historical rows are not part of this repair.

The current PK is `(symbol,date)`. A provider date-format/period-end correction
adds a second row instead of superseding the old fiscal identity. Row-based
QoQ, TTM and CAGR computations can count one quarter twice. The compass's
sequence gate correctly detects an invalid sequence, but the invalid underlying
current rows must be repaired rather than bypassing that gate.

## Source verification and reviewed keep dates

Live FMP re-fetch on 2026-09-05 returned a unique date for each audited active
fiscal group. Most financial columns are identical; differences in SNDK income,
LITE cash flow and WDC balance require explicit reviewed mapping, not guessing
from calendar ordering.

| Symbol | FY / quarter | Tables | Keep | Remove |
|---|---|---|---|---|
| GSK | 2025 Q3 | income / balance / cashflow | 2025-09-30 | 2025-10-28 |
| HD | 2024 Q4 | income / balance / cashflow | 2025-02-02 | 2025-01-31 |
| LITE | 2024 Q4 | income / balance / cashflow | 2024-06-29 | 2024-06-30 |
| MDT | 2025 Q4 | income / cashflow | 2025-04-25 | 2025-04-30 |
| SNDK | 2025 Q2 | income | 2024-12-27 | 2024-12-31 |
| SNDK | 2025 Q3 | income | 2025-03-28 | 2025-03-31 |
| SNDK | 2025 Q4 | income | 2025-06-27 | 2025-06-30 |
| SNDK | 2026 Q1 | income / cashflow | 2025-10-03 | 2025-09-30 |
| WDC | 2025 Q3 | balance | 2025-03-28 | 2025-03-31 |

This is 17 source rows across 17 groups. Whole-group hashes are required before
application; any intervening data change aborts the transaction.

SNDK's actual October 3 and March 28 period ends are established by its
[Q1 FY2026 release](https://investor.sandisk.com/news-releases/news-release-details/sandisk-reports-fiscal-first-quarter-2026-financial-results)
and [Q3 FY2025 filing](https://www.sec.gov/Archives/edgar/data/2023554/000202355425000027/sndk-20250328.htm).

WDC is a stronger vendor defect: the March 31 row has the same assets/cash as
the following June 27 quarter ($14.002B / $2.114B). Its final
[March 28 10-Q](https://www.sec.gov/Archives/edgar/data/106040/000010604025000026/wdc-20250328.htm)
confirms March 28 assets $16.368B and cash $3.477B, matching the retained row.
The automatic writer must reject any future conflicting attempt to reintroduce
the wrong fiscal-date row; the current feed is not silently preferred over
this primary-source evidence.

## Boundaries

Only reviewed duplicate current rows and derived metrics are changed. Unique
older quarters, other symbols, prices and all fundamental_vintage records are
preserved. Before repair, the latest-per-date vintage view has zero duplicate
logical fiscal identities; the erroneous WDC provider snapshot still remains
an immutable historical observation. This work does not rewrite PIT history.

The repair records displaced rows and before-metrics in
`fundamental_current_archive`, in the same transaction as deletion and metrics
recomputation. A SQLite backup is required first under `market_db_writer`.

## Turnaround-rule clarification

BE income from 2025 Q3 through 2026 Q2 (USD millions):

| Quarter | Revenue | Net income |
|---|---:|---:|
| 2025 Q3 | 519.048 | -23.093 |
| 2025 Q4 | 777.683 | 1.091 |
| 2026 Q1 | 751.054 | 70.653 |
| 2026 Q2 | 1065.365 | 196.290 |

Net income increases every quarter, but revenue falls once. The initial question
was whether “持续增加” meant strict quarter-on-quarter growth. Boss subsequently
confirmed overall growth with intermediate declines allowed; implementation and
production verification are recorded below. EPS, RVOL and EMA30 remain in force.

## Production completion evidence

- Data code `6ff07c7` plus prevalidation writer-lock fix `ae86757`; deployed merge `8895121`.
- Full suite before the lock refinement: 2,864 passed, four skipped. Final affected suite: 269 passed. Cloud fiscal-repair suite: 37 passed. Both legacy and collector paths covered, including rollback and concurrent-write checks.
- Staged DB repair: 17 groups, 17 source rows archived/removed, 60 metrics rows recomputed; idempotent replay reports 17 unchanged groups. SNDK became raw-input-ready and entered the normal EMA/RVOL compass.
- Live SQLite backup under `market_db_writer`: `data/market.db.before-fiscal-alias-repair-20260905`, 1,015,717,888 bytes; quick_check=ok.
- Production application at 09:04:27: 17 groups repaired, 17 source rows removed, 60 metrics rows recomputed. Archive contains 85 rows (17 source + 68 prior metrics). Prices and vintage counts unchanged; quick_check=ok; replay made zero changes.
- Production scan as-of 2026-09-04: available=true; fundamental 898/935, RVOL and EMA30 924/935. Six hits in market-cap order: SNDK, SU, OKTA, BEKE, NTNX, FIVE.
- Post-repair scan of all three statements and metrics: zero duplicate fiscal-key groups among active Extended members. Remaining historical/non-base duplicates: PSTG, STRC, STRF (cashflow: PSTG only).
- SNDK YoY is turnaround, EPS QoQ 94.66%, revenue 4Q compound growth 57.19%, net-income 295.01%, mean 176.10%; latest price 1740 > EMA30 1528.06 and RVOL 2.0049. Raw formula checks passed independently.
- Authoritative DB pulled locally; local health check passed at 09:07. No extra group report sent.
- A later local HMC read revealed a bad downloaded snapshot despite that narrow health check. Cloud remained healthy; immutable SQLite backup transfer and hash-verified publication restored the local snapshot (issue 059). Full read-only builder/render replay then passed with SNDK and its beta present.
- Recomputed old-growth-rule comparison after repair: 79 fundamentals-only, 54 fundamentals+EMA30 without RVOL, six standard compass hits. This was the pre-clarification baseline.

## Turnaround growth — clarification and verification, 10:18 CST

Boss confirmed “只要求四季整体上升”. The new route checks for net income crossing from nonpositive to positive in any of the latest four quarters, including the oldest target quarter against its predecessor. Latest revenue and net income must both exceed the oldest of those four quarters, with latest net income positive. Interior declines are allowed. No turnaround means the original average-CAGR >=15% gate; failed turnaround endpoint growth cannot fall back to CAGR. EPS, RVOL, EMA30, GAAP basis and beta remain unchanged.

- TDD: 13 expected RED failures, then 233 passed / 1 skipped for the complete compass and morning-report test files. Covers middle-quarter decline, oldest-quarter turnaround, endpoint equality/decline, missing NI history, unchanged other gates and all three display contracts. The growth-summary cell reads `扭亏成长`; undefined CAGR remains `—`.
- Real 2026-09-04 replay: fundamentals-only **79→103**; fundamentals+EMA30 without RVOL **54→69**, no removals. Added: BAX, BE, EQNR, HALO, IVZ, LYB, PKX, SITM, SKM, SMTC, SYM, TEAM, TEM, VNOM, YPF.
- Full compass remains **6**: SNDK, SU, OKTA, BEKE, NTNX, FIVE. SNDK now displays the turnaround route; its numeric CAGR observations remain visible.
- BE: new fundamental route passes; EPS QoQ +169.57% and YoY turnaround pass; close 252.869995 > EMA30 222.069637. RVOL max7=0.525165σ remains below 2σ, so it joins the no-RVOL list, not the full compass.
- Current local snapshot resolves 934 active eligible members, with fundamental-ready890/934 and RVOL/EMA30-ready924/934 in the production builder. Comparing old and new raw-readiness predicates on the same current snapshot yields **zero changed stocks**: the lower denominator/coverage than the 09:07 repair snapshot is not caused by this rule change.
- Read-only complete builder + HTML render passed, all six hits retained nonmissing beta; output `reports/rendered/turnaround-check/morning_report_2026-09-04.html` in the worktree. No market.db writes, DV recollection or Telegram send.
- Deployment: implementation `28efad2`, merge `3b68433`, pushed to origin/main and fast-forwarded on aliyun. Python3 compile passed; cloud targeted tests **43 passed**. Production read-only screen + shared rendering reproduced the six names and coverage above, SNDK's `扭亏成长` label and all six betas; BE endpoint-growth/EMA passed and RVOL failed exactly as locally observed. No extra group message was sent.

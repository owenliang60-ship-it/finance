# 058 — Compass exclusion diagnostics: fiscal aliases and earnings basis

Date: 2026-09-05

Status: current fiscal aliases repaired in the 2026-09-05 follow-up; financial-basis and turnaround-policy questions remain separate.

Update 09:07: SNDK and the five other affected active Extended stocks were repaired using reviewed date mappings, a SQLite backup and atomic archival/metric recomputation. SNDK now qualifies in the standard six-hit compass. See `docs/audit/2026-09-05-fiscal-alias-audit.md`; the sections below preserve the initial diagnosis.

## SNDK: duplicate representations of a fiscal quarter

The production income table contains two FY2026 Q1 rows dated 2025-10-03 and
2025-09-30 with identical EPS/revenue/net-income values. FY2025 Q4 and Q3 also
have both calendar-end and actual fiscal-end rows. The screen's first-five-row
fiscal sequence validator sees two Q1 entries, cannot establish five distinct
quarters, and marks SNDK not-ready. Therefore its exclusion is a data-validation
outcome, not evidence that its earnings growth fails the user's thresholds.

Do not bypass sequence validation or silently select one conflicting version.
A follow-up fix must distinguish identical fiscal aliases from genuinely
conflicting restatements, normalize by fiscal identity, preserve provenance,
recompute affected metrics and check the rest of the universe for the same case.
Database writes remain cloud-owned. No production financial rows were modified
as part of diagnosing this issue.

## Other requested exclusions (as of 2026-09-04)

- NVDA: current GAAP EPS 2.46 versus prior quarter 2.39 yields +2.93% QoQ,
  below 25%; YoY is +127.78%. Both prices and other growth fields pass.
- LITE: current GAAP net loss is 7,161.7 million, driven by a one-time noncash
  debt extinguishment charge. Non-GAAP EPS is positive. Changing the screen's
  financial basis requires a separate explicit decision, not a ticker exception.
- BE: latest EPS is positive and YoY turns profitable; QoQ 0.23 to 0.62 is
  +169.57%. The four-quarter net-income CAGR baseline (2025-09-30) is negative
  23.093 million, so its CAGR is undefined under the current formula. EPS
  turnaround treatment does not automatically waive this separate growth gate.
- All four traded above EMA30 on 2026-09-04. RVOL is irrelevant to the requested
  53-name what-if list, which explicitly removed the volume condition.

## Primary-source verification

- [NVIDIA Q2 FY2027 results](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/default.aspx)
- [Lumentum Q4 FY2026 results](https://investor.lumentum.com/financial-news-releases/news-details/2026/Lumentum-Announces-Fourth-Quarter-and-Full-Fiscal-Year-2026-Results/default.aspx)
- [Sandisk Q4 FY2026 results](https://investor.sandisk.com/news-releases/news-release-details/sandisk-reports-fiscal-fourth-quarter-2026-financial-results)
- [Bloom Q2 2026 results](https://investor.bloomenergy.com/press-releases/press-release-details/2026/Bloom-Energy-Reports-Record-Second-Quarter-2026-Financial-Results-and-Raises-Full-Year-2026-Guidance/default.aspx)

The preliminary LITE/SNDK earnings-release EPS values differ from current
database values. A final-filing/revision comparison is required before labeling
that difference corruption; do not infer data corruption merely from unusual
financial magnitudes. This does not change the independently established
GAAP-loss or duplicate-quarter exclusion reasons above.

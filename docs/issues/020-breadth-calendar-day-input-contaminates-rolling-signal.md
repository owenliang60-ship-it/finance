# 020 - Breadth Calendar-Day Input Contaminates Rolling Signal

## Symptom

`data/breadth_study_1b/daily_breadth.csv` contained 23 mid-stream NaN rows after `breadth_50` became valid. Every row was a US equity market holiday. The percentile-upcross verification then had only a tiny effective signal window because each holiday NaN contaminated the 252-day rolling percentile window.

## Root Cause

The broad breadth producer anchored `daily_breadth` to raw price dates. Raw `daily_price` can contain non-equity-market series such as `^VIX` on US stock holidays, while eligible equity breadth aggregates have no row for those dates. Merging raw dates with aggregate breadth therefore created holiday rows with NaN breadth values.

## Fix

Anchor `daily_breadth` to dates that actually produce active or partial breadth aggregates. This keeps raw auxiliary dates out of the research signal while preserving the existing PIT eligibility and delisted-overlay logic.

## Prevention

- For rolling-window research signals, audit mid-stream NaNs after the first valid signal date.
- Do not use raw multi-source price dates as the canonical trading calendar unless all series share the same market calendar.
- Validate target return coverage separately; missing benchmark ETFs can silently turn cross-target checks into partial-target checks.

# 061 — Optional live-DB volume-concentration parity differs from frozen CSV

Date: 2026-09-05
Status: observed baseline discrepancy; outside issue060 deployment scope, not fixed.

`tests/test_morning_report.py::TestVolumeConcentrationFreezeDateParity::test_frozen_date_matches_research_csv_across_six_fields` runs only where both the ignored research CSV and live market.db are present. It was skipped in the clean development worktree, but enabled in the main checkout's post-merge verification.

For the frozen2026-07-17 date, the live database path yields `share_sm_pct=47.821734684966856`, versus the research CSV's `47.80391692200165`; difference0.017817763 percentage points, outside the test's1e-6 tolerance. Both the pre-merge4971734 engine and mergedc674f44 produce the same failed assertion; morning-report implementation and its tests are byte-identical across this merge. This is not an issue060 regression.

Do not loosen the tolerance or rewrite the reference as part of an unrelated deployment. A separate investigation must establish which underlying data changed and whether the frozen test should use versioned inputs; this observation alone does not prove corruption or invalidate current daily reports.

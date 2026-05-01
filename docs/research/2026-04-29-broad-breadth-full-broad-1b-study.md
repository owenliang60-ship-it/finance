# Broad Breadth Participation Signal Study — QQQ/SOXX

Generated: 2026-04-29 09:38:42

## Executive Summary

This is a research artifact only. It does not create trading instructions and does not modify production logic.

| Target | Verdict | Primary FDR | Best OOS breadth overlay @10 bps vs own MA50 | Reason |
|---|---|---:|---:|---|
| QQQ | 淘汰 | min q=1.0000 | breadth_20_hard: CAGR -0.59%, Sharpe +0.25 | No primary cell passed verdict FDR + bootstrap gate. |
| SOXX | 淘汰 | min q=1.0000 | breadth_20_hard: CAGR -14.29%, Sharpe -0.05 | No primary cell passed verdict FDR + bootstrap gate. |

## Study Protocol

- Universe: broad $1B+ with active-only and with-delisted_partial variants.
- Effective sample: 2021-06-22 00:00:00 -> 2026-04-28 00:00:00.
- OOS split: 2025-01-01.
- Primary hypotheses: H1/H2 level effects and H3/H4 events, QQQ/SOXX, 10d/20d, fixed 16-cell family.
- P-values: HAC one-sided by pre-registered direction; bootstrap is a second gate.
- Event low-N rule: N>=15 tested, 10<=N<15 supportive, N<10 not_tested; verdict FDR uses p=1.0 for N<15.

## Coverage Audit

- Latest breadth: 2026-04-28 00:00:00: MA20 breadth 62.7%, MA50 breadth 60.0%, eligible 2480.
- Mean active eligible count: 2254.8.
- Mean with-delisted_partial eligible count: 2261.4.
- Mean delisted overlay coverage ratio: 31.0%.
- Max PIT staleness exclusions/day: 1.

## Forward Return Tests

| universe_variant | sample | hypothesis | target | horizon | diff_mean | hac_p_value | bootstrap_p_value | verdict_q | audit_q | cell_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_only | Full | H1 | QQQ | 10 | -0.0086 | 0.9435 | 0.9371 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | QQQ | 10 | -0.0084 | 0.9391 | 0.9351 | 1.0000 | 1.0000 | tested |
| active_only | Full | H1 | QQQ | 20 | -0.0140 | 0.9292 | 0.9351 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | QQQ | 20 | -0.0137 | 0.9251 | 0.9351 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | QQQ | 10 | -0.0051 | 0.8382 | 0.8262 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | QQQ | 10 | -0.0050 | 0.8329 | 0.8332 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | QQQ | 20 | -0.0143 | 0.9611 | 0.9710 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | QQQ | 20 | -0.0145 | 0.9634 | 0.9650 | 1.0000 | 1.0000 | tested |
| active_only | Full | H3 | QQQ | 10 | 0.0284 | 0.0135 | 0.0430 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | QQQ | 10 | 0.0284 | 0.0135 | 0.0430 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H3 | QQQ | 20 | 0.0183 | 0.2019 | 0.2577 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | QQQ | 20 | 0.0183 | 0.2019 | 0.2577 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | QQQ | 10 | 0.0121 | 0.8275 | 0.7532 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | QQQ | 10 | 0.0121 | 0.8275 | 0.7532 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | QQQ | 20 | 0.0125 | 0.7801 | 0.7053 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | QQQ | 20 | 0.0125 | 0.7801 | 0.7053 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H1 | SOXX | 10 | -0.0122 | 0.9127 | 0.9121 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | SOXX | 10 | -0.0122 | 0.9125 | 0.9121 | 1.0000 | 1.0000 | tested |
| active_only | Full | H1 | SOXX | 20 | -0.0115 | 0.7663 | 0.7682 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | SOXX | 20 | -0.0115 | 0.7671 | 0.7712 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | SOXX | 10 | -0.0063 | 0.7787 | 0.7912 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | SOXX | 10 | -0.0063 | 0.7796 | 0.8002 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | SOXX | 20 | -0.0134 | 0.8329 | 0.8362 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | SOXX | 20 | -0.0140 | 0.8451 | 0.8531 | 1.0000 | 1.0000 | tested |
| active_only | Full | H3 | SOXX | 10 | 0.0348 | 0.0932 | 0.1339 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | SOXX | 10 | 0.0348 | 0.0932 | 0.1339 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H3 | SOXX | 20 | 0.0270 | 0.2459 | 0.3137 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | SOXX | 20 | 0.0270 | 0.2459 | 0.3137 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | SOXX | 10 | 0.0204 | 0.8708 | 0.8042 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | SOXX | 10 | 0.0204 | 0.8708 | 0.8042 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | SOXX | 20 | 0.0051 | 0.5938 | 0.5544 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | SOXX | 20 | 0.0051 | 0.5938 | 0.5544 | 1.0000 | 1.0000 | not_tested |
| active_only | IS | H1 | QQQ | 10 | -0.0086 | 0.9168 | 0.9221 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | IS | H1 | QQQ | 10 | -0.0083 | 0.9093 | 0.9101 | 1.0000 | 1.0000 | tested |
| active_only | IS | H1 | QQQ | 20 | -0.0151 | 0.9242 | 0.9321 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | IS | H1 | QQQ | 20 | -0.0146 | 0.9185 | 0.9181 | 1.0000 | 1.0000 | tested |

## Overlay Backtest

| universe_variant | sample | target | strategy | tc_bps | cagr | sharpe | max_drawdown | excess_cagr_vs_own_ma50 | sharpe_diff_vs_own_ma50 | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| with_delisted_partial | Full | QQQ | breadth_20_50_hard | 10 | 0.0612 | 0.5488 | -0.1557 | -0.2220 | -1.5403 | 13.2834 |
| with_delisted_partial | Full | QQQ | breadth_20_hard | 10 | 0.0754 | 0.5882 | -0.2259 | -0.2078 | -1.5009 | 16.3636 |
| with_delisted_partial | Full | QQQ | breadth_50_hard | 10 | 0.0877 | 0.7278 | -0.1772 | -0.1956 | -1.3613 | 12.8984 |
| with_delisted_partial | Full | QQQ | breadth_50_soft_w0 | 10 | 0.0686 | 0.6068 | -0.2065 | -0.2146 | -1.4823 | 7.7085 |
| with_delisted_partial | Full | QQQ | buy_hold | 10 | 0.1401 | 0.6237 | -0.3562 | -0.1431 | -1.4654 | 0.1925 |
| with_delisted_partial | Full | QQQ | equal_weight_ma50 | 10 | 0.1990 | 1.4688 | -0.1114 | -0.0842 | -0.6203 | 17.7112 |
| with_delisted_partial | Full | QQQ | own_ma50 | 10 | 0.2832 | 2.0891 | -0.0868 | 0.0000 | 0.0000 | 14.0535 |
| with_delisted_partial | Full | QQQ | spy_ma50 | 10 | 0.2809 | 2.0306 | -0.0868 | -0.0023 | -0.0585 | 15.9786 |
| with_delisted_partial | IS | QQQ | breadth_20_50_hard | 10 | 0.0335 | 0.2889 | -0.1557 | -0.2436 | -1.6981 | 13.3851 |
| with_delisted_partial | IS | QQQ | breadth_20_hard | 10 | 0.0112 | 0.0841 | -0.2259 | -0.2659 | -1.9029 | 16.9888 |
| with_delisted_partial | IS | QQQ | breadth_50_hard | 10 | 0.0529 | 0.4224 | -0.1772 | -0.2242 | -1.5646 | 13.3851 |
| with_delisted_partial | IS | QQQ | breadth_50_soft_w0 | 10 | 0.0409 | 0.3430 | -0.2065 | -0.2362 | -1.6440 | 7.9710 |
| with_delisted_partial | IS | QQQ | buy_hold | 10 | 0.1169 | 0.5197 | -0.3562 | -0.1602 | -1.4673 | 0.2574 |
| with_delisted_partial | IS | QQQ | equal_weight_ma50 | 10 | 0.1823 | 1.2830 | -0.1114 | -0.0948 | -0.7040 | 20.5924 |
| with_delisted_partial | IS | QQQ | own_ma50 | 10 | 0.2771 | 1.9870 | -0.0868 | 0.0000 | 0.0000 | 12.6129 |
| with_delisted_partial | IS | QQQ | spy_ma50 | 10 | 0.2823 | 1.9786 | -0.0868 | 0.0052 | -0.0084 | 16.4740 |
| with_delisted_partial | OOS | QQQ | breadth_20_50_hard | 10 | 0.1478 | 1.5200 | -0.0572 | -0.1489 | -0.8781 | 12.9818 |
| with_delisted_partial | OOS | QQQ | breadth_20_hard | 10 | 0.2908 | 2.6505 | -0.0572 | -0.0059 | 0.2524 | 14.5091 |
| with_delisted_partial | OOS | QQQ | breadth_50_hard | 10 | 0.1976 | 1.8832 | -0.0572 | -0.0991 | -0.5149 | 11.4545 |
| with_delisted_partial | OOS | QQQ | breadth_50_soft_w0 | 10 | 0.1555 | 1.6689 | -0.0497 | -0.1412 | -0.7292 | 6.9299 |
| with_delisted_partial | OOS | QQQ | buy_hold | 10 | 0.2137 | 0.9516 | -0.2288 | -0.0830 | -1.4465 | 0.0000 |
| with_delisted_partial | OOS | QQQ | equal_weight_ma50 | 10 | 0.2502 | 2.1903 | -0.0572 | -0.0465 | -0.2078 | 9.1636 |
| with_delisted_partial | OOS | QQQ | own_ma50 | 10 | 0.2967 | 2.3981 | -0.0473 | 0.0000 | 0.0000 | 18.3273 |
| with_delisted_partial | OOS | QQQ | spy_ma50 | 10 | 0.2770 | 2.2141 | -0.0531 | -0.0197 | -0.1840 | 14.5091 |
| with_delisted_partial | Full | SOXX | breadth_20_50_hard | 10 | 0.1210 | 0.6074 | -0.3542 | -0.3387 | -1.3691 | 13.8219 |
| with_delisted_partial | Full | SOXX | breadth_20_hard | 10 | 0.1463 | 0.6606 | -0.3692 | -0.3134 | -1.3159 | 17.0270 |
| with_delisted_partial | Full | SOXX | breadth_50_hard | 10 | 0.1683 | 0.7775 | -0.2385 | -0.2914 | -1.1990 | 13.4213 |
| with_delisted_partial | Full | SOXX | breadth_50_soft_w0 | 10 | 0.1272 | 0.6237 | -0.2838 | -0.3325 | -1.3528 | 8.0210 |
| with_delisted_partial | Full | SOXX | buy_hold | 10 | 0.2447 | 0.6861 | -0.4624 | -0.2149 | -1.2904 | 0.2003 |
| with_delisted_partial | Full | SOXX | equal_weight_ma50 | 10 | 0.3237 | 1.3453 | -0.2662 | -0.1359 | -0.6312 | 18.4293 |
| with_delisted_partial | Full | SOXX | own_ma50 | 10 | 0.4596 | 1.9765 | -0.1326 | 0.0000 | 0.0000 | 16.6264 |
| with_delisted_partial | Full | SOXX | spy_ma50 | 10 | 0.4344 | 1.7477 | -0.1616 | -0.0253 | -0.2288 | 16.6264 |
| with_delisted_partial | IS | SOXX | breadth_20_50_hard | 10 | 0.0363 | 0.1818 | -0.3542 | -0.3112 | -1.3834 | 14.1207 |
| with_delisted_partial | IS | SOXX | breadth_20_hard | 10 | -0.0004 | -0.0018 | -0.3678 | -0.3479 | -1.5670 | 17.9224 |
| with_delisted_partial | IS | SOXX | breadth_50_hard | 10 | 0.0517 | 0.2378 | -0.2385 | -0.2958 | -1.3274 | 14.1207 |
| with_delisted_partial | IS | SOXX | breadth_50_soft_w0 | 10 | 0.0241 | 0.1150 | -0.2838 | -0.3234 | -1.4502 | 8.4091 |
| with_delisted_partial | IS | SOXX | buy_hold | 10 | 0.1092 | 0.3190 | -0.4624 | -0.2383 | -1.2462 | 0.2716 |
| with_delisted_partial | IS | SOXX | equal_weight_ma50 | 10 | 0.2191 | 0.9015 | -0.2589 | -0.1284 | -0.6637 | 21.7241 |
| with_delisted_partial | IS | SOXX | own_ma50 | 10 | 0.3475 | 1.5652 | -0.1252 | 0.0000 | 0.0000 | 16.2931 |
| with_delisted_partial | IS | SOXX | spy_ma50 | 10 | 0.3207 | 1.2975 | -0.1616 | -0.0268 | -0.2677 | 17.3793 |
| with_delisted_partial | OOS | SOXX | breadth_20_50_hard | 10 | 0.3979 | 2.0148 | -0.0983 | -0.4295 | -1.1674 | 12.9818 |
| with_delisted_partial | OOS | SOXX | breadth_20_hard | 10 | 0.6845 | 3.1358 | -0.0983 | -0.1429 | -0.0464 | 14.5091 |
| with_delisted_partial | OOS | SOXX | breadth_50_hard | 10 | 0.5701 | 2.6755 | -0.1085 | -0.2573 | -0.5067 | 11.4545 |
| with_delisted_partial | OOS | SOXX | breadth_50_soft_w0 | 10 | 0.4761 | 2.5475 | -0.0825 | -0.3513 | -0.6347 | 6.9299 |
| with_delisted_partial | OOS | SOXX | buy_hold | 10 | 0.7129 | 1.8086 | -0.3433 | -0.1145 | -1.3736 | 0.0000 |
| with_delisted_partial | OOS | SOXX | equal_weight_ma50 | 10 | 0.6687 | 2.8595 | -0.1085 | -0.1587 | -0.3227 | 9.1636 |
| with_delisted_partial | OOS | SOXX | own_ma50 | 10 | 0.8274 | 3.1822 | -0.0980 | 0.0000 | 0.0000 | 17.5636 |
| with_delisted_partial | OOS | SOXX | spy_ma50 | 10 | 0.8092 | 3.2046 | -0.0986 | -0.0182 | 0.0224 | 14.5091 |

## Robustness Notes

- Active-only and with-delisted_partial are both exported. A conflict downgrades the verdict.
- Supplementary 5d/60d horizons are exported but not used in the primary FDR family.
- Exploratory slices ($1B/$3B, EMA, industry subsets) are intentionally not included in this first execution.

## Artifacts

- `data/breadth_study/daily_breadth.csv`
- `data/breadth_study/coverage_audit.csv`
- `data/breadth_study/state_forward_returns.csv`
- `data/breadth_study/event_forward_returns.csv`
- `data/breadth_study/overlay_backtest.csv`
- `data/breadth_study/sidecar/sidecar_coverage_report.md`

# Broad Breadth Participation Signal Study — QQQ/SOXX

Generated: 2026-04-29 08:16:42

## Executive Summary

This is a research artifact only. It does not create trading instructions and does not modify production logic.

| Target | Verdict | Primary FDR | Best OOS breadth overlay @10 bps vs own MA50 | Reason |
|---|---|---:|---:|---|
| QQQ | 淘汰 | min q=1.0000 | breadth_20_hard: CAGR -5.17%, Sharpe -0.36 | No primary cell passed verdict FDR + bootstrap gate. |
| SOXX | 淘汰 | min q=1.0000 | breadth_20_hard: CAGR -7.94%, Sharpe -0.01 | No primary cell passed verdict FDR + bootstrap gate. |

## Study Protocol

- Universe: broad $10B+ with active-only and with-delisted_partial variants.
- Effective sample: 2021-06-22 00:00:00 -> 2026-04-27 00:00:00.
- OOS split: 2025-01-01.
- Primary hypotheses: H1/H2 level effects and H3/H4 events, QQQ/SOXX, 10d/20d, fixed 16-cell family.
- P-values: HAC one-sided by pre-registered direction; bootstrap is a second gate.
- Event low-N rule: N>=15 tested, 10<=N<15 supportive, N<10 not_tested; verdict FDR uses p=1.0 for N<15.

## Coverage Audit

- Latest breadth: 2026-04-27 00:00:00: MA20 breadth 56.8%, MA50 breadth 51.0%, eligible 995.
- Mean active eligible count: 864.0.
- Mean with-delisted_partial eligible count: 869.7.
- Mean delisted overlay coverage ratio: 26.4%.
- Max PIT staleness exclusions/day: 1.

## Forward Return Tests

| universe_variant | sample | hypothesis | target | horizon | diff_mean | hac_p_value | bootstrap_p_value | verdict_q | audit_q | cell_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| active_only | Full | H1 | QQQ | 10 | -0.0104 | 0.9596 | 0.9590 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | QQQ | 10 | -0.0102 | 0.9570 | 0.9540 | 1.0000 | 1.0000 | tested |
| active_only | Full | H1 | QQQ | 20 | -0.0164 | 0.9569 | 0.9670 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | QQQ | 20 | -0.0161 | 0.9545 | 0.9680 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | QQQ | 10 | -0.0071 | 0.9081 | 0.9051 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | QQQ | 10 | -0.0071 | 0.9081 | 0.9061 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | QQQ | 20 | -0.0161 | 0.9670 | 0.9680 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | QQQ | 20 | -0.0161 | 0.9670 | 0.9690 | 1.0000 | 1.0000 | tested |
| active_only | Full | H3 | QQQ | 10 | 0.0293 | 0.0202 | 0.0519 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | QQQ | 10 | 0.0293 | 0.0202 | 0.0519 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H3 | QQQ | 20 | 0.0251 | 0.0990 | 0.1658 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | QQQ | 20 | 0.0251 | 0.0990 | 0.1658 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | QQQ | 10 | 0.0179 | 0.9151 | 0.8452 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | QQQ | 10 | 0.0179 | 0.9151 | 0.8452 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | QQQ | 20 | 0.0224 | 0.8038 | 0.7852 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | QQQ | 20 | 0.0224 | 0.8038 | 0.7852 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H1 | SOXX | 10 | -0.0147 | 0.9404 | 0.9431 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | SOXX | 10 | -0.0146 | 0.9403 | 0.9361 | 1.0000 | 1.0000 | tested |
| active_only | Full | H1 | SOXX | 20 | -0.0132 | 0.8019 | 0.8142 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H1 | SOXX | 20 | -0.0132 | 0.8026 | 0.8262 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | SOXX | 10 | -0.0078 | 0.8268 | 0.8312 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | SOXX | 10 | -0.0078 | 0.8268 | 0.8432 | 1.0000 | 1.0000 | tested |
| active_only | Full | H2 | SOXX | 20 | -0.0134 | 0.8218 | 0.8382 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | Full | H2 | SOXX | 20 | -0.0134 | 0.8218 | 0.8302 | 1.0000 | 1.0000 | tested |
| active_only | Full | H3 | SOXX | 10 | 0.0364 | 0.1100 | 0.1399 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | SOXX | 10 | 0.0364 | 0.1100 | 0.1399 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H3 | SOXX | 20 | 0.0390 | 0.1470 | 0.1858 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H3 | SOXX | 20 | 0.0390 | 0.1470 | 0.1858 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | SOXX | 10 | 0.0227 | 0.8594 | 0.7742 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | SOXX | 10 | 0.0227 | 0.8594 | 0.7742 | 1.0000 | 1.0000 | not_tested |
| active_only | Full | H4 | SOXX | 20 | 0.0163 | 0.6891 | 0.6603 | 1.0000 | 1.0000 | not_tested |
| with_delisted_partial | Full | H4 | SOXX | 20 | 0.0163 | 0.6891 | 0.6603 | 1.0000 | 1.0000 | not_tested |
| active_only | IS | H1 | QQQ | 10 | -0.0091 | 0.9143 | 0.9181 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | IS | H1 | QQQ | 10 | -0.0088 | 0.9081 | 0.9201 | 1.0000 | 1.0000 | tested |
| active_only | IS | H1 | QQQ | 20 | -0.0130 | 0.8966 | 0.9091 | 1.0000 | 1.0000 | tested |
| with_delisted_partial | IS | H1 | QQQ | 20 | -0.0127 | 0.8905 | 0.8881 | 1.0000 | 1.0000 | tested |

## Overlay Backtest

| universe_variant | sample | target | strategy | tc_bps | cagr | sharpe | max_drawdown | excess_cagr_vs_own_ma50 | sharpe_diff_vs_own_ma50 | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| with_delisted_partial | Full | QQQ | breadth_20_50_hard | 10 | 0.0823 | 0.7000 | -0.1610 | -0.2036 | -1.4099 | 14.0642 |
| with_delisted_partial | Full | QQQ | breadth_20_hard | 10 | 0.0800 | 0.6065 | -0.1911 | -0.2060 | -1.5034 | 17.5321 |
| with_delisted_partial | Full | QQQ | breadth_50_hard | 10 | 0.0849 | 0.6582 | -0.2198 | -0.2011 | -1.4517 | 14.0642 |
| with_delisted_partial | Full | QQQ | breadth_50_soft_w0 | 10 | 0.0631 | 0.5403 | -0.2334 | -0.2229 | -1.5696 | 7.4364 |
| with_delisted_partial | Full | QQQ | buy_hold | 10 | 0.1424 | 0.6340 | -0.3562 | -0.1435 | -1.4759 | 0.1927 |
| with_delisted_partial | Full | QQQ | equal_weight_ma50 | 10 | 0.2158 | 1.5418 | -0.1193 | -0.0702 | -0.5681 | 17.3394 |
| with_delisted_partial | Full | QQQ | own_ma50 | 10 | 0.2860 | 2.1099 | -0.0868 | 0.0000 | 0.0000 | 14.0642 |
| with_delisted_partial | Full | QQQ | spy_ma50 | 10 | 0.2837 | 2.0509 | -0.0868 | -0.0023 | -0.0590 | 15.9908 |
| with_delisted_partial | IS | QQQ | breadth_20_50_hard | 10 | 0.0307 | 0.2534 | -0.1610 | -0.2464 | -1.7336 | 14.4147 |
| with_delisted_partial | IS | QQQ | breadth_20_hard | 10 | 0.0266 | 0.1961 | -0.1911 | -0.2505 | -1.7909 | 17.5036 |
| with_delisted_partial | IS | QQQ | breadth_50_hard | 10 | 0.0480 | 0.3641 | -0.2198 | -0.2291 | -1.6229 | 14.9295 |
| with_delisted_partial | IS | QQQ | breadth_50_soft_w0 | 10 | 0.0438 | 0.3587 | -0.2334 | -0.2333 | -1.6283 | 7.5683 |
| with_delisted_partial | IS | QQQ | buy_hold | 10 | 0.1169 | 0.5197 | -0.3562 | -0.1602 | -1.4673 | 0.2574 |
| with_delisted_partial | IS | QQQ | equal_weight_ma50 | 10 | 0.2075 | 1.4212 | -0.1193 | -0.0696 | -0.5658 | 19.5628 |
| with_delisted_partial | IS | QQQ | own_ma50 | 10 | 0.2771 | 1.9870 | -0.0868 | 0.0000 | 0.0000 | 12.6129 |
| with_delisted_partial | IS | QQQ | spy_ma50 | 10 | 0.2823 | 1.9786 | -0.0868 | 0.0052 | -0.0084 | 16.4740 |
| with_delisted_partial | OOS | QQQ | breadth_20_50_hard | 10 | 0.2517 | 2.3744 | -0.0572 | -0.0561 | -0.1175 | 13.0213 |
| with_delisted_partial | OOS | QQQ | breadth_20_hard | 10 | 0.2561 | 2.1285 | -0.0808 | -0.0517 | -0.3634 | 17.6170 |
| with_delisted_partial | OOS | QQQ | breadth_50_hard | 10 | 0.2027 | 1.6832 | -0.0625 | -0.1051 | -0.8087 | 11.4894 |
| with_delisted_partial | OOS | QQQ | breadth_50_soft_w0 | 10 | 0.1229 | 1.2282 | -0.0888 | -0.1849 | -1.2637 | 7.0440 |
| with_delisted_partial | OOS | QQQ | buy_hold | 10 | 0.2239 | 0.9962 | -0.2288 | -0.0839 | -1.4957 | 0.0000 |
| with_delisted_partial | OOS | QQQ | equal_weight_ma50 | 10 | 0.2407 | 1.9974 | -0.0572 | -0.0671 | -0.4945 | 10.7234 |
| with_delisted_partial | OOS | QQQ | own_ma50 | 10 | 0.3078 | 2.4919 | -0.0473 | 0.0000 | 0.0000 | 18.3830 |
| with_delisted_partial | OOS | QQQ | spy_ma50 | 10 | 0.2879 | 2.3046 | -0.0531 | -0.0200 | -0.1873 | 14.5532 |
| with_delisted_partial | Full | SOXX | breadth_20_50_hard | 10 | 0.1506 | 0.7192 | -0.3376 | -0.3205 | -1.3111 | 14.6348 |
| with_delisted_partial | Full | SOXX | breadth_20_hard | 10 | 0.1680 | 0.7374 | -0.3776 | -0.3031 | -1.2929 | 18.2434 |
| with_delisted_partial | Full | SOXX | breadth_50_hard | 10 | 0.1473 | 0.6382 | -0.2663 | -0.3237 | -1.3921 | 14.6348 |
| with_delisted_partial | Full | SOXX | breadth_50_soft_w0 | 10 | 0.1228 | 0.5875 | -0.2928 | -0.3482 | -1.4428 | 7.7381 |
| with_delisted_partial | Full | SOXX | buy_hold | 10 | 0.2543 | 0.7135 | -0.4624 | -0.2168 | -1.3168 | 0.2005 |
| with_delisted_partial | Full | SOXX | equal_weight_ma50 | 10 | 0.3251 | 1.3080 | -0.2580 | -0.1460 | -0.7223 | 18.0430 |
| with_delisted_partial | Full | SOXX | own_ma50 | 10 | 0.4711 | 2.0303 | -0.1326 | 0.0000 | 0.0000 | 16.6396 |
| with_delisted_partial | Full | SOXX | spy_ma50 | 10 | 0.4456 | 1.7963 | -0.1616 | -0.0255 | -0.2340 | 16.6396 |
| with_delisted_partial | IS | SOXX | breadth_20_50_hard | 10 | 0.0034 | 0.0164 | -0.3261 | -0.3442 | -1.5488 | 15.2069 |
| with_delisted_partial | IS | SOXX | breadth_20_hard | 10 | 0.0011 | 0.0047 | -0.3261 | -0.3465 | -1.5605 | 18.4655 |
| with_delisted_partial | IS | SOXX | breadth_50_hard | 10 | 0.0336 | 0.1476 | -0.2663 | -0.3139 | -1.4176 | 15.7500 |
| with_delisted_partial | IS | SOXX | breadth_50_soft_w0 | 10 | 0.0350 | 0.1642 | -0.2928 | -0.3125 | -1.4010 | 7.9842 |
| with_delisted_partial | IS | SOXX | buy_hold | 10 | 0.1092 | 0.3190 | -0.4624 | -0.2383 | -1.2462 | 0.2716 |
| with_delisted_partial | IS | SOXX | equal_weight_ma50 | 10 | 0.2204 | 0.8785 | -0.2580 | -0.1271 | -0.6867 | 20.6379 |
| with_delisted_partial | IS | SOXX | own_ma50 | 10 | 0.3475 | 1.5652 | -0.1252 | 0.0000 | 0.0000 | 16.2931 |
| with_delisted_partial | IS | SOXX | spy_ma50 | 10 | 0.3207 | 1.2975 | -0.1616 | -0.0268 | -0.2677 | 17.3793 |
| with_delisted_partial | OOS | SOXX | breadth_20_50_hard | 10 | 0.6926 | 3.2383 | -0.0983 | -0.1913 | -0.1861 | 13.0213 |
| with_delisted_partial | OOS | SOXX | breadth_20_hard | 10 | 0.8045 | 3.4187 | -0.1210 | -0.0794 | -0.0057 | 17.6170 |
| with_delisted_partial | OOS | SOXX | breadth_50_hard | 10 | 0.5401 | 2.2573 | -0.1359 | -0.3438 | -1.1671 | 11.4894 |
| with_delisted_partial | OOS | SOXX | breadth_50_soft_w0 | 10 | 0.4127 | 2.1003 | -0.1340 | -0.4712 | -1.3241 | 7.0440 |
| with_delisted_partial | OOS | SOXX | buy_hold | 10 | 0.7655 | 1.9465 | -0.3433 | -0.1184 | -1.4779 | 0.0000 |
| with_delisted_partial | OOS | SOXX | equal_weight_ma50 | 10 | 0.6712 | 2.7751 | -0.1085 | -0.2127 | -0.6493 | 10.7234 |
| with_delisted_partial | OOS | SOXX | own_ma50 | 10 | 0.8839 | 3.4244 | -0.0980 | 0.0000 | 0.0000 | 17.6170 |
| with_delisted_partial | OOS | SOXX | spy_ma50 | 10 | 0.8651 | 3.4528 | -0.0986 | -0.0188 | 0.0284 | 14.5532 |

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

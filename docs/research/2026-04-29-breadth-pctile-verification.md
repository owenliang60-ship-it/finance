# Breadth Percentile Upcross Verification — v1.2

**Manifest version**: v1.2
**Manifest SHA256**: `a3e9ed99410dd871c085a09075e414f1014903f3dd917020d23ddcb1d67302fa`
**Frozen at**: 2026-04-30
**Data sample**: 2021-02-01 → 2026-04-28
**Effective sample**: 2022-03-03 → 2026-04-28 (4.13 effective years)
**Universe**: $1B+ PIT, with_delisted_partial overlay
**Primary cell (主)**: SPY 10d
**Sensitivity cell (副)**: QQQ 10d
**Strategy costs**: 10bp one-way (20bp roundtrip)

## Top-Level Verdict

- **Cluster passes (主表)**: 0
- **Cluster passes (副表 QQQ)**: 0
- **SPY+QQQ 双通过 cluster**: 0 ← 最强证据
- **Isolated passes (主表)**: 2
- **Verdict**: **REJECT_NO_SIGNAL**

## Sensitivity Comparison (主 vs 副)

|   ma_window |   threshold | event_type    |   passes_spy_10d |   passes_qqq_10d | verdict                           |
|------------:|------------:|:--------------|-----------------:|-----------------:|:----------------------------------|
|          20 |        0.05 | low_recovery  |                1 |                1 | Neither                           |
|          20 |        0.1  | low_recovery  |                1 |                1 | Neither                           |
|          20 |        0.15 | low_recovery  |                3 |                3 | Neither                           |
|          20 |        0.8  | high_strength |                2 |                2 | Neither                           |
|          20 |        0.9  | high_strength |                1 |                1 | Neither                           |
|          20 |        0.95 | high_strength |                0 |                0 | Neither                           |
|          50 |        0.05 | low_recovery  |                0 |                0 | Neither                           |
|          50 |        0.1  | low_recovery  |                1 |                1 | Neither                           |
|          50 |        0.15 | low_recovery  |                3 |                4 | QQQ-only (warn: AI bull leg fit?) |
|          50 |        0.8  | high_strength |                4 |                4 | BOTH (strong)                     |
|          50 |        0.9  | high_strength |                4 |                4 | BOTH (strong)                     |
|          50 |        0.95 | high_strength |                3 |                3 | Neither                           |

Reading: 双过 = strong evidence; 仅 QQQ 过 = AI-bull-leg fit risk; 仅 SPY 过 = breadth has broad effect but no QQQ specificity; 都不过 = 4.13y sample cannot validate.

## Param Summary — Primary (SPY 10d)

|   ma_window |   threshold | event_type    | primary_cell   |   event_n_short |   event_n_long |   effective_years |   events_per_year |   event_hit |   baseline_hit |   hit_lift_pp |   perm_p | perm_sampling_method   |   perm_success_rate |   strategy_cagr_pp |   bnh_cagr_pp |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative |   n_trades |   exposure_pct |   target_same_sign_count | target_same_sign_targets   |   short_horizon_same_sign_count | short_horizon_same_sign_horizons   |   long_horizon_diff | h1_freq_pass   | h2_hit_pass   | h3_target_pass   | h4_short_horizon_pass   | h5_perm_pass   | h6_strategy_pass   |   passes_count_param |
|------------:|------------:|:--------------|:---------------|----------------:|---------------:|------------------:|------------------:|------------:|---------------:|--------------:|---------:|:-----------------------|--------------------:|-------------------:|--------------:|-----------------:|---------------------:|----------------------:|-----------------------------:|-----------:|---------------:|-------------------------:|:---------------------------|--------------------------------:|:-----------------------------------|--------------------:|:---------------|:--------------|:-----------------|:------------------------|:---------------|:-------------------|---------------------:|
|          20 |      0.0500 | low_recovery  | SPY_10d        |               8 |              7 |            4.1349 |            1.9347 |     37.5000 |        62.2439 |      -24.7439 |   0.8352 | rejection              |              1.0000 |            -2.0491 |       12.5991 |         -14.6482 |             -15.6010 |              -11.9868 |                       1.0000 |          8 |         7.6775 |                        0 |                            |                               1 | 20                                 |              0.0160 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.1000 | low_recovery  | SPY_10d        |               9 |              7 |            4.1349 |            2.1766 |     44.4444 |        62.2070 |      -17.7626 |   0.6543 | rejection              |              1.0000 |            -0.8229 |       12.5991 |         -13.4220 |             -16.9760 |               -8.8348 |                       1.0000 |          9 |         8.6372 |                        0 |                            |                               1 | 20                                 |              0.0006 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.1500 | low_recovery  | SPY_10d        |              11 |              8 |            4.1349 |            2.6603 |     72.7273 |        61.9374 |       10.7899 |   0.1608 | rejection              |              1.0000 |             2.7996 |       12.5991 |          -9.7995 |             -13.9398 |               -6.9309 |                       1.0000 |         11 |        10.5566 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |              0.0224 | True           | False         | True             | True                    | False          | False              |                    3 |
|          20 |      0.8000 | high_strength | SPY_10d        |              19 |             12 |            4.1349 |            4.5950 |     63.1579 |        62.0316 |        1.1263 |   0.4293 | rejection              |              0.9960 |             1.7412 |       12.5991 |         -10.8580 |             -14.5130 |               -8.0950 |                       1.0000 |         19 |        18.2342 |                        4 | SPY,QQQ,SOXX,XLK           |                               2 | 5,10                               |             -0.0210 | False          | False         | True             | True                    | False          | False              |                    2 |
|          20 |      0.9000 | high_strength | SPY_10d        |              11 |              7 |            4.1349 |            2.6603 |     63.6364 |        62.0352 |        1.6011 |   0.5924 | rejection              |              1.0000 |            -0.2904 |       12.5991 |         -12.8896 |             -15.5405 |               -8.9232 |                       1.0000 |         11 |        10.5566 |                        1 | IWM                        |                               0 |                                    |             -0.0092 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.9500 | high_strength | SPY_10d        |               6 |              4 |            4.1349 |            1.4511 |     60.0000 |        62.0623 |       -2.0623 |   0.7223 | rejection              |              1.0000 |            -1.0714 |       12.5991 |         -13.6706 |             -15.8856 |              -10.1722 |                       1.0000 |          5 |         4.7985 |                        0 |                            |                               0 |                                    |              0.0326 | False          | False         | False            | False                   | False          | False              |                    0 |
|          50 |      0.0500 | low_recovery  | SPY_10d        |               6 |              5 |            4.0159 |            1.4941 |     16.6667 |        62.6881 |      -46.0214 |   0.8911 | rejection              |              1.0000 |            -2.8344 |       12.8620 |         -15.6964 |             -16.4465 |              -12.9463 |                       1.0000 |          6 |         5.9289 |                        0 |                            |                               1 | 20                                 |             -0.0127 | False          | False         | False            | False                   | False          | False              |                    0 |
|          50 |      0.1000 | low_recovery  | SPY_10d        |               8 |              6 |            4.0159 |            1.9921 |     25.0000 |        62.7136 |      -37.7136 |   0.7992 | rejection              |              1.0000 |            -2.3346 |       12.8620 |         -15.1966 |             -18.1587 |              -11.1346 |                       1.0000 |          8 |         7.9051 |                        0 |                            |                               1 | 5                                  |             -0.0066 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.1500 | low_recovery  | SPY_10d        |               8 |              7 |            4.0159 |            1.9921 |     75.0000 |        62.3116 |       12.6884 |   0.0759 | rejection              |              1.0000 |             3.2002 |       12.8620 |          -9.6618 |             -13.6180 |               -7.4333 |                       1.0000 |          8 |         7.9051 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               3 | 5,10,20                            |              0.0180 | True           | False         | True             | True                    | False          | False              |                    3 |
|          50 |      0.8000 | high_strength | SPY_10d        |              12 |              8 |            4.0159 |            2.9881 |     83.3333 |        62.1594 |       21.1739 |   0.1319 | rejection              |              1.0000 |             3.8969 |       12.8620 |          -8.9650 |             -11.8012 |               -8.9219 |                       1.0000 |         12 |        11.8577 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 10,20                              |             -0.0129 | True           | True          | True             | True                    | False          | False              |                    4 |
|          50 |      0.9000 | high_strength | SPY_10d        |               8 |              6 |            4.0159 |            1.9921 |     87.5000 |        62.2111 |       25.2889 |   0.0989 | rejection              |              1.0000 |             3.3328 |       12.8620 |          -9.5292 |             -11.9182 |               -9.5644 |                       1.0000 |          8 |         7.9051 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |             -0.0113 | True           | True          | True             | True                    | False          | False              |                    4 |
|          50 |      0.9500 | high_strength | SPY_10d        |               5 |              5 |            4.0159 |            1.2451 |     80.0000 |        62.3246 |       17.6754 |   0.0979 | rejection              |              1.0000 |             2.6085 |       12.8620 |         -10.2535 |             -12.3929 |              -10.0208 |                       1.0000 |          5 |         4.9407 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |             -0.0097 | False          | True          | True             | True                    | False          | False              |                    3 |

## Param Summary — Sensitivity (QQQ 10d)

|   ma_window |   threshold | event_type    | primary_cell   |   event_n_short |   event_n_long |   effective_years |   events_per_year |   event_hit |   baseline_hit |   hit_lift_pp |   perm_p | perm_sampling_method   |   perm_success_rate |   strategy_cagr_pp |   bnh_cagr_pp |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative |   n_trades |   exposure_pct |   target_same_sign_count | target_same_sign_targets   |   short_horizon_same_sign_count | short_horizon_same_sign_horizons   |   long_horizon_diff | h1_freq_pass   | h2_hit_pass   | h3_target_pass   | h4_short_horizon_pass   | h5_perm_pass   | h6_strategy_pass   |   passes_count_param |
|------------:|------------:|:--------------|:---------------|----------------:|---------------:|------------------:|------------------:|------------:|---------------:|--------------:|---------:|:-----------------------|--------------------:|-------------------:|--------------:|-----------------:|---------------------:|----------------------:|-----------------------------:|-----------:|---------------:|-------------------------:|:---------------------------|--------------------------------:|:-----------------------------------|--------------------:|:---------------|:--------------|:-----------------|:------------------------|:---------------|:-------------------|---------------------:|
|          20 |      0.0500 | low_recovery  | QQQ_10d        |               8 |              7 |            4.1349 |            1.9347 |     37.5000 |        60.7805 |      -23.2805 |   0.8721 | rejection              |              1.0000 |            -3.0012 |       17.1062 |         -20.1074 |             -21.2258 |              -16.5814 |                       1.0000 |          8 |         7.6775 |                        0 |                            |                               1 | 20                                 |              0.0046 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.1000 | low_recovery  | QQQ_10d        |               9 |              7 |            4.1349 |            2.1766 |     44.4444 |        60.7422 |      -16.2977 |   0.5924 | rejection              |              1.0000 |            -0.7900 |       17.1062 |         -17.8962 |             -22.5990 |              -12.2958 |                       1.0000 |          9 |         8.6372 |                        0 |                            |                               1 | 20                                 |             -0.0048 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.1500 | low_recovery  | QQQ_10d        |              11 |              8 |            4.1349 |            2.6603 |     63.6364 |        60.5675 |        3.0688 |   0.1409 | rejection              |              1.0000 |             4.2592 |       17.1062 |         -12.8470 |             -18.1321 |              -10.1177 |                       1.0000 |         11 |        10.5566 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |              0.0392 | True           | False         | True             | True                    | False          | False              |                    3 |
|          20 |      0.8000 | high_strength | QQQ_10d        |              19 |             12 |            4.1349 |            4.5950 |     63.1579 |        60.5523 |        2.6056 |   0.2748 | rejection              |              0.9960 |             4.2112 |       17.1062 |         -12.8950 |             -18.9881 |              -10.1683 |                       1.0000 |         19 |        18.2342 |                        4 | SPY,QQQ,SOXX,XLK           |                               2 | 5,10                               |             -0.0360 | False          | False         | True             | True                    | False          | False              |                    2 |
|          20 |      0.9000 | high_strength | QQQ_10d        |              11 |              7 |            4.1349 |            2.6603 |     63.6364 |        60.5675 |        3.0688 |   0.5614 | rejection              |              1.0000 |             0.0509 |       17.1062 |         -17.0553 |             -21.0386 |              -12.2415 |                       1.0000 |         11 |        10.5566 |                        1 | IWM                        |                               0 |                                    |             -0.0235 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.9500 | high_strength | QQQ_10d        |               6 |              4 |            4.1349 |            1.4511 |     40.0000 |        60.7004 |      -20.7004 |   0.6923 | rejection              |              1.0000 |            -1.0198 |       17.1062 |         -18.1260 |             -20.9898 |              -14.2868 |                       1.0000 |          5 |         4.7985 |                        0 |                            |                               1 | 20                                 |              0.0404 | False          | False         | False            | False                   | False          | False              |                    0 |
|          50 |      0.0500 | low_recovery  | QQQ_10d        |               6 |              5 |            4.0159 |            1.4941 |     16.6667 |        61.2839 |      -44.6172 |   0.9141 | rejection              |              1.0000 |            -4.1046 |       17.9859 |         -22.0905 |             -23.1034 |              -18.2319 |                       1.0000 |          6 |         5.9289 |                        0 |                            |                               1 | 20                                 |             -0.0338 | False          | False         | False            | False                   | False          | False              |                    0 |
|          50 |      0.1000 | low_recovery  | QQQ_10d        |               8 |              6 |            4.0159 |            1.9921 |     37.5000 |        61.2060 |      -23.7060 |   0.7642 | rejection              |              1.0000 |            -2.5674 |       17.9859 |         -20.5533 |             -24.3759 |              -15.7253 |                       1.0000 |          8 |         7.9051 |                        0 |                            |                               1 | 5                                  |             -0.0254 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.1500 | low_recovery  | QQQ_10d        |               8 |              7 |            4.0159 |            1.9921 |     75.0000 |        60.9045 |       14.0955 |   0.0480 | rejection              |              1.0000 |             4.5830 |       17.9859 |         -13.4029 |             -18.8954 |              -10.6716 |                       1.0000 |          8 |         7.9051 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               3 | 5,10,20                            |              0.0126 | True           | False         | True             | True                    | True           | False              |                    4 |
|          50 |      0.8000 | high_strength | QQQ_10d        |              12 |              8 |            4.0159 |            2.9881 |     83.3333 |        60.7467 |       22.5866 |   0.1269 | rejection              |              1.0000 |             5.3886 |       17.9859 |         -12.5972 |             -17.0484 |              -12.3720 |                       1.0000 |         12 |        11.8577 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 10,20                              |             -0.0193 | True           | True          | True             | True                    | False          | False              |                    4 |
|          50 |      0.9000 | high_strength | QQQ_10d        |               8 |              6 |            4.0159 |            1.9921 |     87.5000 |        60.8040 |       26.6960 |   0.1409 | rejection              |              1.0000 |             4.0152 |       17.9859 |         -13.9707 |             -16.9349 |              -13.8325 |                       1.0000 |          8 |         7.9051 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |             -0.0181 | True           | True          | True             | True                    | False          | False              |                    4 |
|          50 |      0.9500 | high_strength | QQQ_10d        |               5 |              5 |            4.0159 |            1.2451 |     80.0000 |        60.9218 |       19.0782 |   0.1279 | rejection              |              1.0000 |             3.0668 |       17.9859 |         -14.9191 |             -17.2227 |              -14.7475 |                       1.0000 |          5 |         4.9407 |                        5 | SPY,QQQ,SOXX,IWM,XLK       |                               2 | 5,10                               |             -0.0232 | False          | True          | True             | True                    | False          | False              |                    3 |

## Cluster Pattern Detail — Primary

_No clusters found._

## Cluster Pattern Detail — Sensitivity

_No clusters found._

## Bootstrap CI for Excess CAGR (primary)

|   ma_window |   threshold | event_type    |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative | h6_strategy_pass   |
|------------:|------------:|:--------------|-----------------:|---------------------:|----------------------:|-----------------------------:|:-------------------|
|          20 |      0.0500 | low_recovery  |         -14.6482 |             -15.6010 |              -11.9868 |                       1.0000 | False              |
|          20 |      0.1000 | low_recovery  |         -13.4220 |             -16.9760 |               -8.8348 |                       1.0000 | False              |
|          20 |      0.1500 | low_recovery  |          -9.7995 |             -13.9398 |               -6.9309 |                       1.0000 | False              |
|          20 |      0.8000 | high_strength |         -10.8580 |             -14.5130 |               -8.0950 |                       1.0000 | False              |
|          20 |      0.9000 | high_strength |         -12.8896 |             -15.5405 |               -8.9232 |                       1.0000 | False              |
|          20 |      0.9500 | high_strength |         -13.6706 |             -15.8856 |              -10.1722 |                       1.0000 | False              |
|          50 |      0.0500 | low_recovery  |         -15.6964 |             -16.4465 |              -12.9463 |                       1.0000 | False              |
|          50 |      0.1000 | low_recovery  |         -15.1966 |             -18.1587 |              -11.1346 |                       1.0000 | False              |
|          50 |      0.1500 | low_recovery  |          -9.6618 |             -13.6180 |               -7.4333 |                       1.0000 | False              |
|          50 |      0.8000 | high_strength |          -8.9650 |             -11.8012 |               -8.9219 |                       1.0000 | False              |
|          50 |      0.9000 | high_strength |          -9.5292 |             -11.9182 |               -9.5644 |                       1.0000 | False              |
|          50 |      0.9500 | high_strength |         -10.2535 |             -12.3929 |              -10.0208 |                       1.0000 | False              |
_No borderline params (H6 fail but CI upper >= threshold)._


## Permutation Diagnostics (primary)

|   ma_window |   threshold | event_type    |   perm_p | perm_sampling_method   |   perm_success_rate | warn   |
|------------:|------------:|:--------------|---------:|:-----------------------|--------------------:|:-------|
|          20 |      0.0500 | low_recovery  |   0.8352 | rejection              |              1.0000 | False  |
|          20 |      0.1000 | low_recovery  |   0.6543 | rejection              |              1.0000 | False  |
|          20 |      0.1500 | low_recovery  |   0.1608 | rejection              |              1.0000 | False  |
|          20 |      0.8000 | high_strength |   0.4293 | rejection              |              0.9960 | False  |
|          20 |      0.9000 | high_strength |   0.5924 | rejection              |              1.0000 | False  |
|          20 |      0.9500 | high_strength |   0.7223 | rejection              |              1.0000 | False  |
|          50 |      0.0500 | low_recovery  |   0.8911 | rejection              |              1.0000 | False  |
|          50 |      0.1000 | low_recovery  |   0.7992 | rejection              |              1.0000 | False  |
|          50 |      0.1500 | low_recovery  |   0.0759 | rejection              |              1.0000 | False  |
|          50 |      0.8000 | high_strength |   0.1319 | rejection              |              1.0000 | False  |
|          50 |      0.9000 | high_strength |   0.0989 | rejection              |              1.0000 | False  |
|          50 |      0.9500 | high_strength |   0.0979 | rejection              |              1.0000 | False  |

_warn=True flags `perm_success_rate < 0.7`; such cells were re-sampled via sequential fallback or hit warning threshold._

## Isolated Pass Detail — Primary

|   ma_window |   threshold | event_type    |   passes_count_param |   hit_lift_pp |   perm_p |   excess_cagr_pp |
|------------:|------------:|:--------------|---------------------:|--------------:|---------:|-----------------:|
|          50 |      0.8000 | high_strength |                    4 |       21.1739 |   0.1319 |          -8.9650 |
|          50 |      0.9000 | high_strength |                    4 |       25.2889 |   0.0989 |          -9.5292 |
> ⚠️ **High fluke risk.** Isolated passes are NOT independent evidence; they cannot be promoted on their own.

## Diagnostic: 240-row Verification Table

<details><summary>Click to expand</summary>

|   ma_window |   threshold | event_type    | target   |   horizon |   event_n |   event_mean |   non_event_mean |   mean_diff |   hit_rate |   non_event_hit_rate |
|------------:|------------:|:--------------|:---------|----------:|----------:|-------------:|-----------------:|------------:|-----------:|---------------------:|
|          20 |      0.0500 | low_recovery  | SPY      |         5 |         8 |      -0.0206 |           0.0027 |     -0.0233 |     0.1250 |               0.5942 |
|          20 |      0.0500 | low_recovery  | SPY      |        10 |         8 |      -0.0082 |           0.0054 |     -0.0136 |     0.3750 |               0.6224 |
|          20 |      0.0500 | low_recovery  | SPY      |        20 |         8 |       0.0205 |           0.0097 |      0.0109 |     0.7500 |               0.6581 |
|          20 |      0.0500 | low_recovery  | SPY      |        60 |         6 |       0.0468 |           0.0308 |      0.0160 |     0.6667 |               0.7482 |
|          20 |      0.0500 | low_recovery  | QQQ      |         5 |         8 |      -0.0299 |           0.0037 |     -0.0335 |     0.1250 |               0.5981 |
|          20 |      0.0500 | low_recovery  | QQQ      |        10 |         8 |      -0.0131 |           0.0073 |     -0.0204 |     0.3750 |               0.6078 |
|          20 |      0.0500 | low_recovery  | QQQ      |        20 |         8 |       0.0249 |           0.0131 |      0.0117 |     0.7500 |               0.6502 |
|          20 |      0.0500 | low_recovery  | QQQ      |        60 |         6 |       0.0468 |           0.0422 |      0.0046 |     0.5000 |               0.7001 |
|          20 |      0.0500 | low_recovery  | SOXX     |         5 |         8 |      -0.0393 |           0.0063 |     -0.0455 |     0.2500 |               0.5689 |
|          20 |      0.0500 | low_recovery  | SOXX     |        10 |         8 |      -0.0115 |           0.0124 |     -0.0238 |     0.5000 |               0.5766 |
|          20 |      0.0500 | low_recovery  | SOXX     |        20 |         8 |       0.0580 |           0.0224 |      0.0357 |     0.6250 |               0.5813 |
|          20 |      0.0500 | low_recovery  | SOXX     |        60 |         6 |       0.0836 |           0.0675 |      0.0161 |     0.6667 |               0.6479 |
|          20 |      0.0500 | low_recovery  | IWM      |         5 |         8 |      -0.0211 |           0.0019 |     -0.0231 |     0.2500 |               0.5291 |
|          20 |      0.0500 | low_recovery  | IWM      |        10 |         8 |      -0.0049 |           0.0039 |     -0.0088 |     0.5000 |               0.5532 |
|          20 |      0.0500 | low_recovery  | IWM      |        20 |         8 |       0.0309 |           0.0067 |      0.0242 |     0.7500 |               0.5527 |
|          20 |      0.0500 | low_recovery  | IWM      |        60 |         6 |       0.0528 |           0.0213 |      0.0315 |     0.6667 |               0.6264 |
|          20 |      0.0500 | low_recovery  | XLK      |         5 |         8 |      -0.0343 |           0.0043 |     -0.0385 |     0.1250 |               0.5728 |
|          20 |      0.0500 | low_recovery  | XLK      |        10 |         8 |      -0.0137 |           0.0084 |     -0.0220 |     0.2500 |               0.5893 |
|          20 |      0.0500 | low_recovery  | XLK      |        20 |         8 |       0.0341 |           0.0149 |      0.0192 |     0.6250 |               0.6227 |
|          20 |      0.0500 | low_recovery  | XLK      |        60 |         6 |       0.0623 |           0.0459 |      0.0163 |     0.5000 |               0.6807 |
|          20 |      0.1000 | low_recovery  | SPY      |         5 |         9 |      -0.0041 |           0.0026 |     -0.0067 |     0.6667 |               0.5899 |
|          20 |      0.1000 | low_recovery  | SPY      |        10 |         9 |       0.0000 |           0.0053 |     -0.0053 |     0.4444 |               0.6221 |
|          20 |      0.1000 | low_recovery  | SPY      |        20 |         9 |       0.0172 |           0.0097 |      0.0075 |     0.6667 |               0.6588 |
|          20 |      0.1000 | low_recovery  | SPY      |        60 |         6 |       0.0315 |           0.0308 |      0.0006 |     0.5000 |               0.7492 |
|          20 |      0.1000 | low_recovery  | QQQ      |         5 |         9 |      -0.0071 |           0.0035 |     -0.0105 |     0.5556 |               0.5948 |
|          20 |      0.1000 | low_recovery  | QQQ      |        10 |         9 |       0.0012 |           0.0072 |     -0.0060 |     0.4444 |               0.6074 |
|          20 |      0.1000 | low_recovery  | QQQ      |        20 |         9 |       0.0280 |           0.0131 |      0.0150 |     0.6667 |               0.6509 |
|          20 |      0.1000 | low_recovery  | QQQ      |        60 |         6 |       0.0375 |           0.0423 |     -0.0048 |     0.5000 |               0.7001 |
|          20 |      0.1000 | low_recovery  | SOXX     |         5 |         9 |      -0.0009 |           0.0060 |     -0.0069 |     0.4444 |               0.5675 |
|          20 |      0.1000 | low_recovery  | SOXX     |        10 |         9 |       0.0109 |           0.0122 |     -0.0013 |     0.4444 |               0.5771 |
|          20 |      0.1000 | low_recovery  | SOXX     |        20 |         9 |       0.0580 |           0.0223 |      0.0357 |     0.5556 |               0.5819 |
|          20 |      0.1000 | low_recovery  | SOXX     |        60 |         6 |       0.0816 |           0.0675 |      0.0141 |     0.5000 |               0.6489 |
|          20 |      0.1000 | low_recovery  | IWM      |         5 |         9 |      -0.0047 |           0.0018 |     -0.0065 |     0.5556 |               0.5267 |
|          20 |      0.1000 | low_recovery  | IWM      |        10 |         9 |      -0.0042 |           0.0039 |     -0.0081 |     0.4444 |               0.5537 |
|          20 |      0.1000 | low_recovery  | IWM      |        20 |         9 |       0.0124 |           0.0068 |      0.0056 |     0.5556 |               0.5542 |
|          20 |      0.1000 | low_recovery  | IWM      |        60 |         6 |       0.0314 |           0.0214 |      0.0100 |     0.5000 |               0.6274 |
|          20 |      0.1000 | low_recovery  | XLK      |         5 |         9 |      -0.0057 |           0.0040 |     -0.0097 |     0.3333 |               0.5714 |
|          20 |      0.1000 | low_recovery  | XLK      |        10 |         9 |       0.0065 |           0.0082 |     -0.0017 |     0.4444 |               0.5879 |
|          20 |      0.1000 | low_recovery  | XLK      |        20 |         9 |       0.0406 |           0.0148 |      0.0258 |     0.6667 |               0.6223 |
|          20 |      0.1000 | low_recovery  | XLK      |        60 |         6 |       0.0569 |           0.0460 |      0.0109 |     0.5000 |               0.6807 |
|          20 |      0.1500 | low_recovery  | SPY      |         5 |        11 |       0.0091 |           0.0025 |      0.0066 |     0.6364 |               0.5901 |
|          20 |      0.1500 | low_recovery  | SPY      |        10 |        11 |       0.0136 |           0.0052 |      0.0084 |     0.7273 |               0.6194 |
|          20 |      0.1500 | low_recovery  | SPY      |        20 |        10 |      -0.0067 |           0.0099 |     -0.0166 |     0.6000 |               0.6594 |
|          20 |      0.1500 | low_recovery  | SPY      |        60 |         7 |       0.0531 |           0.0307 |      0.0224 |     0.7143 |               0.7480 |
|          20 |      0.1500 | low_recovery  | QQQ      |         5 |        11 |       0.0114 |           0.0033 |      0.0081 |     0.6364 |               0.5940 |
|          20 |      0.1500 | low_recovery  | QQQ      |        10 |        11 |       0.0193 |           0.0070 |      0.0123 |     0.6364 |               0.6057 |
|          20 |      0.1500 | low_recovery  | QQQ      |        20 |        10 |      -0.0016 |           0.0134 |     -0.0150 |     0.6000 |               0.6515 |
|          20 |      0.1500 | low_recovery  | QQQ      |        60 |         7 |       0.0811 |           0.0420 |      0.0392 |     0.7143 |               0.6988 |
|          20 |      0.1500 | low_recovery  | SOXX     |         5 |        11 |       0.0106 |           0.0058 |      0.0048 |     0.3636 |               0.5686 |
|          20 |      0.1500 | low_recovery  | SOXX     |        10 |        11 |       0.0135 |           0.0122 |      0.0013 |     0.5455 |               0.5763 |
|          20 |      0.1500 | low_recovery  | SOXX     |        20 |        10 |      -0.0240 |           0.0231 |     -0.0471 |     0.3000 |               0.5844 |
|          20 |      0.1500 | low_recovery  | SOXX     |        60 |         7 |       0.0871 |           0.0675 |      0.0196 |     0.8571 |               0.6465 |
|          20 |      0.1500 | low_recovery  | IWM      |         5 |        11 |       0.0086 |           0.0017 |      0.0069 |     0.6364 |               0.5258 |
|          20 |      0.1500 | low_recovery  | IWM      |        10 |        11 |       0.0072 |           0.0038 |      0.0034 |     0.5455 |               0.5528 |
|          20 |      0.1500 | low_recovery  | IWM      |        20 |        10 |      -0.0208 |           0.0071 |     -0.0280 |     0.5000 |               0.5548 |
|          20 |      0.1500 | low_recovery  | IWM      |        60 |         7 |       0.0349 |           0.0214 |      0.0135 |     0.5714 |               0.6270 |
|          20 |      0.1500 | low_recovery  | XLK      |         5 |        11 |       0.0131 |           0.0039 |      0.0092 |     0.6364 |               0.5686 |
|          20 |      0.1500 | low_recovery  | XLK      |        10 |        11 |       0.0214 |           0.0080 |      0.0133 |     0.5455 |               0.5871 |
|          20 |      0.1500 | low_recovery  | XLK      |        20 |        10 |       0.0002 |           0.0152 |     -0.0150 |     0.6000 |               0.6229 |
|          20 |      0.1500 | low_recovery  | XLK      |        60 |         7 |       0.0972 |           0.0456 |      0.0515 |     0.7143 |               0.6793 |
|          20 |      0.8000 | high_strength | SPY      |         5 |        19 |       0.0040 |           0.0025 |      0.0015 |     0.6842 |               0.5888 |
|          20 |      0.8000 | high_strength | SPY      |        10 |        19 |       0.0063 |           0.0052 |      0.0011 |     0.6316 |               0.6203 |
|          20 |      0.8000 | high_strength | SPY      |        20 |        18 |       0.0079 |           0.0098 |     -0.0018 |     0.5000 |               0.6617 |
|          20 |      0.8000 | high_strength | SPY      |        60 |        11 |       0.0101 |           0.0311 |     -0.0210 |     0.6364 |               0.7490 |
|          20 |      0.8000 | high_strength | QQQ      |         5 |        19 |       0.0047 |           0.0034 |      0.0013 |     0.6842 |               0.5927 |
|          20 |      0.8000 | high_strength | QQQ      |        10 |        19 |       0.0121 |           0.0071 |      0.0050 |     0.6316 |               0.6055 |
|          20 |      0.8000 | high_strength | QQQ      |        20 |        18 |       0.0110 |           0.0133 |     -0.0022 |     0.6111 |               0.6517 |
|          20 |      0.8000 | high_strength | QQQ      |        60 |        11 |       0.0066 |           0.0426 |     -0.0360 |     0.5455 |               0.7006 |
|          20 |      0.8000 | high_strength | SOXX     |         5 |        19 |      -0.0011 |           0.0060 |     -0.0072 |     0.5263 |               0.5672 |
|          20 |      0.8000 | high_strength | SOXX     |        10 |        19 |       0.0137 |           0.0122 |      0.0016 |     0.5789 |               0.5759 |
|          20 |      0.8000 | high_strength | SOXX     |        20 |        18 |       0.0112 |           0.0229 |     -0.0117 |     0.6111 |               0.5811 |
|          20 |      0.8000 | high_strength | SOXX     |        60 |        11 |       0.0268 |           0.0681 |     -0.0413 |     0.4545 |               0.6502 |
|          20 |      0.8000 | high_strength | IWM      |         5 |        19 |       0.0032 |           0.0017 |      0.0015 |     0.5789 |               0.5260 |
|          20 |      0.8000 | high_strength | IWM      |        10 |        19 |       0.0034 |           0.0038 |     -0.0004 |     0.5263 |               0.5533 |
|          20 |      0.8000 | high_strength | IWM      |        20 |        18 |      -0.0044 |           0.0071 |     -0.0114 |     0.4444 |               0.5562 |
|          20 |      0.8000 | high_strength | IWM      |        60 |        11 |      -0.0046 |           0.0218 |     -0.0264 |     0.4545 |               0.6286 |
|          20 |      0.8000 | high_strength | XLK      |         5 |        19 |       0.0048 |           0.0039 |      0.0009 |     0.5789 |               0.5692 |
|          20 |      0.8000 | high_strength | XLK      |        10 |        19 |       0.0121 |           0.0081 |      0.0040 |     0.5789 |               0.5868 |
|          20 |      0.8000 | high_strength | XLK      |        20 |        18 |       0.0123 |           0.0151 |     -0.0027 |     0.6111 |               0.6229 |
|          20 |      0.8000 | high_strength | XLK      |        60 |        11 |       0.0072 |           0.0465 |     -0.0393 |     0.5455 |               0.6811 |
|          20 |      0.9000 | high_strength | SPY      |         5 |        11 |      -0.0009 |           0.0026 |     -0.0035 |     0.6364 |               0.5901 |
|          20 |      0.9000 | high_strength | SPY      |        10 |        11 |       0.0019 |           0.0053 |     -0.0034 |     0.6364 |               0.6204 |
|          20 |      0.9000 | high_strength | SPY      |        20 |        10 |       0.0044 |           0.0098 |     -0.0054 |     0.6000 |               0.6594 |
|          20 |      0.9000 | high_strength | SPY      |        60 |         6 |       0.0217 |           0.0309 |     -0.0092 |     0.6667 |               0.7482 |
|          20 |      0.9000 | high_strength | QQQ      |         5 |        11 |      -0.0015 |           0.0034 |     -0.0050 |     0.5455 |               0.5949 |
|          20 |      0.9000 | high_strength | QQQ      |        10 |        11 |       0.0038 |           0.0072 |     -0.0033 |     0.6364 |               0.6057 |
|          20 |      0.9000 | high_strength | QQQ      |        20 |        10 |       0.0051 |           0.0133 |     -0.0082 |     0.6000 |               0.6515 |
|          20 |      0.9000 | high_strength | QQQ      |        60 |         6 |       0.0189 |           0.0424 |     -0.0235 |     0.6667 |               0.6991 |
|          20 |      0.9000 | high_strength | SOXX     |         5 |        11 |      -0.0062 |           0.0060 |     -0.0123 |     0.5455 |               0.5667 |
|          20 |      0.9000 | high_strength | SOXX     |        10 |        11 |      -0.0004 |           0.0123 |     -0.0127 |     0.4545 |               0.5773 |
|          20 |      0.9000 | high_strength | SOXX     |        20 |        10 |      -0.0068 |           0.0229 |     -0.0298 |     0.7000 |               0.5805 |
|          20 |      0.9000 | high_strength | SOXX     |        60 |         6 |       0.0402 |           0.0678 |     -0.0275 |     0.5000 |               0.6489 |
|          20 |      0.9000 | high_strength | IWM      |         5 |        11 |      -0.0001 |           0.0017 |     -0.0019 |     0.6364 |               0.5258 |
|          20 |      0.9000 | high_strength | IWM      |        10 |        11 |       0.0041 |           0.0038 |      0.0004 |     0.5455 |               0.5528 |
|          20 |      0.9000 | high_strength | IWM      |        20 |        10 |      -0.0054 |           0.0070 |     -0.0124 |     0.5000 |               0.5548 |
|          20 |      0.9000 | high_strength | IWM      |        60 |         6 |       0.0245 |           0.0215 |      0.0030 |     0.5000 |               0.6274 |
|          20 |      0.9000 | high_strength | XLK      |         5 |        11 |      -0.0008 |           0.0040 |     -0.0049 |     0.6364 |               0.5686 |
|          20 |      0.9000 | high_strength | XLK      |        10 |        11 |       0.0046 |           0.0082 |     -0.0036 |     0.6364 |               0.5861 |
|          20 |      0.9000 | high_strength | XLK      |        20 |        10 |       0.0089 |           0.0151 |     -0.0062 |     0.6000 |               0.6229 |
|          20 |      0.9000 | high_strength | XLK      |        60 |         6 |       0.0304 |           0.0461 |     -0.0157 |     0.6667 |               0.6796 |
|          20 |      0.9500 | high_strength | SPY      |         5 |         6 |      -0.0050 |           0.0026 |     -0.0076 |     0.8333 |               0.5891 |
|          20 |      0.9500 | high_strength | SPY      |        10 |         5 |      -0.0049 |           0.0053 |     -0.0102 |     0.6000 |               0.6206 |
|          20 |      0.9500 | high_strength | SPY      |        20 |         5 |       0.0061 |           0.0098 |     -0.0036 |     0.6000 |               0.6591 |
|          20 |      0.9500 | high_strength | SPY      |        60 |         3 |       0.0634 |           0.0308 |      0.0326 |     0.6667 |               0.7480 |
|          20 |      0.9500 | high_strength | QQQ      |         5 |         6 |      -0.0004 |           0.0034 |     -0.0038 |     0.8333 |               0.5930 |
|          20 |      0.9500 | high_strength | QQQ      |        10 |         5 |      -0.0041 |           0.0072 |     -0.0113 |     0.4000 |               0.6070 |
|          20 |      0.9500 | high_strength | QQQ      |        20 |         5 |       0.0133 |           0.0132 |      0.0001 |     0.6000 |               0.6513 |
|          20 |      0.9500 | high_strength | QQQ      |        60 |         3 |       0.0826 |           0.0421 |      0.0404 |     0.6667 |               0.6990 |
|          20 |      0.9500 | high_strength | SOXX     |         5 |         6 |       0.0155 |           0.0058 |      0.0097 |     0.6667 |               0.5659 |
|          20 |      0.9500 | high_strength | SOXX     |        10 |         5 |      -0.0141 |           0.0123 |     -0.0264 |     0.4000 |               0.5768 |
|          20 |      0.9500 | high_strength | SOXX     |        20 |         5 |       0.0027 |           0.0228 |     -0.0201 |     0.6000 |               0.5815 |
|          20 |      0.9500 | high_strength | SOXX     |        60 |         3 |       0.1304 |           0.0674 |      0.0630 |     0.6667 |               0.6480 |
|          20 |      0.9500 | high_strength | IWM      |         5 |         6 |       0.0021 |           0.0017 |      0.0004 |     0.6667 |               0.5262 |
|          20 |      0.9500 | high_strength | IWM      |        10 |         5 |       0.0024 |           0.0038 |     -0.0014 |     0.6000 |               0.5525 |
|          20 |      0.9500 | high_strength | IWM      |        20 |         5 |       0.0113 |           0.0068 |      0.0044 |     0.6000 |               0.5540 |
|          20 |      0.9500 | high_strength | IWM      |        60 |         3 |       0.0742 |           0.0213 |      0.0528 |     0.6667 |               0.6265 |
|          20 |      0.9500 | high_strength | XLK      |         5 |         6 |       0.0014 |           0.0040 |     -0.0026 |     0.8333 |               0.5678 |
|          20 |      0.9500 | high_strength | XLK      |        10 |         5 |      -0.0034 |           0.0082 |     -0.0116 |     0.4000 |               0.5875 |
|          20 |      0.9500 | high_strength | XLK      |        20 |         5 |       0.0115 |           0.0150 |     -0.0036 |     0.6000 |               0.6228 |
|          20 |      0.9500 | high_strength | XLK      |        60 |         3 |       0.0978 |           0.0459 |      0.0519 |     0.6667 |               0.6796 |
|          50 |      0.0500 | low_recovery  | SPY      |         5 |         6 |      -0.0203 |           0.0026 |     -0.0228 |     0.3333 |               0.5938 |
|          50 |      0.0500 | low_recovery  | SPY      |        10 |         6 |      -0.0165 |           0.0054 |     -0.0219 |     0.1667 |               0.6269 |
|          50 |      0.0500 | low_recovery  | SPY      |        20 |         6 |       0.0187 |           0.0108 |      0.0079 |     0.8333 |               0.6677 |
|          50 |      0.0500 | low_recovery  | SPY      |        60 |         5 |       0.0228 |           0.0355 |     -0.0127 |     0.6000 |               0.7722 |
|          50 |      0.0500 | low_recovery  | QQQ      |         5 |         6 |      -0.0330 |           0.0036 |     -0.0366 |     0.1667 |               0.5988 |
|          50 |      0.0500 | low_recovery  | QQQ      |        10 |         6 |      -0.0247 |           0.0075 |     -0.0322 |     0.1667 |               0.6128 |
|          50 |      0.0500 | low_recovery  | QQQ      |        20 |         6 |       0.0177 |           0.0149 |      0.0028 |     0.6667 |               0.6606 |
|          50 |      0.0500 | low_recovery  | QQQ      |        60 |         5 |       0.0149 |           0.0487 |     -0.0338 |     0.4000 |               0.7226 |
|          50 |      0.0500 | low_recovery  | SOXX     |         5 |         6 |      -0.0637 |           0.0067 |     -0.0704 |     0.1667 |               0.5719 |
|          50 |      0.0500 | low_recovery  | SOXX     |        10 |         6 |      -0.0501 |           0.0134 |     -0.0635 |     0.1667 |               0.5848 |
|          50 |      0.0500 | low_recovery  | SOXX     |        20 |         6 |       0.0191 |           0.0256 |     -0.0065 |     0.5000 |               0.5957 |
|          50 |      0.0500 | low_recovery  | SOXX     |        60 |         5 |       0.0388 |           0.0752 |     -0.0364 |     0.6000 |               0.6688 |
|          50 |      0.0500 | low_recovery  | IWM      |         5 |         6 |      -0.0267 |           0.0019 |     -0.0286 |     0.1667 |               0.5299 |
|          50 |      0.0500 | low_recovery  | IWM      |        10 |         6 |      -0.0205 |           0.0042 |     -0.0247 |     0.3333 |               0.5567 |
|          50 |      0.0500 | low_recovery  | IWM      |        20 |         6 |       0.0165 |           0.0084 |      0.0081 |     0.5000 |               0.5633 |
|          50 |      0.0500 | low_recovery  | IWM      |        60 |         5 |       0.0124 |           0.0263 |     -0.0138 |     0.6000 |               0.6466 |
|          50 |      0.0500 | low_recovery  | XLK      |         5 |         6 |      -0.0332 |           0.0042 |     -0.0375 |     0.1667 |               0.5749 |
|          50 |      0.0500 | low_recovery  | XLK      |        10 |         6 |      -0.0236 |           0.0086 |     -0.0322 |     0.1667 |               0.5928 |
|          50 |      0.0500 | low_recovery  | XLK      |        20 |         6 |       0.0319 |           0.0165 |      0.0154 |     0.8333 |               0.6312 |
|          50 |      0.0500 | low_recovery  | XLK      |        60 |         5 |       0.0364 |           0.0517 |     -0.0153 |     0.4000 |               0.7025 |
|          50 |      0.1000 | low_recovery  | SPY      |         5 |         8 |       0.0070 |           0.0024 |      0.0046 |     0.5000 |               0.5930 |
|          50 |      0.1000 | low_recovery  | SPY      |        10 |         8 |      -0.0084 |           0.0054 |     -0.0137 |     0.2500 |               0.6271 |
|          50 |      0.1000 | low_recovery  | SPY      |        20 |         7 |       0.0006 |           0.0109 |     -0.0102 |     0.5714 |               0.6694 |
|          50 |      0.1000 | low_recovery  | SPY      |        60 |         5 |       0.0289 |           0.0355 |     -0.0066 |     0.8000 |               0.7711 |
|          50 |      0.1000 | low_recovery  | QQQ      |         5 |         8 |       0.0064 |           0.0033 |      0.0030 |     0.5000 |               0.5970 |
|          50 |      0.1000 | low_recovery  | QQQ      |        10 |         8 |      -0.0087 |           0.0074 |     -0.0161 |     0.3750 |               0.6121 |
|          50 |      0.1000 | low_recovery  | QQQ      |        20 |         7 |       0.0014 |           0.0150 |     -0.0136 |     0.4286 |               0.6623 |
|          50 |      0.1000 | low_recovery  | QQQ      |        60 |         5 |       0.0233 |           0.0487 |     -0.0254 |     0.6000 |               0.7215 |
|          50 |      0.1000 | low_recovery  | SOXX     |         5 |         8 |      -0.0143 |           0.0064 |     -0.0207 |     0.2500 |               0.5720 |
|          50 |      0.1000 | low_recovery  | SOXX     |        10 |         8 |      -0.0248 |           0.0134 |     -0.0381 |     0.2500 |               0.5849 |
|          50 |      0.1000 | low_recovery  | SOXX     |        20 |         7 |      -0.0229 |           0.0259 |     -0.0488 |     0.2857 |               0.5974 |
|          50 |      0.1000 | low_recovery  | SOXX     |        60 |         5 |       0.0251 |           0.0753 |     -0.0502 |     0.6000 |               0.6688 |
|          50 |      0.1000 | low_recovery  | IWM      |         5 |         8 |       0.0063 |           0.0017 |      0.0046 |     0.5000 |               0.5280 |
|          50 |      0.1000 | low_recovery  | IWM      |        10 |         8 |      -0.0079 |           0.0041 |     -0.0120 |     0.2500 |               0.5578 |
|          50 |      0.1000 | low_recovery  | IWM      |        20 |         7 |      -0.0062 |           0.0086 |     -0.0148 |     0.4286 |               0.5639 |
|          50 |      0.1000 | low_recovery  | IWM      |        60 |         5 |       0.0251 |           0.0262 |     -0.0012 |     0.8000 |               0.6456 |
|          50 |      0.1000 | low_recovery  | XLK      |         5 |         8 |       0.0054 |           0.0040 |      0.0014 |     0.5000 |               0.5730 |
|          50 |      0.1000 | low_recovery  | XLK      |        10 |         8 |      -0.0081 |           0.0086 |     -0.0167 |     0.3750 |               0.5920 |
|          50 |      0.1000 | low_recovery  | XLK      |        20 |         7 |       0.0049 |           0.0167 |     -0.0118 |     0.5714 |               0.6329 |
|          50 |      0.1000 | low_recovery  | XLK      |        60 |         5 |       0.0388 |           0.0517 |     -0.0129 |     0.6000 |               0.7015 |
|          50 |      0.1500 | low_recovery  | SPY      |         5 |         8 |       0.0096 |           0.0024 |      0.0072 |     0.5000 |               0.5930 |
|          50 |      0.1500 | low_recovery  | SPY      |        10 |         8 |       0.0191 |           0.0051 |      0.0140 |     0.7500 |               0.6231 |
|          50 |      0.1500 | low_recovery  | SPY      |        20 |         7 |       0.0364 |           0.0106 |      0.0258 |     0.8571 |               0.6673 |
|          50 |      0.1500 | low_recovery  | SPY      |        60 |         6 |       0.0533 |           0.0353 |      0.0180 |     0.8333 |               0.7709 |
|          50 |      0.1500 | low_recovery  | QQQ      |         5 |         8 |       0.0148 |           0.0033 |      0.0116 |     0.5000 |               0.5970 |
|          50 |      0.1500 | low_recovery  | QQQ      |        10 |         8 |       0.0267 |           0.0071 |      0.0195 |     0.7500 |               0.6090 |
|          50 |      0.1500 | low_recovery  | QQQ      |        20 |         7 |       0.0491 |           0.0147 |      0.0344 |     0.8571 |               0.6592 |
|          50 |      0.1500 | low_recovery  | QQQ      |        60 |         6 |       0.0611 |           0.0485 |      0.0126 |     0.8333 |               0.7202 |
|          50 |      0.1500 | low_recovery  | SOXX     |         5 |         8 |       0.0238 |           0.0061 |      0.0177 |     0.7500 |               0.5680 |
|          50 |      0.1500 | low_recovery  | SOXX     |        10 |         8 |       0.0626 |           0.0127 |      0.0499 |     0.7500 |               0.5809 |
|          50 |      0.1500 | low_recovery  | SOXX     |        20 |         7 |       0.0932 |           0.0250 |      0.0682 |     0.7143 |               0.5943 |
|          50 |      0.1500 | low_recovery  | SOXX     |        60 |         6 |       0.1040 |           0.0749 |      0.0292 |     0.6667 |               0.6684 |
|          50 |      0.1500 | low_recovery  | IWM      |         5 |         8 |       0.0054 |           0.0017 |      0.0037 |     0.5000 |               0.5280 |
|          50 |      0.1500 | low_recovery  | IWM      |        10 |         8 |       0.0300 |           0.0038 |      0.0262 |     0.8750 |               0.5528 |
|          50 |      0.1500 | low_recovery  | IWM      |        20 |         7 |       0.0403 |           0.0082 |      0.0321 |     0.7143 |               0.5619 |
|          50 |      0.1500 | low_recovery  | IWM      |        60 |         6 |       0.0519 |           0.0260 |      0.0259 |     0.8333 |               0.6452 |
|          50 |      0.1500 | low_recovery  | XLK      |         5 |         8 |       0.0187 |           0.0039 |      0.0148 |     0.5000 |               0.5730 |
|          50 |      0.1500 | low_recovery  | XLK      |        10 |         8 |       0.0390 |           0.0082 |      0.0308 |     0.7500 |               0.5889 |
|          50 |      0.1500 | low_recovery  | XLK      |        20 |         7 |       0.0611 |           0.0163 |      0.0448 |     0.8571 |               0.6308 |
|          50 |      0.1500 | low_recovery  | XLK      |        60 |         6 |       0.0773 |           0.0515 |      0.0258 |     0.8333 |               0.7001 |
|          50 |      0.8000 | high_strength | SPY      |         5 |        12 |       0.0021 |           0.0024 |     -0.0003 |     0.4167 |               0.5944 |
|          50 |      0.8000 | high_strength | SPY      |        10 |        12 |       0.0150 |           0.0051 |      0.0099 |     0.8333 |               0.6216 |
|          50 |      0.8000 | high_strength | SPY      |        20 |        12 |       0.0208 |           0.0107 |      0.0101 |     0.8333 |               0.6667 |
|          50 |      0.8000 | high_strength | SPY      |        60 |         8 |       0.0227 |           0.0356 |     -0.0129 |     0.6250 |               0.7725 |
|          50 |      0.8000 | high_strength | QQQ      |         5 |        12 |       0.0017 |           0.0034 |     -0.0017 |     0.4167 |               0.5984 |
|          50 |      0.8000 | high_strength | QQQ      |        10 |        12 |       0.0201 |           0.0071 |      0.0130 |     0.8333 |               0.6075 |
|          50 |      0.8000 | high_strength | QQQ      |        20 |        12 |       0.0240 |           0.0148 |      0.0092 |     0.6667 |               0.6606 |
|          50 |      0.8000 | high_strength | QQQ      |        60 |         8 |       0.0294 |           0.0487 |     -0.0193 |     0.6250 |               0.7217 |
|          50 |      0.8000 | high_strength | SOXX     |         5 |        12 |      -0.0028 |           0.0064 |     -0.0092 |     0.4167 |               0.5713 |
|          50 |      0.8000 | high_strength | SOXX     |        10 |        12 |       0.0248 |           0.0129 |      0.0118 |     0.6667 |               0.5812 |
|          50 |      0.8000 | high_strength | SOXX     |        20 |        12 |       0.0365 |           0.0254 |      0.0111 |     0.7500 |               0.5933 |
|          50 |      0.8000 | high_strength | SOXX     |        60 |         8 |       0.0486 |           0.0753 |     -0.0266 |     0.6250 |               0.6688 |
|          50 |      0.8000 | high_strength | IWM      |         5 |        12 |       0.0036 |           0.0017 |      0.0018 |     0.5833 |               0.5271 |
|          50 |      0.8000 | high_strength | IWM      |        10 |        12 |       0.0144 |           0.0039 |      0.0104 |     0.6667 |               0.5540 |
|          50 |      0.8000 | high_strength | IWM      |        20 |        12 |       0.0275 |           0.0082 |      0.0193 |     0.7500 |               0.5607 |
|          50 |      0.8000 | high_strength | IWM      |        60 |         8 |       0.0200 |           0.0263 |     -0.0062 |     0.6250 |               0.6466 |
|          50 |      0.8000 | high_strength | XLK      |         5 |        12 |      -0.0009 |           0.0041 |     -0.0049 |     0.4167 |               0.5743 |
|          50 |      0.8000 | high_strength | XLK      |        10 |        12 |       0.0187 |           0.0083 |      0.0105 |     0.6667 |               0.5893 |
|          50 |      0.8000 | high_strength | XLK      |        20 |        12 |       0.0230 |           0.0165 |      0.0065 |     0.7500 |               0.6310 |
|          50 |      0.8000 | high_strength | XLK      |        60 |         8 |       0.0269 |           0.0518 |     -0.0250 |     0.6250 |               0.7016 |
|          50 |      0.9000 | high_strength | SPY      |         5 |         8 |       0.0057 |           0.0024 |      0.0033 |     0.7500 |               0.5910 |
|          50 |      0.9000 | high_strength | SPY      |        10 |         8 |       0.0187 |           0.0051 |      0.0136 |     0.8750 |               0.6221 |
|          50 |      0.9000 | high_strength | SPY      |        20 |         8 |       0.0026 |           0.0109 |     -0.0083 |     0.5000 |               0.6701 |
|          50 |      0.9000 | high_strength | SPY      |        60 |         6 |       0.0242 |           0.0355 |     -0.0113 |     0.6667 |               0.7719 |
|          50 |      0.9000 | high_strength | QQQ      |         5 |         8 |       0.0078 |           0.0033 |      0.0045 |     0.5000 |               0.5970 |
|          50 |      0.9000 | high_strength | QQQ      |        10 |         8 |       0.0222 |           0.0071 |      0.0150 |     0.8750 |               0.6080 |
|          50 |      0.9000 | high_strength | QQQ      |        20 |         8 |      -0.0015 |           0.0151 |     -0.0165 |     0.5000 |               0.6619 |
|          50 |      0.9000 | high_strength | QQQ      |        60 |         6 |       0.0306 |           0.0487 |     -0.0181 |     0.6667 |               0.7212 |
|          50 |      0.9000 | high_strength | SOXX     |         5 |         8 |       0.0143 |           0.0062 |      0.0080 |     0.7500 |               0.5680 |
|          50 |      0.9000 | high_strength | SOXX     |        10 |         8 |       0.0322 |           0.0129 |      0.0193 |     0.8750 |               0.5799 |
|          50 |      0.9000 | high_strength | SOXX     |        20 |         8 |       0.0113 |           0.0256 |     -0.0144 |     0.6250 |               0.5949 |
|          50 |      0.9000 | high_strength | SOXX     |        60 |         6 |       0.0940 |           0.0749 |      0.0191 |     0.6667 |               0.6684 |
|          50 |      0.9000 | high_strength | IWM      |         5 |         8 |       0.0084 |           0.0017 |      0.0067 |     0.6250 |               0.5270 |
|          50 |      0.9000 | high_strength | IWM      |        10 |         8 |       0.0229 |           0.0039 |      0.0190 |     0.7500 |               0.5538 |
|          50 |      0.9000 | high_strength | IWM      |        20 |         8 |       0.0011 |           0.0085 |     -0.0074 |     0.5000 |               0.5635 |
|          50 |      0.9000 | high_strength | IWM      |        60 |         6 |       0.0184 |           0.0263 |     -0.0078 |     0.6667 |               0.6463 |
|          50 |      0.9000 | high_strength | XLK      |         5 |         8 |       0.0091 |           0.0040 |      0.0051 |     0.6250 |               0.5720 |
|          50 |      0.9000 | high_strength | XLK      |        10 |         8 |       0.0212 |           0.0083 |      0.0129 |     0.8750 |               0.5879 |
|          50 |      0.9000 | high_strength | XLK      |        20 |         8 |      -0.0023 |           0.0168 |     -0.0191 |     0.5000 |               0.6335 |
|          50 |      0.9000 | high_strength | XLK      |        60 |         6 |       0.0358 |           0.0517 |     -0.0159 |     0.6667 |               0.7012 |
|          50 |      0.9500 | high_strength | SPY      |         5 |         5 |       0.0089 |           0.0024 |      0.0065 |     0.8000 |               0.5912 |
|          50 |      0.9500 | high_strength | SPY      |        10 |         5 |       0.0231 |           0.0052 |      0.0180 |     0.8000 |               0.6232 |
|          50 |      0.9500 | high_strength | SPY      |        20 |         5 |      -0.0026 |           0.0109 |     -0.0134 |     0.4000 |               0.6700 |
|          50 |      0.9500 | high_strength | SPY      |        60 |         5 |       0.0258 |           0.0355 |     -0.0097 |     0.6000 |               0.7722 |
|          50 |      0.9500 | high_strength | QQQ      |         5 |         5 |       0.0109 |           0.0033 |      0.0075 |     0.8000 |               0.5952 |
|          50 |      0.9500 | high_strength | QQQ      |        10 |         5 |       0.0268 |           0.0072 |      0.0197 |     0.8000 |               0.6092 |
|          50 |      0.9500 | high_strength | QQQ      |        20 |         5 |      -0.0079 |           0.0150 |     -0.0230 |     0.4000 |               0.6619 |
|          50 |      0.9500 | high_strength | QQQ      |        60 |         5 |       0.0255 |           0.0487 |     -0.0232 |     0.6000 |               0.7215 |
|          50 |      0.9500 | high_strength | SOXX     |         5 |         5 |       0.0141 |           0.0062 |      0.0079 |     0.6000 |               0.5693 |
|          50 |      0.9500 | high_strength | SOXX     |        10 |         5 |       0.0356 |           0.0129 |      0.0226 |     1.0000 |               0.5802 |
|          50 |      0.9500 | high_strength | SOXX     |        20 |         5 |      -0.0049 |           0.0257 |     -0.0305 |     0.4000 |               0.5962 |
|          50 |      0.9500 | high_strength | SOXX     |        60 |         5 |       0.0436 |           0.0752 |     -0.0316 |     0.6000 |               0.6688 |
|          50 |      0.9500 | high_strength | IWM      |         5 |         5 |       0.0085 |           0.0017 |      0.0068 |     0.8000 |               0.5264 |
|          50 |      0.9500 | high_strength | IWM      |        10 |         5 |       0.0239 |           0.0039 |      0.0200 |     0.8000 |               0.5541 |
|          50 |      0.9500 | high_strength | IWM      |        20 |         5 |      -0.0201 |           0.0086 |     -0.0287 |     0.2000 |               0.5648 |
|          50 |      0.9500 | high_strength | IWM      |        60 |         5 |       0.0134 |           0.0263 |     -0.0129 |     0.6000 |               0.6466 |
|          50 |      0.9500 | high_strength | XLK      |         5 |         5 |       0.0122 |           0.0040 |      0.0082 |     1.0000 |               0.5703 |
|          50 |      0.9500 | high_strength | XLK      |        10 |         5 |       0.0255 |           0.0083 |      0.0171 |     0.8000 |               0.5892 |
|          50 |      0.9500 | high_strength | XLK      |        20 |         5 |      -0.0053 |           0.0167 |     -0.0220 |     0.4000 |               0.6336 |
|          50 |      0.9500 | high_strength | XLK      |        60 |         5 |       0.0272 |           0.0518 |     -0.0246 |     0.6000 |               0.7015 |

</details>

## Reproduction

- Manifest version: v1.2
- Manifest SHA256: `a3e9ed99410dd871c085a09075e414f1014903f3dd917020d23ddcb1d67302fa`
- Git commit: `4d28be2`
- CLI command: `python scripts/run_breadth_pctile_verification.py`

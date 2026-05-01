# Breadth Absolute Upcross Verification — absolute_v1

**Manifest version**: absolute_v1
**Manifest SHA256**: `12627c60dca13230993c8d86403ade2f4b6428e777467b085f5a27fc936b54a8`
**Frozen at**: 2026-05-01
**Signal mode**: absolute
**Data sample**: 2021-02-01 → 2026-04-28
**Effective sample**: 2021-03-01 → 2026-04-28 (5.03 effective years)
**Universe**: $1B+ PIT, with_delisted_partial overlay
**Primary cell (主)**: SPY 10d
**Sensitivity cell (副)**: QQQ 10d
**Strategy costs**: 10bp one-way (20bp roundtrip)

## Top-Level Verdict

- **Cluster passes (主表)**: 0
- **Cluster passes (副表 QQQ)**: 0
- **SPY+QQQ 双通过 cluster**: 0 ← 最强证据
- **Isolated passes (主表)**: 0
- **Verdict**: **REJECT_NO_SIGNAL**

## Sensitivity Comparison (主 vs 副)

|   ma_window |   threshold | event_type    |   passes_spy_10d |   passes_qqq_10d | verdict   |
|------------:|------------:|:--------------|-----------------:|-----------------:|:----------|
|          20 |        0.2  | low_recovery  |                1 |                1 | Neither   |
|          20 |        0.25 | low_recovery  |                0 |                0 | Neither   |
|          20 |        0.3  | low_recovery  |                2 |                2 | Neither   |
|          20 |        0.7  | high_strength |                1 |                1 | Neither   |
|          20 |        0.75 | high_strength |                1 |                1 | Neither   |
|          20 |        0.8  | high_strength |                2 |                1 | Neither   |
|          50 |        0.2  | low_recovery  |                2 |                2 | Neither   |
|          50 |        0.25 | low_recovery  |                2 |                2 | Neither   |
|          50 |        0.3  | low_recovery  |                1 |                1 | Neither   |
|          50 |        0.7  | high_strength |                1 |                1 | Neither   |
|          50 |        0.75 | high_strength |                1 |                1 | Neither   |
|          50 |        0.8  | high_strength |                0 |                0 | Neither   |

Reading: 双过 = strong evidence; 仅 QQQ 过 = AI-bull-leg fit risk; 仅 SPY 过 = breadth has broad effect but no QQQ specificity; 都不过 = 5.03y sample cannot validate.

## Param Summary — Primary (SPY 10d)

|   ma_window |   threshold | event_type    | primary_cell   |   event_n_short |   event_n_long |   effective_years |   events_per_year |   event_hit |   baseline_hit |   hit_lift_pp |   perm_p | perm_sampling_method   |   perm_success_rate |   strategy_cagr_pp |   bnh_cagr_pp |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative |   n_trades |   exposure_pct |   target_same_sign_count | target_same_sign_targets   |   short_horizon_same_sign_count | short_horizon_same_sign_horizons   |   long_horizon_diff | h1_freq_pass   | h2_hit_pass   | h3_target_pass   | h4_short_horizon_pass   | h5_perm_pass   | h6_strategy_pass   |   passes_count_param |
|------------:|------------:|:--------------|:---------------|----------------:|---------------:|------------------:|------------------:|------------:|---------------:|--------------:|---------:|:-----------------------|--------------------:|-------------------:|--------------:|-----------------:|---------------------:|----------------------:|-----------------------------:|-----------:|---------------:|-------------------------:|:---------------------------|--------------------------------:|:-----------------------------------|--------------------:|:---------------|:--------------|:-----------------|:------------------------|:---------------|:-------------------|---------------------:|
|          20 |      0.2000 | low_recovery  | SPY_10d        |              19 |             13 |            5.1468 |            3.6916 |     57.8947 |        61.9685 |       -4.0738 |   0.8811 | rejection              |              1.0000 |            -2.2773 |       12.4206 |         -14.6979 |             -16.8760 |              -10.5598 |                       1.0000 |         19 |        14.6492 |                        0 |                            |                               1 | 20                                 |              0.0072 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.2500 | low_recovery  | SPY_10d        |              22 |             14 |            5.1468 |            4.2745 |     59.0909 |        61.9574 |       -2.8665 |   0.5660 | rejection              |              0.9990 |             0.3984 |       12.4206 |         -12.0222 |             -15.4259 |               -8.0499 |                       1.0000 |         22 |        16.9622 |                        1 | SOXX                       |                               1 | 20                                 |              0.0128 | False          | False         | False            | False                   | False          | False              |                    0 |
|          20 |      0.3000 | low_recovery  | SPY_10d        |              28 |             17 |            5.1468 |            5.4402 |     64.2857 |        61.8557 |        2.4300 |   0.2140 | rejection              |              0.4990 |             3.1281 |       12.4206 |          -9.2925 |             -13.8393 |               -6.5361 |                       1.0000 |         28 |        21.5883 |                        3 | SPY,QQQ,SOXX               |                               2 | 10,20                              |              0.0155 | False          | False         | True             | True                    | False          | False              |                    2 |
|          20 |      0.7000 | high_strength | SPY_10d        |              29 |             16 |            5.1468 |            5.6345 |     62.0690 |        61.9048 |        0.1642 |   0.4469 | rejection              |              0.8740 |             1.8209 |       12.4206 |         -10.5997 |             -13.6793 |               -8.1542 |                       1.0000 |         29 |        22.3593 |                        2 | SPY,QQQ                    |                               2 | 5,10                               |             -0.0101 | False          | False         | False            | True                    | False          | False              |                    1 |
|          20 |      0.7500 | high_strength | SPY_10d        |              23 |             14 |            5.1468 |            4.4688 |     73.9130 |        61.6904 |       12.2227 |   0.4444 | rejection              |              0.9350 |             1.5582 |       12.4206 |         -10.8623 |             -13.9965 |               -8.3118 |                       1.0000 |         23 |        17.7332 |                        2 | SPY,QQQ                    |                               3 | 5,10,20                            |             -0.0045 | False          | False         | False            | True                    | False          | False              |                    1 |
|          20 |      0.8000 | high_strength | SPY_10d        |              14 |              9 |            5.1468 |            2.7201 |     71.4286 |        61.8039 |        9.6246 |   0.4965 | rejection              |              1.0000 |             0.0615 |       12.4206 |         -12.3591 |             -14.6437 |               -9.5518 |                       1.0000 |         13 |        10.0231 |                        0 |                            |                               2 | 5,20                               |             -0.0131 | True           | False         | False            | True                    | False          | False              |                    2 |
|          50 |      0.2000 | low_recovery  | SPY_10d        |               7 |              5 |            5.0278 |            1.3923 |     57.1429 |        61.3419 |       -4.1990 |   0.1668 | rejection              |              1.0000 |             1.5488 |       11.4388 |          -9.8900 |             -11.9159 |               -9.0214 |                       1.0000 |          7 |         5.5249 |                        3 | SPY,QQQ,SOXX               |                               2 | 10,20                              |              0.0538 | False          | False         | True             | True                    | False          | False              |                    2 |
|          50 |      0.2500 | low_recovery  | SPY_10d        |              14 |             10 |            5.0278 |            2.7845 |     64.2857 |        61.2851 |        3.0006 |   0.3701 | rejection              |              0.9420 |             0.3838 |       11.4388 |         -11.0550 |             -13.2723 |               -8.7027 |                       1.0000 |         14 |        11.0497 |                        1 | SOXX                       |                               2 | 5,20                               |              0.0156 | True           | False         | False            | True                    | False          | False              |                    2 |
|          50 |      0.3000 | low_recovery  | SPY_10d        |              17 |             12 |            5.0278 |            3.3812 |     47.0588 |        61.5137 |      -14.4549 |   0.8889 | rejection              |              0.4940 |            -3.4347 |       11.4388 |         -14.8735 |             -16.0117 |              -10.8184 |                       1.0000 |         17 |        13.4175 |                        0 |                            |                               1 | 20                                 |              0.0014 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.7000 | high_strength | SPY_10d        |              19 |             12 |            5.0278 |            3.7790 |     73.6842 |        61.1290 |       12.5552 |   0.6034 | rejection              |              1.0000 |             0.6000 |       11.4388 |         -10.8387 |             -12.9141 |               -9.2870 |                       1.0000 |         19 |        14.9961 |                        0 |                            |                               1 | 20                                 |             -0.0073 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.7500 | high_strength | SPY_10d        |              11 |              8 |            5.0278 |            2.1878 |     63.6364 |        61.2981 |        2.3383 |   0.7023 | rejection              |              1.0000 |            -0.2692 |       11.4388 |         -11.7080 |             -12.7052 |              -10.4164 |                       1.0000 |         11 |         8.6819 |                        0 |                            |                               0 |                                    |             -0.0008 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.8000 | high_strength | SPY_10d        |               6 |              5 |            5.0278 |            1.1934 |     66.6667 |        61.2929 |        5.3738 |   0.4895 | rejection              |              1.0000 |             0.2006 |       11.4388 |         -11.2382 |             -12.1085 |              -10.4188 |                       1.0000 |          6 |         4.7356 |                        1 | SOXX                       |                               1 | 5                                  |             -0.0268 | False          | False         | False            | False                   | False          | False              |                    0 |

## Param Summary — Sensitivity (QQQ 10d)

|   ma_window |   threshold | event_type    | primary_cell   |   event_n_short |   event_n_long |   effective_years |   events_per_year |   event_hit |   baseline_hit |   hit_lift_pp |   perm_p | perm_sampling_method   |   perm_success_rate |   strategy_cagr_pp |   bnh_cagr_pp |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative |   n_trades |   exposure_pct |   target_same_sign_count | target_same_sign_targets   |   short_horizon_same_sign_count | short_horizon_same_sign_horizons   |   long_horizon_diff | h1_freq_pass   | h2_hit_pass   | h3_target_pass   | h4_short_horizon_pass   | h5_perm_pass   | h6_strategy_pass   |   passes_count_param |
|------------:|------------:|:--------------|:---------------|----------------:|---------------:|------------------:|------------------:|------------:|---------------:|--------------:|---------:|:-----------------------|--------------------:|-------------------:|--------------:|-----------------:|---------------------:|----------------------:|-----------------------------:|-----------:|---------------:|-------------------------:|:---------------------------|--------------------------------:|:-----------------------------------|--------------------:|:---------------|:--------------|:-----------------|:------------------------|:---------------|:-------------------|---------------------:|
|          20 |      0.2000 | low_recovery  | QQQ_10d        |              19 |             13 |            5.1468 |            3.6916 |     42.1053 |        60.1575 |      -18.0522 |   0.9281 | rejection              |              1.0000 |            -3.7745 |       14.7704 |         -18.5449 |             -21.1310 |              -13.0732 |                       1.0000 |         19 |        14.6492 |                        0 |                            |                               0 |                                    |              0.0119 | True           | False         | False            | False                   | False          | False              |                    1 |
|          20 |      0.2500 | low_recovery  | QQQ_10d        |              22 |             14 |            5.1468 |            4.2745 |     50.0000 |        60.0631 |      -10.0631 |   0.6080 | rejection              |              0.9990 |             0.1126 |       14.7704 |         -14.6577 |             -18.7144 |               -9.6377 |                       1.0000 |         22 |        16.9622 |                        1 | SOXX                       |                               1 | 20                                 |              0.0175 | False          | False         | False            | False                   | False          | False              |                    0 |
|          20 |      0.3000 | low_recovery  | QQQ_10d        |              28 |             17 |            5.1468 |            5.4402 |     67.8571 |        59.7145 |        8.1426 |   0.2380 | rejection              |              0.4990 |             3.6992 |       14.7704 |         -11.0712 |             -16.7525 |               -8.0067 |                       1.0000 |         28 |        21.5883 |                        3 | SPY,QQQ,SOXX               |                               2 | 10,20                              |              0.0205 | False          | False         | True             | True                    | False          | False              |                    2 |
|          20 |      0.7000 | high_strength | QQQ_10d        |              29 |             16 |            5.1468 |            5.6345 |     55.1724 |        60.0000 |       -4.8276 |   0.4309 | rejection              |              0.8740 |             2.8038 |       14.7704 |         -11.9666 |             -16.8097 |               -8.3994 |                       1.0000 |         29 |        22.3593 |                        2 | SPY,QQQ                    |                               2 | 5,10                               |             -0.0151 | False          | False         | False            | True                    | False          | False              |                    1 |
|          20 |      0.7500 | high_strength | QQQ_10d        |              23 |             14 |            5.1468 |            4.4688 |     69.5652 |        59.7156 |        9.8496 |   0.4669 | rejection              |              0.9350 |             1.9363 |       14.7704 |         -12.8340 |             -17.3547 |               -8.9754 |                       1.0000 |         23 |        17.7332 |                        2 | SPY,QQQ                    |                               3 | 5,10,20                            |             -0.0047 | False          | False         | False            | True                    | False          | False              |                    1 |
|          20 |      0.8000 | high_strength | QQQ_10d        |              14 |              9 |            5.1468 |            2.7201 |     71.4286 |        59.7647 |       11.6639 |   0.4605 | rejection              |              1.0000 |             0.1330 |       14.7704 |         -14.6374 |             -18.0830 |              -10.1935 |                       1.0000 |         13 |        10.0231 |                        0 |                            |                               1 | 5                                  |             -0.0226 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.2000 | low_recovery  | QQQ_10d        |               7 |              5 |            5.0278 |            1.3923 |     57.1429 |        59.3450 |       -2.2022 |   0.1958 | rejection              |              1.0000 |             1.9648 |       13.9780 |         -12.0132 |             -14.9732 |              -10.5679 |                       1.0000 |          7 |         5.5249 |                        3 | SPY,QQQ,SOXX               |                               2 | 10,20                              |              0.0705 | False          | False         | True             | True                    | False          | False              |                    2 |
|          50 |      0.2500 | low_recovery  | QQQ_10d        |              14 |             10 |            5.0278 |            2.7845 |     50.0000 |        59.4378 |       -9.4378 |   0.4878 | rejection              |              0.9420 |            -0.7360 |       13.9780 |         -14.7140 |             -17.0553 |              -11.2421 |                       1.0000 |         14 |        11.0497 |                        1 | SOXX                       |                               2 | 5,20                               |              0.0054 | True           | False         | False            | True                    | False          | False              |                    2 |
|          50 |      0.3000 | low_recovery  | QQQ_10d        |              17 |             12 |            5.0278 |            3.3812 |     35.2941 |        59.6618 |      -24.3677 |   0.9394 | rejection              |              0.4940 |            -5.8494 |       13.9780 |         -19.8274 |             -20.9840 |              -14.1742 |                       1.0000 |         17 |        13.4175 |                        0 |                            |                               1 | 20                                 |             -0.0061 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.7000 | high_strength | QQQ_10d        |              19 |             12 |            5.0278 |            3.7790 |     63.1579 |        59.2742 |        3.8837 |   0.6803 | rejection              |              1.0000 |            -0.0664 |       13.9780 |         -14.0444 |             -16.9848 |              -10.8979 |                       1.0000 |         19 |        14.9961 |                        0 |                            |                               0 |                                    |             -0.0090 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.7500 | high_strength | QQQ_10d        |              11 |              8 |            5.0278 |            2.1878 |     45.4545 |        59.4551 |      -14.0006 |   0.7622 | rejection              |              1.0000 |            -0.6402 |       13.9780 |         -14.6183 |             -16.1807 |              -12.4962 |                       1.0000 |         11 |         8.6819 |                        0 |                            |                               1 | 5                                  |              0.0012 | True           | False         | False            | False                   | False          | False              |                    1 |
|          50 |      0.8000 | high_strength | QQQ_10d        |               6 |              5 |            5.0278 |            1.1934 |     33.3333 |        59.4573 |      -26.1240 |   0.5115 | rejection              |              1.0000 |             0.2540 |       13.9780 |         -13.7240 |             -15.4187 |              -12.4793 |                       1.0000 |          6 |         4.7356 |                        1 | SOXX                       |                               1 | 5                                  |             -0.0356 | False          | False         | False            | False                   | False          | False              |                    0 |

## Cluster Pattern Detail — Primary

_No clusters found._

## Cluster Pattern Detail — Sensitivity

_No clusters found._

## Bootstrap CI for Excess CAGR (primary)

|   ma_window |   threshold | event_type    |   excess_cagr_pp |   excess_cagr_ci_low |   excess_cagr_ci_high |   excess_cagr_share_negative | h6_strategy_pass   |
|------------:|------------:|:--------------|-----------------:|---------------------:|----------------------:|-----------------------------:|:-------------------|
|          20 |      0.2000 | low_recovery  |         -14.6979 |             -16.8760 |              -10.5598 |                       1.0000 | False              |
|          20 |      0.2500 | low_recovery  |         -12.0222 |             -15.4259 |               -8.0499 |                       1.0000 | False              |
|          20 |      0.3000 | low_recovery  |          -9.2925 |             -13.8393 |               -6.5361 |                       1.0000 | False              |
|          20 |      0.7000 | high_strength |         -10.5997 |             -13.6793 |               -8.1542 |                       1.0000 | False              |
|          20 |      0.7500 | high_strength |         -10.8623 |             -13.9965 |               -8.3118 |                       1.0000 | False              |
|          20 |      0.8000 | high_strength |         -12.3591 |             -14.6437 |               -9.5518 |                       1.0000 | False              |
|          50 |      0.2000 | low_recovery  |          -9.8900 |             -11.9159 |               -9.0214 |                       1.0000 | False              |
|          50 |      0.2500 | low_recovery  |         -11.0550 |             -13.2723 |               -8.7027 |                       1.0000 | False              |
|          50 |      0.3000 | low_recovery  |         -14.8735 |             -16.0117 |              -10.8184 |                       1.0000 | False              |
|          50 |      0.7000 | high_strength |         -10.8387 |             -12.9141 |               -9.2870 |                       1.0000 | False              |
|          50 |      0.7500 | high_strength |         -11.7080 |             -12.7052 |              -10.4164 |                       1.0000 | False              |
|          50 |      0.8000 | high_strength |         -11.2382 |             -12.1085 |              -10.4188 |                       1.0000 | False              |
_No borderline params (H6 fail but CI upper >= threshold)._


## Permutation Diagnostics (primary)

|   ma_window |   threshold | event_type    |   perm_p | perm_sampling_method   |   perm_success_rate | warn   |
|------------:|------------:|:--------------|---------:|:-----------------------|--------------------:|:-------|
|          20 |      0.2000 | low_recovery  |   0.8811 | rejection              |              1.0000 | False  |
|          20 |      0.2500 | low_recovery  |   0.5660 | rejection              |              0.9990 | False  |
|          20 |      0.3000 | low_recovery  |   0.2140 | rejection              |              0.4990 | True   |
|          20 |      0.7000 | high_strength |   0.4469 | rejection              |              0.8740 | False  |
|          20 |      0.7500 | high_strength |   0.4444 | rejection              |              0.9350 | False  |
|          20 |      0.8000 | high_strength |   0.4965 | rejection              |              1.0000 | False  |
|          50 |      0.2000 | low_recovery  |   0.1668 | rejection              |              1.0000 | False  |
|          50 |      0.2500 | low_recovery  |   0.3701 | rejection              |              0.9420 | False  |
|          50 |      0.3000 | low_recovery  |   0.8889 | rejection              |              0.4940 | True   |
|          50 |      0.7000 | high_strength |   0.6034 | rejection              |              1.0000 | False  |
|          50 |      0.7500 | high_strength |   0.7023 | rejection              |              1.0000 | False  |
|          50 |      0.8000 | high_strength |   0.4895 | rejection              |              1.0000 | False  |

_warn=True flags `perm_success_rate < 0.7`; such cells were re-sampled via sequential fallback or hit warning threshold._

## Isolated Pass Detail — Primary

_No isolated passes._

## Diagnostic: 144-row Verification Table

<details><summary>Click to expand</summary>

|   ma_window |   threshold | event_type    | target   |   horizon |   event_n |   event_mean |   non_event_mean |   mean_diff |   hit_rate |   non_event_hit_rate |
|------------:|------------:|:--------------|:---------|----------:|----------:|-------------:|-----------------:|------------:|-----------:|---------------------:|
|          20 |      0.2000 | low_recovery  | SPY      |         5 |        19 |      -0.0033 |           0.0025 |     -0.0059 |     0.4211 |               0.5929 |
|          20 |      0.2000 | low_recovery  | SPY      |        10 |        19 |      -0.0035 |           0.0051 |     -0.0086 |     0.5789 |               0.6197 |
|          20 |      0.2000 | low_recovery  | SPY      |        20 |        19 |       0.0123 |           0.0098 |      0.0026 |     0.6842 |               0.6611 |
|          20 |      0.2000 | low_recovery  | SPY      |        60 |        12 |       0.0345 |           0.0273 |      0.0072 |     0.6667 |               0.7253 |
|          20 |      0.2000 | low_recovery  | QQQ      |         5 |        19 |      -0.0067 |           0.0031 |     -0.0098 |     0.4211 |               0.5882 |
|          20 |      0.2000 | low_recovery  | QQQ      |        10 |        19 |      -0.0071 |           0.0065 |     -0.0136 |     0.4211 |               0.6016 |
|          20 |      0.2000 | low_recovery  | QQQ      |        20 |        19 |       0.0109 |           0.0124 |     -0.0015 |     0.6316 |               0.6405 |
|          20 |      0.2000 | low_recovery  | QQQ      |        60 |        12 |       0.0467 |           0.0348 |      0.0119 |     0.6667 |               0.6838 |
|          20 |      0.2000 | low_recovery  | SOXX     |         5 |        18 |      -0.0073 |           0.0052 |     -0.0125 |     0.3889 |               0.5622 |
|          20 |      0.2000 | low_recovery  | SOXX     |        10 |        18 |      -0.0059 |           0.0109 |     -0.0168 |     0.4444 |               0.5718 |
|          20 |      0.2000 | low_recovery  | SOXX     |        20 |        18 |       0.0195 |           0.0211 |     -0.0016 |     0.5556 |               0.5920 |
|          20 |      0.2000 | low_recovery  | SOXX     |        60 |        11 |       0.0735 |           0.0570 |      0.0166 |     0.6364 |               0.6353 |
|          20 |      0.2500 | low_recovery  | SPY      |         5 |        22 |       0.0005 |           0.0025 |     -0.0020 |     0.5455 |               0.5912 |
|          20 |      0.2500 | low_recovery  | SPY      |        10 |        22 |       0.0039 |           0.0050 |     -0.0012 |     0.5909 |               0.6196 |
|          20 |      0.2500 | low_recovery  | SPY      |        20 |        22 |       0.0180 |           0.0096 |      0.0084 |     0.6818 |               0.6611 |
|          20 |      0.2500 | low_recovery  | SPY      |        60 |        13 |       0.0400 |           0.0272 |      0.0128 |     0.6923 |               0.7251 |
|          20 |      0.2500 | low_recovery  | QQQ      |         5 |        22 |      -0.0021 |           0.0031 |     -0.0052 |     0.4545 |               0.5881 |
|          20 |      0.2500 | low_recovery  | QQQ      |        10 |        22 |       0.0038 |           0.0063 |     -0.0026 |     0.5000 |               0.6006 |
|          20 |      0.2500 | low_recovery  | QQQ      |        20 |        22 |       0.0205 |           0.0122 |      0.0083 |     0.7273 |               0.6388 |
|          20 |      0.2500 | low_recovery  | QQQ      |        60 |        13 |       0.0522 |           0.0347 |      0.0175 |     0.6923 |               0.6835 |
|          20 |      0.2500 | low_recovery  | SOXX     |         5 |        21 |       0.0010 |           0.0051 |     -0.0041 |     0.4762 |               0.5611 |
|          20 |      0.2500 | low_recovery  | SOXX     |        10 |        21 |       0.0184 |           0.0105 |      0.0079 |     0.5714 |               0.5699 |
|          20 |      0.2500 | low_recovery  | SOXX     |        20 |        21 |       0.0339 |           0.0209 |      0.0130 |     0.6190 |               0.5910 |
|          20 |      0.2500 | low_recovery  | SOXX     |        60 |        12 |       0.0758 |           0.0569 |      0.0189 |     0.6667 |               0.6350 |
|          20 |      0.3000 | low_recovery  | SPY      |         5 |        28 |       0.0020 |           0.0024 |     -0.0005 |     0.6071 |               0.5900 |
|          20 |      0.3000 | low_recovery  | SPY      |        10 |        28 |       0.0083 |           0.0049 |      0.0034 |     0.6429 |               0.6186 |
|          20 |      0.3000 | low_recovery  | SPY      |        20 |        28 |       0.0200 |           0.0096 |      0.0104 |     0.7500 |               0.6595 |
|          20 |      0.3000 | low_recovery  | SPY      |        60 |        16 |       0.0427 |           0.0271 |      0.0155 |     0.7500 |               0.7244 |
|          20 |      0.3000 | low_recovery  | QQQ      |         5 |        28 |       0.0011 |           0.0030 |     -0.0019 |     0.6071 |               0.5853 |
|          20 |      0.3000 | low_recovery  | QQQ      |        10 |        28 |       0.0097 |           0.0062 |      0.0035 |     0.6786 |               0.5971 |
|          20 |      0.3000 | low_recovery  | QQQ      |        20 |        28 |       0.0246 |           0.0121 |      0.0126 |     0.7857 |               0.6371 |
|          20 |      0.3000 | low_recovery  | QQQ      |        60 |        16 |       0.0551 |           0.0347 |      0.0205 |     0.7500 |               0.6827 |
|          20 |      0.3000 | low_recovery  | SOXX     |         5 |        27 |       0.0055 |           0.0050 |      0.0005 |     0.5926 |               0.5590 |
|          20 |      0.3000 | low_recovery  | SOXX     |        10 |        27 |       0.0223 |           0.0104 |      0.0119 |     0.7407 |               0.5662 |
|          20 |      0.3000 | low_recovery  | SOXX     |        20 |        27 |       0.0378 |           0.0207 |      0.0171 |     0.5926 |               0.5914 |
|          20 |      0.3000 | low_recovery  | SOXX     |        60 |        15 |       0.1061 |           0.0565 |      0.0496 |     0.7333 |               0.6341 |
|          20 |      0.7000 | high_strength | SPY      |         5 |        29 |       0.0060 |           0.0023 |      0.0036 |     0.7586 |               0.5866 |
|          20 |      0.7000 | high_strength | SPY      |        10 |        29 |       0.0056 |           0.0050 |      0.0007 |     0.6207 |               0.6190 |
|          20 |      0.7000 | high_strength | SPY      |        20 |        28 |       0.0090 |           0.0098 |     -0.0008 |     0.6786 |               0.6611 |
|          20 |      0.7000 | high_strength | SPY      |        60 |        15 |       0.0174 |           0.0275 |     -0.0101 |     0.6667 |               0.7255 |
|          20 |      0.7000 | high_strength | QQQ      |         5 |        29 |       0.0068 |           0.0029 |      0.0039 |     0.5172 |               0.5874 |
|          20 |      0.7000 | high_strength | QQQ      |        10 |        29 |       0.0079 |           0.0063 |      0.0016 |     0.5517 |               0.6000 |
|          20 |      0.7000 | high_strength | QQQ      |        20 |        28 |       0.0106 |           0.0124 |     -0.0018 |     0.6071 |               0.6411 |
|          20 |      0.7000 | high_strength | QQQ      |        60 |        15 |       0.0200 |           0.0351 |     -0.0151 |     0.6667 |               0.6838 |
|          20 |      0.7000 | high_strength | SOXX     |         5 |        28 |       0.0046 |           0.0050 |     -0.0004 |     0.5000 |               0.5611 |
|          20 |      0.7000 | high_strength | SOXX     |        10 |        28 |       0.0071 |           0.0107 |     -0.0037 |     0.5714 |               0.5699 |
|          20 |      0.7000 | high_strength | SOXX     |        20 |        27 |       0.0109 |           0.0213 |     -0.0104 |     0.5926 |               0.5914 |
|          20 |      0.7000 | high_strength | SOXX     |        60 |        14 |       0.0561 |           0.0571 |     -0.0010 |     0.5714 |               0.6361 |
|          20 |      0.7500 | high_strength | SPY      |         5 |        23 |       0.0079 |           0.0023 |      0.0056 |     0.6957 |               0.5885 |
|          20 |      0.7500 | high_strength | SPY      |        10 |        23 |       0.0060 |           0.0050 |      0.0010 |     0.7391 |               0.6169 |
|          20 |      0.7500 | high_strength | SPY      |        20 |        22 |       0.0116 |           0.0098 |      0.0019 |     0.7273 |               0.6603 |
|          20 |      0.7500 | high_strength | SPY      |        60 |        13 |       0.0229 |           0.0274 |     -0.0045 |     0.7692 |               0.7243 |
|          20 |      0.7500 | high_strength | QQQ      |         5 |        23 |       0.0097 |           0.0029 |      0.0068 |     0.5652 |               0.5862 |
|          20 |      0.7500 | high_strength | QQQ      |        10 |        23 |       0.0074 |           0.0063 |      0.0011 |     0.6957 |               0.5972 |
|          20 |      0.7500 | high_strength | QQQ      |        20 |        22 |       0.0141 |           0.0123 |      0.0017 |     0.6818 |               0.6396 |
|          20 |      0.7500 | high_strength | QQQ      |        60 |        13 |       0.0303 |           0.0350 |     -0.0047 |     0.7692 |               0.6827 |
|          20 |      0.7500 | high_strength | SOXX     |         5 |        22 |       0.0062 |           0.0050 |      0.0012 |     0.5455 |               0.5600 |
|          20 |      0.7500 | high_strength | SOXX     |        10 |        22 |       0.0036 |           0.0108 |     -0.0071 |     0.5000 |               0.5712 |
|          20 |      0.7500 | high_strength | SOXX     |        20 |        21 |       0.0084 |           0.0213 |     -0.0129 |     0.5238 |               0.5926 |
|          20 |      0.7500 | high_strength | SOXX     |        60 |        12 |       0.0470 |           0.0572 |     -0.0102 |     0.5000 |               0.6367 |
|          20 |      0.8000 | high_strength | SPY      |         5 |        14 |       0.0034 |           0.0024 |      0.0010 |     0.5714 |               0.5906 |
|          20 |      0.8000 | high_strength | SPY      |        10 |        14 |       0.0038 |           0.0050 |     -0.0012 |     0.7143 |               0.6180 |
|          20 |      0.8000 | high_strength | SPY      |        20 |        13 |       0.0109 |           0.0098 |      0.0011 |     0.6154 |               0.6619 |
|          20 |      0.8000 | high_strength | SPY      |        60 |         8 |       0.0144 |           0.0274 |     -0.0131 |     0.7500 |               0.7246 |
|          20 |      0.8000 | high_strength | QQQ      |         5 |        14 |       0.0044 |           0.0030 |      0.0014 |     0.5000 |               0.5867 |
|          20 |      0.8000 | high_strength | QQQ      |        10 |        14 |       0.0062 |           0.0063 |     -0.0001 |     0.7143 |               0.5976 |
|          20 |      0.8000 | high_strength | QQQ      |        20 |        13 |       0.0098 |           0.0124 |     -0.0026 |     0.5385 |               0.6414 |
|          20 |      0.8000 | high_strength | QQQ      |        60 |         8 |       0.0125 |           0.0351 |     -0.0226 |     0.6250 |               0.6840 |
|          20 |      0.8000 | high_strength | SOXX     |         5 |        13 |       0.0002 |           0.0051 |     -0.0049 |     0.4615 |               0.5607 |
|          20 |      0.8000 | high_strength | SOXX     |        10 |        13 |       0.0073 |           0.0107 |     -0.0034 |     0.5385 |               0.5703 |
|          20 |      0.8000 | high_strength | SOXX     |        20 |        12 |      -0.0043 |           0.0213 |     -0.0256 |     0.5833 |               0.5915 |
|          20 |      0.8000 | high_strength | SOXX     |        60 |         7 |       0.0121 |           0.0574 |     -0.0453 |     0.4286 |               0.6365 |
|          50 |      0.2000 | low_recovery  | SPY      |         5 |         7 |      -0.0039 |           0.0022 |     -0.0062 |     0.4286 |               0.5871 |
|          50 |      0.2000 | low_recovery  | SPY      |        10 |         7 |       0.0135 |           0.0045 |      0.0090 |     0.5714 |               0.6134 |
|          50 |      0.2000 | low_recovery  | SPY      |        20 |         7 |       0.0327 |           0.0088 |      0.0239 |     0.7143 |               0.6530 |
|          50 |      0.2000 | low_recovery  | SPY      |        60 |         5 |       0.0798 |           0.0260 |      0.0538 |     0.8000 |               0.7176 |
|          50 |      0.2000 | low_recovery  | QQQ      |         5 |         7 |      -0.0032 |           0.0028 |     -0.0060 |     0.5714 |               0.5839 |
|          50 |      0.2000 | low_recovery  | QQQ      |        10 |         7 |       0.0170 |           0.0057 |      0.0112 |     0.5714 |               0.5935 |
|          50 |      0.2000 | low_recovery  | QQQ      |        20 |         7 |       0.0320 |           0.0114 |      0.0206 |     0.5714 |               0.6377 |
|          50 |      0.2000 | low_recovery  | QQQ      |        60 |         5 |       0.1040 |           0.0336 |      0.0705 |     0.6000 |               0.6761 |
|          50 |      0.2000 | low_recovery  | SOXX     |         5 |         7 |      -0.0161 |           0.0051 |     -0.0212 |     0.5714 |               0.5596 |
|          50 |      0.2000 | low_recovery  | SOXX     |        10 |         7 |       0.0127 |           0.0106 |      0.0021 |     0.7143 |               0.5691 |
|          50 |      0.2000 | low_recovery  | SOXX     |        20 |         7 |       0.0424 |           0.0210 |      0.0215 |     0.5714 |               0.5916 |
|          50 |      0.2000 | low_recovery  | SOXX     |        60 |         5 |       0.1625 |           0.0567 |      0.1058 |     0.8000 |               0.6346 |
|          50 |      0.2500 | low_recovery  | SPY      |         5 |        14 |       0.0132 |           0.0021 |      0.0111 |     0.8571 |               0.5832 |
|          50 |      0.2500 | low_recovery  | SPY      |        10 |        14 |       0.0039 |           0.0046 |     -0.0006 |     0.6429 |               0.6129 |
|          50 |      0.2500 | low_recovery  | SPY      |        20 |        14 |       0.0284 |           0.0087 |      0.0197 |     0.7143 |               0.6526 |
|          50 |      0.2500 | low_recovery  | SPY      |        60 |         9 |       0.0416 |           0.0261 |      0.0156 |     0.5556 |               0.7192 |
|          50 |      0.2500 | low_recovery  | QQQ      |         5 |        14 |       0.0160 |           0.0026 |      0.0133 |     0.7857 |               0.5816 |
|          50 |      0.2500 | low_recovery  | QQQ      |        10 |        14 |       0.0002 |           0.0059 |     -0.0056 |     0.5000 |               0.5944 |
|          50 |      0.2500 | low_recovery  | QQQ      |        20 |        14 |       0.0336 |           0.0113 |      0.0223 |     0.7143 |               0.6364 |
|          50 |      0.2500 | low_recovery  | QQQ      |        60 |         9 |       0.0393 |           0.0338 |      0.0054 |     0.5556 |               0.6767 |
|          50 |      0.2500 | low_recovery  | SOXX     |         5 |        14 |       0.0201 |           0.0048 |      0.0152 |     0.6429 |               0.5588 |
|          50 |      0.2500 | low_recovery  | SOXX     |        10 |        14 |       0.0119 |           0.0106 |      0.0013 |     0.5714 |               0.5699 |
|          50 |      0.2500 | low_recovery  | SOXX     |        20 |        14 |       0.0601 |           0.0206 |      0.0394 |     0.6429 |               0.5909 |
|          50 |      0.2500 | low_recovery  | SOXX     |        60 |         9 |       0.0804 |           0.0569 |      0.0235 |     0.5556 |               0.6359 |
|          50 |      0.3000 | low_recovery  | SPY      |         5 |        17 |       0.0009 |           0.0022 |     -0.0013 |     0.5294 |               0.5870 |
|          50 |      0.3000 | low_recovery  | SPY      |        10 |        17 |      -0.0078 |           0.0047 |     -0.0125 |     0.4706 |               0.6151 |
|          50 |      0.3000 | low_recovery  | SPY      |        20 |        17 |       0.0135 |           0.0089 |      0.0046 |     0.6471 |               0.6534 |
|          50 |      0.3000 | low_recovery  | SPY      |        60 |        11 |       0.0276 |           0.0262 |      0.0014 |     0.7273 |               0.7179 |
|          50 |      0.3000 | low_recovery  | QQQ      |         5 |        17 |      -0.0015 |           0.0028 |     -0.0044 |     0.5294 |               0.5846 |
|          50 |      0.3000 | low_recovery  | QQQ      |        10 |        17 |      -0.0147 |           0.0061 |     -0.0208 |     0.3529 |               0.5966 |
|          50 |      0.3000 | low_recovery  | QQQ      |        20 |        17 |       0.0142 |           0.0115 |      0.0027 |     0.6471 |               0.6372 |
|          50 |      0.3000 | low_recovery  | QQQ      |        60 |        11 |       0.0278 |           0.0339 |     -0.0061 |     0.6364 |               0.6761 |
|          50 |      0.3000 | low_recovery  | SOXX     |         5 |        17 |       0.0067 |           0.0050 |      0.0017 |     0.6471 |               0.5585 |
|          50 |      0.3000 | low_recovery  | SOXX     |        10 |        17 |       0.0006 |           0.0108 |     -0.0102 |     0.3529 |               0.5729 |
|          50 |      0.3000 | low_recovery  | SOXX     |        20 |        17 |       0.0312 |           0.0209 |      0.0102 |     0.5294 |               0.5923 |
|          50 |      0.3000 | low_recovery  | SOXX     |        60 |        11 |       0.0732 |           0.0570 |      0.0162 |     0.6364 |               0.6353 |
|          50 |      0.7000 | high_strength | SPY      |         5 |        19 |      -0.0002 |           0.0022 |     -0.0024 |     0.6842 |               0.5847 |
|          50 |      0.7000 | high_strength | SPY      |        10 |        19 |       0.0039 |           0.0046 |     -0.0007 |     0.7368 |               0.6113 |
|          50 |      0.7000 | high_strength | SPY      |        20 |        19 |       0.0090 |           0.0089 |      0.0000 |     0.6842 |               0.6528 |
|          50 |      0.7000 | high_strength | SPY      |        60 |        12 |       0.0190 |           0.0263 |     -0.0073 |     0.6667 |               0.7185 |
|          50 |      0.7000 | high_strength | QQQ      |         5 |        19 |      -0.0004 |           0.0028 |     -0.0032 |     0.4737 |               0.5855 |
|          50 |      0.7000 | high_strength | QQQ      |        10 |        19 |       0.0025 |           0.0059 |     -0.0033 |     0.6316 |               0.5927 |
|          50 |      0.7000 | high_strength | QQQ      |        20 |        19 |       0.0079 |           0.0116 |     -0.0037 |     0.6316 |               0.6374 |
|          50 |      0.7000 | high_strength | QQQ      |        60 |        12 |       0.0250 |           0.0340 |     -0.0090 |     0.6667 |               0.6759 |
|          50 |      0.7000 | high_strength | SOXX     |         5 |        18 |       0.0005 |           0.0051 |     -0.0045 |     0.5000 |               0.5606 |
|          50 |      0.7000 | high_strength | SOXX     |        10 |        18 |      -0.0053 |           0.0109 |     -0.0161 |     0.5556 |               0.5702 |
|          50 |      0.7000 | high_strength | SOXX     |        20 |        18 |       0.0203 |           0.0211 |     -0.0008 |     0.7222 |               0.5895 |
|          50 |      0.7000 | high_strength | SOXX     |        60 |        11 |       0.0434 |           0.0572 |     -0.0139 |     0.5455 |               0.6361 |
|          50 |      0.7500 | high_strength | SPY      |         5 |        11 |       0.0016 |           0.0022 |     -0.0006 |     0.5455 |               0.5866 |
|          50 |      0.7500 | high_strength | SPY      |        10 |        11 |       0.0009 |           0.0046 |     -0.0037 |     0.6364 |               0.6130 |
|          50 |      0.7500 | high_strength | SPY      |        20 |        11 |      -0.0005 |           0.0090 |     -0.0096 |     0.5455 |               0.6543 |
|          50 |      0.7500 | high_strength | SPY      |        60 |         8 |       0.0254 |           0.0262 |     -0.0008 |     0.7500 |               0.7177 |
|          50 |      0.7500 | high_strength | QQQ      |         5 |        11 |       0.0050 |           0.0028 |      0.0023 |     0.5455 |               0.5842 |
|          50 |      0.7500 | high_strength | QQQ      |        10 |        11 |      -0.0005 |           0.0059 |     -0.0064 |     0.4545 |               0.5946 |
|          50 |      0.7500 | high_strength | QQQ      |        20 |        11 |      -0.0032 |           0.0117 |     -0.0149 |     0.4545 |               0.6389 |
|          50 |      0.7500 | high_strength | QQQ      |        60 |         8 |       0.0351 |           0.0339 |      0.0012 |     0.7500 |               0.6753 |
|          50 |      0.7500 | high_strength | SOXX     |         5 |        10 |       0.0082 |           0.0050 |      0.0032 |     0.6000 |               0.5594 |
|          50 |      0.7500 | high_strength | SOXX     |        10 |        10 |      -0.0046 |           0.0108 |     -0.0154 |     0.4000 |               0.5713 |
|          50 |      0.7500 | high_strength | SOXX     |        20 |        10 |       0.0014 |           0.0212 |     -0.0199 |     0.5000 |               0.5922 |
|          50 |      0.7500 | high_strength | SOXX     |        60 |         7 |       0.0372 |           0.0572 |     -0.0200 |     0.4286 |               0.6365 |
|          50 |      0.8000 | high_strength | SPY      |         5 |         6 |       0.0091 |           0.0022 |      0.0070 |     0.8333 |               0.5851 |
|          50 |      0.8000 | high_strength | SPY      |        10 |         6 |       0.0038 |           0.0046 |     -0.0007 |     0.6667 |               0.6129 |
|          50 |      0.8000 | high_strength | SPY      |        20 |         6 |      -0.0182 |           0.0091 |     -0.0273 |     0.1667 |               0.6557 |
|          50 |      0.8000 | high_strength | SPY      |        60 |         5 |      -0.0005 |           0.0263 |     -0.0268 |     0.6000 |               0.7184 |
|          50 |      0.8000 | high_strength | QQQ      |         5 |         6 |       0.0113 |           0.0027 |      0.0086 |     0.6667 |               0.5835 |
|          50 |      0.8000 | high_strength | QQQ      |        10 |         6 |       0.0046 |           0.0058 |     -0.0012 |     0.3333 |               0.5946 |
|          50 |      0.8000 | high_strength | QQQ      |        20 |         6 |      -0.0350 |           0.0118 |     -0.0468 |     0.1667 |               0.6396 |
|          50 |      0.8000 | high_strength | QQQ      |        60 |         5 |      -0.0015 |           0.0340 |     -0.0356 |     0.6000 |               0.6761 |
|          50 |      0.8000 | high_strength | SOXX     |         5 |         5 |       0.0127 |           0.0050 |      0.0077 |     0.6000 |               0.5596 |
|          50 |      0.8000 | high_strength | SOXX     |        10 |         5 |       0.0263 |           0.0106 |      0.0158 |     0.6000 |               0.5698 |
|          50 |      0.8000 | high_strength | SOXX     |        20 |         5 |      -0.0256 |           0.0213 |     -0.0469 |     0.4000 |               0.5922 |
|          50 |      0.8000 | high_strength | SOXX     |        60 |         4 |       0.0157 |           0.0573 |     -0.0415 |     0.5000 |               0.6358 |

</details>

## Reproduction

- Manifest version: absolute_v1
- Manifest SHA256: `12627c60dca13230993c8d86403ade2f4b6428e777467b085f5a27fc936b54a8`
- Git commit: `85ec316`
- CLI command: `python scripts/run_breadth_absolute_verification.py`

# 景气 M7 S1 对拍汇总

原站数据截至 2026-09-02。

**结论：达标**。2021-01-01 起 23 期，原站口径 6220/6478 = 96.0%，门槛 95%。

逐期对账：全部对上；报错 0 条。

## 按因子（原站口径）

| 因子 | 计入 | 达标 | 达标率 |
|---|---|---|---|
| revenue_yoy | 1093 | 1067 | 97.6% |
| revenue_accel | 1093 | 1061 | 97.1% |
| gm_level | 1087 | 1050 | 96.6% |
| gm_yoy | 1087 | 1026 | 94.4% |
| fcf_margin_yoy | 1025 | 948 | 92.5% |
| net_margin_yoy | 1093 | 1068 | 97.7% |

我们口径（只展示）：5723/6478 = 88.3%。

## 归因

| 类别 | 原站口径 | 我们口径 |
|---|---|---|
| basis_only_day_adjust | 0 | 520 |
| basis_only_pairing | 0 | 2 |
| input_diff | 62 | 62 |
| match | 6220 | 5723 |
| ours_missing | 123 | 166 |
| quarter_sequence | 71 | 3 |
| site_missing | 80 | 80 |
| site_repaired | 2 | 2 |

## 时点与对齐

当前季与原站一致：match 1061，ours_behind 31，ours_none 1

对齐方式：asof 1061，rebuilt 30，none 2

## 剔除

全期 117 行，2021-01-01 起 50 行。

| 原站代码 | 原因 |
|---|---|
| AABA | not_in_our_data |
| AET | not_in_our_data |
| AGN | not_in_our_data |
| AI | not_in_our_data |
| BB | not_in_our_data |
| BRK.A | duplicate_share_class |
| BYND | not_in_our_data |
| CELG | not_in_our_data |
| CLOV | not_in_our_data |
| GOOGL | duplicate_share_class |
| KITE | not_in_our_data |
| LNKD | not_in_our_data |
| MARA | not_in_our_data |
| NKLA | not_in_our_data |
| PLUG | not_in_our_data |
| RGTI | not_in_our_data |
| RIOT | not_in_our_data |
| SOUN | not_in_our_data |
| SPCE | not_in_our_data |
| SPRT | not_in_our_data |
| TLRY | not_in_our_data |
| TWC | not_in_our_data |
| TWX | not_in_our_data |
| UTX | not_in_our_data |
| WISH | not_in_our_data |
| WORK | not_in_our_data |
| X | not_in_our_data |

## 2021-01-01 之前（参考，不计门槛）

24 期，原站口径 3704/6556 = 56.5%。

## 按期

| 期 | 原站行数 | 已比对 | 剔除 | 报错 | 对上 | 计入 | 达标 | 达标率 |
|---|---|---|---|---|---|---|---|---|
| 2015-03-31 | 46 | 44 | 2 | 0 | 是 | 264 | 5 | 1.9% |
| 2015-06-30 | 46 | 43 | 3 | 0 | 是 | 258 | 6 | 2.3% |
| 2015-09-30 | 46 | 44 | 2 | 0 | 是 | 264 | 0 | 0.0% |
| 2015-12-31 | 47 | 43 | 4 | 0 | 是 | 258 | 0 | 0.0% |
| 2016-03-31 | 48 | 44 | 4 | 0 | 是 | 264 | 0 | 0.0% |
| 2016-06-30 | 46 | 43 | 3 | 0 | 是 | 258 | 0 | 0.0% |
| 2016-09-30 | 47 | 45 | 2 | 0 | 是 | 269 | 7 | 2.6% |
| 2016-12-31 | 50 | 46 | 4 | 0 | 是 | 276 | 46 | 16.7% |
| 2017-03-31 | 49 | 46 | 3 | 0 | 是 | 275 | 46 | 16.7% |
| 2017-06-30 | 49 | 46 | 3 | 0 | 是 | 276 | 45 | 16.3% |
| 2017-09-30 | 48 | 44 | 4 | 0 | 是 | 262 | 51 | 19.5% |
| 2017-12-31 | 50 | 45 | 5 | 0 | 是 | 270 | 223 | 82.6% |
| 2018-03-31 | 50 | 48 | 2 | 0 | 是 | 287 | 281 | 97.9% |
| 2018-06-30 | 48 | 45 | 3 | 0 | 是 | 267 | 265 | 99.3% |
| 2018-09-30 | 49 | 47 | 2 | 0 | 是 | 279 | 269 | 96.4% |
| 2018-12-31 | 50 | 47 | 3 | 0 | 是 | 280 | 276 | 98.6% |
| 2019-03-31 | 50 | 48 | 2 | 0 | 是 | 285 | 281 | 98.6% |
| 2019-06-30 | 49 | 46 | 3 | 0 | 是 | 273 | 264 | 96.7% |
| 2019-09-30 | 50 | 48 | 2 | 0 | 是 | 286 | 281 | 98.3% |
| 2019-12-31 | 50 | 49 | 1 | 0 | 是 | 294 | 289 | 98.3% |
| 2020-03-31 | 50 | 48 | 2 | 0 | 是 | 284 | 281 | 98.9% |
| 2020-06-30 | 49 | 47 | 2 | 0 | 是 | 278 | 274 | 98.6% |
| 2020-09-30 | 50 | 48 | 2 | 0 | 是 | 287 | 273 | 95.1% |
| 2020-12-31 | 48 | 44 | 4 | 0 | 是 | 262 | 241 | 92.0% |
| 2021-03-31 | 49 | 46 | 3 | 0 | 是 | 271 | 251 | 92.6% |
| 2021-06-30 | 50 | 45 | 5 | 0 | 是 | 264 | 231 | 87.5% |
| 2021-09-30 | 50 | 48 | 2 | 0 | 是 | 286 | 248 | 86.7% |
| 2021-12-31 | 49 | 48 | 1 | 0 | 是 | 285 | 274 | 96.1% |
| 2022-03-31 | 50 | 48 | 2 | 0 | 是 | 282 | 277 | 98.2% |
| 2022-06-30 | 50 | 48 | 2 | 0 | 是 | 287 | 275 | 95.8% |
| 2022-09-30 | 50 | 48 | 2 | 0 | 是 | 288 | 281 | 97.6% |
| 2022-12-31 | 50 | 48 | 2 | 0 | 是 | 288 | 281 | 97.6% |
| 2023-03-31 | 50 | 48 | 2 | 0 | 是 | 285 | 275 | 96.5% |
| 2023-06-30 | 50 | 47 | 3 | 0 | 是 | 281 | 278 | 98.9% |
| 2023-09-30 | 50 | 48 | 2 | 0 | 是 | 285 | 277 | 97.2% |
| 2023-12-31 | 50 | 47 | 3 | 0 | 是 | 282 | 279 | 98.9% |
| 2024-03-31 | 50 | 47 | 3 | 0 | 是 | 277 | 270 | 97.5% |
| 2024-06-30 | 50 | 48 | 2 | 0 | 是 | 286 | 281 | 98.3% |
| 2024-09-30 | 50 | 48 | 2 | 0 | 是 | 287 | 281 | 97.9% |
| 2024-12-31 | 50 | 45 | 5 | 0 | 是 | 268 | 263 | 98.1% |
| 2025-03-31 | 50 | 48 | 2 | 0 | 是 | 282 | 277 | 98.2% |
| 2025-06-30 | 48 | 47 | 1 | 0 | 是 | 278 | 265 | 95.3% |
| 2025-09-30 | 49 | 47 | 2 | 0 | 是 | 277 | 265 | 95.7% |
| 2025-12-31 | 50 | 49 | 1 | 0 | 是 | 289 | 272 | 94.1% |
| 2026-03-31 | 50 | 49 | 1 | 0 | 是 | 286 | 268 | 93.7% |
| 2026-06-30 | 49 | 48 | 1 | 0 | 是 | 283 | 278 | 98.2% |
| 2026-09-02 | 49 | 48 | 1 | 0 | 是 | 281 | 273 | 97.2% |

## 不达标清单（原站口径，2021-01-01 起）

| 期 | 股票 | 因子 | 类别 | 说明 |
|---|---|---|---|---|
| 2021-03-31 | JPM | gm_level | ours_missing | gross_profit_missing |
| 2021-03-31 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2021-03-31 | MA | gm_level | input_diff | gm@2020-12-31 |
| 2021-03-31 | MA | gm_yoy | input_diff | gm@2020-12-31 |
| 2021-03-31 | PLTR | revenue_yoy | quarter_sequence | offset -4: ours 2019-06-30 site 2019-12-31 |
| 2021-03-31 | PLTR | revenue_accel | quarter_sequence | offset -4: ours 2019-06-30 site 2019-12-31 |
| 2021-03-31 | PLTR | gm_yoy | quarter_sequence | offset -4: ours 2019-06-30 site 2019-12-31 |
| 2021-03-31 | PLTR | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-06-30 site 2019-12-31 |
| 2021-03-31 | PLTR | net_margin_yoy | quarter_sequence | offset -4: ours 2019-06-30 site 2019-12-31 |
| 2021-03-31 | RBLX | revenue_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2019-12-31 |
| 2021-03-31 | RBLX | revenue_accel | quarter_sequence | offset -4: ours 2019-09-30 site 2019-12-31 |
| 2021-03-31 | RBLX | gm_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2019-12-31 |
| 2021-03-31 | RBLX | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2019-12-31 |
| 2021-03-31 | RBLX | net_margin_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2019-12-31 |
| 2021-03-31 | SNOW | revenue_yoy | quarter_sequence | offset -4: ours 2019-10-31 site 2020-01-31 |
| 2021-03-31 | SNOW | revenue_accel | quarter_sequence | offset -4: ours 2019-10-31 site 2020-01-31 |
| 2021-03-31 | SNOW | gm_yoy | quarter_sequence | offset -4: ours 2019-10-31 site 2020-01-31 |
| 2021-03-31 | SNOW | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-10-31 site 2020-01-31 |
| 2021-03-31 | SNOW | net_margin_yoy | quarter_sequence | offset -4: ours 2019-10-31 site 2020-01-31 |
| 2021-03-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-06-30 | ABNB | revenue_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | ABNB | revenue_accel | ours_missing | prior_yoy_missing |
| 2021-06-30 | ABNB | gm_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | ABNB | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-06-30 | ABNB | net_margin_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | BAC | gm_level | ours_missing | gross_profit_missing |
| 2021-06-30 | BAC | gm_yoy | ours_missing | gross_profit_missing |
| 2021-06-30 | C | gm_level | ours_missing | gross_profit_missing |
| 2021-06-30 | C | gm_yoy | ours_missing | gross_profit_missing |
| 2021-06-30 | COIN | revenue_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | COIN | revenue_accel | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | COIN | gm_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | COIN | net_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | JPM | gm_level | ours_missing | gross_profit_missing |
| 2021-06-30 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2021-06-30 | MA | gm_level | input_diff | gm@2021-03-31 |
| 2021-06-30 | MA | gm_yoy | input_diff | gm@2021-03-31 |
| 2021-06-30 | PLTR | revenue_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | PLTR | revenue_accel | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | PLTR | gm_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | PLTR | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | PLTR | net_margin_yoy | quarter_sequence | offset -4: ours 2019-09-30 site 2020-03-31 |
| 2021-06-30 | RBLX | revenue_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | RBLX | revenue_accel | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | RBLX | gm_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | RBLX | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | RBLX | net_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-03-31 |
| 2021-06-30 | SNOW | revenue_yoy | quarter_sequence | offset -4: ours 2020-01-31 site 2020-04-30 |
| 2021-06-30 | SNOW | revenue_accel | quarter_sequence | offset -4: ours 2020-01-31 site 2020-04-30 |
| 2021-06-30 | SNOW | fcf_margin_yoy | quarter_sequence | offset -4: ours 2020-01-31 site 2020-04-30 |
| 2021-06-30 | SNOW | net_margin_yoy | quarter_sequence | offset -4: ours 2020-01-31 site 2020-04-30 |
| 2021-06-30 | XYZ/SQ | fcf_margin_yoy | input_diff | fcfm@2021-03-31 |
| 2021-06-30 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-09-30 | ABNB | revenue_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | ABNB | revenue_accel | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | ABNB | gm_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | ABNB | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | ABNB | net_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | AFRM | revenue_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | AFRM | revenue_accel | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | AFRM | gm_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | AFRM | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | AFRM | net_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | BAC | gm_level | ours_missing | gross_profit_missing |
| 2021-09-30 | BAC | gm_yoy | ours_missing | gross_profit_missing |
| 2021-09-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-09-30 | JPM | gm_level | ours_missing | gross_profit_missing |
| 2021-09-30 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2021-09-30 | MA | gm_level | input_diff | gm@2021-06-30 |
| 2021-09-30 | MA | gm_yoy | input_diff | gm@2021-06-30 |
| 2021-09-30 | MXIM | revenue_yoy | ours_missing | quarter_not_found |
| 2021-09-30 | MXIM | revenue_accel | ours_missing | quarter_not_found |
| 2021-09-30 | MXIM | gm_level | ours_missing | quarter_not_found |
| 2021-09-30 | MXIM | gm_yoy | ours_missing | quarter_not_found |
| 2021-09-30 | MXIM | fcf_margin_yoy | ours_missing | quarter_not_found |
| 2021-09-30 | MXIM | net_margin_yoy | ours_missing | quarter_not_found |
| 2021-09-30 | PLTR | revenue_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | PLTR | revenue_accel | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | PLTR | gm_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | PLTR | fcf_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | PLTR | net_margin_yoy | quarter_sequence | offset -4: ours 2019-12-31 site 2020-06-30 |
| 2021-09-30 | SNOW | revenue_accel | quarter_sequence | offset -5: ours 2020-01-31 site 2020-04-30 |
| 2021-09-30 | XYZ/SQ | fcf_margin_yoy | input_diff | fcfm@2021-06-30 |
| 2021-09-30 | UPST | revenue_yoy | ours_missing | no_yoy_base |
| 2021-09-30 | UPST | revenue_accel | ours_missing | no_yoy_base |
| 2021-09-30 | UPST | gm_yoy | ours_missing | no_yoy_base |
| 2021-09-30 | UPST | fcf_margin_yoy | ours_missing | no_yoy_base |
| 2021-09-30 | UPST | net_margin_yoy | ours_missing | no_yoy_base |
| 2021-09-30 | WFC | gm_level | ours_missing | gross_profit_missing |
| 2021-09-30 | WFC | gm_yoy | ours_missing | gross_profit_missing |
| 2021-09-30 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-12-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-12-31 | JPM | gm_level | ours_missing | gross_profit_missing |
| 2021-12-31 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2021-12-31 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2021-12-31 | LCID | revenue_accel | ours_missing | prior_yoy_missing |
| 2021-12-31 | MA | gm_level | input_diff | gm@2021-09-30 |
| 2021-12-31 | MA | gm_yoy | input_diff | gm@2021-09-30 |
| 2021-12-31 | XYZ/SQ | fcf_margin_yoy | input_diff | fcfm@2021-09-30 |
| 2021-12-31 | WFC | gm_level | ours_missing | gross_profit_missing |
| 2021-12-31 | WFC | gm_yoy | ours_missing | gross_profit_missing |
| 2021-12-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-03-31 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2022-03-31 | MA | gm_level | input_diff | gm@2021-12-31 |
| 2022-03-31 | MA | gm_yoy | input_diff | gm@2021-12-31 |
| 2022-03-31 | UPST | revenue_accel | ours_missing | prior_yoy_missing |
| 2022-03-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-06-30 | BAC | gm_yoy | ours_missing | gross_profit_missing |
| 2022-06-30 | C | gm_yoy | ours_missing | gross_profit_missing |
| 2022-06-30 | COP | revenue_yoy | input_diff | rev@2022-03-31 |
| 2022-06-30 | COP | revenue_accel | input_diff | rev@2022-03-31 |
| 2022-06-30 | COP | gm_level | input_diff | gm@2022-03-31 |
| 2022-06-30 | COP | gm_yoy | input_diff | gm@2022-03-31 |
| 2022-06-30 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2022-06-30 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-06-30 | MA | gm_level | input_diff | gm@2022-03-31 |
| 2022-06-30 | MA | gm_yoy | input_diff | gm@2022-03-31 |
| 2022-06-30 | XYZ/SQ | fcf_margin_yoy | input_diff | fcfm@2021-03-31 |
| 2022-06-30 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-09-30 | BAC | gm_yoy | ours_missing | gross_profit_missing |
| 2022-09-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-09-30 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2022-09-30 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-09-30 | MA | gm_level | input_diff | gm@2022-06-30 |
| 2022-09-30 | MA | gm_yoy | input_diff | gm@2022-06-30 |
| 2022-09-30 | XYZ/SQ | fcf_margin_yoy | input_diff | fcfm@2021-06-30 |
| 2022-12-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-12-31 | JPM | gm_yoy | ours_missing | gross_profit_missing |
| 2022-12-31 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2022-12-31 | MA | gm_level | input_diff | gm@2022-09-30 |
| 2022-12-31 | MA | gm_yoy | input_diff | gm@2022-09-30 |
| 2022-12-31 | WFC | gm_yoy | ours_missing | gross_profit_missing |
| 2022-12-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-03-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-03-31 | FRC | revenue_yoy | ours_missing | quarter_not_found |
| 2023-03-31 | FRC | revenue_accel | ours_missing | quarter_not_found |
| 2023-03-31 | FRC | gm_level | ours_missing | quarter_not_found |
| 2023-03-31 | FRC | gm_yoy | ours_missing | quarter_not_found |
| 2023-03-31 | FRC | fcf_margin_yoy | ours_missing | quarter_not_found |
| 2023-03-31 | FRC | net_margin_yoy | ours_missing | quarter_not_found |
| 2023-03-31 | MA | gm_level | input_diff | gm@2022-12-31 |
| 2023-03-31 | MA | gm_yoy | input_diff | gm@2022-12-31 |
| 2023-03-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-06-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-06-30 | MA | gm_level | input_diff | gm@2023-03-31 |
| 2023-06-30 | MA | gm_yoy | input_diff | gm@2023-03-31 |
| 2023-09-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-09-30 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-09-30 | KVUE | revenue_yoy | quarter_sequence | offset -4: ours 2022-03-31 site 2022-06-30 |
| 2023-09-30 | KVUE | revenue_accel | quarter_sequence | offset -4: ours 2022-03-31 site 2022-06-30 |
| 2023-09-30 | KVUE | gm_yoy | quarter_sequence | offset -4: ours 2022-03-31 site 2022-06-30 |
| 2023-09-30 | KVUE | fcf_margin_yoy | quarter_sequence | offset -4: ours 2022-03-31 site 2022-06-30 |
| 2023-09-30 | KVUE | net_margin_yoy | quarter_sequence | offset -4: ours 2022-03-31 site 2022-06-30 |
| 2023-09-30 | MA | gm_level | input_diff | gm@2023-06-30 |
| 2023-12-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-12-31 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2023-12-31 | MA | gm_level | input_diff | gm@2023-09-30 |
| 2024-03-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-03-31 | RDDT | revenue_yoy | quarter_sequence | offset -4: ours 2022-09-30 site 2022-12-31 |
| 2024-03-31 | RDDT | revenue_accel | quarter_sequence | offset -1: ours 2023-06-30 site 2023-09-30 |
| 2024-03-31 | RDDT | gm_yoy | quarter_sequence | offset -4: ours 2022-09-30 site 2022-12-31 |
| 2024-03-31 | RDDT | fcf_margin_yoy | quarter_sequence | offset -4: ours 2022-09-30 site 2022-12-31 |
| 2024-03-31 | RDDT | net_margin_yoy | quarter_sequence | offset -4: ours 2022-09-30 site 2022-12-31 |
| 2024-03-31 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-06-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-06-30 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-06-30 | MA | gm_level | input_diff | gm@2024-03-31 |
| 2024-06-30 | MA | gm_yoy | input_diff | gm@2024-03-31 |
| 2024-06-30 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-09-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-09-30 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-09-30 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-09-30 | MA | gm_level | input_diff | gm@2024-06-30 |
| 2024-09-30 | MA | gm_yoy | input_diff | gm@2024-06-30 |
| 2024-09-30 | WFC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-12-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-12-31 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-12-31 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2024-12-31 | MA | gm_level | input_diff | gm@2024-09-30 |
| 2024-12-31 | MA | gm_yoy | input_diff | gm@2024-09-30 |
| 2025-03-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-03-31 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-03-31 | GEV | revenue_accel | quarter_sequence | offset -5: ours 2023-06-30 site 2023-09-30 |
| 2025-03-31 | MA | gm_level | input_diff | gm@2024-12-31 |
| 2025-03-31 | MA | gm_yoy | input_diff | gm@2024-12-31 |
| 2025-06-30 | APP | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-06-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-06-30 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-06-30 | CRWV | revenue_yoy | ours_missing | no_yoy_base |
| 2025-06-30 | CRWV | revenue_accel | ours_missing | no_yoy_base |
| 2025-06-30 | CRWV | gm_yoy | ours_missing | no_yoy_base |
| 2025-06-30 | CRWV | net_margin_yoy | ours_missing | no_yoy_base |
| 2025-06-30 | MA | gm_level | input_diff | gm@2025-03-31 |
| 2025-06-30 | MA | gm_yoy | input_diff | gm@2025-03-31 |
| 2025-06-30 | QBTS | revenue_yoy | ours_missing | no_yoy_base |
| 2025-06-30 | QBTS | revenue_accel | ours_missing | no_yoy_base |
| 2025-06-30 | QBTS | gm_yoy | ours_missing | no_yoy_base |
| 2025-06-30 | QBTS | net_margin_yoy | ours_missing | no_yoy_base |
| 2025-09-30 | APP | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-09-30 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-09-30 | BMNR | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-09-30 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-09-30 | CRWV | revenue_yoy | ours_missing | no_yoy_base |
| 2025-09-30 | CRWV | revenue_accel | ours_missing | no_yoy_base |
| 2025-09-30 | CRWV | gm_yoy | ours_missing | no_yoy_base |
| 2025-09-30 | CRWV | net_margin_yoy | ours_missing | no_yoy_base |
| 2025-09-30 | HOOD | gm_level | input_diff | gm@2025-06-30 |
| 2025-09-30 | HOOD | gm_yoy | input_diff | gm@2025-06-30 |
| 2025-09-30 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-09-30 | MA | gm_level | input_diff | gm@2025-06-30 |
| 2025-12-31 | APP | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-12-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-12-31 | COIN | gm_level | input_diff | gm@2025-09-30 |
| 2025-12-31 | COIN | gm_yoy | input_diff | gm@2025-09-30 |
| 2025-12-31 | COIN | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-12-31 | CRWV | revenue_yoy | ours_missing | no_yoy_base |
| 2025-12-31 | CRWV | revenue_accel | ours_missing | no_yoy_base |
| 2025-12-31 | CRWV | gm_yoy | ours_missing | no_yoy_base |
| 2025-12-31 | CRWV | fcf_margin_yoy | ours_missing | no_yoy_base |
| 2025-12-31 | CRWV | net_margin_yoy | ours_missing | no_yoy_base |
| 2025-12-31 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2025-12-31 | MA | gm_level | input_diff | gm@2025-09-30 |
| 2025-12-31 | MA | gm_yoy | input_diff | gm@2025-09-30 |
| 2025-12-31 | SNDK | revenue_yoy | quarter_sequence | offset -4: ours 2024-03-31 site 2024-09-30 |
| 2025-12-31 | SNDK | revenue_accel | quarter_sequence | offset -4: ours 2024-03-31 site 2024-09-30 |
| 2025-12-31 | SNDK | gm_yoy | quarter_sequence | offset -4: ours 2024-03-31 site 2024-09-30 |
| 2025-12-31 | SNDK | net_margin_yoy | quarter_sequence | offset -4: ours 2024-03-31 site 2024-09-30 |
| 2026-03-31 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2026-03-31 | COIN | revenue_yoy | input_diff | rev@2025-12-31 |
| 2026-03-31 | COIN | revenue_accel | input_diff | rev@2025-12-31 |
| 2026-03-31 | COIN | gm_level | input_diff | gm@2025-12-31 |
| 2026-03-31 | COIN | gm_yoy | input_diff | gm@2025-12-31 |
| 2026-03-31 | COIN | net_margin_yoy | input_diff | nim@2025-12-31 |
| 2026-03-31 | CRCL | revenue_yoy | ours_missing | no_yoy_base |
| 2026-03-31 | CRCL | revenue_accel | ours_missing | no_yoy_base |
| 2026-03-31 | CRCL | gm_yoy | ours_missing | no_yoy_base |
| 2026-03-31 | CRCL | fcf_margin_yoy | ours_missing | no_yoy_base |
| 2026-03-31 | CRCL | net_margin_yoy | ours_missing | no_yoy_base |
| 2026-03-31 | CRWV | revenue_yoy | quarter_sequence | offset -4: ours 2024-09-30 site 2024-12-31 |
| 2026-03-31 | CRWV | revenue_accel | ours_missing | prior_yoy_missing |
| 2026-03-31 | CRWV | net_margin_yoy | quarter_sequence | offset -4: ours 2024-09-30 site 2024-12-31 |
| 2026-03-31 | LLY | gm_level | input_diff | gm@2025-12-31 |
| 2026-03-31 | LLY | gm_yoy | input_diff | gm@2025-12-31 |
| 2026-03-31 | SNDK | revenue_accel | quarter_sequence | offset -5: ours 2024-03-31 site 2024-09-30 |
| 2026-03-31 | VRT | fcf_margin_yoy | input_diff | fcfm@2025-12-31 |
| 2026-06-30 | APP | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2026-06-30 | CRWV | revenue_accel | quarter_sequence | offset -5: ours 2024-09-30 site 2024-12-31 |
| 2026-06-30 | HOOD | fcf_margin_yoy | input_diff | fcfm@2026-03-31 |
| 2026-06-30 | NBIS | gm_level | site_repaired | gm@2026-03-31 |
| 2026-06-30 | NBIS | gm_yoy | site_repaired | gm@2026-03-31 |
| 2026-09-02 | APP | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2026-09-02 | BAC | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |
| 2026-09-02 | HOOD | revenue_yoy | input_diff | rev@2026-06-30 |
| 2026-09-02 | HOOD | revenue_accel | input_diff | rev@2026-06-30 |
| 2026-09-02 | HOOD | gm_level | input_diff | gm@2026-06-30 |
| 2026-09-02 | HOOD | gm_yoy | input_diff | gm@2026-06-30 |
| 2026-09-02 | HOOD | net_margin_yoy | input_diff | nim@2026-06-30 |
| 2026-09-02 | JPM | fcf_margin_yoy | ours_missing | fcf_zero_placeholder |

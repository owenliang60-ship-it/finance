"""Valuation scenario constants; each cites where the value comes from (plan 2026-11-22)."""

G1_TOL = 0.01                 # Boss 2026-11-22 锁定
FX_XVENDOR_TOL = 0.03         # TSM 2026-11-21 三个隐含汇率离散 1.6%
FX_DRIFT_TOL = 0.10           # TSM 近 5 季已公告隐含汇率对 fx0 最大偏离 6.0%
SHARE_JUMP_TOL = 0.10         # MU 2016-18 稀释最大 6.3% 不触发
MILD_ZONE_SIGMAS = 2.0        # 计划「温和区」：营收意外 2σ
SURPRISE_QUARTERS, SURPRISE_MIN_QUARTERS = 20, 8   # 计划「温和区」：最近 20 季，少于 8 季桥禁用（B2）
PE_MIN_DAYS = 250             # 计划门控 P3：约一年交易日
PE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)         # 计划「矩阵」五个分位
MIN_ABS_EPS_FOR_FX = 0.05     # 计划 C2：亏损季守卫
REV_DELTA_MIN = 0.01          # 计划「增量利润率」：ΔRev < 1% 视为不可得
QUOTE_MIN_CHARS = 20          # 计划 G2：引文规范化后至少 20 字符
YF_MAX_AGE_DAYS = 14          # 同 prosperity FORWARD_STALE_DAYS
T1_GLOBAL_DOMAINS = ("sec.gov",)                   # 计划 G2：全局一手披露域名
G3_LABELS = ("eps_split_rescaled", "eps_split_unconfirmed", "eps_split_retrospective",
             "gaap_split_basis_break", "eps_conflicting_quarter")   # terminal/prosperity/street_eps.py 标签
EXIT_OK, EXIT_DEGRADED, EXIT_BLOCKED = 0, 10, 20   # 计划「三档故障模型」

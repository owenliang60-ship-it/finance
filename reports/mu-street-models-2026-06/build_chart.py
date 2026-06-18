"""MU 基本面变化图 — 过去8季实绩(GAAP) + 未来8季券商预测(non-GAAP).

数据来源:
- 实绩: data/market.db income_quarterly (FMP, GAAP)
- UBS 2026-05-26 Figure 4 Summary model (季度全要素, non-GAAP)
- Citi 2026-05-18 EPS 季度表 (core EPS)
- GS 2026-06-08 Exhibit 1 + GS Forecast box (FQ3/FQ4'26 全要素, FQ1'27 EPS)
净利润(预测)由 EPS x 1.15B 摊薄股数折算, 标注为 implied。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

Q = ["FQ3'24", "FQ4'24", "FQ1'25", "FQ2'25", "FQ3'25", "FQ4'25", "FQ1'26", "FQ2'26",
     "FQ3'26", "FQ4'26", "FQ1'27", "FQ2'27", "FQ3'27", "FQ4'27", "FQ1'28", "FQ2'28"]
N = len(Q)
x = np.arange(N)
SPLIT = 7.5  # actual / estimate boundary (after FQ2'26)
SH = 1.15   # diluted shares (B), Citi model 1,149.6M

nan = float("nan")

# ---- actuals (GAAP, $B / % / $) : FQ3'24..FQ2'26 ----
act_rev = [6.811, 7.750, 8.709, 8.053, 9.301, 11.315, 13.643, 23.860] + [nan]*8
act_gm  = [26.9, 35.3, 38.4, 36.8, 37.7, 44.7, 56.1, 74.4] + [nan]*8
act_ni  = [0.332, 0.887, 1.870, 1.583, 1.885, 3.201, 5.240, 13.789] + [nan]*8
act_eps = [0.30, 0.79, 1.67, 1.41, 1.68, 2.83, 4.60, 12.08] + [nan]*8

# ---- UBS (non-GAAP), anchor at FQ2'26 own estimate for QoQ continuity ----
ubs_rev = [nan]*7 + [23.860, 36.026, 43.067, 49.971, 55.552, 60.264, 64.919, 68.792, 68.915]
ubs_gm  = [nan]*7 + [74.9, 83.1, 85.4, 86.9, 87.8, 88.4, 88.8, 89.0, 88.5]
ubs_eps = [nan]*7 + [12.20, 20.96, 25.64, 30.19, 34.33, 37.27, 40.40, 42.91, 42.72]
ubs_ni  = [nan]*7 + [e*SH if e == e else nan for e in ubs_eps[7:]]

# ---- Citi (core EPS only, quarterly) ----
citi_eps = [nan]*7 + [11.94, 18.86, 23.12, 24.06, 26.11, 27.26, 27.13, 23.45, 20.58]
citi_ni  = [nan]*7 + [e*SH if e == e else nan for e in citi_eps[7:]]

# ---- GS (FQ3/FQ4'26 full, FQ1'27 EPS only) ----
gs_rev = [nan]*7 + [23.860, 37.579, 48.767] + [nan]*6
gs_gm  = [nan]*7 + [74.4, 83.4, 86.1] + [nan]*6
gs_eps = [nan]*7 + [11.94, 22.07, 29.95, 33.63] + [nan]*5
gs_ni  = [nan]*7 + [e*SH if e == e else nan for e in gs_eps[7:]]


def qoq_pct(series):
    out = [nan]*N
    for i in range(1, N):
        a, b = series[i-1], series[i]
        if a == a and b == b and a != 0:
            out[i] = (b/a - 1) * 100
    return out


def qoq_pp(series):
    out = [nan]*N
    for i in range(1, N):
        a, b = series[i-1], series[i]
        if a == a and b == b:
            out[i] = b - a
    return out


C = dict(act="#1a1a1a", ubs="#d62728", citi="#1f77b4", gs="#b8860b")

fig, axes = plt.subplots(4, 2, figsize=(16, 18))
fig.suptitle("美光 (MU) 基本面 16 季全景：FQ3'24–FQ2'26 实绩 (GAAP) + FQ3'26–FQ2'28 券商预测 (non-GAAP)\n"
             "来源: market.db / UBS 5-26 / Citi 5-18 / GS 6-8 · 预测净利润 = EPS × 1.15B 摊薄股数折算",
             fontsize=14, y=0.995)

panels = [
    ("收入 ($B)", act_rev, ubs_rev, None, gs_rev, "level"),
    ("净利润 ($B, 预测为折算值)", act_ni, ubs_ni, citi_ni, gs_ni, "level"),
    ("EPS ($, 摊薄)", act_eps, ubs_eps, citi_eps, gs_eps, "level"),
    ("毛利率 (%)", act_gm, ubs_gm, None, gs_gm, "margin"),
]

for r, (title, a, u, c, g, kind) in enumerate(panels):
    axL, axR = axes[r]
    # left: level
    axL.plot(x, a, "-o", color=C["act"], lw=2, ms=5, label="实绩 (GAAP)")
    axL.plot(x, u, "--s", color=C["ubs"], lw=1.6, ms=4, label="UBS")
    if c is not None:
        axL.plot(x, c, "--^", color=C["citi"], lw=1.6, ms=4, label="Citi")
    axL.plot(x, g, "--D", color=C["gs"], lw=1.6, ms=4, label="GS")
    axL.set_title(title, fontsize=12, loc="left")
    # right: QoQ
    if kind == "margin":
        qa, qu = qoq_pp(a), qoq_pp(u)
        qc = qoq_pp(c) if c is not None else None
        qg = qoq_pp(g)
        axR.set_title(title.split(" ")[0] + " QoQ 变化 (pp)", fontsize=12, loc="left")
    else:
        qa, qu = qoq_pct(a), qoq_pct(u)
        qc = qoq_pct(c) if c is not None else None
        qg = qoq_pct(g)
        axR.set_title(title.split(" ")[0] + " QoQ 增速 (%)", fontsize=12, loc="left")
    axR.plot(x, qa, "-o", color=C["act"], lw=2, ms=5)
    axR.plot(x, qu, "--s", color=C["ubs"], lw=1.6, ms=4)
    if qc is not None:
        axR.plot(x, qc, "--^", color=C["citi"], lw=1.6, ms=4)
    axR.plot(x, qg, "--D", color=C["gs"], lw=1.6, ms=4)
    axR.axhline(0, color="gray", lw=0.8)
    for ax in (axL, axR):
        ax.axvspan(SPLIT, N-0.5, color="#f0f0f0", zorder=0)
        ax.axvline(SPLIT, color="gray", ls=":", lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels(Q, rotation=55, fontsize=8)
        ax.grid(alpha=0.25)
    if r == 0:
        axL.legend(fontsize=10, loc="upper left")
        axL.text(SPLIT+0.2, axL.get_ylim()[1]*0.93, "← 实绩 | 预测 →", fontsize=9, color="gray")

fig.tight_layout(rect=[0, 0, 1, 0.97])
out = "/Users/owen/CC workspace/Finance/reports/mu-street-models-2026-06/mu_fundamentals_16q.png"
fig.savefig(out, dpi=150)
print("saved", out)

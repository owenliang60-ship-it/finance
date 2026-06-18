"""MU vs SK Hynix 基本面对比图 — 绝对值(USD) + QoQ + 营业利润率.

口径与对齐:
- 日历季度对齐: MU 财季→日历季 (FQ3'25→CQ2'25 ... FQ3'26→CQ2'26 ... FQ1'27→CQ4'26)。
  两家"下一份财报"都落在 CQ2'26 (MU FQ3'26 报 6/24; Hynix 2Q26 报 7/29)。
- 货币: Hynix KRW→USD @ 1,420 (HSBC 2026-05-12 报告所用汇率, line 675)。
- 实绩: MU = memo §1 (non-GAAP, market.db 交叉验证); Hynix = Citi 5/11 Fig.2 (公司公告).
- 前瞻虚线 = 各自单一券商模型(内部自洽): MU=MS 模型(memo §1, 锚定指引); Hynix=Citi 5/11 Fig.2。
- CQ2'26 额外散点 = 最新一致预期(6/10 FnGuide 15家 / MU street 6/15) + MU 公司指引。
  显示"共识在过去一个月上修到模型之上"——尤其 Hynix。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

FX = 1420.0  # KRW per USD (HSBC 5/12)
nan = float("nan")
Q = ["CQ2'25", "CQ3'25", "CQ4'25", "CQ1'26", "CQ2'26E", "CQ3'26E", "CQ4'26E"]
N = len(Q); x = np.arange(N)
NEXT = 4  # index of CQ2'26 (next earnings for both)

# ---------------- MU (USD $B / %), memo §1 MS-model self-consistent ----------------
# actuals: CQ2'25=FQ3-25 ... CQ1'26=FQ2-26
mu_rev_act = [9.301, 11.315, 13.643, 23.860, nan, nan, nan]
mu_opm_act = [26.8, 35.0, 47.0, 69.0, nan, nan, nan]
# forward (MS model, anchored at CQ1'26 actual for QoQ continuity)
mu_rev_fwd = [nan, nan, nan, 23.860, 33.523, 39.463, 43.805]
mu_opm_fwd = [nan, nan, nan, 69.0, 77.3, 79.6, 78.0]   # FQ1'27 opm ~78 (memo net 62.4%→op est)
# consensus / guidance dots at CQ2'26
mu_rev_cons = 34.8     # street 6/15
mu_rev_guid = 33.5     # company guidance midpoint
mu_opm_cons = 77.0

# ---------------- Hynix (KRW tr → USD $B), Citi 5/11 Fig.2 self-consistent ----------
hx_rev_act_krw = [22.232, 24.449, 32.827, 52.576, nan, nan, nan]
hx_op_act_krw  = [9.213, 11.383, 19.170, 37.610, nan, nan, nan]
hx_opm_act     = [41.0, 47.0, 58.0, 72.0, nan, nan, nan]
hx_rev_fwd_krw = [nan, nan, nan, 52.576, 76.356, 90.748, 101.494]
hx_op_fwd_krw  = [nan, nan, nan, 37.610, 58.861, 72.308, 82.367]
hx_opm_fwd     = [nan, nan, nan, 72.0, 77.0, 80.0, 81.0]
# consensus dots at CQ2'26 (6/10 FnGuide 15家)
hx_rev_cons_krw = 83.4
hx_op_cons_krw  = 64.3
hx_opm_cons     = 77.3

def to_usd(series):
    # KRW 万亿(tr) → USD 十亿($B):  tr × 1e12 / FX / 1e9 = tr × 1000 / FX
    return [v * 1000.0 / FX if v == v else nan for v in series]

def op_from_margin(rev, opm):
    return [(r * m / 100.0) if (r == r and m == m) else nan for r, m in zip(rev, opm)]

# MU operating profit = rev × opm
mu_op_act = op_from_margin(mu_rev_act, mu_opm_act)
mu_op_fwd = op_from_margin(mu_rev_fwd, mu_opm_fwd)
mu_op_cons = mu_rev_cons * mu_opm_cons / 100.0

# Hynix → USD
hx_rev_act = to_usd(hx_rev_act_krw); hx_rev_fwd = to_usd(hx_rev_fwd_krw)
hx_op_act = to_usd(hx_op_act_krw);   hx_op_fwd = to_usd(hx_op_fwd_krw)
hx_rev_cons = hx_rev_cons_krw * 1000.0 / FX;  hx_op_cons = hx_op_cons_krw * 1000.0 / FX

def merge(act, fwd):
    """实绩段 + 前瞻段拼成一条连续序列(用于 QoQ 计算)."""
    return [fwd[i] if (fwd[i] == fwd[i]) else act[i] for i in range(N)]

def qoq(series):
    out = [nan]*N
    for i in range(1, N):
        a, b = series[i-1], series[i]
        if a == a and b == b and a != 0:
            out[i] = (b/a - 1)*100
    return out

def qoq_pp(series):
    out = [nan]*N
    for i in range(1, N):
        a, b = series[i-1], series[i]
        if a == a and b == b:
            out[i] = b - a
    return out

MU_C = "#d62728"; HX_C = "#1f77b4"

fig, axes = plt.subplots(3, 2, figsize=(16, 15))
fig.suptitle(
    "美光 (MU) vs SK海力士 (000660.KS) — 基本面对比：绝对值(USD) + QoQ 增速 + 营业利润率\n"
    "日历季对齐 · 两家下一份财报均在 CQ2'26 (MU FQ3'26 报 6/24; Hynix 2Q26 报 7/29) · Hynix KRW→USD @1,420\n"
    "实线=实绩 · 粗虚线=券商模型(MU:MS / Hynix:Citi 5-11) · 星=最新共识(6/10~6/15) · 菱=MU公司指引",
    fontsize=12.5, y=0.997)

def style(ax):
    ax.axvspan(NEXT-0.5, N-0.5, color="#f4f4f4", zorder=0)
    ax.axvline(NEXT, color="#888", ls=":", lw=1.4)
    ax.set_xticks(x); ax.set_xticklabels(Q, fontsize=9)
    ax.grid(alpha=0.25)

# ---- Row 1: Revenue ----
axL, axR = axes[0]
axL.plot(x, mu_rev_act, "-o", color=MU_C, lw=2.2, ms=6, label="MU 实绩")
axL.plot(x, mu_rev_fwd, "--", color=MU_C, lw=1.8)
axL.plot(x, hx_rev_act, "-o", color=HX_C, lw=2.2, ms=6, label="Hynix 实绩")
axL.plot(x, hx_rev_fwd, "--", color=HX_C, lw=1.8)
axL.scatter([NEXT],[mu_rev_cons], marker="*", s=240, color=MU_C, zorder=5, edgecolor="k", lw=0.5)
axL.scatter([NEXT],[mu_rev_guid], marker="D", s=70, color="white", zorder=5, edgecolor=MU_C, lw=1.6)
axL.scatter([NEXT],[hx_rev_cons], marker="*", s=240, color=HX_C, zorder=5, edgecolor="k", lw=0.5)
axL.set_title("收入 (USD $B)", fontsize=12, loc="left")
axL.legend(fontsize=10, loc="upper left")
axL.annotate(f"共识 ${hx_rev_cons:.0f}B", (NEXT, hx_rev_cons), textcoords="offset points",
             xytext=(6,4), fontsize=8.5, color=HX_C)
axL.annotate(f"共识 ${mu_rev_cons:.0f}B\n指引 ${mu_rev_guid:.0f}B", (NEXT, mu_rev_guid),
             textcoords="offset points", xytext=(6,-26), fontsize=8.5, color=MU_C)
style(axL)

mu_rev_q = qoq(merge(mu_rev_act, mu_rev_fwd)); hx_rev_q = qoq(merge(hx_rev_act, hx_rev_fwd))
axR.plot(x, mu_rev_q, "-o", color=MU_C, lw=2.2, ms=6, label="MU (MS模型)")
axR.plot(x, hx_rev_q, "-o", color=HX_C, lw=2.2, ms=6, label="Hynix (Citi模型)")
# consensus QoQ at next
axR.scatter([NEXT],[(mu_rev_cons/mu_rev_act[3]-1)*100], marker="*", s=240, color=MU_C, zorder=5, edgecolor="k", lw=0.5)
axR.scatter([NEXT],[(hx_rev_cons_krw/hx_rev_act_krw[3]-1)*100], marker="*", s=240, color=HX_C, zorder=5, edgecolor="k", lw=0.5)
axR.set_title("收入 QoQ 增速 (%) — ●模型 / ★共识", fontsize=12, loc="left")
axR.axhline(0, color="gray", lw=0.8); axR.legend(fontsize=9, loc="upper right")
style(axR)

# ---- Row 2: Operating Profit ----
axL, axR = axes[1]
axL.plot(x, mu_op_act, "-o", color=MU_C, lw=2.2, ms=6)
axL.plot(x, mu_op_fwd, "--", color=MU_C, lw=1.8)
axL.plot(x, hx_op_act, "-o", color=HX_C, lw=2.2, ms=6)
axL.plot(x, hx_op_fwd, "--", color=HX_C, lw=1.8)
axL.scatter([NEXT],[mu_op_cons], marker="*", s=240, color=MU_C, zorder=5, edgecolor="k", lw=0.5)
axL.scatter([NEXT],[hx_op_cons], marker="*", s=240, color=HX_C, zorder=5, edgecolor="k", lw=0.5)
axL.set_title("营业利润 (USD $B)  ·  MU=non-GAAP 经营利润 / Hynix=营业利润(OP)", fontsize=11.5, loc="left")
axL.annotate(f"共识 ${hx_op_cons:.0f}B", (NEXT, hx_op_cons), textcoords="offset points",
             xytext=(6,4), fontsize=8.5, color=HX_C)
axL.annotate(f"共识 ${mu_op_cons:.0f}B", (NEXT, mu_op_cons), textcoords="offset points",
             xytext=(6,-14), fontsize=8.5, color=MU_C)
style(axL)

mu_op_q = qoq(merge(mu_op_act, mu_op_fwd)); hx_op_q = qoq(merge(hx_op_act, hx_op_fwd))
axR.plot(x, mu_op_q, "-o", color=MU_C, lw=2.2, ms=6, label="MU (MS模型)")
axR.plot(x, hx_op_q, "-o", color=HX_C, lw=2.2, ms=6, label="Hynix (Citi模型)")
axR.scatter([NEXT],[(mu_op_cons/mu_op_act[3]-1)*100], marker="*", s=240, color=MU_C, zorder=5, edgecolor="k", lw=0.5)
axR.scatter([NEXT],[(hx_op_cons_krw/hx_op_act_krw[3]-1)*100], marker="*", s=240, color=HX_C, zorder=5, edgecolor="k", lw=0.5)
axR.set_title("营业利润 QoQ 增速 (%) — ●模型 / ★共识", fontsize=12, loc="left")
axR.axhline(0, color="gray", lw=0.8); axR.legend(fontsize=9, loc="upper right")
style(axR)

# ---- Row 3: Operating Margin ----
axL, axR = axes[2]
axL.plot(x, mu_opm_act, "-o", color=MU_C, lw=2.2, ms=6)
axL.plot(x, mu_opm_fwd, "--", color=MU_C, lw=1.8)
axL.plot(x, hx_opm_act, "-o", color=HX_C, lw=2.2, ms=6)
axL.plot(x, hx_opm_fwd, "--", color=HX_C, lw=1.8)
axL.scatter([NEXT],[mu_opm_cons], marker="*", s=240, color=MU_C, zorder=5, edgecolor="k", lw=0.5)
axL.scatter([NEXT],[hx_opm_cons], marker="*", s=240, color=HX_C, zorder=5, edgecolor="k", lw=0.5)
axL.set_title("营业利润率 (%)  ·  CQ2'26 两家收敛至 ~77%，此后 Hynix 重新领先", fontsize=11.5, loc="left")
style(axL)

mu_opm_q = qoq_pp(merge(mu_opm_act, mu_opm_fwd)); hx_opm_q = qoq_pp(merge(hx_opm_act, hx_opm_fwd))
axR.plot(x, mu_opm_q, "-o", color=MU_C, lw=2.2, ms=6, label="MU")
axR.plot(x, hx_opm_q, "-o", color=HX_C, lw=2.2, ms=6, label="Hynix")
axR.set_title("营业利润率 QoQ 变化 (pp)", fontsize=12, loc="left")
axR.axhline(0, color="gray", lw=0.8); axR.legend(fontsize=9, loc="upper right")
style(axR)

fig.text(0.01, 0.004,
         "来源: MU=memo §1 (non-GAAP) + MS模型; Hynix=Citi 2026-05-11 Fig.2 + 6/10 共识 + HSBC 5/12. "
         "FX KRW1,420/$ (HSBC). 灰底=预测区. 注: 绝对值受 FX 影响; QoQ/利润率 货币无关、可直接对比.",
         fontsize=8.5, color="dimgray")
fig.tight_layout(rect=[0, 0.012, 1, 0.965])
out = "/Users/owen/CC workspace/Finance/reports/mu-street-models-2026-06/mu_vs_hynix_comparison.png"
fig.savefig(out, dpi=150)
print("saved", out)

# ---- print data table for the record ----
print("\n# CQ2'26E (下一份财报) 关键对比:")
print(f"{'':14}{'MU (USD$B)':>16}{'Hynix (USD$B)':>16}")
print(f"{'收入-共识':14}{mu_rev_cons:>16.1f}{hx_rev_cons:>16.1f}  (Hynix ₩{hx_rev_cons_krw}tr)")
print(f"{'营利-共识':14}{mu_op_cons:>16.1f}{hx_op_cons:>16.1f}  (Hynix ₩{hx_op_cons_krw}tr)")
print(f"{'收入QoQ共识%':14}{(mu_rev_cons/mu_rev_act[3]-1)*100:>16.1f}{(hx_rev_cons_krw/hx_rev_act_krw[3]-1)*100:>16.1f}")
print(f"{'营利QoQ共识%':14}{(mu_op_cons/mu_op_act[3]-1)*100:>16.1f}{(hx_op_cons_krw/hx_op_act_krw[3]-1)*100:>16.1f}")
print(f"{'收入QoQ模型%':14}{mu_rev_q[NEXT]:>16.1f}{hx_rev_q[NEXT]:>16.1f}")
print(f"{'营利QoQ模型%':14}{mu_op_q[NEXT]:>16.1f}{hx_op_q[NEXT]:>16.1f}")
print(f"{'营利率%-共识':14}{mu_opm_cons:>16.1f}{hx_opm_cons:>16.1f}")

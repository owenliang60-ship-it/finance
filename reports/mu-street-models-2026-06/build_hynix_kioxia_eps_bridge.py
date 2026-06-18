"""SK Hynix EPS 桥: 含 Kioxia(报告) vs 剔除 Kioxia(经营) — 1Q26 基数 + 2Q26E 预测.

口径:
- 股数 705.1M (1Q26 实际 NP ₩40.35tr ÷ EPS ₩57,228 倒推)
- Kioxia FVPL 估值收益税后化 @22% (与 1Q26 实际有效税率 21.8% 对账一致)
- 1Q26: 实际数. Kioxia 税前估值收益 ₩9.88tr (分期报告披露).
- 2Q26E 经营基线: 卖方共识 EPS ₩66,606 (按惯例不含 mark-to-market Kioxia).
- 2Q26E Kioxia markup: 情景 {+₩10 / +₩25 / +₩40tr 税前}; 2Q Kioxia 涨幅>1Q,
  故 markup 大概率 ≥ 1Q 的 ₩9.88tr → 中/高情景更可能. (精确数取决于 SPC/CB 的 Level 3 估值)
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

SHARES = 705.1e6
TAX = 0.22

def per_share(krw_tr):
    return krw_tr * 1e12 / SHARES

# ---- 1Q26 实际 ----
q1_reported = 57228
q1_kioxia_pretax = 9.88
q1_kioxia_ps = per_share(q1_kioxia_pretax * (1 - TAX))   # 税后 / 股
q1_oper = q1_reported - q1_kioxia_ps                       # 经营 EPS (剔除 Kioxia)

# ---- 2Q26E ----
q2_oper = 66606                                            # 共识(经营, 不含 Kioxia)
markups = {"低 (+₩10tr)": 10.0, "中 (+₩25tr)": 25.0, "高 (+₩40tr)": 40.0}
q2_kioxia_ps = {k: per_share(v * (1 - TAX)) for k, v in markups.items()}
q2_reported = {k: q2_oper + v for k, v in q2_kioxia_ps.items()}

print("="*68)
print(f"{'':16}{'经营EPS(剔Kioxia)':>20}{'Kioxia贡献':>14}{'报告EPS':>14}")
print(f"{'1Q26 (实际)':16}{q1_oper:>20,.0f}{q1_kioxia_ps:>14,.0f}{q1_reported:>14,.0f}")
for k in markups:
    print(f"{'2Q26E '+k:16}{q2_oper:>20,.0f}{q2_kioxia_ps[k]:>14,.0f}{q2_reported[k]:>14,.0f}")
print("-"*68)
print(f"Kioxia 对 1Q26 报告EPS 的拉高: +{q1_kioxia_ps:,.0f}  (+{q1_kioxia_ps/q1_oper*100:.0f}%)")
print(f"EPS QoQ — 剔除Kioxia(经营):  {q1_oper:,.0f} → {q2_oper:,.0f} = +{(q2_oper/q1_oper-1)*100:.0f}%")
for k in markups:
    print(f"EPS QoQ — 报告({k}):    {q1_reported:,.0f} → {q2_reported[k]:,.0f} = +{(q2_reported[k]/q1_reported-1)*100:.0f}%")

# ============ 图 ============
fig, (axL, axR) = plt.subplots(1, 2, figsize=(15, 7))
KO = "#ff7f0e"; OP = "#1f77b4"

# 左: 堆叠 EPS 桥 (1Q26 + 2Q26E 三情景)
labels = ["1Q26\n(实际基数)", "2Q26E\n低", "2Q26E\n中", "2Q26E\n高"]
oper_vals = [q1_oper, q2_oper, q2_oper, q2_oper]
ko_vals = [q1_kioxia_ps, q2_kioxia_ps["低 (+₩10tr)"], q2_kioxia_ps["中 (+₩25tr)"], q2_kioxia_ps["高 (+₩40tr)"]]
xpos = np.arange(4)
axL.bar(xpos, oper_vals, color=OP, label="经营 EPS (剔除 Kioxia)")
axL.bar(xpos, ko_vals, bottom=oper_vals, color=KO, label="Kioxia FVPL 估值收益 (税后)")
for i in range(4):
    tot = oper_vals[i] + ko_vals[i]
    axL.text(i, tot+1500, f"₩{tot:,.0f}", ha="center", fontsize=10, fontweight="bold")
    axL.text(i, oper_vals[i]/2, f"₩{oper_vals[i]:,.0f}", ha="center", fontsize=8.5, color="white")
    axL.text(i, oper_vals[i]+ko_vals[i]/2, f"+{ko_vals[i]:,.0f}", ha="center", fontsize=8.5, color="white")
axL.axhline(q1_reported, color="gray", ls=":", lw=1)
axL.text(3.45, q1_reported, "1Q报告基数\n₩57,228", fontsize=8, color="gray", va="center")
axL.set_xticks(xpos); axL.set_xticklabels(labels, fontsize=9.5)
axL.set_ylabel("单季 EPS (KRW)")
axL.set_title("SK Hynix 单季 EPS 桥：经营 + Kioxia 估值收益", fontsize=12.5, loc="left")
axL.legend(fontsize=9.5, loc="upper left")
axL.grid(axis="y", alpha=0.25)

# 右: EPS QoQ — 两口径
scen = list(markups.keys())
qoq_oper = (q2_oper/q1_oper - 1)*100
qoq_rep = [(q2_reported[k]/q1_reported - 1)*100 for k in scen]
xx = np.arange(len(scen))
axR.axhline(qoq_oper, color=OP, lw=2.2, ls="--", label=f"剔除Kioxia(经营) = +{qoq_oper:.0f}%  ★真实经营增速")
axR.bar(xx, qoq_rep, color=KO, width=0.55, label="报告口径(含 Kioxia,随 markup 变)")
for i, v in enumerate(qoq_rep):
    axR.text(i, v+1.5, f"+{v:.0f}%", ha="center", fontsize=10, fontweight="bold")
axR.axhline(16, color="red", lw=1.3, ls=":", label="我之前给的 +16% (错配口径,作废)")
axR.set_xticks(xx); axR.set_xticklabels([f"报告\n{s}" for s in scen], fontsize=9.5)
axR.set_ylabel("EPS QoQ (%)")
axR.set_title("EPS QoQ 增速：1Q26 → 2Q26E", fontsize=12.5, loc="left")
axR.legend(fontsize=9, loc="upper left")
axR.grid(axis="y", alpha=0.25)
axR.set_ylim(0, max(qoq_rep)*1.18)

fig.text(0.01, 0.005,
         "口径: 705.1M 股 · Kioxia 估值收益税后@22% · 2Q 经营基线=共识 ₩66,606(不含Kioxia) · "
         "2Q Kioxia markup 为情景估计(Level 3, 涨幅>1Q→中/高更可能). 经营 EPS 不受 Kioxia 干扰.",
         fontsize=8.5, color="dimgray")
fig.tight_layout(rect=[0, 0.02, 1, 1])
out = "/Users/owen/CC workspace/Finance/reports/mu-street-models-2026-06/hynix_kioxia_eps_bridge.png"
fig.savefig(out, dpi=150)
print("\nsaved", out)

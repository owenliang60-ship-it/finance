"""MU vs SK Hynix vs Samsung 三方对比: Forward P/E + 下季 EPS QoQ + 盈利干净度.

数据(2026-06-17):
- 价格: MU $1,057(6/16) / Hynix ₩2,460,000(6/17) / Samsung ₩332,000(6/17)
- MU: 花旗 6/17 刷新 — FY26 EPS $60.73, FY27 $114.73(街 $110-113), FY28 $117.83(下行腿抹平);
       Citi 估值表 FY26 16.8x / FY27 8.9x / FY28 8.7x @ $1,020.76; EPS QoQ FQ3 +57~67%; 干净
- Hynix: FY26 报告 EPS ₩301,077(含Kioxia)/干净~₩268,000; FY27 ~₩370,000(Citi净利折);
         EPS QoQ 干净 +44~63%(OP依赖), 报告(含Kioxia) +36~94%
- Samsung: FY26 EPS ₩36,859 / FY27 ₩50,550 (JPM adj 3/22, 季度模型 Table 10); EPS QoQ ~+51%; 干净
  (注: JPM 3/22 模型 1Q26E 偏低, 实际已超; forward 偏保守, 价格已远超其 TP ₩300K)
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

names = ["美光 MU", "SK海力士", "三星电子"]
col = ["#d62728", "#1f77b4", "#2ca02c"]

# ---- forward P/E ----
pe_fy26 = [16.8, 8.2, 9.0]          # MU=花旗6/17刷新; Hynix=报告(含Kioxia)
pe_fy26_clean = [16.8, 9.2, 9.0]    # Hynix 剔Kioxia后
pe_fy27 = [8.9, 6.6, 6.6]           # MU 因 FY27 EPS 上修至 $114.73 → 10.8x 降到 8.9x

# ---- 下季 EPS QoQ (干净经营口径) ----
qoq_lo = [57, 44, 49]
qoq_hi = [67, 63, 53]               # MU 上沿用花旗 6/17 $19.98 (vs Q2 core $11.94 = +67%)
qoq_mid = [(a+b)/2 for a, b in zip(qoq_lo, qoq_hi)]

fig, axes = plt.subplots(1, 3, figsize=(17, 6.2))

# Panel 1: Forward P/E (FY26 含/剔Kioxia + FY27)
ax = axes[0]
x = np.arange(3); w = 0.26
ax.bar(x - w, pe_fy26, w, color=col, alpha=0.55, label="FY26 (报告)")
# Hynix 剔Kioxia 叠加(只对中间那根)
ax.bar(x[1] - w, pe_fy26_clean[1] - pe_fy26[1], w, bottom=pe_fy26[1],
       color=col[1], alpha=1.0, hatch="///", edgecolor="white",
       label="Hynix 剔Kioxia 修正(+1x)")
ax.bar(x + w*0.0, [np.nan]*3, w)  # spacer placeholder (skip)
ax.bar(x + w, pe_fy27, w, color=col, alpha=1.0, label="FY27")
for i in range(3):
    ax.text(i - w, pe_fy26[i]+0.3, f"{pe_fy26[i]:.0f}x", ha="center", fontsize=9)
    ax.text(i + w, pe_fy27[i]+0.3, f"{pe_fy27[i]:.1f}x", ha="center", fontsize=9)
ax.text(1 - w, pe_fy26_clean[1]+0.5, "9.2x\n(干净)", ha="center", fontsize=7.5, color="#0d3b66")
ax.set_xticks(x); ax.set_xticklabels(names, fontsize=11)
ax.set_ylabel("Forward P/E (x)")
ax.set_title("① Forward P/E (按当前价, MU=花旗6/17刷新)\nFY27 三家挤到 7-9x; Hynix 8x 含Kioxia水分,剔后~9x≈MU≈三星", fontsize=10.5, loc="left")
ax.legend(fontsize=8.5, loc="upper right")
ax.grid(axis="y", alpha=0.25)

# Panel 2: 下季 EPS QoQ (干净) + Hynix 报告区间
ax = axes[1]
yerr = [[m-l for m, l in zip(qoq_mid, qoq_lo)], [h-m for m, h in zip(qoq_mid, qoq_hi)]]
ax.bar(x, qoq_mid, 0.5, color=col, yerr=yerr, capsize=6, error_kw=dict(lw=1.5))
for i in range(3):
    ax.text(i, qoq_hi[i]+2.5, f"+{qoq_lo[i]}~{qoq_hi[i]}%", ha="center", fontsize=9, fontweight="bold")
# Hynix 报告口径(含Kioxia) 区间标注
ax.annotate("Hynix 报告口径(含Kioxia):\n+36% ~ +94% (随markup乱跳)",
            xy=(1, 63), xytext=(1.05, 88), fontsize=8, color="#1f77b4",
            arrowprops=dict(arrowstyle="->", color="#1f77b4", lw=1))
ax.set_xticks(x); ax.set_xticklabels(names, fontsize=11)
ax.set_ylabel("下季 EPS QoQ (%)")
ax.set_ylim(0, 105)
ax.set_title("② 下次财报 EPS QoQ (干净经营口径)\n三家基本打平 (+44~63%), 谁第一取决于OP落点", fontsize=11, loc="left")
ax.grid(axis="y", alpha=0.25)

# Panel 3: 盈利干净度 + 结构 (文字面板)
ax = axes[2]
ax.axis("off")
rows = [
    ("",            "MU",        "Hynix",         "Samsung"),
    ("业态",         "纯内存",     "纯内存",         "集团(内存94%OP)"),
    ("EPS 干净?",    "✓ 干净",    "✗ Kioxia注水",   "✓ 干净"),
    ("非经营污染",   "无",        "1Q +₩9.9tr\n(净利25%)", "无(~₩1.5tr)"),
    ("公司营业利率", "77%",       "77%",            "43%(内存66%)"),
    ("下季营利QoQ",  "+57%",      "+57~71%",        "~+50%"),
    ("HBM 地位",     "#3 追赶",   "龙头62%",        "追赶,认证落后"),
    ("FY27 P/E",     "8.9x(已不贵)", "6.6x(假便宜Kioxia)", "6.6x+集团折价"),
    ("FY28 走向",    "花旗抹平下行腿", "—",              "—"),
]
ncol = 4
cw = [0.24, 0.25, 0.27, 0.30]
xpos = [0.0, 0.25, 0.50, 0.78]
y0 = 0.95; dy = 0.099
for r, row in enumerate(rows):
    yy = y0 - r*dy
    for c in range(ncol):
        txt = row[c]
        fw = "bold" if (r == 0 or c == 0) else "normal"
        cc = "black"
        if r == 0 and c > 0: cc = col[c-1]
        ax.text(xpos[c], yy, txt, fontsize=9.2, fontweight=fw, color=cc,
                va="top", ha="left", transform=ax.transAxes)
    if r == 0:
        ax.plot([0, 1], [yy-0.045, yy-0.045], color="gray", lw=1, transform=ax.transAxes)
ax.set_title("③ 结构与盈利质量对比", fontsize=11, loc="left")

fig.suptitle("美光 MU vs SK海力士 vs 三星电子 — 下次财报三方对比 (2026-06-17)",
             fontsize=14, y=1.0)
fig.text(0.01, 0.005,
         "价格: MU $1,020.76(6/16) / Hynix ₩2.46M / Samsung ₩332K. MU估值=花旗6/17刷新(FY27 EPS$114.73). "
         "EPS干净=剔一次性非经营估值收益; Hynix Kioxia FVPL收益直接进净利→压低P/E、扰乱EPS QoQ; 三星/MU净利干净.",
         fontsize=8.3, color="dimgray")
fig.tight_layout(rect=[0, 0.02, 1, 0.97])
out = "/Users/owen/CC workspace/Finance/reports/mu-street-models-2026-06/three_way_compare.png"
fig.savefig(out, dpi=150)
print("saved", out)

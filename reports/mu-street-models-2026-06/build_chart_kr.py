"""Hynix + Samsung 基本面 16 季全景图（日历季度, KRW tr）.

数据来源:
- 实绩 2Q24-3Q25: 公司公告(真实历史), 经 Citi/JPM 报告年度合计交叉验证
- 实绩 4Q25/1Q26: Citi 2026-05-11 Fig.2 (Hynix) / JPM 2026-05-07 1Q26 review (Samsung)
- Hynix 预测: Citi 季度模型到 4Q26E + Citi FY27 年度平推 + HSBC 2026-05-12 FY28 年度平推
- Samsung 预测: JPM 2026-05-07 + GS 2026-05-03 均为年度模型, 全部年度平推
平推季度只代表年化水平、不代表季度形状; QoQ 面板在真实季度数据终点截断。
韩股卖方 headline 是营业利润(OP), 季度净利前瞻未披露 → 利润维度用 OP + NP(可得点)。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

Q = ["2Q24", "3Q24", "4Q24", "1Q25", "2Q25", "3Q25", "4Q25", "1Q26",
     "2Q26", "3Q26", "4Q26", "1Q27", "2Q27", "3Q27", "4Q27", "1Q28"]
N = len(Q); x = np.arange(N); SPLIT = 7.5
nan = float("nan")

def qoq(series, stop=None):
    out = [nan]*N
    for i in range(1, N if stop is None else stop+1):
        a, b = series[i-1], series[i]
        if a == a and b == b and a != 0:
            out[i] = (b/a - 1) * 100
    return out

def qoq_pp(series, stop=None):
    out = [nan]*N
    for i in range(1, N if stop is None else stop+1):
        a, b = series[i-1], series[i]
        if a == a and b == b:
            out[i] = b - a
    return out

COMPANIES = {
    "SK Hynix (000660.KS)": {
        "act_rev": [16.42, 17.57, 19.77, 17.64, 22.23, 24.45, 32.83, 52.58] + [nan]*8,
        "act_op":  [5.47, 7.03, 8.08, 7.44, 9.21, 11.38, 19.17, 37.61] + [nan]*8,
        "act_opm": [33.3, 40.0, 40.9, 42.2, 41.4, 46.6, 58.4, 71.5] + [nan]*8,
        "act_np":  [4.12, 5.75, 8.01, 8.11, 7.00, 12.60, nan, nan] + [nan]*8,
        # Citi 真实季度到 4Q26 (idx 8-10); 之后 FY27 平推 (idx 11-14), HSBC FY28 平推 (idx 15)
        "fwd": [
            ("Citi 5/11 (季度→4Q26, FY27平推)", "#1f77b4",
             [nan]*7 + [52.58, 76.36, 90.75, 101.49, 109.59, 109.59, 109.59, 109.59, nan],
             [nan]*7 + [37.61, 58.86, 72.31, 82.37, 86.71, 86.71, 86.71, 86.71, nan],
             [nan]*7 + [71.5, 77.1, 79.7, 81.2, 79.1, 79.1, 79.1, 79.1, nan],
             [nan]*7 + [nan, 45.14, 55.49, nan, 65.60, 65.60, 65.60, 65.60, nan],
             10),  # 真实季度数据截止 idx
            ("HSBC 5/12 (FY26-28 年度平推)", "#2ca02c",
             [nan]*7 + [nan, 92.03, 92.03, 92.03, 118.96, 118.96, 118.96, 118.96, 140.78],
             [nan]*7 + [nan, 75.65, 75.65, 75.65, 96.91, 96.91, 96.91, 96.91, 101.79],
             [nan]*7 + [nan, 82.2, 82.2, 82.2, 81.5, 81.5, 81.5, 81.5, 72.3],
             [nan]*7 + [nan, 51.69, 51.69, 51.69, 72.66, 72.66, 72.66, 72.66, 76.40],
             7),
        ],
        "note": "HSBC 平推 = (FY26e−1Q26A)/3 与 FY27e/4, FY28e/4 | GS 6/2: TP W3.5M (+94%), 未披露新模型明细",
    },
    "Samsung Electronics (005930.KS)": {
        "act_rev": [74.07, 79.10, 75.79, 79.14, 74.57, 86.06, 93.84, 133.87] + [nan]*8,
        "act_op":  [10.44, 9.18, 6.49, 6.69, 4.68, 12.16, 20.07, 57.23] + [nan]*8,
        "act_opm": [14.1, 11.6, 8.6, 8.5, 6.3, 14.1, 21.4, 42.8] + [nan]*8,
        "act_np":  [9.84, 9.78, 7.75, 8.03, 4.93, 12.22, 19.29, 47.10] + [nan]*8,
        "fwd": [
            ("JPM 5/7 (FY26-28 年度平推)", "#d62728",
             [nan]*7 + [133.87, 174.74, 174.74, 174.74, 201.50, 201.50, 201.50, 201.50, 233.20],
             [nan]*7 + [57.23, 99.82, 99.82, 99.82, 119.08, 119.08, 119.08, 119.08, 141.93],
             [nan]*7 + [42.8, 57.1, 57.1, 57.1, 59.1, 59.1, 59.1, 59.1, 60.9],
             [nan]*7 + [47.10, 82.10, 82.10, 82.10, 99.95, 99.95, 99.95, 99.95, 121.53],
             7),
            ("GS 5/3 (FY26-28 年度平推)", "#b8860b",
             [nan]*7 + [133.87, 186.40, 186.40, 186.40, 198.15, 198.15, 198.15, 198.15, 223.84],
             [nan]*7 + [57.23, 99.13, 99.13, 99.13, 109.55, 109.55, 109.55, 109.55, 123.65],
             [nan]*7 + [42.8, 53.2, 53.2, 53.2, 55.3, 55.3, 55.3, 55.3, 55.2],
             [nan]*7 + [47.10, 77.68, 77.68, 77.68, 87.08, 87.08, 87.08, 87.08, 99.69],
             7),
        ],
        "note": "两家均无季度前瞻 → 全年度平推; 1Q26 OP QoQ +185% 为本周期增速峰(实绩)",
    },
}

for cname, d in COMPANIES.items():
    fig, axes = plt.subplots(4, 2, figsize=(16, 18))
    short = cname.split(" (")[0]
    fig.suptitle(f"{cname} 基本面 16 季全景：2Q24–1Q26 实绩 + 2Q26–1Q28 券商预测 (KRW tr)\n"
                 "灰底=预测区 · 平推段(细虚线)只代表年化水平不代表季度形状 · QoQ 面板在真实季度数据终点截断",
                 fontsize=13, y=0.995)
    panels = [
        ("收入 (KRW tr)", "act_rev", 0),
        ("营业利润 (KRW tr)", "act_op", 1),
        ("净利润 (KRW tr, 部分季度未披露)", "act_np", 3),
        ("营业利润率 (%)", "act_opm", 2),
    ]
    for r, (title, akey, fidx) in enumerate(panels):
        axL, axR = axes[r]
        a = d[akey]
        axL.plot(x, a, "-o", color="#1a1a1a", lw=2, ms=5, label="实绩")
        is_margin = "率" in title
        qa = qoq_pp(a, 7) if is_margin else qoq(a, 7)
        axR.plot(x, qa, "-o", color="#1a1a1a", lw=2, ms=5)
        for (label, color, frev, fop, fopm, fnp, qstop) in d["fwd"]:
            series = [frev, fop, fopm, fnp][fidx]
            # 真实季度段(到 qstop)实线虚线, 平推段细虚线低透明
            solid = [v if i <= qstop else nan for i, v in enumerate(series)]
            flat = [v if i >= qstop else nan for i, v in enumerate(series)]
            axL.plot(x, solid, "--s", color=color, lw=1.6, ms=4, label=label)
            axL.plot(x, flat, ":", color=color, lw=1.2, alpha=0.55)
            qf = (qoq_pp(series, qstop) if is_margin else qoq(series, qstop))
            axR.plot(x, qf, "--s", color=color, lw=1.6, ms=4)
        axL.set_title(title, fontsize=12, loc="left")
        axR.set_title(title.split(" ")[0] + (" QoQ 变化 (pp)" if is_margin else " QoQ 增速 (%)"),
                      fontsize=12, loc="left")
        axR.axhline(0, color="gray", lw=0.8)
        for ax in (axL, axR):
            ax.axvspan(SPLIT, N-0.5, color="#f0f0f0", zorder=0)
            ax.axvline(SPLIT, color="gray", ls=":", lw=1)
            ax.set_xticks(x); ax.set_xticklabels(Q, rotation=55, fontsize=8)
            ax.grid(alpha=0.25)
        if r == 0:
            axL.legend(fontsize=9, loc="upper left")
    fig.text(0.01, 0.002, "注: " + d["note"], fontsize=9, color="dimgray")
    fig.tight_layout(rect=[0, 0.01, 1, 0.97])
    out = f"/Users/owen/CC workspace/Finance/reports/mu-street-models-2026-06/{short.lower().replace(' ', '_')}_fundamentals_16q.png"
    fig.savefig(out, dpi=150)
    print("saved", out)

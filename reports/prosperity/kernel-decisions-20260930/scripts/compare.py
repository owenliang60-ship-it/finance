import json, sys, collections
SP = sys.argv[1]
def load(side, mode, scheme, date):
    rows = [json.loads(l) for l in open(f"{SP}/{side}/{mode}/board-{scheme}-{date}.jsonl")]
    return {r["factor_row"]["symbol"]: r for r in rows}
def close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9 * max(1, abs(a)))
out = {}
for mode, date in (("live-0929", "2026-09-29"), ("replay-240817", "2024-08-17")):
    for scheme in ("F1", "F1-rank", "F1-exfin", "F0"):
        m, n = load("main", mode, scheme, date), load("new", mode, scheme, date)
        assert set(m) == set(n)
        fac = collections.defaultdict(list); miss = collections.Counter()
        for s in m:
            fm, fn = m[s]["factor_row"], n[s]["factor_row"]
            for k in set(fm["values"]) | set(fn["values"]):
                if not close(fm["values"].get(k), fn["values"].get(k)):
                    fac[k].append(s)
                    if fm["values"].get(k) is None or fn["values"].get(k) is None:
                        miss[(k, fm["missing"].get(k), fn["missing"].get(k))] += 1
        sm = {s: m[s]["scored"] for s in m}; sn = {s: n[s]["scored"] for s in n}
        badge = collections.Counter()
        for s in m:
            bm, bn = set(sm[s]["badges"]), set(sn[s]["badges"])
            for b in bn - bm: badge["+" + b] += 1
            for b in bm - bn: badge["-" + b] += 1
        status = sum(sm[s]["status"] != sn[s]["status"] for s in m)
        grade = [(s, sm[s].get("grade"), sn[s].get("grade")) for s in m if sm[s].get("grade") != sn[s].get("grade")]
        dscore = [abs((sn[s].get("score") or 0) - (sm[s].get("score") or 0)) for s in m if sm[s].get("score") is not None and sn[s].get("score") is not None]
        top = lambda d: [s for s, _ in sorted(((s, r["rank"]) for s, r in d.items() if r.get("rank")), key=lambda x: x[1])[:40]]
        tm, tn = top(sm), top(sn)
        out[f"{mode}/{scheme}"] = {
            "factor_changes": {k: (len(v), sorted(v)[:25]) for k, v in fac.items()},
            "missing_transitions": {f"{k[0]}: {k[1]} -> {k[2]}": c for k, c in miss.items()},
            "badges": dict(badge), "status_changes": status, "grade_changes": grade,
            "max_abs_score_change": round(max(dscore), 4) if dscore else 0,
            "top40_in": [s for s in tn if s not in tm], "top40_out": [s for s in tm if s not in tn],
            "top10_main": tm[:10], "top10_new": tn[:10]}
json.dump(out, open(f"{SP}/compare.json", "w"), indent=1, ensure_ascii=False)
for k, v in out.items():
    print("==", k); [print("  ", kk, vv) for kk, vv in v.items()]

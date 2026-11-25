"""고정 비중 자산배분 + ETF 추세전환 조합 검증.

계좌를 (자산배분 ws, 추세전환 wt, 나머지 현금)으로 나눠 일간 수익을 합산한다(매일 비중 유지 근사).
추세전환은 운 좋은 설정에 속지 않도록 '현재 설정'과 '20개 설정 평균' 두 가지로 본다.
대기자금 이자·분배금·해외 ETF 매매차익 과세는 미반영.
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
import etf_robustness as er  # noqa: E402
import static_allocation as sa  # noqa: E402


def metrics(r):
    eq = (1 + r).cumprod()
    years = len(r) / 252
    ye = eq.resample("YE").last()
    yr = ye.pct_change()
    yr.iloc[0] = ye.iloc[0] - 1
    part = lambda a, b: ((1 + r[a:b]).prod() ** (252 / len(r[a:b])) - 1) * 100
    return {
        "연수익": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
        "낙폭": round((eq / eq.cummax() - 1).min() * 100, 1),
        "샤프": round(r.mean() / r.std() * np.sqrt(252), 2),
        "앞": round(part("2017", "2021"), 1), "뒤": round(part("2022", "2026"), 1),
        "플러스 해": int((yr > 0).sum()), "최악의 해": round(yr.min() * 100, 1),
        "최고의 해": round(yr.max() * 100, 1),
    }


def main():
    dates, arr, si = er.prepare()
    base_eq, _ = er.simulate(dates, arr, si, alloc=1.0, **er.BASE)
    trend_base = base_eq.pct_change().fillna(0)
    grid = list(itertools.product([40, 60, 90, 120], [(40, 80), (60, 120), (90, 180), (20, 120), (120, 240)]))
    rets = []
    for ma, (s, l) in grid:
        eq, _ = er.simulate(dates, arr, si, alloc=1.0, **{**er.BASE, "ma": ma, "short": s, "long": l})
        rets.append(eq.pct_change().fillna(0))
    trend_avg = pd.concat(rets, axis=1).mean(axis=1)

    data = sa.load_prices([sa.K, sa.NQ, sa.SP, sa.B3, sa.B10, sa.GOLD, sa.USD], "2014-01-01", sa.CACHE)
    close = pd.DataFrame({c: d["Close"] for c, d in data.items()}).ffill()
    statics = {}
    for name in ("올웨더형(주식30·채권40·금15·달러15)", "영구 포트폴리오(주식·채권·금·달러 25%씩)",
                 "균형형(주식50·채권25·금15·달러10)"):
        eq = sa.simulate(close, sa.PORTFOLIOS[name], dates[si])
        statics[name.split("(")[0]] = eq.pct_change().fillna(0).reindex(trend_base.index).fillna(0)

    m = lambda r: (1 + r).resample("ME").prod() - 1
    for sn, sr in statics.items():
        print(f"{sn} vs 추세전환 월간 상관: 현재설정 {m(sr).corr(m(trend_base)):.2f}, 평균 {m(sr).corr(m(trend_avg)):.2f}")

    rows = []
    for tn, tr in (("현재 설정", trend_base), ("20개 설정 평균", trend_avg)):
        rows.append({"추세전환": tn, "구성": "추세전환 40% (지금 정책)", **metrics(0.4 * tr)})
        for sn, sr in statics.items():
            for ws, wt in ((1.0, 0.0), (0.7, 0.3), (0.6, 0.4), (0.5, 0.5), (0.5, 0.3)):
                if wt == 0 and tn != "현재 설정":
                    continue
                rows.append({"추세전환": tn if wt else "-", "구성": f"{sn} {int(ws * 100)}% + 추세 {int(wt * 100)}%",
                             **metrics(ws * sr + wt * tr)})
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 230)
    print(out.to_string(index=False))
    out.to_csv(os.path.join(sa.CACHE, "combo_allocation.csv"), index=False)
    best = 0.6 * statics["올웨더형"] + 0.4 * trend_avg
    eq = (1 + best).cumprod()
    ye = eq.resample("YE").last()
    yr = ye.pct_change()
    yr.iloc[0] = ye.iloc[0] - 1
    print("올웨더형 60% + 추세(평균) 40% 연도별:", {d.year: round(v * 100, 1) for d, v in yr.items()})


if __name__ == "__main__":
    main()

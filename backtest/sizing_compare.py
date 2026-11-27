"""추세전환 40%에서 종목별 투입 비중 규칙 비교: 일괄 40% / 인버스만 15% / 변동성 맞춤.

계좌 = 코어(올웨더형 근사) 50% + 추세전환 40% + 나머지 대기자금.
추세전환 안에서 안 쓴 돈은 대기자금(연 cash_rate)으로 본다. 추세전환은 현재 설정과 20개 설정 평균 둘 다.
변동성 맞춤: 진입 전일까지 60일 연변동성 v로 투입 = min(1, 목표/v) (추세전환 몫 대비).
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combo_allocation as ca  # noqa: E402
import etf_robustness as er  # noqa: E402
import static_allocation as sa  # noqa: E402

CASH_RATE = 0.03
INV = "114800"


def main():
    dates, arr, si = er.prepare()
    vol = {}
    for c, a in arr.items():
        r = pd.Series(a["Close"], index=dates).ffill().pct_change()
        vol[c] = (r.rolling(60).std() * np.sqrt(252)).shift(1).values
    rules = {
        "일괄 40%": None,
        "인버스만 15% (현재)": lambda c, i: 15 / 40 if c == INV else 1.0,
    }
    for tgt in (0.12, 0.15, 0.20):
        rules[f"변동성 맞춤 목표 {int(tgt*100)}%"] = (lambda t: lambda c, i: min(1.0, t / vol[c][i]) if vol[c][i] > 0 else 1.0)(tgt)
    grid = list(itertools.product([40, 60, 90, 120], [(40, 80), (60, 120), (90, 180), (20, 120), (120, 240)]))

    data = sa.load_prices([sa.K, sa.NQ, sa.SP, sa.B3, sa.B10, sa.GOLD, sa.USD], "2014-01-01", sa.CACHE)
    close = pd.DataFrame({c: d["Close"] for c, d in data.items()}).ffill()
    core = sa.simulate(close, sa.PORTFOLIOS["올웨더형(주식30·채권40·금15·달러15)"], dates[si]).pct_change().fillna(0)

    rows = []
    for name, fn in rules.items():
        base, _ = er.simulate(dates, arr, si, alloc=1.0, size_fn=fn, cash_rate=CASH_RATE, **er.BASE)
        rets = [er.simulate(dates, arr, si, alloc=1.0, size_fn=fn, cash_rate=CASH_RATE,
                            **{**er.BASE, "ma": ma, "short": s, "long": l})[0].pct_change().fillna(0) for ma, (s, l) in grid]
        avg = pd.concat(rets, axis=1).mean(axis=1)
        park = CASH_RATE / 252 * 0.10
        for tn, tr in (("현재 설정", base.pct_change().fillna(0)), ("20개 평균", avg)):
            r = 0.5 * core.reindex(tr.index).fillna(0) + 0.4 * tr + park
            worst = r.nsmallest(1)
            rows.append({"규칙": name, "추세 설정": tn, **ca.metrics(r), "최악의 하루": round(worst.iloc[0] * 100, 1),
                         "최악일": worst.index[0].date()})
        print(name, "done", flush=True)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    for tn in ("현재 설정", "20개 평균"):
        print(f"\n[{tn}]")
        print(out[out["추세 설정"] == tn].drop(columns="추세 설정").to_string(index=False))


if __name__ == "__main__":
    main()

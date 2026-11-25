"""ETF 추세전환 엣지 견고성 검증: 파라미터·자산·기간을 바꿔도 성적이 유지되는지 본다.

실전 선택 규칙(60일선 +1%, 기울기 5일, 3/6개월 40/60, 교체 완충 2%p, 재해 손절 5%,
역방향 갭 -3%, 청산 당일 재진입 금지)을 파라미터화해 다시 구현했다.
계좌의 alloc 비율만 ETF에 넣고 나머지는 수익 0(대기자금 미반영)으로 계산한다.
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from config import ETF_ROTATION_ASSETS  # noqa: E402
from etf_rotation_backtest import load_prices, TAX_EXEMPT, DIVIDEND_TAX, ONE_WAY  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
BASE = dict(ma=60, band=0.01, slope=5, short=60, long=120, margin=0.02, stop=0.05)


def prepare(start="2017-01-02"):
    data = load_prices(list(ETF_ROTATION_ASSETS), "2014-01-01", CACHE)
    dates = data["069500"].index
    arr = {}
    for code, df in data.items():
        df = df.reindex(dates)
        arr[code] = {k: df[k].values.astype(float) for k in ("Open", "High", "Low", "Close")}
    return dates, arr, int(np.searchsorted(dates, pd.Timestamp(start)))


def simulate(dates, arr, start_i, ma=60, band=0.01, slope=5, short=60, long=120,
             margin=0.02, stop=0.05, alloc=0.5, assets=None):
    assets = assets or list(arr)
    need = max(ma + slope, long + 1)
    closes = {c: arr[c]["Close"] for c in assets}
    # 결측을 건너뛴 확정 종가 배열 인덱스
    valid_idx = {c: np.flatnonzero(~np.isnan(closes[c])) for c in assets}
    equity, held, entry, shares_value = 1.0, None, 0.0, 0.0
    cash = 1.0
    units = 0.0
    cost_basis = 0.0
    curve = np.ones(len(dates))
    trades = []
    for i in range(start_i, len(dates)):
        scores, elig = {}, {}
        for c in assets:
            vi = valid_idx[c]
            n = np.searchsorted(vi, i)          # i 이전(전일까지) 확정 봉 수
            if n < need:
                continue
            cl = closes[c][vi[:n]]
            last = cl[-1]
            ma_now = cl[-ma:].mean()
            ma_prev = cl[-slope - ma:-slope].mean()
            sc = 0.4 * (last / cl[-short - 1] - 1) + 0.6 * (last / cl[-long - 1] - 1)
            scores[c] = sc
            elig[c] = last > ma_now * (1 + band) and ma_now > ma_prev and sc > 0
        cands = [(scores[c], c) for c in assets if elig.get(c)]
        target = max(cands)[1] if cands else None
        if target and held and held != target and elig.get(held) and max(cands)[0] < scores[held] + margin:
            target = held
        exited = False
        if held:
            o, lo, hi = arr[held]["Open"][i], arr[held]["Low"][i], arr[held]["High"][i]
            if not np.isnan(o):
                px = None
                if target != held:
                    px = o
                elif stop is not None:
                    sp = entry * (1 - stop)
                    if o <= sp:
                        px = o
                    elif lo <= sp:
                        px = sp
                if px is not None:
                    proceeds = units * px * (1 - ONE_WAY)
                    gain = proceeds - cost_basis
                    if held not in TAX_EXEMPT and gain > 0:
                        proceeds -= gain * DIVIDEND_TAX
                    trades.append(proceeds / cost_basis - 1)
                    cash += proceeds
                    held, units, exited = None, 0.0, True
        if held is None and target and not exited:
            o = arr[target]["Open"][i]
            vi = valid_idx[target]
            prev_close = closes[target][vi[np.searchsorted(vi, i) - 1]]
            if not np.isnan(o) and o / prev_close - 1 >= -0.03:
                invest = cash * alloc if alloc < 1 else cash
                units = invest / (o * (1 + ONE_WAY))
                cost_basis = invest
                cash -= invest
                held, entry = target, o
        value = cash
        if held:
            c_ = arr[held]["Close"][i]
            if np.isnan(c_):
                vi = valid_idx[held]
                c_ = closes[held][vi[np.searchsorted(vi, i) - 1]]
            value += units * c_
        curve[i] = value
    return pd.Series(curve[start_i:], index=dates[start_i:]), trades


def stat(eq, a=None, b=None):
    e = eq[a:b]
    e = e / e.iloc[0]
    years = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return (round((e.iloc[-1] ** (1 / years) - 1) * 100, 1),
            round((e / e.cummax() - 1).min() * 100, 1),
            round(r.mean() / r.std() * np.sqrt(252), 2) if r.std() else 0.0)


def main():
    dates, arr, si = prepare()
    pd.set_option("display.width", 220)
    eq, tr = simulate(dates, arr, si, **BASE)
    print("기준(현재 정책, ETF 50%, 대기자금 제외):", stat(eq), "매매", len(tr))
    print("  앞 2017~2021:", stat(eq, "2017", "2021"), " 뒤 2022~2026:", stat(eq, "2022", "2026"))
    yearly = eq.resample("YE").last().pct_change()
    yearly.iloc[0] = eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1
    print("  연도별(%):", {d.year: round(v * 100, 1) for d, v in yearly.items()})

    print("\n[1] 한 번에 하나씩 바꾸기 (연수익, 낙폭, 샤프) 전체 / 앞 / 뒤")
    one = {
        "ma": [20, 40, 60, 90, 120], "band": [0.0, 0.01, 0.02, 0.03],
        "slope": [1, 5, 10, 20], "margin": [0.0, 0.02, 0.05, 0.10],
        "stop": [0.03, 0.05, 0.08, 0.12, None],
    }
    for key, values in one.items():
        for v in values:
            eq, tr = simulate(dates, arr, si, **{**BASE, key: v})
            mark = " ← 현재" if v == BASE[key] else ""
            print(f"  {key}={v}: {stat(eq)} / {stat(eq, '2017', '2021')} / {stat(eq, '2022', '2026')} 매매 {len(tr)}{mark}")
    for s, l in [(20, 60), (40, 80), (60, 120), (90, 180), (120, 240), (20, 120), (60, 240)]:
        eq, tr = simulate(dates, arr, si, **{**BASE, "short": s, "long": l})
        mark = " ← 현재" if (s, l) == (60, 120) else ""
        print(f"  모멘텀 {s}/{l}일: {stat(eq)} / {stat(eq, '2017', '2021')} / {stat(eq, '2022', '2026')} 매매 {len(tr)}{mark}")

    print("\n[2] 자산 하나씩 빼기")
    for drop in ETF_ROTATION_ASSETS:
        assets = [c for c in ETF_ROTATION_ASSETS if c != drop]
        eq, tr = simulate(dates, arr, si, assets=assets, **BASE)
        print(f"  {ETF_ROTATION_ASSETS[drop]} 제외: {stat(eq)} / {stat(eq, '2017', '2021')} / {stat(eq, '2022', '2026')}")

    print("\n[3] 전체 그리드")
    rows = []
    for ma, band, (s, l), margin, stop in itertools.product(
            [20, 40, 60, 90, 120], [0.0, 0.01, 0.02],
            [(20, 60), (40, 80), (60, 120), (90, 180), (120, 240)],
            [0.0, 0.02, 0.05], [0.03, 0.05, 0.08, None]):
        eq, tr = simulate(dates, arr, si, ma=ma, band=band, slope=5, short=s, long=l, margin=margin, stop=stop)
        a, b, c = stat(eq), stat(eq, "2017", "2021"), stat(eq, "2022", "2026")
        rows.append(dict(ma=ma, band=band, short=s, long=l, margin=margin, stop=stop,
                         cagr=a[0], mdd=a[1], sharpe=a[2], is_cagr=b[0], is_mdd=b[1],
                         oos_cagr=c[0], oos_mdd=c[1], is_sharpe=b[2], oos_sharpe=c[2]))
    g = pd.DataFrame(rows)
    g.to_csv(os.path.join(CACHE, "etf_robustness.csv"), index=False)
    print("  조합 수", len(g))
    print("  전체기간 연수익 분위(10/25/50/75/90%):", g.cagr.quantile([.1, .25, .5, .75, .9]).round(1).tolist())
    print("  전체기간 낙폭 분위:", g.mdd.quantile([.1, .25, .5, .75, .9]).round(1).tolist())
    print("  연수익>0 비율:", round((g.cagr > 0).mean() * 100), "%  두 기간 모두 >0:", round(((g.is_cagr > 0) & (g.oos_cagr > 0)).mean() * 100), "%")
    print("  두 기간 모두 연 3% 이상:", round(((g.is_cagr >= 3) & (g.oos_cagr >= 3)).mean() * 100), "%")
    print("  앞-뒤 기간 샤프 상관:", round(g.is_sharpe.corr(g.oos_sharpe), 2))
    base = g[(g.ma == 60) & (g.band == 0.01) & (g.short == 60) & (g.margin == 0.02) & (g.stop == 0.05)].iloc[0]
    print("  현재 정책의 순위(샤프 기준 상위 %):", round((g.sharpe > base.sharpe).mean() * 100))
    for col in ("ma", "band", "short", "margin", "stop"):
        print(g.fillna({"stop": 0}).groupby(col)[["cagr", "mdd", "is_cagr", "oos_cagr"]].median().round(1).to_string())


if __name__ == "__main__":
    main()

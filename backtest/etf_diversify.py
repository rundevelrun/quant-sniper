"""ETF 추세전환 확장 검증: 자산을 늘리고 상위 K종목으로 나눠 담으면 낙폭·쏠림이 줄어드는가.

파라미터 운에 속지 않도록 설정 하나가 아니라 (이동평균 × 모멘텀 기간) 25개 조합의
중간값으로 비교한다. 계좌의 alloc만 ETF에 넣고 나머지는 수익 0으로 계산한다.
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from etf_rotation_backtest import load_prices, TAX_EXEMPT, DIVIDEND_TAX, ONE_WAY  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
BASE8 = {
    "069500": ("KODEX 200", "국내주식"), "229200": ("KODEX 코스닥150", "국내주식"),
    "379800": ("KODEX 미국S&P500", "해외주식"), "379810": ("KODEX 미국나스닥100", "해외주식"),
    "132030": ("KODEX 골드선물(H)", "원자재"), "114260": ("KODEX 국고채3년", "채권"),
    "261240": ("KODEX 미국달러선물", "통화"), "114800": ("KODEX 인버스", "인버스"),
}
# 상장 이력이 긴 미국 ETF로 교체
LONG_US = {k: v for k, v in BASE8.items() if k not in ("379800", "379810")}
LONG_US.update({"219480": ("KODEX 미국S&P500선물(H)", "해외주식"),
                "133690": ("TIGER 미국나스닥100", "해외주식")})
EXTENDED = dict(LONG_US)
EXTENDED.update({
    "241180": ("TIGER 일본니케이225", "해외주식"), "200250": ("KIWOOM 인도Nifty50(합성)", "해외주식"),
    "192090": ("TIGER 차이나CSI300", "해외주식"), "195930": ("TIGER 유로스탁스50(합성 H)", "해외주식"),
    "148070": ("KIWOOM 국고채10년", "채권"), "304660": ("KODEX 미국30년국채울트라선물(H)", "채권"),
    "261220": ("KODEX WTI원유선물(H)", "원자재"), "138910": ("KODEX 구리선물(H)", "원자재"),
    "144600": ("KODEX 은선물(H)", "원자재"),
})
UNIVERSES = {"현재 8종": BASE8, "8종(미국 장기이력)": LONG_US, "확장 17종": EXTENDED}


def prepare(codes, start="2017-01-02"):
    data = load_prices(sorted(codes), "2014-01-01", CACHE)
    dates = data["069500"].index
    arr = {c: {k: df.reindex(dates)[k].values.astype(float) for k in ("Open", "High", "Low", "Close")}
           for c, df in data.items()}
    return dates, arr, int(np.searchsorted(dates, pd.Timestamp(start)))


def simulate(dates, arr, start_i, assets, groups, k=1, one_per_group=False, ma=60, band=0.01,
             slope=5, short=60, long=120, margin=0.02, stop=0.05, alloc=0.5,
             risk_adj=False):
    need = max(ma + slope, long + 1)
    closes = {c: arr[c]["Close"] for c in assets}
    valid = {c: np.flatnonzero(~np.isnan(closes[c])) for c in assets}
    cash, pos = 1.0, {}      # code -> dict(units, entry, cost)
    curve = np.ones(len(dates))
    n_trades = 0
    for i in range(start_i, len(dates)):
        score, elig, prev_close = {}, set(), {}
        for c in assets:
            vi = valid[c]
            n = np.searchsorted(vi, i)
            if n < need:
                continue
            cl = closes[c][vi[:n]]
            last = cl[-1]
            prev_close[c] = last
            ma_now, ma_prev = cl[-ma:].mean(), cl[-slope - ma:-slope].mean()
            sc = 0.4 * (last / cl[-short - 1] - 1) + 0.6 * (last / cl[-long - 1] - 1)
            if risk_adj:
                # 변동성 큰 자산(원유·은 등)이 순위를 독식하지 않도록 위험 대비 모멘텀으로 순위를 매긴다.
                vol = np.std(np.diff(cl[-long - 1:]) / cl[-long - 1:-1]) * np.sqrt(252)
                sc = sc / vol if vol > 0 else 0.0
            score[c] = sc
            if last > ma_now * (1 + band) and ma_now > ma_prev and sc > 0:
                elig.add(c)
        # 보유 중인 자산에 교체 완충(margin) 가산점을 주고 상위 K 선택
        bonus = margin * (5 if risk_adj else 1)   # 위험조정 점수는 단위가 커서 완충도 키운다
        ranked = sorted(elig, key=lambda c: score[c] + (bonus if c in pos else 0), reverse=True)
        targets, used = [], set()
        for c in ranked:
            if one_per_group and groups[c] in used:
                continue
            targets.append(c)
            used.add(groups[c])
            if len(targets) == k:
                break
        exited = False
        for c in list(pos):
            o, lo = arr[c]["Open"][i], arr[c]["Low"][i]
            if np.isnan(o):
                continue
            px = None
            if c not in targets:
                px = o
            elif stop is not None:
                sp = pos[c]["entry"] * (1 - stop)
                px = o if o <= sp else sp if lo <= sp else None
            if px is not None:
                proceeds = pos[c]["units"] * px * (1 - ONE_WAY)
                gain = proceeds - pos[c]["cost"]
                if c not in TAX_EXEMPT and gain > 0:
                    proceeds -= gain * DIVIDEND_TAX
                cash += proceeds
                del pos[c]
                n_trades += 1
                exited = True
        if not exited:
            equity = cash + sum(p["units"] * prev_close.get(c, p["entry"]) for c, p in pos.items())
            for c in targets:
                if c in pos or len(pos) >= k:
                    continue
                o = arr[c]["Open"][i]
                if np.isnan(o) or o / prev_close[c] - 1 < -0.03:
                    continue
                invest = min(equity * alloc / k, cash)
                if invest <= 0:
                    continue
                pos[c] = {"units": invest / (o * (1 + ONE_WAY)), "entry": o, "cost": invest}
                cash -= invest
        value = cash
        for c, p in pos.items():
            px = arr[c]["Close"][i]
            if np.isnan(px):
                px = prev_close.get(c, p["entry"])
            value += p["units"] * px
        curve[i] = value
    return pd.Series(curve[start_i:], index=dates[start_i:]), n_trades


def stat(eq, a=None, b=None):
    e = eq[a:b]
    e = e / e.iloc[0]
    years = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return ((e.iloc[-1] ** (1 / years) - 1) * 100, (e / e.cummax() - 1).min() * 100,
            r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0)


def main():
    all_codes = set(BASE8) | set(EXTENDED)
    dates, arr, si = prepare(all_codes)
    for c in sorted(all_codes):
        first = dates[np.flatnonzero(~np.isnan(arr[c]["Close"]))[0]].date()
        print(c, (EXTENDED.get(c) or BASE8.get(c))[0], "데이터 시작", first)
    grid = list(itertools.product([40, 60, 90, 120], [(40, 80), (60, 120), (90, 180), (20, 120), (120, 240)]))
    rows = []
    for (un, uni), k, opg, ra in itertools.product(
            UNIVERSES.items(), (1, 2, 3), (False, True), (False, True)):
        if (k == 1 and opg) or (k > 1 and not opg):
            continue
        assets = list(uni)
        groups = {c: v[1] for c, v in uni.items()}
        res, yearly_all = [], []
        for ma, (s, l) in grid:
            eq, nt = simulate(dates, arr, si, assets, groups, k, opg, ma=ma, short=s, long=l, risk_adj=ra)
            a, b, c = stat(eq), stat(eq, "2017", "2021"), stat(eq, "2022", "2026")
            ye = eq.resample("YE").last()
            yr = ye.pct_change()
            yr.iloc[0] = ye.iloc[0] / eq.iloc[0] - 1
            yearly_all.append(yr.values * 100)
            res.append((a[0], a[1], a[2], b[0], c[0], nt, (yr > 0).sum()))
        r = np.array(res)
        base_eq, _ = simulate(dates, arr, si, assets, groups, k, opg, risk_adj=ra)
        ba = stat(base_eq)
        yr_med = np.median(np.array(yearly_all), axis=0)
        rows.append({
            "자산": un, "보유수": k, "그룹당1개": "예" if opg else "아니오", "위험조정": "예" if ra else "아니오",
            "연수익(중간)": round(np.median(r[:, 0]), 1), "낙폭(중간)": round(np.median(r[:, 1]), 1),
            "낙폭(최악)": round(r[:, 1].min(), 1), "샤프(중간)": round(np.median(r[:, 2]), 2),
            "앞기간": round(np.median(r[:, 3]), 1), "뒤기간": round(np.median(r[:, 4]), 1),
            "플러스 해(10년중)": round(np.median(r[:, 6]), 1),
            "최고의 해 %": round(yr_med.max(), 0), "2018~24 합 %": round(yr_med[1:8].sum(), 0),
            "현재설정 연수익/낙폭": f"{ba[0]:.1f}/{ba[1]:.1f}",
        })
        print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(CACHE, "etf_diversify.csv"), index=False)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()

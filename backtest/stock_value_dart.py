"""DART 10년 재무로 개별주 가치·우량 전략 검증 (2017~2021로 보고 2022~2026으로 확인).

- 회계연도 실적은 다음 해 4월 첫 거래일부터 사용(공시 시차)
- PBR=시총/자본총계, PER=시총/당기순이익, ROE=순이익/자본, 부채비율=부채/자본 (연결 우선)
- 시총은 현재 시총×(과거 종가/현재 종가)로 추정(증자·감자 무시)
- 월 1회 리밸런싱, 동일가중, 매수 0.265%·매도 0.465%
- 한계: 현재 상장 종목만 있어 생존편향이 있다(저PBR 부실주가 빠져 가치 전략에 유리).
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from full_backtest import load_stocks, CACHE  # noqa: E402
from etf_rotation_backtest import load_prices  # noqa: E402
from stock_search import run, stats, IS, OOS  # noqa: E402


def load_dart():
    df = pd.read_csv(os.path.join(CACHE, "dart_annual.csv"), dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    df["prio"] = (df["fs"] == "CFS").astype(int)
    df = df.sort_values("prio").drop_duplicates(["code", "year", "account"], keep="last")
    return df


def main():
    etf = load_prices(["069500"], "2014-01-01", CACHE)
    dates = etf["069500"].index
    st = load_stocks(dates)
    close, volume, cap = st["close"], st["volume"], st["cap"]
    dart = load_dart()
    print("DART 종목", dart.code.nunique(), "연도별 종목 수:",
          dart[dart.account == "자본총계"].groupby("year").code.nunique().to_dict())
    ret = close.pct_change(fill_method=None).fillna(0).values

    def panel(account):
        wide = dart[dart.account == account].pivot_table(index="year", columns="code", values="value")
        out = pd.DataFrame(np.nan, index=dates, columns=close.columns)
        for (y, m), idx in pd.Series(dates, index=dates).groupby([dates.year, dates.month]):
            fy = y - 1 if m >= 4 else y - 2
            if fy in wide.index:
                out.loc[idx.index] = wide.loc[fy].reindex(close.columns).values
        return out

    equity, ni, op, debt, sales = (panel(a) for a in ("자본총계", "당기순이익", "영업이익", "부채총계", "매출액"))
    pbr = cap / equity.where(equity > 0)
    per = cap / ni.where(ni > 0)
    roe = ni / equity.where(equity > 0)
    debt_ratio = debt / equity.where(equity > 0)
    rank = lambda x: x.rank(axis=1, pct=True)
    profitable = (op > 0) & (ni > 0)
    factors = {
        "저PBR": -pbr, "저PER": -per, "고ROE": roe,
        "저PBR+고ROE": rank(-pbr) + rank(roe),
        "저PER+저PBR+고ROE": rank(-per) + rank(-pbr) + rank(roe),
        "저PBR(흑자·부채 200% 이하)": (-pbr).where(profitable & (debt_ratio <= 2)),
        "저PER(부채 200% 이하)": (-per).where(profitable & (debt_ratio <= 2)),
        "저PSR": -(cap / sales.where(sales > 0)),
    }
    liquid = ((close * volume).rolling(20).mean() >= 1e9) & close.notna()
    universes = {"시총 2천억 이상": cap >= 2e11, "시총 5천억~5조": (cap >= 5e11) & (cap <= 5e12),
                 "시총 1조 이상": cap >= 1e12}
    ds = pd.Series(dates, index=dates)
    rebal = np.flatnonzero(dates.isin(ds.groupby([dates.year, dates.month]).last().values) & (dates >= "2016-12-01"))
    k = etf["069500"]["Close"].reindex(dates)
    gates = {"없음": np.ones(len(dates)), "K200>120일선": (k > k.rolling(120).mean()).values.astype(float)}
    is_mask = (dates >= IS[0]) & (dates <= IS[1])
    oos_mask = (dates >= OOS[0]) & (dates <= OOS[1])
    full = dates >= IS[0]
    rows, series = [], {}
    for fn, f in factors.items():
        for un, u in universes.items():
            score = f.where(u & liquid).values
            for gn, g in gates.items():
                for n in (10, 20):
                    net, turn = run(score, ret, rebal, g, n)
                    a, b, c = stats(net, full), stats(net, is_mask), stats(net, oos_mask)
                    rows.append({"팩터": fn, "유니버스": un, "시장필터": gn, "N": n,
                                 "연수익": a[0], "낙폭": a[1], "샤프": a[2],
                                 "앞 연수익": b[0], "앞 낙폭": b[1], "뒤 연수익": c[0], "뒤 낙폭": c[1]})
                    series[(fn, un, gn, n)] = net
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(CACHE, "stock_value_dart.csv"), index=False)
    pd.set_option("display.width", 230)
    pd.set_option("display.max_rows", 200)
    print(out.sort_values("샤프", ascending=False).head(30).to_string(index=False))
    print("\n두 기간 모두 연 10% 이상:", int(((out["앞 연수익"] >= 10) & (out["뒤 연수익"] >= 10)).sum()), "/", len(out))
    ok = out[(out["앞 연수익"] >= 10) & (out["뒤 연수익"] >= 10) & (out["앞 낙폭"] >= -30) & (out["뒤 낙폭"] >= -30)]
    print("그중 두 기간 낙폭 -30% 이내:", len(ok))
    print(ok.sort_values("샤프", ascending=False).to_string(index=False))
    print("\n팩터별 중간값")
    print(out.groupby("팩터")[["연수익", "낙폭", "앞 연수익", "뒤 연수익"]].median().round(1).to_string())
    print(out.groupby("유니버스")[["연수익", "낙폭", "앞 연수익", "뒤 연수익"]].median().round(1).to_string())
    print(out.groupby("시장필터")[["연수익", "낙폭", "앞 연수익", "뒤 연수익"]].median().round(1).to_string())
    kr = k.pct_change().fillna(0).values
    print("\nKODEX 200:", stats(kr, full), stats(kr, is_mask), stats(kr, oos_mask))
    for un, u in universes.items():
        eqw = close.pct_change(fill_method=None).where((u & liquid).shift(1)).mean(axis=1).fillna(0).values
        print(f"{un} 동일가중:", stats(eqw, full), stats(eqw, is_mask), stats(eqw, oos_mask))
    best = out.sort_values("샤프", ascending=False).iloc[0]
    key = (best["팩터"], best["유니버스"], best["시장필터"], best["N"])
    eq = pd.Series(np.cumprod(1 + series[key][full]), index=dates[full])
    ye = eq.resample("YE").last()
    yr = ye.pct_change()
    yr.iloc[0] = ye.iloc[0] - 1
    print("\n샤프 1위", key, "연도별(%):", {d.year: round(v * 100, 1) for d, v in yr.items()})


if __name__ == "__main__":
    main()

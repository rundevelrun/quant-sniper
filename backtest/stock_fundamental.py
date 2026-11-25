"""개별주 재무(가치·우량) 팩터 백테스트.

데이터: 네이버 증권 연간 재무(최근 3개 회계연도만 제공) → 검증 가능 기간이 2024-04 이후뿐이다.
회계연도 실적은 다음 해 4월 첫 거래일부터 사용한다(공시 시차). 월 1회 리밸런싱, 동일가중.
한계: 기간이 2.6년으로 짧아 통계적 근거가 약하고, 현재 상장 종목만 있어 생존편향이 있다.
"""
import concurrent.futures as cf
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from full_backtest import load_stocks, CACHE  # noqa: E402
from etf_rotation_backtest import load_prices  # noqa: E402
from stock_search import run, stats  # noqa: E402

FIN = os.path.join(CACHE, "finance_annual.csv")
ROWS = ["영업이익", "ROE", "부채비율", "EPS", "BPS", "주당배당금"]


def fetch_one(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        info = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("financeInfo") or {}
        out = []
        for row in info.get("rowList", []):
            if row.get("title") not in ROWS:
                continue
            for period, cell in (row.get("columns") or {}).items():
                value = str((cell or {}).get("value", "")).replace(",", "")
                try:
                    out.append((code, row["title"], int(period[:4]), float(value)))
                except ValueError:
                    pass
        return out
    except Exception:
        return []


def load_finance(codes):
    if os.path.exists(FIN):
        return pd.read_csv(FIN, dtype={"code": str})
    rows = []
    with cf.ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(fetch_one, codes)):
            rows += r
            if i % 200 == 0:
                print("재무", i, len(rows), flush=True)
    df = pd.DataFrame(rows, columns=["code", "item", "year", "value"])
    df.to_csv(FIN, index=False)
    return df


def main():
    etf = load_prices(["069500"], "2014-01-01", CACHE)
    dates = etf["069500"].index
    st = load_stocks(dates)
    close, volume, cap = st["close"], st["volume"], st["cap"]
    fin = load_finance(list(close.columns))
    print("재무 데이터 종목 수", fin.code.nunique(), "연도", sorted(fin.year.unique()))
    ret = close.pct_change(fill_method=None).fillna(0).values
    start = pd.Timestamp("2024-04-01")

    def panel(item):
        wide = fin[fin.item == item].pivot_table(index="year", columns="code", values="value")
        out = pd.DataFrame(np.nan, index=dates, columns=close.columns)
        for d in dates[dates >= start]:
            fy = d.year - 1 if d.month >= 4 else d.year - 2      # 4월부터 전년도 실적 사용
            if fy in wide.index:
                out.loc[d] = wide.loc[fy].reindex(close.columns)
        return out

    eps, bps, roe, debt, dps, op = (panel(x) for x in ("EPS", "BPS", "ROE", "부채비율", "주당배당금", "영업이익"))
    pbr = close / bps.where(bps > 0)
    per = close / eps.where(eps > 0)
    rank = lambda x: x.rank(axis=1, pct=True)
    quality_ok = (op > 0) & (eps > 0)
    factors = {
        "저PBR": -pbr, "저PER": -per, "고ROE": roe, "고배당수익률": (dps / close).where(dps > 0),
        "저PBR+고ROE": rank(-pbr) + rank(roe),
        "저PER+저PBR+고ROE": rank(-per) + rank(-pbr) + rank(roe),
        "저PBR(흑자·부채 200% 이하)": (-pbr).where(quality_ok & (debt <= 200)),
        "저PER+고배당(흑자)": (rank(-per) + rank((dps / close).where(dps > 0))).where(quality_ok),
    }
    liquid = ((close * volume).rolling(20).mean() >= 1e9) & close.notna()
    universes = {"시총 2천억 이상": cap >= 2e11, "시총 5천억~5조": (cap >= 5e11) & (cap <= 5e12), "시총 1조 이상": cap >= 1e12}
    ds = pd.Series(dates, index=dates)
    rebal = np.flatnonzero(dates.isin(ds.groupby([dates.year, dates.month]).last().values) & (dates >= start))
    gate = np.ones(len(dates))
    mask = dates >= "2024-05-01"
    m1 = (dates >= "2024-05-01") & (dates <= "2025-08-31")
    m2 = dates >= "2025-09-01"
    rows = []
    for (fn, f), (un, u), n in ((a, b, c) for a in factors.items() for b in universes.items() for c in (10, 20)):
        net, turn = run(f.where(u & liquid).values, ret, rebal, gate, n)
        a, b, c = stats(net, mask), stats(net, m1), stats(net, m2)
        rows.append({"팩터": fn, "유니버스": un, "N": n, "연수익": a[0], "낙폭": a[1], "샤프": a[2],
                     "전반 연수익": b[0], "후반 연수익": c[0], "후반 낙폭": c[1]})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(CACHE, "stock_fundamental.csv"), index=False)
    pd.set_option("display.width", 220)
    print(out.sort_values("샤프", ascending=False).to_string(index=False))
    k = etf["069500"]["Close"].reindex(dates).pct_change().fillna(0).values
    print("KODEX 200 보유:", stats(k, mask), stats(k, m1), stats(k, m2))
    for un, u in universes.items():
        eqw = close.pct_change(fill_method=None).where((u & liquid).shift(1)).mean(axis=1).fillna(0).values
        print(f"{un} 동일가중:", stats(eqw, mask), stats(eqw, m1), stats(eqw, m2))


if __name__ == "__main__":
    main()

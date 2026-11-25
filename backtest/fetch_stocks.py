"""네이버 증권 종목 목록(현재 시가총액) + 일봉 캐시. 상장폐지 종목은 포함되지 않는다."""
import concurrent.futures as cf
import json
import os
import sys
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache", "stocks")
LISTING = os.path.join(ROOT, "backtest", ".cache", "listing.csv")


def fetch_listing():
    rows = []
    for market, sosok in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
        page = 1
        while True:
            url = (f"https://m.stock.naver.com/api/stocks/marketValue/{sosok}"
                   f"?page={page}&pageSize=100")
            data = json.loads(urllib.request.urlopen(url, timeout=20).read())
            stocks = data.get("stocks", [])
            if not stocks:
                break
            for s in stocks:
                rows.append({
                    "code": s["itemCode"], "name": s["stockName"], "market": market,
                    "end_type": s.get("stockEndType"),
                    "close_now": float(s["closePrice"].replace(",", "")),
                    "cap_now": float(s["marketValue"].replace(",", "")) * 1e8,
                })
            page += 1
    df = pd.DataFrame(rows).drop_duplicates("code")
    df.to_csv(LISTING, index=False)
    return df


def fetch_one(code, start):
    import FinanceDataReader as fdr
    path = os.path.join(CACHE, f"{code}.csv")
    if os.path.exists(path):
        return code, True
    try:
        df = fdr.DataReader(code, start)
        if len(df):
            df[["Open", "High", "Low", "Close", "Volume"]].to_csv(path)
        return code, True
    except Exception:
        return code, False


def main(start="2015-06-01", min_cap=1e11, max_cap=2e13):
    os.makedirs(CACHE, exist_ok=True)
    listing = pd.read_csv(LISTING, dtype={"code": str}) if os.path.exists(LISTING) else fetch_listing()
    name = listing["name"].astype(str)
    mask = (
        (listing["end_type"] == "stock")
        & listing["cap_now"].between(min_cap, max_cap)
        & ~name.str.contains("스팩|ETN|리츠")
        & ~name.str.endswith("우") & ~name.str.contains("우B|우C")
    )
    codes = listing.loc[mask, "code"].tolist()
    print("대상", len(codes), flush=True)
    ok = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for i, (code, success) in enumerate(ex.map(lambda c: fetch_one(c, start), codes)):
            ok += success
            if i % 200 == 0:
                print(i, ok, flush=True)
    print("완료", ok, flush=True)


if __name__ == "__main__":
    main()

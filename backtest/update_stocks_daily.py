"""코스피·코스닥 전 종목 일봉 수집/갱신(네이버, 병렬). 결과: backtest/.cache/stocks_all/{code}.csv

처음 실행: 종목별 최대 약 3,000거래일(2014년~) 수집.
이후 실행: 기존 파일 마지막 날짜 이후만 덧붙임(최근 30거래일을 다시 받아 병합).
종목 목록: backtest/.cache/stocks_all/listing.csv (실행 때마다 갱신, 상장폐지 종목 파일은 남겨 둠).
"""
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backtest", ".cache", "stocks_all")
CHART = "https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={n}&requestType=0"


def listing():
    rows = []
    for market in ("KOSPI", "KOSDAQ"):
        page = 1
        while True:
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
            data = json.loads(urllib.request.urlopen(url, timeout=20).read())
            items = data.get("stocks", [])
            if not items:
                break
            rows += [{"code": s["itemCode"], "name": s["stockName"], "market": market,
                      "end_type": s.get("stockEndType")} for s in items]
            page += 1
    df = pd.DataFrame(rows).drop_duplicates("code")
    df.to_csv(os.path.join(OUT, "listing.csv"), index=False)
    return df


def fetch(code, n):
    xml = urllib.request.urlopen(CHART.format(code=code, n=n), timeout=20).read().decode("euc-kr", "ignore")
    rows = []
    for item in re.findall(r'data="([^"]+)"', xml):
        d, o, h, l, c, v = item.split("|")
        rows.append((pd.Timestamp(d), float(o), float(h), float(l), float(c), float(v)))
    return pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")


def update(code):
    path = os.path.join(OUT, f"{code}.csv")
    try:
        if os.path.exists(path):
            old = pd.read_csv(path, index_col=0, parse_dates=True)
            new = fetch(code, 30)
            df = pd.concat([old[old.index < new.index.min()], new]) if len(new) else old
        else:
            df = fetch(code, 3000)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if len(df):
            df.to_csv(path)
        return len(df)
    except Exception:
        return -1


def main():
    os.makedirs(OUT, exist_ok=True)
    codes = listing()
    codes = codes[codes["end_type"].fillna("stock") == "stock"]["code"].tolist()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(update, codes))
    print(f"종목 {len(codes)}개, 성공 {sum(r > 0 for r in res)}, 실패 {sum(r < 0 for r in res)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

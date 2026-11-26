"""네이버 차트 API로 ETF 일봉을 받아 backtest/.cache/{code}.csv로 저장한다(최대 약 3,000거래일).

사용: python3 backtest/fetch_naver_etf.py 102110 367380 ...
가격은 분배금 미반영 원시 종가다.
"""
import os
import re
import sys
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
URL = "https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=3000&requestType=0"


def fetch(code):
    xml = urllib.request.urlopen(URL.format(code=code), timeout=20).read().decode("euc-kr", "ignore")
    rows = []
    for item in re.findall(r'data="([^"]+)"', xml):
        d, o, h, l, c, v = item.split("|")
        rows.append((pd.Timestamp(d), float(o), float(h), float(l), float(c), float(v)))
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    return df[df["Close"] > 0]


def main(codes):
    os.makedirs(CACHE, exist_ok=True)
    for code in codes:
        df = fetch(code)
        df.to_csv(os.path.join(CACHE, f"{code}.csv"))
        print(code, df.index[0].date(), df.index[-1].date(), len(df))


if __name__ == "__main__":
    main(sys.argv[1:])

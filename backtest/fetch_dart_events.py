"""DART 공시 목록에서 이벤트(자기주식 취득 결정, 단일판매·공급계약 체결)를 수집한다.

키는 DART_API_KEY 또는 DART_KEY_FILE. 결과: backtest/.cache/dart_events.csv
(stock_code, date, kind, report_nm). 정정 공시와 신탁계약은 제외한다.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
KINDS = {
    "B": [("buyback", "자기주식취득결정")],
    "I": [("supply", "단일판매ㆍ공급계약체결"), ("supply", "단일판매·공급계약체결")],
}
# 거래소공시(I)는 분기당 2만 건이 넘어 일일 호출 한도(2만 회)를 고려해 기간을 줄인다.
TYPE_START = {"B": "2016-01-01", "I": "2020-01-01"}


def api_key():
    key = os.environ.get("DART_API_KEY")
    if not key and os.environ.get("DART_KEY_FILE"):
        key = open(os.environ["DART_KEY_FILE"]).read().strip()
    if not key:
        sys.exit("DART_API_KEY 또는 DART_KEY_FILE이 필요합니다.")
    return key


def get(params):
    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=30).read())
        except Exception:
            time.sleep(2 + attempt * 3)
    return {}


def windows(start, end, days=88):
    cur = start
    while cur <= end:
        nxt = min(cur + dt.timedelta(days=days), end)
        yield cur, nxt
        cur = nxt + dt.timedelta(days=1)


def collect(items, targets):
    rows = []
    for item in items:
        name = item.get("report_nm", "").replace(" ", "")
        if "정정" in name or "신탁" in name or not item.get("stock_code"):
            continue
        for kind, needle in targets:
            if needle in name:
                rows.append({"stock_code": item["stock_code"], "date": item["rcept_dt"],
                             "kind": kind, "report_nm": item.get("report_nm")})
                break
    return rows


def main(end=None, workers=6):
    from concurrent.futures import ThreadPoolExecutor
    key = api_key()
    end = dt.date.fromisoformat(end) if end else dt.date.today()
    rows = []
    with ThreadPoolExecutor(workers) as pool:
        for ty, targets in KINDS.items():
            for a, b in windows(dt.date.fromisoformat(TYPE_START[ty]), end):
                base = {"crtfc_key": key, "bgn_de": a.strftime("%Y%m%d"),
                        "end_de": b.strftime("%Y%m%d"), "pblntf_ty": ty, "page_count": 100}
                first = get({**base, "page_no": 1})
                if first.get("status") != "000":
                    print(ty, a, b, "status", first.get("status"), first.get("message"), flush=True)
                    continue
                rows += collect(first.get("list", []), targets)
                pages = range(2, int(first.get("total_page", 1)) + 1)
                for data in pool.map(lambda n: get({**base, "page_no": n}), pages):
                    rows += collect(data.get("list", []), targets)
                print(ty, a, b, len(rows), flush=True)
    pd.DataFrame(rows).drop_duplicates().to_csv(os.path.join(CACHE, "dart_events.csv"), index=False)


if __name__ == "__main__":
    main()

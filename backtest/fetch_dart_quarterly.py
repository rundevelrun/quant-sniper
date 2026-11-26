"""DART 분기·반기·연간 보고서의 주요계정(영업이익·당기순이익)을 다중회사 API로 수집한다.

키는 환경변수 DART_API_KEY 또는 DART_KEY_FILE에서 읽는다(저장소에 두지 않음).
대상: backtest/.cache/stocks 에 일봉이 있는 종목. 결과: backtest/.cache/dart_quarterly.csv
접수번호(rcept_no) 앞 8자리가 공시 접수일이다.
"""
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
REPORTS = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
ACCOUNTS = {"영업이익", "당기순이익", "매출액"}


def api_key():
    key = os.environ.get("DART_API_KEY")
    if not key and os.environ.get("DART_KEY_FILE"):
        key = open(os.environ["DART_KEY_FILE"]).read().strip()
    if not key:
        sys.exit("DART_API_KEY 또는 DART_KEY_FILE이 필요합니다.")
    return key


def corp_codes(key, stock_codes):
    raw = urllib.request.urlopen(
        f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}", timeout=60
    ).read()
    xml = zipfile.ZipFile(io.BytesIO(raw)).read("CORPCODE.xml")
    out = {}
    for node in ET.fromstring(xml).iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        if stock in stock_codes:
            out[stock] = node.findtext("corp_code").strip()
    return out


def fetch_batch(key, corps, year, reprt):
    query = urllib.parse.urlencode({
        "crtfc_key": key, "corp_code": ",".join(corps),
        "bsns_year": str(year), "reprt_code": reprt,
    })
    for attempt in range(3):
        try:
            data = json.loads(urllib.request.urlopen(
                f"https://opendart.fss.or.kr/api/fnlttMultiAcnt.json?{query}", timeout=30
            ).read())
            break
        except Exception:
            time.sleep(2 + attempt * 3)
    else:
        return []
    if data.get("status") not in ("000", "013"):  # 013 = 데이터 없음
        print("DART", year, reprt, data.get("status"), data.get("message"), flush=True)
        return []
    return [row for row in data.get("list", []) if row.get("account_nm") in ACCOUNTS]


def number(text):
    text = (text or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def main(start_year=2015, end_year=2026):
    key = api_key()
    stocks = {f[:-4] for f in os.listdir(os.path.join(CACHE, "stocks")) if f.endswith(".csv")}
    mapping = corp_codes(key, stocks)
    corps = sorted(mapping.values())
    print("대상 기업", len(corps), flush=True)
    rows = []
    for year in range(start_year, end_year + 1):
        for reprt, label in REPORTS.items():
            for i in range(0, len(corps), 100):
                for row in fetch_batch(key, corps[i:i + 100], year, reprt):
                    rows.append({
                        "stock_code": row.get("stock_code"), "year": year, "report": label,
                        "fs_div": row.get("fs_div"), "account": row.get("account_nm"),
                        "this": number(row.get("thstrm_amount")),
                        "prev": number(row.get("frmtrm_amount")),
                        "rcept_date": (row.get("rcept_no") or "")[:8],
                    })
                time.sleep(0.15)
            print(year, label, len(rows), flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(CACHE, "dart_quarterly.csv"), index=False)


if __name__ == "__main__":
    main()

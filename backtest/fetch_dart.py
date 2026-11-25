"""DART 연간 재무(주요계정) 수집. 키는 환경변수 DART_API_KEY 또는 DART_KEY_FILE 경로에서 읽는다.

키를 코드·저장소에 넣지 않는다. 결과: backtest/.cache/dart_annual.csv
"""
import io
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
OUT = os.path.join(CACHE, "dart_annual.csv")
BASE = "https://opendart.fss.or.kr/api"
ACCOUNTS = {"자산총계", "부채총계", "자본총계", "매출액", "영업이익", "당기순이익", "당기순이익(손실)"}


def api_key():
    key = os.environ.get("DART_API_KEY")
    if not key and os.environ.get("DART_KEY_FILE"):
        key = open(os.environ["DART_KEY_FILE"]).read().strip()
    if not key:
        sys.exit("DART_API_KEY 또는 DART_KEY_FILE이 필요합니다.")
    return key


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def corp_codes(key):
    # 이 파일은 내려받는 데 몇 분 걸릴 수 있다. 미리 받아둔 zip이 있으면 DART_CORP_ZIP으로 지정한다.
    local = os.environ.get("DART_CORP_ZIP")
    raw = open(local, "rb").read() if local else get(f"{BASE}/corpCode.xml?crtfc_key={key}", 1500)
    z = zipfile.ZipFile(io.BytesIO(raw))
    root = ET.fromstring(z.read(z.namelist()[0]))
    return {
        (el.findtext("stock_code") or "").strip(): el.findtext("corp_code")
        for el in root.iter("list") if (el.findtext("stock_code") or "").strip()
    }


def number(text):
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def main(first_year=2015, last_year=2025, chunk_size=20, workers=12):
    """DART는 연결당 전송 속도가 느려서(초당 약 15KB) 작은 묶음을 여러 연결로 나눠 받는다."""
    import concurrent.futures as cf
    key = api_key()
    stocks = [f[:-4] for f in os.listdir(os.path.join(CACHE, "stocks"))]
    mapping = corp_codes(key)
    corp_list = [mapping[c] for c in stocks if c in mapping]
    print("대상", len(corp_list), "/", len(stocks), flush=True)
    jobs = [(year, corp_list[i:i + chunk_size])
            for year in range(first_year, last_year + 1)
            for i in range(0, len(corp_list), chunk_size)]

    def work(job):
        year, chunk = job
        url = (f"{BASE}/fnlttMultiAcnt.json?crtfc_key={key}&corp_code={','.join(chunk)}"
               f"&bsns_year={year}&reprt_code=11011")
        for _ in range(3):
            try:
                data = json.loads(get(url, 120))
                break
            except Exception:
                time.sleep(2)
        else:
            return year, None, []
        return year, data.get("status"), [
            (r["stock_code"], year, r["fs_div"], r["account_nm"].replace("(손실)", ""),
             number(r.get("thstrm_amount")))
            for r in data.get("list", []) if r.get("account_nm") in ACCOUNTS
        ]

    rows, failed = [], 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for n, (year, status, part) in enumerate(ex.map(work, jobs), 1):
            rows += part
            if status not in ("000", "013"):
                failed += 1
            if n % 50 == 0:
                print(f"{n}/{len(jobs)} 묶음, 행 {len(rows)}, 실패 {failed}", flush=True)
    df = pd.DataFrame(rows, columns=["code", "year", "fs", "account", "value"]).dropna()
    df.to_csv(OUT, index=False)
    print("완료", len(df), df.code.nunique(), "실패 묶음", failed)


if __name__ == "__main__":
    main()

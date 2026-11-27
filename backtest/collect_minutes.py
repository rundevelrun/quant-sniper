"""국내 주요 ETF 1분봉 수집(ISA 앱키, 조회 전용). 결과: backtest/.cache/minutes/{code}.csv"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kiwoom_research import ResearchClient  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backtest", ".cache", "minutes")
CODES = {"122630": "KODEX 레버리지", "252670": "KODEX 200선물인버스2X", "069500": "KODEX 200",
         "229200": "KODEX 코스닥150", "233740": "KODEX 코스닥150레버리지", "251340": "KODEX 코스닥150선물인버스",
         "114800": "KODEX 인버스"}


def num(v):
    return abs(float(str(v).replace("+", "").replace(",", "") or 0))


def symbols():
    """수집 대상: minutes/symbols.txt(없으면 기본 7종)."""
    path = os.path.join(OUT, "symbols.txt")
    if os.path.exists(path):
        return [s.strip() for s in open(path) if s.strip()]
    return list(CODES)


def main(base_dt):
    os.makedirs(OUT, exist_ok=True)
    c = ResearchClient()
    for code in symbols():
        name = CODES.get(code, code)
        if os.path.exists(os.path.join(OUT, f"{code}.csv")):
            continue  # 이미 받은 종목은 일일 갱신에서 이어 붙인다
        try:
            rows = c.paged("/api/dostk/chart", "ka10080",
                       {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": base_dt},
                           "stk_min_pole_chart_qry", max_pages=400)
        except Exception as exc:
            print(code, "실패", exc, flush=True)
            continue
        if not rows:
            continue
        df = pd.DataFrame({"time": pd.to_datetime([r["cntr_tm"] for r in rows], format="%Y%m%d%H%M%S"),
                           "open": [num(r["open_pric"]) for r in rows], "high": [num(r["high_pric"]) for r in rows],
                           "low": [num(r["low_pric"]) for r in rows], "close": [num(r["cur_prc"]) for r in rows],
                           "volume": [num(r["trde_qty"]) for r in rows]})
        df = df.drop_duplicates("time").sort_values("time")
        df.to_csv(os.path.join(OUT, f"{code}.csv"), index=False)
        print(code, name, len(df), df["time"].min(), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else pd.Timestamp.today().strftime("%Y%m%d"))

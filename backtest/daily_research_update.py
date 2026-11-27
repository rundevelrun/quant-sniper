"""매일 장 마감 후 연구 데이터 증분 갱신(ISA 앱키 조회 전용 + 공개 데이터).

1) 시장 투자자별 순매수(ka10051): 없는 날짜만 추가
2) 프로그램 매매 일자별(ka90010): 최근 1페이지를 받아 병합
3) 주요 ETF 1분봉(ka10080): 최근 몇 페이지를 받아 병합(키움 보관 1년 한계를 넘어 누적)
4) VIX(야후), 미국 금리(FRED): 전체 재수신(작음)
"""
import json
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_flows as cf  # noqa: E402
import collect_minutes as cm  # noqa: E402
from kiwoom_research import ResearchClient  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "backtest", ".cache")


def merge_csv(path, new, key):
    if os.path.exists(path):
        old = pd.read_csv(path, dtype={key: str} if key == "date" else None,
                          parse_dates=[key] if key == "time" else None)
        new = pd.concat([old, new])
    new = new.drop_duplicates(key, keep="last").sort_values(key)
    new.to_csv(path, index=False)
    return len(new)


def main():
    client = ResearchClient()
    today = pd.Timestamp.today().strftime("%Y%m%d")
    k = pd.read_csv(os.path.join(C, "069500.csv"), index_col=0, parse_dates=True)
    recent = [d.strftime("%Y%m%d") for d in k.index[-10:]]
    if today not in recent:
        recent.append(today)
    for name, (pcode, mcode, total) in cf.MARKETS.items():
        # 최근 날짜는 장중에 받은 중간값일 수 있으므로 지우고 다시 받는다.
        ipath = os.path.join(C, "flows", f"investor_{name}.csv")
        if os.path.exists(ipath):
            inv = pd.read_csv(ipath, dtype={"date": str})
            inv[~inv["date"].isin(recent)].to_csv(ipath, index=False)
        cf.investor(client, name, mcode, total, recent)
        rows = client.paged("/api/dostk/mrkcond", "ka90010",
                            {"date": today, "amt_qty_tp": "1", "mrkt_tp": pcode, "min_tic_tp": "1", "stex_tp": "1"},
                            "prm_trde_trnsn", max_pages=1)
        new = pd.DataFrame([{"date": r["cntr_tm"][:8], "arb_net": cf.num(r.get("dfrt_trde_netprps")),
                             "nonarb_net": cf.num(r.get("ndiffpro_trde_netprps")),
                             "all_net": cf.num(r.get("all_netprps"))} for r in rows])
        n = merge_csv(os.path.join(C, "flows", f"program_{name}.csv"), new, "date")
        print(name, "program rows", n, flush=True)
    for code in cm.CODES:
        rows = client.paged("/api/dostk/chart", "ka10080",
                            {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today},
                            "stk_min_pole_chart_qry", max_pages=8)
        new = pd.DataFrame({"time": pd.to_datetime([r["cntr_tm"] for r in rows], format="%Y%m%d%H%M%S"),
                            "open": [cm.num(r["open_pric"]) for r in rows], "high": [cm.num(r["high_pric"]) for r in rows],
                            "low": [cm.num(r["low_pric"]) for r in rows], "close": [cm.num(r["cur_prc"]) for r in rows],
                            "volume": [cm.num(r["trde_qty"]) for r in rows]})
        n = merge_csv(os.path.join(C, "minutes", f"{code}.csv"), new, "time")
        print(code, "minute rows", n, flush=True)
    for sid in ("DGS10", "DGS2", "T10Y2Y", "DTWEXBGS"):
        try:
            data = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30).read()
            open(os.path.join(C, "macro", f"{sid}.csv"), "wb").write(data)
        except Exception as exc:
            print("FRED 실패", sid, exc, flush=True)
    try:
        req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?period1=946684800&period2=1900000000&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
        json.loads(data)  # 형식 확인 후 저장
        open(os.path.join(C, "macro", "VIX.json"), "wb").write(data)
    except Exception as exc:
        print("VIX 실패", exc, flush=True)
    print("done", today, flush=True)


if __name__ == "__main__":
    main()

import os
import time
import json
import urllib.request
import urllib.parse

def _number(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return default

def _market_name(value):
    """키움의 한글/영문 시장명을 전략용 KOSPI/KOSDAQ 값으로 정규화한다."""
    text = str(value or "").strip().upper()
    if "코스닥" in text or "KOSDAQ" in text:
        return "KOSDAQ"
    if "거래소" in text or "KOSPI" in text:
        return "KOSPI"
    return "미분류"

class KiwoomRestAPI:
    """
    키움증권 최신 공식 REST API 클라이언트
    - REST 구간은 파이썬 표준 라이브러리(urllib, json) 사용
    - 지원 환경: Linux, macOS, Windows 완벽 호환
    - 엔드포인트:
      * 토큰 발급: POST /oauth2/token (au10001)
      * 주식 주문: POST /api/dostk/ordr (kt10000: 매수, kt10001: 매도)
      * 예수금 조회: POST /api/dostk/acnt (kt00001)
    """
    def __init__(self, app_key=None, app_secret=None, account_no=None):
        from config import LIVE_TRADING_ENABLED
        self.app_key = (os.getenv("KIWOOM_APP_KEY", "") if app_key is None else app_key).strip()
        self.app_secret = (os.getenv("KIWOOM_APP_SECRET", "") if app_secret is None else app_secret).strip()
        self.account_no = (os.getenv("KIWOOM_ACCOUNT_NO", "") if account_no is None else account_no).strip()
        self.live_trading_enabled = LIVE_TRADING_ENABLED
        self.base_url = "https://api.kiwoom.com"
        self.token = None
        self.token_expiry = 0
        self.last_universe_metadata = {}
        self.cash_balance_cache = None
        self.cash_balance_cache_at = 0.0
        self.cash_balance_cache_ttl = 60.0

        missing = [name for name, value in (
            ("KIWOOM_APP_KEY", self.app_key),
            ("KIWOOM_APP_SECRET", self.app_secret),
            ("KIWOOM_ACCOUNT_NO", self.account_no),
        ) if not value or value.startswith("your_")]
        if missing:
            raise RuntimeError(f"실전 필수 설정 누락: {', '.join(missing)}")
        if not self.live_trading_enabled:
            raise RuntimeError("실전 주문이 잠겨 있습니다. LIVE_TRADING_ENABLED=true가 필요합니다.")

    def get_token(self):
        """OAuth 2.0 접근 토큰 발급 (au10001) 및 캐싱"""
        if self.token and time.time() < self.token_expiry - 60:
            return self.token

        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
                self.token = data.get("token")
                # 기본 만료시간 (보통 86400초/24시간)
                expires_in = int(data.get("expires_in", 86400))
                self.token_expiry = time.time() + expires_in
                print(f"[AUTH] 키움 REST API 접근 토큰 발급 성공 (만료: {expires_in}초)")
                return self.token
        except Exception as e:
            print(f"[ERR] 토큰 발급 실패: {e}")
            return None

    def get_cash_balance(self, force_refresh=False):
        """계좌 예수금 조회. 반복 감시 중에는 캐시로 kt00001 호출을 제한한다."""
        now = time.time()
        if (
            not force_refresh
            and self.cash_balance_cache is not None
            and now - self.cash_balance_cache_at < self.cash_balance_cache_ttl
        ):
            return self.cash_balance_cache
        token = self.get_token()
        if not token:
            return 0.0

        url = f"{self.base_url}/api/dostk/acnt"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "kt00001"
        }
        body = {"qry_tp": "3"}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
                balance = _number(data.get("ord_alow_amt", data.get("entr", 0.0)))
                self.cash_balance_cache = balance
                self.cash_balance_cache_at = now
                return balance
        except Exception as e:
            print(f"[ERR] 예수금 조회 실패: {e}")
            return self.cash_balance_cache if self.cash_balance_cache is not None else 0.0

    def _post_domestic(self, path, api_id, body):
        """국내주식 조회 TR 공통 호출. 실패 시 원인을 포함한 dict를 반환한다."""
        token = self.get_token()
        if not token:
            return {"return_code": -1, "return_msg": "토큰 발급 실패"}
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
        }
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return {"return_code": -1, "return_msg": str(exc)}

    def get_previous_day_universe(self, top_n, min_market_cap, max_market_cap):
        """ka10031 조회구분 2의 전일 거래대금 순위와 전일 시총으로 유니버스를 만든다."""
        ranking = self._post_domestic(
            "/api/dostk/rkinfo", "ka10031",
            {"mrkt_tp": "000", "qry_tp": "2", "rank_strt": "0",
             "rank_end": "100", "stex_tp": "1"},
        )
        if ranking.get("return_code") != 0:
            print(f"[SCREENER ERR] 키움 전일 거래대금 순위 실패: {ranking.get('return_msg')}")
            return {}

        selected = {}
        metadata = {}
        for item in ranking.get("pred_trde_qty_upper", []):
            code = str(item.get("stk_cd", "")).lstrip("AJQ")[-6:]
            name = str(item.get("stk_nm", "")).strip()
            if not code or not name:
                continue
            info = self._post_domestic(
                "/api/dostk/stkinfo", "ka10100", {"stk_cd": code}
            )
            time.sleep(0.22)
            if info.get("return_code") != 0:
                continue
            # ka10100의 전일종가와 상장주식수만 사용해 장중 가격 혼입을 막는다.
            previous_close = abs(_number(info.get("lastPrice")))
            listed_shares = abs(_number(info.get("listCount")))
            previous_market_cap = previous_close * listed_shares
            unsafe_name = (
                name.endswith("우") or "우B" in name or "스팩" in name
                or "ETN" in name or "ETF" in str(info.get("marketName", "")).upper()
            )
            audit_info = str(info.get("auditInfo", "")).strip()
            unsafe_state = audit_info not in {"", "정상"} or str(
                info.get("orderWarning", "0")
            ).strip() not in {"", "0"}
            if unsafe_name or unsafe_state:
                continue
            if min_market_cap <= previous_market_cap <= max_market_cap:
                selected[name] = code
                metadata[name] = {
                    "code": code,
                    "market": _market_name(info.get("marketName")),
                    "sector": str(info.get("upName", "")).strip() or "미분류",
                    "previous_close": previous_close,
                    "previous_market_cap": previous_market_cap,
                }
                if len(selected) >= top_n:
                    break
        self.last_universe_metadata = metadata
        return selected

    def get_account_snapshot(self):
        """kt00018 기준 실계좌 보유수량/평균매입가/평가자산을 조회한다."""
        token = self.get_token()
        if not token:
            return {"ok": False, "error": "토큰 발급 실패"}
        url = f"{self.base_url}/api/dostk/acnt"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "kt00018",
        }
        body = {"qry_tp": "1", "dmst_stex_tp": "KRX"}
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("return_code") != 0:
                return {"ok": False, "error": data.get("return_msg", "잔고 조회 실패")}
            positions = {}
            for item in data.get("acnt_evlt_remn_indv_tot", []):
                code = str(item.get("stk_cd", "")).lstrip("AJQ")[-6:]
                qty = int(_number(item.get("rmnd_qty")))
                if code and qty > 0:
                    positions[code] = {
                        "code": code,
                        "name": str(item.get("stk_nm", "")).strip(),
                        "shares": qty,
                        "available_shares": int(_number(item.get("trde_able_qty"))),
                        "buy_price": abs(_number(item.get("pur_pric"))),
                        "current_price": abs(_number(item.get("cur_prc"))),
                        "evaluation_pnl": _number(item.get("evltv_prft")),
                    }
            return {
                "ok": True,
                "positions": positions,
                "total_assets": _number(data.get("prsm_dpst_aset_amt")),
                "total_pnl": _number(data.get("tot_evlt_pl")),
                "cash": self.get_cash_balance(),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_order_execution(self, order_id, code=""):
        """kt00009에서 주문번호별 실제 체결수량과 가중평균 체결가를 조회한다."""
        token = self.get_token()
        if not token:
            return {"ok": False, "error": "토큰 발급 실패"}
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "kt00009",
        }
        body = {
            "ord_dt": time.strftime("%Y%m%d"),
            "stk_bond_tp": "1", "mrkt_tp": "0", "sell_tp": "0",
            "qry_tp": "1", "stk_cd": code, "fr_ord_no": str(order_id),
            "dmst_stex_tp": "%",
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/dostk/acnt",
            data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("return_code") != 0:
                return {"ok": False, "error": data.get("return_msg", "체결 조회 실패")}
            fills = [
                item for item in data.get("acnt_ord_cntr_prst_array", [])
                if str(item.get("ord_no", "")).lstrip("0") == str(order_id).lstrip("0")
                and int(_number(item.get("cntr_qty"))) > 0
            ]
            qty = sum(int(_number(item.get("cntr_qty"))) for item in fills)
            amount = sum(
                int(_number(item.get("cntr_qty"))) * abs(_number(item.get("cntr_uv")))
                for item in fills
            )
            return {"ok": True, "filled_qty": qty,
                    "fill_price": amount / qty if qty else 0.0}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_daily_candles(self, code, count=30):
        """키움 ka10081 일봉 조회. 최신순 응답을 과거→현재 순으로 변환한다."""
        data = self._post_domestic(
            "/api/dostk/chart", "ka10081",
            {"stk_cd": str(code)[-6:], "base_dt": time.strftime("%Y%m%d"),
             "upd_stkpc_tp": "1"},
        )
        if data.get("return_code") != 0:
            print(f"[ERR] {code} 키움 일봉 조회 실패: {data.get('return_msg')}")
            return []
        candles = []
        for item in reversed(data.get("stk_dt_pole_chart_qry", [])[:count]):
            candles.append({
                "date": str(item.get("dt", "")),
                "open": abs(_number(item.get("open_pric"))),
                "high": abs(_number(item.get("high_pric"))),
                "low": abs(_number(item.get("low_pric"))),
                "close": abs(_number(item.get("cur_prc"))),
                "vol": abs(_number(item.get("trde_qty"))),
            })
        return candles

    def send_buy_order(self, code, name, qty, price=0, trade_type="3"):
        """
        주식 매수 주문 집행 (kt10000)
        - trade_type: "0" 보통(지정가), "3" 시장가 (기본값: 체결 지연 방지용 시장가)
        - dmst_stex_tp: "SOR" (최선주문집행) 또는 "KRX"
        """
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "토큰 없음"}

        url = f"{self.base_url}/api/dostk/ordr"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "kt10000"
        }
        body = {
            "dmst_stex_tp": "SOR",  # 거래소 간 최선가 자동 탐색
            "stk_cd": code,
            "ord_qty": str(qty),
            "ord_uv": str(int(price)) if trade_type == "0" else "",
            "trde_tp": trade_type
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
                ret_code = res.get("return_code", -1)
                if ret_code == 0:
                    ord_no = res.get("ord_no", "")
                    print(f"✅ [실전 매수 완료] {name}({code}) {qty}주 | 주문번호: {ord_no}")
                    return {
                        "status": "accepted",
                        "order_id": ord_no,
                        "code": code,
                        "name": name,
                        "qty": qty,
                        "price": price
                    }
                else:
                    err_msg = res.get("return_msg", "주문 실패")
                    print(f"❌ [매수 거절] {name}({code}): {err_msg}")
                    return {"status": "error", "message": err_msg}
        except Exception as e:
            print(f"[ERR] 매수 주문 예외 발생: {e}")
            return {"status": "error", "message": str(e)}

    def send_sell_order(self, code, name, qty, price=0, reason="손절/익절", trade_type="3"):
        """
        주식 매도 주문 집행 (kt10001)
        - 칼손절 및 이익 보존을 위해 기본 '3' 시장가로 즉시 청산
        """
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "토큰 없음"}

        url = f"{self.base_url}/api/dostk/ordr"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "kt10001"
        }
        body = {
            "dmst_stex_tp": "SOR",
            "stk_cd": code,
            "ord_qty": str(qty),
            "ord_uv": str(int(price)) if trade_type == "0" else "",
            "trde_tp": trade_type
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
                ret_code = res.get("return_code", -1)
                if ret_code == 0:
                    ord_no = res.get("ord_no", "")
                    print(f"🚨 [실전 매도 완료] {name}({code}) {qty}주 | {reason} | 주문번호: {ord_no}")
                    return {
                        "status": "accepted",
                        "order_id": ord_no,
                        "code": code,
                        "name": name,
                        "qty": qty,
                        "price": price,
                        "reason": reason
                    }
                else:
                    err_msg = res.get("return_msg", "매도 실패")
                    print(f"❌ [매도 거절] {name}({code}): {err_msg}")
                    return {"status": "error", "message": err_msg}
        except Exception as e:
            print(f"[ERR] 매도 주문 예외 발생: {e}")
            return {"status": "error", "message": str(e)}

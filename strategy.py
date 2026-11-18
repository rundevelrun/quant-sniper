from config import (
    RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_RECOVERY_THRESHOLD, VBO_K,
    MARKET_MA_PERIOD, VOLUME_CONFIRM_MULTIPLIER, MAX_ENTRY_GAP_PCT,
    MAX_ENTRY_DAY_RETURN_PCT, MAX_UPPER_WICK_RATIO,
    ATR_PERIOD, ATR_STOP_MULTIPLIER, MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT,
    MIN_RISK_REWARD_RATIO, STOP_LOSS_PCT, TAKE_PROFIT_PCT
)

def is_favorable_market(market_candles):
    """시장 프록시가 장기 이동평균 위이고 장기선이 상승할 때만 진입한다."""
    if not market_candles or len(market_candles) < MARKET_MA_PERIOD + 1:
        return False
    closes = [c["close"] for c in market_candles]
    ma_now = sum(closes[-MARKET_MA_PERIOD:]) / MARKET_MA_PERIOD
    ma_prev = sum(closes[-MARKET_MA_PERIOD - 1:-1]) / MARKET_MA_PERIOD
    return closes[-1] > ma_now and ma_now > ma_prev

def calculate_rsi(closes, period=RSI_PERIOD):
    """14일 RSI 지표 계산"""
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_atr(candles, period=ATR_PERIOD):
    """
    14일 ATR(Average True Range) 변동성 지표 계산
    - 고정 % 손절 대신 종목 고유의 일중 변동폭을 반영하여 휩소(Whipsaw) 방어
    """
    if len(candles) < period + 1:
        return candles[-1]["close"] * 0.03 # 기본 3% 변동성 가정
        
    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
        
    if len(tr_list) < period:
        return candles[-1]["close"] * 0.03
        
    return sum(tr_list[-period:]) / period

def evaluate_signals(candles_dict, market_candles=None, volume_progress=1.0,
                     diagnostics=None):
    """
    전 종목의 캔들 데이터를 분석하여 매수 신호 탐색 및 우선순위 랭킹 반환
    candles_dict: {name: [{"open", "high", "low", "close", "vol"}, ...]}
    
    💡 퀀트 피드백 반영:
    1. 고정 -2% 손절 대신 ATR 변동성 기반 동적 손절폭(2.5%~4.0%) 산출
    2. 진입 시 손절폭과 기준 보상폭을 기록하고 실제 청산은 봇의 고점 추적으로 수행
    """
    candidates = []
    favorable_market = is_favorable_market(market_candles)
    
    for name, candles in candles_dict.items():
        if len(candles) < 25:
            if diagnostics is not None:
                diagnostics[name] = {
                    "rsi": None, "previous_rsi": None,
                    "rsi_blocker": "캔들 부족",
                    "vbo_blocker": "캔들 부족",
                }
            continue
            
        closes = [c["close"] for c in candles]
        curr = candles[-1]
        prev = candles[-2]
        
        rsi = calculate_rsi(closes)
        prev_rsi = calculate_rsi(closes[:-1])
        ma20 = sum(closes[-20:]) / 20.0
        prev_ma20 = sum(closes[-21:-1]) / 20.0
        atr = calculate_atr(candles)

        prev_close = prev["close"]
        gap_pct = (curr["open"] - prev_close) / prev_close if prev_close else 0.0
        day_return_pct = (curr["close"] - prev_close) / prev_close if prev_close else 0.0
        candle_range = max(curr["high"] - curr["low"], 1.0)
        upper_wick_ratio = (curr["high"] - max(curr["open"], curr["close"])) / candle_range
        price_quality_ok = (
            gap_pct <= MAX_ENTRY_GAP_PCT
            and day_return_pct <= MAX_ENTRY_DAY_RETURN_PCT
            and upper_wick_ratio <= MAX_UPPER_WICK_RATIO
        )
        
        # 전략 1: RSI 극단 과매도 반등
        is_rsi_signal = (
            prev_rsi <= RSI_BUY_THRESHOLD
            and rsi > RSI_RECOVERY_THRESHOLD
            and curr["close"] > curr["open"]
            and curr["close"] > prev["high"]
            and price_quality_ok
        )
        
        # 전략 2: 20일선 위 변동성 돌파
        target_price = curr["open"] + (prev["high"] - prev["low"]) * VBO_K
        avg_volume20 = sum(c["vol"] for c in candles[-21:-1]) / 20.0
        expected_volume = avg_volume20 * max(0.05, min(1.0, volume_progress))
        volume_confirmed = (
            expected_volume > 0
            and curr["vol"] >= expected_volume * VOLUME_CONFIRM_MULTIPLIER
        )
        is_vbo_signal = (
            (curr["close"] >= target_price) and
            (curr["close"] > curr["open"]) and 
            (curr["close"] > ma20) and
            (ma20 > prev_ma20) and
            volume_confirmed and
            price_quality_ok
        )

        if diagnostics is not None:
            if is_rsi_signal:
                rsi_blocker = "충족"
            elif prev_rsi > RSI_BUY_THRESHOLD:
                rsi_blocker = f"직전 RSI {prev_rsi:.1f}>28"
            elif rsi <= RSI_RECOVERY_THRESHOLD:
                rsi_blocker = f"현재 RSI {rsi:.1f}≤30"
            elif curr["close"] <= curr["open"]:
                rsi_blocker = "양봉 아님"
            elif curr["close"] <= prev["high"]:
                rsi_blocker = "전일 고가 미돌파"
            else:
                rsi_blocker = "가격 품질 기준 미달"

            if not favorable_market:
                vbo_blocker = "시장 비우호"
            elif curr["close"] < target_price:
                vbo_blocker = "돌파가 미도달"
            elif curr["close"] <= curr["open"]:
                vbo_blocker = "양봉 아님"
            elif curr["close"] <= ma20:
                vbo_blocker = "20일선 아래"
            elif ma20 <= prev_ma20:
                vbo_blocker = "20일선 하락"
            elif not volume_confirmed:
                vbo_blocker = "거래량 부족"
            elif not price_quality_ok:
                vbo_blocker = "가격 품질 기준 미달"
            else:
                vbo_blocker = "충족"

            diagnostics[name] = {
                "rsi": round(rsi, 1),
                "previous_rsi": round(prev_rsi, 1),
                "rsi_blocker": rsi_blocker,
                "vbo_blocker": vbo_blocker,
            }
        
        if is_rsi_signal:
            entry_p = curr["close"]
            atr_pct = (atr / entry_p) if entry_p > 0 else 0.03
            
            # 과매도 종목은 변동성이 터진 상태이므로 ATR * 1.5배 손절선 적용 (최소 2.5%, 최대 4.0% 캡)
            dynamic_stop_pct = max(MIN_STOP_LOSS_PCT, min(MAX_STOP_LOSS_PCT, atr_pct * ATR_STOP_MULTIPLIER))
            dynamic_take_profit_pct = max(TAKE_PROFIT_PCT, dynamic_stop_pct * MIN_RISK_REWARD_RATIO)
            
            candidates.append({
                "name": name,
                "strategy": "RSI 과매도 반등",
                # 비우호장에서는 충분히 확인된 역추세 반등만 절반 크기로 허용한다.
                "size_multiplier": 1.0 if favorable_market else 0.5,
                "market_favorable": favorable_market,
                "score": rsi,
                "entry_price": entry_p,
                "rsi": round(rsi, 1),
                "previous_rsi": round(prev_rsi, 1),
                "atr": round(atr, 0),
                "stop_loss_pct": round(dynamic_stop_pct, 4),
                "take_profit_pct": round(dynamic_take_profit_pct, 4)
            })
        elif is_vbo_signal and favorable_market:
            # 시장가 주문 수량은 이미 지나간 돌파선이 아니라 현재가로 산정한다.
            entry_p = curr["close"]
            atr_pct = (atr / entry_p) if entry_p > 0 else 0.03
            breakout_pct = (curr["close"] - target_price) / target_price * 100
            
            dynamic_stop_pct = max(MIN_STOP_LOSS_PCT, min(MAX_STOP_LOSS_PCT, atr_pct * ATR_STOP_MULTIPLIER))
            dynamic_take_profit_pct = max(TAKE_PROFIT_PCT, dynamic_stop_pct * MIN_RISK_REWARD_RATIO)
            
            candidates.append({
                "name": name,
                "strategy": "변동성 돌파 (추세)",
                "score": 50.0 + breakout_pct,
                "entry_price": entry_p,
                "rsi": round(rsi, 1),
                "volume_pace_ratio": round(curr["vol"] / expected_volume, 2),
                "atr": round(atr, 0),
                "stop_loss_pct": round(dynamic_stop_pct, 4),
                "take_profit_pct": round(dynamic_take_profit_pct, 4)
            })
            
    # 우선순위 정렬 (RSI 과매도 신호 최우선 배치)
    candidates.sort(key=lambda x: x["score"])
    return candidates

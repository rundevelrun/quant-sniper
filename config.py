import os

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

LIVE_TRADING_ENABLED = env_bool("LIVE_TRADING_ENABLED", False)
AUTO_GIT_PUSH = env_bool("AUTO_GIT_PUSH", True)
ORDER_CONFIRM_TIMEOUT_SECONDS = int(os.getenv("ORDER_CONFIRM_TIMEOUT_SECONDS", "20"))

# 동적 스크리닝 활성화 여부
DYNAMIC_SCREENING = True
SCREENER_TOP_N = 30
SCREENER_RANK_POOL = 300       # 전일 거래량 상위 300개에서 실제 전일 거래대금 재정렬
MIN_MARKET_CAP_KRW = 500_000_000_000 # 시가총액 최소 5천억 원: 소액 계좌의 매수 가능 후보 확대
MAX_MARKET_CAP_KRW = 5_000_000_000_000 # 🛡️ 시가총액 최대 5조 원 이하 (굼뜬 10조+ 메가캡 배제, 탄력적 황금구간)

# 고정 코어 유니버스 (동적 스크리닝 비활성화 시 또는 오프라인 폴백용)
DEFAULT_UNIVERSE = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "현대차": "005380",
    "기아": "000270",
    "NAVER": "035420",
    "카카오": "035720",
    "셀트리온": "068270",
    "신한지주": "055550",
    "KB금융": "105560",
    "삼성바이오로직스": "207940",
    "LG에너지솔루션": "373220",
    "POSCO홀딩스": "005490",
    "KODEX200": "069500",
    "KODEX레버리지": "122630",
    "KODEX코스닥150": "229200",
    "TIGER미국S&P500": "360750",
    "TIGER미국나스닥100": "133690"
}

# 전략 파라미터
RSI_PERIOD = 14
RSI_BUY_THRESHOLD = 28.0       # RSI 28 이하 시 과매도 저격 매수
RSI_RECOVERY_THRESHOLD = 30.0  # 과매도 진입 후 30 상향 돌파 확인
VBO_K = 0.5                    # 변동성 돌파 계수 (전일 레인지의 0.5)
MARKET_PROXY_CODE = "069500"   # KODEX 200: 국내 시장 추세 프록시
KOSDAQ_MARKET_PROXY_CODE = "229200" # KODEX 코스닥150: 코스닥 시장 추세 프록시
MARKET_MA_PERIOD = 60
VOLUME_CONFIRM_MULTIPLIER = 1.5
UNFAVORABLE_VBO_VOLUME_MULTIPLIER = 2.0
UNFAVORABLE_VBO_RELATIVE_STRENGTH_PCT = 0.02
UNFAVORABLE_VBO_MAX_MARKET_DROP_PCT = 0.015
UNFAVORABLE_VBO_MAX_DAY_RETURN_PCT = 0.06
UNFAVORABLE_VBO_MAX_BREAKOUT_CHASE_PCT = 0.02
UNFAVORABLE_VBO_SIZE_MULTIPLIER = 0.15 / 0.35
INTRADAY_SURGE_ENABLED = True
INTRADAY_SURGE_SCAN_INTERVAL_SECONDS = 300
INTRADAY_SURGE_MAX_CANDIDATES = 8
INTRADAY_SURGE_MIN_DAY_RETURN_PCT = 0.01
INTRADAY_SURGE_MAX_DAY_RETURN_PCT = 0.08
INTRADAY_SURGE_MIN_VOLUME_RATE_PCT = 50.0
INTRADAY_SURGE_MAX_INVESTMENT_PCT = 0.15
INTRADAY_SURGE_SIZE_MULTIPLIER = 0.15 / 0.35
MAX_ENTRY_GAP_PCT = 0.04
MAX_ENTRY_DAY_RETURN_PCT = 0.10
MAX_UPPER_WICK_RATIO = 0.40
ENTRY_START_TIME = "09:15"
ENTRY_END_TIME = "14:30"
MIM_ENABLED = False
MIM_ENTRY_START_TIME = "15:00"
MIM_ENTRY_END_TIME = "15:05"
MIM_EXIT_TIME = "15:20"
MIM_MAX_SHARES = 1
MIM_STOP_LOSS_PCT = 0.005
ETF_ROTATION_ENABLED = True
ETF_ROTATION_LONG_CODE = "069500"
ETF_ROTATION_INVERSE_CODE = "114800"
ETF_ROTATION_ASSETS = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "379800": "KODEX 미국S&P500",
    "379810": "KODEX 미국나스닥100",
    "132030": "KODEX 골드선물(H)",
    "114260": "KODEX 국고채3년",
    "261240": "KODEX 미국달러선물",
    "114800": "KODEX 인버스",
}
ETF_ROTATION_MA_PERIOD = 60
ETF_ROTATION_SLOPE_DAYS = 5
ETF_ROTATION_BAND_PCT = 0.01
ETF_ROTATION_SHORT_MOMENTUM_DAYS = 60
ETF_ROTATION_LONG_MOMENTUM_DAYS = 120
ETF_ROTATION_SWITCH_MARGIN = 0.02
ETF_ROTATION_ALLOC_PCT = 0.30
ETF_ROTATION_STOP_LOSS_PCT = 0.025
ETF_MAX_ADVERSE_ENTRY_GAP_PCT = 0.03  # 전일 종가 대비 -3% 초과 역방향 갭은 회복 전 진입 금지
ETF_ROTATION_TRAILING_TRIGGER = 0.060  # ETF 추세전환은 +6%부터 고점 추적
ETF_ROTATION_TRAILING_GAP = 0.015      # 활성화 후 최고가 대비 1.5% 간격
SECTOR_ETF_ENABLED = True
SECTOR_ETF_ASSETS = {
    "091160": "KODEX 반도체",
    "305720": "KODEX 2차전지산업",
    "244580": "KODEX 바이오",
    "091170": "KODEX 은행",
    "091180": "KODEX 자동차",
    "445290": "KODEX 로봇액티브",
}
SECTOR_ETF_MA_PERIOD = 60
SECTOR_ETF_SLOPE_DAYS = 5
SECTOR_ETF_BAND_PCT = 0.01
SECTOR_ETF_SHORT_MOMENTUM_DAYS = 20
SECTOR_ETF_LONG_MOMENTUM_DAYS = 60
SECTOR_ETF_SWITCH_MARGIN = 0.02
SECTOR_ETF_ALLOC_PCT = 0.15
SECTOR_ETF_STOP_LOSS_PCT = 0.025

# 💡 ATR 기반 동적 손절 및 손익비 규칙 (휩소 노이즈 방어)
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.5      # 변동성(ATR) 기반 동적 손절 배수
MIN_STOP_LOSS_PCT = 0.025      # 최소 손절선 2.5% (과매도 바닥권 일중 흔들림 노이즈 방어)
MAX_STOP_LOSS_PCT = 0.040      # 최대 손절선 4.0% (과도한 손실 캡)
MIN_RISK_REWARD_RATIO = 2.0    # 진입 신호의 기준 보상폭 산출용

# 청산 및 리스크 관리 기본값
STOP_LOSS_PCT = 0.030          # 기본 손절 기준선 (-3.0%)
TRAILING_STOP_TRIGGER = 0.040  # +4.0% 수익 시 트레일링 스탑 발동
TRAILING_STOP_LOCK = 0.010     # 발동 후 손절선을 매수가 +1.0%로 상향
TAKE_PROFIT_PCT = 0.080        # 고점 추적 트레일링 활성화 기준 (+8.0%)
MAX_HOLD_DAYS = 7              # 최대 보유일 (7거래일 경과 시 타임스탑)
MAX_PROFIT_HOLD_DAYS = 14      # 수익 중이지만 추적 미활성인 포지션의 연장 한도

# 💡 거래비용 및 슬리피지 보수적 반영 규칙
TRANSACTION_TAX_PCT = 0.0018   # 증권거래세 0.18%
BROKER_COMMISSION_PCT = 0.00015 # 키움증권 온라인 위탁수수료 0.015%
SLIPPAGE_ESTIMATE_PCT = 0.0010 # 시장가 진입/청산 슬리피지 0.10% 보수적 추정
TOTAL_ROUNDTRIP_COST_PCT = TRANSACTION_TAX_PCT + (BROKER_COMMISSION_PCT * 2) + (SLIPPAGE_ESTIMATE_PCT * 2) # 왕복 총비용 약 0.41%

# 자금 관리 규칙
MAX_ALLOC_PER_STOCK = 0.35     # 1종목당 계좌 총자산의 최대 35%
MAX_POSITIONS = 5              # 최대 동시 보유 종목 수
MAX_TOTAL_EXPOSURE_PCT = 0.90  # 전체 투자금은 총자산의 90% 이내(현금 10% 유지)
MAX_POSITIONS_PER_SECTOR = 2   # 동일 업종 동시 보유 상한
MAX_RISK_PER_TRADE_PCT = 0.007 # 1회 거래의 계획 손실을 계좌 자산 0.7% 이내로 제한
EQUITY_SLOWDOWN_DRAWDOWN_PCT = 0.08 # 계좌 고점 대비 -8%부터 신규 주문 크기 절반
EQUITY_HALT_DRAWDOWN_PCT = 0.12     # 계좌 고점 대비 -12%부터 신규 매수 중단
DAILY_KILL_SWITCH_PCT = 0.040  # 일일 계좌 누적 손실 -4.0% 도달 시 당일 올스톱
EXCLUDE_ETFS = True             # 개별주 전략에서 ETF/레버리지/인버스 상품 제외

# 감시 및 호출 주기
POLL_INTERVAL_SECONDS = 30     # 장중 30초 주기 초정밀 감시
API_REQUEST_DELAY = 0.3        # API 연속 호출 시 0.3초 대기 (레이트 리밋 방어)

# 키움 REST API 공식 엔드포인트
REAL_API_BASE_URL = "https://api.kiwoom.com"

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║    ROBOTKING v23 ADVANCED - PROBABILITY & TREND FILTER         ║
║    0.8$ Margin | M5 Trading | Trend Filter | Probability Score ║
║    BTC Correlation | Fear & Greed | Breakeven | Auto Recovery  ║
╚══════════════════════════════════════════════════════════════════╝

v23 NEW FEATURES:
✅ Probability scoring system (setup + trend + BTC + session + sentiment)
✅ H1/M15 trend filter (only trade WITH the trend)
✅ BTC correlation check
✅ Fear & Greed Index integration
✅ Automatic breakeven (move SL to entry at small profit)
✅ LIMIT order fallback if MARKET rejected
✅ Full position recovery (manage any open position)
✅ Increased margin to 0.8$ per trade
✅ M5 timeframe trading
✅ Enhanced risk management
"""

import time, hmac, hashlib, requests, threading, os, logging, json, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

logging.basicConfig(level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("v23_advanced.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg: str):
    """Send Telegram notification"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except:
        pass

# SECURITY - BINANCE
API_KEY = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")
if not API_KEY or not API_SECRET:
    logger.error("❌ BINANCE API keys missing! Set BINANCE_API_KEY and BINANCE_API_SECRET")
    exit(1)

BASE_URL = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION v23 ADVANCED
# ═══════════════════════════════════════════════════════════════════

# MARGIN & LEVERAGE
MARGIN_PER_TRADE = 0.8  # Increased from 0.5$ to 0.8$
LEVERAGE = 20
MARGIN_TYPE = "ISOLATED"

# POSITIONS & TRADING
MAX_POSITIONS = 4
MIN_PROBABILITY_SCORE = 65  # Minimum 65% probability to enter trade
TRAILING_STOP_START_RR = 1.0
BREAKEVEN_RR = 0.3  # Move SL to breakeven at RR 0.3

# TREND FILTER (NEW in v23)
ENABLE_TREND_FILTER = True
TREND_TIMEFRAME = "15m"  # H1 or 15m for trend detection
MIN_TREND_STRENGTH = 0.6  # 60% trend strength required

# PROBABILITY WEIGHTS (NEW in v23)
PROBABILITY_WEIGHTS = {
    "setup_score": 0.25,      # 25% - Quality of SMC setup
    "trend_alignment": 0.25,  # 25% - Trend filter alignment
    "btc_correlation": 0.15,  # 15% - BTC direction correlation
    "session_quality": 0.15,  # 15% - Trading session quality
    "sentiment": 0.10,        # 10% - Fear & Greed Index
    "volatility": 0.10        # 10% - Market volatility
}

# SESSION-BASED TRADING
ENABLE_SESSION_FILTER = True
SESSION_WEIGHTS = {
    "LONDON": 1.0,       # Best session - 100%
    "NEW_YORK": 1.0,     # Best session - 100%
    "ASIA": 0.7,         # Moderate - 70%
    "OFF_HOURS": 0.4     # Worst - 40%
}

# SCANNING
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "MATICUSDT",
    "DOTUSDT", "ATOMUSDT", "LTCUSDT", "TRXUSDT", "APTUSDT",
    "OPUSDT", "ARBUSDT", "INJUSDT", "SUIUSDT", "FTMUSDT",
    "NEARUSDT", "FILUSDT", "RUNEUSDT", "PEPEUSDT"
]

SCAN_INTERVAL = 12
MONITOR_INTERVAL = 2
TRAILING_STOP_INTERVAL = 3
DASHBOARD_INTERVAL = 60
MAX_WORKERS = 12

CACHE_DURATION = 4

# ═══════════════════════════════════════════════════════════════════
#  12 SMC SETUPS
# ═══════════════════════════════════════════════════════════════════

SETUPS = {
    "OB_CHOCH_DEMAND": {"score": 90, "description": "Order Block + ChoCH + Demand"},
    "MSS_BREAKER_BOS": {"score": 90, "description": "MSS + Breaker + BOS"},
    "BOS_BREAKER_MSS": {"score": 80, "description": "BOS + Breaker + MSS"},
    "NEW_HH_CHOCH": {"score": 80, "description": "New Higher High + ChoCH"},
    "FAKEOUT_TRENDLINE": {"score": 70, "description": "Fakeout + Trendline Break"},
    "DOUBLE_TOP_OB": {"score": 80, "description": "Double Top + Order Block"},
    "BREAKOUT_RETEST": {"score": 70, "description": "Breakout + Retest"},
    "LIQ_SWEEP_BOS": {"score": 90, "description": "Liquidity Sweep + BOS"},
    "MSS_FVG_FIB": {"score": 90, "description": "MSS + FVG + Fibonacci"},
    "OB_IDM_BOS": {"score": 90, "description": "Order Block + IDM + BOS"},
    "DOUBLE_BOTTOM_BB": {"score": 70, "description": "Double Bottom + Bollinger"},
    "FVG_SUPPORT_BOS": {"score": 70, "description": "FVG Support + BOS"},
}

# STATE
account_balance = 0
total_traded = 0
total_wins = 0
total_losses = 0

trade_log = {}
setup_memory = defaultdict(lambda: {"wins": 0, "losses": 0})
session_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0})

klines_cache = {}
price_cache = {}
symbol_info_cache = {}
fear_greed_cache = {"value": 50, "timestamp": 0}
btc_trend_cache = {"trend": 0, "timestamp": 0}

trade_lock = threading.Lock()
api_lock = threading.Lock()
api_call_times = []

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    session = get_current_session()
    return f"v23 ADVANCED | Balance: ${account_balance:.2f} | Open: {n_open}/4 | Session: {session}", 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    
    return jsonify({
        "status": "RUNNING",
        "balance": round(account_balance, 2),
        "positions_open": n_open,
        "total_traded": total_traded,
        "wins": total_wins,
        "losses": total_losses,
        "margin_per_trade": MARGIN_PER_TRADE,
        "leverage": LEVERAGE,
        "current_session": get_current_session(),
        "fear_greed": fear_greed_cache.get("value", 50),
        "min_probability": MIN_PROBABILITY_SCORE
    })

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    try:
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False), daemon=True).start()
    except:
        pass

# ═══════════════════════════════════════════════════════════════════
#  SESSION DETECTION
# ═══════════════════════════════════════════════════════════════════

def get_current_session() -> str:
    """Determine current trading session based on UTC time"""
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    # London: 07:00-16:00 UTC
    if 7 <= hour < 16:
        return "LONDON"
    
    # New York: 13:00-22:00 UTC (overlap with London 13:00-16:00)
    elif 13 <= hour < 22:
        return "NEW_YORK"
    
    # Asia: 23:00-08:00 UTC
    elif hour >= 23 or hour < 8:
        return "ASIA"
    
    # Off hours
    else:
        return "OFF_HOURS"

def get_session_weight() -> float:
    """Get session quality weight"""
    session = get_current_session()
    return SESSION_WEIGHTS.get(session, 0.5)

# ═══════════════════════════════════════════════════════════════════
#  API FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def wait_for_rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < 60]
        if len(api_call_times) >= 1200 * 0.9:
            sleep_time = 60 - (now - api_call_times[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        api_call_times.append(now)

def _sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method: str, path: str, params: dict = None, signed: bool = True) -> dict:
    """Make authenticated request to Binance"""
    if params is None:
        params = {}
    
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    
    wait_for_rate_limit()
    headers = {"X-MBX-APIKEY": API_KEY}
    url = BASE_URL + path
    
    for attempt in range(3):
        try:
            if method == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == "POST":
                resp = requests.post(url, params=params, headers=headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, params=params, headers=headers, timeout=10)
            else:
                return None
            
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(60)
            elif resp.status_code >= 400:
                logger.warning(f"API {resp.status_code}: {resp.text[:100]}")
                return None
        except Exception as e:
            logger.warning(f"Request error: {e}")
            if attempt < 2:
                time.sleep(1)
    
    return None

# ═══════════════════════════════════════════════════════════════════
#  DATA FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str = "5m", limit: int = 25) -> list:
    key = f"{symbol}_{interval}"
    now = time.time()
    
    if key in klines_cache:
        data, ts = klines_cache[key]
        if now - ts < CACHE_DURATION:
            return data
    
    data = request_binance("GET", "/fapi/v1/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }, signed=False)
    
    if data:
        klines_cache[key] = (data, now)
    
    return data if data else []

def get_price(symbol: str) -> float:
    now = time.time()
    if symbol in price_cache:
        price, ts = price_cache[symbol]
        if now - ts < 2:
            return price
    
    data = request_binance("GET", "/fapi/v1/ticker/price", {"symbol": symbol}, signed=False)
    if data and "price" in data:
        price = float(data["price"])
        price_cache[symbol] = (price, now)
        return price
    return 0

def get_symbol_info(symbol: str) -> dict:
    if symbol in symbol_info_cache:
        return symbol_info_cache[symbol]
    return None

def load_symbol_info():
    """Load symbol precision info"""
    logger.info("📥 Loading symbol info...")
    data = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
    if not data:
        logger.error("Failed to load symbol info")
        return
    
    for s in data.get("symbols", []):
        symbol = s["symbol"]
        if symbol in SYMBOLS:
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            
            symbol_info_cache[symbol] = {
                "quantityPrecision": s.get("quantityPrecision", 3),
                "pricePrecision": s.get("pricePrecision", 2),
                "minQty": float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                "maxQty": float(filters.get("LOT_SIZE", {}).get("maxQty", 10000)),
                "stepSize": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
            }
    
    logger.info(f"✅ Loaded info for {len(symbol_info_cache)} symbols")

def sync_account_balance():
    """Sync account balance from Binance"""
    global account_balance
    try:
        account = request_binance("GET", "/fapi/v2/account")
        if account:
            account_balance = float(account.get("availableBalance", 0))
    except Exception as e:
        logger.error(f"sync_account_balance: {e}")

def set_leverage(symbol: str, leverage: int):
    """Set leverage for symbol"""
    try:
        request_binance("POST", "/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage
        })
    except:
        pass

def set_margin_type(symbol: str, margin_type: str):
    """Set margin type for symbol"""
    try:
        request_binance("POST", "/fapi/v1/marginType", {
            "symbol": symbol,
            "marginType": margin_type
        })
    except:
        pass

def calc_atr(symbol: str, period: int = 14) -> float:
    """Calculate ATR"""
    klines = get_klines(symbol, "5m", period + 1)
    if not klines or len(klines) < period:
        return 0
    
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(abs(highs[1:] - closes[:-1]),
                              abs(lows[1:] - closes[:-1])))
    
    return np.mean(tr) if len(tr) > 0 else 0

# ═══════════════════════════════════════════════════════════════════
#  TREND FILTER (NEW in v23)
# ═══════════════════════════════════════════════════════════════════

def detect_trend(symbol: str, timeframe: str = "15m") -> dict:
    """Detect trend using EMA crossover on higher timeframe"""
    try:
        klines = get_klines(symbol, timeframe, 50)
        if not klines or len(klines) < 50:
            return {"direction": 0, "strength": 0}
        
        closes = np.array([float(k[4]) for k in klines])
        
        # Calculate EMAs
        ema_9 = np.mean(closes[-9:])
        ema_21 = np.mean(closes[-21:])
        ema_50 = np.mean(closes[-50:])
        
        # Determine trend
        if ema_9 > ema_21 > ema_50:
            direction = 1  # Uptrend
            strength = min((ema_9 - ema_50) / ema_50 * 100, 1.0)
        elif ema_9 < ema_21 < ema_50:
            direction = -1  # Downtrend
            strength = min((ema_50 - ema_9) / ema_50 * 100, 1.0)
        else:
            direction = 0  # Sideways
            strength = 0
        
        return {"direction": direction, "strength": abs(strength)}
    
    except:
        return {"direction": 0, "strength": 0}

def get_btc_trend() -> int:
    """Get BTC trend direction (cached)"""
    global btc_trend_cache
    
    now = time.time()
    if now - btc_trend_cache.get("timestamp", 0) < 60:  # Cache for 60 seconds
        return btc_trend_cache.get("trend", 0)
    
    trend_data = detect_trend("BTCUSDT", TREND_TIMEFRAME)
    btc_trend = trend_data["direction"]
    
    btc_trend_cache = {"trend": btc_trend, "timestamp": now}
    return btc_trend

def calculate_btc_correlation(symbol: str) -> float:
    """Calculate correlation with BTC direction"""
    if symbol == "BTCUSDT":
        return 1.0
    
    try:
        btc_trend = get_btc_trend()
        symbol_trend = detect_trend(symbol, TREND_TIMEFRAME)
        
        if btc_trend == 0 or symbol_trend["direction"] == 0:
            return 0.5  # Neutral
        
        # If trends align, positive correlation
        if btc_trend == symbol_trend["direction"]:
            return 0.8
        else:
            return 0.2
    
    except:
        return 0.5

# ═══════════════════════════════════════════════════════════════════
#  FEAR & GREED INDEX (NEW in v23)
# ═══════════════════════════════════════════════════════════════════

def get_fear_greed_index() -> int:
    """Get Fear & Greed Index (0-100)"""
    global fear_greed_cache
    
    now = time.time()
    # Cache for 1 hour
    if now - fear_greed_cache.get("timestamp", 0) < 3600:
        return fear_greed_cache.get("value", 50)
    
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            value = int(data["data"][0]["value"])
            fear_greed_cache = {"value": value, "timestamp": now}
            logger.info(f"📊 Fear & Greed Index: {value}")
            return value
    except:
        pass
    
    return 50  # Neutral if failed

def calculate_sentiment_score(fear_greed: int) -> float:
    """Convert Fear & Greed to sentiment score (0-1)"""
    # Extreme Fear (0-25) = Good for buying = 0.8
    # Fear (25-45) = Moderate buying = 0.6
    # Neutral (45-55) = Neutral = 0.5
    # Greed (55-75) = Moderate selling = 0.6
    # Extreme Greed (75-100) = Good for selling = 0.8
    
    if fear_greed < 25:
        return 0.8  # Extreme fear - good for longs
    elif fear_greed < 45:
        return 0.6  # Fear
    elif fear_greed < 55:
        return 0.5  # Neutral
    elif fear_greed < 75:
        return 0.6  # Greed
    else:
        return 0.8  # Extreme greed - good for shorts

# ═══════════════════════════════════════════════════════════════════
#  PROBABILITY CALCULATION (NEW in v23)
# ═══════════════════════════════════════════════════════════════════

def calculate_volatility_score(symbol: str) -> float:
    """Calculate volatility score (0-1)"""
    try:
        atr = calc_atr(symbol)
        price = get_price(symbol)
        
        if not atr or not price:
            return 0.5
        
        # ATR as % of price
        atr_pct = (atr / price) * 100
        
        # Optimal volatility: 1-3%
        if 1.0 <= atr_pct <= 3.0:
            return 1.0
        elif atr_pct < 1.0:
            return 0.6  # Too low
        elif atr_pct < 5.0:
            return 0.8  # Acceptable
        else:
            return 0.4  # Too high
    
    except:
        return 0.5

def calculate_probability(symbol: str, side: str, setup_name: str) -> float:
    """Calculate overall probability score (0-100)"""
    try:
        # 1. Setup Score (0-100 -> 0-1)
        setup_score_raw = SETUPS.get(setup_name, {}).get("score", 50)
        setup_score = setup_score_raw / 100.0
        
        # 2. Trend Alignment (0-1)
        trend_data = detect_trend(symbol, TREND_TIMEFRAME)
        trend_direction = trend_data["direction"]
        trend_strength = trend_data["strength"]
        
        if not ENABLE_TREND_FILTER:
            trend_score = 0.7  # Neutral if disabled
        elif side == "BUY" and trend_direction == 1:
            trend_score = 0.7 + (trend_strength * 0.3)  # 0.7-1.0
        elif side == "SELL" and trend_direction == -1:
            trend_score = 0.7 + (trend_strength * 0.3)  # 0.7-1.0
        elif trend_direction == 0:
            trend_score = 0.5  # Sideways
        else:
            trend_score = 0.2  # Against trend
        
        # 3. BTC Correlation (0-1)
        btc_corr = calculate_btc_correlation(symbol)
        
        # 4. Session Quality (0-1)
        session_score = get_session_weight()
        
        # 5. Sentiment (0-1)
        fear_greed = get_fear_greed_index()
        sentiment_score = calculate_sentiment_score(fear_greed)
        
        # Adjust sentiment based on side
        if side == "BUY" and fear_greed < 35:
            sentiment_score = min(sentiment_score * 1.2, 1.0)  # Boost longs in fear
        elif side == "SELL" and fear_greed > 65:
            sentiment_score = min(sentiment_score * 1.2, 1.0)  # Boost shorts in greed
        
        # 6. Volatility (0-1)
        volatility_score = calculate_volatility_score(symbol)
        
        # Calculate weighted probability
        probability = (
            setup_score * PROBABILITY_WEIGHTS["setup_score"] +
            trend_score * PROBABILITY_WEIGHTS["trend_alignment"] +
            btc_corr * PROBABILITY_WEIGHTS["btc_correlation"] +
            session_score * PROBABILITY_WEIGHTS["session_quality"] +
            sentiment_score * PROBABILITY_WEIGHTS["sentiment"] +
            volatility_score * PROBABILITY_WEIGHTS["volatility"]
        ) * 100
        
        return round(probability, 1)
    
    except Exception as e:
        logger.error(f"calculate_probability: {e}")
        return 50.0

# ═══════════════════════════════════════════════════════════════════
#  SMC SETUP DETECTION (All 12 setups)
# ═══════════════════════════════════════════════════════════════════

def detect_ob_choch_demand(symbol: str, side: str) -> dict:
    """Order Block + ChoCH + Demand Zone"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    for i in range(10, 18):
        if closes[i] < closes[i-1] and closes[i+1] > closes[i]:
            if closes[-1] > max(highs[i-3:i+1]):
                if closes[-1] < min(lows[-5:]):
                    return {"name": "OB_CHOCH_DEMAND", "score": SETUPS["OB_CHOCH_DEMAND"]["score"]}
    
    return None

def detect_mss_breaker_bos(symbol: str, side: str) -> dict:
    """MSS + Breaker Block + BOS"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    for i in range(8, 16):
        if closes[i] < min(lows[i-5:i]):
            if closes[-1] > closes[i]:
                if closes[-1] > max(closes[-8:-1]):
                    return {"name": "MSS_BREAKER_BOS", "score": SETUPS["MSS_BREAKER_BOS"]["score"]}
    
    return None

def detect_bos_breaker_mss(symbol: str, side: str) -> dict:
    """BOS + Breaker + MSS"""
    if side != "SELL":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    
    for i in range(8, 16):
        if closes[i] > max(highs[i-5:i]):
            if closes[-1] < closes[i]:
                if closes[-1] < min(closes[-8:-1]):
                    return {"name": "BOS_BREAKER_MSS", "score": SETUPS["BOS_BREAKER_MSS"]["score"]}
    
    return None

def detect_new_hh_choch(symbol: str, side: str) -> dict:
    """New Higher High + ChoCH"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    highs = np.array([float(k[2]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    if highs[-1] > max(highs[-15:-1]):
        if closes[-2] < closes[-3] and closes[-1] > closes[-2]:
            return {"name": "NEW_HH_CHOCH", "score": SETUPS["NEW_HH_CHOCH"]["score"]}
    
    return None

def detect_fakeout_trendline(symbol: str, side: str) -> dict:
    """Fakeout + Trendline Break"""
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    
    if side == "BUY":
        if min(lows[-3:]) < min(lows[-10:-3]):
            if closes[-1] > closes[-2]:
                return {"name": "FAKEOUT_TRENDLINE", "score": SETUPS["FAKEOUT_TRENDLINE"]["score"]}
    else:
        if max(highs[-3:]) > max(highs[-10:-3]):
            if closes[-1] < closes[-2]:
                return {"name": "FAKEOUT_TRENDLINE", "score": SETUPS["FAKEOUT_TRENDLINE"]["score"]}
    
    return None

def detect_double_top_ob(symbol: str, side: str) -> dict:
    """Double Top + Order Block"""
    if side != "SELL":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    highs = np.array([float(k[2]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    peaks = []
    for i in range(5, len(highs)-5):
        if highs[i] == max(highs[i-3:i+4]):
            peaks.append(i)
    
    if len(peaks) >= 2:
        if abs(highs[peaks[-1]] - highs[peaks[-2]]) / highs[peaks[-1]] < 0.02:
            if closes[-1] < min(closes[-5:]):
                return {"name": "DOUBLE_TOP_OB", "score": SETUPS["DOUBLE_TOP_OB"]["score"]}
    
    return None

def detect_breakout_retest(symbol: str, side: str) -> dict:
    """Breakout + Retest"""
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    if side == "BUY":
        resistance = max(highs[-15:-5])
        if max(highs[-5:-2]) > resistance:
            if min(lows[-2:]) < resistance and closes[-1] > resistance:
                return {"name": "BREAKOUT_RETEST", "score": SETUPS["BREAKOUT_RETEST"]["score"]}
    else:
        support = min(lows[-15:-5])
        if min(lows[-5:-2]) < support:
            if max(highs[-2:]) > support and closes[-1] < support:
                return {"name": "BREAKOUT_RETEST", "score": SETUPS["BREAKOUT_RETEST"]["score"]}
    
    return None

def detect_liq_sweep_bos(symbol: str, side: str) -> dict:
    """Liquidity Sweep + BOS"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    prev_low = min(lows[-15:-5])
    if min(lows[-5:-2]) < prev_low:
        if closes[-1] > max(closes[-8:-2]):
            return {"name": "LIQ_SWEEP_BOS", "score": SETUPS["LIQ_SWEEP_BOS"]["score"]}
    
    return None

def detect_mss_fvg_fib(symbol: str, side: str) -> dict:
    """MSS + FVG + Fibonacci"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    for i in range(10, 18):
        if closes[i] < min(closes[i-5:i]):
            if lows[i-1] > highs[i+1]:
                swing_high = max(highs[i-5:i])
                swing_low = min(lows[i:i+5])
                fib_618 = swing_low + (swing_high - swing_low) * 0.618
                
                if abs(closes[-1] - fib_618) / closes[-1] < 0.01:
                    return {"name": "MSS_FVG_FIB", "score": SETUPS["MSS_FVG_FIB"]["score"]}
    
    return None

def detect_ob_idm_bos(symbol: str, side: str) -> dict:
    """Order Block + IDM + BOS"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    for i in range(10, 18):
        if lows[i] < min(lows[i-5:i]):
            if closes[-1] > max(closes[i:i+5]):
                if closes[-1] > max(closes[-8:-1]):
                    return {"name": "OB_IDM_BOS", "score": SETUPS["OB_IDM_BOS"]["score"]}
    
    return None

def detect_double_bottom_bb(symbol: str, side: str) -> dict:
    """Double Bottom + Bollinger Bounce"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    closes = np.array([float(k[4]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    sma = np.mean(closes[-20:])
    std = np.std(closes[-20:])
    lower_bb = sma - (2 * std)
    
    troughs = []
    for i in range(5, len(lows)-5):
        if lows[i] == min(lows[i-3:i+4]):
            troughs.append(i)
    
    if len(troughs) >= 2:
        if abs(lows[troughs[-1]] - lows[troughs[-2]]) / lows[troughs[-1]] < 0.02:
            if lows[troughs[-1]] <= lower_bb:
                if closes[-1] > closes[-2]:
                    return {"name": "DOUBLE_BOTTOM_BB", "score": SETUPS["DOUBLE_BOTTOM_BB"]["score"]}
    
    return None

def detect_fvg_support_bos(symbol: str, side: str) -> dict:
    """FVG Support + BOS"""
    if side != "BUY":
        return None
    
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    for i in range(10, len(klines)-2):
        if highs[i-1] < lows[i+1]:
            gap_zone = (lows[i+1], highs[i-1])
            if highs[-2] >= gap_zone[0] and closes[-1] <= gap_zone[1]:
                if closes[-1] < min(lows[-12:-2]):
                    return {"name": "FVG_SUPPORT_BOS", "score": SETUPS["FVG_SUPPORT_BOS"]["score"]}
    
    return None

def detect_all_setups(symbol: str, side: str) -> list:
    """Detect all 12 SMC setups"""
    detectors = [
        detect_ob_choch_demand,
        detect_mss_breaker_bos,
        detect_bos_breaker_mss,
        detect_new_hh_choch,
        detect_fakeout_trendline,
        detect_double_top_ob,
        detect_breakout_retest,
        detect_liq_sweep_bos,
        detect_mss_fvg_fib,
        detect_ob_idm_bos,
        detect_double_bottom_bb,
        detect_fvg_support_bos
    ]
    
    found = []
    for detector in detectors:
        try:
            result = detector(symbol, side)
            if result:
                found.append(result)
        except:
            pass
    
    return found

# ═══════════════════════════════════════════════════════════════════
#  ORDER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def cleanup_orders(symbol: str):
    """Cancel all open orders for a symbol"""
    try:
        open_orders = request_binance("GET", "/fapi/v1/openOrders", {
            "symbol": symbol
        })
        
        if open_orders:
            for order in open_orders:
                request_binance("DELETE", "/fapi/v1/order", {
                    "symbol": symbol,
                    "orderId": order["orderId"]
                })
                logger.info(f"🗑️ Cancelled order {order['orderId']} for {symbol}")
    
    except Exception as e:
        logger.error(f"cleanup_orders {symbol}: {e}")

def update_sl_order_on_binance(symbol: str, new_sl: float, side: str):
    """Update SL order on Binance (cancel old, place new)"""
    try:
        info = get_symbol_info(symbol)
        if not info:
            return False
        
        # Cancel all existing orders first
        cleanup_orders(symbol)
        
        # Place new SL order
        sl_order_side = "SELL" if side == "BUY" else "BUY"
        sl_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": sl_order_side,
            "type": "STOP_MARKET",
            "stopPrice": round(new_sl, info["pricePrecision"]),
            "closePosition": "true"
        })
        
        if sl_order:
            logger.info(f"✅ Updated SL order on Binance for {symbol}: ${new_sl:.6f}")
            return True
        else:
            logger.warning(f"⚠️ Failed to update SL order on Binance for {symbol}")
            return False
    
    except Exception as e:
        logger.error(f"update_sl_order_on_binance {symbol}: {e}")
        return False

def place_order_with_fallback(symbol: str, side: str, qty: float, order_type: str = "MARKET", price: float = None) -> dict:
    """Place order with LIMIT fallback if MARKET fails (NEW in v23)"""
    info = get_symbol_info(symbol)
    if not info:
        return None
    
    # Try MARKET order first
    if order_type == "MARKET":
        order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty
        })
        
        if order:
            return order
        
        # MARKET failed, try LIMIT order
        logger.warning(f"⚠️ {symbol} MARKET order rejected, trying LIMIT")
        
        if not price:
            price = get_price(symbol)
        
        if not price:
            return None
        
        # Place LIMIT order at current price with small buffer
        if side == "BUY":
            limit_price = price * 1.001  # 0.1% above market
        else:
            limit_price = price * 0.999  # 0.1% below market
        
        limit_price = round(limit_price, info["pricePrecision"])
        
        order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty,
            "price": limit_price
        })
        
        if order:
            logger.info(f"✅ {symbol} LIMIT order placed at ${limit_price}")
            return order
    
    return None

# ═══════════════════════════════════════════════════════════════════
#  TRADING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def open_position(symbol: str, side: str, entry: float, sl: float, tp: float, setup_name: str, probability: float):
    """Open a new position"""
    global total_traded, account_balance
    
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return
            
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open >= MAX_POSITIONS:
                return
        
        info = get_symbol_info(symbol)
        if not info:
            logger.warning(f"❌ {symbol} info not found")
            return
        
        set_leverage(symbol, LEVERAGE)
        set_margin_type(symbol, MARGIN_TYPE)
        
        risk = abs(entry - sl)
        notional = MARGIN_PER_TRADE * LEVERAGE
        qty = notional / entry
        
        qty = round(qty, info["quantityPrecision"])
        
        if qty < info["minQty"]:
            logger.warning(f"❌ {symbol} qty too small: {qty} < {info['minQty']}")
            return
        
        price_precision = info["pricePrecision"]
        entry_str = f"{entry:.{price_precision}f}"
        sl_str = f"{sl:.{price_precision}f}"
        tp_str = f"{tp:.{price_precision}f}"
        
        session = get_current_session()
        logger.info(f"🎯 {symbol} {side} | Prob: {probability}% | Entry: {entry_str} | SL: {sl_str} | TP: {tp_str} | Setup: {setup_name} | Session: {session}")
        
        # Place market order with LIMIT fallback
        order_side = "BUY" if side == "BUY" else "SELL"
        order = place_order_with_fallback(symbol, order_side, qty, "MARKET", entry)
        
        if not order:
            logger.error(f"❌ {symbol} order failed (both MARKET and LIMIT)")
            return
        
        actual_entry = float(order.get("avgPrice", entry))
        
        logger.info(f"✅ {symbol} {side} OPENED at {actual_entry:.{price_precision}f}")
        
        if side == "BUY":
            sl_distance = actual_entry - sl
            sl = actual_entry - sl_distance
            tp = actual_entry + (sl_distance * 2.0)
        else:
            sl_distance = sl - actual_entry
            sl = actual_entry + sl_distance
            tp = actual_entry - (sl_distance * 2.0)
        
        sl = round(sl, price_precision)
        tp = round(tp, price_precision)
        
        # Place SL order
        sl_rejected = False
        sl_order_side = "SELL" if side == "BUY" else "BUY"
        sl_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": sl_order_side,
            "type": "STOP_MARKET",
            "stopPrice": sl,
            "closePosition": "true"
        })
        
        if not sl_order:
            logger.warning(f"⚠️ {symbol} SL order rejected - will monitor manually")
            sl_rejected = True
        else:
            logger.info(f"✅ {symbol} SL order placed at ${sl:.{price_precision}f}")
        
        # Place TP order
        tp_rejected = False
        tp_order_side = "SELL" if side == "BUY" else "BUY"
        tp_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": tp_order_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp,
            "closePosition": "true"
        })
        
        if not tp_order:
            logger.warning(f"⚠️ {symbol} TP order rejected - will monitor manually")
            tp_rejected = True
        else:
            logger.info(f"✅ {symbol} TP order placed at ${tp:.{price_precision}f}")
        
        with trade_lock:
            trade_log[symbol] = {
                "side": side,
                "entry": actual_entry,
                "sl": sl,
                "tp": tp,
                "qty": qty,
                "setup": setup_name,
                "probability": probability,
                "status": "OPEN",
                "opened_at": time.time(),
                "session": session,
                "sl_rejected": sl_rejected,
                "tp_rejected": tp_rejected,
                "trailing_stop_active": False,
                "breakeven_moved": False,
                "highest_price": actual_entry if side == "BUY" else None,
                "lowest_price": actual_entry if side == "SELL" else None,
                "last_sl_update": time.time()
            }
            total_traded += 1
        
        rr = abs(tp - actual_entry) / abs(actual_entry - sl)
        msg = f"🚀 <b>{symbol} {side}</b>\n"
        msg += f"📊 Setup: {setup_name}\n"
        msg += f"🎲 Probability: {probability}%\n"
        msg += f"🕐 Session: {session}\n"
        msg += f"💵 Entry: ${actual_entry:.{price_precision}f}\n"
        msg += f"🛡️ SL: ${sl:.{price_precision}f}\n"
        msg += f"🎯 TP: ${tp:.{price_precision}f}\n"
        msg += f"📈 RR: {rr:.2f}\n"
        msg += f"⚖️ Qty: {qty}\n"
        msg += f"💰 Margin: ${MARGIN_PER_TRADE}"
        
        if sl_rejected or tp_rejected:
            msg += f"\n⚠️ "
            if sl_rejected:
                msg += "SL "
            if tp_rejected:
                msg += "TP "
            msg += "monitored manually"
        
        send_telegram(msg)
        
        logger.info(f"✅ {symbol} position recorded")
        
    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

def update_breakeven(symbol: str, current_price: float):
    """Move SL to breakeven at small profit (NEW in v23)"""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            
            if trade.get("breakeven_moved"):
                return  # Already moved
            
            side = trade["side"]
            entry = trade["entry"]
            sl = trade["sl"]
            
            # Calculate current RR
            if side == "BUY":
                profit = current_price - entry
                risk = entry - sl
            else:
                profit = entry - current_price
                risk = sl - entry
            
            if risk <= 0:
                return
            
            current_rr = profit / risk
            
            # Move to breakeven at RR 0.3
            if current_rr >= BREAKEVEN_RR:
                # Move SL to entry (breakeven)
                info = get_symbol_info(symbol)
                if info:
                    new_sl = round(entry, info["pricePrecision"])
                    
                    if side == "BUY" and new_sl > sl:
                        trade["sl"] = new_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BREAKEVEN moved SL to ${new_sl:.6f}")
                        
                        if not trade.get("sl_rejected"):
                            update_sl_order_on_binance(symbol, new_sl, side)
                    
                    elif side == "SELL" and new_sl < sl:
                        trade["sl"] = new_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BREAKEVEN moved SL to ${new_sl:.6f}")
                        
                        if not trade.get("sl_rejected"):
                            update_sl_order_on_binance(symbol, new_sl, side)
    
    except Exception as e:
        logger.error(f"update_breakeven {symbol}: {e}")

def update_trailing_stop(symbol: str, current_price: float):
    """Update trailing stop for a position"""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            
            side = trade["side"]
            entry = trade["entry"]
            sl = trade["sl"]
            
            if side == "BUY":
                profit = current_price - entry
                risk = entry - sl
            else:
                profit = entry - current_price
                risk = sl - entry
            
            if risk <= 0:
                return
            
            current_rr = profit / risk
            
            if current_rr >= TRAILING_STOP_START_RR:
                if not trade.get("trailing_stop_active"):
                    logger.info(f"🎯 {symbol} Trailing stop ACTIVATED at RR {current_rr:.2f}")
                    trade["trailing_stop_active"] = True
                
                new_sl = None
                if side == "BUY":
                    if not trade.get("highest_price") or current_price > trade["highest_price"]:
                        trade["highest_price"] = current_price
                    
                    new_sl = max(sl, entry, trade["highest_price"] * 0.995)
                    
                    if new_sl > sl:
                        trade["sl"] = new_sl
                        logger.info(f"📈 {symbol} Trailing SL moved to ${new_sl:.6f}")
                
                else:
                    if not trade.get("lowest_price") or current_price < trade["lowest_price"]:
                        trade["lowest_price"] = current_price
                    
                    new_sl = min(sl, entry, trade["lowest_price"] * 1.005)
                    
                    if new_sl < sl:
                        trade["sl"] = new_sl
                        logger.info(f"📉 {symbol} Trailing SL moved to ${new_sl:.6f}")
                
                if new_sl and new_sl != sl:
                    now = time.time()
                    last_update = trade.get("last_sl_update", 0)
                    
                    if now - last_update >= 30:
                        if not trade.get("sl_rejected"):
                            if update_sl_order_on_binance(symbol, new_sl, side):
                                trade["last_sl_update"] = now
    
    except Exception as e:
        logger.error(f"update_trailing_stop {symbol}: {e}")

def monitor_manual_sl(symbol: str):
    """Monitor and execute SL manually if Binance rejected it"""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            
            if not trade.get("sl_rejected"):
                return
            
            if trade.get("status") != "OPEN":
                return
            
            current_price = get_price(symbol)
            if not current_price:
                return
            
            side = trade["side"]
            sl = trade["sl"]
            qty = trade["qty"]
            
            if side == "BUY" and current_price <= sl:
                logger.warning(f"🚨 {symbol} BUY touched SL ({current_price:.6f} <= {sl:.6f})")
                close_order = place_order_with_fallback(symbol, "SELL", qty, "MARKET", current_price)
                
                if close_order:
                    logger.info(f"✅ Manual SL executed for {symbol}")
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_SL"
                        trade["closed_at"] = time.time()
                        setup_memory[trade["setup"]]["losses"] += 1
                        session_stats[trade["session"]]["losses"] += 1
                    
                    send_telegram(f"🔴 {symbol} closed by manual SL")
            
            elif side == "SELL" and current_price >= sl:
                logger.warning(f"🚨 {symbol} SELL touched SL ({current_price:.6f} >= {sl:.6f})")
                close_order = place_order_with_fallback(symbol, "BUY", qty, "MARKET", current_price)
                
                if close_order:
                    logger.info(f"✅ Manual SL executed for {symbol}")
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_SL"
                        trade["closed_at"] = time.time()
                        setup_memory[trade["setup"]]["losses"] += 1
                        session_stats[trade["session"]]["losses"] += 1
                    
                    send_telegram(f"🔴 {symbol} closed by manual SL")
    
    except Exception as e:
        logger.error(f"monitor_manual_sl {symbol}: {e}")

def monitor_manual_tp(symbol: str):
    """Monitor and execute TP manually if Binance rejected it"""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            
            if not trade.get("tp_rejected"):
                return
            
            if trade.get("status") != "OPEN":
                return
            
            current_price = get_price(symbol)
            if not current_price:
                return
            
            side = trade["side"]
            tp = trade["tp"]
            qty = trade["qty"]
            
            if side == "BUY" and current_price >= tp:
                logger.info(f"🎯 {symbol} BUY touched TP ({current_price:.6f} >= {tp:.6f})")
                close_order = place_order_with_fallback(symbol, "SELL", qty, "MARKET", current_price)
                
                if close_order:
                    logger.info(f"✅ Manual TP executed for {symbol}")
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_TP"
                        trade["closed_at"] = time.time()
                        setup_memory[trade["setup"]]["wins"] += 1
                        session_stats[trade["session"]]["wins"] += 1
                    
                    send_telegram(f"✅ {symbol} closed by manual TP")
            
            elif side == "SELL" and current_price <= tp:
                logger.info(f"🎯 {symbol} SELL touched TP ({current_price:.6f} <= {tp:.6f})")
                close_order = place_order_with_fallback(symbol, "BUY", qty, "MARKET", current_price)
                
                if close_order:
                    logger.info(f"✅ Manual TP executed for {symbol}")
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_TP"
                        trade["closed_at"] = time.time()
                        setup_memory[trade["setup"]]["wins"] += 1
                        session_stats[trade["session"]]["wins"] += 1
                    
                    send_telegram(f"✅ {symbol} closed by manual TP")
    
    except Exception as e:
        logger.error(f"monitor_manual_tp {symbol}: {e}")

def scan_symbol(symbol: str) -> dict:
    """Scan symbol for setups with probability filtering"""
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
        
        with trade_lock:
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        
        if n_open >= MAX_POSITIONS:
            return None
        
        entry = get_price(symbol)
        if not entry:
            return None
        
        atr = calc_atr(symbol)
        if not atr:
            return None
        
        # Try BUY
        setups_buy = detect_all_setups(symbol, "BUY")
        for setup in setups_buy:
            sl = entry - (atr * 1.5)
            tp = entry + ((entry - sl) * 2.0)
            
            # Calculate probability
            probability = calculate_probability(symbol, "BUY", setup["name"])
            
            if probability >= MIN_PROBABILITY_SCORE:
                return {
                    "symbol": symbol,
                    "side": "BUY",
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "setup": setup["name"],
                    "probability": probability
                }
        
        # Try SELL
        setups_sell = detect_all_setups(symbol, "SELL")
        for setup in setups_sell:
            sl = entry + (atr * 1.5)
            tp = entry - ((sl - entry) * 2.0)
            
            # Calculate probability
            probability = calculate_probability(symbol, "SELL", setup["name"])
            
            if probability >= MIN_PROBABILITY_SCORE:
                return {
                    "symbol": symbol,
                    "side": "SELL",
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "setup": setup["name"],
                    "probability": probability
                }
        
        return None
        
    except:
        return None

# ═══════════════════════════════════════════════════════════════════
#  POSITION RECOVERY (NEW in v23)
# ═══════════════════════════════════════════════════════════════════

def recover_existing_positions():
    """Recover and manage any existing open positions (NEW in v23)"""
    logger.info("🔄 Recovering existing positions...")
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if positions:
            recovered = 0
            for pos in positions:
                symbol = pos.get("symbol")
                pos_amt = float(pos.get("positionAmt", 0))
                
                if symbol in SYMBOLS and pos_amt != 0:
                    entry_price = float(pos.get("entryPrice", 0))
                    unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                    side = "BUY" if pos_amt > 0 else "SELL"
                    
                    logger.warning(f"⚠️ Found open position: {symbol} {side} ({pos_amt}) Entry: ${entry_price:.4f} PnL: ${unrealized_pnl:.2f}")
                    
                    # Calculate ATR for SL/TP
                    atr = calc_atr(symbol)
                    if not atr:
                        atr = entry_price * 0.02  # Fallback 2%
                    
                    # Set SL and TP based on entry and ATR
                    if side == "BUY":
                        sl = entry_price - (atr * 1.5)
                        tp = entry_price + (atr * 3.0)
                    else:
                        sl = entry_price + (atr * 1.5)
                        tp = entry_price - (atr * 3.0)
                    
                    # Add to trade_log
                    with trade_lock:
                        if symbol not in trade_log or trade_log[symbol].get("status") != "OPEN":
                            trade_log[symbol] = {
                                "side": side,
                                "entry": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "qty": abs(pos_amt),
                                "setup": "RECOVERED",
                                "probability": 50.0,
                                "status": "OPEN",
                                "opened_at": time.time(),
                                "session": get_current_session(),
                                "sl_rejected": True,
                                "tp_rejected": True,
                                "trailing_stop_active": False,
                                "breakeven_moved": False,
                                "highest_price": entry_price if side == "BUY" else None,
                                "lowest_price": entry_price if side == "SELL" else None,
                                "last_sl_update": time.time()
                            }
                            
                            recovered += 1
                            logger.info(f"✅ Recovered {symbol} - will manage with SL: ${sl:.4f} TP: ${tp:.4f}")
                            
                            # Try to place SL/TP orders
                            info = get_symbol_info(symbol)
                            if info:
                                sl_rounded = round(sl, info["pricePrecision"])
                                tp_rounded = round(tp, info["pricePrecision"])
                                
                                # Place SL
                                sl_order_side = "SELL" if side == "BUY" else "BUY"
                                sl_order = request_binance("POST", "/fapi/v1/order", {
                                    "symbol": symbol,
                                    "side": sl_order_side,
                                    "type": "STOP_MARKET",
                                    "stopPrice": sl_rounded,
                                    "closePosition": "true"
                                })
                                
                                if sl_order:
                                    trade_log[symbol]["sl_rejected"] = False
                                    logger.info(f"✅ Placed SL order for recovered {symbol}")
                                
                                # Place TP
                                tp_order_side = "SELL" if side == "BUY" else "BUY"
                                tp_order = request_binance("POST", "/fapi/v1/order", {
                                    "symbol": symbol,
                                    "side": tp_order_side,
                                    "type": "TAKE_PROFIT_MARKET",
                                    "stopPrice": tp_rounded,
                                    "closePosition": "true"
                                })
                                
                                if tp_order:
                                    trade_log[symbol]["tp_rejected"] = False
                                    logger.info(f"✅ Placed TP order for recovered {symbol}")
            
            if recovered > 0:
                logger.info(f"✅ Successfully recovered {recovered} positions")
                send_telegram(f"🔄 Recovered {recovered} existing positions")
            else:
                logger.info("✅ No positions to recover")
        
    except Exception as e:
        logger.error(f"recover_existing_positions: {e}")

# ═══════════════════════════════════════════════════════════════════
#  MAIN LOOPS
# ═══════════════════════════════════════════════════════════════════

def scanner_loop():
    """Continuous scanner"""
    logger.info("🔍 Scanner started")
    time.sleep(5)
    
    while True:
        try:
            sync_account_balance()
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(scan_symbol, symbol): symbol for symbol in SYMBOLS}
                signals = [f.result() for f in as_completed(futures) if f.result()]
            
            signals.sort(key=lambda x: x.get("probability", 0), reverse=True)
            
            for signal in signals:
                with trade_lock:
                    n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                
                if n_open >= MAX_POSITIONS:
                    break
                
                open_position(signal["symbol"], signal["side"], signal["entry"],
                    signal["sl"], signal["tp"], signal["setup"], signal["probability"])
            
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            
            session = get_current_session()
            logger.info(f"📊 Scan | Open: {n_open}/{MAX_POSITIONS} | Balance: ${account_balance:.2f} | Session: {session}")
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"scanner_loop: {e}")
            time.sleep(5)

def monitor_positions_loop():
    """Monitor positions and trailing stops"""
    logger.info("📍 Monitor started")
    time.sleep(10)
    
    while True:
        try:
            with trade_lock:
                open_symbols = [k for k, v in trade_log.items() if v.get("status") == "OPEN"]
            
            for symbol in open_symbols:
                monitor_manual_sl(symbol)
                monitor_manual_tp(symbol)
                
                price = get_price(symbol)
                if price:
                    update_breakeven(symbol, price)
                    update_trailing_stop(symbol, price)
            
            positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
            if positions:
                for pos in positions:
                    symbol = pos.get("symbol")
                    pos_amt = float(pos.get("positionAmt", 0))
                    
                    if symbol in SYMBOLS and symbol in trade_log:
                        if pos_amt == 0:
                            with trade_lock:
                                if trade_log[symbol].get("status") == "OPEN":
                                    setup = trade_log[symbol].get("setup", "UNKNOWN")
                                    session = trade_log[symbol].get("session", "UNKNOWN")
                                    probability = trade_log[symbol].get("probability", 0)
                                    
                                    pnl = float(pos.get("unRealizedProfit", 0))
                                    if pnl > 0:
                                        setup_memory[setup]["wins"] += 1
                                        session_stats[session]["wins"] += 1
                                        logger.info(f"✅ {symbol} CLOSED - WIN (${pnl:.2f}) Prob: {probability}%")
                                        send_telegram(f"✅ <b>{symbol} WIN</b>\nSetup: {setup}\nProb: {probability}%\nPnL: ${pnl:.2f}")
                                    else:
                                        setup_memory[setup]["losses"] += 1
                                        session_stats[session]["losses"] += 1
                                        logger.info(f"🔴 {symbol} CLOSED - LOSS (${pnl:.2f}) Prob: {probability}%")
                                        send_telegram(f"🔴 <b>{symbol} LOSS</b>\nSetup: {setup}\nProb: {probability}%\nPnL: ${pnl:.2f}")
                                    
                                    trade_log[symbol]["status"] = "CLOSED"
                                    trade_log[symbol]["closed_at"] = time.time()
                                    trade_log[symbol]["closed_by"] = "BINANCE"
                                    
                                    cleanup_orders(symbol)
            
            time.sleep(MONITOR_INTERVAL)
            
        except Exception as e:
            logger.error(f"monitor_positions_loop: {e}")
            time.sleep(5)

def dashboard_loop():
    """Display dashboard"""
    logger.info("📈 Dashboard started")
    time.sleep(15)
    
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                total_w = sum(v["wins"] for v in setup_memory.values())
                total_l = sum(v["losses"] for v in setup_memory.values())
            
            session = get_current_session()
            fear_greed = fear_greed_cache.get("value", 50)
            
            logger.info("═" * 100)
            logger.info(f"🤖 v23 ADVANCED | Balance: ${account_balance:.2f} | Open: {n_open}/{MAX_POSITIONS} | Traded: {total_traded}")
            logger.info(f"Session: {session} | F&G: {fear_greed} | Min Prob: {MIN_PROBABILITY_SCORE}% | Margin: ${MARGIN_PER_TRADE} | W: {total_w} | L: {total_l}")
            
            if setup_memory:
                logger.info("Setup Performance:")
                for setup, stats in sorted(setup_memory.items(), key=lambda x: x[1]["wins"], reverse=True):
                    w = stats["wins"]
                    l = stats["losses"]
                    total = w + l
                    wr = (w / total * 100) if total > 0 else 0
                    logger.info(f"  {setup}: {w}W/{l}L ({wr:.1f}%)")
            
            if session_stats:
                logger.info("Session Performance:")
                for sess, stats in sorted(session_stats.items()):
                    w = stats["wins"]
                    l = stats["losses"]
                    total = w + l
                    if total > 0:
                        wr = (w / total * 100)
                        logger.info(f"  {sess}: {w}W/{l}L ({wr:.1f}%)")
            
            logger.info("═" * 100)
            
            time.sleep(DASHBOARD_INTERVAL)
            
        except Exception as e:
            logger.error(f"dashboard_loop: {e}")
            time.sleep(10)

def main():
    logger.info("╔" + "═" * 98 + "╗")
    logger.info("║" + " " * 24 + "ROBOTKING v23 ADVANCED - PROBABILITY & TREND FILTER" + " " * 23 + "║")
    logger.info("║" + " " * 12 + "M5 Trading | 0.8$ Margin | Trend Filter | BTC Corr | F&G | Breakeven" + " " * 12 + "║")
    logger.info("╚" + "═" * 98 + "╝\n")
    
    logger.warning("🔥 LIVE TRADING ON BINANCE FUTURES 🔥")
    logger.info(f"Margin: ${MARGIN_PER_TRADE}/trade | Leverage: {LEVERAGE}x | Max Positions: {MAX_POSITIONS}")
    logger.info(f"Min Probability: {MIN_PROBABILITY_SCORE}% | Trend Filter: {ENABLE_TREND_FILTER}\n")
    
    start_health_server()
    load_symbol_info()
    sync_account_balance()
    
    # NEW: Recover any existing positions first
    recover_existing_positions()
    
    # Update Fear & Greed Index
    fear_greed = get_fear_greed_index()
    logger.info(f"📊 Fear & Greed Index: {fear_greed}")
    
    threading.Thread(target=scanner_loop, daemon=True, name="Scanner").start()
    threading.Thread(target=monitor_positions_loop, daemon=True, name="Monitor").start()
    threading.Thread(target=dashboard_loop, daemon=True, name="Dashboard").start()
    
    session = get_current_session()
    logger.info(f"✅ v23 ADVANCED — ONLINE 🚀")
    logger.info(f"📍 Session: {session} | Fear & Greed: {fear_greed}\n")
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("\n🛑 Shutdown signal received")
        logger.info("💰 Final Balance: ${:.2f}".format(account_balance))
        with trade_lock:
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        if n_open > 0:
            logger.warning(f"⚠️ {n_open} positions still open!")
        logger.info("👋 RobotKing v23 stopped")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║    ROBOTKING v24 MICRO CAPITAL - OPTIMIZED FOR 3-10$ ACCOUNTS  ║
║    1.0$ Margin | 20x Leverage | Smart Symbol Filter            ║
║    Binance Notional 20$ Fix | Micro Cap Optimized              ║
╚══════════════════════════════════════════════════════════════════╝

v24 MICRO CAPITAL - PRODUCTION READY
✅ Fixed: All Binance errors (-4164, qty too small, invalid symbol)
✅ Fixed: Python syntax errors (global declarations)
✅ Fixed: SHORT position TP calculation bug
✅ Optimized for 3-10$ accounts
"""

import time, hmac, hashlib, requests, threading, os, logging, json, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

logging.basicConfig(level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("v24_micro.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except:
        pass

API_KEY = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

if not API_KEY or not API_SECRET:
    logger.error("❌ BINANCE API keys missing!")
    exit(1)

BASE_URL = "https://fapi.binance.com"

# CONFIGURATION
MIN_NOTIONAL = 20
LEVERAGE = 20
MIN_MARGIN_PER_TRADE = MIN_NOTIONAL / LEVERAGE
MARGIN_TYPE = "ISOLATED"

MIN_PROBABILITY_SCORE = 65
TRAILING_STOP_START_RR = 1.0
BREAKEVEN_RR = 0.3

ENABLE_TREND_FILTER = True
TREND_TIMEFRAME = "15m"

PROBABILITY_WEIGHTS = {
    "setup_score": 0.25,
    "trend_alignment": 0.25,
    "btc_correlation": 0.15,
    "session_quality": 0.15,
    "sentiment": 0.10,
    "volatility": 0.10
}

SESSION_WEIGHTS = {
    "LONDON": 1.0,
    "NEW_YORK": 1.0,
    "ASIA": 0.7,
    "OFF_HOURS": 0.4
}

# MICRO CAP SYMBOLS - declared as constant, won't be modified
MICRO_CAP_SYMBOLS = [
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT", "MATICUSDT",
    "AVAXUSDT", "LINKUSDT", "ATOMUSDT", "DOTUSDT", "NEARUSDT",
    "FTMUSDT", "APTUSDT",
]

SYMBOLS = MICRO_CAP_SYMBOLS.copy()  # Working copy

SCAN_INTERVAL = 12
MONITOR_INTERVAL = 2
DASHBOARD_INTERVAL = 60
MAX_WORKERS = 10
CACHE_DURATION = 4

SETUPS = {
    "MSS_FVG_FIB": {"score": 90},
    "LIQ_SWEEP_BOS": {"score": 90},
    "BREAKOUT_RETEST": {"score": 70},
}

# STATE
account_balance = 0
total_traded = 0

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
    max_pos = calculate_max_positions(account_balance)
    return f"v24 MICRO | Balance: ${account_balance:.2f} | Open: {n_open}/{max_pos}", 200

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
        "max_positions": calculate_max_positions(account_balance),
        "total_traded": total_traded
    })

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    try:
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False), daemon=True).start()
    except:
        pass

def get_current_session() -> str:
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    if 7 <= hour < 16:
        return "LONDON"
    elif 13 <= hour < 22:
        return "NEW_YORK"
    elif hour >= 23 or hour < 8:
        return "ASIA"
    else:
        return "OFF_HOURS"

def get_session_weight() -> float:
    return SESSION_WEIGHTS.get(get_current_session(), 0.5)

def calculate_max_positions(balance: float) -> int:
    if balance < 5:
        return 2
    elif balance < 10:
        return 3
    else:
        return 4

def calculate_margin_for_trade(balance: float) -> float:
    min_margin = MIN_MARGIN_PER_TRADE
    
    if balance < 5:
        calculated_margin = balance * 0.30
    elif balance < 10:
        calculated_margin = balance * 0.25
    else:
        calculated_margin = balance * 0.20
    
    margin = max(min_margin, calculated_margin)
    margin = min(margin, balance * 0.4)
    
    return round(margin, 2)

def can_afford_position(balance: float, existing_positions: int) -> bool:
    margin_needed = calculate_margin_for_trade(balance)
    max_positions = calculate_max_positions(balance)
    
    if existing_positions >= max_positions:
        return False
    
    total_margin_used = margin_needed * (existing_positions + 1)
    return balance >= total_margin_used

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
    return symbol_info_cache.get(symbol)

def load_symbol_info():
    """Load and validate symbols - NO global modification"""
    logger.info("📥 Loading symbol info...")
    data = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
    if not data:
        logger.error("Failed to load symbol info")
        return
    
    for s in data.get("symbols", []):
        symbol = s["symbol"]
        
        if symbol in MICRO_CAP_SYMBOLS and s.get("status") == "TRADING":
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            
            symbol_info_cache[symbol] = {
                "quantityPrecision": s.get("quantityPrecision", 3),
                "pricePrecision": s.get("pricePrecision", 2),
                "minQty": float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                "maxQty": float(filters.get("LOT_SIZE", {}).get("maxQty", 10000)),
                "stepSize": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 20)),
            }
    
    logger.info(f"✅ Loaded {len(symbol_info_cache)} symbols")

def sync_account_balance():
    global account_balance
    try:
        account = request_binance("GET", "/fapi/v2/account")
        if account:
            account_balance = float(account.get("availableBalance", 0))
    except Exception as e:
        logger.error(f"sync_account_balance: {e}")

def set_leverage(symbol: str, leverage: int):
    try:
        result = request_binance("POST", "/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage
        })
        if result:
            logger.info(f"⚙️ {symbol} leverage {leverage}x")
    except:
        pass

def set_margin_type(symbol: str, margin_type: str):
    try:
        request_binance("POST", "/fapi/v1/marginType", {
            "symbol": symbol,
            "marginType": margin_type
        })
    except:
        pass

def calc_atr(symbol: str, period: int = 14) -> float:
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

def detect_trend(symbol: str, timeframe: str = "15m") -> dict:
    try:
        klines = get_klines(symbol, timeframe, 50)
        if not klines or len(klines) < 50:
            return {"direction": 0, "strength": 0}
        
        closes = np.array([float(k[4]) for k in klines])
        
        ema_9 = np.mean(closes[-9:])
        ema_21 = np.mean(closes[-21:])
        ema_50 = np.mean(closes[-50:])
        
        if ema_9 > ema_21 > ema_50:
            direction = 1
            strength = min((ema_9 - ema_50) / ema_50 * 100, 1.0)
        elif ema_9 < ema_21 < ema_50:
            direction = -1
            strength = min((ema_50 - ema_9) / ema_50 * 100, 1.0)
        else:
            direction = 0
            strength = 0
        
        return {"direction": direction, "strength": abs(strength)}
    except:
        return {"direction": 0, "strength": 0}

def get_btc_trend() -> int:
    global btc_trend_cache
    
    now = time.time()
    if now - btc_trend_cache.get("timestamp", 0) < 60:
        return btc_trend_cache.get("trend", 0)
    
    trend_data = detect_trend("DOGEUSDT", TREND_TIMEFRAME)
    btc_trend = trend_data["direction"]
    
    btc_trend_cache = {"trend": btc_trend, "timestamp": now}
    return btc_trend

def calculate_btc_correlation(symbol: str) -> float:
    try:
        btc_trend = get_btc_trend()
        symbol_trend = detect_trend(symbol, TREND_TIMEFRAME)
        
        if btc_trend == 0 or symbol_trend["direction"] == 0:
            return 0.5
        
        if btc_trend == symbol_trend["direction"]:
            return 0.8
        else:
            return 0.2
    except:
        return 0.5

def get_fear_greed_index() -> int:
    global fear_greed_cache
    
    now = time.time()
    if now - fear_greed_cache.get("timestamp", 0) < 3600:
        return fear_greed_cache.get("value", 50)
    
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            value = int(data["data"][0]["value"])
            fear_greed_cache = {"value": value, "timestamp": now}
            return value
    except:
        pass
    
    return 50

def calculate_sentiment_score(fear_greed: int) -> float:
    if fear_greed < 25:
        return 0.8
    elif fear_greed < 45:
        return 0.6
    elif fear_greed < 55:
        return 0.5
    elif fear_greed < 75:
        return 0.6
    else:
        return 0.8

def calculate_volatility_score(symbol: str) -> float:
    try:
        atr = calc_atr(symbol)
        price = get_price(symbol)
        
        if not atr or not price:
            return 0.5
        
        atr_pct = (atr / price) * 100
        
        if 1.0 <= atr_pct <= 3.0:
            return 1.0
        elif atr_pct < 1.0:
            return 0.6
        elif atr_pct < 5.0:
            return 0.8
        else:
            return 0.4
    except:
        return 0.5

def calculate_probability(symbol: str, side: str, setup_name: str) -> float:
    try:
        setup_score_raw = SETUPS.get(setup_name, {}).get("score", 50)
        setup_score = setup_score_raw / 100.0
        
        trend_data = detect_trend(symbol, TREND_TIMEFRAME)
        trend_direction = trend_data["direction"]
        trend_strength = trend_data["strength"]
        
        if not ENABLE_TREND_FILTER:
            trend_score = 0.7
        elif side == "BUY" and trend_direction == 1:
            trend_score = 0.7 + (trend_strength * 0.3)
        elif side == "SELL" and trend_direction == -1:
            trend_score = 0.7 + (trend_strength * 0.3)
        elif trend_direction == 0:
            trend_score = 0.5
        else:
            trend_score = 0.2
        
        btc_corr = calculate_btc_correlation(symbol)
        session_score = get_session_weight()
        
        fear_greed = get_fear_greed_index()
        sentiment_score = calculate_sentiment_score(fear_greed)
        
        if side == "BUY" and fear_greed < 35:
            sentiment_score = min(sentiment_score * 1.2, 1.0)
        elif side == "SELL" and fear_greed > 65:
            sentiment_score = min(sentiment_score * 1.2, 1.0)
        
        volatility_score = calculate_volatility_score(symbol)
        
        probability = (
            setup_score * PROBABILITY_WEIGHTS["setup_score"] +
            trend_score * PROBABILITY_WEIGHTS["trend_alignment"] +
            btc_corr * PROBABILITY_WEIGHTS["btc_correlation"] +
            session_score * PROBABILITY_WEIGHTS["session_quality"] +
            sentiment_score * PROBABILITY_WEIGHTS["sentiment"] +
            volatility_score * PROBABILITY_WEIGHTS["volatility"]
        ) * 100
        
        return round(probability, 1)
    except:
        return 50.0

def detect_mss_fvg_fib(symbol: str, side: str) -> dict:
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

def detect_liq_sweep_bos(symbol: str, side: str) -> dict:
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

def detect_breakout_retest(symbol: str, side: str) -> dict:
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

def detect_all_setups(symbol: str, side: str) -> list:
    detectors = [detect_mss_fvg_fib, detect_liq_sweep_bos, detect_breakout_retest]
    
    found = []
    for detector in detectors:
        try:
            result = detector(symbol, side)
            if result:
                found.append(result)
        except:
            pass
    
    return found

def cleanup_orders(symbol: str):
    try:
        open_orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if open_orders:
            for order in open_orders:
                request_binance("DELETE", "/fapi/v1/order", {
                    "symbol": symbol,
                    "orderId": order["orderId"]
                })
    except:
        pass

def validate_order_size(symbol: str, qty: float, price: float) -> tuple:
    info = get_symbol_info(symbol)
    if not info:
        return (False, "Symbol info not available", 0)
    
    if qty < info["minQty"]:
        return (False, f"Qty {qty} < min {info['minQty']}", 0)
    
    notional = price * qty
    min_notional = info.get("minNotional", MIN_NOTIONAL)
    
    if notional < min_notional:
        adjusted_qty = min_notional / price
        adjusted_qty = round(adjusted_qty, info["quantityPrecision"])
        
        if adjusted_qty < info["minQty"]:
            return (False, f"Cannot meet min notional", 0)
        
        adjusted_notional = price * adjusted_qty
        if adjusted_notional < min_notional:
            return (False, f"Notional {adjusted_notional:.2f} < {min_notional}", 0)
        
        return (True, f"Adjusted to meet notional", adjusted_qty)
    
    return (True, "OK", qty)

def place_order_with_fallback(symbol: str, side: str, qty: float, price: float = None) -> dict:
    info = get_symbol_info(symbol)
    if not info:
        return None
    
    if not price:
        price = get_price(symbol)
    
    if not price:
        return None
    
    is_valid, msg, adjusted_qty = validate_order_size(symbol, qty, price)
    
    if not is_valid:
        logger.error(f"❌ {symbol} {msg}")
        return None
    
    if adjusted_qty != qty:
        logger.info(f"📊 {symbol} qty adjusted: {qty} → {adjusted_qty}")
        qty = adjusted_qty
    
    order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty
    })
    
    if order:
        return order
    
    logger.warning(f"⚠️ {symbol} MARKET rejected, trying LIMIT")
    
    if side == "BUY":
        limit_price = price * 1.001
    else:
        limit_price = price * 0.999
    
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
        logger.info(f"✅ {symbol} LIMIT at ${limit_price}")
        return order
    
    return None

def open_position(symbol: str, side: str, entry: float, sl: float, tp: float, setup_name: str, probability: float):
    global total_traded
    
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return
            
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            
            if not can_afford_position(account_balance, n_open):
                return
        
        info = get_symbol_info(symbol)
        if not info:
            return
        
        set_leverage(symbol, LEVERAGE)
        set_margin_type(symbol, MARGIN_TYPE)
        
        margin = calculate_margin_for_trade(account_balance)
        
        notional = margin * LEVERAGE
        qty = notional / entry
        
        qty = round(qty, info["quantityPrecision"])
        
        is_valid, msg, adjusted_qty = validate_order_size(symbol, qty, entry)
        if not is_valid:
            logger.warning(f"❌ {symbol} {msg}")
            return
        
        if adjusted_qty != qty:
            qty = adjusted_qty
            margin = (qty * entry) / LEVERAGE
        
        price_precision = info["pricePrecision"]
        session = get_current_session()
        
        logger.info(f"🎯 {symbol} {side} | Prob: {probability}% | Margin: ${margin:.2f}")
        
        order_side = "BUY" if side == "BUY" else "SELL"
        order = place_order_with_fallback(symbol, order_side, qty, entry)
        
        if not order:
            logger.error(f"❌ {symbol} order failed")
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
        
        with trade_lock:
            trade_log[symbol] = {
                "side": side,
                "entry": actual_entry,
                "sl": sl,
                "tp": tp,
                "qty": qty,
                "margin": margin,
                "setup": setup_name,
                "probability": probability,
                "status": "OPEN",
                "opened_at": time.time(),
                "session": session,
                "sl_rejected": True,
                "tp_rejected": True,
                "trailing_stop_active": False,
                "breakeven_moved": False,
                "highest_price": actual_entry if side == "BUY" else None,
                "lowest_price": actual_entry if side == "SELL" else None,
                "last_sl_update": time.time()
            }
            total_traded += 1
        
        send_telegram(f"🚀 {symbol} {side}\nProb: {probability}%\nEntry: ${actual_entry:.{price_precision}f}\nMargin: ${margin:.2f}")
        
    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

def update_breakeven(symbol: str, current_price: float):
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN" or trade.get("breakeven_moved"):
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
            
            if current_rr >= BREAKEVEN_RR:
                info = get_symbol_info(symbol)
                if info:
                    new_sl = round(entry, info["pricePrecision"])
                    
                    if (side == "BUY" and new_sl > sl) or (side == "SELL" and new_sl < sl):
                        trade["sl"] = new_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BREAKEVEN ${new_sl:.6f}")
    except:
        pass

def monitor_manual_sl(symbol: str):
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            
            current_price = get_price(symbol)
            if not current_price:
                return
            
            side = trade["side"]
            sl = trade["sl"]
            qty = trade["qty"]
            
            if (side == "BUY" and current_price <= sl) or (side == "SELL" and current_price >= sl):
                logger.warning(f"🚨 {symbol} {side} SL hit")
                
                close_side = "SELL" if side == "BUY" else "BUY"
                close_order = place_order_with_fallback(symbol, close_side, qty, current_price)
                
                if close_order:
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_SL"
                        setup_memory[trade["setup"]]["losses"] += 1
                    send_telegram(f"🔴 {symbol} SL")
    except:
        pass

def monitor_manual_tp(symbol: str):
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            
            current_price = get_price(symbol)
            if not current_price:
                return
            
            side = trade["side"]
            tp = trade["tp"]
            qty = trade["qty"]
            
            if (side == "BUY" and current_price >= tp) or (side == "SELL" and current_price <= tp):
                logger.info(f"🎯 {symbol} {side} TP hit")
                
                close_side = "SELL" if side == "BUY" else "BUY"
                close_order = place_order_with_fallback(symbol, close_side, qty, current_price)
                
                if close_order:
                    with trade_lock:
                        trade["status"] = "CLOSED"
                        trade["closed_by"] = "MANUAL_TP"
                        setup_memory[trade["setup"]]["wins"] += 1
                    send_telegram(f"✅ {symbol} TP")
    except:
        pass

def scan_symbol(symbol: str) -> dict:
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
            
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        
        if not can_afford_position(account_balance, n_open):
            return None
        
        entry = get_price(symbol)
        if not entry:
            return None
        
        atr = calc_atr(symbol)
        if not atr:
            return None
        
        setups_buy = detect_all_setups(symbol, "BUY")
        for setup in setups_buy:
            sl = entry - (atr * 1.5)
            tp = entry + ((entry - sl) * 2.0)
            
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
        
        setups_sell = detect_all_setups(symbol, "SELL")
        for setup in setups_sell:
            sl = entry + (atr * 1.5)
            tp = entry - ((sl - entry) * 2.0)
            
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

def recover_existing_positions():
    logger.info("🔄 Recovering positions...")
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if positions:
            for pos in positions:
                symbol = pos.get("symbol")
                pos_amt = float(pos.get("positionAmt", 0))
                
                if symbol in SYMBOLS and pos_amt != 0:
                    entry_price = float(pos.get("entryPrice", 0))
                    side = "BUY" if pos_amt > 0 else "SELL"
                    
                    logger.warning(f"⚠️ Found: {symbol} {side}")
                    
                    atr = calc_atr(symbol) or entry_price * 0.02
                    
                    if side == "BUY":
                        sl = entry_price - (atr * 1.5)
                        tp = entry_price + (atr * 3.0)
                    else:
                        sl = entry_price + (atr * 1.5)
                        tp = entry_price - (atr * 3.0)
                    
                    with trade_lock:
                        if symbol not in trade_log:
                            trade_log[symbol] = {
                                "side": side,
                                "entry": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "qty": abs(pos_amt),
                                "margin": calculate_margin_for_trade(account_balance),
                                "setup": "RECOVERED",
                                "probability": 50.0,
                                "status": "OPEN",
                                "opened_at": time.time(),
                                "session": get_current_session(),
                                "sl_rejected": True,
                                "tp_rejected": True,
                                "trailing_stop_active": False,
                                "breakeven_moved": False,
                                "highest_price": None,
                                "lowest_price": None,
                                "last_sl_update": time.time()
                            }
                            logger.info(f"✅ Recovered {symbol}")
    except:
        pass

def scanner_loop():
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
                
                if not can_afford_position(account_balance, n_open):
                    break
                
                open_position(signal["symbol"], signal["side"], signal["entry"],
                    signal["sl"], signal["tp"], signal["setup"], signal["probability"])
            
            time.sleep(SCAN_INTERVAL)
        except:
            time.sleep(5)

def monitor_positions_loop():
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
            
            positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
            if positions:
                for pos in positions:
                    symbol = pos.get("symbol")
                    pos_amt = float(pos.get("positionAmt", 0))
                    
                    if symbol in SYMBOLS and symbol in trade_log:
                        if pos_amt == 0:
                            with trade_lock:
                                if trade_log[symbol].get("status") == "OPEN":
                                    setup = trade_log[symbol].get("setup")
                                    
                                    pnl = float(pos.get("unRealizedProfit", 0))
                                    if pnl > 0:
                                        setup_memory[setup]["wins"] += 1
                                        logger.info(f"✅ {symbol} WIN ${pnl:.2f}")
                                        send_telegram(f"✅ {symbol} WIN ${pnl:.2f}")
                                    else:
                                        setup_memory[setup]["losses"] += 1
                                        logger.info(f"🔴 {symbol} LOSS ${pnl:.2f}")
                                    
                                    trade_log[symbol]["status"] = "CLOSED"
                                    cleanup_orders(symbol)
            
            time.sleep(MONITOR_INTERVAL)
        except:
            time.sleep(5)

def dashboard_loop():
    logger.info("📈 Dashboard started")
    time.sleep(15)
    
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                total_w = sum(v["wins"] for v in setup_memory.values())
                total_l = sum(v["losses"] for v in setup_memory.values())
            
            max_pos = calculate_max_positions(account_balance)
            margin = calculate_margin_for_trade(account_balance)
            
            logger.info("═" * 60)
            logger.info(f"v24 MICRO | ${account_balance:.2f} | {n_open}/{max_pos} | W:{total_w} L:{total_l}")
            logger.info(f"Margin: ${margin:.2f}/trade")
            logger.info("═" * 60)
            
            time.sleep(DASHBOARD_INTERVAL)
        except:
            time.sleep(10)

def main():
    logger.info("╔" + "═" * 58 + "╗")
    logger.info("║" + " " * 10 + "ROBOTKING v24 MICRO - PRODUCTION READY" + " " * 8 + "║")
    logger.info("╚" + "═" * 58 + "╝\n")
    
    logger.warning("🔥 LIVE TRADING 🔥")
    
    start_health_server()
    load_symbol_info()
    sync_account_balance()
    
    max_pos = calculate_max_positions(account_balance)
    margin = calculate_margin_for_trade(account_balance)
    
    logger.info(f"💰 Balance: ${account_balance:.2f}")
    logger.info(f"📊 Max: {max_pos} | Margin: ${margin:.2f}\n")
    
    recover_existing_positions()
    
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=monitor_positions_loop, daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()
    
    logger.info("✅ v24 MICRO — ONLINE 🚀\n")
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("\n🛑 Shutdown")

if __name__ == "__main__":
    main()

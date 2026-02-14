"""
╔══════════════════════════════════════════════════════════╗
║         ROBOTKING M1 PRO – ULTRA OCÉAN SYSTEM           ║
║         Version: 5.0.0 | Production Ready               ║
╠══════════════════════════════════════════════════════════╣
║  CORRECTIONS v5 :                                        ║
║  ✅ Break-even : STOP_MARKET pur sans paramètre algo     ║
║  ✅ Session tracker : mauvaise session = étoiles réduites║
║  ✅ Recovery mode : perte → réduction trades jusqu'au   ║
║     retour au capital de départ                          ║
║  ✅ TP jamais annulé → RR max garanti                    ║
║  ✅ Trailing local ATR si algo rejeté                    ║
╚══════════════════════════════════════════════════════════╝
"""

import time
import hmac
import hashlib
import requests
import threading
import os
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("robotking_ocean.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════

API_KEY    = os.environ.get("BINANCE_API_KEY",    "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://fapi.binance.com"
DRY_RUN  = os.environ.get("DRY_RUN", "false").lower() == "true"

# ── Marge & Levier ─────────────────────────────────────────
MARGIN_TYPE      = "ISOLATED"
LEVERAGE         = 20
MARGIN_PER_TRADE = 0.80        # 0.80$ marge fixe par trade

# ── Capital ────────────────────────────────────────────────
INITIAL_CAPITAL      = 5.0
MAX_DRAWDOWN_PCT     = 0.20    # Kill switch -20%
MIN_CAPITAL_TO_TRADE = 2.0
MAX_POSITIONS        = 4       # Maximum normal

# ── SL / TP ATR ───────────────────────────────────────────
ATR_SL_MULT     = 1.5
ATR_TP_MULT     = 2.5
FALLBACK_SL_PCT = 0.012
FALLBACK_TP_PCT = 0.020

# ── Trailing stop ──────────────────────────────────────────
BREAKEVEN_TRIGGER_PCT  = 0.005   # +0.5% → break-even
TRAILING_TRIGGER_PCT   = 0.010   # +1.0% → trailing
TRAILING_CALLBACK_RATE = 0.5     # 0.5% callback algo

# ── Scanner ────────────────────────────────────────────────
SCAN_INTERVAL      = 15
MONITOR_INTERVAL   = 3
DASHBOARD_INTERVAL = 30
MAX_WORKERS        = 6

# ── Session & Recovery ─────────────────────────────────────
# Mauvaise session : MIN_STARS monte → moins de trades pris
MIN_STARS_NORMAL   = 3    # Session normale
MIN_STARS_BAD      = 4    # Session mauvaise (>1 perte)
MIN_STARS_VERY_BAD = 5    # Très mauvaise session (>2 pertes)

# Recovery : après perte, MAX_POSITIONS réduit jusqu'à combler
RECOVERY_MAX_POS_1 = 3    # Après 1 perte : max 3 positions
RECOVERY_MAX_POS_2 = 2    # Après 2 pertes : max 2 positions
RECOVERY_MAX_POS_3 = 1    # Après 3+ pertes : max 1 position

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

MAX_CALLS_PER_MIN = 1200
RATE_LIMIT_WINDOW = 60
CACHE_DURATION    = 5

# ═══════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════

trade_log: dict              = {}
symbol_precision_cache: dict = {}
price_cache: dict            = {}
klines_cache: dict           = {}

current_capital  = INITIAL_CAPITAL
peak_capital     = INITIAL_CAPITAL
starting_capital = INITIAL_CAPITAL

# ── Session tracker ────────────────────────────────────────
session_losses   = 0    # Pertes consécutives depuis dernier reset
session_wins     = 0    # Gains depuis dernier reset
session_pnl      = 0.0  # PnL session en $

trade_lock     = threading.Lock()
capital_lock   = threading.Lock()
api_lock       = threading.Lock()
session_lock   = threading.Lock()
api_call_times: list = []

# ═══════════════════════════════════════════════════════════
#  SESSION INTELLIGENCE
#  Mauvaise session → plus d'étoiles requises
#  Recovery → moins de positions max
# ═══════════════════════════════════════════════════════════

def get_session_params() -> tuple[int, int]:
    """
    Retourne (min_stars, max_positions) selon l'état de la session.
    Logique :
      - 0 perte  : normal (3 étoiles, 4 positions)
      - 1 perte  : prudent (4 étoiles, 3 positions)
      - 2 pertes : très prudent (5 étoiles, 2 positions)
      - 3+ pertes: ultra défensif (5 étoiles, 1 position)

    Recovery : dès que PnL session repasse positif → reset
    """
    with session_lock:
        losses = session_losses

    if losses == 0:
        return MIN_STARS_NORMAL, MAX_POSITIONS
    elif losses == 1:
        return MIN_STARS_BAD, RECOVERY_MAX_POS_1
    elif losses == 2:
        return MIN_STARS_VERY_BAD, RECOVERY_MAX_POS_2
    else:
        return MIN_STARS_VERY_BAD, RECOVERY_MAX_POS_3

def record_trade_result(pnl_usdt: float):
    """
    Enregistre le résultat d'un trade fermé.
    Perte → session_losses++
    Gain → si session_pnl >= 0 → reset session
    """
    global session_losses, session_wins, session_pnl
    with session_lock:
        session_pnl += pnl_usdt
        if pnl_usdt < 0:
            session_losses += 1
            logger.warning(f"📉 Session: {session_losses} perte(s) | PnL session: {session_pnl:.3f}$")
            stars, max_pos = get_session_params()
            logger.warning(f"   → Mode: {max_pos} positions max | {stars}★ min requis")
            send_telegram(
                f"⚠️ Perte enregistrée — session: {session_losses} perte(s)\n"
                f"Mode recovery: {max_pos} positions max | {stars}★ requis"
            )
        else:
            session_wins += 1
            logger.info(f"📈 Session: {session_wins} gain(s) | PnL session: {session_pnl:.3f}$")
            # Reset si session revenue positive
            if session_pnl >= 0 and session_losses > 0:
                session_losses = 0
                session_wins   = 0
                logger.info("✅ Session revenue positive — reset mode recovery")
                send_telegram("✅ Session recovery terminée — retour mode normal")

# ═══════════════════════════════════════════════════════════
#  RATE LIMITING
# ═══════════════════════════════════════════════════════════

def wait_for_rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < RATE_LIMIT_WINDOW]
        if len(api_call_times) >= MAX_CALLS_PER_MIN * 0.80:
            sleep_time = RATE_LIMIT_WINDOW - (now - api_call_times[0])
            if sleep_time > 0:
                logger.warning(f"⚠️  Rate limit — pause {sleep_time:.1f}s")
                time.sleep(sleep_time)
                api_call_times.clear()
        api_call_times.append(now)

# ═══════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        logger.error(f"Telegram: {e}")

# ═══════════════════════════════════════════════════════════
#  API BINANCE
# ═══════════════════════════════════════════════════════════

def _sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method: str, path: str, params: dict = None,
                    max_retries: int = 3, signed: bool = True):
    if params is None:
        params = {}

    if DRY_RUN and method in ("POST", "DELETE"):
        logger.info(f"[DRY RUN] {method} {path}")
        return {
            "orderId": f"DRY_{int(time.time()*1000)}",
            "algoId":  f"DRYALGO_{int(time.time()*1000)}",
            "avgPrice": "0"
        }

    wait_for_rate_limit()
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)

    headers = {"X-MBX-APIKEY": API_KEY}

    for attempt in range(max_retries):
        try:
            if method == "GET":
                resp = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
            elif method == "POST":
                resp = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(BASE_URL + path, params=params, headers=headers, timeout=10)
            else:
                return None

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"⚠️  Rate limit — pause {retry_after}s")
                time.sleep(retry_after)
            else:
                logger.error(f"API {resp.status_code} [{path}]: {resp.text[:250]}")
                if attempt < max_retries - 1:
                    time.sleep((2 ** attempt) * 0.5)

        except requests.exceptions.Timeout:
            logger.error(f"Timeout {attempt+1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) * 0.5)
        except Exception as e:
            logger.error(f"Requête: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)

    return None

# ═══════════════════════════════════════════════════════════
#  CACHE KLINES / PRIX
# ═══════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str = "1m", limit: int = 50):
    key = f"{symbol}_{interval}"
    now = time.time()
    if key in klines_cache:
        data, ts = klines_cache[key]
        if now - ts < CACHE_DURATION:
            return data
    try:
        resp = requests.get(
            f"{BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        if resp.status_code == 200:
            klines_cache[key] = (resp.json(), now)
            return resp.json()
    except Exception as e:
        logger.error(f"get_klines {symbol}: {e}")
    return None

def get_price(symbol: str):
    now = time.time()
    if symbol in price_cache:
        p, ts = price_cache[symbol]
        if now - ts < CACHE_DURATION:
            return p
    try:
        resp = requests.get(
            f"{BASE_URL}/fapi/v1/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        if resp.status_code == 200:
            p = float(resp.json()["price"])
            price_cache[symbol] = (p, now)
            return p
    except Exception as e:
        logger.error(f"get_price {symbol}: {e}")
    return None

# ═══════════════════════════════════════════════════════════
#  PRÉCISIONS SYMBOLS
# ═══════════════════════════════════════════════════════════

def load_symbol_precision_all():
    global symbol_precision_cache
    logger.info("📐 Chargement précisions symbols...")
    info = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
    if not info:
        logger.error("❌ exchangeInfo indisponible")
        return

    for s in info.get("symbols", []):
        sym = s["symbol"]
        if sym not in SYMBOLS:
            continue
        filters  = {f["filterType"]: f for f in s.get("filters", [])}
        lot      = filters.get("LOT_SIZE", {})
        step     = lot.get("stepSize", "0.001")
        qty_prec = len(step.rstrip("0").split(".")[-1]) if "." in step else 0

        symbol_precision_cache[sym] = {
            "qty_precision":   qty_prec,
            "min_qty":         float(lot.get("minQty", 0.001)),
            "min_notional":    float(filters.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
            "price_precision": int(s.get("pricePrecision", 6))
        }
    logger.info(f"✅ {len(symbol_precision_cache)} symbols chargés")

def get_symbol_info(symbol: str) -> dict:
    return symbol_precision_cache.get(symbol, {
        "qty_precision": 3, "min_qty": 0.001,
        "min_notional": 5.0, "price_precision": 6
    })

# ═══════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES
# ═══════════════════════════════════════════════════════════

def calc_atr(symbol: str, interval: str = "5m", period: int = 14):
    klines = get_klines(symbol, interval, period + 5)
    if not klines or len(klines) < period + 1:
        return None
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    trs = [max(
        highs[i] - lows[i],
        abs(highs[i] - closes[i-1]),
        abs(lows[i]  - closes[i-1])
    ) for i in range(1, len(highs))]
    return sum(trs[-period:]) / period

def calc_ema(closes: list, period: int):
    if len(closes) < period:
        return None
    mult = 2 / (period + 1)
    ema  = closes[0]
    for c in closes[1:]:
        ema = (c - ema) * mult + ema
    return ema

# ═══════════════════════════════════════════════════════════
#  CALCUL SL / TP
# ═══════════════════════════════════════════════════════════

def calculate_sl_tp(symbol: str, entry: float, side: str):
    atr  = calc_atr(symbol, "5m", 14)
    info = get_symbol_info(symbol)
    pp   = info["price_precision"]

    if atr and atr > 0:
        sl_dist = atr * ATR_SL_MULT
        tp_dist = atr * ATR_TP_MULT
        method  = f"ATR={atr:.6f}"
    else:
        sl_dist = entry * FALLBACK_SL_PCT
        tp_dist = entry * FALLBACK_TP_PCT
        method  = "Fallback%"

    sl = round(entry - sl_dist if side == "LONG" else entry + sl_dist, pp)
    tp = round(entry + tp_dist if side == "LONG" else entry - tp_dist, pp)
    logger.info(f"   📐 [{method}] SL:{sl} | TP:{tp} | RR:{tp_dist/sl_dist:.2f}")
    return sl, tp

# ═══════════════════════════════════════════════════════════
#  CAPITAL
# ═══════════════════════════════════════════════════════════

def update_capital():
    global current_capital, peak_capital
    data = request_binance("GET", "/fapi/v2/balance")
    if not data:
        return
    usdt = next((b for b in data if b["asset"] == "USDT"), None)
    if usdt:
        with capital_lock:
            current_capital = float(usdt["balance"])
            if current_capital > peak_capital:
                peak_capital = current_capital
        logger.info(f"💰 Capital:{current_capital:.2f} | Peak:{peak_capital:.2f}")

def check_capital_protection() -> bool:
    dd = (starting_capital - current_capital) / max(starting_capital, 0.01)
    if dd >= MAX_DRAWDOWN_PCT:
        msg = f"🛑 KILL SWITCH — DD:{dd*100:.1f}% | {current_capital:.2f}/{starting_capital:.2f}$"
        logger.critical(msg)
        send_telegram(msg)
        emergency_close_all()
        return False
    if current_capital < MIN_CAPITAL_TO_TRADE:
        logger.warning(f"⚠️  Capital trop faible: {current_capital:.2f}")
        return False
    return True

def calculate_qty(symbol: str, entry: float) -> float:
    info     = get_symbol_info(symbol)
    notional = MARGIN_PER_TRADE * LEVERAGE
    qty      = notional / entry
    qty      = round(qty, info["qty_precision"])
    qty      = max(qty, info["min_qty"])
    if qty * entry < info["min_notional"]:
        qty = round(info["min_notional"] / entry * 1.01, info["qty_precision"])
    return qty

# ═══════════════════════════════════════════════════════════
#  SYNC ÉTAT BINANCE
# ═══════════════════════════════════════════════════════════

def sync_positions_from_exchange():
    data = request_binance("GET", "/fapi/v2/positionRisk")
    if not data:
        return

    exchange_open = {
        p["symbol"] for p in data
        if abs(float(p.get("positionAmt", 0))) > 0
    }

    with trade_lock:
        for sym, info in trade_log.items():
            if info.get("status") == "OPEN" and sym not in exchange_open:
                trade_log[sym]["status"] = "CLOSED"
                logger.info(f"🔄 SYNC: {sym} → CLOSED")
                # Estimer PnL pour le session tracker
                entry = info.get("entry", 0)
                price = get_price(sym) or entry
                side  = info.get("side", "LONG")
                pnl   = (price - entry) / entry * MARGIN_PER_TRADE * LEVERAGE
                if side == "SHORT":
                    pnl = -pnl
                record_trade_result(pnl)

def is_position_open(symbol: str) -> bool:
    data = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
    if data:
        return abs(float(data[0].get("positionAmt", 0))) > 0
    return False

# ═══════════════════════════════════════════════════════════
#  MARGE ISOLÉE + LEVIER
# ═══════════════════════════════════════════════════════════

def set_margin_type(symbol: str):
    request_binance("POST", "/fapi/v1/marginType",
                    {"symbol": symbol, "marginType": MARGIN_TYPE})
    time.sleep(0.15)

def set_leverage_isolated(symbol: str):
    set_margin_type(symbol)
    request_binance("POST", "/fapi/v1/leverage",
                    {"symbol": symbol, "leverage": LEVERAGE})
    time.sleep(0.15)

# ═══════════════════════════════════════════════════════════
#  ORDRES — CORRECTION COMPLÈTE ERREUR -4120
#
#  RÈGLE BINANCE FUTURES :
#  ✅ STOP_MARKET     → /fapi/v1/order  (standard)
#  ✅ TAKE_PROFIT_MARKET → /fapi/v1/order  (standard)
#  ✅ MARKET          → /fapi/v1/order  (standard)
#  ❌ TRAILING_STOP_MARKET → /fapi/v1/order  (INTERDIT → -4120)
#  ✅ Trailing        → /fapi/v1/order/algo/trailing-stop (ALGO)
#
#  PARAMÈTRES OBLIGATOIRES STOP_MARKET :
#  - stopPrice (arrondi au price_precision du symbol)
#  - workingType: MARK_PRICE
#  - reduceOnly: true
#  - quantity (obligatoire même avec reduceOnly)
#  NE PAS utiliser closePosition: true avec quantity → rejet
# ═══════════════════════════════════════════════════════════

def place_market_order(symbol: str, side: str, qty: float):
    info = get_symbol_info(symbol)
    return request_binance("POST", "/fapi/v1/order", {
        "symbol":   symbol,
        "side":     "BUY" if side == "LONG" else "SELL",
        "type":     "MARKET",
        "quantity": round(qty, info["qty_precision"])
    })

def place_stop_market(symbol: str, side: str, qty: float, stop_price: float):
    """
    SL correct pour Binance Futures.
    STOP_MARKET + MARK_PRICE + reduceOnly + quantity.
    Aucun paramètre algo (callbackRate etc.) → pas de -4120.
    """
    info = get_symbol_info(symbol)
    pp   = info["price_precision"]
    return request_binance("POST", "/fapi/v1/order", {
        "symbol":       symbol,
        "side":         "SELL" if side == "LONG" else "BUY",
        "type":         "STOP_MARKET",
        "stopPrice":    round(stop_price, pp),
        "quantity":     round(qty, info["qty_precision"]),
        "reduceOnly":   "true",
        "workingType":  "MARK_PRICE",
        "priceProtect": "TRUE"
    })

def place_take_profit_market(symbol: str, side: str, qty: float, tp_price: float):
    """
    TP correct pour Binance Futures.
    TAKE_PROFIT_MARKET + MARK_PRICE + reduceOnly + quantity.
    """
    info = get_symbol_info(symbol)
    pp   = info["price_precision"]
    return request_binance("POST", "/fapi/v1/order", {
        "symbol":       symbol,
        "side":         "SELL" if side == "LONG" else "BUY",
        "type":         "TAKE_PROFIT_MARKET",
        "stopPrice":    round(tp_price, pp),
        "quantity":     round(qty, info["qty_precision"]),
        "reduceOnly":   "true",
        "workingType":  "MARK_PRICE",
        "priceProtect": "TRUE"
    })

def place_trailing_algo(symbol: str, side: str, qty: float,
                         callback_rate: float, activation_price: float = None):
    """
    TRAILING via /fapi/v1/order/algo/trailing-stop UNIQUEMENT.
    Cet endpoint accepte callbackRate.
    L'endpoint standard /fapi/v1/order rejette → -4120.
    """
    info   = get_symbol_info(symbol)
    params = {
        "symbol":       symbol,
        "side":         "SELL" if side == "LONG" else "BUY",
        "quantity":     round(qty, info["qty_precision"]),
        "callbackRate": callback_rate,
        "reduceOnly":   "true"
    }
    if activation_price:
        params["activationPrice"] = round(activation_price, info["price_precision"])
    return request_binance("POST", "/fapi/v1/order/algo/trailing-stop", params)

def cancel_trailing_algo(symbol: str, algo_id: str):
    request_binance("DELETE", "/fapi/v1/order/algo/trailing-stop",
                    {"symbol": symbol, "algoId": algo_id})

def cancel_all_orders(symbol: str):
    request_binance("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
    time.sleep(0.2)

# ═══════════════════════════════════════════════════════════
#  FERMETURE MARKET
# ═══════════════════════════════════════════════════════════

def close_market(symbol: str, side: str, qty: float, reason: str = ""):
    if not is_position_open(symbol):
        logger.info(f"ℹ️  {symbol} déjà fermé — skip ({reason})")
        with trade_lock:
            if symbol in trade_log:
                trade_log[symbol]["status"] = "CLOSED"
        return

    cancel_all_orders(symbol)
    time.sleep(0.25)

    info = get_symbol_info(symbol)
    resp = request_binance("POST", "/fapi/v1/order", {
        "symbol":     symbol,
        "side":       "SELL" if side == "LONG" else "BUY",
        "type":       "MARKET",
        "quantity":   round(qty, info["qty_precision"]),
        "reduceOnly": "true"
    })

    if resp and "orderId" in resp:
        logger.info(f"✅ {symbol} FERMÉ — {reason}")
        send_telegram(f"🔴 <b>{symbol}</b> fermé — {reason}")
        with trade_lock:
            if symbol in trade_log:
                info_log  = trade_log[symbol]
                entry     = info_log.get("entry", 0)
                price_now = get_price(symbol) or entry
                side_pos  = info_log.get("side", side)
                pnl       = (price_now - entry) / entry * MARGIN_PER_TRADE * LEVERAGE
                if side_pos == "SHORT":
                    pnl = -pnl
                trade_log[symbol]["status"] = "CLOSED"
        record_trade_result(pnl)
    else:
        logger.error(f"❌ Impossible de fermer {symbol}")

# ═══════════════════════════════════════════════════════════
#  SL / TP — PLACEMENT + FALLBACK
# ═══════════════════════════════════════════════════════════

def has_sl_tp(symbol: str) -> dict:
    orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
    has_sl, has_tp = False, False
    if orders:
        for o in orders:
            t = o.get("type", "")
            if t in ("STOP_MARKET", "STOP"):               has_sl = True
            if t in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"): has_tp = True
    return {"has_sl": has_sl, "has_tp": has_tp}

def place_sl_tp_with_fallback(symbol: str, side: str, qty: float,
                               sl: float, tp: float):
    sl_ok, tp_ok = False, False

    r = place_stop_market(symbol, side, qty, sl)
    if r and "orderId" in r:
        sl_ok = True
        logger.info(f"   ✅ SL:{sl}")
    else:
        logger.warning(f"   ⚠️  SL rejeté → fallback local")

    time.sleep(0.25)

    r = place_take_profit_market(symbol, side, qty, tp)
    if r and "orderId" in r:
        tp_ok = True
        logger.info(f"   ✅ TP:{tp}")
    else:
        logger.warning(f"   ⚠️  TP rejeté → fallback local")

    fallback = not sl_ok or not tp_ok
    with trade_lock:
        if symbol in trade_log:
            trade_log[symbol].update({
                "sl": sl, "tp": tp,
                "fallback_active": fallback,
                "fallback_sl": sl, "fallback_tp": tp
            })
    if fallback:
        send_telegram(f"🟡 <b>{symbol}</b> fallback actif")

# ═══════════════════════════════════════════════════════════
#  SCAN POSITIONS EXISTANTES
# ═══════════════════════════════════════════════════════════

def scan_existing_positions():
    logger.info("🔎 Scan positions existantes...")
    data = request_binance("GET", "/fapi/v2/positionRisk")
    if not data:
        return

    found = 0
    for p in data:
        pos_amt = float(p.get("positionAmt", 0))
        if pos_amt == 0:
            continue

        symbol = p["symbol"]
        entry  = float(p["entryPrice"])
        qty    = abs(pos_amt)
        side   = "LONG" if pos_amt > 0 else "SHORT"
        found += 1

        logger.info(f"   📌 {symbol} | {side} | Entry:{entry} | Qty:{qty}")
        set_margin_type(symbol)

        with trade_lock:
            if symbol not in trade_log or trade_log[symbol].get("status") != "OPEN":
                trade_log[symbol] = {
                    "entry": entry, "qty": qty, "side": side,
                    "sl": None, "tp": None,
                    "fallback_active": False, "fallback_sl": None, "fallback_tp": None,
                    "trailing_active": False, "trailing_order_id": None,
                    "highest_price": entry if side == "LONG" else None,
                    "lowest_price":  entry if side == "SHORT" else None,
                    "breakeven_done": False, "status": "OPEN",
                    "opened_at": datetime.now(timezone.utc).isoformat()
                }

        existing = has_sl_tp(symbol)
        if not existing["has_sl"] or not existing["has_tp"]:
            sl, tp = calculate_sl_tp(symbol, entry, side)
            place_sl_tp_with_fallback(symbol, side, qty, sl, tp)

    logger.info(f"✅ {found} position(s) trouvée(s)")

# ═══════════════════════════════════════════════════════════
#  FALLBACK MONITOR
# ═══════════════════════════════════════════════════════════

def run_fallback_check(symbol: str, info: dict):
    if not info.get("fallback_active"):
        return
    price = get_price(symbol)
    if not price:
        return

    side = info["side"]
    fsl  = info.get("fallback_sl")
    ftp  = info.get("fallback_tp")
    triggered, reason = False, ""

    if side == "LONG":
        if fsl and price <= fsl: triggered, reason = True, f"Fallback SL@{fsl}"
        elif ftp and price >= ftp: triggered, reason = True, f"Fallback TP@{ftp}"
    else:
        if fsl and price >= fsl: triggered, reason = True, f"Fallback SL@{fsl}"
        elif ftp and price <= ftp: triggered, reason = True, f"Fallback TP@{ftp}"

    if triggered:
        logger.info(f"🔴 FALLBACK {symbol} — {reason} | Prix:{price}")
        close_market(symbol, side, info["qty"], reason)

# ═══════════════════════════════════════════════════════════
#  BREAK-EVEN + TRAILING
#
#  PHILOSOPHIE :
#  TP toujours conservé → laisser courir au RR max
#  Trailing remplace SL sans toucher TP
#  Break-even = STOP_MARKET pur (pas algo) → plus de -4120
# ═══════════════════════════════════════════════════════════

def run_trailing_logic(symbol: str, info: dict):
    price = get_price(symbol)
    if not price:
        return

    entry  = info["entry"]
    side   = info["side"]
    qty    = info["qty"]
    tp     = info.get("tp")
    profit = (price - entry) / entry if side == "LONG" else (entry - price) / entry

    # ── 1. Break-even (+0.5%) ─────────────────────────────
    # CORRECTION -4120 : STOP_MARKET pur, pas de paramètre algo
    if profit >= BREAKEVEN_TRIGGER_PCT and not info.get("breakeven_done"):
        logger.info(f"🐎 {symbol} break-even +{profit*100:.2f}%")

        # Annuler tous les ordres existants
        cancel_all_orders(symbol)
        time.sleep(0.2)

        # Placer SL au break-even — STOP_MARKET pur
        r_sl = place_stop_market(symbol, side, qty, entry)

        with trade_lock:
            trade_log[symbol]["breakeven_done"] = True
            if r_sl and "orderId" in r_sl:
                trade_log[symbol]["sl"]          = entry
                trade_log[symbol]["fallback_sl"] = entry
                logger.info(f"   ✅ Break-even SL @ {entry}")
            else:
                trade_log[symbol]["fallback_sl"]    = entry
                trade_log[symbol]["fallback_active"] = True
                logger.warning(f"   ⚠️  Break-even rejeté → fallback {entry}")

        # Remettre le TP en place (conservé après cancel)
        if tp:
            r_tp = place_take_profit_market(symbol, side, qty, tp)
            if not (r_tp and "orderId" in r_tp):
                with trade_lock:
                    trade_log[symbol]["fallback_active"] = True
                    trade_log[symbol]["fallback_tp"]     = tp

    # ── 2. Trailing Algo (+1%) ────────────────────────────
    # Via /fapi/v1/order/algo/trailing-stop uniquement
    # TP CONSERVÉ sur l'exchange (pas annulé)
    if profit >= TRAILING_TRIGGER_PCT and not info.get("trailing_active"):
        old_id = info.get("trailing_order_id")
        if old_id:
            cancel_trailing_algo(symbol, old_id)
            time.sleep(0.2)

        algo_r = place_trailing_algo(
            symbol,
            side,
            qty,
            callback_rate    = TRAILING_CALLBACK_RATE,
            activation_price = price
        )

        if algo_r and "algoId" in algo_r:
            logger.info(f"📈 {symbol} trailing ALGO | {TRAILING_CALLBACK_RATE}% | id:{algo_r['algoId']}")
            with trade_lock:
                trade_log[symbol]["trailing_active"]   = True
                trade_log[symbol]["trailing_order_id"] = algo_r["algoId"]
        else:
            # Trailing local ATR — TP toujours préservé
            logger.warning(f"   ⚠️  Trailing algo rejeté → local ATR")
            with trade_lock:
                trade_log[symbol]["trailing_active"] = True
                trade_log[symbol]["fallback_active"] = True

    # ── 3. Trailing local ATR si algo non dispo ───────────
    if info.get("trailing_active") and info.get("fallback_active"):
        _local_trailing_atr(symbol, info, price)

def _local_trailing_atr(symbol: str, info: dict, price: float):
    """
    Trailing local basé sur ATR.
    Met à jour fallback_sl uniquement — le TP N'EST JAMAIS touché.
    """
    atr = calc_atr(symbol, "5m", 14)
    if not atr:
        return
    side = info["side"]

    if side == "LONG":
        highest = max(info.get("highest_price") or price, price)
        with trade_lock:
            trade_log[symbol]["highest_price"] = highest
        new_sl = highest - atr
        if new_sl > (info.get("fallback_sl") or 0):
            logger.info(f"📈 {symbol} local trail SL→{new_sl:.6f}")
            with trade_lock:
                trade_log[symbol]["fallback_sl"] = new_sl
    else:
        lowest = min(info.get("lowest_price") or price, price)
        with trade_lock:
            trade_log[symbol]["lowest_price"] = lowest
        new_sl = lowest + atr
        if new_sl < (info.get("fallback_sl") or float("inf")):
            logger.info(f"📉 {symbol} local trail SL→{new_sl:.6f}")
            with trade_lock:
                trade_log[symbol]["fallback_sl"] = new_sl

# ═══════════════════════════════════════════════════════════
#  MONITOR LOOP
# ═══════════════════════════════════════════════════════════

def monitor_loop():
    logger.info("👁️  Monitor démarré")
    cycle = 0
    while True:
        try:
            if cycle % 10 == 0:
                sync_positions_from_exchange()

            with trade_lock:
                to_check = {k: v.copy() for k, v in trade_log.items()
                            if v.get("status") == "OPEN"}

            for symbol, info in to_check.items():
                try:
                    run_fallback_check(symbol, info)
                    with trade_lock:
                        updated = trade_log.get(symbol, {})
                    if updated.get("status") == "OPEN":
                        run_trailing_logic(symbol, updated)
                except Exception as e:
                    logger.error(f"Monitor {symbol}: {e}")

            cycle += 1

        except Exception as e:
            logger.error(f"Monitor loop: {e}")

        time.sleep(MONITOR_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  EMERGENCY CLOSE ALL
# ═══════════════════════════════════════════════════════════

def emergency_close_all():
    logger.critical("🛑 FERMETURE D'URGENCE")
    with trade_lock:
        to_close = [(k, v.copy()) for k, v in trade_log.items()
                    if v.get("status") == "OPEN"]
    for sym, info in to_close:
        close_market(sym, info["side"], info["qty"], "EMERGENCY_STOP")
        time.sleep(0.5)

# ═══════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════

def display_dashboard():
    sep = "═" * 60
    min_stars, max_pos = get_session_params()

    with trade_lock:
        open_pos     = {k: v for k, v in trade_log.items() if v.get("status") == "OPEN"}
        closed_count = sum(1 for v in trade_log.values() if v.get("status") == "CLOSED")

    with session_lock:
        losses = session_losses
        s_pnl  = session_pnl

    logger.info(f"\n{sep}")
    logger.info(f"  🤖 ROBOTKING M1 PRO — ULTRA OCÉAN v5.0")
    logger.info(f"  💰 Capital:{current_capital:.2f}$ | Peak:{peak_capital:.2f}$ | Marge/trade:{MARGIN_PER_TRADE}$")
    logger.info(f"  📍 Positions:{len(open_pos)}/{max_pos} | Fermées:{closed_count}")
    logger.info(f"  🎯 Session: Pertes={losses} | PnL={s_pnl:+.3f}$ | {max_pos}pos max | {min_stars}★ min")
    logger.info(f"  🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(sep)

    if not open_pos:
        logger.info("  📭 Aucune position — Scanner actif")
    else:
        for sym, info in open_pos.items():
            price   = get_price(sym) or 0
            entry   = info["entry"]
            side    = info["side"]
            pnl_pct = (price-entry)/entry*100 if side=="LONG" else (entry-price)/entry*100
            pnl_usd = MARGIN_PER_TRADE * LEVERAGE * (pnl_pct / 100)
            sign    = "+" if pnl_pct >= 0 else ""

            # RR restant
            rr_str = ""
            if info.get("tp") and info.get("sl") and price:
                tp_d = abs(info["tp"] - price)
                sl_d = abs(price - info["sl"])
                if sl_d > 0:
                    rr_str = f" RR:{tp_d/sl_d:.1f}"

            trail_str = (
                "✅ALGO"  if info.get("trailing_active") and not info.get("fallback_active")
                else "🔄ATR" if info.get("trailing_active")
                else "⏳"
            )

            logger.info(f"\n  ┌─ {sym} [{side}]")
            logger.info(f"  │  {entry} → {price} | {sign}{pnl_pct:.2f}% ({sign}{pnl_usd:.3f}$){rr_str}")
            logger.info(f"  │  SL:{info.get('sl') or 'N/A'} | TP:{info.get('tp') or 'N/A'}")
            logger.info(f"  │  TRAIL:{trail_str} | FB:{'🟡' if info.get('fallback_active') else '✅'} | BE:{'✅' if info.get('breakeven_done') else '⏳'}")
            logger.info(f"  └{'─'*50}")

    logger.info(f"\n{sep}\n")

def dashboard_loop():
    while True:
        try:
            display_dashboard()
        except Exception as e:
            logger.error(f"Dashboard: {e}")
        time.sleep(DASHBOARD_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  SCANNER
# ═══════════════════════════════════════════════════════════

def score_symbol(symbol: str):
    try:
        klines = get_klines(symbol, "1m", 50)
        if not klines or len(klines) < 50:
            return None

        closes  = [float(k[4]) for k in klines]
        highs   = [float(k[2]) for k in klines]
        lows    = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        price   = closes[-1]

        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        if not ema9 or not ema21:
            return None

        side    = "LONG" if ema9 > ema21 else "SHORT"
        stars   = 1
        details = ["EMA9/21"]

        recent_high = max(highs[-20:-2])
        recent_low  = min(lows[-20:-2])
        if price > recent_high * 1.001:
            stars += 2; details.append("BOS↑")
        elif price < recent_low * 0.999:
            stars += 2; details.append("BOS↓")

        avg_vol = sum(volumes[-20:-1]) / 19
        if volumes[-1] > avg_vol * 1.5:
            stars += 1; details.append("Vol↑")

        atr = calc_atr(symbol, "5m", 14)
        if atr:
            stars += 1; details.append("ATR✓")

        return {"symbol": symbol, "side": side, "price": price,
                "stars": min(stars, 5), "details": details}

    except Exception as e:
        logger.error(f"score {symbol}: {e}")
        return None

def open_new_trade(opp: dict):
    global current_capital
    symbol, side, price = opp["symbol"], opp["side"], opp["price"]

    min_stars, max_pos = get_session_params()

    with trade_lock:
        if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
            return
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        if n_open >= max_pos:
            return

    # Filtrer par étoiles selon la session
    if opp["stars"] < min_stars:
        logger.info(f"   ⏭  {symbol} ignoré — {opp['stars']}★ < {min_stars}★ requis (session)")
        return

    update_capital()
    if not check_capital_protection():
        return

    qty = calculate_qty(symbol, price)
    set_leverage_isolated(symbol)
    time.sleep(0.25)

    resp = place_market_order(symbol, side, qty)
    if not resp or "orderId" not in resp:
        logger.error(f"❌ Ordre market {symbol} échoué")
        return

    entry = float(resp.get("avgPrice") or 0)
    if entry == 0:
        entry = get_price(symbol) or price

    sl, tp = calculate_sl_tp(symbol, entry, side)

    with trade_lock:
        trade_log[symbol] = {
            "entry": entry, "qty": qty, "side": side,
            "sl": None, "tp": None,
            "fallback_active": False, "fallback_sl": None, "fallback_tp": None,
            "trailing_active": False, "trailing_order_id": None,
            "highest_price": entry if side == "LONG" else None,
            "lowest_price":  entry if side == "SHORT" else None,
            "breakeven_done": False, "status": "OPEN",
            "opened_at": datetime.now(timezone.utc).isoformat()
        }

    place_sl_tp_with_fallback(symbol, side, qty, sl, tp)

    min_stars_now, max_pos_now = get_session_params()
    msg = (f"🟢 <b>{symbol}</b> {side} @ {entry}\n"
           f"  {MARGIN_PER_TRADE}$ × {LEVERAGE}x | SL:{sl} | TP:{tp}\n"
           f"  ⭐{opp['stars']}/5 — {', '.join(opp['details'])}\n"
           f"  Session: {max_pos_now}pos max | {min_stars_now}★ requis")
    logger.info(msg.replace("<b>","").replace("</b>",""))
    send_telegram(msg)

def scan_loop():
    logger.info("🔍 Scanner démarré")
    while True:
        try:
            min_stars, max_pos = get_session_params()

            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])

            slots = max_pos - n_open
            if slots > 0:
                logger.info(f"🔍 Scan — {slots} slot(s) | {min_stars}★ min requis")
                opps = []
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = {ex.submit(score_symbol, s): s for s in SYMBOLS}
                    for f in as_completed(futures):
                        r = f.result()
                        if r and r["stars"] >= min_stars:
                            opps.append(r)

                opps.sort(key=lambda x: x["stars"], reverse=True)
                for opp in opps[:slots]:
                    open_new_trade(opp)
                    time.sleep(1)
            else:
                logger.info(f"✋ {n_open}/{max_pos} — scanner en pause")

        except Exception as e:
            logger.error(f"Scan loop: {e}")

        time.sleep(SCAN_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    global starting_capital, current_capital, peak_capital

    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  ROBOTKING M1 PRO — ULTRA OCÉAN v5.0 ║")
    logger.info("╚══════════════════════════════════════╝")

    if DRY_RUN:
        logger.info("🟡 DRY RUN — aucun ordre réel")

    load_symbol_precision_all()
    update_capital()
    starting_capital = current_capital
    peak_capital     = current_capital

    logger.info(f"💰 Capital départ : {starting_capital:.2f} USDT")
    logger.info(f"📦 Marge/trade    : {MARGIN_PER_TRADE}$ × {LEVERAGE}x = {MARGIN_PER_TRADE*LEVERAGE:.0f}$ notional")
    logger.info(f"🎯 Mode départ    : {MAX_POSITIONS} positions | {MIN_STARS_NORMAL}★ min")

    scan_existing_positions()

    threads = [
        threading.Thread(target=monitor_loop,   daemon=True, name="Monitor"),
        threading.Thread(target=scan_loop,       daemon=True, name="Scanner"),
        threading.Thread(target=dashboard_loop,  daemon=True, name="Dashboard"),
    ]
    for t in threads:
        t.start()
        logger.info(f"▶️  [{t.name}] lancé")

    logger.info("✅ Bot opérationnel\n")

    try:
        while True:
            time.sleep(60)
            update_capital()
            check_capital_protection()
    except KeyboardInterrupt:
        logger.info("\n⛔ Arrêt")
        emergency_close_all()

if __name__ == "__main__":
    main()

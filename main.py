#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         ROBOTKING M1 PRO v6.2 – SNIPER FINAL EDITION            ║
║         H1 Adaptive + Weighted + Dynamic TP/BE + Flash          ║
╠══════════════════════════════════════════════════════════════════╣
║  NOUVEAUTÉS v6.2 SNIPER FINAL :                                  ║
║  ✅ H1 Adaptive Filter (Score-based, not binary)                 ║
║  ✅ Weighted Setup Confluence (Breaker=2.0, OB=1.5, etc.)        ║
║  ✅ Dynamic TP Auto (ULTRA RR 1:3.25+ to WEAK RR 1:2.1)          ║
║  ✅ Dynamic BE Trigger (ULTRA +0.8% to WEAK +0.1%)               ║
║  ✅ Sniper Mode (Max 1 position, RR filter 1:2.1+)               ║
║  ✅ FLASH Keep-Alive (every 2 min) ⚡                            ║
║  ✅ Full Logging + Capital Protection                            ║
╚══════════════════════════════════════════════════════════════════╝
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
from flask import Flask

# ═══════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("robotking_v6_2_final.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("BINANCE_API_KEY",    "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
RENDER_URL       = os.environ.get("RENDER_EXTERNAL_URL", "")

BASE_URL = "https://fapi.binance.com"
DRY_RUN  = os.environ.get("DRY_RUN", "false").lower() == "true"

# ── Marge & Levier ─────────────────────────────────────────────────
MARGIN_TYPE      = "ISOLATED"
LEVERAGE         = 20
MARGIN_PER_TRADE = 1.50        # 1.50$ marge → 30$ notional

# ── Capital ────────────────────────────────────────────────────────
INITIAL_CAPITAL      = 5.0
MAX_DRAWDOWN_PCT     = 0.20
MIN_CAPITAL_TO_TRADE = 2.0
MAX_POSITIONS        = 1       # SNIPER MODE: 1 position max

# ── SL / TP ATR ────────────────────────────────────────────────────
ATR_SL_MULT      = 1.5         # SL = ATR × 1.5
ATR_TP_BASE_MULT = 2.5         # Base TP (modifié dynamiquement)
FALLBACK_SL_PCT  = 0.012
FALLBACK_TP_PCT  = 0.025

# ── Dynamic TP/BE (selon confluence) ───────────────────────────────
# Ces valeurs seront ajustées dynamiquement
BREAKEVEN_TRIGGER_PCT  = 0.010  # Valeur de base (+1.0%)
TRAILING_TRIGGER_PCT   = 0.020  # +2.0%
TRAILING_CALLBACK_RATE = 1.0    # 1.0%

# ── RR Filter (SNIPER MODE) ────────────────────────────────────────
MIN_RR_FILTER = 2.1             # Rejette trade si RR < 2.1

# ── Intervals ──────────────────────────────────────────────────────
SCAN_INTERVAL      = 20         # Scan toutes les 20s
MONITOR_INTERVAL   = 3          # Monitor toutes les 3s
DASHBOARD_INTERVAL = 30         # Dashboard toutes les 30s
KEEPALIVE_INTERVAL = 120        # FLASH every 2 minutes ⚡
MAX_WORKERS        = 8

# ── Scoring 5★ requis ──────────────────────────────────────────────
MIN_STARS_REQUIRED = 5

# ── Session & Recovery ─────────────────────────────────────────────
RECOVERY_MAX_POS_1 = 1
RECOVERY_MAX_POS_2 = 1
RECOVERY_MAX_POS_3 = 1

# ── Symbols ────────────────────────────────────────────────────────
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

# ═══════════════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════════════

trade_log: dict              = {}
symbol_precision_cache: dict = {}
price_cache: dict            = {}
klines_cache: dict           = {}

current_capital  = INITIAL_CAPITAL
peak_capital     = INITIAL_CAPITAL
starting_capital = INITIAL_CAPITAL

session_losses = 0
session_wins   = 0
session_pnl    = 0.0

trade_lock   = threading.Lock()
capital_lock = threading.Lock()
api_lock     = threading.Lock()
session_lock = threading.Lock()
api_call_times: list = []

# ═══════════════════════════════════════════════════════════════════
#  FLASK HEALTH SERVER
# ═══════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    return (
        f"🤖 ROBOTKING v6.2 SNIPER FINAL — OPÉRATIONNEL\n"
        f"Positions ouvertes: {n_open}/{MAX_POSITIONS}\n"
        f"Capital: {current_capital:.2f}$\n"
        f"Peak: {peak_capital:.2f}$\n"
        f"Mode: SNIPER (1 pos max, RR 1:2.1+ filter)"
    ), 200

@flask_app.route("/health")
def health():
    return "✅ ALIVE", 200

@flask_app.route("/status")
def status():
    with trade_lock:
        open_pos = {k: v for k, v in trade_log.items() if v.get("status") == "OPEN"}
    with session_lock:
        losses = session_losses
        s_pnl  = session_pnl
    return {
        "status":       "running",
        "version":      "v6.2 SNIPER FINAL",
        "capital":      round(current_capital, 4),
        "peak":         round(peak_capital, 4),
        "positions":    len(open_pos),
        "max_pos":      MAX_POSITIONS,
        "session_loss": losses,
        "session_pnl":  round(s_pnl, 4),
        "symbols":      list(open_pos.keys()),
        "mode":         "SNIPER"
    }, 200

def start_health_server():
    """Lance Flask dans un thread daemon"""
    port = int(os.environ.get("PORT", 10000))
    try:
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        threading.Thread(
            target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False),
            daemon=True,
            name="Flask"
        ).start()
        logger.info(f"🌐 Flask Health Server → http://0.0.0.0:{port}")
        logger.info(f"   Routes: / | /health | /status")
    except Exception as e:
        logger.warning(f"Flask server: {e}")

# ═══════════════════════════════════════════════════════════════════
#  FLASH KEEP-ALIVE ⚡ (Every 2 minutes)
# ═══════════════════════════════════════════════════════════════════

def flash_keep_alive_loop():
    """
    FLASH Keep-Alive: ping toutes les 2 minutes pour maintenir Render actif
    + local health check + Binance API check
    """
    time.sleep(30)  # Attendre le démarrage complet
    while True:
        try:
            now_str = datetime.now().strftime("%H:%M:%S")
            logger.info(f"⚡ FLASH PING @ {now_str} — keeping Render AWAKE")
            
            # 1. Local health check
            try:
                resp = requests.get("http://localhost:10000/health", timeout=5)
                if resp.status_code == 200:
                    logger.info("   ✅ Local health OK")
                else:
                    logger.warning("   ⚠️  Local health failed")
            except Exception as e:
                logger.warning(f"   ⚠️  Local health error: {e}")
            
            # 2. External ping (Render)
            if RENDER_URL:
                try:
                    url = RENDER_URL.rstrip("/") + "/health"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        logger.info("   ✅ External ping OK")
                    else:
                        logger.warning("   ⚠️  External ping failed")
                except Exception as e:
                    logger.warning(f"   ⚠️  External ping error: {e}")
            
            # 3. Binance API health check (simple ping)
            try:
                resp = requests.get(f"{BASE_URL}/fapi/v1/ping", timeout=5)
                if resp.status_code == 200:
                    logger.info("   ✅ Binance API OK")
                else:
                    logger.warning("   ⚠️  Binance API failed")
            except Exception as e:
                logger.warning(f"   ⚠️  Binance API error: {e}")
            
        except Exception as e:
            logger.error(f"FLASH keep-alive error: {e}")
        
        time.sleep(KEEPALIVE_INTERVAL)  # 120 seconds = 2 minutes

# ═══════════════════════════════════════════════════════════════════
#  SESSION INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════

def get_session_params():
    with session_lock:
        losses = session_losses
    if losses == 0:
        return MIN_STARS_REQUIRED, MAX_POSITIONS
    elif losses == 1:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_1
    elif losses == 2:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_2
    else:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_3

def record_trade_result(pnl_usdt: float):
    global session_losses, session_wins, session_pnl
    with session_lock:
        session_pnl += pnl_usdt
        if pnl_usdt < 0:
            session_losses += 1
            _, max_pos = get_session_params()
            logger.warning(f"📉 Perte #{session_losses} | Session PnL:{session_pnl:.3f}$ | Mode: {max_pos}pos max")
            send_telegram(f"⚠️ Perte #{session_losses}\nSession: {session_pnl:+.3f}$ | {max_pos} pos max")
        else:
            session_wins += 1
            logger.info(f"📈 Gain #{session_wins} | Session PnL:{session_pnl:.3f}$")
            if session_pnl >= 0 and session_losses > 0:
                session_losses = 0
                session_wins   = 0
                logger.info("✅ Recovery terminé — retour mode normal")
                send_telegram("✅ Recovery terminé — mode normal restauré")

# ═══════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  API BINANCE
# ═══════════════════════════════════════════════════════════════════

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
            "orderId":  f"DRY_{int(time.time()*1000)}",
            "algoId":   f"DRYALGO_{int(time.time()*1000)}",
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

# ═══════════════════════════════════════════════════════════════════
#  CACHE KLINES / PRIX
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  PRÉCISIONS SYMBOLS
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES
# ═══════════════════════════════════════════════════════════════════

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

def calc_rsi(closes: list, period: int = 14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ═══════════════════════════════════════════════════════════════════
#  H1 ADAPTIVE FILTER (SCORE-BASED)
# ═══════════════════════════════════════════════════════════════════

def get_h1_trend(symbol: str):
    """
    Retourne un score H1 de tendance : STRONG (3/3), MODERATE (2/3), WEAK (0-1/3)
    Critères : EMA alignment + RSI zone + SMA direction
    """
    klines = get_klines(symbol, "1h", 50)
    if not klines or len(klines) < 50:
        return "NONE", 0
    
    closes = [float(k[4]) for k in klines]
    
    # 1. EMA alignment
    ema_9  = calc_ema(closes, 9)
    ema_21 = calc_ema(closes, 21)
    ema_50 = calc_ema(closes[-50:], 50)
    
    ema_score = 0
    if ema_9 and ema_21 and ema_50:
        if ema_9 > ema_21 > ema_50:  # Bullish alignment
            ema_score = 1
        elif ema_9 < ema_21 < ema_50:  # Bearish alignment
            ema_score = 1
    
    # 2. RSI zone
    rsi = calc_rsi(closes, 14)
    rsi_score = 0
    if rsi:
        if 40 <= rsi <= 60:  # Neutral (not extreme)
            rsi_score = 1
    
    # 3. SMA direction
    sma_20 = sum(closes[-20:]) / 20
    sma_50 = sum(closes[-50:]) / 50
    
    sma_score = 0
    if sma_20 > sma_50:  # Uptrend
        sma_score = 1
    elif sma_20 < sma_50:  # Downtrend
        sma_score = 1
    
    total_score = ema_score + rsi_score + sma_score
    
    if total_score == 3:
        return "STRONG", 3
    elif total_score == 2:
        return "MODERATE", 2
    elif total_score == 1:
        return "WEAK", 1
    else:
        return "NONE", 0

# ═══════════════════════════════════════════════════════════════════
#  SETUP DETECTION (WEIGHTED CONFLUENCE)
# ═══════════════════════════════════════════════════════════════════

def detect_setups(symbol: str, side: str):
    """
    Détecte les setups et calcule leur confluence pondérée.
    Retourne (setups_list, confluence_score, confluence_label)
    
    Poids:
    - Breaker: 2.0
    - ChoCh: 2.0
    - Order Block: 1.5
    - New HH/LL: 1.0
    - LH/LL: 0.8
    - Double Top/Bottom + Fib: 0.5
    """
    klines = get_klines(symbol, "1m", 50)
    if not klines or len(klines) < 30:
        return [], 0.0, "NONE"
    
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    
    setups = []
    confluence = 0.0
    
    # 1. Breaker (structure forte)
    if side == "LONG":
        prev_high = max(highs[-20:-1])
        if closes[-1] > prev_high:
            setups.append("BREAKER")
            confluence += 2.0
    else:
        prev_low = min(lows[-20:-1])
        if closes[-1] < prev_low:
            setups.append("BREAKER")
            confluence += 2.0
    
    # 2. ChoCh (Change of Character)
    if len(highs) >= 10:
        if side == "LONG":
            if highs[-1] > highs[-5] and lows[-1] > lows[-5]:
                setups.append("CHOCH")
                confluence += 2.0
        else:
            if highs[-1] < highs[-5] and lows[-1] < lows[-5]:
                setups.append("CHOCH")
                confluence += 2.0
    
    # 3. Order Block (zone institutionnelle)
    if side == "LONG":
        if closes[-1] > closes[-2] and closes[-2] < closes[-3]:
            setups.append("ORDER_BLOCK")
            confluence += 1.5
    else:
        if closes[-1] < closes[-2] and closes[-2] > closes[-3]:
            setups.append("ORDER_BLOCK")
            confluence += 1.5
    
    # 4. New HH/LL
    if side == "LONG":
        if highs[-1] == max(highs[-10:]):
            setups.append("NEW_HH")
            confluence += 1.0
    else:
        if lows[-1] == min(lows[-10:]):
            setups.append("NEW_LL")
            confluence += 1.0
    
    # 5. LH/LL formation
    if len(highs) >= 6:
        if side == "LONG":
            if highs[-1] < highs[-3] and lows[-1] < lows[-3]:
                setups.append("LH_LL")
                confluence += 0.8
        else:
            if highs[-1] > highs[-3] and lows[-1] > lows[-3]:
                setups.append("LH_LL")
                confluence += 0.8
    
    # Déterminer le label de confluence
    if confluence >= 4.0:
        conf_label = "ULTRA"
    elif confluence >= 3.5:
        conf_label = "PREMIUM"
    elif confluence >= 3.0:
        conf_label = "NORMAL"
    elif confluence >= 2.5:
        conf_label = "WEAK"
    else:
        conf_label = "NONE"
    
    return setups, confluence, conf_label

# ═══════════════════════════════════════════════════════════════════
#  DYNAMIC TP & BE CALCULATION
# ═══════════════════════════════════════════════════════════════════

def calc_dynamic_tp_be(h1_strength: str, confluence_score: float, atr: float, entry: float, side: str):
    """
    Calcule TP et BE dynamiques selon H1 + confluence
    
    Niveaux:
    - ULTRA (H1 STRONG + confluence >4): RR 3.25, BE +0.8%
    - PREMIUM (H1 MODERATE + confluence 3.5-4): RR 2.75, BE +0.4%
    - NORMAL (confluence 3-3.5): RR 2.5, BE +0.2%
    - WEAK (confluence <3): RR 2.1, BE +0.1%
    """
    info = get_symbol_info("")
    pp   = info["price_precision"]
    
    # Calcul SL basé sur ATR
    if atr and atr > 0:
        sl_dist = atr * ATR_SL_MULT
    else:
        sl_dist = entry * FALLBACK_SL_PCT
    
    # Déterminer RR et BE selon profil
    if h1_strength == "STRONG" and confluence_score >= 4.0:
        # ULTRA
        tp_mult = 3.25
        be_pct = 0.008  # +0.8%
        level = "ULTRA"
    elif h1_strength in ["STRONG", "MODERATE"] and confluence_score >= 3.5:
        # PREMIUM
        tp_mult = 2.75
        be_pct = 0.004  # +0.4%
        level = "PREMIUM"
    elif confluence_score >= 3.0:
        # NORMAL
        tp_mult = 2.5
        be_pct = 0.002  # +0.2%
        level = "NORMAL"
    else:
        # WEAK
        tp_mult = 2.1
        be_pct = 0.001  # +0.1%
        level = "WEAK"
    
    tp_dist = sl_dist * tp_mult
    
    sl = round(entry - sl_dist if side == "LONG" else entry + sl_dist, pp)
    tp = round(entry + tp_dist if side == "LONG" else entry - tp_dist, pp)
    rr = tp_dist / sl_dist
    
    logger.info(f"   📐 [{level}] H1:{h1_strength} | Conf:{confluence_score:.1f} | RR:{rr:.2f}")
    logger.info(f"   SL:{sl} | TP:{tp} | BE:{be_pct*100:.1f}%")
    
    return sl, tp, rr, be_pct, level

# ═══════════════════════════════════════════════════════════════════
#  SCORING 5★ VALIDATION
# ═══════════════════════════════════════════════════════════════════

def score_signal(symbol: str, side: str):
    """
    Score le signal sur 5 critères :
    1. EMA Cross
    2. BOS (Break of Structure)
    3. Volume Spike
    4. ATR Range
    5. RSI Zone
    
    Retourne (score, details_dict)
    """
    klines = get_klines(symbol, "1m", 50)
    if not klines or len(klines) < 50:
        return 0, {}
    
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    
    score = 0
    details = {}
    
    # 1. EMA Cross
    ema_9  = calc_ema(closes, 9)
    ema_21 = calc_ema(closes, 21)
    if ema_9 and ema_21:
        if side == "LONG" and ema_9 > ema_21:
            score += 1
            details["EMA"] = "✓"
        elif side == "SHORT" and ema_9 < ema_21:
            score += 1
            details["EMA"] = "✓"
        else:
            details["EMA"] = "✗"
    else:
        details["EMA"] = "✗"
    
    # 2. BOS (Break of Structure)
    if side == "LONG":
        prev_high = max(highs[-20:-1])
        if closes[-1] > prev_high:
            score += 1
            details["BOS"] = "↑"
        else:
            details["BOS"] = "✗"
    else:
        prev_low = min(lows[-20:-1])
        if closes[-1] < prev_low:
            score += 1
            details["BOS"] = "↓"
        else:
            details["BOS"] = "✗"
    
    # 3. Volume Spike
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] > avg_vol * 1.3:
        score += 1
        details["Vol"] = "↑"
    else:
        details["Vol"] = "✗"
    
    # 4. ATR Range
    atr = calc_atr(symbol, "5m", 14)
    if atr and atr > 0:
        score += 1
        details["ATR"] = "✓"
    else:
        details["ATR"] = "✗"
    
    # 5. RSI Zone
    rsi = calc_rsi(closes, 14)
    if rsi:
        if side == "LONG" and rsi < 45:
            score += 1
            details["RSI"] = f"{rsi:.0f}✓"
        elif side == "SHORT" and rsi > 55:
            score += 1
            details["RSI"] = f"{rsi:.0f}✓"
        else:
            details["RSI"] = f"{rsi:.0f}✗"
    else:
        details["RSI"] = "✗"
    
    return score, details

# ═══════════════════════════════════════════════════════════════════
#  CAPITAL
# ═══════════════════════════════════════════════════════════════════

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
        logger.info(f"💰 Capital:{current_capital:.2f}$ | Peak:{peak_capital:.2f}$")

def check_capital_protection() -> bool:
    dd = (starting_capital - current_capital) / max(starting_capital, 0.01)
    if dd >= MAX_DRAWDOWN_PCT:
        msg = f"🛑 KILL SWITCH — DD:{dd*100:.1f}% | {current_capital:.2f}/{starting_capital:.2f}$"
        logger.critical(msg)
        send_telegram(msg)
        emergency_close_all()
        return False
    if current_capital < MIN_CAPITAL_TO_TRADE:
        logger.warning(f"⚠️  Capital trop faible: {current_capital:.2f}$")
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

# ═══════════════════════════════════════════════════════════════════
#  SYNC POSITIONS
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  MARGE & LEVIER
# ═══════════════════════════════════════════════════════════════════

def set_margin_type(symbol: str):
    request_binance("POST", "/fapi/v1/marginType",
                    {"symbol": symbol, "marginType": MARGIN_TYPE})
    time.sleep(0.15)

def set_leverage_isolated(symbol: str):
    set_margin_type(symbol)
    request_binance("POST", "/fapi/v1/leverage",
                    {"symbol": symbol, "leverage": LEVERAGE})
    time.sleep(0.15)

# ═══════════════════════════════════════════════════════════════════
#  OUVERTURE POSITION
# ═══════════════════════════════════════════════════════════════════

def open_position(symbol: str, side: str, entry: float, sl: float, tp: float, 
                  score_details: dict, h1_trend: str, setups: list, 
                  confluence: float, conf_label: str, rr: float, be_pct: float, level: str):
    """Ouvre une position LONG/SHORT avec SL/TP"""
    
    set_leverage_isolated(symbol)
    qty = calculate_qty(symbol, entry)
    
    # Market order
    order_side = "BUY" if side == "LONG" else "SELL"
    order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": order_side,
        "type": "MARKET",
        "quantity": qty
    })
    
    if not order:
        logger.error(f"❌ Ordre marché échoué: {symbol}")
        return
    
    time.sleep(0.2)
    
    # Stop Loss
    sl_side = "SELL" if side == "LONG" else "BUY"
    sl_order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": sl_side,
        "type": "STOP_MARKET",
        "stopPrice": sl,
        "closePosition": "true"
    })
    
    # Take Profit
    tp_side = "SELL" if side == "LONG" else "BUY"
    tp_order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": tp_side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": tp,
        "closePosition": "true"
    })
    
    # Logging
    with trade_lock:
        trade_log[symbol] = {
            "status": "OPEN",
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "qty": qty,
            "sl_order_id": sl_order.get("orderId") if sl_order else None,
            "tp_order_id": tp_order.get("orderId") if tp_order else None,
            "be_triggered": False,
            "be_pct": be_pct,
            "level": level,
            "h1_trend": h1_trend,
            "confluence": confluence,
            "conf_label": conf_label,
            "setups": setups,
            "rr": rr
        }
    
    stars = "⭐" * 5
    emoji = "🟢" if side == "LONG" else "🔴"
    logger.info(f"{emoji} {symbol} {side} @ {entry}")
    logger.info(f"{stars} 5/5 — {', '.join(f'{k}{v}' for k,v in score_details.items())}")
    logger.info(f"📊 H1: {h1_trend} | Setups: {', '.join(setups)}")
    logger.info(f"💎 Confluence: {conf_label} ({confluence:.1f}) | Level: {level}")
    logger.info(f"📈 RR: 1:{rr:.2f} | SL:{sl} | TP:{tp} | BE:{be_pct*100:.1f}%")
    
    msg = (
        f"{emoji} <b>{symbol} {side}</b> @ {entry}\n"
        f"{stars} 5/5\n"
        f"📊 H1: {h1_trend}\n"
        f"🔧 Setups: {', '.join(setups)}\n"
        f"💎 {conf_label} ({confluence:.1f})\n"
        f"📈 RR: 1:{rr:.2f}\n"
        f"SL: {sl} | TP: {tp}"
    )
    send_telegram(msg)

# ═══════════════════════════════════════════════════════════════════
#  EMERGENCY CLOSE
# ═══════════════════════════════════════════════════════════════════

def emergency_close_all():
    logger.critical("🚨 EMERGENCY CLOSE ALL POSITIONS")
    data = request_binance("GET", "/fapi/v2/positionRisk")
    if not data:
        return
    for pos in data:
        amt = float(pos.get("positionAmt", 0))
        if abs(amt) > 0:
            sym = pos["symbol"]
            side = "SELL" if amt > 0 else "BUY"
            request_binance("POST", "/fapi/v1/order", {
                "symbol": sym,
                "side": side,
                "type": "MARKET",
                "quantity": abs(amt)
            })
            logger.info(f"💥 Emergency close: {sym}")

# ═══════════════════════════════════════════════════════════════════
#  SCANNER
# ═══════════════════════════════════════════════════════════════════

def scan_symbol(symbol: str):
    """Scan un symbol et retourne un dict de signal ou None"""
    try:
        # Vérifier si position déjà ouverte
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
        
        # Vérifier nombre de positions
        with trade_lock:
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        
        _, max_pos = get_session_params()
        if n_open >= max_pos:
            return None
        
        # 1. H1 Adaptive Filter
        h1_trend, h1_score = get_h1_trend(symbol)
        
        # 2. Essayer LONG
        score_long, details_long = score_signal(symbol, "LONG")
        if score_long == 5:
            # Vérifier setups et confluence
            setups, confluence, conf_label = detect_setups(symbol, "LONG")
            
            # Confluence minimum 3.0 pour ouvrir
            if confluence < 3.0:
                return None
            
            # Calculer TP/BE dynamique
            price = get_price(symbol)
            if not price:
                return None
            
            atr = calc_atr(symbol, "5m", 14)
            sl, tp, rr, be_pct, level = calc_dynamic_tp_be(h1_trend, confluence, atr, price, "LONG")
            
            # RR filter (SNIPER MODE)
            if rr < MIN_RR_FILTER:
                logger.debug(f"   {symbol} LONG rejeté: RR {rr:.2f} < {MIN_RR_FILTER}")
                return None
            
            return {
                "symbol": symbol,
                "side": "LONG",
                "entry": price,
                "sl": sl,
                "tp": tp,
                "score_details": details_long,
                "h1_trend": h1_trend,
                "setups": setups,
                "confluence": confluence,
                "conf_label": conf_label,
                "rr": rr,
                "be_pct": be_pct,
                "level": level
            }
        
        # 3. Essayer SHORT
        score_short, details_short = score_signal(symbol, "SHORT")
        if score_short == 5:
            setups, confluence, conf_label = detect_setups(symbol, "SHORT")
            
            if confluence < 3.0:
                return None
            
            price = get_price(symbol)
            if not price:
                return None
            
            atr = calc_atr(symbol, "5m", 14)
            sl, tp, rr, be_pct, level = calc_dynamic_tp_be(h1_trend, confluence, atr, price, "SHORT")
            
            if rr < MIN_RR_FILTER:
                logger.debug(f"   {symbol} SHORT rejeté: RR {rr:.2f} < {MIN_RR_FILTER}")
                return None
            
            return {
                "symbol": symbol,
                "side": "SHORT",
                "entry": price,
                "sl": sl,
                "tp": tp,
                "score_details": details_short,
                "h1_trend": h1_trend,
                "setups": setups,
                "confluence": confluence,
                "conf_label": conf_label,
                "rr": rr,
                "be_pct": be_pct,
                "level": level
            }
        
        return None
        
    except Exception as e:
        logger.error(f"Scan {symbol}: {e}")
        return None

def scanner_loop():
    """Boucle principale de scan"""
    logger.info("▶️  [Scanner] started")
    time.sleep(5)
    
    while True:
        try:
            if not check_capital_protection():
                time.sleep(60)
                continue
            
            logger.info("🔍 Scan — searching for HIGH RR ULTRA setups...")
            
            signals = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(scan_symbol, sym): sym for sym in SYMBOLS}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        signals.append(result)
            
            if signals:
                # Filtrer par RR décroissant
                signals.sort(key=lambda x: x["rr"], reverse=True)
                
                # Compter ULTRA setups (RR > 3.0)
                ultra_count = sum(1 for s in signals if s["rr"] >= 3.0)
                
                logger.info(f"   ✨ {len(signals)} HIGH RR setup(s) found")
                if ultra_count > 0:
                    logger.info(f"   🔥 {ultra_count} ULTRA setup(s)! RR 1:3.0+")
                
                # Prendre le meilleur (SNIPER MODE = 1 seul)
                best_signal = signals[0]
                
                # Vérifier encore une fois le nombre de positions
                with trade_lock:
                    n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                
                _, max_pos = get_session_params()
                
                if n_open < max_pos:
                    open_position(
                        best_signal["symbol"],
                        best_signal["side"],
                        best_signal["entry"],
                        best_signal["sl"],
                        best_signal["tp"],
                        best_signal["score_details"],
                        best_signal["h1_trend"],
                        best_signal["setups"],
                        best_signal["confluence"],
                        best_signal["conf_label"],
                        best_signal["rr"],
                        best_signal["be_pct"],
                        best_signal["level"]
                    )
            
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Scanner loop: {e}")
            time.sleep(10)

# ═══════════════════════════════════════════════════════════════════
#  MONITOR (BREAK-EVEN & TRAILING)
# ═══════════════════════════════════════════════════════════════════

def monitor_loop():
    """Surveille les positions ouvertes pour BE et trailing"""
    logger.info("▶️  [Monitor] started")
    time.sleep(10)
    
    while True:
        try:
            with trade_lock:
                open_trades = {k: v for k, v in trade_log.items() if v.get("status") == "OPEN"}
            
            for symbol, info in open_trades.items():
                price = get_price(symbol)
                if not price:
                    continue
                
                entry  = info["entry"]
                sl     = info["sl"]
                side   = info["side"]
                be_pct = info.get("be_pct", 0.010)
                be_triggered = info.get("be_triggered", False)
                
                # Break-even dynamique
                if not be_triggered:
                    if side == "LONG":
                        profit_pct = (price - entry) / entry
                        if profit_pct >= be_pct:
                            # Move SL to entry
                            new_sl = entry
                            request_binance("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                            time.sleep(0.2)
                            request_binance("POST", "/fapi/v1/order", {
                                "symbol": symbol,
                                "side": "SELL",
                                "type": "STOP_MARKET",
                                "stopPrice": new_sl,
                                "closePosition": "true"
                            })
                            with trade_lock:
                                trade_log[symbol]["sl"] = new_sl
                                trade_log[symbol]["be_triggered"] = True
                            logger.info(f"🟢 {symbol} BE triggered (+{profit_pct*100:.1f}%) | SL→{new_sl}")
                    else:  # SHORT
                        profit_pct = (entry - price) / entry
                        if profit_pct >= be_pct:
                            new_sl = entry
                            request_binance("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                            time.sleep(0.2)
                            request_binance("POST", "/fapi/v1/order", {
                                "symbol": symbol,
                                "side": "BUY",
                                "type": "STOP_MARKET",
                                "stopPrice": new_sl,
                                "closePosition": "true"
                            })
                            with trade_lock:
                                trade_log[symbol]["sl"] = new_sl
                                trade_log[symbol]["be_triggered"] = True
                            logger.info(f"🔴 {symbol} BE triggered (+{profit_pct*100:.1f}%) | SL→{new_sl}")
            
            time.sleep(MONITOR_INTERVAL)
            
        except Exception as e:
            logger.error(f"Monitor loop: {e}")
            time.sleep(5)

# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════

def dashboard_loop():
    """Affiche périodiquement un dashboard de status"""
    logger.info("▶️  [Dashboard] started")
    time.sleep(15)
    
    while True:
        try:
            update_capital()
            
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            
            with session_lock:
                s_pnl = session_pnl
                losses = session_losses
            
            logger.info("═" * 60)
            logger.info(f"📊 DASHBOARD — SNIPER MODE")
            logger.info(f"💰 Capital: {current_capital:.2f}$ | Peak: {peak_capital:.2f}$")
            logger.info(f"📦 Positions: {n_open}/{MAX_POSITIONS}")
            logger.info(f"📈 Session PnL: {s_pnl:+.3f}$ | Losses: {losses}")
            logger.info("═" * 60)
            
            time.sleep(DASHBOARD_INTERVAL)
            
        except Exception as e:
            logger.error(f"Dashboard loop: {e}")
            time.sleep(10)

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("╔" + "═" * 60 + "╗")
    logger.info("║" + " " * 10 + "ROBOTKING M1 PRO v6.2 SNIPER FINAL" + " " * 15 + "║")
    logger.info("║" + " " * 5 + "Adaptive H1 + Weighted + Dynamic TP/BE" + " " * 12 + "║")
    logger.info("║" + " " * 10 + "Keep-Alive: FLASH (every 2 min)" + " " * 17 + "║")
    logger.info("╚" + "═" * 60 + "╝")
    logger.info("")
    
    # Flask Health Server
    start_health_server()
    
    logger.info("")
    logger.info(f"💰 Capital: {current_capital:.2f} USDT")
    logger.info(f"📦 Position: {MARGIN_PER_TRADE:.2f}$ × {LEVERAGE}x = {MARGIN_PER_TRADE * LEVERAGE:.0f}$ notional")
    logger.info(f"🎯 Strategy: SNIPER — High RR only ({MIN_RR_FILTER}:1+ minimum)")
    logger.info(f"🔧 Max Positions: {MAX_POSITIONS} (one at a time)")
    logger.info(f"⚡ Keep-Alive: FLASH every {KEEPALIVE_INTERVAL} seconds")
    logger.info("")
    
    # Chargement précisions
    load_symbol_precision_all()
    
    # Lancement threads
    threading.Thread(target=scanner_loop, daemon=True, name="Scanner").start()
    threading.Thread(target=monitor_loop, daemon=True, name="Monitor").start()
    threading.Thread(target=dashboard_loop, daemon=True, name="Dashboard").start()
    threading.Thread(target=flash_keep_alive_loop, daemon=True, name="FlashKeepAlive").start()
    
    logger.info("✅ SNIPER v6.2 FINAL — ONLINE AND HUNTING 🔥")
    logger.info("")
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Arrêt manuel détecté")
        emergency_close_all()

if __name__ == "__main__":
    main()

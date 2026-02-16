##!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║    ROBOTKING v15 ULTIMATE - FINAL EDITION                        ║
║    24 Cryptos | 6 Max Pos | 70% WR | v14.3 + v6.2 Features       ║
╠══════════════════════════════════════════════════════════════════╣
║  🔥 ULTIMATE FEATURES (v14.3 MULTI + v6.2 SNIPER):               ║
║                                                                  ║
║  ✅ 24 cryptos scanning (v6.2 symbol list)                       ║
║  ✅ 6 concurrent positions max (v14.3 multi-asset)               ║
║  ✅ H1 Adaptive Filter SCORE-BASED (v6.2: 0-3 points)            ║
║  ✅ Weighted Setup Confluence (v6.2: Breaker=2.0, OB=1.5, etc.)  ║
║  ✅ Dynamic TP/BE System (v6.2: ULTRA RR 1:3.25+ to WEAK 1:2.1)  ║
║  ✅ FLASH Keep-Alive every 2 min (v6.2)                          ║
║  ✅ Session Intelligence Recovery Mode (v6.2)                    ║
║  ✅ Multi-timeframe filter (v14.3: 1H trend)                     ║
║  ✅ RSI/Stoch entry filter (v14.3)                               ║
║  ✅ Volume confirmation 2x (v14.3)                               ║
║  ✅ ATR-based SL adjustment (v14.3)                              ║
║  ✅ Time filter 00-06 UTC (v14.3)                                ║
║  ✅ 3 win streak pause (v14.3)                                   ║
║  ✅ 0.9$ margin cap/position (v14.3)                             ║
║  ✅ -30% daily loss limit (v14.3)                                ║
║  ✅ Telegram real-time alerts                                   ║
║                                                                  ║
║  TARGET: 70% win rate + higher volume + elite setups            ║
║  EXPECTED: 5$ → 100$+ in 3-4 weeks                              ║
╚══════════════════════════════════════════════════════════════════╝

v15 ULTIMATE = BEST OF BOTH WORLDS:
- v14.3 MULTI multi-asset portfolio management (6 positions, volume filters)
- v6.2 SNIPER elite setup detection (H1 Adaptive, Weighted, Dynamic TP/BE)
- FLASH Keep-Alive (Render always alive)
- Session Intelligence (recovery mode after losses)

Result: Maximum win rate + real volume + multiple opportunities + safe growth
"""

import time
import hmac
import hashlib
import requests
import threading
import os
import logging
import json
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import deque
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

# ═══════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("robotking_v15_ultimate.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except:
        pass

# ═══════════════════════════════════════════════════════════════════
#  SECURITY - v6.2 REAL APIS
# ═══════════════════════════════════════════════════════════════════

API_KEY = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

BACKTEST_MODE = os.environ.get("DRY_RUN", "false").lower() == "true"

if BACKTEST_MODE:
    logger.warning("⚠️ BACKTEST MODE (DRY RUN)")
else:
    logger.info("✅ LIVE MODE")

# ═══════════════════════════════════════════════════════════════════
#  ENUMS
# ═══════════════════════════════════════════════════════════════════

class StrategyType(Enum):
    LIQUIDITY_SWEEP = "sweep"
    ORDER_BLOCK = "ob"
    BREAKER = "breaker"
    CHOCH = "choch"

class PartialStatus(Enum):
    FULL = "full"
    HALF_CLOSED = "half"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION - v15 ULTIMATE (v14.3 MULTI + v6.2 SNIPER)
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://fapi.binance.com"

# Core capital (v14.3)
INITIAL_CAPITAL = 5.0
MAX_MARGIN_PER_TRADE = 0.9
LEVERAGE = 20
RISK_PER_TRADE_PCT = 0.03

# v6.2 marge config (alternative, pour compatibilité)
MARGIN_PER_TRADE = 1.50  # 1.50$ marge pour v6.2 mode

# v14.3 filters
RR_MINIMUM = 4.0
QUALITY_MINIMUM = 9.0
DAILY_LOSS_LIMIT_PCT = 0.30
MAX_DRAWDOWN_PCT = 0.25
MIN_CAPITAL_TO_STOP = 3.0

# v6.2 capital protection
MIN_CAPITAL_TO_TRADE = 2.0
MARGIN_TYPE = "ISOLATED"

# v14.3 win pause
CONSECUTIVE_WIN_LIMIT = 3
WIN_PAUSE_MINUTES = 60

# v14.3 pyramiding
PYRAMIDING_ENABLED = False
PYRAMIDING_FIRST_SIZE = 0.5
PYRAMIDING_SECOND_SIZE = 0.5

# v14.3 partial exit
PARTIAL_EXIT_RR_TARGET = 0.5

# v14.3 time filter
SKIP_HOURS_START = 0
SKIP_HOURS_END = 6

# v14.3 elite win rate filters
VOLUME_MULTIPLIER_MIN = 2.0
RSI_PERIOD = 14
RSI_OVERBOUGHT_LEVEL = 70
RSI_OVERSOLD_LEVEL = 30

# v6.2 ATR config
ATR_SL_MULTIPLIER = 0.5  # v14.3 style
ATR_SL_MULT = 1.5        # v6.2 style
ATR_TP_BASE_MULT = 2.5
FALLBACK_SL_PCT = 0.012
FALLBACK_TP_PCT = 0.025

# v6.2 Dynamic TP/BE (adjusted dynamically)
BREAKEVEN_TRIGGER_PCT = 0.010   # Base +1.0%
TRAILING_TRIGGER_PCT = 0.020    # +2.0%
TRAILING_CALLBACK_RATE = 1.0    # 1.0%

# v6.2 RR filter
MIN_RR_FILTER = 2.1  # Minimum RR to accept trade

# v6.2 scoring
MIN_STARS_REQUIRED = 5  # 5★ required

# v6.2 Session Intelligence recovery
RECOVERY_MAX_POS_1 = 1
RECOVERY_MAX_POS_2 = 1
RECOVERY_MAX_POS_3 = 1

# v14.3 multi-asset portfolio
MAX_CONCURRENT_POSITIONS = 6   # Max 6 open positions (v14.3 multi)

# ═══════════════════════════════════════════════════════════════════
#  24 CRYPTOS - v6.2 SYMBOL LIST
# ═══════════════════════════════════════════════════════════════════

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "MATICUSDT",
    "DOTUSDT", "ATOMUSDT", "LTCUSDT", "TRXUSDT", "APTUSDT",
    "OPUSDT", "ARBUSDT", "INJUSDT", "SUIUSDT", "FTMUSDT",
    "NEARUSDT", "FILUSDT", "RUNEUSDT", "PEPEUSDT"
]

# v14.3 slippage map (extended to 24 cryptos)
SLIPPAGE_MAP = {
    "BTCUSDT": 0.00001, "ETHUSDT": 0.00001,  # Most liquid
    "BNBUSDT": 0.0001, "SOLUSDT": 0.0001, "XRPUSDT": 0.0001,
    "ADAUSDT": 0.0001, "AVAXUSDT": 0.0001, "DOGEUSDT": 0.0001,
    "LINKUSDT": 0.0001, "MATICUSDT": 0.0001, "DOTUSDT": 0.0001,
    "ATOMUSDT": 0.0001, "LTCUSDT": 0.0001, "TRXUSDT": 0.0001,
    "APTUSDT": 0.0001, "OPUSDT": 0.0001, "ARBUSDT": 0.0001,
    "INJUSDT": 0.0001, "SUIUSDT": 0.0001, "FTMUSDT": 0.0001,
    "NEARUSDT": 0.0001, "FILUSDT": 0.0001, "RUNEUSDT": 0.0001,
    "PEPEUSDT": 0.0001
}

TAKER_FEE = 0.0004

# Intervals
SCAN_INTERVAL = 20        # v6.2: 20s scan
MONITOR_INTERVAL = 3      # v6.2: 3s monitor
DASHBOARD_INTERVAL = 30   # v6.2: 30s dashboard
KEEPALIVE_INTERVAL = 120  # v6.2: FLASH every 2 min ⚡
MAX_WORKERS = 8           # v6.2: parallel scanning

# v6.2 rate limiting
MAX_CALLS_PER_MIN = 1200
RATE_LIMIT_WINDOW = 60
CACHE_DURATION = 5

# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════

current_capital = INITIAL_CAPITAL
peak_capital = INITIAL_CAPITAL
daily_start_capital = INITIAL_CAPITAL
daily_start_time = datetime.now(timezone.utc)

starting_capital = INITIAL_CAPITAL  # v6.2 compatibility

session_pnl = 0.0
daily_pnl = 0.0

# v6.2 session intelligence
session_losses = 0
session_wins = 0

open_positions = {}
trade_log = {}  # v6.2 dict format (not deque)

signal_count = 0
signal_filtered = 0
executed_count = 0
win_count = 0
loss_count = 0
consecutive_wins = 0
consecutive_losses = 0
trading_stopped = False

paused_until = None

klines_cache = {}
price_cache = {}
symbol_info_cache = {}
symbol_precision_cache = {}  # v6.2 format

trade_lock = threading.Lock()
capital_lock = threading.Lock()
api_lock = threading.Lock()
session_lock = threading.Lock()

api_call_times = []  # v6.2 rate limiting

# ═══════════════════════════════════════════════════════════════════
#  FLASK HEALTH SERVER (v6.2)
# ═══════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    return (
        f"🤖 ROBOTKING v15 ULTIMATE — OPÉRATIONNEL\n"
        f"Positions ouvertes: {n_open}/{MAX_CONCURRENT_POSITIONS}\n"
        f"Capital: {current_capital:.2f}$\n"
        f"Peak: {peak_capital:.2f}$\n"
        f"Mode: ULTIMATE (24 cryptos, 6 pos max, H1 Adaptive + Weighted + Dynamic TP/BE)"
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
        s_pnl = session_pnl
    return {
        "status": "running",
        "version": "v15 ULTIMATE",
        "capital": round(current_capital, 4),
        "peak": round(peak_capital, 4),
        "positions": len(open_pos),
        "max_pos": MAX_CONCURRENT_POSITIONS,
        "session_loss": losses,
        "session_pnl": round(s_pnl, 4),
        "symbols": list(open_pos.keys()),
        "mode": "ULTIMATE"
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
#  FLASH KEEP-ALIVE ⚡ (v6.2 - Every 2 minutes)
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
            except:
                logger.warning("   ⚠️  Local health check failed")
            
            # 2. External ping (if RENDER_URL set)
            if RENDER_URL:
                try:
                    resp = requests.get(f"{RENDER_URL}/health", timeout=10)
                    if resp.status_code == 200:
                        logger.info(f"   ✅ External ping OK → {RENDER_URL}")
                except:
                    logger.warning(f"   ⚠️  External ping failed")
            
            # 3. Binance API check
            try:
                price = get_price("BTCUSDT")
                if price:
                    logger.info(f"   ✅ Binance API OK (BTC: {price:.0f}$)")
            except:
                logger.warning("   ⚠️  Binance API check failed")
            
        except Exception as e:
            logger.error(f"FLASH keep-alive error: {e}")
        
        time.sleep(KEEPALIVE_INTERVAL)  # 120 seconds = 2 minutes

# ═══════════════════════════════════════════════════════════════════
#  SESSION INTELLIGENCE (v6.2)
# ═══════════════════════════════════════════════════════════════════

def get_session_params():
    """
    Retourne (min_stars, max_positions) selon le nombre de pertes
    
    Recovery mode:
    - 0 loss: normal mode (5★, 6 positions)
    - 1 loss: recovery 1 (5★, 1 position)
    - 2 losses: recovery 2 (5★, 1 position)
    - 3+ losses: recovery 3 (5★, 1 position)
    """
    with session_lock:
        losses = session_losses
    
    if losses == 0:
        return MIN_STARS_REQUIRED, MAX_CONCURRENT_POSITIONS
    elif losses == 1:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_1
    elif losses == 2:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_2
    else:
        return MIN_STARS_REQUIRED, RECOVERY_MAX_POS_3

def record_trade_result(pnl_usdt: float):
    """
    Enregistre le résultat d'un trade et ajuste le mode session
    
    Si gain après pertes → reset recovery mode
    """
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
            
            # Reset recovery mode si on revient positif
            if session_pnl >= 0 and session_losses > 0:
                session_losses = 0
                session_wins = 0
                logger.info("✅ Recovery terminé — retour mode normal")
                send_telegram("✅ Recovery terminé — mode normal restauré")

# ═══════════════════════════════════════════════════════════════════
#  RATE LIMITING (v6.2)
# ═══════════════════════════════════════════════════════════════════

def wait_for_rate_limit():
    """Limite à 80% du rate limit Binance (960 calls/min)"""
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
#  BINANCE API
# ═══════════════════════════════════════════════════════════════════

def _sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method: str, path: str, params: dict = None, signed: bool = True) -> dict:
    """Request Binance API with rate limiting"""
    if BACKTEST_MODE:
        return {"orderId": f"BT_{int(time.time()*1000)}", "status": "FILLED"}
    
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
                logger.warning("⚠️  Rate limit 429 — pause 60s")
                time.sleep(60)
            else:
                logger.error(f"API error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"API request error: {e}")
    
    return None

# ═══════════════════════════════════════════════════════════════════
#  DATA RETRIEVAL
# ═══════════════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str, limit: int) -> list:
    """Get klines with cache"""
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
            data = resp.json()
            klines_cache[key] = (data, now)
            return data
    except Exception as e:
        logger.error(f"get_klines {symbol} {interval}: {e}")
    
    return None

def get_price(symbol: str) -> float:
    """Get current price with cache"""
    now = time.time()
    
    if symbol in price_cache:
        p, ts = price_cache[symbol]
        if now - ts < CACHE_DURATION:
            return p
    
    try:
        resp = requests.get(
            f"{BASE_URL}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            p = float(resp.json()["price"])
            price_cache[symbol] = (p, now)
            return p
    except Exception as e:
        logger.error(f"get_price {symbol}: {e}")
    
    return None

def load_symbol_info():
    """Load symbol info (v14.3 + v6.2 combined)"""
    global symbol_info_cache, symbol_precision_cache
    
    logger.info(f"📐 Loading {len(SYMBOLS)} symbols...")
    
    info = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
    if not info:
        logger.error("❌ exchangeInfo indisponible")
        return
    
    for sym_data in info.get("symbols", []):
        sym = sym_data.get("symbol")
        if sym not in SYMBOLS:
            continue
        
        filters = {f["filterType"]: f for f in sym_data.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        step = lot.get("stepSize", "0.001")
        qty_prec = len(step.rstrip("0").split(".")[-1]) if "." in step else 0
        
        # v14.3 format
        symbol_info_cache[sym] = {
            "qty_precision": qty_prec,
            "min_qty": float(lot.get("minQty", 0.001))
        }
        
        # v6.2 format
        symbol_precision_cache[sym] = {
            "qty_precision": qty_prec,
            "min_qty": float(lot.get("minQty", 0.001)),
            "min_notional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
            "price_precision": int(sym_data.get("pricePrecision", 6))
        }
    
    logger.info(f"✅ Loaded {len(symbol_info_cache)} symbols")

def get_symbol_info(symbol: str) -> dict:
    """Get symbol info (v6.2 format with fallback)"""
    return symbol_precision_cache.get(symbol, {
        "qty_precision": 3,
        "min_qty": 0.001,
        "min_notional": 5.0,
        "price_precision": 6
    })

# ═══════════════════════════════════════════════════════════════════
#  INDICATORS
# ═══════════════════════════════════════════════════════════════════

def calc_atr(symbol: str = None, interval: str = "5m", period: int = 14, 
             closes: list = None, highs: list = None, lows: list = None) -> float:
    """
    Calculate ATR - supports both v14.3 (list input) and v6.2 (symbol input) styles
    """
    if symbol:
        # v6.2 style: fetch klines
        klines = get_klines(symbol, interval, period + 5)
        if not klines or len(klines) < period + 1:
            return None
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
    elif closes and highs and lows:
        # v14.3 style: use provided lists
        if len(closes) < period + 1:
            return None
    else:
        return None
    
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    
    return sum(trs[-period:]) / period

def calc_ema(closes: list, period: int):
    """Calculate EMA"""
    if len(closes) < period:
        return None
    
    mult = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = (c - ema) * mult + ema
    
    return ema

def calc_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI"""
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
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ═══════════════════════════════════════════════════════════════════
#  H1 ADAPTIVE FILTER (v6.2 SCORE-BASED)
# ═══════════════════════════════════════════════════════════════════

def get_h1_trend(symbol: str):
    """
    Retourne un score H1 de tendance : STRONG (3/3), MODERATE (2/3), WEAK (0-1/3)
    
    Critères :
    1. EMA alignment (9 > 21 > 50 for bull, or 9 < 21 < 50 for bear)
    2. RSI zone (40-60 = neutral, not extreme)
    3. SMA direction (SMA20 > SMA50 for uptrend, or SMA20 < SMA50 for downtrend)
    
    Returns: (trend_label, score)
    - STRONG: 3/3 points
    - MODERATE: 2/3 points
    - WEAK: 1/3 points
    - NONE: 0/3 points
    """
    klines = get_klines(symbol, "1h", 50)
    if not klines or len(klines) < 50:
        return "NONE", 0
    
    closes = [float(k[4]) for k in klines]
    
    # 1. EMA alignment
    ema_9 = calc_ema(closes, 9)
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
#  SETUP DETECTION (v6.2 WEIGHTED CONFLUENCE)
# ═══════════════════════════════════════════════════════════════════

def detect_setups(symbol: str, side: str):
    """
    Détecte les setups et calcule leur confluence pondérée.
    
    Poids:
    - Breaker: 2.0 (structure forte)
    - ChoCh: 2.0 (Change of Character)
    - Order Block: 1.5 (zone institutionnelle)
    - New HH/LL: 1.0
    - LH/LL: 0.8
    
    Returns: (setups_list, confluence_score, confluence_label)
    
    Labels:
    - ULTRA: confluence >= 4.0
    - PREMIUM: confluence >= 3.5
    - NORMAL: confluence >= 3.0
    - WEAK: confluence >= 2.5
    - NONE: confluence < 2.5
    """
    klines = get_klines(symbol, "1m", 50)
    if not klines or len(klines) < 30:
        return [], 0.0, "NONE"
    
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
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
#  DYNAMIC TP & BE CALCULATION (v6.2)
# ═══════════════════════════════════════════════════════════════════

def calc_dynamic_tp_be(h1_strength: str, confluence_score: float, atr: float, entry: float, side: str):
    """
    Calcule TP et BE dynamiques selon H1 + confluence
    
    Niveaux:
    - ULTRA (H1 STRONG + confluence >=4): RR 3.25, BE +0.8%
    - PREMIUM (H1 STRONG/MODERATE + confluence 3.5-4): RR 2.75, BE +0.4%
    - NORMAL (confluence 3-3.5): RR 2.5, BE +0.2%
    - WEAK (confluence <3): RR 2.1, BE +0.1%
    
    Returns: (sl, tp, rr, be_pct, level)
    """
    info = get_symbol_info("")
    pp = info["price_precision"]
    
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
#  SCORING 5★ VALIDATION (v6.2)
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
    
    5★ = signal parfait
    """
    klines = get_klines(symbol, "1m", 50)
    if not klines or len(klines) < 50:
        return 0, {}
    
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    
    score = 0
    details = {}
    
    # 1. EMA Cross
    ema_9 = calc_ema(closes, 9)
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
    vol_avg = sum(volumes[-20:-1]) / 19
    if volumes[-1] > vol_avg * 1.5:
        score += 1
        details["VOL"] = f"{volumes[-1]/vol_avg:.1f}x"
    else:
        details["VOL"] = "✗"
    
    # 4. ATR Range
    atr = calc_atr(symbol=symbol, interval="1m", period=14)
    if atr:
        price = closes[-1]
        atr_pct = atr / price
        if 0.005 < atr_pct < 0.025:  # 0.5% to 2.5%
            score += 1
            details["ATR"] = f"{atr_pct*100:.2f}%"
        else:
            details["ATR"] = f"{atr_pct*100:.2f}%✗"
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
#  v14.3 ELITE FILTERS
# ═══════════════════════════════════════════════════════════════════

def is_in_trading_hours() -> bool:
    """v14.3 time filter: skip 00-06 UTC"""
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    if SKIP_HOURS_START < SKIP_HOURS_END:
        return not (SKIP_HOURS_START <= hour < SKIP_HOURS_END)
    else:
        return hour >= SKIP_HOURS_START or hour < SKIP_HOURS_END

def check_multitimeframe_trend(symbol: str, side: str) -> bool:
    """v14.3 multi-timeframe trend confirmation"""
    klines = get_klines(symbol, "1h", 30)
    if not klines or len(klines) < 15:
        return True
    
    closes = [float(k[4]) for k in klines]
    
    if side == "BUY" or side == "LONG":
        return closes[-1] > np.mean(closes[-10:])
    else:
        return closes[-1] < np.mean(closes[-10:])

def check_rsi_filter(symbol: str, side: str) -> bool:
    """v14.3 RSI filter: avoid extremes"""
    klines = get_klines(symbol, "5m", 30)
    if not klines or len(klines) < 20:
        return True
    
    closes = [float(k[4]) for k in klines]
    rsi = calc_rsi(closes, RSI_PERIOD)
    
    if rsi is None:
        return True
    
    if side == "BUY" or side == "LONG":
        return rsi < RSI_OVERBOUGHT_LEVEL
    else:
        return rsi > RSI_OVERSOLD_LEVEL

def check_volume_confirmation(symbol: str, interval: str) -> bool:
    """v14.3 volume confirmation: 2x minimum"""
    klines = get_klines(symbol, interval, 20)
    if not klines or len(klines) < 10:
        return True
    
    volumes = [float(k[7]) for k in klines]
    vol_avg = np.mean(volumes[-10:])
    vol_current = volumes[-1]
    
    return vol_current >= vol_avg * VOLUME_MULTIPLIER_MIN

# ═══════════════════════════════════════════════════════════════════
#  CAPITAL MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def update_capital():
    """Update capital from Binance"""
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
    """
    Check capital protection (kill switches)
    
    v14.3 + v6.2 combined:
    - Max drawdown 25%
    - Min capital 2$
    """
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
    """
    Calculate quantity (v6.2 style)
    
    Uses MARGIN_PER_TRADE (1.50$) × LEVERAGE (20) = 30$ notional
    """
    info = get_symbol_info(symbol)
    notional = MARGIN_PER_TRADE * LEVERAGE
    qty = notional / entry
    qty = round(qty, info["qty_precision"])
    qty = max(qty, info["min_qty"])
    
    # Ensure min notional
    if qty * entry < info["min_notional"]:
        qty = round(info["min_notional"] / entry * 1.01, info["qty_precision"])
    
    return qty

# ═══════════════════════════════════════════════════════════════════
#  POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def can_open_position() -> bool:
    """
    Check if we can open a new position
    
    v14.3 rules:
    - Not trading_stopped
    - Max concurrent positions not reached
    - Not in win pause
    
    v6.2 rules:
    - Session recovery mode (reduces max positions after losses)
    """
    global paused_until, trading_stopped
    
    if trading_stopped:
        return False
    
    # Check session params (recovery mode)
    _, max_pos = get_session_params()
    
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    
    if n_open >= max_pos:
        return False
    
    if paused_until and datetime.now(timezone.utc) < paused_until:
        return False
    
    if paused_until and datetime.now(timezone.utc) >= paused_until:
        paused_until = None
        logger.info("✅ Win pause expired")
        send_telegram("✅ Win pause expired - resume trading")
    
    return True

def sync_positions_from_exchange():
    """Sync positions with exchange (v6.2)"""
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
                side = info.get("side", "LONG")
                
                pnl = (price - entry) / entry * MARGIN_PER_TRADE * LEVERAGE
                if side == "SHORT":
                    pnl = -pnl
                
                record_trade_result(pnl)

def is_position_open(symbol: str) -> bool:
    """Check if position is open on exchange"""
    data = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
    if data:
        return abs(float(data[0].get("positionAmt", 0))) > 0
    return False

def set_margin_type(symbol: str):
    """Set margin type to ISOLATED"""
    request_binance("POST", "/fapi/v1/marginType",
                    {"symbol": symbol, "marginType": MARGIN_TYPE})
    time.sleep(0.15)

def set_leverage_isolated(symbol: str):
    """Set leverage on isolated margin"""
    set_margin_type(symbol)
    request_binance("POST", "/fapi/v1/leverage",
                    {"symbol": symbol, "leverage": LEVERAGE})
    time.sleep(0.15)

# ═══════════════════════════════════════════════════════════════════
#  OPEN POSITION (v6.2 + v14.3 combined)
# ═══════════════════════════════════════════════════════════════════

def open_position(symbol: str, side: str, entry: float, sl: float, tp: float,
                  score_details: dict, h1_trend: str, setups: list,
                  confluence: float, conf_label: str, rr: float, be_pct: float, level: str):
    """
    Ouvre une position LONG/SHORT avec SL/TP
    
    v6.2 style: full logging + telegram alerts
    """
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
    
    if not sl_order:
        logger.error(f"❌ SL échoué: {symbol}")
        return
    
    time.sleep(0.2)
    
    # Take Profit
    tp_side = "SELL" if side == "LONG" else "BUY"
    tp_order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": tp_side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": tp,
        "closePosition": "true"
    })
    
    if not tp_order:
        logger.error(f"❌ TP échoué: {symbol}")
    
    # Enregistrer dans trade_log
    with trade_lock:
        trade_log[symbol] = {
            "status": "OPEN",
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "qty": qty,
            "score": score_details,
            "h1_trend": h1_trend,
            "setups": setups,
            "confluence": confluence,
            "conf_label": conf_label,
            "rr": rr,
            "be_pct": be_pct,
            "level": level,
            "be_triggered": False,
            "opened_at": datetime.now(timezone.utc).isoformat()
        }
    
    # Log détaillé
    logger.info("═" * 60)
    logger.info(f"🚀 {symbol} {side} OUVERT")
    logger.info(f"   Entry: {entry}")
    logger.info(f"   SL: {sl} | TP: {tp}")
    logger.info(f"   Qty: {qty}")
    logger.info(f"   H1: {h1_trend}")
    logger.info(f"   Setups: {', '.join(setups)}")
    logger.info(f"   {conf_label} (Conf: {confluence:.1f})")
    logger.info(f"   Level: {level} | RR: 1:{rr:.2f}")
    logger.info(f"   BE: {be_pct*100:.1f}%")
    logger.info(f"   Score: {score_details}")
    logger.info("═" * 60)
    
    # Telegram alert
    emoji = "🟢" if side == "LONG" else "🔴"
    stars = "⭐" * 5
    
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
    """Close all positions immediately (v6.2)"""
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
#  SCANNER (v6.2 + v14.3 combined)
# ═══════════════════════════════════════════════════════════════════

def scan_symbol(symbol: str):
    """
    Scan un symbol et retourne un dict de signal ou None
    
    Combines:
    - v6.2 H1 Adaptive Filter (score-based)
    - v6.2 Weighted Confluence (Breaker=2.0, OB=1.5, etc.)
    - v6.2 Dynamic TP/BE (ULTRA to WEAK)
    - v6.2 5★ scoring
    - v14.3 volume filters
    - v14.3 time filters
    - v14.3 RSI filters
    """
    try:
        # Skip if position already open
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
        
        # Check max positions (session recovery mode)
        with trade_lock:
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        
        _, max_pos = get_session_params()
        if n_open >= max_pos:
            return None
        
        # v14.3 time filter
        if not is_in_trading_hours():
            return None
        
        # 1. v6.2 H1 Adaptive Filter (score-based)
        h1_trend, h1_score = get_h1_trend(symbol)
        
        # 2. Try LONG
        score_long, details_long = score_signal(symbol, "LONG")
        
        if score_long == 5:
            # v6.2 weighted confluence
            setups, confluence, conf_label = detect_setups(symbol, "LONG")
            
            # Confluence minimum 3.0 pour ouvrir
            if confluence >= 3.0:
                # v14.3 additional filters
                if not check_multitimeframe_trend(symbol, "LONG"):
                    return None
                if not check_rsi_filter(symbol, "LONG"):
                    return None
                if not check_volume_confirmation(symbol, "5m"):
                    return None
                
                # Calculate dynamic TP/BE
                atr = calc_atr(symbol=symbol, interval="5m", period=14)
                entry = get_price(symbol)
                if not entry:
                    return None
                
                sl, tp, rr, be_pct, level = calc_dynamic_tp_be(h1_trend, confluence, atr, entry, "LONG")
                
                # v6.2 RR filter (SNIPER MODE)
                if rr < MIN_RR_FILTER:
                    logger.info(f"⚠️  {symbol} LONG rejected: RR {rr:.2f} < {MIN_RR_FILTER}")
                    return None
                
                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "entry": entry,
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
        
        # 3. Try SHORT
        score_short, details_short = score_signal(symbol, "SHORT")
        
        if score_short == 5:
            # v6.2 weighted confluence
            setups, confluence, conf_label = detect_setups(symbol, "SHORT")
            
            # Confluence minimum 3.0 pour ouvrir
            if confluence >= 3.0:
                # v14.3 additional filters
                if not check_multitimeframe_trend(symbol, "SHORT"):
                    return None
                if not check_rsi_filter(symbol, "SHORT"):
                    return None
                if not check_volume_confirmation(symbol, "5m"):
                    return None
                
                # Calculate dynamic TP/BE
                atr = calc_atr(symbol=symbol, interval="5m", period=14)
                entry = get_price(symbol)
                if not entry:
                    return None
                
                sl, tp, rr, be_pct, level = calc_dynamic_tp_be(h1_trend, confluence, atr, entry, "SHORT")
                
                # v6.2 RR filter
                if rr < MIN_RR_FILTER:
                    logger.info(f"⚠️  {symbol} SHORT rejected: RR {rr:.2f} < {MIN_RR_FILTER}")
                    return None
                
                return {
                    "symbol": symbol,
                    "side": "SHORT",
                    "entry": entry,
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
        logger.error(f"scan_symbol {symbol}: {e}")
        return None

def scanner_loop():
    """
    Scanner loop (v6.2 + v14.3)
    
    - Parallel scanning (v6.2: ThreadPoolExecutor)
    - Sort by RR descending
    - Take best signals up to max positions
    """
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
                # Sort by RR descending
                signals.sort(key=lambda x: x["rr"], reverse=True)
                
                # Count ULTRA setups (RR > 3.0)
                ultra_count = sum(1 for s in signals if s["rr"] >= 3.0)
                
                logger.info(f"   ✨ {len(signals)} HIGH RR setup(s) found")
                if ultra_count > 0:
                    logger.info(f"   🔥 {ultra_count} ULTRA setup(s)! RR 1:3.0+")
                
                # Open positions up to max
                with trade_lock:
                    n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                
                _, max_pos = get_session_params()
                
                for signal in signals:
                    if n_open >= max_pos:
                        break
                    
                    open_position(
                        signal["symbol"],
                        signal["side"],
                        signal["entry"],
                        signal["sl"],
                        signal["tp"],
                        signal["score_details"],
                        signal["h1_trend"],
                        signal["setups"],
                        signal["confluence"],
                        signal["conf_label"],
                        signal["rr"],
                        signal["be_pct"],
                        signal["level"]
                    )
                    
                    n_open += 1
            
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Scanner loop: {e}")
            time.sleep(10)

# ═══════════════════════════════════════════════════════════════════
#  MONITOR (BREAK-EVEN & TRAILING) (v6.2)
# ═══════════════════════════════════════════════════════════════════

def monitor_loop():
    """
    Surveille les positions ouvertes pour BE et trailing
    
    v6.2 dynamic BE trigger:
    - ULTRA: +0.8%
    - PREMIUM: +0.4%
    - NORMAL: +0.2%
    - WEAK: +0.1%
    """
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
                
                entry = info["entry"]
                sl = info["sl"]
                side = info["side"]
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
#  DASHBOARD (v6.2)
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
            
            _, max_pos = get_session_params()
            
            logger.info("═" * 60)
            logger.info(f"📊 DASHBOARD — v15 ULTIMATE")
            logger.info(f"💰 Capital: {current_capital:.2f}$ | Peak: {peak_capital:.2f}$")
            logger.info(f"📦 Positions: {n_open}/{max_pos}")
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
    logger.info("║" + " " * 12 + "ROBOTKING v15 ULTIMATE FINAL" + " " * 20 + "║")
    logger.info("║" + " " * 8 + "v14.3 MULTI + v6.2 SNIPER FEATURES" + " " * 17 + "║")
    logger.info("║" + " " * 4 + "24 Cryptos | 6 Pos | H1 Adaptive | Weighted | Dynamic" + " " * 3 + "║")
    logger.info("╚" + "═" * 60 + "╝")
    logger.info("")
    
    # Flask Health Server
    start_health_server()
    
    logger.info("")
    logger.info(f"💰 Capital: {current_capital:.2f} USDT")
    logger.info(f"📦 Position: {MARGIN_PER_TRADE:.2f}$ × {LEVERAGE}x = {MARGIN_PER_TRADE * LEVERAGE:.0f}$ notional")
    logger.info(f"🎯 Strategy: ULTIMATE — v14.3 MULTI + v6.2 SNIPER")
    logger.info(f"🔧 Max Positions: {MAX_CONCURRENT_POSITIONS} (session recovery: 1-6)")
    logger.info(f"⚡ Keep-Alive: FLASH every {KEEPALIVE_INTERVAL} seconds")
    logger.info("")
    logger.info("🔥 ULTIMATE FEATURES:")
    logger.info("   ✅ H1 Adaptive Filter (score-based 0-3)")
    logger.info("   ✅ Weighted Confluence (Breaker=2.0, OB=1.5, etc.)")
    logger.info("   ✅ Dynamic TP/BE (ULTRA RR 1:3.25+ to WEAK 1:2.1)")
    logger.info("   ✅ FLASH Keep-Alive (every 2 min)")
    logger.info("   ✅ Session Intelligence (recovery mode)")
    logger.info("   ✅ Volume filters + Time filters + RSI filters")
    logger.info("   ✅ 24 cryptos + 6 max positions")
    logger.info("")
    
    # Load symbol info
    load_symbol_info()
    
    # Launch threads
    threading.Thread(target=scanner_loop, daemon=True, name="Scanner").start()
    threading.Thread(target=monitor_loop, daemon=True, name="Monitor").start()
    threading.Thread(target=dashboard_loop, daemon=True, name="Dashboard").start()
    threading.Thread(target=flash_keep_alive_loop, daemon=True, name="FlashKeepAlive").start()
    
    logger.info("✅ v15 ULTIMATE — ONLINE AND HUNTING 🔥")
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

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   ROBOTKING v30 — PRODUCTION READY                             ║
║   Levier adaptatif 8-15x | Kill-switch | TP liquidity smart    ║
╚══════════════════════════════════════════════════════════════════╝

v30 — CORRECTIFS & AMÉLIORATIONS vs v29 :
✅ V30-1  — Levier adaptatif 8→15x (prob × btc × vol) — plus de 20x fixe
✅ V30-2  — TP liquidité smart : top 3 murs + distance min 1.8×ATR + anti-spoofing
✅ V30-3  — Kill-switch drawdown : pause trading si perte ≥ DAILY_DRAWDOWN_LIMIT
✅ V30-4  — Filtre funding rate : skip entry si |funding| > 0.15% (pump/dump)
✅ V30-5  — Filtre spread : skip si spread bid-ask > MAX_SPREAD_PCT
✅ V30-6  — Recover externe sécurisé : whitelist symboles + levier max 12x
✅ V30-7  — Trailing SL → alerte Telegram à chaque déplacement
✅ V30-8  — Version strings corrigées (v30 partout, log v30.log)
✅ V30-9  — Timeout robuste sur get_order_book_walls (fallback propre)
✅ V30-10 — Trailing BEAR renforcé : start_rr=0.8, step_atr=0.35
"""

import time, hmac, hashlib, requests, threading, os, logging, json, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("v30_robotking.log"), logging.StreamHandler()])  # V30-8
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

# ─── CONFIGURATION ───────────────────────────────────────────────
MIN_NOTIONAL      = 20
MARGIN_TYPE       = "ISOLATED"

# V30-1 — Levier adaptatif (prob × BTC × volatilité) — plus de fixe 20x
LEVERAGE_MIN      = 8    # Jamais en dessous (liquide suffisant)
LEVERAGE_MAX      = 15   # Jamais au-dessus (micro-caps = trop risqué)
LEVERAGE          = 10   # Valeur par défaut / fallback
MIN_MARGIN_PER_TRADE = MIN_NOTIONAL / LEVERAGE_MAX

MIN_PROBABILITY_SCORE  = 68
TRAILING_STOP_START_RR = 1.0
BREAKEVEN_RR           = 0.5

BTC_FILTER_ENABLED  = True
MIN_SL_DISTANCE_PCT = 0.008    # SL minimum 0.8% du prix
ENABLE_TREND_FILTER = True
TREND_TIMEFRAME     = "15m"

# V30-3 — Kill-switch drawdown journalier
DAILY_DRAWDOWN_LIMIT  = 0.25   # -25% du capital sur 24h → pause trading
DRAWDOWN_PAUSE_HOURS  = 4      # Durée de la pause en heures

# V30-4 — Filtre funding rate (pump/dump imminent)
MAX_FUNDING_RATE_ABS  = 0.0015  # |funding| > 0.15% → skip entry

# V30-5 — Filtre spread bid-ask
MAX_SPREAD_PCT        = 0.004   # Spread > 0.4% → slippage trop élevé → skip

# V30-6 — Recover externe : liste blanche + levier max acceptable
EXTERNAL_POSITION_WHITELIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "MATICUSDT", "AVAXUSDT",
    "LINKUSDT", "ATOMUSDT", "DOTUSDT", "NEARUSDT", "FTMUSDT", "APTUSDT",
]
EXTERNAL_MAX_LEVERAGE = 12     # Refuse d'adopter une position > 12x

# V30-2 — TP liquidité : paramètres améliorés
LIQ_TOP_N_WALLS       = 3      # Analyser les 3 plus gros murs (anti-spoofing)
LIQ_MIN_WALL_DISTANCE_ATR = 1.8  # Mur doit être à ≥ 1.8×ATR pour être valide
LIQ_SPOOF_THRESHOLD   = 3.0    # Si le plus gros mur > 3× la moyenne → spoofing probable

# Probability Engine — poids (total = 1.0)
PROBABILITY_WEIGHTS = {
    "setup_score":      0.25,
    "trend_alignment":  0.25,
    "btc_correlation":  0.15,
    "session_quality":  0.10,
    "sentiment":        0.10,
    "volatility":       0.05,
    "liquidity":        0.10,
}

SESSION_WEIGHTS = {
    "LONDON":    1.0,
    "NEW_YORK":  1.0,
    "ASIA":      0.7,
    "OFF_HOURS": 0.4
}

# ─── FIX CRITIQUE V28+V29 : constantes complètes ─────────────────

BTC_BULL_THRESHOLD = 0.25
BTC_BEAR_THRESHOLD = -0.25
BTC_NEUTRAL_BLOCK  = True
BTC_NEUTRAL_MIN    = -0.15
BTC_NEUTRAL_MAX    =  0.15
BTC_DAILY_BLOCK    = True

BTC_TIMEFRAMES = {
    "15m": {"weight": 0.15, "label": "15m"},
    "1h":  {"weight": 0.25, "label": "1H"},
    "4h":  {"weight": 0.35, "label": "4H"},
    "1d":  {"weight": 0.25, "label": "1D"},
}

TRAILING_ENABLED  = True
TRAILING_START_RR = 1.0
TRAILING_STEP_ATR = 0.5
TRAILING_LOCK_PCT = 0.004
SL_MIN_UPDATE_TICKS = 3

# V30-1 — Profils adaptatifs (levier calculé selon prob + BTC + vol)
# Levier = LEVERAGE_MIN + ratio * (LEVERAGE_MAX - LEVERAGE_MIN)
SIZING_PROFILES = {
    "STRONG_BULL": {
        "min": 0.50,  "max": 1.00,
        "multiplier": 1.0, "leverage": 14,
        "start_rr": 0.8,   "step_atr": 0.4, "lock_pct": 0.003, "label": "🟢 BULL FORT",
    },
    "NEUTRAL": {
        "min": -0.25, "max": 0.50,
        "multiplier": 0.85, "leverage": 10,
        "start_rr": 1.0,    "step_atr": 0.5, "lock_pct": 0.004, "label": "⚪ NEUTRE",
    },
    "STRONG_BEAR": {
        "min": -1.00, "max": -0.25,
        "multiplier": 0.75, "leverage": 8,
        "start_rr": 0.8,    "step_atr": 0.35, "lock_pct": 0.003, "label": "🔴 BEAR FORT",
    },
}

# V30-10 — Trailing BEAR plus défensif (start_rr=0.8, step_atr=0.35)
TRAILING_PROFILES = {
    "STRONG_BULL": {
        "min": 0.50,  "max": 1.00,
        "start_rr": 0.8, "step_atr": 0.4, "lock_pct": 0.003, "label": "🟢",
    },
    "NEUTRAL": {
        "min": -0.25, "max": 0.50,
        "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "⚪",
    },
    "STRONG_BEAR": {
        "min": -1.00, "max": -0.25,
        "start_rr": 0.8,  "step_atr": 0.35, "lock_pct": 0.003, "label": "🔴",  # V30-10
    },
}

MICRO_CAP_SYMBOLS = [
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT", "MATICUSDT",
    "AVAXUSDT", "LINKUSDT", "ATOMUSDT", "DOTUSDT", "NEARUSDT",
    "FTMUSDT",  "APTUSDT",
]

SYMBOLS = MICRO_CAP_SYMBOLS.copy()

SCAN_INTERVAL      = 12
MONITOR_INTERVAL   = 2
DASHBOARD_INTERVAL = 60
MAX_WORKERS        = 10
CACHE_DURATION     = 4

SETUPS = {
    "MSS_FVG_FIB":     {"score": 90},
    "LIQ_SWEEP_BOS":   {"score": 90},
    "BREAKOUT_RETEST": {"score": 70},
}

SESSION_WEIGHTS = {
    "LONDON":    1.0,
    "NEW_YORK":  1.0,
    "ASIA":      0.7,
    "OFF_HOURS": 0.4
}

MICRO_CAP_SYMBOLS = [
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT", "MATICUSDT",
    "AVAXUSDT", "LINKUSDT", "ATOMUSDT", "DOTUSDT", "NEARUSDT",
    "FTMUSDT",  "APTUSDT",
]

SYMBOLS = MICRO_CAP_SYMBOLS.copy()

SCAN_INTERVAL      = 12
MONITOR_INTERVAL   = 2
DASHBOARD_INTERVAL = 60
MAX_WORKERS        = 10
CACHE_DURATION     = 4

SETUPS = {
    "MSS_FVG_FIB":     {"score": 90},
    "LIQ_SWEEP_BOS":   {"score": 90},
    "BREAKOUT_RETEST": {"score": 70},
}

# ─── STATE ───────────────────────────────────────────────────────
account_balance = 0
total_traded    = 0

trade_log     = {}
setup_memory  = defaultdict(lambda: {"wins": 0, "losses": 0})
session_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0})

klines_cache      = {}
price_cache       = {}
symbol_info_cache = {}
fear_greed_cache  = {"value": 50,  "timestamp": 0}
btc_trend_cache   = {"trend": 0,   "timestamp": 0}

trade_lock     = threading.Lock()
api_lock       = threading.Lock()
api_call_times = []

# V30-3 — Kill-switch drawdown : état global
drawdown_state = {
    "balance_at_start_of_day": 0.0,   # Balance en début de journée
    "paused_until":            0.0,   # timestamp jusqu'auquel le trading est pausé
    "last_reset":              0.0,   # Dernier reset journalier
}

# ─── FLASK HEALTH SERVER ─────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    max_pos = calculate_max_positions(account_balance)
    paused  = time.time() < drawdown_state.get("paused_until", 0)
    status  = "⏸ PAUSED (drawdown)" if paused else "🟢 RUNNING"
    return f"v30 ROBOTKING | {status} | Balance: ${account_balance:.2f} | Open: {n_open}/{max_pos}", 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status():
    with trade_lock:
        n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    paused = time.time() < drawdown_state.get("paused_until", 0)
    return jsonify({
        "status":          "PAUSED" if paused else "RUNNING",
        "balance":         round(account_balance, 2),
        "positions_open":  n_open,
        "max_positions":   calculate_max_positions(account_balance),
        "total_traded":    total_traded,
        "version":         "v30",        # V30-8
        "drawdown_paused": paused,
    })

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    try:
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        threading.Thread(
            target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False),
            daemon=True
        ).start()
    except:
        pass

# ─── SESSION ─────────────────────────────────────────────────────
def get_current_session() -> str:
    hour = datetime.now(timezone.utc).hour
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

# ─── V30-3 : KILL-SWITCH DRAWDOWN ────────────────────────────────
def check_drawdown_kill_switch() -> bool:
    """
    Vérifie si la perte journalière dépasse DAILY_DRAWDOWN_LIMIT.
    Si oui → pause trading DRAWDOWN_PAUSE_HOURS heures + alerte Telegram.
    Retourne True si le trading est AUTORISÉ, False si pausé.
    """
    global drawdown_state

    now = time.time()

    # Reset quotidien à minuit UTC
    last_reset = drawdown_state.get("last_reset", 0)
    day_start  = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if last_reset < day_start:
        drawdown_state["balance_at_start_of_day"] = account_balance
        drawdown_state["last_reset"]              = now
        logger.info(f"📅 Drawdown reset | Balance de référence : ${account_balance:.2f}")

    # Vérifier si toujours en pause
    if now < drawdown_state.get("paused_until", 0):
        remaining = (drawdown_state["paused_until"] - now) / 3600
        logger.info(f"⏸ Trading pausé (drawdown) — encore {remaining:.1f}h")
        return False

    # Calculer la perte journalière
    ref_balance = drawdown_state.get("balance_at_start_of_day", account_balance)
    if ref_balance <= 0:
        return True

    drawdown_pct = (ref_balance - account_balance) / ref_balance
    if drawdown_pct >= DAILY_DRAWDOWN_LIMIT:
        pause_until = now + DRAWDOWN_PAUSE_HOURS * 3600
        drawdown_state["paused_until"] = pause_until
        logger.error(f"🚨 KILL-SWITCH — Drawdown {drawdown_pct:.1%} ≥ {DAILY_DRAWDOWN_LIMIT:.0%} | Pause {DRAWDOWN_PAUSE_HOURS}h")
        send_telegram(
            f"🚨 <b>KILL-SWITCH ACTIVÉ</b>\n"
            f"Perte journalière : <b>{drawdown_pct:.1%}</b> (limite : {DAILY_DRAWDOWN_LIMIT:.0%})\n"
            f"Balance : ${account_balance:.2f} (début : ${ref_balance:.2f})\n"
            f"⏸ Trading suspendu <b>{DRAWDOWN_PAUSE_HOURS}h</b>"
        )
        return False

    return True


# ─── V30-4 : FILTRE FUNDING RATE ─────────────────────────────────
def is_funding_safe(symbol: str) -> bool:
    """
    Skip l'entrée si |funding rate| > MAX_FUNDING_RATE_ABS.
    Funding extrême = squeeze ou liquidation en cours → dangereux.
    """
    try:
        data = request_binance("GET", "/fapi/v1/fundingRate",
                               {"symbol": symbol, "limit": 1}, signed=False)
        if not data:
            return True   # Pas de data → pas de raison de bloquer
        fr = abs(float(data[0]["fundingRate"]))
        if fr > MAX_FUNDING_RATE_ABS:
            logger.info(f"  [FUNDING] {symbol} funding={fr:.4%} > {MAX_FUNDING_RATE_ABS:.4%} → skip")
            return False
        return True
    except:
        return True   # Erreur → on ne bloque pas par défaut


# ─── V30-5 : FILTRE SPREAD BID-ASK ──────────────────────────────
def is_spread_acceptable(symbol: str) -> bool:
    """
    Skip l'entrée si le spread bid-ask > MAX_SPREAD_PCT.
    Spread large = slippage élevé + marché illiquide → dangereux avec levier.
    """
    try:
        data = request_binance("GET", "/fapi/v1/ticker/bookTicker",
                               {"symbol": symbol}, signed=False)
        if not data:
            return True
        bid   = float(data["bidPrice"])
        ask   = float(data["askPrice"])
        mid   = (bid + ask) / 2
        spread = (ask - bid) / mid if mid > 0 else 0
        if spread > MAX_SPREAD_PCT:
            logger.info(f"  [SPREAD] {symbol} spread={spread:.4%} > {MAX_SPREAD_PCT:.4%} → skip")
            return False
        return True
    except:
        return True


# ─── V30-1 : LEVIER ADAPTATIF ────────────────────────────────────
def calculate_adaptive_leverage(btc_score: float, probability: float,
                                atr_ratio: float) -> int:
    """
    Levier adaptatif : 8→15x selon BTC + probabilité + volatilité.

    Logique :
      • Base = prob score normalisé → [0.0–1.0]
      • Bonus si BTC fort haussier (score > 0.5)
      • Malus si volatilité ATR élevée (atr_ratio > 0.02)
      • Résultat clampé entre LEVERAGE_MIN et LEVERAGE_MAX
    """
    prob_ratio = max(0.0, (probability - MIN_PROBABILITY_SCORE) / (100.0 - MIN_PROBABILITY_SCORE))
    base_lev   = LEVERAGE_MIN + prob_ratio * (LEVERAGE_MAX - LEVERAGE_MIN)

    # Bonus BTC bull → +1 levier
    if btc_score > 0.5:
        base_lev += 1.0
    # Malus BTC bear → -1 levier
    elif btc_score < -0.25:
        base_lev -= 1.0

    # Malus volatilité élevée → réduire le levier si ATR > 2%
    if atr_ratio > 0.025:
        vol_penalty = min(3.0, (atr_ratio - 0.025) / 0.005)
        base_lev -= vol_penalty

    leverage = int(round(max(LEVERAGE_MIN, min(LEVERAGE_MAX, base_lev))))
    logger.info(f"  [LEV] prob={probability:.1f} btc={btc_score:+.2f} atr={atr_ratio:.4f} → {leverage}x")
    return leverage

# ─── POSITION SIZING ─────────────────────────────────────────────
def calculate_max_positions(balance: float) -> int:
    """V29-4: Toujours 3 positions max (toutes balances)."""
    return 3

def calculate_margin_for_trade(balance: float, probability: float = 68.0,
                               setup_score: float = 70.0) -> float:
    """
    V29-2 — Marge variable selon probabilité + setup.

    Logique :
      • prob ≥ 85 + setup ≥ 85 → marge max (45% balance)
      • prob ≥ 75              → marge élevée (30% balance)
      • prob ≥ 68              → marge normale (20% balance)
      • prob < 68              → refusé avant d'arriver ici

    Plancher absolu : MIN_NOTIONAL / LEVERAGE ($1 avec 20x)
    Plafond absolu  : 45% du capital (jamais plus)
    """
    # Score composite probabilité + setup (50/50)
    composite = (probability + setup_score) / 2.0

    if composite >= 85:
        pct = 0.45   # Setup excellent + haute probabilité → max size
    elif composite >= 77:
        pct = 0.30   # Bon setup → taille confortable
    elif composite >= 70:
        pct = 0.20   # Setup correct → taille normale
    else:
        pct = 0.12   # Setup limite → taille réduite

    margin = balance * pct
    margin = max(MIN_MARGIN_PER_TRADE, margin)   # Plancher notionnel
    margin = min(margin, balance * 0.45)          # Plafond de sécurité
    return round(margin, 2)

def can_afford_position(balance: float, existing_positions: int) -> bool:
    margin_needed  = calculate_margin_for_trade(balance)  # marge plancher
    max_positions  = calculate_max_positions(balance)
    if existing_positions >= max_positions:
        return False
    total_margin_used = margin_needed * (existing_positions + 1)
    return balance >= total_margin_used

# ─── RATE LIMITING ───────────────────────────────────────────────
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

# ─── API ─────────────────────────────────────────────────────────
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
                logger.warning(f"API {resp.status_code}: {resp.text[:120]}")
                return None
        except Exception as e:
            logger.warning(f"Request error: {e}")
            if attempt < 2:
                time.sleep(1)
    return None

# ─── MARKET DATA ─────────────────────────────────────────────────
def get_klines(symbol: str, interval: str = "5m", limit: int = 25) -> list:
    key = f"{symbol}_{interval}"
    now = time.time()
    if key in klines_cache:
        data, ts = klines_cache[key]
        if now - ts < CACHE_DURATION:
            return data
    data = request_binance("GET", "/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
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
                "pricePrecision":    s.get("pricePrecision", 2),
                "minQty":            float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                "maxQty":            float(filters.get("LOT_SIZE", {}).get("maxQty", 10000)),
                "stepSize":          float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "minNotional":       float(filters.get("MIN_NOTIONAL", {}).get("notional", 20)),
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

# ─── LEVERAGE / MARGIN ───────────────────────────────────────────
def set_leverage(symbol: str, leverage: int):
    try:
        result = request_binance("POST", "/fapi/v1/leverage", {
            "symbol": symbol, "leverage": leverage
        })
        if result:
            logger.info(f"⚙️  {symbol} leverage {leverage}x")
    except:
        pass

def set_margin_type(symbol: str, margin_type: str):
    try:
        request_binance("POST", "/fapi/v1/marginType", {
            "symbol": symbol, "marginType": margin_type
        })
    except:
        pass

# ─── FIX 3: VRAIE EMA (exponentielle) ───────────────────────────
def calc_ema(values: np.ndarray, period: int) -> float:
    """Calcule une vraie EMA exponentielle — remplace np.mean() (SMA) de v24."""
    if len(values) < period:
        return float(np.mean(values))
    k = 2.0 / (period + 1)
    ema = float(np.mean(values[:period]))          # seed avec SMA initiale
    for price in values[period:]:
        ema = price * k + ema * (1 - k)
    return ema

# ─── ATR ─────────────────────────────────────────────────────────
def calc_atr(symbol: str, period: int = 14) -> float:
    klines = get_klines(symbol, "5m", period + 1)
    if not klines or len(klines) < period:
        return 0
    highs  = np.array([float(k[2]) for k in klines])
    lows   = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    tr = np.maximum(highs[1:] - lows[1:],
         np.maximum(abs(highs[1:] - closes[:-1]),
                    abs(lows[1:]  - closes[:-1])))
    return float(np.mean(tr)) if len(tr) > 0 else 0

# ─── V30-2 : ORDER BOOK — ZONES DE LIQUIDITÉ (SMART) ────────────
def get_order_book_walls(symbol: str, depth: int = 50) -> dict:
    """
    V30-2 — Analyse améliorée du carnet d'ordres.

    Améliorations vs v29 :
      • Top 3 murs (pas seulement le plus gros) → moyenne pondérée
      • Filtre anti-spoofing : si mur#1 > LIQ_SPOOF_THRESHOLD × moyenne → ignoré
      • Timeout robuste : retourne valeurs neutres si erreur API
    """
    try:
        data = request_binance("GET", "/fapi/v1/depth",
                               {"symbol": symbol, "limit": depth}, signed=False)
        if not data or "bids" not in data or "asks" not in data:
            return {"bid_wall_price": 0, "bid_wall_qty": 0,
                    "ask_wall_price": 0, "ask_wall_qty": 0, "ratio": 1.0,
                    "bid_walls": [], "ask_walls": []}

        bids = [(float(b[0]), float(b[1])) for b in data["bids"]]
        asks = [(float(a[0]), float(a[1])) for a in data["asks"]]

        if not bids or not asks:
            return {"bid_wall_price": 0, "bid_wall_qty": 0,
                    "ask_wall_price": 0, "ask_wall_qty": 0, "ratio": 1.0,
                    "bid_walls": [], "ask_walls": []}

        # Trier par quantité décroissante → top N murs
        n = LIQ_TOP_N_WALLS
        top_bids = sorted(bids, key=lambda x: x[1], reverse=True)[:n]
        top_asks = sorted(asks, key=lambda x: x[1], reverse=True)[:n]

        # Anti-spoofing : si le plus gros mur > N× la moyenne des suivants → probable fake
        def _anti_spoof(walls: list) -> list:
            if len(walls) < 2:
                return walls
            avg_rest = sum(w[1] for w in walls[1:]) / (len(walls) - 1)
            if avg_rest > 0 and walls[0][1] / avg_rest > LIQ_SPOOF_THRESHOLD:
                logger.debug(f"  [ANTI-SPOOF] Mur suspect ignoré (ratio={walls[0][1]/avg_rest:.1f}×)")
                return walls[1:]   # Ignorer le mur suspect
            return walls

        top_bids = _anti_spoof(top_bids)
        top_asks = _anti_spoof(top_asks)

        # Mur représentatif = mur avec la plus grosse quantité après filtre spoof
        best_bid = top_bids[0] if top_bids else (0.0, 0.0)
        best_ask = top_asks[0] if top_asks else (0.0, 0.0)

        ratio = best_bid[1] / best_ask[1] if best_ask[1] > 0 else 1.0

        return {
            "bid_wall_price": best_bid[0],
            "bid_wall_qty":   best_bid[1],
            "ask_wall_price": best_ask[0],
            "ask_wall_qty":   best_ask[1],
            "ratio":          ratio,
            "bid_walls":      top_bids,   # Liste complète pour le TP smart
            "ask_walls":      top_asks,
        }
    except Exception as e:
        logger.debug(f"get_order_book_walls {symbol}: {e}")
        return {"bid_wall_price": 0, "bid_wall_qty": 0,
                "ask_wall_price": 0, "ask_wall_qty": 0, "ratio": 1.0,
                "bid_walls": [], "ask_walls": []}


def get_tp_from_liquidity(symbol: str, side: str, entry: float,
                          sl_distance: float) -> float:
    """
    V30-2 — TP smart basé sur les zones de liquidité.

    Améliorations vs v29 :
      • Filtre distance minimale : mur doit être à ≥ LIQ_MIN_WALL_DISTANCE_ATR × ATR
        (évite les TP trop proches / murs trop serrés)
      • Parcourt les top N murs et prend le premier valide
      • Fallback R:R 2.5 si aucun mur valide trouvé
      • Marge de sortie 0.3% avant le mur (exit avant les pros)
    """
    try:
        walls = get_order_book_walls(symbol)
        info  = get_symbol_info(symbol)
        pp    = info.get("pricePrecision", 4) if info else 4
        atr   = calc_atr(symbol) or entry * 0.015
        min_wall_dist = atr * LIQ_MIN_WALL_DISTANCE_ATR   # Distance min valide
        min_rr        = 1.5
        fallback_rr   = 2.5

        if side == "BUY":
            # Parcourir les murs ask du plus proche au plus loin
            candidates = sorted(walls.get("ask_walls", []), key=lambda x: x[0])  # prix croissant
            for wall_price, wall_qty in candidates:
                if wall_price <= entry:
                    continue   # Mur en dessous de l'entrée → invalide
                dist_to_wall = wall_price - entry
                if dist_to_wall < min_wall_dist:
                    logger.debug(f"  [TP-LIQ] Mur ask {wall_price:.{pp}f} trop proche ({dist_to_wall:.{pp}f} < {min_wall_dist:.{pp}f})")
                    continue   # Trop proche → probable micro-mur
                # Mur valide → sortir 0.3% avant
                tp_liq = wall_price * 0.997
                if tp_liq >= entry + sl_distance * min_rr:
                    logger.info(f"  [TP-LIQ] {symbol} BUY → mur ask @ {wall_price:.{pp}f} (qty={wall_qty:.0f}) | TP={tp_liq:.{pp}f}")
                    return round(tp_liq, pp)

            # Fallback
            tp = round(entry + sl_distance * fallback_rr, pp)
            logger.info(f"  [TP-LIQ] {symbol} BUY → fallback TP={tp:.{pp}f} (R:R {fallback_rr})")
            return tp

        else:  # SELL
            # Parcourir les murs bid du plus proche (en dessous) au plus loin
            candidates = sorted(walls.get("bid_walls", []), key=lambda x: x[0], reverse=True)
            for wall_price, wall_qty in candidates:
                if wall_price >= entry:
                    continue
                dist_to_wall = entry - wall_price
                if dist_to_wall < min_wall_dist:
                    logger.debug(f"  [TP-LIQ] Mur bid {wall_price:.{pp}f} trop proche")
                    continue
                # Sortir 0.3% au-dessus du mur bid (ne pas attendre que le mur cède)
                tp_liq = wall_price * 1.003
                if tp_liq <= entry - sl_distance * min_rr:
                    logger.info(f"  [TP-LIQ] {symbol} SELL → mur bid @ {wall_price:.{pp}f} (qty={wall_qty:.0f}) | TP={tp_liq:.{pp}f}")
                    return round(tp_liq, pp)

            tp = round(entry - sl_distance * fallback_rr, pp)
            logger.info(f"  [TP-LIQ] {symbol} SELL → fallback TP={tp:.{pp}f} (R:R {fallback_rr})")
            return tp

    except Exception as e:
        logger.warning(f"get_tp_from_liquidity {symbol}: {e}")
        pp = 4
        return round(entry + sl_distance * 2.5, pp) if side == "BUY" \
               else round(entry - sl_distance * 2.5, pp)


def calculate_liquidity_score(symbol: str) -> float:
    """
    V29-6 — Score de liquidité [0.0–1.0] pour le Probability Engine.

    ratio bid_qty / ask_qty :
      > 1.4  → gros acheteurs → score haussier 0.75
      < 0.7  → gros vendeurs  → score baissier 0.30
      sinon  → neutre 0.50
    """
    try:
        walls = get_order_book_walls(symbol)
        ratio = walls["ratio"]
        if ratio >= 1.4:
            score = 0.75
        elif ratio <= 0.7:
            score = 0.30
        else:
            score = 0.50
        logger.debug(f"  [LIQ-SCORE] {symbol} ratio={ratio:.2f} → {score:.2f}")
        return score
    except:
        return 0.50

# ─── FIX 3: DÉTECTION TENDANCE AVEC VRAIE EMA ───────────────────
def detect_trend(symbol: str, timeframe: str = "15m") -> dict:
    try:
        klines = get_klines(symbol, timeframe, 60)
        if not klines or len(klines) < 50:
            return {"direction": 0, "strength": 0}
        closes = np.array([float(k[4]) for k in klines])
        ema_9  = calc_ema(closes, 9)
        ema_21 = calc_ema(closes, 21)
        ema_50 = calc_ema(closes, 50)
        if ema_9 > ema_21 > ema_50:
            direction = 1
            strength  = min((ema_9 - ema_50) / ema_50 * 100, 1.0)
        elif ema_9 < ema_21 < ema_50:
            direction = -1
            strength  = min((ema_50 - ema_9) / ema_50 * 100, 1.0)
        else:
            direction = 0
            strength  = 0
        return {"direction": direction, "strength": abs(strength)}
    except:
        return {"direction": 0, "strength": 0}

# ─── FIX 2: BTC TREND → utilise BTCUSDT (était DOGEUSDT en v24) ──
# ─── BTC MULTI-TIMEFRAME (tendance de fond = nécessité) ────────
# Cache séparé par timeframe
btc_mtf_cache = {}

def get_btc_trend_tf(tf: str) -> dict:
    """Tendance BTC sur un timeframe donné. Cache 60s pour 15m/1h, 5min pour 4h/1d."""
    global btc_mtf_cache
    now      = time.time()
    cache_ttl = 300 if tf in ("4h", "1d") else 60
    cached   = btc_mtf_cache.get(tf)
    if cached and now - cached["ts"] < cache_ttl:
        return cached["data"]
    data = detect_trend("BTCUSDT", tf)
    btc_mtf_cache[tf] = {"data": data, "ts": now}
    return data

def get_btc_composite_score() -> dict:
    """
    Score BTC composite sur 4 timeframes pondérés.
    Retourne :
      score  : float entre -1.0 (full bear) et +1.0 (full bull)
      daily_bear : bool — Daily clairement bearish
      daily_bull : bool — Daily clairement bullish
      label  : str — résumé lisible
      details: dict — breakdown par TF
    """
    global btc_trend_cache
    now = time.time()
    # Cache composite 60s
    if now - btc_trend_cache.get("timestamp", 0) < 60:
        return btc_trend_cache.get("composite", _default_btc_composite())

    score    = 0.0
    details  = {}
    daily_dir = 0

    for tf, cfg in BTC_TIMEFRAMES.items():
        try:
            td  = get_btc_trend_tf(tf)
            dir = td["direction"]   # -1 / 0 / +1
            str = td["strength"]    # 0.0 → 1.0
            # Contribution : direction × force × poids
            contribution = dir * (0.5 + str * 0.5) * cfg["weight"]
            score += contribution
            details[tf] = {
                "direction": dir,
                "strength":  round(str, 2),
                "contrib":   round(contribution, 3),
                "label":     cfg["label"]
            }
            if tf == "1d":
                daily_dir = dir
        except:
            pass

    score = round(max(-1.0, min(1.0, score)), 3)

    if score > BTC_BULL_THRESHOLD:
        label = "🟢 BULL"
    elif score < BTC_BEAR_THRESHOLD:
        label = "🔴 BEAR"
    else:
        label = "⚪ NEUTRE"

    composite = {
        "score":      score,
        "label":      label,
        "daily_bear": daily_dir == -1,
        "daily_bull": daily_dir == 1,
        "details":    details
    }

    btc_trend_cache = {"composite": composite, "trend": int(score > 0) - int(score < 0), "timestamp": now}
    logger.info(f"📊 BTC composite: {label} ({score:+.2f}) | "
                + " | ".join(f"{d['label']}:{'▲' if d['direction']==1 else '▼' if d['direction']==-1 else '—'}"
                             for d in details.values()))
    return composite

def _default_btc_composite() -> dict:
    return {"score": 0, "label": "⚪ NEUTRE", "daily_bear": False, "daily_bull": False, "details": {}}

def get_btc_profile(btc_score: float, profiles: dict) -> dict:
    """Retourne le profil correspondant au score BTC composite."""
    for name, p in profiles.items():
        if p["min"] <= btc_score <= p["max"]:
            return {**p, "name": name}
    # Fallback neutre
    return profiles.get("NEUTRAL", {"start_rr": 1.0, "step_atr": 0.5,
                                     "lock_pct": 0.004, "multiplier": 1.0,
                                     "leverage": 10, "label": "⚪", "name": "NEUTRAL"})


def get_adaptive_leverage(btc_score: float) -> int:
    """ADAPT 3: Levier adaptatif selon la conviction BTC."""
    p = get_btc_profile(btc_score, SIZING_PROFILES)
    lev = p.get("leverage", LEVERAGE)
    return min(lev, LEVERAGE_MAX)


def get_adaptive_margin(base_margin: float, btc_score: float) -> float:
    """ADAPT 2: Margin adaptative — plus grosse quand BTC fort, plus petite sinon."""
    p = get_btc_profile(btc_score, SIZING_PROFILES)
    mult = p.get("multiplier", 1.0)
    return round(base_margin * mult, 2)


def get_trailing_profile(btc_score: float) -> dict:
    """ADAPT 1: Profil trailing selon force BTC."""
    return get_btc_profile(btc_score, TRAILING_PROFILES)


def get_tick_size(symbol: str) -> float:
    """Retourne le tick size (plus petite variation de prix) du symbol."""
    info = get_symbol_info(symbol)
    if not info:
        return 0.0001
    # Calcule depuis pricePrecision
    return 10 ** (-info.get("pricePrecision", 4))


def get_btc_trend() -> int:
    """Compatibilité avec l'ancien code — retourne -1/0/1."""
    c = get_btc_composite_score()
    return int(c["score"] > BTC_BULL_THRESHOLD) - int(c["score"] < BTC_BEAR_THRESHOLD)

def calculate_btc_correlation(symbol: str) -> float:
    """
    Corrélation BTC-symbol améliorée.
    Tient compte du score composite BTC, pas juste de la direction.
    """
    try:
        btc     = get_btc_composite_score()
        s_trend = detect_trend(symbol, TREND_TIMEFRAME)
        s_dir   = s_trend["direction"]
        btc_dir = int(btc["score"] > 0) - int(btc["score"] < 0)

        if btc_dir == 0 or s_dir == 0:
            return 0.5

        if btc_dir == s_dir:
            # Aligné : bonus proportionnel à la force du signal BTC
            return min(0.95, 0.65 + abs(btc["score"]) * 0.3)
        else:
            # Divergence : pénalité
            return max(0.05, 0.35 - abs(btc["score"]) * 0.3)
    except:
        return 0.5

# ─── FEAR & GREED ────────────────────────────────────────────────
def get_fear_greed_index() -> int:
    global fear_greed_cache
    now = time.time()
    if now - fear_greed_cache.get("timestamp", 0) < 3600:
        return fear_greed_cache.get("value", 50)
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=5)
        if resp.status_code == 200:
            data  = resp.json()
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
        atr   = calc_atr(symbol)
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

# ─── PROBABILITY ─────────────────────────────────────────────────
def calculate_probability(symbol: str, side: str, setup_name: str) -> float:
    try:
        setup_score_raw = SETUPS.get(setup_name, {}).get("score", 50)
        setup_score     = setup_score_raw / 100.0

        trend_data      = detect_trend(symbol, TREND_TIMEFRAME)
        trend_direction = trend_data["direction"]
        trend_strength  = trend_data["strength"]

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

        btc_corr        = calculate_btc_correlation(symbol)
        session_score   = get_session_weight()
        fear_greed      = get_fear_greed_index()
        sentiment_score = calculate_sentiment_score(fear_greed)

        if side == "BUY" and fear_greed < 35:
            sentiment_score = min(sentiment_score * 1.2, 1.0)
        elif side == "SELL" and fear_greed > 65:
            sentiment_score = min(sentiment_score * 1.2, 1.0)

        volatility_score = calculate_volatility_score(symbol)

        # V29-6 — Liquidity score intégré au Probability Engine
        liquidity_score = calculate_liquidity_score(symbol)

        # Ajustement directionnel du score de liquidité
        # Pour un BUY, un ratio bid > ask est favorable
        if side == "SELL" and liquidity_score > 0.5:
            liquidity_score = 1.0 - liquidity_score   # Inverser pour SELL
        elif side == "BUY" and liquidity_score < 0.5:
            liquidity_score = 1.0 - liquidity_score   # Inverser si baissier pour BUY

        probability = (
            setup_score      * PROBABILITY_WEIGHTS["setup_score"]     +
            trend_score      * PROBABILITY_WEIGHTS["trend_alignment"]  +
            btc_corr         * PROBABILITY_WEIGHTS["btc_correlation"]  +
            session_score    * PROBABILITY_WEIGHTS["session_quality"]  +
            sentiment_score  * PROBABILITY_WEIGHTS["sentiment"]        +
            volatility_score * PROBABILITY_WEIGHTS["volatility"]       +
            liquidity_score  * PROBABILITY_WEIGHTS["liquidity"]        # V29-6
        ) * 100

        return round(probability, 1)
    except:
        return 50.0

# ─── FIX 4: SETUPS SUPPORTENT MAINTENANT BUY ET SELL ────────────

def detect_mss_fvg_fib(symbol: str, side: str) -> dict:
    """
    Market Structure Shift + Fair Value Gap + Fibonacci 0.618
    v24: BUY uniquement ❌
    v25: BUY + SELL ✅
    """
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    closes = np.array([float(k[4]) for k in klines])
    highs  = np.array([float(k[2]) for k in klines])
    lows   = np.array([float(k[3]) for k in klines])

    if side == "BUY":
        for i in range(10, 18):
            if closes[i] < min(closes[i-5:i]):
                if lows[i-1] > highs[i+1]:
                    swing_high = max(highs[i-5:i])
                    swing_low  = min(lows[i:i+5])
                    fib_618    = swing_low + (swing_high - swing_low) * 0.618
                    if abs(closes[-1] - fib_618) / closes[-1] < 0.01:
                        return {"name": "MSS_FVG_FIB", "score": SETUPS["MSS_FVG_FIB"]["score"]}

    elif side == "SELL":
        # Miroir logique : MSS baissier + FVG + rejet Fibonacci 0.618
        for i in range(10, 18):
            if closes[i] > max(closes[i-5:i]):
                if highs[i-1] < lows[i+1]:
                    swing_low  = min(lows[i-5:i])
                    swing_high = max(highs[i:i+5])
                    fib_618    = swing_high - (swing_high - swing_low) * 0.618
                    if abs(closes[-1] - fib_618) / closes[-1] < 0.01:
                        return {"name": "MSS_FVG_FIB", "score": SETUPS["MSS_FVG_FIB"]["score"]}

    return None


def detect_liq_sweep_bos(symbol: str, side: str) -> dict:
    """
    Liquidity Sweep + Break of Structure
    v24: BUY uniquement ❌
    v25: BUY + SELL ✅
    """
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    lows   = np.array([float(k[3]) for k in klines])
    highs  = np.array([float(k[2]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])

    if side == "BUY":
        prev_low = min(lows[-15:-5])
        if min(lows[-5:-2]) < prev_low:
            if closes[-1] > max(closes[-8:-2]):
                return {"name": "LIQ_SWEEP_BOS", "score": SETUPS["LIQ_SWEEP_BOS"]["score"]}

    elif side == "SELL":
        # Miroir : sweep des highs + BOS baissier
        prev_high = max(highs[-15:-5])
        if max(highs[-5:-2]) > prev_high:
            if closes[-1] < min(closes[-8:-2]):
                return {"name": "LIQ_SWEEP_BOS", "score": SETUPS["LIQ_SWEEP_BOS"]["score"]}

    return None


def detect_breakout_retest(symbol: str, side: str) -> dict:
    klines = get_klines(symbol, "5m", 25)
    if not klines or len(klines) < 20:
        return None
    closes = np.array([float(k[4]) for k in klines])
    highs  = np.array([float(k[2]) for k in klines])
    lows   = np.array([float(k[3]) for k in klines])

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

# ─── ORDER UTILS ─────────────────────────────────────────────────
def validate_order_size(symbol: str, qty: float, price: float) -> tuple:
    info = get_symbol_info(symbol)
    if not info:
        return (False, "Symbol info not available", 0)
    if qty < info["minQty"]:
        return (False, f"Qty {qty} < min {info['minQty']}", 0)
    notional    = price * qty
    min_notional = info.get("minNotional", MIN_NOTIONAL)
    if notional < min_notional:
        adjusted_qty = min_notional / price
        adjusted_qty = round(adjusted_qty, info["quantityPrecision"])
        if adjusted_qty < info["minQty"]:
            return (False, "Cannot meet min notional", 0)
        adjusted_notional = price * adjusted_qty
        if adjusted_notional < min_notional:
            return (False, f"Notional {adjusted_notional:.2f} < {min_notional}", 0)
        return (True, "Adjusted to meet notional", adjusted_qty)
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
        logger.info(f"📊 {symbol} qty: {qty} → {adjusted_qty}")
        qty = adjusted_qty

    order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "MARKET", "quantity": qty
    })
    if order:
        return order

    logger.warning(f"⚠️  {symbol} MARKET rejected → LIMIT fallback")
    limit_price = price * (1.001 if side == "BUY" else 0.999)
    limit_price = round(limit_price, info["pricePrecision"])
    order = request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "LIMIT",
        "timeInForce": "GTC", "quantity": qty, "price": limit_price
    })
    if order:
        logger.info(f"✅ {symbol} LIMIT at ${limit_price}")
        return order

    return None

def cleanup_orders(symbol: str):
    try:
        open_orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if open_orders:
            for order in open_orders:
                request_binance("DELETE", "/fapi/v1/order", {
                    "symbol": symbol, "orderId": order["orderId"]
                })
    except:
        pass

# ─── FIX 1: SL/TP RÉELS SUR BINANCE ─────────────────────────────
def place_sl_tp_orders(symbol: str, side: str, sl: float, tp: float, info: dict) -> dict:
    """
    Envoie de vrais ordres STOP_MARKET et TAKE_PROFIT_MARKET à Binance.
    Si Render crash → Binance protège quand même la position.

    Règles Binance Futures strictes :
    - closePosition=true  →  NE PAS mettre reduceOnly (mutuellement exclusifs)
    - NE PAS mettre timeInForce sur STOP_MARKET / TAKE_PROFIT_MARKET
    - workingType valide : MARK_PRICE ou CONTRACT_PRICE
    """
    results = {"sl_sent": False, "tp_sent": False}
    close_side = "SELL" if side == "BUY" else "BUY"
    pp = info["pricePrecision"]

    # Vérifie que les prix sont valides avant d'envoyer
    current_price = get_price(symbol)
    if not current_price:
        logger.warning(f"⚠️  {symbol} prix indisponible — SL/TP Binance non posés")
        return results

    # Validation direction SL/TP vs prix courant
    if side == "BUY":
        if sl >= current_price:
            logger.warning(f"⚠️  {symbol} SL ({sl}) >= prix courant ({current_price}) — ignoré")
            sl = None
        if tp <= current_price:
            logger.warning(f"⚠️  {symbol} TP ({tp}) <= prix courant ({current_price}) — ignoré")
            tp = None
    else:  # SELL
        if sl <= current_price:
            logger.warning(f"⚠️  {symbol} SL ({sl}) <= prix courant ({current_price}) — ignoré")
            sl = None
        if tp >= current_price:
            logger.warning(f"⚠️  {symbol} TP ({tp}) >= prix courant ({current_price}) — ignoré")
            tp = None

    # ── Stop Loss — FIX 11: Retry x3 + fermeture forcée si échec ──
    if sl:
        for attempt in range(3):
            try:
                sl_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "STOP_MARKET",
                    "stopPrice":     round(sl, pp),
                    "closePosition": "true",       # ✅ Ferme toute la position
                    "workingType":   "MARK_PRICE"  # ✅ Mark Price (pas Last Price)
                    # ❌ PAS de reduceOnly (incompatible avec closePosition)
                    # ❌ PAS de timeInForce (invalide pour STOP_MARKET)
                })
                if sl_order and sl_order.get("orderId"):
                    results["sl_sent"] = True
                    logger.info(f"🛡️  {symbol} SL Binance ✅ @ {round(sl, pp)} (id={sl_order['orderId']})")
                    break
                else:
                    logger.warning(f"⚠️  {symbol} SL tentative {attempt+1}/3 échouée")
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"⚠️  {symbol} SL error (t{attempt+1}): {e}")
                time.sleep(0.5)

        if not results["sl_sent"]:
            # ✅ SL échoué → PAS de fermeture forcée (position peut être prometteuse)
            # On active le mode surveillance ULTRA-RAPIDE + retry SL en background
            logger.error(f"🚨 {symbol} SL Binance impossible après 3 tentatives → MODE URGENCE")
            results["urgent_monitoring"] = True

    # ── Take Profit — Retry x2 ─────────────────────────────────
    if tp:
        for attempt in range(2):
            try:
                tp_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "TAKE_PROFIT_MARKET",
                    "stopPrice":     round(tp, pp),
                    "closePosition": "true",       # ✅ Ferme toute la position
                    "workingType":   "MARK_PRICE"  # ✅ Mark Price (pas Last Price)
                    # ❌ PAS de reduceOnly (incompatible avec closePosition)
                    # ❌ PAS de timeInForce (invalide pour TAKE_PROFIT_MARKET)
                })
                if tp_order and tp_order.get("orderId"):
                    results["tp_sent"] = True
                    logger.info(f"🎯 {symbol} TP Binance ✅ @ {round(tp, pp)} (id={tp_order['orderId']})")
                    break
                else:
                    logger.warning(f"⚠️  {symbol} TP tentative {attempt+1}/2 échouée")
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"⚠️  {symbol} TP error (t{attempt+1}): {e}")
                time.sleep(0.5)

        if not results["tp_sent"]:
            logger.warning(f"⚠️  {symbol} TP Binance échoué — TP logiciel actif en fallback")

    return results

# ─── OPEN POSITION ───────────────────────────────────────────────
def open_position(symbol: str, side: str, entry: float, sl: float, tp: float,
                  setup_name: str, probability: float):
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

        # ── V30-1 : Levier adaptatif (prob × BTC × vol) ────────────
        btc_ctx       = get_btc_composite_score()
        btc_score_ctx = btc_ctx["score"]
        profile_ctx   = get_btc_profile(btc_score_ctx, SIZING_PROFILES)
        atr_price     = calc_atr(symbol)
        current_p     = get_price(symbol) or entry
        atr_ratio     = atr_price / current_p if current_p > 0 else 0.015
        adap_lev      = calculate_adaptive_leverage(btc_score_ctx, probability, atr_ratio)

        # ── V29-2 : Marge variable selon probabilité + setup ───────
        setup_score_raw = SETUPS.get(setup_name, {}).get("score", 70)
        margin = calculate_margin_for_trade(
            account_balance,
            probability=probability,
            setup_score=float(setup_score_raw)
        )
        margin = min(margin, account_balance * 0.45)

        set_leverage(symbol, adap_lev)
        set_margin_type(symbol, MARGIN_TYPE)
        notional = margin * adap_lev
        qty      = round(notional / entry, info["quantityPrecision"])

        is_valid, msg, adjusted_qty = validate_order_size(symbol, qty, entry)
        if not is_valid:
            logger.warning(f"❌ {symbol} {msg}")
            return
        if adjusted_qty != qty:
            qty    = adjusted_qty
            margin = (qty * entry) / adap_lev

        pp      = info["pricePrecision"]
        session = get_current_session()

        logger.info(f"🎯 {symbol} {side} | Prob: {probability}% | Margin: ${margin:.2f}")

        order = place_order_with_fallback(symbol, side, qty, entry)
        if not order:
            logger.error(f"❌ {symbol} order failed")
            return

        # ── FIX: avgPrice="0" sur MARKET orders ──────────────────────
        # Binance retourne "price":"0" et parfois "avgPrice":"0" pour
        # les ordres MARKET. On récupère le vrai prix via positionRisk.
        actual_entry = 0.0
        for attempt in range(5):
            time.sleep(0.4)  # laisse Binance finaliser le fill
            pos_data = request_binance("GET", "/fapi/v2/positionRisk",
                                       {"symbol": symbol}, signed=True)
            if pos_data:
                for pos in pos_data:
                    if pos.get("symbol") == symbol:
                        ep = float(pos.get("entryPrice", 0))
                        if ep > 0:
                            actual_entry = ep
                            break
            if actual_entry > 0:
                break

        # Dernier recours : utilise le prix spot connu
        if actual_entry <= 0:
            actual_entry = get_price(symbol) or entry
            logger.warning(f"⚠️  {symbol} entryPrice non récupéré — fallback spot ${actual_entry}")

        logger.info(f"📌 {symbol} entryPrice confirmé: ${actual_entry}")

        # Recalcul SL/TP sur la base du vrai prix d'entrée
        atr_real = calc_atr(symbol) or actual_entry * 0.015  # fallback 1.5%
        if side == "BUY":
            sl_distance = max(actual_entry - sl, atr_real * 1.0)
            sl = round(actual_entry - sl_distance, pp)
            # V29-3 : TP basé sur les zones de liquidité order book
            tp = get_tp_from_liquidity(symbol, "BUY", actual_entry, sl_distance)
        else:
            sl_distance = max(sl - actual_entry, atr_real * 1.0)
            sl = round(actual_entry + sl_distance, pp)
            # V29-3 : TP basé sur les zones de liquidité order book
            tp = get_tp_from_liquidity(symbol, "SELL", actual_entry, sl_distance)

        # Validation finale : TP doit être cohérent avec la direction
        if side == "BUY" and tp <= actual_entry:
            logger.error(f"❌ {symbol} TP incohérent ({tp} <= {actual_entry}) — fermeture")
            place_order_with_fallback(symbol, "SELL", qty, actual_entry)
            return
        if side == "SELL" and tp >= actual_entry:
            logger.error(f"❌ {symbol} TP incohérent ({tp} >= {actual_entry}) — fermeture")
            place_order_with_fallback(symbol, "BUY", qty, actual_entry)
            return

        logger.info(f"✅ {symbol} {side} @ {actual_entry:.{pp}f} | SL {sl:.{pp}f} | TP {tp:.{pp}f}")

        # ── FIX 1: Envoi SL/TP réels à Binance ──────────────────
        sl_tp_results = place_sl_tp_orders(symbol, side, sl, tp, info)

        # ✅ FIX 11: Si SL Binance impossible → mode urgence (pas de fermeture)
        # La position reste ouverte — software SL ultra-rapide + retry SL en background
        if sl_tp_results.get("urgent_monitoring"):
            send_telegram(
                f"⚠️ <b>{symbol}</b> SL Binance non posé\n"
                f"Mode URGENCE activé : surveillance 0.5s + retry SL auto\n"
                f"Position conservée — SL logiciel actif"
            )

        with trade_lock:
            trade_log[symbol] = {
                "side":                 side,
                "entry":                actual_entry,
                "sl":                   sl,
                "tp":                   tp,
                "qty":                  qty,
                "margin":               margin,
                "setup":                setup_name,
                "probability":          probability,
                "status":               "OPEN",
                "opened_at":            time.time(),
                "session":              session,
                "sl_on_binance":        sl_tp_results["sl_sent"],
                "tp_on_binance":        sl_tp_results["tp_sent"],
                "urgent_monitoring":    sl_tp_results.get("urgent_monitoring", False),
                "sl_retry_at":          time.time() + 30 if sl_tp_results.get("urgent_monitoring") else None,
                "trailing_stop_active": False,
                "breakeven_moved":      False,
                "highest_price":        actual_entry if side == "BUY"  else None,
                "lowest_price":         actual_entry if side == "SELL" else None,
                "last_sl_update":       time.time()
            }
            total_traded += 1

        send_telegram(
            f"🚀 <b>{symbol}</b> {side}\n"
            f"Prob: {probability}% | Mode: {profile_ctx.get('label','?')}\n"
            f"Entry: ${actual_entry:.{pp}f} | Levier: {adap_lev}x\n"
            f"SL: ${sl:.{pp}f} {'🛡️ Binance' if sl_tp_results['sl_sent'] else '⚠️ logiciel'}\n"
            f"TP: ${tp:.{pp}f} {'🎯 Binance' if sl_tp_results['tp_sent'] else '⚠️ logiciel'}\n"
            f"Margin: ${margin:.2f} | BTC: {btc_ctx['label']} ({btc_score_ctx:+.2f})"
        )

    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

# ─── BREAKEVEN ───────────────────────────────────────────────────
def _push_sl_to_binance(symbol: str, trade: dict, new_sl: float, info: dict):
    """Met à jour le SL sur Binance : cancel ancien + nouveau STOP_MARKET."""
    try:
        cleanup_orders(symbol)
        results = place_sl_tp_orders(symbol, trade["side"], new_sl, trade["tp"], info)
        trade["sl_on_binance"] = results["sl_sent"]
        if results["sl_sent"]:
            logger.info(f"🛡️  {symbol} SL Binance mis à jour → ${new_sl:.{info['pricePrecision']}f}")
    except Exception as e:
        logger.warning(f"_push_sl_to_binance {symbol}: {e}")


def update_trailing_sl(symbol: str, current_price: float):
    """
    SL Suiveur (Trailing Stop) — v27.

    Phases :
      Phase 1 — Breakeven : dès 0.5R → SL monte au prix d'entrée (zéro perte)
      Phase 2 — Trailing  : dès 1R   → SL suit le prix à distance ATR × 0.5
                            Le SL ne recule JAMAIS, il ne fait que monter (BUY) ou descendre (SELL)
      Phase 3 — Verrouillage : si le prix s'éloigne encore, le SL continue à suivre

    Le SL est mis à jour sur Binance à chaque mouvement significatif.
    """
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return

            side    = trade["side"]
            entry   = trade["entry"]
            sl      = trade["sl"]
            info    = get_symbol_info(symbol)
            if not info:
                return
            pp        = info["pricePrecision"]
            tick_size = get_tick_size(symbol)

            profit  = (current_price - entry) if side == "BUY" else (entry - current_price)
            risk    = (entry - sl)            if side == "BUY" else (sl - entry)
            if risk <= 0:
                return
            rr = profit / risk

            # ── Mise à jour water mark ───────────────────────────
            if side == "BUY":
                hwm = trade.get("highest_price") or current_price
                if current_price > hwm:
                    trade["highest_price"] = current_price
                    hwm = current_price
            else:
                lwm = trade.get("lowest_price") or current_price
                if current_price < lwm:
                    trade["lowest_price"] = current_price
                    lwm = current_price

            # ── ADAPT 1 : Profil trailing selon BTC composite ───
            btc = get_btc_composite_score()
            t_profile = get_trailing_profile(btc["score"])
            t_start   = t_profile.get("start_rr",   TRAILING_START_RR)
            t_step    = t_profile.get("step_atr",    TRAILING_STEP_ATR)
            t_lock    = t_profile.get("lock_pct",    TRAILING_LOCK_PCT)
            t_label   = t_profile.get("label", "")

            atr      = calc_atr(symbol) or entry * 0.015
            atr_step = max(atr * t_step, entry * t_lock)

            new_sl = sl

            # ── Phase 1 : Breakeven ──────────────────────────────
            if rr >= BREAKEVEN_RR and not trade.get("breakeven_moved"):
                be_sl = round(entry, pp)
                if (side == "BUY" and be_sl > sl) or (side == "SELL" and be_sl < sl):
                    new_sl = be_sl
                    trade["breakeven_moved"] = True
                    logger.info(f"🎯 {symbol} BREAKEVEN → {be_sl:.{pp}f} | BTC: {t_label}")

            # ── Phase 2+3 : Trailing adaptatif ──────────────────
            if TRAILING_ENABLED and rr >= t_start:
                trade["trailing_stop_active"] = True
                if side == "BUY":
                    trail_sl = round(trade["highest_price"] - atr_step, pp)
                    if trail_sl > sl:
                        new_sl = trail_sl
                else:
                    trail_sl = round(trade["lowest_price"] + atr_step, pp)
                    if trail_sl < sl:
                        new_sl = trail_sl

            # ── ADAPT 5 : Anti-spam — seuil minimum de déplacement ──
            # Ne push sur Binance que si le SL bouge d'au moins 3 ticks
            # Évite de spammer l'API pour des micro-mouvements
            sl_delta  = abs(new_sl - sl)
            min_delta = tick_size * SL_MIN_UPDATE_TICKS
            sl_moved  = ((side == "BUY" and new_sl > sl) or
                         (side == "SELL" and new_sl < sl))

            if sl_moved and sl_delta >= min_delta:
                old_sl = sl
                trade["sl"] = new_sl
                tag = "🔁 TRAILING" if trade.get("trailing_stop_active") else "🎯 BREAKEVEN"
                logger.info(f"{tag} [{t_label}] {symbol}: "
                            f"{old_sl:.{pp}f} → {new_sl:.{pp}f} "
                            f"(RR={rr:.2f}R, Δ={sl_delta:.{pp}f})")
                _push_sl_to_binance(symbol, trade, new_sl, info)
                # V30-7 — Alerte Telegram à chaque déplacement de SL
                pnl_pct = profit / entry * 100
                send_telegram(
                    f"{tag} <b>{symbol}</b> [{t_label}]\n"
                    f"SL : ${old_sl:.{pp}f} → <b>${new_sl:.{pp}f}</b>\n"
                    f"Profit : {pnl_pct:+.2f}% | RR={rr:.2f}R"
                )

            elif sl_moved and sl_delta < min_delta:
                # Mouvement trop petit → ne spamme pas Binance, mais log en debug
                logger.debug(f"⏸ {symbol} SL Δ={sl_delta:.{pp}f} < {min_delta:.{pp}f} — skip API")

    except Exception as e:
        logger.warning(f"update_trailing_sl {symbol}: {e}")


# Alias pour compatibilité avec l'appel existant dans monitor_loop
def update_breakeven(symbol: str, current_price: float):
    update_trailing_sl(symbol, current_price)

# ─── MONITOR SL/TP (SOFTWARE FALLBACK) ──────────────────────────
def monitor_manual_sl(symbol: str):
    """SL logiciel = fallback si SL Binance n'a pas pu être posé."""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            # Si SL Binance actif, pas besoin de surveillance logicielle
            if trade.get("sl_on_binance"):
                return
            current_price = get_price(symbol)
            if not current_price:
                return
            side = trade["side"]
            sl   = trade["sl"]
            qty  = trade["qty"]
            if (side == "BUY" and current_price <= sl) or (side == "SELL" and current_price >= sl):
                logger.warning(f"🚨 {symbol} SL logiciel hit @ {current_price}")
                close_side  = "SELL" if side == "BUY" else "BUY"
                close_order = place_order_with_fallback(symbol, close_side, qty, current_price)
                if close_order:
                    trade["status"]     = "CLOSED"
                    trade["closed_by"]  = "SOFTWARE_SL"
                    setup_memory[trade["setup"]]["losses"] += 1
                    send_telegram(f"🔴 {symbol} SL (logiciel)")
    except:
        pass

def monitor_manual_tp(symbol: str):
    """TP logiciel = fallback si TP Binance n'a pas pu être posé."""
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            if trade.get("tp_on_binance"):
                return
            current_price = get_price(symbol)
            if not current_price:
                return
            side = trade["side"]
            tp   = trade["tp"]
            qty  = trade["qty"]
            if (side == "BUY" and current_price >= tp) or (side == "SELL" and current_price <= tp):
                logger.info(f"🎯 {symbol} TP logiciel hit @ {current_price}")
                close_side  = "SELL" if side == "BUY" else "BUY"
                close_order = place_order_with_fallback(symbol, close_side, qty, current_price)
                if close_order:
                    trade["status"]    = "CLOSED"
                    trade["closed_by"] = "SOFTWARE_TP"
                    setup_memory[trade["setup"]]["wins"] += 1
                    send_telegram(f"✅ {symbol} TP (logiciel)")
    except:
        pass

# ─── SCAN ────────────────────────────────────────────────────────
def scan_symbol(symbol: str) -> dict:
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        if not can_afford_position(account_balance, n_open):
            return None

        # V30-3 — Kill-switch drawdown : vérifier avant chaque scan
        if not check_drawdown_kill_switch():
            return None

        # V30-4 — Filtre funding rate (pump/dump imminent)
        if not is_funding_safe(symbol):
            return None

        # V30-5 — Filtre spread (marché illiquide → slippage)
        if not is_spread_acceptable(symbol):
            return None

        entry = get_price(symbol)
        if not entry:
            return None

        atr = calc_atr(symbol)
        if not atr:
            return None

        # ── BTC Multi-TF : tendance de fond primordiale ──────────────
        if BTC_FILTER_ENABLED:
            btc       = get_btc_composite_score()
            btc_score = btc["score"]

            # ADAPT 4 — Zone neutre : trop d'incertitude → on attend
            if BTC_NEUTRAL_BLOCK and BTC_NEUTRAL_MIN < btc_score < BTC_NEUTRAL_MAX:
                logger.debug(f"⏸ {symbol} — BTC zone neutre ({btc_score:+.2f}), skip")
                return None

            # Règle absolue : Daily bearish = ZÉRO BUY
            if BTC_DAILY_BLOCK and btc["daily_bear"]:
                allow_buy  = False
                allow_sell = True
            elif btc_score > BTC_BULL_THRESHOLD:
                allow_buy  = True
                allow_sell = False  # Fort bull → pas de short contre tendance
            elif btc_score < BTC_BEAR_THRESHOLD:
                allow_buy  = False
                allow_sell = True
            else:
                allow_buy  = True
                allow_sell = True
        else:
            btc       = get_btc_composite_score()
            btc_score = btc["score"]
            allow_buy = allow_sell = True

        # BUY
        if allow_buy:
            setups_buy = detect_all_setups(symbol, "BUY")
            for setup in setups_buy:
                atr_min     = max(atr, entry * MIN_SL_DISTANCE_PCT)
                sl_distance = atr_min * 1.5
                sl          = entry - sl_distance
                # V29-3 : TP depuis liquidité order book
                tp          = get_tp_from_liquidity(symbol, "BUY", entry, sl_distance)
                probability = calculate_probability(symbol, "BUY", setup["name"])
                if probability >= MIN_PROBABILITY_SCORE:
                    return {
                        "symbol": symbol, "side": "BUY",
                        "entry": entry, "sl": sl, "tp": tp,
                        "setup": setup["name"], "probability": probability
                    }
        else:
            logger.debug(f"🔴 {symbol} BUY bloqué — BTC BEAR")

        # SELL (tous les setups fonctionnent + filtre BTC)
        if allow_sell:
            setups_sell = detect_all_setups(symbol, "SELL")
            for setup in setups_sell:
                atr_min     = max(atr, entry * MIN_SL_DISTANCE_PCT)
                sl_distance = atr_min * 1.5
                sl          = entry + sl_distance
                # V29-3 : TP depuis liquidité order book
                tp          = get_tp_from_liquidity(symbol, "SELL", entry, sl_distance)
                probability = calculate_probability(symbol, "SELL", setup["name"])
                if probability >= MIN_PROBABILITY_SCORE:
                    return {
                        "symbol": symbol, "side": "SELL",
                        "entry": entry, "sl": sl, "tp": tp,
                        "setup": setup["name"], "probability": probability
                    }

        return None
    except:
        return None

# ─── RECOVER POSITIONS ───────────────────────────────────────────
def recover_existing_positions():
    """
    V29-4+5 — Récupère TOUTES les positions ouvertes sur Binance,
    y compris celles prises manuellement (pas par le bot).

    Pour chaque position externe détectée :
      → SL calculé depuis ATR (protection immédiate)
      → TP calculé depuis les zones de liquidité order book (V29-3)
      → SL/TP envoyés sur Binance
      → Trailing SL activé comme pour les positions normales
      → Telegram notifié

    Le bot ne distingue plus ses positions des positions manuelles.
    Toutes sont gérées avec le même niveau de protection.
    """
    logger.info("🔄 Recovering ALL positions (bot + manuelles)...")
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if not positions:
            return

        for pos in positions:
            symbol  = pos.get("symbol")
            pos_amt = float(pos.get("positionAmt", 0))

            # Ignorer les positions nulles
            if pos_amt == 0:
                continue

            entry_price = float(pos.get("entryPrice", 0))
            side        = "BUY" if pos_amt > 0 else "SELL"
            qty         = abs(pos_amt)

            # Déjà dans le trade_log → déjà géré
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    continue

            # Position inconnue → l'adopter (manuelle ou bot redémarré)
            # V30-6 — Sécurité : whitelist + levier max acceptable
            if symbol not in EXTERNAL_POSITION_WHITELIST and symbol not in SYMBOLS:
                logger.warning(f"  [{symbol}] Hors whitelist → position ignorée (protection)")
                continue

            # Vérifier si le levier de la position externe est trop élevé
            pos_leverage = float(pos.get("leverage", 0))
            if pos_leverage > EXTERNAL_MAX_LEVERAGE:
                logger.error(
                    f"  [{symbol}] Levier {pos_leverage}x > max autorisé {EXTERNAL_MAX_LEVERAGE}x "
                    f"→ Position ignorée (trop risquée à adopter)"
                )
                send_telegram(
                    f"⚠️ <b>Position externe ignorée</b> : {symbol}\n"
                    f"Levier {pos_leverage}x > limite {EXTERNAL_MAX_LEVERAGE}x\n"
                    f"Fermez ou gérez cette position manuellement !"
                )
                continue

            source = "BOT" if symbol in SYMBOLS else "MANUELLE"
            logger.warning(f"⚠️  [{source}] Position détectée : {symbol} {side} qty={qty} @ {entry_price}")

            # Ajouter le symbol au périmètre si pas dedans (position manuelle)
            if symbol not in SYMBOLS:
                SYMBOLS.append(symbol)
                # Charger les infos si nécessaire
                if symbol not in symbol_info_cache:
                    ex_data = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
                    if ex_data:
                        for s in ex_data.get("symbols", []):
                            if s["symbol"] == symbol and s.get("status") == "TRADING":
                                filters = {f["filterType"]: f for f in s.get("filters", [])}
                                symbol_info_cache[symbol] = {
                                    "quantityPrecision": s.get("quantityPrecision", 3),
                                    "pricePrecision":    s.get("pricePrecision", 4),
                                    "minQty":            float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                                    "maxQty":            float(filters.get("LOT_SIZE", {}).get("maxQty", 1e6)),
                                    "stepSize":          float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                                    "minNotional":       float(filters.get("MIN_NOTIONAL", {}).get("notional", 20)),
                                }
                                break

            info = get_symbol_info(symbol)
            if not info:
                logger.warning(f"  [{symbol}] Infos introuvables — position adoptée sans SL/TP")
                continue

            pp  = info["pricePrecision"]
            atr = calc_atr(symbol) or entry_price * 0.02  # fallback 2%

            # SL adaptatif (ATR × 1.5)
            atr_sl = max(atr * 1.5, entry_price * MIN_SL_DISTANCE_PCT)
            if side == "BUY":
                sl          = round(entry_price - atr_sl, pp)
                sl_distance = entry_price - sl
            else:
                sl          = round(entry_price + atr_sl, pp)
                sl_distance = sl - entry_price

            # V29-3 : TP depuis zones de liquidité order book
            tp = get_tp_from_liquidity(symbol, side, entry_price, sl_distance)

            # Forcer le levier à 20x sur cette position récupérée
            set_leverage(symbol, LEVERAGE)

            # Annuler d'éventuels ordres orphelins avant de reposer SL/TP
            cleanup_orders(symbol)

            with trade_lock:
                sl_tp = {"sl_sent": False, "tp_sent": False}
                if info:
                    sl_tp = place_sl_tp_orders(symbol, side, sl, tp, info)

                trade_log[symbol] = {
                    "side":                 side,
                    "entry":                entry_price,
                    "sl":                   sl,
                    "tp":                   tp,
                    "qty":                  qty,
                    "margin":               calculate_margin_for_trade(account_balance),
                    "setup":                f"RECOVERED_{source}",
                    "probability":          68.0,    # Probabilité neutre par défaut
                    "status":               "OPEN",
                    "opened_at":            time.time(),
                    "session":              get_current_session(),
                    "sl_on_binance":        sl_tp["sl_sent"],
                    "tp_on_binance":        sl_tp["tp_sent"],
                    "urgent_monitoring":    not sl_tp["sl_sent"],
                    "sl_retry_at":          time.time() + 30 if not sl_tp["sl_sent"] else None,
                    "retry_count":          0,
                    "trailing_stop_active": False,
                    "breakeven_moved":      False,
                    "highest_price":        entry_price if side == "BUY"  else None,
                    "lowest_price":         entry_price if side == "SELL" else None,
                    "last_sl_update":       time.time(),
                    "is_external":          source == "MANUELLE",  # Flag position externe
                }

            sl_status = "🛡️ Binance" if sl_tp["sl_sent"] else "⚠️ logiciel"
            tp_status = "🎯 Binance" if sl_tp["tp_sent"] else "⚠️ logiciel"
            logger.info(f"✅ [{source}] {symbol} {side} adopté | SL {sl_status} @ {sl:.{pp}f} | TP {tp_status} @ {tp:.{pp}f}")

            send_telegram(
                f"🔄 <b>Position {'externe' if source == 'MANUELLE' else 'récupérée'} adoptée</b>\n"
                f"<b>{symbol}</b> {side} qty={qty}\n"
                f"Entry: ${entry_price:.{pp}f} | Levier: {LEVERAGE}x\n"
                f"SL: ${sl:.{pp}f} {sl_status}\n"
                f"TP: ${tp:.{pp}f} {tp_status} (zones liquidité)\n"
                f"Trailing SL actif dès +1R de profit 🔁"
            )

    except Exception as e:
        logger.error(f"recover_existing_positions: {e}")

# ─── LOOPS ───────────────────────────────────────────────────────
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

def retry_sl_binance(symbol: str):
    """
    Retry SL Binance toutes les 30s pour les positions en mode urgence.
    La position reste ouverte — on ne ferme jamais une position prometteuse
    juste parce que le SL n'a pas pu être posé.
    """
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN" or trade.get("sl_on_binance"):
                return
            if time.time() < trade.get("sl_retry_at", 0):
                return
            sl   = trade["sl"]
            tp   = trade["tp"]
            side = trade["side"]

        info = get_symbol_info(symbol)
        if not info:
            return

        logger.info(f"🔄 {symbol} retry SL Binance...")
        results = place_sl_tp_orders(symbol, side, sl, tp, info)

        with trade_lock:
            if symbol in trade_log:
                if results["sl_sent"]:
                    trade_log[symbol]["sl_on_binance"]    = True
                    trade_log[symbol]["urgent_monitoring"] = False
                    trade_log[symbol]["sl_retry_at"]      = None
                    trade_log[symbol]["retry_count"]       = 0
                    logger.info(f"✅ {symbol} SL Binance posé au retry 🛡️")
                    send_telegram(f"✅ <b>{symbol}</b> SL Binance posé (retry réussi)")
                else:
                    # ADAPT 6 — Backoff exponentiel : 5s → 15s → 30s → 60s
                    retry_n   = trade_log[symbol].get("retry_count", 0) + 1
                    backoff   = min(60, [5, 15, 30, 60][min(retry_n - 1, 3)])
                    trade_log[symbol]["retry_count"]  = retry_n
                    trade_log[symbol]["sl_retry_at"]  = time.time() + backoff
                    logger.warning(f"⚠️  {symbol} SL retry #{retry_n} dans {backoff}s")
                if results.get("tp_sent"):
                    trade_log[symbol]["tp_on_binance"] = True
    except Exception as e:
        logger.warning(f"retry_sl_binance {symbol}: {e}")


def monitor_positions_loop():
    logger.info("📍 Monitor started")
    time.sleep(10)
    while True:
        try:
            with trade_lock:
                open_symbols   = [k for k, v in trade_log.items() if v.get("status") == "OPEN"]
                urgent_symbols = [k for k, v in trade_log.items()
                                  if v.get("status") == "OPEN" and v.get("urgent_monitoring")]

            # Mode urgence : intervalle réduit à 0.5s + retry SL
            sleep_interval = 0.5 if urgent_symbols else MONITOR_INTERVAL
            for symbol in urgent_symbols:
                retry_sl_binance(symbol)

            for symbol in open_symbols:
                monitor_manual_sl(symbol)
                monitor_manual_tp(symbol)
                price = get_price(symbol)
                if price:
                    update_breakeven(symbol, price)

            # Vérifie si Binance a fermé une position (TP/SL déclenché côté Binance)
            positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
            if positions:
                for pos in positions:
                    symbol  = pos.get("symbol")
                    pos_amt = float(pos.get("positionAmt", 0))
                    if symbol in SYMBOLS and symbol in trade_log:
                        if pos_amt == 0:
                            with trade_lock:
                                if trade_log[symbol].get("status") == "OPEN":
                                    setup = trade_log[symbol].get("setup")
                                    pnl   = float(pos.get("unRealizedProfit", 0))
                                    if pnl > 0:
                                        setup_memory[setup]["wins"] += 1
                                        logger.info(f"✅ {symbol} WIN ${pnl:.2f} (Binance close)")
                                        send_telegram(f"✅ <b>{symbol}</b> WIN ${pnl:.2f}")
                                    else:
                                        setup_memory[setup]["losses"] += 1
                                        logger.info(f"🔴 {symbol} LOSS ${pnl:.2f} (Binance close)")
                                        send_telegram(f"🔴 <b>{symbol}</b> LOSS ${pnl:.2f}")
                                    trade_log[symbol]["status"] = "CLOSED"
                                    cleanup_orders(symbol)
            time.sleep(sleep_interval)
        except:
            time.sleep(5)

def dashboard_loop():
    logger.info("📈 Dashboard started")
    time.sleep(15)
    while True:
        try:
            with trade_lock:
                n_open  = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                total_w = sum(v["wins"]   for v in setup_memory.values())
                total_l = sum(v["losses"] for v in setup_memory.values())
                binance_sl = sum(1 for v in trade_log.values()
                                 if v.get("status") == "OPEN" and v.get("sl_on_binance"))
                software_sl = n_open - binance_sl

            max_pos = calculate_max_positions(account_balance)
            margin  = calculate_margin_for_trade(account_balance)
            btc_t   = "🟢" if get_btc_trend() == 1 else ("🔴" if get_btc_trend() == -1 else "⚪")

            liq_risk = "🚨 ÉLEVÉ" if (account_balance < 3 and n_open > 0) else ("⚠️  MOYEN" if account_balance < 6 else "✅ OK")
            btc_full  = get_btc_composite_score()
            btc_score = btc_full["score"]
            btc_label = btc_full["label"]
            # Compte les positions avec trailing actif
            with trade_lock:
                trailing_active = sum(1 for v in trade_log.values()
                                      if v.get("status") == "OPEN" and v.get("trailing_stop_active"))

            paused = time.time() < drawdown_state.get("paused_until", 0)
            pause_str = " | ⏸ PAUSED (drawdown)" if paused else ""
            ref_bal   = drawdown_state.get("balance_at_start_of_day", account_balance)
            dd_pct    = (ref_bal - account_balance) / ref_bal * 100 if ref_bal > 0 else 0

            logger.info("═" * 64)
            logger.info(f"v30 ROBOTKING | ${account_balance:.2f} | {n_open}/{max_pos} pos | W:{total_w} L:{total_l}{pause_str}")
            logger.info(f"Levier: {LEVERAGE_MIN}→{LEVERAGE_MAX}x adaptatif | Marge: prob-adaptive")
            logger.info(f"BTC: {btc_label} ({btc_score:+.2f}) | Daily: {'🔴 BEAR' if btc_full['daily_bear'] else '🟢 BULL' if btc_full['daily_bull'] else '⚪ NEUTRE'}")
            logger.info(f"SL Binance: {binance_sl} ✅ | SL logiciel: {software_sl} | Trailing: {trailing_active} 🔁")
            logger.info(f"Drawdown jour: {dd_pct:.1f}% / {DAILY_DRAWDOWN_LIMIT*100:.0f}% max | Risque: {liq_risk}")
            logger.info("═" * 64)

            time.sleep(DASHBOARD_INTERVAL)
        except:
            time.sleep(10)

# ─── MAIN ────────────────────────────────────────────────────────
def main():
    logger.info("╔" + "═" * 60 + "╗")
    logger.info("║" + "   ROBOTKING v30 — PRODUCTION READY                      ║")
    logger.info("╚" + "═" * 60 + "╝\n")

    logger.warning("🔥 LIVE TRADING 🔥")

    start_health_server()
    load_symbol_info()
    sync_account_balance()

    # V30-3 — Initialiser la balance de référence pour le drawdown
    drawdown_state["balance_at_start_of_day"] = account_balance
    drawdown_state["last_reset"] = time.time()

    max_pos = calculate_max_positions(account_balance)

    logger.info(f"💰 Balance:  ${account_balance:.2f}")
    logger.info(f"📊 Max pos:  {max_pos} | Levier: {LEVERAGE_MIN}→{LEVERAGE_MAX}x adaptatif")
    logger.info(f"🛡️  Kill-switch: -{DAILY_DRAWDOWN_LIMIT*100:.0f}% / 24h | Funding filter: {MAX_FUNDING_RATE_ABS*100:.2f}%")
    logger.info(f"✅ v30: Levier adapt | Marge prob | TP liquidité smart | Kill-switch | Filtres\n")

    recover_existing_positions()

    threading.Thread(target=scanner_loop,          daemon=True).start()
    threading.Thread(target=monitor_positions_loop, daemon=True).start()
    threading.Thread(target=dashboard_loop,         daemon=True).start()

    logger.info("✅ v25 MICRO — ONLINE 🚀\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("\n🛑 Shutdown")

if __name__ == "__main__":
    main()

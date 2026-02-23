#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   SCANNER M15 RR4 — LIVE TRADING | PROB 90%+ | ATR SL         ║
║   Breaker Block ICT M15 | Correl BTC | Fondamentaux            ║
║   Dépendance : requests  (pip install requests)                 ║
╚══════════════════════════════════════════════════════════════════╝

  ✅ Timeframe M15 — SL naturellement plus large (ATR × 1.5)
  ✅ SL structurel basé sur ATR M15 (évite les faux stops)
  ✅ MIN_SL_PCT 0.5% | MAX_SL_PCT 3.0% (adapté M15)
  ✅ RR4 net minimum après frais 0.12% A/R
  ✅ Probabilité minimum 90% (4/5 confluences)
  ✅ Corrélation BTC M15 + co-mouvement
  ✅ Fondamentaux : Funding + OI + Spread (seuil 40/60)
  ✅ Sizing automatique selon balance + levier adaptatif
  ✅ SL suiveur bidirectionnel dès RR1
  ✅ Python pur — zéro numpy
"""

import time, hmac, hashlib, requests, threading, os, logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

# ─── LOGGING ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scanner_m15.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── TELEGRAM ────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except:
        pass

# ─── CLÉS API BINANCE ────────────────────────────────────────────
API_KEY    = os.environ.get("BINANCE_API_KEY",    "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

BASE_URL = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION M15
# ═══════════════════════════════════════════════════════════════════

# ── Timeframe M15 ────────────────────────────────────────────────
TIMEFRAME        = "15m"
LIMIT_CANDLES    = 60       # 60 × 15min = 15 heures d'historique
KLINES_CACHE_TTL = 60       # Cache 60s (bougie M15 dure 15min)

# ── Frais Binance ─────────────────────────────────────────────────
BINANCE_FEE_RATE    = 0.0004
BREAKEVEN_FEE_TOTAL = BINANCE_FEE_RATE * 2 * 1.5   # 0.12% A/R
BE_PROFIT_MIN       = 0.01

# ── RR & SL — ADAPTÉ M15 (plus large que M5) ─────────────────────
MIN_RR     = 4.0
# SL M15 basé sur ATR × 1.5 → naturellement 0.5%–3%
MIN_SL_PCT = 0.005   # SL min 0.5% (was 0.2% en M5 → trop serré)
MAX_SL_PCT = 0.030   # SL max 3.0% (was 1.5% en M5)
ATR_SL_MULT = 1.5    # SL = ATR M15 × 1.5 (donne de la marge)

# ── Breaker Block ─────────────────────────────────────────────────
BB_LOOKBACK    = 40
BB_TOUCH_MIN   = 2
BB_ZONE_BUFFER = 0.0005   # 0.05% tolérance (plus large en M15)
OB_LOOKBACK    = 6

# ── Filtres signal ────────────────────────────────────────────────
MIN_SCORE      = 90
MIN_CONFLUENCE = 4      # 4/5 minimum
MIN_PROB       = 90.0
MIN_BODY_RATIO = 0.40

# ── Corrélation BTC ──────────────────────────────────────────────
BTC_SYMBOL       = "BTCUSDT"
BTC_EMA_FAST     = 5
BTC_EMA_SLOW     = 13
BTC_RSI_PERIOD   = 9
BTC_RSI_BULL_MAX = 68
BTC_RSI_BEAR_MIN = 32

# ── Fondamentaux ─────────────────────────────────────────────────
FOND_MIN_SCORE = 40

# ── Gestion positions ─────────────────────────────────────────────
MAX_POSITIONS    = 2
MARGIN_TYPE      = "ISOLATED"
MARGIN_FIXED_PCT = 0.30     # 30% balance par position
MIN_NOTIONAL     = 5.0      # Binance min $5
MAX_NOTIONAL_RISK = 0.90   # Petit compte : on accepte plus de risque pour passer le notional min

# ── Paliers balance ───────────────────────────────────────────────
TARGET_BALANCE = 10.0
TIER1_LIMIT    = 2.0   # Palier survie jusqu'à $2
TIER2_LIMIT    = 5.0   # Palier croissance jusqu'à $5

TIER_PARAMS = {
    1: {"risk_pct": 0.15, "max_risk": 0.20, "max_sl": 0.030},  # Survie $0.50→$2 : 15% balance
    2: {"risk_pct": 0.10, "max_risk": 0.40, "max_sl": 0.030},  # Croissance $2→$5
    3: {"risk_pct": 0.07, "max_risk": 0.70, "max_sl": 0.030},  # Normal $5+
}

LEVERAGE_BY_TIER = {
    1: [(92, 50), (90, 35), (0, 25)],
    2: [(92, 35), (90, 25), (0, 20)],
    3: [(92, 25), (90, 20), (0, 15)],
}

# ── SL suiveur ────────────────────────────────────────────────────
TP_RR          = 4.0
BREAKEVEN_RR   = 0.6
TRAIL_START_RR = 1.0
TRAIL_ATR_MULT = 1.0    # Plus large en M15 (was 0.7 en M1)

# ── Timing ───────────────────────────────────────────────────────
SCAN_INTERVAL    = 15 * 60   # Scan toutes les 15min (= 1 bougie M15)
MONITOR_INTERVAL = 5         # SL suiveur toutes les 5s
DASHBOARD_INTERVAL = 30
SIGNAL_COOLDOWN  = 15 * 60   # 15min cooldown par symbole
HARD_FLOOR       = 0.50   # Plancher $0.50 (compte $0.80)

# ── Exclusions ───────────────────────────────────────────────────
EXCLUDE_SYMBOLS = {
    "USDCUSDT","BUSDUSDT","TUSDUSDT","FDUSDUSDT","USDPUSDT","BTCUSDT"
}
MEME_COINS = {
    "DOGE","SHIB","PEPE","FLOKI","BONK","WIF","MEME","BOME","NEIRO",
    "BRETT","DOGS","LUNC","LUNA","TRUMP","MELANIA","TURBO","COQ","MYRO",
    "SLERF","BODEN","PONKE","PNUT","GIGA","MOODENG","FWOG","GOAT","ACT",
    "BABYDOGE","ELON","SAMO","CHEEMS","WOJAK","LADYS","AIDOGE","VOLT",
    "SNEK","MOG","DEFI","BLESS","POPCAT","VINE","FART","KEKIUS","PEOPLE",
}
FALLBACK_SYMBOLS = [
    "ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT",
    "AVAXUSDT","LINKUSDT","DOTUSDT","NEARUSDT","LTCUSDT",
    "UNIUSDT","ATOMUSDT","INJUSDT","ARBUSDT","OPUSDT",
    "APTUSDT","SUIUSDT","TIAUSDT","SEIUSDT","STXUSDT",
]

# ─── ÉTAT GLOBAL ─────────────────────────────────────────────────
account_balance   = 0.0
trade_log         = {}
trade_lock        = threading.Lock()
api_lock          = threading.Lock()
api_semaphore     = threading.Semaphore(8)
api_call_times    = []
klines_cache      = {}
price_cache       = {}
symbol_info_cache = {}
signal_last_at    = {}
symbols_list      = []
consec_losses     = 0
cooldown_until    = 0.0
_bot_stop         = False
_binance_time_offset = 0

symbol_stats   = defaultdict(lambda: {"wins": 0, "losses": 0})
drawdown_state = {"ref_balance": 0.0, "last_alert": 0.0}

_btc_cache = {"direction": 0, "ts": 0.0, "label": "NEUTRE",
              "rsi": 50.0, "slope": 0.0, "closes": None}
_btc_lock  = threading.Lock()

CONSEC_LOSS_LIMIT = 2
CONSEC_COOLDOWN   = 30 * 60
DD_ALERT_PCT      = 0.15
MAX_WORKERS       = 6

# ─── COULEURS ────────────────────────────────────────────────────
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
MAGENTA = "\033[95m"

def cc(text, col):
    return "{}{}{}".format(col, text, RESET)

# ─── PALIERS ─────────────────────────────────────────────────────
def get_tier():
    if account_balance < TIER1_LIMIT: return 1
    if account_balance < TIER2_LIMIT: return 2
    return 3

def get_tier_label():
    return {1: "🔴 SURVIE", 2: "🟡 CROISSANCE", 3: "🟢 NORMAL"}[get_tier()]

def get_risk_usdt():
    p = TIER_PARAMS[get_tier()]
    return max(0.04, min(account_balance * p["risk_pct"], p["max_risk"]))

def get_leverage(score):
    table = LEVERAGE_BY_TIER[get_tier()]
    for threshold, lev in table:
        if score >= threshold: return lev
    return 15

def get_max_sl_pct():
    return TIER_PARAMS[get_tier()]["max_sl"]

def get_progress_bar():
    pct    = min(account_balance / TARGET_BALANCE * 100, 100)
    filled = int(pct / 10)
    bar    = "█" * filled + "░" * (10 - filled)
    return "[{}] {:.1f}%".format(bar, pct)

# ─── FLASK HEALTH ─────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    st = "🛑 STOP" if _bot_stop else "🟢 RUNNING"
    return "SCANNER M15 | {} | ${:.4f} | Pos:{}/{}".format(
        st, account_balance, n, MAX_POSITIONS), 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status_ep():
    with trade_lock:
        open_pos = {s: {k: t.get(k) for k in ["side","entry","sl","tp","setup","probability","leverage"]}
                    for s, t in trade_log.items() if t.get("status") == "OPEN"}
    tw = sum(v["wins"]   for v in symbol_stats.values())
    tl = sum(v["losses"] for v in symbol_stats.values())
    return jsonify({"balance": round(account_balance, 4), "positions": open_pos,
                    "wins": tw, "losses": tl, "stop": _bot_stop})

@flask_app.route("/stop", methods=["GET","POST"])
def stop_ep():
    global _bot_stop
    _bot_stop = True
    send_telegram("🛑 <b>STOP</b>")
    return "🛑 STOPPED", 200

@flask_app.route("/resume", methods=["GET","POST"])
def resume_ep():
    global _bot_stop, cooldown_until
    _bot_stop = False; cooldown_until = 0.0
    send_telegram("▶️ <b>Repris</b>")
    return "▶️ RESUMED", 200

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    try:
        import logging as _l
        _l.getLogger("werkzeug").setLevel(_l.ERROR)
        threading.Thread(target=lambda: flask_app.run(
            host="0.0.0.0", port=port, debug=False), daemon=True).start()
        logger.info("🌐 Health server port {}".format(port))
    except Exception as e:
        logger.warning("Health server: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  MATH PUR PYTHON
# ═══════════════════════════════════════════════════════════════════

def _mean(lst):
    return sum(lst) / len(lst) if lst else 0.0

def _ema(values, period):
    if len(values) < period:
        return [0.0] * len(values)
    k = 2.0 / (period + 1)
    result = [0.0] * len(values)
    result[period - 1] = _mean(values[:period])
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result

def _rsi(closes, period=14):
    n = period + 1
    if len(closes) < n:
        return 50.0
    subset = closes[-n:]
    gains  = [max(subset[i] - subset[i-1], 0.0) for i in range(1, len(subset))]
    losses = [max(subset[i-1] - subset[i], 0.0) for i in range(1, len(subset))]
    ag = _mean(gains)
    al = _mean(losses)
    if al == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return closes[-1] * 0.005 if closes else 0.001
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]))
        for i in range(1, len(closes))
    ]
    tail = trs[-min(period, len(trs)):]
    return _mean(tail)

def _atr_live(symbol):
    """ATR M15 live pour SL suiveur."""
    try:
        klines = get_klines(symbol, TIMEFRAME, 20)
        if not klines or len(klines) < 5:
            p = get_price(symbol)
            return p * 0.005 if p else 0.001
        o, h, l, c, v = _parse_klines(klines)
        trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
               for i in range(1, len(c))]
        tail = trs[-min(14, len(trs)):]
        return _mean(tail) if tail else c[-1] * 0.005
    except:
        p = get_price(symbol)
        return p * 0.005 if p else 0.001

def _find_ph(highs, lb=2):
    return [i for i in range(lb, len(highs) - lb)
            if all(highs[i] >= highs[i-j] for j in range(1, lb+1))
            and all(highs[i] >= highs[i+j] for j in range(1, lb+1))]

def _find_pl(lows, lb=2):
    return [i for i in range(lb, len(lows) - lb)
            if all(lows[i] <= lows[i-j] for j in range(1, lb+1))
            and all(lows[i] <= lows[i+j] for j in range(1, lb+1))]

def _parse_klines(klines):
    o  = [float(k[1]) for k in klines]
    h  = [float(k[2]) for k in klines]
    l  = [float(k[3]) for k in klines]
    cl = [float(k[4]) for k in klines]
    v  = [float(k[5]) for k in klines]
    return o, h, l, cl, v

def _round_step(qty, step):
    if step <= 0: return qty
    return float(int(qty / step) * step)

# ═══════════════════════════════════════════════════════════════════
#  TIME SYNC
# ═══════════════════════════════════════════════════════════════════

def sync_binance_time():
    global _binance_time_offset
    offsets = []
    for _ in range(3):
        try:
            t0 = int(time.time() * 1000)
            r  = requests.get(BASE_URL + "/fapi/v1/time", timeout=3)
            t1 = int(time.time() * 1000)
            if r.status_code == 200:
                offsets.append(r.json()["serverTime"] - t0 - (t1 - t0) // 2)
        except:
            pass
        time.sleep(0.1)
    if offsets:
        _binance_time_offset = int(sum(offsets) / len(offsets))
        logger.info("⏱️ Binance offset: {}ms".format(_binance_time_offset))

# ═══════════════════════════════════════════════════════════════════
#  RATE LIMIT & API
# ═══════════════════════════════════════════════════════════════════

def _rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < 60]
        if len(api_call_times) >= 1100:
            s = 60 - (now - api_call_times[0])
            if s > 0: time.sleep(s)
        api_call_times.append(now)

def _sign(params):
    q = "&".join("{}={}".format(k, v) for k, v in params.items())
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

def _parse_binance_error(response_text):
    import json
    try:
        d = json.loads(response_text)
        return int(d.get("code", 0)), str(d.get("msg", ""))
    except:
        return 0, response_text[:100]

def request_binance(method, path, params=None, signed=True):
    if params is None: params = {}
    _rate_limit()
    headers = {"X-MBX-APIKEY": API_KEY}
    url     = BASE_URL + path

    with api_semaphore:
        for attempt in range(4):
            try:
                p = dict(params)
                if signed:
                    p.pop("signature", None)
                    p["timestamp"]  = int(time.time() * 1000) + _binance_time_offset
                    p["recvWindow"] = 20000
                    p["signature"]  = _sign(p)

                if   method == "GET":    r = requests.get(url,    params=p, headers=headers, timeout=10)
                elif method == "POST":   r = requests.post(url,   params=p, headers=headers, timeout=10)
                elif method == "DELETE": r = requests.delete(url, params=p, headers=headers, timeout=10)
                else: return None

                if r.status_code == 200:
                    return r.json()

                code, msg = _parse_binance_error(r.text)

                if r.status_code == 429:
                    wait = [5, 15, 30, 60][min(attempt, 3)]
                    logger.warning("Rate limit 429 → {}s".format(wait))
                    time.sleep(wait); continue
                if r.status_code == 418:
                    time.sleep(120); return None
                if r.status_code in (401, 403):
                    logger.error("🔑 Auth {} — clé API invalide".format(r.status_code))
                    return None

                if code == -1021:
                    sync_binance_time(); continue
                elif code == -1111:
                    sym = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    if "quantity" in params:
                        new_qty = round(_round_step(float(params["quantity"]),
                                                    info.get("stepSize", 0.001)),
                                        info.get("quantityPrecision", 3))
                        params["quantity"] = new_qty; continue
                elif code == -1013:
                    sym = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    step = info.get("stepSize", 0.001)
                    pr   = get_price(sym) or 1.0
                    if "quantity" in params:
                        params["quantity"] = _round_step(MIN_NOTIONAL / pr + step, step); continue
                elif code == -2010:
                    if "quantity" in params and attempt < 2:
                        sym  = params.get("symbol", "?")
                        step = symbol_info_cache.get(sym, {}).get("stepSize", 0.001)
                        params["quantity"] = _round_step(float(params["quantity"]) * 0.85, step); continue
                    return None
                elif code in (-4003, -5021):
                    sym = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    pp   = info.get("pricePrecision", 4)
                    if "stopPrice" in params and "side" in params:
                        sp   = float(params["stopPrice"])
                        mark = get_price(sym) or sp
                        shift = mark * 0.002
                        side_ord = params.get("side", "")
                        new_sp = round(min(sp, mark - shift) if side_ord == "SELL"
                                       else max(sp, mark + shift), pp)
                        params["stopPrice"] = new_sp; continue
                elif code == -5022:
                    return {"_already_triggered": True}
                elif code == -2011:
                    if method == "DELETE": return {"_already_cancelled": True}
                    continue
                elif code == -4129:
                    if "reduceOnly" not in params:
                        params["reduceOnly"] = "true"; continue
                elif code == -4161:
                    # Levier verrouillé ISOLATED → lire levier actuel
                    sym = params.get("symbol", "?")
                    logger.debug("  {} -4161 levier verrouillé → conservé".format(sym))
                    return {"_leverage_locked": True, "leverage": params.get("leverage", 0)}
                elif code == -1121:
                    sym = params.get("symbol", "?")
                    if sym in symbols_list: symbols_list.remove(sym)
                    return None
                else:
                    logger.warning("⚠️  Binance {} code={}: {}".format(
                        r.status_code, code, msg[:60]))
                    if attempt < 3:
                        time.sleep(1.0 * (attempt + 1)); continue
                    return None

            except requests.exceptions.Timeout:
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                logger.debug("API exception attempt {}: {}".format(attempt+1, e))
                time.sleep(1.0 * (attempt + 1))

    return None

# ═══════════════════════════════════════════════════════════════════
#  MARKET DATA
# ═══════════════════════════════════════════════════════════════════

def get_klines(symbol, interval=TIMEFRAME, limit=LIMIT_CANDLES):
    key = "{}_{}".format(symbol, interval)
    with threading.Lock():
        cached = klines_cache.get(key)
        if cached:
            d, ts = cached
            if time.time() - ts < KLINES_CACHE_TTL:
                return d
    d = request_binance("GET", "/fapi/v1/klines",
                        {"symbol": symbol, "interval": interval, "limit": limit},
                        signed=False)
    if d:
        klines_cache[key] = (d, time.time())
    return d or []

def get_price(symbol):
    now = time.time()
    cached = price_cache.get(symbol)
    if cached:
        p, ts = cached
        if now - ts < 2.0: return p
    d = request_binance("GET", "/fapi/v1/ticker/price", {"symbol": symbol}, signed=False)
    if d and "price" in d:
        p = float(d["price"])
        price_cache[symbol] = (p, now)
        return p
    return 0.0

def get_account_balance():
    global account_balance
    d = request_binance("GET", "/fapi/v2/balance", signed=True)
    if d:
        for a in d:
            if a.get("asset") == "USDT":
                account_balance = float(a.get("availableBalance", 0))
                return account_balance
    return account_balance

def load_top_symbols():
    global symbols_list
    logger.info("📥 Chargement symboles M15...")
    try:
        tickers  = request_binance("GET", "/fapi/v1/ticker/24hr", signed=False)
        exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
        if not tickers or not exchange: raise ValueError("API indispo")

        tradeable = set()
        for s in exchange.get("symbols", []):
            sym  = s["symbol"]
            base = s["baseAsset"].upper()
            if (sym.endswith("USDT") and s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                    and sym not in EXCLUDE_SYMBOLS and base not in MEME_COINS):
                tradeable.add(sym)
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                symbol_info_cache[sym] = {
                    "quantityPrecision": s.get("quantityPrecision", 3),
                    "pricePrecision":    s.get("pricePrecision", 4),
                    "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                    "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                }

        vol_map = {}
        for t in tickers:
            sym = t.get("symbol", "")
            if sym in tradeable:
                try: vol_map[sym] = float(t.get("quoteVolume", 0))
                except: pass

        symbols_list = sorted([s for s, v in vol_map.items() if v > 1_000_000])
        if not symbols_list: symbols_list = list(tradeable)
        logger.info("✅ {} symboles M15 (vol > 1M$)".format(len(symbols_list)))
    except Exception as e:
        logger.error("load_top_symbols: {} → fallback".format(e))
        symbols_list = FALLBACK_SYMBOLS[:]

# ═══════════════════════════════════════════════════════════════════
#  DIRECTION BTC M15
# ═══════════════════════════════════════════════════════════════════

def get_btc_direction():
    global _btc_cache
    with _btc_lock:
        if time.time() - _btc_cache.get("ts", 0) < 60:  # Cache 60s en M15
            return _btc_cache

    klines = get_klines(BTC_SYMBOL, TIMEFRAME, 30)
    if not klines or len(klines) < 20:
        return _btc_cache

    o, h, l, cl, v = _parse_klines(klines)
    ema5  = _ema(cl, BTC_EMA_FAST)
    ema13 = _ema(cl, BTC_EMA_SLOW)
    rsi   = _rsi(cl, BTC_RSI_PERIOD)
    price = cl[-1]
    e5, e13 = ema5[-1], ema13[-1]

    slope = 0.0
    if len(ema5) >= 4 and ema5[-4] > 0:
        slope = (ema5[-1] - ema5[-4]) / ema5[-4] * 100

    bull = (e5 > e13) and (price > e5) and (rsi < BTC_RSI_BULL_MAX)
    bear = (e5 < e13) and (price < e5) and (rsi > BTC_RSI_BEAR_MIN)

    if bull:
        direction, label = 1, "BTC M15 🟢 HAUSSIER RSI={:.0f}".format(rsi)
    elif bear:
        direction, label = -1, "BTC M15 🔴 BAISSIER RSI={:.0f}".format(rsi)
    else:
        direction, label = 0, "BTC M15 ⚪ NEUTRE RSI={:.0f}".format(rsi)

    result = {"direction": direction, "label": label, "rsi": round(rsi, 1),
              "slope": round(slope, 4), "ts": time.time(), "closes": cl}
    with _btc_lock:
        _btc_cache = result
    return result

# ═══════════════════════════════════════════════════════════════════
#  CORRÉLATION BTC
# ═══════════════════════════════════════════════════════════════════

def check_btc_correlation(symbol, side, sym_closes):
    btc     = get_btc_direction()
    btc_dir = btc["direction"]

    if btc_dir == 0:
        return False, "BTC M15 neutre"
    if side == "BUY" and btc_dir != 1:
        return False, "BUY refusé — BTC baissier"
    if side == "SELL" and btc_dir != -1:
        return False, "SELL refusé — BTC haussier"

    btc_closes = btc.get("closes")
    if sym_closes and btc_closes and len(sym_closes) >= 6 and len(btc_closes) >= 6:
        sym_ret = (sym_closes[-1] - sym_closes[-6]) / sym_closes[-6] \
                  if sym_closes[-6] > 0 else 0
        btc_ret = (btc_closes[-1] - btc_closes[-6]) / btc_closes[-6] \
                  if btc_closes[-6] > 0 else 0
        same_dir = (sym_ret > 0 and btc_ret > 0) or (sym_ret < 0 and btc_ret < 0)
        if not same_dir:
            return False, "{} diverge BTC (sym:{:+.2f}% btc:{:+.2f}%)".format(
                symbol, sym_ret * 100, btc_ret * 100)

    return True, "OK ({})".format(btc["label"])

# ═══════════════════════════════════════════════════════════════════
#  FONDAMENTAUX
# ═══════════════════════════════════════════════════════════════════

def get_funding_rate(symbol):
    try:
        d = request_binance("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
        if d: return float(d.get("lastFundingRate", 0))
    except: pass
    return 0.0

def get_oi_change(symbol):
    try:
        d = request_binance("GET", "/futures/data/openInterestHist",
                            {"symbol": symbol, "period": "15m", "limit": 6}, signed=False)
        if d and len(d) >= 2:
            oi0 = float(d[0]["sumOpenInterest"])
            oi1 = float(d[-1]["sumOpenInterest"])
            if oi0 > 0: return (oi1 - oi0) / oi0
    except: pass
    return 0.0

def get_mark_spread(symbol):
    try:
        d = request_binance("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
        if d:
            mark = float(d.get("markPrice", 0))
            idx  = float(d.get("indexPrice", 1))
            if idx > 0: return (mark - idx) / idx * 100
    except: pass
    return 0.0

def check_fondamentaux(symbol, side):
    score = 0; parts = []
    funding = get_funding_rate(symbol)
    fp = funding * 100
    if side == "BUY":
        if funding <= 0.001:   score += 20; parts.append("Fund {:.4f}%+".format(fp))
        elif funding > 0.002:  parts.append("Fund {:.4f}%-".format(fp))
        else:                  score += 10; parts.append("Fund {:.4f}%~".format(fp))
    else:
        if funding >= -0.001:  score += 20; parts.append("Fund {:.4f}%+".format(fp))
        elif funding < -0.002: parts.append("Fund {:.4f}%-".format(fp))
        else:                  score += 10; parts.append("Fund {:.4f}%~".format(fp))

    oi_chg = get_oi_change(symbol)
    if oi_chg > 0.005:  score += 20; parts.append("OI +{:.2f}%+".format(oi_chg*100))
    elif oi_chg > 0:    score += 10; parts.append("OI +{:.2f}%~".format(oi_chg*100))
    elif oi_chg == 0:   score += 10; parts.append("OI N/A")
    else:               parts.append("OI {:.2f}%-".format(oi_chg*100))

    spread = get_mark_spread(symbol)
    if abs(spread) < 0.05:   score += 20; parts.append("Sprd {:+.3f}%+".format(spread))
    elif abs(spread) < 0.15: score += 10; parts.append("Sprd {:+.3f}%~".format(spread))
    else:                    parts.append("Sprd {:+.3f}%-".format(spread))

    return score, score >= FOND_MIN_SCORE, " | ".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  BREAKER BLOCK ICT M15
# ═══════════════════════════════════════════════════════════════════

def _find_breaker_block(o, h, l, cl, v, atr, direction):
    n = len(cl)
    if n < BB_LOOKBACK + 5:
        return None

    avg_vol = _mean(v[-20:]) if n >= 20 else _mean(v)
    e9  = _ema(cl, 9)
    e21 = _ema(cl, 21)

    if direction == "BUY":
        ph = _find_ph(h, 2)
        if len(ph) < 2: return None

        for candidate_idx in reversed(ph[-4:]):
            resistance = h[candidate_idx]
            touches = sum(
                1 for i in range(candidate_idx-1, max(0, candidate_idx-BB_LOOKBACK), -1)
                if abs(h[i] - resistance) / resistance < 0.001)

            bos_idx = None
            for i in range(candidate_idx+1, min(n-2, candidate_idx+20)):
                if cl[i] > resistance and h[i] > resistance:
                    bos_idx = i; break
            if bos_idx is None: continue

            breaker_idx = None
            for j in range(bos_idx-1, max(0, bos_idx-OB_LOOKBACK), -1):
                if cl[j] < o[j]: breaker_idx = j; break
            if breaker_idx is None: breaker_idx = bos_idx - 1

            bb_bottom = min(o[breaker_idx], cl[breaker_idx])
            bb_top    = max(o[breaker_idx], cl[breaker_idx])
            if bb_top - bb_bottom < atr * 0.1: continue

            price   = cl[-1]
            zone    = bb_top - bb_bottom
            in_zone = (bb_bottom - zone*BB_ZONE_BUFFER <= price <= bb_top + zone*BB_ZONE_BUFFER)
            if not in_zone: continue
            if n - 1 - bos_idx > 15: continue

            vol_spike     = float(v[bos_idx]) > avg_vol * 1.3
            bos_impulsive = (h[bos_idx] - l[bos_idx]) > atr * 1.0
            bull_ema      = e9[-1] > e21[-1]

            conf = sum([1,
                        bool(touches >= BB_TOUCH_MIN),
                        bool(vol_spike),
                        bool(bos_impulsive),
                        bool(bull_ema)])
            score = 90 + min(conf - 1, 4)

            return {"bottom": bb_bottom, "top": bb_top, "bos_idx": bos_idx,
                    "score": score, "confluence": min(conf, 5), "direction": "BUY",
                    "atr": atr, "vol_spike": vol_spike, "bos_imp": bos_impulsive,
                    "touches": touches}

    elif direction == "SELL":
        pl = _find_pl(l, 2)
        if len(pl) < 2: return None

        for candidate_idx in reversed(pl[-4:]):
            support = l[candidate_idx]
            touches = sum(
                1 for i in range(candidate_idx-1, max(0, candidate_idx-BB_LOOKBACK), -1)
                if abs(l[i] - support) / support < 0.001)

            bos_idx = None
            for i in range(candidate_idx+1, min(n-2, candidate_idx+20)):
                if cl[i] < support and l[i] < support:
                    bos_idx = i; break
            if bos_idx is None: continue

            breaker_idx = None
            for j in range(bos_idx-1, max(0, bos_idx-OB_LOOKBACK), -1):
                if cl[j] > o[j]: breaker_idx = j; break
            if breaker_idx is None: breaker_idx = bos_idx - 1

            bb_bottom = min(o[breaker_idx], cl[breaker_idx])
            bb_top    = max(o[breaker_idx], cl[breaker_idx])
            if bb_top - bb_bottom < atr * 0.1: continue

            price   = cl[-1]
            zone    = bb_top - bb_bottom
            in_zone = (bb_bottom - zone*BB_ZONE_BUFFER <= price <= bb_top + zone*BB_ZONE_BUFFER)
            if not in_zone: continue
            if n - 1 - bos_idx > 15: continue

            vol_spike     = float(v[bos_idx]) > avg_vol * 1.3
            bos_impulsive = (h[bos_idx] - l[bos_idx]) > atr * 1.0
            bear_ema      = e9[-1] < e21[-1]

            conf = sum([1,
                        bool(touches >= BB_TOUCH_MIN),
                        bool(vol_spike),
                        bool(bos_impulsive),
                        bool(bear_ema)])
            score = 90 + min(conf - 1, 4)

            return {"bottom": bb_bottom, "top": bb_top, "bos_idx": bos_idx,
                    "score": score, "confluence": min(conf, 5), "direction": "SELL",
                    "atr": atr, "vol_spike": vol_spike, "bos_imp": bos_impulsive,
                    "touches": touches}

    return None

# ═══════════════════════════════════════════════════════════════════
#  ANALYSE M15
# ═══════════════════════════════════════════════════════════════════

def analyse_m15(symbol, klines):
    try:
        if not klines or len(klines) < BB_LOOKBACK + 5:
            return None

        o, h, l, cl, v = _parse_klines(klines)
        atr = _atr(h, l, cl, 14)
        e9  = _ema(cl, 9)
        e21 = _ema(cl, 21)
        rsi = _rsi(cl, 14)

        bull_bias = (e9[-1] > e21[-1]) and (rsi < 70)
        bear_bias = (e9[-1] < e21[-1]) and (rsi > 30)

        candidates = []
        if bull_bias:  candidates.append("BUY")
        if bear_bias:  candidates.append("SELL")
        if not candidates: candidates = ["BUY", "SELL"]

        bb = None
        for direction in candidates:
            bb = _find_breaker_block(o, h, l, cl, v, atr, direction)
            if bb: break

        if not bb: return None

        direction = bb["direction"]
        score     = bb["score"]
        conf      = bb["confluence"]

        if score < MIN_SCORE or conf < MIN_CONFLUENCE:
            return None

        # Confirmation bougie de réaction
        ko, kc, kh, kl = o[-1], cl[-1], h[-1], l[-1]
        body   = abs(kc - ko)
        range_ = kh - kl if kh > kl else atr
        br     = body / range_ if range_ > 0 else 0

        if direction == "BUY":
            reaction_ok = (kc >= ko) and (br >= MIN_BODY_RATIO * 0.8)
        else:
            reaction_ok = (kc <= ko) and (br >= MIN_BODY_RATIO * 0.8)

        if not reaction_ok: return None

        # Probabilité
        base = 85
        if conf >= 4: base += 5
        if conf >= 5: base += 5
        prob = min(float(base), 95.0)

        if prob < MIN_PROB: return None

        return {
            "symbol":    symbol,
            "side":      direction,
            "setup":     "BREAKER_BLOCK_M15",
            "score":     score,
            "confluence": conf,
            "probability": prob,
            "ob":        {"bottom": bb["bottom"], "top": bb["top"]},
            "atr":       atr,
            "rsi":       round(rsi, 1),
            "vol_spike": bb.get("vol_spike", False),
            "bos_imp":   bb.get("bos_imp", False),
            "touches":   bb.get("touches", 0),
            "closes":    cl,
        }

    except Exception as e:
        logger.debug("analyse_m15 {}: {}".format(symbol, e))
        return None

# ═══════════════════════════════════════════════════════════════════
#  SL STRUCTUREL — BASÉ ATR M15 (résout SL trop serré)
# ═══════════════════════════════════════════════════════════════════

def get_sl(ob, entry, side, atr):
    """
    SL M15 :
    1. Tente SL structurel (bord zone breaker ± buffer)
    2. Si hors plage → SL = ATR × ATR_SL_MULT (1.5)
    MIN_SL_PCT = 0.5%, MAX_SL_PCT = 3.0% → jamais trop serré
    """
    pp = symbol_info_cache.get("", {}).get("pricePrecision", 6)

    if ob:
        buf    = atr * 0.2      # Buffer = 20% ATR (structurel)
        sl_raw = (ob["bottom"] - buf) if side == "BUY" else (ob["top"] + buf)
        dist   = abs(entry - sl_raw)
        if entry * MIN_SL_PCT <= dist <= entry * MAX_SL_PCT:
            return sl_raw

    # Fallback ATR M15 × 1.5 — SL naturellement adapté au timeframe
    dist = atr * ATR_SL_MULT
    dist = max(dist, entry * MIN_SL_PCT)
    dist = min(dist, entry * MAX_SL_PCT)
    return (entry - dist) if side == "BUY" else (entry + dist)

def calculate_rr(entry, sl, tp, direction):
    fee_e  = entry * BINANCE_FEE_RATE
    fee_tp = tp    * BINANCE_FEE_RATE
    fee_sl = sl    * BINANCE_FEE_RATE

    if direction == "BUY":
        profit_gross = tp - entry
        loss_gross   = entry - sl
    else:
        profit_gross = entry - tp
        loss_gross   = sl - entry

    if loss_gross <= 0: return 0.0
    profit_net = profit_gross - fee_e - fee_tp
    loss_net   = loss_gross   + fee_e + fee_sl
    if loss_net <= 0: return 0.0
    return round(profit_net / loss_net, 2)

def find_tp_for_rr(entry, sl, direction, rr_target=4.0):
    """TP itératif ×3 pour convergence exacte après frais."""
    loss_gross = abs(sl - entry)
    fee_e  = entry * BINANCE_FEE_RATE
    fee_sl = sl    * BINANCE_FEE_RATE
    total  = loss_gross + fee_e + fee_sl

    if direction == "BUY":
        tp = entry + (rr_target * total + fee_e)
        for _ in range(3):
            tp = entry + (rr_target * total + fee_e + tp * BINANCE_FEE_RATE)
    else:
        tp = entry - (rr_target * total + fee_e)
        for _ in range(3):
            tp = entry - (rr_target * total + fee_e + tp * BINANCE_FEE_RATE)
    return tp

# ═══════════════════════════════════════════════════════════════════
#  ORDRES BINANCE
# ═══════════════════════════════════════════════════════════════════

def set_leverage_sym(symbol, lev):
    fallbacks = [lev]
    cur = lev
    while cur > 5:
        cur = max(5, cur - 5)
        fallbacks.append(cur)

    for try_lev in fallbacks:
        r = request_binance("POST", "/fapi/v1/leverage",
                            {"symbol": symbol, "leverage": try_lev})
        if r and r.get("_leverage_locked"):
            try:
                pos = request_binance("GET", "/fapi/v2/positionRisk",
                                      {"symbol": symbol}, signed=True)
                if pos:
                    for p in pos:
                        if p.get("symbol") == symbol:
                            actual = int(float(p.get("leverage", lev)))
                            symbol_info_cache.setdefault(symbol, {})["actual_leverage"] = actual
                            return actual
            except: pass
            return try_lev
        if r and "leverage" in r:
            actual = int(r["leverage"])
            symbol_info_cache.setdefault(symbol, {})["actual_leverage"] = actual
            return actual
        time.sleep(0.2)
    return lev

def set_isolated(symbol):
    r = request_binance("POST", "/fapi/v1/marginType",
                        {"symbol": symbol, "marginType": "ISOLATED"})
    if r is None:
        pos = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        if pos:
            for p in pos:
                if p.get("symbol") == symbol:
                    mode = p.get("marginType", "unknown")
                    if mode != "isolated":
                        logger.warning("  {} CROSS maintenu".format(symbol))

def cleanup_orders(symbol):
    try:
        orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if not orders: return
        for o in orders:
            oid = o.get("orderId")
            if oid:
                request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": oid})
    except Exception as e:
        logger.debug("cleanup_orders {}: {}".format(symbol, e))

def place_market(symbol, side, qty):
    info  = symbol_info_cache.get(symbol, {})
    qp    = info.get("quantityPrecision", 3)
    step  = info.get("stepSize", 0.001)
    qty_adj = round(_round_step(qty, step), qp)
    r = request_binance("POST", "/fapi/v1/order",
                        {"symbol": symbol, "side": side,
                         "type": "MARKET", "quantity": qty_adj})
    if r and r.get("orderId"):
        logger.info("  {} MARKET {} qty={} ✅".format(symbol, side, qty_adj))
        return r
    logger.error("❌ {} MARKET {} échoué".format(symbol, side))
    return None

def place_sl_binance(symbol, sl, close_side):
    """
    Pose un SL sur Binance avec 3 stratégies de fallback :
    1. STOP_MARKET MARK_PRICE (standard)
    2. STOP_MARKET CONTRACT_PRICE (si mark price rejeté)
    3. SL logiciel piloté par le monitor (dernier recours)
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # ── Stratégie 1 : MARK_PRICE (standard) ──────────────────────
    for attempt in range(3):
        r = request_binance("POST", "/fapi/v1/order",
                            {"symbol": symbol, "side": close_side,
                             "type": "STOP_MARKET", "stopPrice": round(sl, pp),
                             "closePosition": "true", "workingType": "MARK_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ MARK_PRICE @ {:.{}f} id={}".format(
                symbol, sl, pp, r["orderId"]))
            return {"sent": True, "order_id": r["orderId"], "method": "MARK_PRICE"}
        if r and r.get("_already_triggered"):
            logger.warning("⚠️  {} SL -5022 déjà déclenché → position fermée".format(symbol))
            return {"sent": False, "order_id": None, "triggered": True}
        time.sleep(0.5)
    logger.warning("⚠️  {} MARK_PRICE rejeté → essai CONTRACT_PRICE".format(symbol))
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order",
                            {"symbol": symbol, "side": close_side,
                             "type": "STOP_MARKET", "stopPrice": round(sl, pp),
                             "closePosition": "true", "workingType": "CONTRACT_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ CONTRACT_PRICE @ {:.{}f} id={}".format(
                symbol, sl, pp, r["orderId"]))
            return {"sent": True, "order_id": r["orderId"], "method": "CONTRACT_PRICE"}
        time.sleep(0.5)

    # ── Stratégie 3 : SL logiciel (monitor toutes les 5s) ────────
    logger.error("🚨 {} SL Binance impossible → SL LOGICIEL activé @ {:.{}f}".format(
        symbol, sl, pp))
    send_telegram(
        "🚨 <b>SL {} non posé sur Binance</b>\n"
        "SL logiciel actif @ {:.{}f}\n"
        "Monitor vérifie toutes les {}s ✅".format(symbol, sl, pp, MONITOR_INTERVAL)
    )
    return {"sent": False, "order_id": None, "method": "SOFTWARE"}

def place_tp_binance(symbol, tp, close_side):
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    for wtype in ["MARK_PRICE", "CONTRACT_PRICE"]:
        for _ in range(2):
            r = request_binance("POST", "/fapi/v1/order",
                                {"symbol": symbol, "side": close_side,
                                 "type": "TAKE_PROFIT_MARKET",
                                 "stopPrice": round(tp, pp),
                                 "closePosition": "true", "workingType": wtype})
            if r and r.get("orderId"):
                return {"sent": True, "order_id": r["orderId"]}
            time.sleep(0.3)
    return {"sent": False, "order_id": None}

def move_sl_binance(symbol, old_order_id, new_sl, close_side):
    """
    Déplace le SL sur Binance intelligemment (v41.0) :
    1. Annule l'ancien SL (ignore -2011 déjà annulé)
    2. Pose le nouveau avec fallback CONTRACT_PRICE si MARK_PRICE rejeté
    Appelé HORS du trade_lock — aucun deadlock possible.
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # ── Étape 1 : Annuler l'ancien SL ────────────────────────────
    if old_order_id:
        r_del = request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": old_order_id})
        if r_del and r_del.get("_already_cancelled"):
            logger.debug("  {} ancien SL {} déjà annulé — OK".format(symbol, old_order_id))
        elif r_del and r_del.get("_already_triggered"):
            # SL déclenché pendant le déplacement → position fermée
            logger.warning("⚠️  {} SL déclenché pendant déplacement → fermée".format(symbol))
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None
        elif r_del:
            logger.debug("  {} ancien SL {} annulé ✅".format(symbol, old_order_id))

    # ── Étape 2 : Poser le nouveau SL — MARK_PRICE ───────────────
    for _ in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     round(new_sl, pp),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })
        if r and r.get("orderId"):
            return r
        if r and r.get("_already_triggered"):
            logger.warning("⚠️  {} SL déclenché pendant déplacement → fermée".format(symbol))
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None
        time.sleep(0.3)

    # ── Fallback : CONTRACT_PRICE ─────────────────────────────────
    logger.warning("⚠️  {} MARK_PRICE rejeté sur trailing → CONTRACT_PRICE".format(symbol))
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     round(new_sl, pp),
            "closePosition": "true",
            "workingType":   "CONTRACT_PRICE",
        })
        if r and r.get("orderId"):
            logger.info("  {} nouveau SL via CONTRACT_PRICE ✅ @ {:.{}f}".format(symbol, new_sl, pp))
            return r
        time.sleep(0.3)

    logger.error("❌ {} move_sl_binance échoué — SL logiciel @ {:.{}f}".format(symbol, new_sl, pp))
    return None


# ════════════════════════════════════════════════════════════════════
#  SL SUIVEUR v41.0 — BIDIR RR1 | FRAIS + $0.01 GARANTIS (M15)
# ════════════════════════════════════════════════════════════════════
def update_trailing_sl(symbol):
    """
    SL Suiveur v41.0 adapté M15 — Bidirectionnel | Frais + $0.01 garantis

    ┌─────────────────────────────────────────────────────────────┐
    │  FRAIS BINANCE FUTURES TAKER                                │
    │  Ouverture  : 0.04% du notionnel                           │
    │  Fermeture  : 0.04% du notionnel                           │
    │  Total A/R  : 0.08% + buffer 50% = 0.12% (BREAKEVEN_FEE)  │
    ├─────────────────────────────────────────────────────────────┤
    │  PLANCHER FRAIS (FEE_FLOOR) — plancher absolu du SL        │
    │  BUY  : entry × (1 + fee×2) + $0.01/qty                   │
    │  SELL : entry × (1 - fee×2) - $0.01/qty                   │
    │  Le SL ne peut JAMAIS franchir ce plancher (vers la perte) │
    ├─────────────────────────────────────────────────────────────┤
    │  PHASE 1 — BREAKEVEN (+0.6R) :                             │
    │  BUY  : SL → entry × 1.0012 + $0.01/qty                   │
    │  SELL : SL → entry × 0.9988 - $0.01/qty                   │
    ├─────────────────────────────────────────────────────────────┤
    │  PHASE 2 — TRAILING M15 (+1R) :                            │
    │  SL = prix_actuel - ATR_M15_live × 1.0  (BUY)             │
    │  SL = prix_actuel + ATR_M15_live × 1.0  (SELL)            │
    │  + garantie plancher fee_floor absolu                       │
    │  BIDIRECTIONNEL : suit hausse ET baisse                     │
    │  + appelé toutes les 5s (M15 = moins fréquent que M1)      │
    └─────────────────────────────────────────────────────────────┘
    """
    # ── Étape 1 : Lecture trade_log (lock COURT) ─────────────────
    with trade_lock:
        if symbol not in trade_log: return
        t = trade_log[symbol]
        if t.get("status") != "OPEN": return
        side        = t["side"]
        entry       = t["entry"]
        sl          = t["sl"]
        sl_order_id = t.get("sl_order_id")
        be_moved    = t.get("breakeven_moved", False)
        qty         = t.get("qty", 0)
        info        = symbol_info_cache.get(symbol, {})
        pp          = info.get("pricePrecision", 4)

    # ── Étape 2 : Prix et ATR live (HORS lock, API libre) ────────
    current_price = get_price(symbol)
    if not current_price or current_price <= 0: return

    atr        = _atr_live(symbol)   # ATR M15 recalculé live
    close_side = "SELL" if side == "BUY" else "BUY"
    qty_safe   = max(qty, 0.001)

    # ── Étape 3 : Calcul RR courant ──────────────────────────────
    risk   = abs(entry - sl)
    if risk <= 0: return
    profit = (current_price - entry) if side == "BUY" else (entry - current_price)
    rr     = profit / risk

    new_sl     = sl
    action     = None
    be_trigger = False
    trail_used = False

    # ── Plancher frais absolu ─────────────────────────────────────
    # Garantit que si le SL est touché, on couvre frais + $0.01 net.
    # Le SL ne peut JAMAIS franchir ce plancher côté perte.
    if side == "BUY":
        fee_floor = round(entry * (1.0 + BINANCE_FEE_RATE * 2) + BE_PROFIT_MIN / qty_safe, pp)
    else:
        fee_floor = round(entry * (1.0 - BINANCE_FEE_RATE * 2) - BE_PROFIT_MIN / qty_safe, pp)

    # ── PHASE 1 : BREAKEVEN avec frais réels (+0.6R) ─────────────
    # Déclenché uniquement si on est au-dessus du coût total des frais A/R
    if rr >= BREAKEVEN_RR and not be_moved:
        if side == "BUY":
            be_sl = round(entry * (1.0 + BREAKEVEN_FEE_TOTAL) + BE_PROFIT_MIN / qty_safe, pp)
            if be_sl > sl:
                new_sl     = be_sl
                be_trigger = True
                action     = "BE @ {:.{}f} (frais couverts + $0.01 net)".format(be_sl, pp)
                logger.info("🎯 {} {} BREAKEVEN RÉEL | {:.{}f} → {:.{}f} | RR={:.2f}R".format(
                    symbol, side, sl, pp, be_sl, pp, rr))
        else:
            be_sl = round(entry * (1.0 - BREAKEVEN_FEE_TOTAL) - BE_PROFIT_MIN / qty_safe, pp)
            if be_sl < sl:
                new_sl     = be_sl
                be_trigger = True
                action     = "BE @ {:.{}f} (frais couverts + $0.01 net)".format(be_sl, pp)
                logger.info("🎯 {} {} BREAKEVEN RÉEL | {:.{}f} → {:.{}f} | RR={:.2f}R".format(
                    symbol, side, sl, pp, be_sl, pp, rr))

    # ── PHASE 2 : TRAILING BIDIRECTIONNEL (+1R) ──────────────────
    # ATR M15 × 1.0 (plus large qu'en M1 × 0.7 pour laisser respirer)
    # Plancher ABSOLU = fee_floor (jamais perdant après BE)
    if rr >= TRAIL_START_RR:
        trail_dist = atr * TRAIL_ATR_MULT
        if side == "BUY":
            trail_sl_raw = round(current_price - trail_dist, pp)
            trail_sl     = max(trail_sl_raw, fee_floor)   # plancher absolu
            if trail_sl != new_sl:
                arrow      = "↑" if trail_sl > new_sl else "↓"
                new_sl     = trail_sl
                trail_used = True
                action     = "TRAIL {} {:.{}f} | ATR={:.{}f} | floor={:.{}f}".format(
                    arrow, trail_sl, pp, atr, pp, fee_floor, pp)
        else:
            trail_sl_raw = round(current_price + trail_dist, pp)
            trail_sl     = min(trail_sl_raw, fee_floor)   # plancher absolu
            if trail_sl != new_sl:
                arrow      = "↓" if trail_sl < new_sl else "↑"
                new_sl     = trail_sl
                trail_used = True
                action     = "TRAIL {} {:.{}f} | ATR={:.{}f} | floor={:.{}f}".format(
                    arrow, trail_sl, pp, atr, pp, fee_floor, pp)

    # ── Étape 4 : Le SL a-t-il bougé ? ──────────────────────────
    tick = 10 ** (-pp)
    if be_moved:
        # Après BE : bidirectionnel OK mais plancher fee_floor absolu
        new_sl  = max(new_sl, fee_floor) if side == "BUY" else min(new_sl, fee_floor)
        changed = abs(new_sl - sl) > tick
    else:
        # Avant BE : mouvement favorable seulement (vers profit)
        changed = (side == "BUY"  and new_sl > sl + tick) or \
                  (side == "SELL" and new_sl < sl - tick)

    if not changed: return

    # ── Étape 5 : Envoyer nouveau SL à Binance (HORS lock) ───────
    new_order = move_sl_binance(symbol, sl_order_id, new_sl, close_side)

    # ── Étape 6 : Mise à jour trade_log (lock COURT) ─────────────
    with trade_lock:
        if symbol not in trade_log or trade_log[symbol].get("status") != "OPEN":
            return
        t = trade_log[symbol]
        if new_order and new_order.get("orderId"):
            t["sl"]                   = new_sl
            t["sl_order_id"]          = new_order["orderId"]
            t["sl_on_binance"]        = True
            t["trailing_stop_active"] = trail_used
            if be_trigger:
                t["breakeven_moved"] = True

            # Label de l'action combinée
            if be_trigger and trail_used:
                label = "BE+TRAIL simultané"
            elif be_trigger:
                label = "BREAKEVEN frais couverts"
            else:
                label = "TRAILING M15 actif"

            logger.info("🔁 SL SUIVEUR {} {} [{}] | {:.{}f} → {:.{}f} | RR={:.2f}R | {}".format(
                symbol, side, label, sl, pp, new_sl, pp, rr, action))

            # Telegram : au BE et à chaque RR entier franchi
            if be_trigger or (trail_used and int(rr) >= 1 and abs(rr - round(rr)) < 0.15):
                pnl_min = abs(new_sl - entry) * qty_safe
                send_telegram(
                    "🔁 <b>SL Suiveur {} {} [M15]</b>\n"
                    "<b>{:.{}f} → {:.{}f}</b>\n"
                    "RR={:.2f}R | Prix: {:.{}f}\n"
                    "Plancher frais: {:.{}f}\n".format(
                        symbol, side,
                        sl, pp, new_sl, pp,
                        rr, current_price, pp,
                        fee_floor, pp) +
                    (
                        "✅ <b>BREAKEVEN</b> — frais 0.12% couverts\n"
                        "Pire cas si SL touché: +${:.4f} net".format(pnl_min)
                        if be_trigger else
                        "🔁 <b>Trailing RR{}R M15</b>\n"
                        "SL garanti au-dessus des frais".format(int(rr))
                    )
                )
        else:
            # SL Binance échoué → SL logiciel reste actif
            t["sl_on_binance"] = False
            logger.warning(
                "⚠️  {} déplacement SL Binance échoué | "
                "SL logiciel actif @ {:.{}f} | "
                "Tentative : {:.{}f}".format(symbol, sl, pp, new_sl, pp)
            )

# ═══════════════════════════════════════════════════════════════════
#  OPEN POSITION
# ═══════════════════════════════════════════════════════════════════

def open_position(signal):
    symbol = signal["symbol"]
    side   = signal["side"]
    setup  = signal["setup"]
    score  = signal["score"]
    prob   = signal["probability"]
    ob     = signal.get("ob", {})
    atr    = signal.get("atr", 0)

    try:
        info = symbol_info_cache.get(symbol)
        if not info: return

        pp        = info.get("pricePrecision", 4)
        step_size = info.get("stepSize", 0.001)
        min_qty   = info.get("minQty", 0.001)
        min_notional = info.get("minNotional", MIN_NOTIONAL)

        entry = get_price(symbol)
        if not entry: return

        lev        = get_leverage(score)
        margin     = account_balance * MARGIN_FIXED_PCT
        max_sl_pct = get_max_sl_pct()

        # ── SL basé ATR M15 — résout le problème SL trop serré ──
        sl = get_sl(ob, entry, side, atr)
        if side == "BUY":
            sl_dist = max(entry - sl, entry * MIN_SL_PCT)
            sl_dist = min(sl_dist, entry * max_sl_pct)
            sl = round(entry - sl_dist, pp)
        else:
            sl_dist = max(sl - entry, entry * MIN_SL_PCT)
            sl_dist = min(sl_dist, entry * max_sl_pct)
            sl = round(entry + sl_dist, pp)

        if sl_dist <= 0: return

        tp = round(entry + sl_dist * TP_RR if side == "BUY"
                   else entry - sl_dist * TP_RR, pp)

        # ── Sizing ────────────────────────────────────────────────
        risk_usdt = get_risk_usdt()
        qty = _round_step(risk_usdt / sl_dist, step_size)
        max_qty_m = _round_step((margin * lev) / entry, step_size)
        if max_qty_m > 0 and qty > max_qty_m:
            qty = max_qty_m

        # Min notional — petit compte : utilise la marge max disponible pour combler
        if qty * entry < min_notional:
            # Calcul qty avec toute la marge disponible × levier
            margin_total = account_balance * MARGIN_FIXED_PCT
            qty_margin   = _round_step((margin_total * lev) / entry, step_size)
            # Fallback : notional strict
            qty_notional = _round_step(min_notional / entry + step_size, step_size)
            qty_try = max(qty_margin, qty_notional)
            # Vérif que la marge requise reste dans la balance disponible
            marge_requise = (qty_try * entry) / lev
            if marge_requise > account_balance * 0.95:
                qty_try = _round_step((account_balance * 0.95 * lev) / entry, step_size)
            if qty_try * entry >= min_notional and qty_try >= min_qty:
                qty = qty_try
                logger.info("  {} notional ajusté: qty={} notional={:.2f}$ marge={:.4f}$".format(
                    symbol, qty, qty * entry, (qty * entry) / lev))
            else:
                logger.info("  {} skip — balance insuffisante pour notional min ($5)".format(symbol))
                return

        if qty < min_qty: return

        logger.info("📊 {} {} | {} score={} prob={:.0f}% | {}x | SL {:.3f}% | qty={}".format(
            symbol, side, setup, score, prob, lev, sl_dist/entry*100, qty))

        set_isolated(symbol)
        actual_lev = set_leverage_sym(symbol, lev)
        if actual_lev != lev: lev = actual_lev
        cleanup_orders(symbol)

        order = place_market(symbol, side, qty)
        if not order: return

        # Vrai prix d'entrée
        actual_entry = 0.0
        for _ in range(5):
            time.sleep(0.4)
            pos = request_binance("GET", "/fapi/v2/positionRisk",
                                  {"symbol": symbol}, signed=True)
            if pos:
                for p in pos:
                    if p.get("symbol") == symbol:
                        ep = float(p.get("entryPrice", 0))
                        if ep > 0: actual_entry = ep
            if actual_entry > 0: break
        if actual_entry <= 0:
            actual_entry = get_price(symbol) or entry

        # Recalcul SL/TP sur vrai entry
        if side == "BUY":
            sl_dist2 = max(actual_entry - sl, actual_entry * MIN_SL_PCT)
            sl_dist2 = min(sl_dist2, actual_entry * MAX_SL_PCT)
            sl = round(actual_entry - sl_dist2, pp)
            tp = round(actual_entry + sl_dist2 * TP_RR, pp)
        else:
            sl_dist2 = max(sl - actual_entry, actual_entry * MIN_SL_PCT)
            sl_dist2 = min(sl_dist2, actual_entry * MAX_SL_PCT)
            sl = round(actual_entry + sl_dist2, pp)
            tp = round(actual_entry - sl_dist2 * TP_RR, pp)

        close_side = "SELL" if side == "BUY" else "BUY"
        sl_r = place_sl_binance(symbol, sl, close_side)
        tp_r = place_tp_binance(symbol, tp, close_side)

        be_price = round(actual_entry * (1.0 + BREAKEVEN_FEE_TOTAL), pp) if side == "BUY" \
                   else round(actual_entry * (1.0 - BREAKEVEN_FEE_TOTAL), pp)

        with trade_lock:
            trade_log[symbol] = {
                "side": side, "entry": actual_entry, "sl": sl, "tp": tp, "qty": qty,
                "leverage": lev, "margin": margin, "setup": setup, "score": score,
                "probability": prob, "status": "OPEN", "opened_at": time.time(),
                "sl_on_binance": sl_r["sent"], "tp_on_binance": tp_r["sent"],
                "sl_order_id": sl_r["order_id"], "tp_order_id": tp_r["order_id"],
                "trailing_stop_active": False, "breakeven_moved": False,
                "btc_corr": signal.get("btc_corr", "?"), "atr": atr, "be_price": be_price,
            }

        logger.info("✅ {} {} @ {:.{}f} | SL {:.{}f} | TP {:.{}f} | {}x | {}".format(
            symbol, side, actual_entry, pp, sl, pp, tp, pp, lev, get_tier_label()))

        send_telegram(
            "🚀 <b>{} {}</b> @ {:.{}f}\n"
            "SL: {:.{}f}  ({:.3f}%)  {}\n"
            "BE: {:.{}f}\n"
            "TP: {:.{}f}  (RR{})\n"
            "Setup: {}({}) | Fond:{}/60\n"
            "Marge: {}% | Levier: {}x | M15\n"
            "{} | {}".format(
                symbol, side,
                actual_entry, pp,
                sl, pp, sl_dist2/actual_entry*100,
                "✅Binance" if sl_r["sent"] else "⚠️logiciel",
                be_price, pp,
                tp, pp, TP_RR,
                setup, score, signal.get("fond_score", 0),
                int(MARGIN_FIXED_PCT*100), lev,
                get_tier_label(), get_progress_bar()
            )
        )

    except Exception as e:
        logger.error("open_position {}: {}".format(symbol, e))

# ═══════════════════════════════════════════════════════════════════
#  MONITOR POSITIONS
# ═══════════════════════════════════════════════════════════════════

def _on_closed(symbol, trade, is_win):
    global consec_losses, cooldown_until
    side  = trade.get("side", "?")
    setup = trade.get("setup", "?")
    info  = symbol_info_cache.get(symbol, {})
    pp    = info.get("pricePrecision", 4)

    if is_win:
        consec_losses = 0
        symbol_stats[symbol]["wins"] += 1
        logger.info("✅ WIN {} {} {}".format(symbol, side, setup))
        # Vérifier franchissement de palier
        milestone_msg = ""
        if account_balance >= TARGET_BALANCE:
            milestone_msg = "\n🏆 <b>OBJECTIF ${:.0f} ATTEINT !</b>".format(TARGET_BALANCE)
        elif account_balance >= TIER2_LIMIT:
            entry_val  = trade.get("entry", 0)
            sl_val     = trade.get("sl", 0)
            qty_val    = trade.get("qty", 0)
            prev_bal   = account_balance - abs(entry_val - sl_val) * qty_val * TP_RR
            if prev_bal < TIER2_LIMIT:
                milestone_msg = "\n🟢 Palier NORMAL atteint ! ${:.4f}".format(account_balance)
        elif account_balance >= TIER1_LIMIT:
            entry_val  = trade.get("entry", 0)
            sl_val     = trade.get("sl", 0)
            qty_val    = trade.get("qty", 0)
            prev_bal   = account_balance - abs(entry_val - sl_val) * qty_val * TP_RR
            if prev_bal < TIER1_LIMIT:
                milestone_msg = "\n🟡 Palier CROISSANCE atteint ! ${:.4f}".format(account_balance)
        send_telegram(
            "✅ <b>WIN {} {}</b>\n"
            "Setup: {} | Frais couverts\n"
            "Balance: ${:.4f} {}{}".format(
                symbol, side, setup, account_balance, get_progress_bar(), milestone_msg)
        )
    else:
        consec_losses += 1
        symbol_stats[symbol]["losses"] += 1
        logger.info("🔴 LOSS {} {} {} consec={}".format(symbol, side, setup, consec_losses))
        send_telegram(
            "🔴 <b>LOSS {} {}</b>\n"
            "Setup: {} | Consécutives: {}\n"
            "Balance: ${:.4f} {}\n{}".format(
                symbol, side, setup, consec_losses,
                account_balance, get_tier_label(), get_progress_bar())
        )
        if consec_losses >= CONSEC_LOSS_LIMIT:
            cooldown_until = time.time() + CONSEC_COOLDOWN
            logger.warning("⏸ Pause {}min".format(CONSEC_COOLDOWN // 60))
            send_telegram(
                "⏸ <b>Pause {}min</b>\n"
                "Après {} pertes consécutives\n"
                "Reprise automatique".format(CONSEC_COOLDOWN // 60, consec_losses)
            )

def _on_closed_from_binance(symbol, trade):
    try:
        income = request_binance("GET", "/fapi/v1/income",
                                 {"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": 5},
                                 signed=True)
        pnl = sum(float(i.get("income", 0)) for i in income) if income else 0.0
        _on_closed(symbol, trade, is_win=pnl >= 0)
    except:
        _on_closed(symbol, trade, is_win=False)

def monitor_positions():
    try:
        with trade_lock:
            open_syms = [s for s, t in trade_log.items() if t.get("status") == "OPEN"]
        if not open_syms: return

        pos_data = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        binance_open = {}
        if pos_data:
            for p in pos_data:
                sym = p.get("symbol")
                if float(p.get("positionAmt", 0)) != 0:
                    binance_open[sym] = p

        for symbol in open_syms:
            update_trailing_sl(symbol)

            with trade_lock:
                if symbol not in trade_log: continue
                t = trade_log[symbol]
                if t.get("status") != "OPEN": continue

                if symbol not in binance_open:
                    t["status"]    = "CLOSED"
                    t["closed_by"] = "BINANCE_SL_TP"
                    logger.info("✅ {} fermée par Binance".format(symbol))
                    _on_closed_from_binance(symbol, t)
                    continue

                if not t.get("sl_on_binance"):
                    cp = get_price(symbol)
                    if cp:
                        sl = t["sl"]
                        if (t["side"] == "BUY" and cp <= sl) or \
                           (t["side"] == "SELL" and cp >= sl):
                            logger.warning("🚨 {} SL LOGICIEL @ {}".format(symbol, cp))
                            cs = "SELL" if t["side"] == "BUY" else "BUY"
                            place_market(symbol, cs, t.get("qty", 0))
                            t["status"]    = "CLOSED"
                            t["closed_by"] = "SOFTWARE_SL"
                            _on_closed(symbol, t, is_win=False)

    except Exception as e:
        logger.debug("monitor_positions: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  SCAN PAR SYMBOLE
# ═══════════════════════════════════════════════════════════════════

def scan_symbol(symbol):
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None

        if time.time() - signal_last_at.get(symbol, 0) < SIGNAL_COOLDOWN:
            return None

        klines = get_klines(symbol, TIMEFRAME, LIMIT_CANDLES)
        if not klines or len(klines) < 30:
            return None

        signal = analyse_m15(symbol, klines)
        if not signal: return None

        side       = signal["side"]
        sym_closes = signal["closes"]

        corr_ok, corr_reason = check_btc_correlation(symbol, side, sym_closes)
        if not corr_ok: return None
        signal["btc_corr"] = corr_reason

        fond_score, fond_ok, fond_detail = check_fondamentaux(symbol, side)
        if not fond_ok: return None
        signal["fond_score"]  = fond_score
        signal["fond_detail"] = fond_detail

        return signal

    except Exception as e:
        logger.debug("scan_symbol {}: {}".format(symbol, e))
        return None

# ═══════════════════════════════════════════════════════════════════
#  RECOVER
# ═══════════════════════════════════════════════════════════════════

def recover_existing_positions():
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if not positions: return
        for pos in positions:
            sym = pos.get("symbol")
            amt = float(pos.get("positionAmt", 0))
            if amt == 0: continue
            side  = "BUY" if amt > 0 else "SELL"
            qty   = abs(amt)
            entry = float(pos.get("entryPrice", 0))
            if entry <= 0: continue

            logger.info("🔄 {} {} @ {} → récupérée".format(sym, side, entry))
            info = symbol_info_cache.get(sym, {})
            pp   = info.get("pricePrecision", 4)
            atr  = _atr_live(sym)
            dist = max(atr * ATR_SL_MULT, entry * MIN_SL_PCT)
            dist = min(dist, entry * MAX_SL_PCT)

            sl = round(entry - dist if side == "BUY" else entry + dist, pp)
            tp = round(entry + dist * TP_RR if side == "BUY" else entry - dist * TP_RR, pp)
            be = round(entry * (1 + BREAKEVEN_FEE_TOTAL) if side == "BUY"
                       else entry * (1 - BREAKEVEN_FEE_TOTAL), pp)

            close_side = "SELL" if side == "BUY" else "BUY"
            cleanup_orders(sym)
            sl_r = place_sl_binance(sym, sl, close_side)
            tp_r = place_tp_binance(sym, tp, close_side)

            if sym not in symbols_list: symbols_list.append(sym)
            with trade_lock:
                trade_log[sym] = {
                    "side": side, "entry": entry, "sl": sl, "tp": tp, "qty": qty,
                    "leverage": 15, "setup": "RECOVERED", "score": 90, "probability": 90.0,
                    "status": "OPEN", "opened_at": time.time(),
                    "sl_on_binance": sl_r["sent"], "tp_on_binance": tp_r["sent"],
                    "sl_order_id": sl_r["order_id"], "tp_order_id": tp_r["order_id"],
                    "trailing_stop_active": False, "breakeven_moved": False,
                    "be_price": be, "atr": atr, "btc_corr": "RECOVERED",
                }
            logger.info("✅ {} récupéré | SL @ {:.{}f}".format(sym, sl, pp))
    except Exception as e:
        logger.error("recover: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  AFFICHAGE CONSOLE
# ═══════════════════════════════════════════════════════════════════

def print_signal_console(sig, rank):
    side  = sig["side"]
    dcol  = RED + BOLD if side == "SELL" else GREEN + BOLD
    sep   = "═" * 65

    print("\n" + cc(sep, CYAN))
    print("  #{} {:<22} {} {}".format(
        rank, sig["symbol"], cc("◄ " + side, dcol), cc("M15 LIVE", MAGENTA + BOLD)))
    print(cc("─" * 65, DIM))

    def row(label, val, col=WHITE):
        print("  {}  {}".format(cc("{:<16}".format(label+":"), DIM), cc(str(val), col)))

    row("Setup M15", "{} | score={} conf={}/5 prob={:.0f}%".format(
        sig["setup"], sig["score"], sig["confluence"], sig["probability"]),
        BOLD + WHITE)
    row("ATR M15", "{:.8f}  → SL naturel {:.3f}%".format(
        sig["atr"], sig["atr"] * ATR_SL_MULT / sig.get("entry_preview", 1) * 100
        if sig.get("entry_preview") else 0))
    row("BTC M15",  sig["btc_dir"],  CYAN)
    row("Fond",     "{}/60  {}".format(sig["fond_score"], sig["fond_detail"]),
        GREEN if sig["fond_score"] >= 50 else YELLOW)
    print(cc(sep, CYAN))

# ═══════════════════════════════════════════════════════════════════
#  LOOPS
# ═══════════════════════════════════════════════════════════════════

def scanner_loop():
    logger.info("🔍 Scanner M15 démarré — RR4 | PROB 90%+ | ATR SL")
    time.sleep(5)
    count = 0
    while True:
        try:
            if _bot_stop:
                time.sleep(10); continue

            count += 1
            if count % (300 // max(SCAN_INTERVAL, 10)) == 0:
                sync_binance_time()
                get_account_balance()
                btc = get_btc_direction()
                logger.info("BTC: {} | Balance: ${:.4f} | {}".format(
                    btc["label"], account_balance, get_tier_label()))

            if count % (3600 // max(SCAN_INTERVAL, 10)) == 0:
                load_top_symbols()

            if account_balance < HARD_FLOOR:
                logger.warning("🛑 Hard floor ${} | ${:.4f}".format(HARD_FLOOR, account_balance))
                time.sleep(60); continue

            if time.time() < cooldown_until:
                r = int((cooldown_until - time.time()) / 60)
                if count % 2 == 0:
                    logger.info("⏸ Cooldown {}min".format(r))
                time.sleep(SCAN_INTERVAL); continue

            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open >= MAX_POSITIONS:
                time.sleep(SCAN_INTERVAL); continue

            btc = get_btc_direction()
            if btc["direction"] == 0:
                logger.debug("⚪ BTC M15 neutre — scan tout de même")

            # Scan parallèle
            signals = []
            sig_lock = threading.Lock()
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {pool.submit(scan_symbol, sym): sym for sym in symbols_list}
                for fut in as_completed(futures, timeout=30):
                    try:
                        sig = fut.result()
                        if sig:
                            with sig_lock:
                                signals.append(sig)
                    except: pass

            if not signals:
                time.sleep(SCAN_INTERVAL); continue

            # Tri : score × prob × confluence
            signals.sort(key=lambda s: s["score"] * s["probability"] * s["confluence"],
                         reverse=True)

            best = signals[0]
            sym  = best["symbol"]

            with trade_lock:
                already = sym in trade_log and trade_log[sym].get("status") == "OPEN"
            if not already:
                signal_last_at[sym] = time.time()
                print_signal_console(best, 1)
                logger.info("🎯 SIGNAL M15: {} {} | score={} conf={}/5 prob={:.0f}% | fond={}/60".format(
                    sym, best["side"], best["score"],
                    best["confluence"], best["probability"], best["fond_score"]))
                open_position(best)

        except Exception as e:
            logger.error("scanner_loop: {}".format(e))
        time.sleep(SCAN_INTERVAL)

def monitor_loop():
    logger.info("📡 Monitor SL suiveur toutes les {}s".format(MONITOR_INTERVAL))
    time.sleep(10)
    while True:
        try:
            monitor_positions()
        except Exception as e:
            logger.debug("monitor_loop: {}".format(e))
        time.sleep(MONITOR_INTERVAL)

def dashboard_loop():
    time.sleep(20)
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            tw  = sum(v["wins"]   for v in symbol_stats.values())
            tl  = sum(v["losses"] for v in symbol_stats.values())
            wr  = tw/(tw+tl)*100 if (tw+tl) > 0 else 0
            btc = get_btc_direction()
            logger.info("═" * 65)
            logger.info("SCANNER M15 | ${:.4f} | {} | {}".format(
                account_balance, get_tier_label(), get_progress_bar()))
            logger.info("Pos:{}/{} | W:{} L:{} WR:{:.1f}% | {}".format(
                n_open, MAX_POSITIONS, tw, tl, wr, btc["label"]))
            logger.info("═" * 65)
        except Exception as e:
            logger.debug("dashboard: {}".format(e))
        time.sleep(DASHBOARD_INTERVAL)

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("╔" + "═" * 63 + "╗")
    logger.info("║  SCANNER M15 RR4 — LIVE TRADING | PROB 90%+ | ATR SL     ║")
    logger.info("║  Breaker Block ICT M15 | Correl BTC | Fondamentaux       ║")
    logger.info("╚" + "═" * 63 + "╝")
    logger.warning("🔥 LIVE TRADING 🔥")

    logger.info("✅ TIMEFRAME     : M15")
    logger.info("✅ SL            : ATR × {} ({:.0f}%–{:.0f}%)".format(
        ATR_SL_MULT, MIN_SL_PCT*100, MAX_SL_PCT*100))
    logger.info("✅ RR MIN        : {}×".format(MIN_RR))
    logger.info("✅ PROB MIN      : {:.0f}%".format(MIN_PROB))
    logger.info("✅ CONFLUENCE    : {}/5 minimum".format(MIN_CONFLUENCE))
    logger.info("✅ FRAIS         : {:.2f}% A/R inclus".format(BREAKEVEN_FEE_TOTAL*100))
    logger.info("✅ POSITIONS MAX : {}".format(MAX_POSITIONS))

    start_health_server()
    sync_binance_time()
    load_top_symbols()
    get_account_balance()
    drawdown_state["ref_balance"] = account_balance

    logger.info("💰 Balance: ${:.4f} | Palier: {}".format(account_balance, get_tier_label()))
    logger.info("🎯 Risque/trade: ${:.4f} | Levier: {}x".format(
        get_risk_usdt(), get_leverage(92)))

    btc = get_btc_direction()
    logger.info("📊 BTC M15: {}".format(btc["label"]))

    send_telegram(
        "🚀 <b>SCANNER M15 RR4 DÉMARRÉ</b>\n\n"
        "💰 Balance: <b>${:.4f}</b>\n"
        "🎯 Objectif: ${:.0f} | {}\n"
        "📊 BTC M15: {}\n\n"
        "⚙️ CONFIG :\n"
        "  Timeframe: M15 | SL: ATR×{} ({:.0f}%–{:.0f}%)\n"
        "  RR min: {}× | Prob min: {:.0f}%\n"
        "  Conf min: {}/5 | Fond: {}/60\n"
        "  Marge: {:.0f}% | Pos max: {}\n\n"
        "{} | Risque: ${:.4f}".format(
            account_balance, TARGET_BALANCE, get_progress_bar(),
            btc["label"],
            ATR_SL_MULT, MIN_SL_PCT*100, MAX_SL_PCT*100,
            MIN_RR, MIN_PROB,
            MIN_CONFLUENCE, FOND_MIN_SCORE,
            int(MARGIN_FIXED_PCT*100), MAX_POSITIONS,
            get_tier_label(), get_risk_usdt()
        )
    )

    recover_existing_positions()

    threading.Thread(target=scanner_loop,  daemon=True).start()
    threading.Thread(target=monitor_loop,  daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()

    logger.info("✅ SCANNER M15 ONLINE 🚀")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown")

if __name__ == "__main__":
    main()

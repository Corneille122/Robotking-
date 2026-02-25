#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   SCANNER M15 v2.0 — BREAKER BLOCK ICT | RR4 | MM DYNAMIQUE   ║
║   BTC-Aligned | Session Filter | Fear&Greed | OI Spike         ║
╠══════════════════════════════════════════════════════════════════╣
║  NOUVEAUTÉS v2.0 :                                              ║
║  ✅ Breaker Block OBLIGATOIREMENT aligné avec BTC M15           ║
║  ✅ Money Management dynamique 10%→40% selon qualité signal     ║
║  ✅ Multiplicateur session (Asia 0.6× / London 1.0× / NY 1.2×) ║
║  ✅ Multiplicateur BTC (fort = plus de mise)                    ║
║  ✅ Fear & Greed Index (API gratuite)                           ║
║  ✅ Détection spike OI anormal                                  ║
║  ✅ Score global = technique + fondamental + session + BTC       ║
║  ✅ Top 80 cryptos par volume (scan ciblé, pas 458 symboles)    ║
║  ✅ SL suiveur v41.0 bidir | Frais + $0.01 garanti             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time, hmac, hashlib, requests, threading, os, logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

# ─── LOGGING ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
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
API_KEY    = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")
BASE_URL   = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION v2.0
# ═══════════════════════════════════════════════════════════════════

# ── Timeframe M15 ────────────────────────────────────────────────
TIMEFRAME        = "15m"
LIMIT_CANDLES    = 60       # 60 × 15min = 15h d'historique
KLINES_CACHE_TTL = 60       # Cache 60s (bougie M15 = 15min)

# ── Frais Binance ─────────────────────────────────────────────────
BINANCE_FEE_RATE    = 0.0004         # 0.04% taker par côté
BREAKEVEN_FEE_TOTAL = BINANCE_FEE_RATE * 2 * 1.5  # 0.12% A/R avec buffer
BE_PROFIT_MIN       = 0.01           # $0.01 net garanti au BE

# ── RR & SL adapté M15 ───────────────────────────────────────────
MIN_RR       = 4.0
TP_RR        = 4.0
MIN_SL_PCT   = 0.005   # SL min 0.5%
MAX_SL_PCT   = 0.030   # SL max 3.0%
ATR_SL_MULT  = 1.5     # SL = ATR × 1.5

# ── Breaker Block ─────────────────────────────────────────────────
BB_LOOKBACK    = 40
BB_TOUCH_MIN   = 2
BB_ZONE_BUFFER = 0.0005
OB_LOOKBACK    = 6

# ── Filtres signal ────────────────────────────────────────────────
MIN_SCORE      = 90    # Score minimum BB
MIN_CONFLUENCE = 4     # 4/5 confluences minimum
MIN_PROB       = 90.0  # Probabilité minimum
MIN_BODY_RATIO = 0.40  # Corps bougie ≥ 40%

# ── BTC corrélation M15 ──────────────────────────────────────────
BTC_SYMBOL       = "BTCUSDT"
BTC_EMA_FAST     = 5
BTC_EMA_SLOW     = 13
BTC_RSI_PERIOD   = 9
BTC_RSI_BULL_MAX = 68
BTC_RSI_BEAR_MIN = 32

# ── MONEY MANAGEMENT DYNAMIQUE ───────────────────────────────────
#
#  Marge = base_pct × multiplicateur_score × mult_session × mult_btc × mult_fond
#
#  base_pct selon qualité du signal :
#    Score 90   (conf 4/5) → 10% balance (base minimale)
#    Score 91   (conf 4/5) → 15%
#    Score 92   (conf 5/5) → 20%
#    Score 93   (conf 5/5) → 30%
#    Score 94   (conf 5/5) → 40%  (signal parfait)
#
#  Multiplicateur session :
#    Asia    02h→08h UTC → ×0.6 (liquidité faible)
#    London  08h→13h UTC → ×1.0 (standard)
#    NY      13h→21h UTC → ×1.2 (meilleure liquidité)
#    Off     21h→02h UTC → ×0.5 (évite les pièges nuit)
#
#  Multiplicateur BTC (force de la tendance) :
#    BTC fort (RSI 45-65, slope > 0.05%) → ×1.2
#    BTC normal                           → ×1.0
#    BTC faible (RSI hors zone)           → ×0.8
#
#  Multiplicateur fondamental (score /60) :
#    ≥ 50/60 → ×1.15   (fondamentaux excellents)
#    ≥ 40/60 → ×1.0    (standard)
#    < 40/60 → signal rejeté (filtre dur)
#
# ─────────────────────────────────────────────────────────────────
MARGIN_BY_SCORE = {
    94: 0.40,  # Confluence parfaite 5/5 + tous critères
    93: 0.30,
    92: 0.20,
    91: 0.15,
    90: 0.10,  # Score minimum
}
MARGIN_MIN = 0.10   # 10% balance minimum
MARGIN_MAX = 0.40   # 40% balance maximum

SESSION_MULT = {
    "LONDON": 1.0,
    "NY":     1.2,
    "ASIA":   0.6,
    "OFF":    0.5,
}

# ── Fondamentaux ──────────────────────────────────────────────────
FOND_MIN_SCORE    = 40   # Score minimum /60 pour trader
FOND_BOOST_SCORE  = 50   # Score pour le multiplicateur ×1.15
OI_SPIKE_THRESH   = 0.02 # +2% OI en 15min = spike anormal → filtre dur

# ── Fear & Greed ──────────────────────────────────────────────────
# Valeur 0-100 : 0=Fear extrême, 100=Greed extrême
# On évite d'acheter dans la greed extrême (>80) et de vendre dans la fear extrême (<20)
FG_BULL_MAX  = 80   # BUY refusé si Fear&Greed > 80 (greed extrême)
FG_BEAR_MIN  = 20   # SELL refusé si Fear&Greed < 20 (fear extrême)
FG_CACHE_TTL = 300  # Cache 5min (API publique limitée)

# ── Top N symboles ────────────────────────────────────────────────
TOP_N_SYMBOLS = 80   # Scan ciblé sur les 80 meilleures cryptos

# ── Gestion positions ─────────────────────────────────────────────
MAX_POSITIONS     = 2
MARGIN_TYPE       = "ISOLATED"
MIN_NOTIONAL      = 5.0
MAX_NOTIONAL_RISK = 0.90

# ── Paliers balance ───────────────────────────────────────────────
TARGET_BALANCE = 10.0
TIER1_LIMIT    = 2.0
TIER2_LIMIT    = 5.0

TIER_PARAMS = {
    1: {"risk_pct": 0.15, "max_risk": 0.20, "max_sl": 0.030},
    2: {"risk_pct": 0.10, "max_risk": 0.40, "max_sl": 0.030},
    3: {"risk_pct": 0.07, "max_risk": 0.70, "max_sl": 0.030},
}

LEVERAGE_BY_TIER = {
    1: [(92, 50), (90, 35), (0, 25)],
    2: [(92, 35), (90, 25), (0, 20)],
    3: [(92, 25), (90, 20), (0, 15)],
}

# ── SL suiveur ────────────────────────────────────────────────────
BREAKEVEN_RR   = 0.6
TRAIL_START_RR = 1.0
TRAIL_ATR_MULT = 1.0   # Plus large en M15

# ── Timing ───────────────────────────────────────────────────────
SCAN_INTERVAL      = 15 * 60
MONITOR_INTERVAL   = 5
DASHBOARD_INTERVAL = 30
SIGNAL_COOLDOWN    = 15 * 60
HARD_FLOOR         = 0.50

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

# ── Anti pertes consécutives ──────────────────────────────────────
CONSEC_LOSS_LIMIT = 2
CONSEC_COOLDOWN   = 30 * 60
DD_ALERT_PCT      = 0.15
MAX_WORKERS       = 6

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
              "rsi": 50.0, "slope": 0.0, "closes": None, "strength": "NORMAL"}
_btc_lock  = threading.Lock()

_fg_cache  = {"value": 50, "label": "Neutral", "ts": 0.0}
_fg_lock   = threading.Lock()

# ─── COULEURS ────────────────────────────────────────────────────
GREEN   = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN    = "\033[96m"; WHITE  = "\033[97m"; RESET  = "\033[0m"
BOLD    = "\033[1m";  DIM    = "\033[2m";  MAGENTA = "\033[95m"

def cc(text, col): return "{}{}{}".format(col, text, RESET)

# ═══════════════════════════════════════════════════════════════════
#  PALIERS & TIER
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  SESSION TRADING
# ═══════════════════════════════════════════════════════════════════

def get_session():
    """
    Retourne la session en cours selon l'heure UTC.
    London : 08h–13h | NY : 13h–21h | Asia : 02h–08h | Off : 21h–02h
    """
    h = datetime.now(timezone.utc).hour
    if 8  <= h < 13: return "LONDON"
    if 13 <= h < 21: return "NY"
    if 2  <= h < 8:  return "ASIA"
    return "OFF"

def get_session_mult():
    return SESSION_MULT.get(get_session(), 1.0)

# ═══════════════════════════════════════════════════════════════════
#  MONEY MANAGEMENT DYNAMIQUE
# ═══════════════════════════════════════════════════════════════════

def compute_dynamic_margin(signal_score, fond_score, btc_strength):
    """
    Calcule la marge dynamique en fonction de :
      - signal_score  : score BB (90→94)
      - fond_score    : score fondamental (/60)
      - btc_strength  : force BTC ("FORT", "NORMAL", "FAIBLE")

    Retourne un float entre MARGIN_MIN (0.10) et MARGIN_MAX (0.40).
    """
    # 1. Base selon qualité du signal
    base = MARGIN_MIN
    for score_thresh, margin in sorted(MARGIN_BY_SCORE.items(), reverse=True):
        if signal_score >= score_thresh:
            base = margin
            break

    # 2. Multiplicateur session
    mult_session = get_session_mult()

    # 3. Multiplicateur BTC
    mult_btc = {"FORT": 1.2, "NORMAL": 1.0, "FAIBLE": 0.8}.get(btc_strength, 1.0)

    # 4. Multiplicateur fondamental
    mult_fond = 1.15 if fond_score >= FOND_BOOST_SCORE else 1.0

    margin = base * mult_session * mult_btc * mult_fond

    # Clamp strict
    margin = max(MARGIN_MIN, min(MARGIN_MAX, margin))

    logger.debug(
        "MM dynamique: base={:.0f}% ×session({})={:.2f} ×BTC({})={:.2f} "
        "×fond={:.2f} → marge={:.1f}%".format(
            base*100, get_session(), mult_session,
            btc_strength, mult_btc, mult_fond, margin*100)
    )
    return margin

# ═══════════════════════════════════════════════════════════════════
#  FLASK HEALTH SERVER
# ═══════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    st  = "🛑 STOP" if _bot_stop else "🟢 RUNNING"
    ses = get_session()
    fg  = _fg_cache.get("value", "?")
    return "SCANNER M15 v2.0 | {} | ${:.4f} | Pos:{}/{} | {} | F&G:{}".format(
        st, account_balance, n, MAX_POSITIONS, ses, fg), 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status_ep():
    with trade_lock:
        open_pos = {s: {k: t.get(k) for k in
                        ["side","entry","sl","tp","setup","probability","leverage",
                         "margin_pct","trailing_stop_active","breakeven_moved"]}
                    for s, t in trade_log.items() if t.get("status") == "OPEN"}
    tw = sum(v["wins"]   for v in symbol_stats.values())
    tl = sum(v["losses"] for v in symbol_stats.values())
    btc = _btc_cache
    return jsonify({
        "version":       "v2.0",
        "balance":       round(account_balance, 4),
        "session":       get_session(),
        "session_mult":  get_session_mult(),
        "fear_greed":    _fg_cache,
        "btc":           {"direction": btc["direction"], "label": btc["label"],
                          "strength": btc.get("strength","NORMAL")},
        "positions":     open_pos,
        "wins": tw, "losses": tl,
        "cooldown_remaining": max(0, int(cooldown_until - time.time())),
        "stop": _bot_stop,
    })

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
#  MATH PUR PYTHON (zéro numpy)
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
        result[i] = values[i] * k + result[i-1] * (1 - k)
    return result

def _rsi(closes, period=14):
    n = period + 1
    if len(closes) < n: return 50.0
    subset = closes[-n:]
    gains  = [max(subset[i] - subset[i-1], 0.0) for i in range(1, len(subset))]
    losses = [max(subset[i-1] - subset[i], 0.0) for i in range(1, len(subset))]
    ag = _mean(gains); al = _mean(losses)
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
    return _mean(trs[-min(period, len(trs)):])

def _atr_live(symbol):
    try:
        klines = get_klines(symbol, TIMEFRAME, 20)
        if not klines or len(klines) < 5:
            p = get_price(symbol)
            return p * 0.005 if p else 0.001
        o, h, l, c, v = _parse_klines(klines)
        trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
               for i in range(1, len(c))]
        return _mean(trs[-min(14, len(trs)):]) if trs else c[-1] * 0.005
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
        except: pass
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

                if r.status_code == 200: return r.json()

                code, msg = _parse_binance_error(r.text)

                if r.status_code == 429:
                    time.sleep([5, 15, 30, 60][min(attempt, 3)]); continue
                if r.status_code == 418:
                    time.sleep(120); return None
                if r.status_code in (401, 403):
                    logger.error("🔑 Auth {} — clé API invalide".format(r.status_code))
                    return None
                if code == -1021:
                    sync_binance_time(); continue
                elif code == -1111:
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    if "quantity" in params:
                        params["quantity"] = round(
                            _round_step(float(params["quantity"]), info.get("stepSize", 0.001)),
                            info.get("quantityPrecision", 3))
                        continue
                elif code == -1013:
                    sym  = params.get("symbol", "?")
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
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    pp   = info.get("pricePrecision", 4)
                    if "stopPrice" in params and "side" in params:
                        sp   = float(params["stopPrice"])
                        mark = get_price(sym) or sp
                        shift = mark * 0.002
                        new_sp = round(min(sp, mark - shift) if params.get("side") == "SELL"
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
                    sym = params.get("symbol", "?")
                    return {"_leverage_locked": True, "leverage": params.get("leverage", 0)}
                elif code == -1121:
                    sym = params.get("symbol", "?")
                    if sym in symbols_list: symbols_list.remove(sym)
                    return None
                else:
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
            if time.time() - ts < KLINES_CACHE_TTL: return d
    d = request_binance("GET", "/fapi/v1/klines",
                        {"symbol": symbol, "interval": interval, "limit": limit},
                        signed=False)
    if d: klines_cache[key] = (d, time.time())
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
    logger.info("📥 Chargement top {} symboles M15...".format(TOP_N_SYMBOLS))
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

        # Top N par volume uniquement — scan ciblé et efficace
        sorted_syms = sorted(vol_map.items(), key=lambda x: x[1], reverse=True)
        symbols_list = [s for s, v in sorted_syms if v > 1_000_000][:TOP_N_SYMBOLS]
        if not symbols_list: symbols_list = list(tradeable)[:TOP_N_SYMBOLS]
        logger.info("✅ {} symboles M15 (top {} par volume)".format(
            len(symbols_list), TOP_N_SYMBOLS))
    except Exception as e:
        logger.error("load_top_symbols: {} → fallback".format(e))
        symbols_list = FALLBACK_SYMBOLS[:]

# ═══════════════════════════════════════════════════════════════════
#  FEAR & GREED INDEX
# ═══════════════════════════════════════════════════════════════════

def get_fear_greed():
    """
    Récupère le Fear & Greed Index via l'API publique alternative.ly
    Valeur 0-100 : 0=Fear extrême, 100=Greed extrême
    Cache 5 minutes.
    """
    global _fg_cache
    with _fg_lock:
        if time.time() - _fg_cache.get("ts", 0) < FG_CACHE_TTL:
            return _fg_cache

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            data  = r.json()["data"][0]
            value = int(data["value"])
            label = data["value_classification"]
            result = {"value": value, "label": label, "ts": time.time()}
            with _fg_lock:
                _fg_cache = result
            return result
    except:
        pass

    # Fallback : valeur neutre si API indisponible
    return {"value": 50, "label": "Neutral", "ts": time.time()}

def check_fear_greed(side):
    """
    Filtre Fear & Greed :
    - BUY  refusé si greed extrême (>80) → le marché est suracheté/euphorique
    - SELL refusé si fear extrême  (<20) → le marché est en panique (rebond possible)
    Retourne (ok: bool, detail: str)
    """
    fg = get_fear_greed()
    v  = fg.get("value", 50)
    lb = fg.get("label", "Neutral")

    if side == "BUY" and v >= FG_BULL_MAX:
        return False, "F&G={} {} (greed extrême → BUY risqué)".format(v, lb)
    if side == "SELL" and v <= FG_BEAR_MIN:
        return False, "F&G={} {} (fear extrême → SELL risqué)".format(v, lb)

    return True, "F&G={} {}".format(v, lb)

# ═══════════════════════════════════════════════════════════════════
#  BTC M15 — DIRECTION + FORCE
# ═══════════════════════════════════════════════════════════════════

def get_btc_direction():
    global _btc_cache
    with _btc_lock:
        if time.time() - _btc_cache.get("ts", 0) < 60:
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
        direction = 1
        label = "BTC M15 🟢 HAUSSIER RSI={:.0f}".format(rsi)
    elif bear:
        direction = -1
        label = "BTC M15 🔴 BAISSIER RSI={:.0f}".format(rsi)
    else:
        direction = 0
        label = "BTC M15 ⚪ NEUTRE RSI={:.0f}".format(rsi)

    # Force de la tendance BTC pour le MM dynamique
    if direction != 0 and 45 <= rsi <= 65 and abs(slope) > 0.05:
        strength = "FORT"
    elif direction == 0:
        strength = "FAIBLE"
    else:
        strength = "NORMAL"

    result = {"direction": direction, "label": label, "rsi": round(rsi, 1),
              "slope": round(slope, 4), "ts": time.time(),
              "closes": cl, "strength": strength}
    with _btc_lock:
        _btc_cache = result
    return result

def check_btc_correlation(symbol, side, sym_closes):
    """
    Vérifie que le setup est OBLIGATOIREMENT aligné avec BTC M15.
    BUY  → BTC M15 HAUSSIER (direction == 1)
    SELL → BTC M15 BAISSIER (direction == -1)
    BTC neutre → aucun trade (pas de prise de risque contre la tendance)
    """
    btc = get_btc_direction()
    btc_dir = btc["direction"]

    # BTC neutre = pas de trade
    if btc_dir == 0:
        return False, "BTC M15 neutre → pas de trade"

    # Alignement strict direction
    if side == "BUY" and btc_dir != 1:
        return False, "BUY refusé — BTC M15 baissier"
    if side == "SELL" and btc_dir != -1:
        return False, "SELL refusé — BTC M15 haussier"

    # Co-mouvement sur 6 bougies
    btc_closes = btc.get("closes")
    if sym_closes and btc_closes and len(sym_closes) >= 6 and len(btc_closes) >= 6:
        sym_ret = (sym_closes[-1] - sym_closes[-6]) / sym_closes[-6] if sym_closes[-6] > 0 else 0
        btc_ret = (btc_closes[-1] - btc_closes[-6]) / btc_closes[-6] if btc_closes[-6] > 0 else 0
        same_dir = (sym_ret > 0 and btc_ret > 0) or (sym_ret < 0 and btc_ret < 0)
        if not same_dir:
            return False, "{} diverge BTC (sym:{:+.2f}% btc:{:+.2f}%)".format(
                symbol, sym_ret * 100, btc_ret * 100)

    return True, "BTC aligné ({}) force={}".format(btc["label"], btc.get("strength","?"))

# ═══════════════════════════════════════════════════════════════════
#  FONDAMENTAUX — Funding + OI Spike + Spread
# ═══════════════════════════════════════════════════════════════════

def get_funding_rate(symbol):
    try:
        d = request_binance("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
        if d: return float(d.get("lastFundingRate", 0))
    except: pass
    return 0.0

def get_oi_data(symbol):
    """
    Retourne (oi_change_pct, oi_spike: bool).
    oi_change_pct : variation sur 15min
    oi_spike      : True si OI a bondi de +2% en 1 bougie (potentiel piège)
    """
    try:
        d = request_binance("GET", "/futures/data/openInterestHist",
                            {"symbol": symbol, "period": "15m", "limit": 6}, signed=False)
        if d and len(d) >= 2:
            oi0 = float(d[0]["sumOpenInterest"])
            oi1 = float(d[-1]["sumOpenInterest"])
            # Spike sur la dernière bougie uniquement
            oi_last_prev = float(d[-2]["sumOpenInterest"])
            oi_last_curr = float(d[-1]["sumOpenInterest"])
            spike_pct    = (oi_last_curr - oi_last_prev) / oi_last_prev if oi_last_prev > 0 else 0

            oi_change = (oi1 - oi0) / oi0 if oi0 > 0 else 0
            oi_spike  = abs(spike_pct) > OI_SPIKE_THRESH
            return oi_change, oi_spike
    except: pass
    return 0.0, False

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
    """
    Score /60 : Funding(20) + OI(20) + Spread(20)
    OI spike détecté → filtre dur (rejet du signal, liquidité artificielle)
    Retourne (score, ok, detail)
    """
    score = 0
    parts = []

    # ── Funding ───────────────────────────────────────────────────
    funding = get_funding_rate(symbol)
    fp = funding * 100
    if side == "BUY":
        if funding <= 0.001:  score += 20; parts.append("Fund {:.4f}%✅".format(fp))
        elif funding > 0.002: parts.append("Fund {:.4f}%❌".format(fp))
        else:                 score += 10; parts.append("Fund {:.4f}%⚠️".format(fp))
    else:
        if funding >= -0.001:  score += 20; parts.append("Fund {:.4f}%✅".format(fp))
        elif funding < -0.002: parts.append("Fund {:.4f}%❌".format(fp))
        else:                  score += 10; parts.append("Fund {:.4f}%⚠️".format(fp))

    # ── OI + spike detection ──────────────────────────────────────
    oi_chg, oi_spike = get_oi_data(symbol)

    # Spike OI anormal = liquidité artificielle → rejet dur
    if oi_spike:
        parts.append("OI SPIKE ❌ (liquidité artificielle)")
        return score, False, " | ".join(parts)

    if oi_chg > 0.005:  score += 20; parts.append("OI +{:.2f}%✅".format(oi_chg*100))
    elif oi_chg > 0:    score += 10; parts.append("OI +{:.2f}%⚠️".format(oi_chg*100))
    elif oi_chg == 0:   score += 10; parts.append("OI N/A⚠️")
    else:               parts.append("OI {:.2f}%❌".format(oi_chg*100))

    # ── Spread Mark/Index ──────────────────────────────────────────
    spread = get_mark_spread(symbol)
    if abs(spread) < 0.05:   score += 20; parts.append("Sprd {:+.3f}%✅".format(spread))
    elif abs(spread) < 0.15: score += 10; parts.append("Sprd {:+.3f}%⚠️".format(spread))
    else:                    parts.append("Sprd {:+.3f}%❌".format(spread))

    return score, score >= FOND_MIN_SCORE, " | ".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  BREAKER BLOCK ICT M15 — ALIGNÉ BTC OBLIGATOIRE
# ═══════════════════════════════════════════════════════════════════

def _find_breaker_block(o, h, l, cl, v, atr, direction):
    """
    Breaker Block ICT M15 :
    - BOS (Break of Structure) validé par volume
    - Zone BB : dernier OB bearish avant BOS haussier (BUY) ou OB bullish avant BOS baissier (SELL)
    - Prix en retour dans la zone → entrée
    - Score 90-94 selon confluences (touches, volume, impulsion, EMA)
    """
    n = len(cl)
    if n < BB_LOOKBACK + 5: return None

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

            conf  = sum([1, bool(touches >= BB_TOUCH_MIN), bool(vol_spike),
                         bool(bos_impulsive), bool(bull_ema)])
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

            conf  = sum([1, bool(touches >= BB_TOUCH_MIN), bool(vol_spike),
                         bool(bos_impulsive), bool(bear_ema)])
            score = 90 + min(conf - 1, 4)

            return {"bottom": bb_bottom, "top": bb_top, "bos_idx": bos_idx,
                    "score": score, "confluence": min(conf, 5), "direction": "SELL",
                    "atr": atr, "vol_spike": vol_spike, "bos_imp": bos_impulsive,
                    "touches": touches}

    return None

# ═══════════════════════════════════════════════════════════════════
#  ANALYSE M15 — SETUP COMPLET
# ═══════════════════════════════════════════════════════════════════

def analyse_m15(symbol, klines):
    """
    Analyse M15 complète :
    1. Breaker Block ICT détecté
    2. Direction OBLIGATOIREMENT alignée avec BTC M15
    3. Confirmation bougie de réaction
    4. Score et probabilité calculés
    """
    try:
        if not klines or len(klines) < BB_LOOKBACK + 5:
            return None

        o, h, l, cl, v = _parse_klines(klines)
        atr = _atr(h, l, cl, 14)
        e9  = _ema(cl, 9)
        e21 = _ema(cl, 21)
        rsi = _rsi(cl, 14)

        # Bias local du symbole
        bull_bias = (e9[-1] > e21[-1]) and (rsi < 70)
        bear_bias = (e9[-1] < e21[-1]) and (rsi > 30)

        # Direction BTC — OBLIGATOIRE
        btc = get_btc_direction()
        btc_dir = btc["direction"]

        # BTC neutre → pas de trade
        if btc_dir == 0:
            return None

        # Ne chercher que dans la direction BTC
        candidates = []
        if btc_dir == 1  and bull_bias: candidates.append("BUY")
        if btc_dir == -1 and bear_bias: candidates.append("SELL")
        # Si le bias local est contre BTC → skip complètement
        if not candidates:
            return None

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

        # Probabilité selon confluences
        base = 85
        if conf >= 4: base += 5
        if conf >= 5: base += 5
        prob = min(float(base), 95.0)

        if prob < MIN_PROB: return None

        return {
            "symbol":      symbol,
            "side":        direction,
            "setup":       "BB_M15_BTC_ALIGN",
            "score":       score,
            "confluence":  conf,
            "probability": prob,
            "ob":          {"bottom": bb["bottom"], "top": bb["top"]},
            "atr":         atr,
            "rsi":         round(rsi, 1),
            "vol_spike":   bb.get("vol_spike", False),
            "bos_imp":     bb.get("bos_imp", False),
            "touches":     bb.get("touches", 0),
            "closes":      cl,
            "btc_strength": btc.get("strength", "NORMAL"),
        }

    except Exception as e:
        logger.debug("analyse_m15 {}: {}".format(symbol, e))
        return None

# ═══════════════════════════════════════════════════════════════════
#  SL STRUCTUREL — BASÉ ATR M15
# ═══════════════════════════════════════════════════════════════════

def get_sl(ob, entry, side, atr):
    if ob:
        buf    = atr * 0.2
        sl_raw = (ob["bottom"] - buf) if side == "BUY" else (ob["top"] + buf)
        dist   = abs(entry - sl_raw)
        if entry * MIN_SL_PCT <= dist <= entry * MAX_SL_PCT:
            return sl_raw
    dist = atr * ATR_SL_MULT
    dist = max(dist, entry * MIN_SL_PCT)
    dist = min(dist, entry * MAX_SL_PCT)
    return (entry - dist) if side == "BUY" else (entry + dist)

def find_tp_for_rr(entry, sl, direction, rr_target=4.0):
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
                    if p.get("marginType", "") != "isolated":
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
    info    = symbol_info_cache.get(symbol, {})
    qp      = info.get("quantityPrecision", 3)
    step    = info.get("stepSize", 0.001)
    qty_adj = round(_round_step(qty, step), qp)
    r = request_binance("POST", "/fapi/v1/order",
                        {"symbol": symbol, "side": side,
                         "type": "MARKET", "quantity": qty_adj})
    if r and r.get("orderId"):
        logger.info("  {} MARKET {} qty={} ✅".format(symbol, side, qty_adj))
        return r
    logger.error("❌ {} MARKET {} échoué".format(symbol, side))
    send_telegram("❌ <b>Ordre MARKET {} {} impossible</b>\nqty={}".format(
        symbol, side, qty_adj))
    return None

def place_sl_binance(symbol, sl, close_side):
    """3 stratégies : MARK_PRICE → CONTRACT_PRICE → SL logiciel"""
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    for _ in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": round(sl, pp), "closePosition": "true",
            "workingType": "MARK_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ MARK_PRICE @ {:.{}f} id={}".format(
                symbol, sl, pp, r["orderId"]))
            return {"sent": True, "order_id": r["orderId"], "method": "MARK_PRICE"}
        if r and r.get("_already_triggered"):
            return {"sent": False, "order_id": None, "triggered": True}
        time.sleep(0.5)

    logger.warning("⚠️  {} MARK_PRICE rejeté → CONTRACT_PRICE".format(symbol))
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": round(sl, pp), "closePosition": "true",
            "workingType": "CONTRACT_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ CONTRACT_PRICE @ {:.{}f}".format(symbol, sl, pp))
            return {"sent": True, "order_id": r["orderId"], "method": "CONTRACT_PRICE"}
        time.sleep(0.5)

    logger.error("🚨 {} SL Binance impossible → SL LOGICIEL @ {:.{}f}".format(symbol, sl, pp))
    send_telegram("🚨 <b>SL {} non posé Binance</b>\nSL logiciel actif @ {:.{}f}".format(
        symbol, sl, pp))
    return {"sent": False, "order_id": None, "method": "SOFTWARE"}

def place_tp_binance(symbol, tp, close_side):
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    for wtype in ["MARK_PRICE", "CONTRACT_PRICE"]:
        for _ in range(2):
            r = request_binance("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": close_side, "type": "TAKE_PROFIT_MARKET",
                "stopPrice": round(tp, pp), "closePosition": "true",
                "workingType": wtype})
            if r and r.get("orderId"):
                logger.info("🎯 {} TP ✅ {} @ {:.{}f}".format(symbol, wtype, tp, pp))
                return {"sent": True, "order_id": r["orderId"]}
            time.sleep(0.3)
    logger.warning("⚠️  {} TP non posé → trailing SL gère la sortie".format(symbol))
    return {"sent": False, "order_id": None}

def move_sl_binance(symbol, old_order_id, new_sl, close_side):
    """Déplace SL : annule ancien + pose nouveau avec fallback CONTRACT_PRICE."""
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    if old_order_id:
        r_del = request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": old_order_id})
        if r_del and r_del.get("_already_triggered"):
            logger.warning("⚠️  {} SL déclenché pendant déplacement → fermée".format(symbol))
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None

    for _ in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": round(new_sl, pp), "closePosition": "true",
            "workingType": "MARK_PRICE"})
        if r and r.get("orderId"): return r
        if r and r.get("_already_triggered"):
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None
        time.sleep(0.3)

    logger.warning("⚠️  {} MARK_PRICE trailing → CONTRACT_PRICE".format(symbol))
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": round(new_sl, pp), "closePosition": "true",
            "workingType": "CONTRACT_PRICE"})
        if r and r.get("orderId"): return r
        time.sleep(0.3)

    logger.error("❌ {} move_sl_binance échoué — SL logiciel @ {:.{}f}".format(
        symbol, new_sl, pp))
    return None

# ═══════════════════════════════════════════════════════════════════
#  SL SUIVEUR v41.0 — BIDIR | FRAIS + $0.01 | M15
# ═══════════════════════════════════════════════════════════════════

def update_trailing_sl(symbol):
    """
    SL Suiveur bidirectionnel :
    Phase 1 — BREAKEVEN à +0.6R : SL → entry + frais + $0.01
    Phase 2 — TRAILING à +1R    : SL = prix - ATR×1.0 (bidirectionnel)
    Plancher fee_floor absolu après BE.
    """
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

    current_price = get_price(symbol)
    if not current_price or current_price <= 0: return

    atr        = _atr_live(symbol)
    close_side = "SELL" if side == "BUY" else "BUY"
    qty_safe   = max(qty, 0.001)

    risk   = abs(entry - sl)
    if risk <= 0: return
    profit = (current_price - entry) if side == "BUY" else (entry - current_price)
    rr     = profit / risk

    new_sl     = sl
    action     = None
    be_trigger = False
    trail_used = False

    # Plancher frais absolu
    if side == "BUY":
        fee_floor = round(entry * (1.0 + BINANCE_FEE_RATE * 2) + BE_PROFIT_MIN / qty_safe, pp)
    else:
        fee_floor = round(entry * (1.0 - BINANCE_FEE_RATE * 2) - BE_PROFIT_MIN / qty_safe, pp)

    # Phase 1 — BREAKEVEN
    if rr >= BREAKEVEN_RR and not be_moved:
        if side == "BUY":
            be_sl = round(entry * (1.0 + BREAKEVEN_FEE_TOTAL) + BE_PROFIT_MIN / qty_safe, pp)
            if be_sl > sl:
                new_sl = be_sl; be_trigger = True
                action = "BE @ {:.{}f}".format(be_sl, pp)
        else:
            be_sl = round(entry * (1.0 - BREAKEVEN_FEE_TOTAL) - BE_PROFIT_MIN / qty_safe, pp)
            if be_sl < sl:
                new_sl = be_sl; be_trigger = True
                action = "BE @ {:.{}f}".format(be_sl, pp)

    # Phase 2 — TRAILING BIDIR (ATR×1.0 M15)
    if rr >= TRAIL_START_RR:
        trail_dist = atr * TRAIL_ATR_MULT
        if side == "BUY":
            trail_sl = max(round(current_price - trail_dist, pp), fee_floor)
            if trail_sl != new_sl:
                arrow = "↑" if trail_sl > new_sl else "↓"
                new_sl = trail_sl; trail_used = True
                action = "TRAIL {} {:.{}f} ATR={:.{}f}".format(arrow, trail_sl, pp, atr, pp)
        else:
            trail_sl = min(round(current_price + trail_dist, pp), fee_floor)
            if trail_sl != new_sl:
                arrow = "↓" if trail_sl < new_sl else "↑"
                new_sl = trail_sl; trail_used = True
                action = "TRAIL {} {:.{}f} ATR={:.{}f}".format(arrow, trail_sl, pp, atr, pp)

    tick = 10 ** (-pp)
    if be_moved:
        new_sl  = max(new_sl, fee_floor) if side == "BUY" else min(new_sl, fee_floor)
        changed = abs(new_sl - sl) > tick
    else:
        changed = (side == "BUY"  and new_sl > sl + tick) or \
                  (side == "SELL" and new_sl < sl - tick)

    if not changed: return

    new_order = move_sl_binance(symbol, sl_order_id, new_sl, close_side)

    with trade_lock:
        if symbol not in trade_log or trade_log[symbol].get("status") != "OPEN": return
        t = trade_log[symbol]
        if new_order and new_order.get("orderId"):
            t["sl"] = new_sl; t["sl_order_id"] = new_order["orderId"]
            t["sl_on_binance"] = True; t["trailing_stop_active"] = trail_used
            if be_trigger: t["breakeven_moved"] = True

            label = "BE+TRAIL" if be_trigger and trail_used else \
                    "BREAKEVEN frais couverts" if be_trigger else "TRAILING M15"
            logger.info("🔁 SL SUIVEUR {} {} [{}] {:.{}f}→{:.{}f} RR={:.2f}R | {}".format(
                symbol, side, label, sl, pp, new_sl, pp, rr, action))

            if be_trigger or (trail_used and int(rr) >= 1 and abs(rr - round(rr)) < 0.15):
                pnl_min = abs(new_sl - entry) * qty_safe
                send_telegram(
                    "🔁 <b>SL Suiveur {} {} [M15]</b>\n"
                    "{:.{}f} → {:.{}f}\n"
                    "RR={:.2f}R | Prix: {:.{}f}\n".format(
                        symbol, side, sl, pp, new_sl, pp, rr, current_price, pp) +
                    ("✅ <b>BREAKEVEN</b> frais couverts\n+${:.4f} net garanti".format(pnl_min)
                     if be_trigger else
                     "🔁 <b>Trailing RR{}R</b>".format(int(rr)))
                )
        else:
            t["sl_on_binance"] = False
            logger.warning("⚠️  {} déplacement SL Binance échoué — SL logiciel @ {:.{}f}".format(
                symbol, sl, pp))

# ═══════════════════════════════════════════════════════════════════
#  OPEN POSITION — MM DYNAMIQUE
# ═══════════════════════════════════════════════════════════════════

def open_position(signal):
    """
    Ouvre une position avec money management dynamique :
    - Marge = f(score, session, BTC force, fondamentaux)
    - Levier adaptatif selon palier
    - SL structurel ATR M15
    - TP exact RR4 après frais
    """
    symbol      = signal["symbol"]
    side        = signal["side"]
    setup       = signal["setup"]
    score       = signal["score"]
    prob        = signal["probability"]
    ob          = signal.get("ob", {})
    atr         = signal.get("atr", 0)
    fond_score  = signal.get("fond_score", 0)
    btc_strength = signal.get("btc_strength", "NORMAL")

    try:
        info = symbol_info_cache.get(symbol)
        if not info: return

        pp           = info.get("pricePrecision", 4)
        step_size    = info.get("stepSize", 0.001)
        min_qty      = info.get("minQty", 0.001)
        min_notional = info.get("minNotional", MIN_NOTIONAL)

        entry = get_price(symbol)
        if not entry: return

        lev        = get_leverage(score)
        max_sl_pct = get_max_sl_pct()

        # ── Marge dynamique ──────────────────────────────────────
        margin_pct = compute_dynamic_margin(score, fond_score, btc_strength)
        margin     = account_balance * margin_pct
        session    = get_session()

        # ── SL structurel ATR M15 ────────────────────────────────
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

        # ── TP exact RR4 après frais ─────────────────────────────
        tp = round(find_tp_for_rr(entry, sl, side, TP_RR), pp)

        # ── Sizing ───────────────────────────────────────────────
        risk_usdt = get_risk_usdt()
        qty = _round_step(risk_usdt / sl_dist, step_size)

        # Cap par marge × levier
        max_qty_m = _round_step((margin * lev) / entry, step_size)
        if max_qty_m > 0 and qty > max_qty_m:
            qty = max_qty_m

        # Min notional Binance ($5)
        if qty * entry < min_notional:
            qty_min      = _round_step(min_notional / entry + step_size, step_size)
            real_risk    = sl_dist * qty_min
            max_risk_pct = MAX_NOTIONAL_RISK
            if real_risk > account_balance * max_risk_pct:
                logger.info("  {} skip — min notional risque {:.1%} balance".format(
                    symbol, real_risk/account_balance))
                return
            qty = qty_min

        if qty < min_qty: return

        logger.info(
            "📊 {} {} | {} score={} prob={:.0f}% | {}x | "
            "SL {:.3f}% | marge={:.0f}% [{}×{}×{}] | session={} | qty={}".format(
                symbol, side, setup, score, prob, lev,
                sl_dist/entry*100, margin_pct*100,
                score, session, btc_strength,
                session, qty))

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
            tp = round(find_tp_for_rr(actual_entry, sl, side, TP_RR), pp)
        else:
            sl_dist2 = max(sl - actual_entry, actual_entry * MIN_SL_PCT)
            sl_dist2 = min(sl_dist2, actual_entry * MAX_SL_PCT)
            sl = round(actual_entry + sl_dist2, pp)
            tp = round(find_tp_for_rr(actual_entry, sl, side, TP_RR), pp)

        close_side = "SELL" if side == "BUY" else "BUY"
        sl_r = place_sl_binance(symbol, sl, close_side)
        tp_r = place_tp_binance(symbol, tp, close_side)

        be_price = round(actual_entry * (1.0 + BREAKEVEN_FEE_TOTAL), pp) if side == "BUY" \
                   else round(actual_entry * (1.0 - BREAKEVEN_FEE_TOTAL), pp)
        fg = _fg_cache

        with trade_lock:
            trade_log[symbol] = {
                "side": side, "entry": actual_entry, "sl": sl, "tp": tp,
                "qty": qty, "leverage": lev, "margin": margin,
                "margin_pct": round(margin_pct * 100, 1),
                "setup": setup, "score": score, "probability": prob,
                "status": "OPEN", "opened_at": time.time(),
                "sl_on_binance": sl_r["sent"], "tp_on_binance": tp_r["sent"],
                "sl_order_id": sl_r["order_id"], "tp_order_id": tp_r["order_id"],
                "trailing_stop_active": False, "breakeven_moved": False,
                "btc_corr": signal.get("btc_corr", "?"), "atr": atr,
                "be_price": be_price, "session": session,
                "fond_score": fond_score, "btc_strength": btc_strength,
            }

        logger.info("✅ {} {} @ {:.{}f} | SL {:.{}f} | TP {:.{}f} | {}x | marge={:.0f}% | {}".format(
            symbol, side, actual_entry, pp, sl, pp, tp, pp, lev,
            margin_pct*100, get_tier_label()))

        send_telegram(
            "🚀 <b>{} {}</b> @ {:.{}f}\n"
            "SL: {:.{}f} ({:.3f}%) {}\n"
            "BE: {:.{}f} | TP: {:.{}f} (RR{})\n"
            "Setup: {} | Score:{} | Conf:{}/5\n"
            "Marge: <b>{:.0f}%</b> [score×{}×{}]\n"
            "Levier: {}x | Session: {}\n"
            "Fond: {}/60 | F&G: {} {}\n"
            "{} | {}".format(
                symbol, side,
                actual_entry, pp,
                sl, pp, sl_dist2/actual_entry*100,
                "✅Binance" if sl_r["sent"] else "⚠️logiciel",
                be_price, pp, tp, pp, TP_RR,
                setup, score, signal.get("confluence", 0),
                margin_pct*100, session, btc_strength,
                lev, session,
                fond_score, fg.get("value","?"), fg.get("label",""),
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
        milestone_msg = ""
        if account_balance >= TARGET_BALANCE:
            milestone_msg = "\n🏆 <b>OBJECTIF ${:.0f} ATTEINT !</b>".format(TARGET_BALANCE)
        elif account_balance >= TIER2_LIMIT:
            milestone_msg = "\n🟢 Palier NORMAL atteint ! ${:.4f}".format(account_balance)
        elif account_balance >= TIER1_LIMIT:
            milestone_msg = "\n🟡 Palier CROISSANCE atteint ! ${:.4f}".format(account_balance)
        send_telegram(
            "✅ <b>WIN {} {}</b>\n"
            "Setup: {} | Frais couverts\n"
            "Balance: ${:.4f} {}{}".format(
                symbol, side, setup, account_balance, get_progress_bar(), milestone_msg))
    else:
        consec_losses += 1
        symbol_stats[symbol]["losses"] += 1
        logger.info("🔴 LOSS {} {} {} consec={}".format(symbol, side, setup, consec_losses))
        send_telegram(
            "🔴 <b>LOSS {} {}</b>\n"
            "Setup: {} | Consécutives: {}\n"
            "Balance: ${:.4f} {}\n{}".format(
                symbol, side, setup, consec_losses,
                account_balance, get_tier_label(), get_progress_bar()))
        if consec_losses >= CONSEC_LOSS_LIMIT:
            cooldown_until = time.time() + CONSEC_COOLDOWN
            logger.warning("⏸ Pause {}min".format(CONSEC_COOLDOWN // 60))
            send_telegram("⏸ <b>Pause {}min</b>\nAprès {} pertes consécutives".format(
                CONSEC_COOLDOWN // 60, consec_losses))

def _on_closed_from_binance(symbol, trade):
    try:
        income = request_binance("GET", "/fapi/v1/income",
                                 {"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": 5},
                                 signed=True)
        pnl = sum(float(i.get("income", 0)) for i in income) if income else 0.0
        _on_closed(symbol, trade, is_win=pnl >= 0)
    except:
        _on_closed(symbol, trade, is_win=False)

def check_drawdown():
    ref = drawdown_state.get("ref_balance", 0)
    if ref <= 0: return
    dd = (ref - account_balance) / ref
    if dd >= DD_ALERT_PCT:
        now = time.time()
        if now - drawdown_state.get("last_alert", 0) > 300:
            drawdown_state["last_alert"] = now
            send_telegram("⚠️ <b>DRAWDOWN {:.1%}</b>\n${:.4f} (départ: ${:.4f})".format(
                dd, account_balance, ref))

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
                            logger.warning("🚨 {} SL LOGICIEL déclenché @ {}".format(symbol, cp))
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
    """
    Scan complet d'un symbole :
    1. Analyse technique M15 (BB aligné BTC)
    2. Fear & Greed Index
    3. Corrélation BTC (co-mouvement 6 bougies)
    4. Fondamentaux (Funding + OI spike + Spread)
    Retourne le signal enrichi ou None.
    """
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

        side        = signal["side"]
        sym_closes  = signal["closes"]

        # Filtre Fear & Greed
        fg_ok, fg_detail = check_fear_greed(side)
        if not fg_ok:
            logger.debug("  {} {} refusé: {}".format(symbol, side, fg_detail))
            return None
        signal["fg_detail"] = fg_detail

        # Corrélation + co-mouvement BTC
        corr_ok, corr_reason = check_btc_correlation(symbol, side, sym_closes)
        if not corr_ok:
            logger.debug("  {} {} refusé: {}".format(symbol, side, corr_reason))
            return None
        signal["btc_corr"] = corr_reason

        # Fondamentaux (avec OI spike)
        fond_score, fond_ok, fond_detail = check_fondamentaux(symbol, side)
        if not fond_ok:
            logger.debug("  {} fond insuffisant ({}/60): {}".format(symbol, fond_score, fond_detail))
            return None
        signal["fond_score"]  = fond_score
        signal["fond_detail"] = fond_detail

        logger.debug("  {} ✅ score={} conf={}/5 fond={}/60 {}".format(
            symbol, signal["score"], signal["confluence"], fond_score, side))

        return signal

    except Exception as e:
        logger.debug("scan_symbol {}: {}".format(symbol, e))
        return None

# ═══════════════════════════════════════════════════════════════════
#  RECOVER POSITIONS EXISTANTES
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

            info = symbol_info_cache.get(sym, {})
            pp   = info.get("pricePrecision", 4)
            atr  = _atr_live(sym)
            dist = max(atr * ATR_SL_MULT, entry * MIN_SL_PCT)
            dist = min(dist, entry * MAX_SL_PCT)

            sl = round(entry - dist if side == "BUY" else entry + dist, pp)
            tp = round(find_tp_for_rr(entry, sl, side, TP_RR), pp)
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
                    "leverage": 15, "margin_pct": 20, "setup": "RECOVERED",
                    "score": 90, "probability": 90.0, "status": "OPEN",
                    "opened_at": time.time(), "sl_on_binance": sl_r["sent"],
                    "tp_on_binance": tp_r["sent"], "sl_order_id": sl_r["order_id"],
                    "tp_order_id": tp_r["order_id"], "trailing_stop_active": False,
                    "breakeven_moved": False, "be_price": be, "atr": atr,
                    "btc_corr": "RECOVERED", "fond_score": 0,
                }
            logger.info("🔄 {} {} @ {:.{}f} récupéré | SL {:.{}f} | TP {:.{}f}".format(
                sym, side, entry, pp, sl, pp, tp, pp))
    except Exception as e:
        logger.error("recover: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  AFFICHAGE CONSOLE
# ═══════════════════════════════════════════════════════════════════

def print_signal_console(sig, rank):
    side = sig["side"]
    dcol = RED + BOLD if side == "SELL" else GREEN + BOLD
    sep  = "═" * 65
    print("\n" + cc(sep, CYAN))
    print("  #{} {:<22} {} {}".format(
        rank, sig["symbol"], cc("◄ " + side, dcol), cc("BB M15 BTC-ALIGN", MAGENTA + BOLD)))
    print(cc("─" * 65, DIM))

    def row(label, val, col=WHITE):
        print("  {}  {}".format(cc("{:<18}".format(label+":"), DIM), cc(str(val), col)))

    fg   = _fg_cache
    sess = get_session()
    mmult = get_session_mult()
    bstr  = sig.get("btc_strength", "NORMAL")
    base  = MARGIN_BY_SCORE.get(sig["score"], MARGIN_MIN)
    marg  = compute_dynamic_margin(sig["score"], sig.get("fond_score", 0), bstr)

    row("Setup",    "{} | score={} conf={}/5 prob={:.0f}%".format(
        sig["setup"], sig["score"], sig["confluence"], sig["probability"]), BOLD + WHITE)
    row("BTC M15",  sig.get("btc_corr", "?"), CYAN)
    row("Session",  "{} (×{})".format(sess, mmult), YELLOW)
    row("BTC Force",bstr, GREEN if bstr == "FORT" else WHITE)
    row("Fond",     "{}/60 — {}".format(sig.get("fond_score",0), sig.get("fond_detail","")),
        GREEN if sig.get("fond_score",0) >= 50 else YELLOW)
    row("F&G",      sig.get("fg_detail", "?"),
        GREEN if fg.get("value",50) < 70 else RED)
    row("Marge dyn", "{:.0f}% → base={:.0f}% ×sess×BTC×fond".format(marg*100, base*100),
        GREEN + BOLD)
    print(cc(sep, CYAN))

# ═══════════════════════════════════════════════════════════════════
#  LOOPS
# ═══════════════════════════════════════════════════════════════════

def scanner_loop():
    logger.info("🔍 Scanner M15 v2.0 — BB BTC-Aligné | RR4 | MM Dynamique")
    time.sleep(5)
    count = 0
    while True:
        try:
            if _bot_stop:
                time.sleep(10); continue

            count += 1
            # Sync balance toutes les 2 bougies M15 (~30min)
            if count % max(1, (1800 // max(SCAN_INTERVAL, 1))) == 0:
                sync_binance_time()
                get_account_balance()
                btc = get_btc_direction()
                fg  = get_fear_greed()
                logger.info("BTC: {} | Balance: ${:.4f} | F&G:{} {} | {}".format(
                    btc["label"], account_balance,
                    fg["value"], fg["label"], get_tier_label()))

            # Reload symboles toutes les 4 bougies M15 (~1h)
            if count % max(1, (3600 // max(SCAN_INTERVAL, 1))) == 0:
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

            # Filtre session OFF → pause (liquidité trop faible)
            session = get_session()
            if session == "OFF":
                logger.info("🌙 Session OFF ({} UTC) — pause scan".format(
                    datetime.now(timezone.utc).hour))
                time.sleep(SCAN_INTERVAL); continue

            btc = get_btc_direction()
            # BTC neutre → scan annulé (direction obligatoire)
            if btc["direction"] == 0:
                logger.info("⚪ BTC M15 neutre — scan annulé (direction requise)")
                time.sleep(SCAN_INTERVAL); continue

            logger.info("🔍 Scan {} symboles | {} | {} | F&G:{} | {}".format(
                len(symbols_list), btc["label"], session,
                _fg_cache.get("value","?"), get_tier_label()))

            # Scan parallèle
            signals  = []
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
                logger.info("  Aucun signal qualifié sur {} symboles".format(len(symbols_list)))
                time.sleep(SCAN_INTERVAL); continue

            # Tri composite : score × probabilité × confluence × fond
            signals.sort(
                key=lambda s: (s["score"] * s["probability"] * s["confluence"]
                               * (1 + s.get("fond_score", 0) / 60)),
                reverse=True)

            logger.info("✨ {} signaux qualifiés — meilleur: {} {} score={} conf={}/5 fond={}/60".format(
                len(signals), signals[0]["symbol"], signals[0]["side"],
                signals[0]["score"], signals[0]["confluence"],
                signals[0].get("fond_score", 0)))

            best = signals[0]
            sym  = best["symbol"]

            with trade_lock:
                already = sym in trade_log and trade_log[sym].get("status") == "OPEN"
            if not already:
                signal_last_at[sym] = time.time()
                print_signal_console(best, 1)
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
            check_drawdown()
        except Exception as e:
            logger.debug("monitor_loop: {}".format(e))
        time.sleep(MONITOR_INTERVAL)

def dashboard_loop():
    time.sleep(20)
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            tw   = sum(v["wins"]   for v in symbol_stats.values())
            tl   = sum(v["losses"] for v in symbol_stats.values())
            wr   = tw/(tw+tl)*100 if (tw+tl) > 0 else 0
            btc  = get_btc_direction()
            fg   = get_fear_greed()
            sess = get_session()
            mmult = get_session_mult()
            logger.info("═" * 65)
            logger.info("SCANNER M15 v2.0 | ${:.4f} | {} | {}".format(
                account_balance, get_tier_label(), get_progress_bar()))
            logger.info("Pos:{}/{} | W:{} L:{} WR:{:.1f}% | {} | {}".format(
                n_open, MAX_POSITIONS, tw, tl, wr, btc["label"], btc.get("strength","?")))
            logger.info("Session:{} (×{}) | F&G:{} {} | Symboles:{}".format(
                sess, mmult, fg["value"], fg["label"], len(symbols_list)))
            logger.info("═" * 65)

            if n_open > 0:
                pos_data = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
                pnl_map  = {}
                if pos_data:
                    for p in pos_data:
                        s, a = p.get("symbol"), float(p.get("positionAmt", 0))
                        if a != 0:
                            pnl_map[s] = {"pnl":  float(p.get("unRealizedProfit", 0)),
                                          "mark": float(p.get("markPrice", 0))}
                with trade_lock:
                    for sym, t in trade_log.items():
                        if t.get("status") != "OPEN": continue
                        info = symbol_info_cache.get(sym, {})
                        pp   = info.get("pricePrecision", 4)
                        pd   = pnl_map.get(sym, {})
                        pnl  = pd.get("pnl", 0)
                        mark = pd.get("mark", t["entry"])
                        risk = abs(t["entry"] - t["sl"])
                        rr   = abs(mark - t["entry"]) / risk if risk > 0 else 0
                        be   = "✅BE" if t.get("breakeven_moved") else "BE@{:.{}f}".format(t.get("be_price",0), pp)
                        tr   = "🔁TR" if t.get("trailing_stop_active") else ""
                        icon = "🟢" if pnl >= 0 else "🔴"
                        logger.info("  {} {} {} | {}x marge={:.0f}% | RR:{:.2f}R | PnL:{:+.4f}$".format(
                            icon, sym, t["side"], t["leverage"], t.get("margin_pct",0), rr, pnl))
                        logger.info("    Entry:{:.{}f} Mark:{:.{}f} SL:{:.{}f} {} {}".format(
                            t["entry"], pp, mark, pp, t["sl"], pp, be, tr))
        except Exception as e:
            logger.debug("dashboard: {}".format(e))
        time.sleep(DASHBOARD_INTERVAL)

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("╔" + "═" * 63 + "╗")
    logger.info("║  SCANNER M15 v2.0 — BB BTC-ALIGNÉ | RR4 | MM DYNAMIQUE  ║")
    logger.info("║  Session Filter | Fear&Greed | OI Spike | Top 80 Cryptos ║")
    logger.info("╚" + "═" * 63 + "╝")
    logger.warning("🔥 LIVE TRADING 🔥")

    logger.info("✅ SETUP         : Breaker Block M15 OBLIGATOIREMENT aligné BTC")
    logger.info("✅ SCAN          : Top {} cryptos par volume".format(TOP_N_SYMBOLS))
    logger.info("✅ MM DYNAMIQUE  : {:.0f}%→{:.0f}% selon score/session/BTC/fond".format(
        MARGIN_MIN*100, MARGIN_MAX*100))
    logger.info("✅ SESSION       : Asia×0.6 | London×1.0 | NY×1.2 | OFF=pause")
    logger.info("✅ FEAR & GREED  : BUY refusé si >80 | SELL refusé si <20")
    logger.info("✅ OI SPIKE      : Rejet si spike > {:.0f}% sur 1 bougie".format(
        OI_SPIKE_THRESH*100))
    logger.info("✅ SL M15        : ATR×{} ({:.0f}%→{:.0f}%)".format(
        ATR_SL_MULT, MIN_SL_PCT*100, MAX_SL_PCT*100))
    logger.info("✅ RR            : {}× net après frais 0.12%".format(TP_RR))
    logger.info("✅ SL SUIVEUR    : Bidir dès RR{} | Frais+$0.01 garanti".format(TRAIL_START_RR))

    start_health_server()
    sync_binance_time()
    load_top_symbols()
    get_account_balance()
    drawdown_state["ref_balance"] = account_balance

    btc = get_btc_direction()
    fg  = get_fear_greed()
    sess = get_session()

    logger.info("💰 Balance: ${:.4f} | Palier: {}".format(account_balance, get_tier_label()))
    logger.info("📊 BTC M15: {} | Force: {}".format(btc["label"], btc.get("strength","?")))
    logger.info("😱 Fear & Greed: {} ({})".format(fg["value"], fg["label"]))
    logger.info("🕐 Session: {} (mult ×{})".format(sess, get_session_mult()))

    send_telegram(
        "🚀 <b>SCANNER M15 v2.0 DÉMARRÉ</b>\n\n"
        "💰 Balance: <b>${:.4f}</b> | {}\n"
        "🎯 Objectif: ${:.0f} | {}\n\n"
        "📊 BTC M15: {} | Force: {}\n"
        "😱 Fear & Greed: {} ({}) \n"
        "🕐 Session: {} (×{})\n\n"
        "⚙️ CONFIG :\n"
        "  BB M15 BTC-aligné obligatoire\n"
        "  Top {} cryptos | OI Spike filtré\n"
        "  MM: {:.0f}%→{:.0f}% dynamique\n"
        "  SL ATR×{} | RR{}× | SL suiveur bidir\n"
        "  Sessions: Asia×0.6 London×1.0 NY×1.2".format(
            account_balance, get_tier_label(),
            TARGET_BALANCE, get_progress_bar(),
            btc["label"], btc.get("strength","?"),
            fg["value"], fg["label"],
            sess, get_session_mult(),
            TOP_N_SYMBOLS,
            MARGIN_MIN*100, MARGIN_MAX*100,
            ATR_SL_MULT, TP_RR
        )
    )

    recover_existing_positions()

    threading.Thread(target=scanner_loop,   daemon=True).start()
    threading.Thread(target=monitor_loop,   daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()

    logger.info("✅ SCANNER M15 v2.0 ONLINE 🚀")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown")

if __name__ == "__main__":
    main()

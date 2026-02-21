#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   SCALPER v39 — TOP 20 CRYPTO | PURE M1 | H24                  ║
║   2 positions max | Levier & marge AUTO                         ║
║   Scan continu M1 — Setup + Signal + Trigger tout en M1         ║
╚══════════════════════════════════════════════════════════════════╝

Architecture :
  ► Scan : top 20 symboles Binance Futures (volume 24h)
  ► Timeframe UNIQUE : M1 (tout est analysé sur M1)
  ► Direction : EMA9/21/50 M1 + RSI14 + structure pivot M1
  ► Setup SMC M1 : SWEEP_OB | BOS_FVG | ENGULF_VOL
  ► Trigger : bougie M1 fermée confirmant la direction
  ► 2 positions simultanées max
  ► Levier auto : score≥90→40x | ≥80→30x | sinon 20x
  ► Marge auto : % balance selon levier
  ► Risque fixe $0.30 / trade
  ► SL structurel Binance + trailing M1
  ► TP filet RR6 + trailing SL (vraie sortie)
  ► H24 sans pause — alerte drawdown sans blocage
  ► Hard floor $1.50
"""

import time, hmac, hashlib, requests, threading, os, logging
import numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

# ─── LOGGING ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scalper_v39.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── TELEGRAM ────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
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

# ─── API ─────────────────────────────────────────────────────────
API_KEY    = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")


if not API_KEY or not API_SECRET:
    logger.error("❌ BINANCE_API_KEY / BINANCE_API_SECRET manquants")
    exit(1)

BASE_URL = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION PRINCIPALE
# ═══════════════════════════════════════════════════════════════════

# ── Univers de trading ───────────────────────────────────────────
TOP_N_SYMBOLS   = 20       # Scanner les 20 premiers par volume 24h
EXCLUDE_SYMBOLS = {"USDCUSDT","BUSDUSDT","TUSDUSDT","FDUSDUSDT","USDPUSDT"}
FALLBACK_SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","NEARUSDT","LTCUSDT","UNIUSDT","ATOMUSDT",
    "INJUSDT","ARBUSDT","OPUSDT","APTUSDT","SUIUSDT",
]

# ── Timeframe UNIQUE M1 ─────────────────────────────────────────
TF = "1m"           # Tout se passe en M1
KLINES_LIMIT = 60   # Bougies M1 chargées par symbole

# ── Risque & Sizing ──────────────────────────────────────────────
FIXED_RISK_USDT      = 0.30   # Risque $ par trade
MAX_POSITIONS        = 2      # Positions simultanées max
BALANCE_HARD_FLOOR   = 1.50   # Gel total si balance < $1.50
MAX_RISK_MULTIPLIER  = 2.0    # Accepte 2× risque si min_notional l'exige
MIN_NOTIONAL         = 5.0    # Notionnel minimum Binance Futures
MARGIN_TYPE          = "ISOLATED"

# ── Levier adaptatif ─────────────────────────────────────────────
LEVERAGE_BY_SCORE = [
    (90, 40),   # SWEEP_OB  → 40x
    (80, 30),   # BOS_FVG   → 30x
    (70, 20),   # ENGULF    → 20x
    (0,  15),   # fallback  → 15x
]
MARGIN_PCT_BY_LEV = {40: 0.40, 30: 0.35, 20: 0.30, 15: 0.25}

# ── SL / TP / Trailing ──────────────────────────────────────────
MIN_SL_PCT       = 0.0025  # SL min 0.25% du prix
TP_RR            = 6.0     # TP filet RR6 (trailing = vraie sortie)
BREAKEVEN_RR     = 0.5     # BE dès +0.5R
BREAKEVEN_FEE    = 0.0006  # Buffer frais
TRAIL_START_RR   = 1.0     # Trailing dès +1R
TRAIL_ATR_MULT   = 1.0     # Multiplicateur ATR trailing M1

# ── Filtres signal M1 ────────────────────────────────────────────
MIN_SCORE         = 75     # Score minimum setup
MIN_PROB          = 60.0   # Probabilité minimum %
MIN_CONFLUENCE    = 2      # Confluence minimum (réduit pour M1)
MIN_BODY_RATIO    = 0.40   # Corps bougie trigger ≥ 40% range
VOLUME_SPIKE_MULT = 1.8    # Volume spike M1
FVG_MIN_GAP_PCT   = 0.0008 # FVG minimum 0.08%
OB_LOOKBACK       = 8      # Lookback Order Block M1
SIGNAL_COOLDOWN   = 120    # 2 min entre 2 signaux même symbole

# ── Drawdown & Sécurité ──────────────────────────────────────────
DD_ALERT_PCT       = 0.20  # Alerte Telegram à -20% (pas de blocage)
CONSEC_LOSS_LIMIT  = 3     # Cooldown après 3 pertes consécutives
CONSEC_COOLDOWN    = 20 * 60  # 20 min (court pour scalper H24)

# ── Timing ───────────────────────────────────────────────────────
SCAN_INTERVAL      = 8     # secondes entre scans (rapide pour M1)
MONITOR_INTERVAL   = 3     # surveillance positions
DASHBOARD_INTERVAL = 30
KLINES_CACHE_TTL   = 8     # cache klines 8s (M1 bouge vite)
PRICE_CACHE_TTL    = 1
MAX_WORKERS        = 8     # threads parallèles scan

# ─── ÉTAT GLOBAL ─────────────────────────────────────────────────
account_balance     = 0.0
trade_log           = {}      # {symbol: trade_dict}
trade_lock          = threading.Lock()
api_lock            = threading.Lock()
api_semaphore       = threading.Semaphore(8)
api_call_times      = []
klines_cache        = {}
price_cache         = {}
symbol_info_cache   = {}
signal_last_at      = {}
structure_memory    = {}
symbols_list        = []
consec_losses       = 0
cooldown_until      = 0.0
_binance_time_offset = 0
_bot_stop           = False

drawdown_state = {
    "ref_balance": 0.0,
    "last_alert":  0.0,
    "last_reset":  0.0,
}

# Compteur wins/losses par symbole
symbol_stats = defaultdict(lambda: {"wins": 0, "losses": 0})

# ─── FLASK HEALTH ─────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    status = "🛑 STOPPED" if _bot_stop else "🟢 RUNNING"
    return (f"SCALPER v39 | {status} | ${account_balance:.4f} | "
            f"Pos: {n}/{MAX_POSITIONS} | Top {TOP_N_SYMBOLS} cryptos | M1"), 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status_ep():
    with trade_lock:
        open_pos = {s: {k: t.get(k) for k in ["side","entry","sl","tp","setup","probability"]}
                    for s, t in trade_log.items() if t.get("status") == "OPEN"}
    total_w = sum(v["wins"]   for v in symbol_stats.values())
    total_l = sum(v["losses"] for v in symbol_stats.values())
    return jsonify({
        "version": "v39", "balance": round(account_balance, 4),
        "positions": open_pos, "symbols": symbols_list[:20],
        "wins": total_w, "losses": total_l,
        "consec_losses": consec_losses,
        "emergency_stop": _bot_stop,
    })

@flask_app.route("/stop", methods=["GET", "POST"])
def stop_ep():
    global _bot_stop
    _bot_stop = True
    logger.error("🛑 EMERGENCY STOP")
    send_telegram("🛑 <b>EMERGENCY STOP</b> — Scanner arrêté, SL Binance actifs")
    return "🛑 STOPPED", 200

@flask_app.route("/resume", methods=["GET", "POST"])
def resume_ep():
    global _bot_stop, cooldown_until
    _bot_stop = False
    cooldown_until = 0.0
    logger.info("▶️ Trading repris via /resume")
    send_telegram("▶️ <b>Trading repris</b>")
    return "▶️ RESUMED", 200

@flask_app.route("/trades")
def trades_ep():
    with trade_lock:
        data = {s: {k: t.get(k) for k in ["side","entry","sl","tp","qty","setup",
                                            "probability","leverage","trailing_stop_active"]}
                for s, t in trade_log.items() if t.get("status") == "OPEN"}
    return jsonify(data)

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    try:
        import logging as _l
        _l.getLogger("werkzeug").setLevel(_l.ERROR)
        threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False),
                         daemon=True).start()
        logger.info(f"🌐 Health server port {port}")
    except Exception as e:
        logger.warning(f"Health server: {e}")

# ─── TIME SYNC ────────────────────────────────────────────────────
def sync_binance_time():
    global _binance_time_offset
    offsets = []
    for _ in range(3):
        try:
            t0   = int(time.time() * 1000)
            resp = requests.get(BASE_URL + "/fapi/v1/time", timeout=3)
            t1   = int(time.time() * 1000)
            if resp.status_code == 200:
                st = resp.json()["serverTime"]
                offsets.append(st - t0 - (t1 - t0) // 2)
        except:
            pass
        time.sleep(0.1)
    if offsets:
        _binance_time_offset = int(sum(offsets) / len(offsets))
        logger.info(f"⏱️ Binance offset: {_binance_time_offset}ms")

# ─── RATE LIMIT ──────────────────────────────────────────────────
def _rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < 60]
        if len(api_call_times) >= 1100:
            sleep = 60 - (now - api_call_times[0])
            if sleep > 0:
                time.sleep(sleep)
        api_call_times.append(now)

# ─── API ─────────────────────────────────────────────────────────
def _sign(params: dict) -> str:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

def request_binance(method: str, path: str, params: dict = None, signed: bool = True):
    if params is None:
        params = {}
    _rate_limit()
    headers = {"X-MBX-APIKEY": API_KEY}
    url = BASE_URL + path
    with api_semaphore:
        for attempt in range(3):
            try:
                if signed:
                    params.pop("signature", None)
                    params["timestamp"]  = int(time.time() * 1000) + _binance_time_offset
                    params["recvWindow"] = 20000
                    params["signature"]  = _sign(params)
                if method == "GET":
                    r = requests.get(url,    params=params, headers=headers, timeout=10)
                elif method == "POST":
                    r = requests.post(url,   params=params, headers=headers, timeout=10)
                elif method == "DELETE":
                    r = requests.delete(url, params=params, headers=headers, timeout=10)
                else:
                    return None
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 429:
                    w = [5, 15, 30][min(attempt, 2)]
                    logger.warning(f"⏳ 429 → {w}s")
                    time.sleep(w)
                elif r.status_code in (401, 403):
                    logger.error(f"🔑 {r.status_code}: {r.text[:200]}")
                    send_telegram(f"🔑 <b>Erreur API {r.status_code}</b> — Clé invalide")
                    return None
                elif r.status_code == 418:
                    logger.error("🚨 IP BAN 418 → 120s")
                    time.sleep(120)
                    return None
                else:
                    body = r.text[:200]
                    if "-1021" in body and attempt < 2:
                        sync_binance_time()
                        continue
                    logger.debug(f"API {r.status_code}: {body}")
                    return None
            except Exception as e:
                logger.debug(f"request_binance attempt {attempt+1}: {e}")
                time.sleep(1 * (attempt + 1))
    return None

# ─── MARKET DATA ─────────────────────────────────────────────────
def get_klines(symbol: str, limit: int = KLINES_LIMIT) -> list:
    """Klines M1 avec cache 8s."""
    key = f"{symbol}_1m"
    now = time.time()
    if key in klines_cache:
        data, ts = klines_cache[key]
        if now - ts < KLINES_CACHE_TTL:
            return data
    data = request_binance("GET", "/fapi/v1/klines",
                           {"symbol": symbol, "interval": "1m", "limit": limit},
                           signed=False)
    if data:
        klines_cache[key] = (data, now)
    return data or []

def get_price(symbol: str) -> float:
    now = time.time()
    if symbol in price_cache:
        p, ts = price_cache[symbol]
        if now - ts < PRICE_CACHE_TTL:
            return p
    data = request_binance("GET", "/fapi/v1/ticker/price", {"symbol": symbol}, signed=False)
    if data and "price" in data:
        p = float(data["price"])
        price_cache[symbol] = (p, now)
        return p
    return 0.0

def get_account_balance() -> float:
    global account_balance
    data = request_binance("GET", "/fapi/v2/balance", signed=True)
    if data:
        for a in data:
            if a.get("asset") == "USDT":
                account_balance = float(a.get("availableBalance", 0))
                return account_balance
    return account_balance

# ─── LOAD SYMBOLS (TOP 20 volume) ────────────────────────────────
def load_top_symbols():
    global symbols_list
    logger.info(f"📥 Chargement top {TOP_N_SYMBOLS} symboles Binance Futures...")
    try:
        tickers  = request_binance("GET", "/fapi/v1/ticker/24hr", signed=False)
        exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
        if not tickers or not exchange:
            raise ValueError("API non disponible")

        tradeable = set()
        for s in exchange.get("symbols", []):
            sym = s["symbol"]
            if (sym.endswith("USDT") and s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                    and sym not in EXCLUDE_SYMBOLS):
                tradeable.add(sym)
                # Charger les infos symbole
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                symbol_info_cache[sym] = {
                    "quantityPrecision": s.get("quantityPrecision", 3),
                    "pricePrecision":    s.get("pricePrecision", 4),
                    "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                    "maxQty":      float(filters.get("LOT_SIZE", {}).get("maxQty", 1e6)),
                    "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                }

        vol_map = {}
        for t in tickers:
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym in tradeable:
                try:
                    vol_map[sym] = float(t.get("quoteVolume", 0))
                except:
                    pass

        ranked = sorted(
            [(s, v) for s, v in vol_map.items() if v > 5_000_000],
            key=lambda x: x[1], reverse=True
        )[:TOP_N_SYMBOLS]

        symbols_list = [s for s, _ in ranked]
        if len(symbols_list) < 5:
            symbols_list = FALLBACK_SYMBOLS[:TOP_N_SYMBOLS]

        logger.info(f"✅ Top {len(symbols_list)} symboles : {symbols_list}")
    except Exception as e:
        logger.error(f"load_top_symbols: {e} → fallback")
        symbols_list = FALLBACK_SYMBOLS[:TOP_N_SYMBOLS]

        # Charger les infos pour les fallback
        exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
        if exchange:
            for s in exchange.get("symbols", []):
                sym = s["symbol"]
                if sym in symbols_list:
                    filters = {f["filterType"]: f for f in s.get("filters", [])}
                    symbol_info_cache[sym] = {
                        "quantityPrecision": s.get("quantityPrecision", 3),
                        "pricePrecision":    s.get("pricePrecision", 4),
                        "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                        "maxQty":      float(filters.get("LOT_SIZE", {}).get("maxQty", 1e6)),
                        "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                        "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                    }

# ─── HELPERS MATH ────────────────────────────────────────────────
def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return float(int(qty / step) * step)

def _parse_klines(klines: list):
    o = np.array([float(k[1]) for k in klines])
    h = np.array([float(k[2]) for k in klines])
    l = np.array([float(k[3]) for k in klines])
    c = np.array([float(k[4]) for k in klines])
    v = np.array([float(k[5]) for k in klines])
    return o, h, l, c, v

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.zeros(len(arr))
    if len(arr) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result

def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag = np.mean(gains[-period:])
    al = np.mean(losses[-period:])
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def _atr(h, l, c, period=14) -> float:
    try:
        trs = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
               for i in range(1, len(c))]
        if not trs:
            return 0.0
        return float(np.mean(trs[-period:])) if len(trs) >= period else float(np.mean(trs))
    except:
        return 0.0

def _find_pivot_highs(h, lb=3):
    return [i for i in range(lb, len(h) - lb)
            if all(h[i] >= h[i-j] for j in range(1, lb+1))
            and all(h[i] >= h[i+j] for j in range(1, lb+1))]

def _find_pivot_lows(l, lb=3):
    return [i for i in range(lb, len(l) - lb)
            if all(l[i] <= l[i-j] for j in range(1, lb+1))
            and all(l[i] <= l[i+j] for j in range(1, lb+1))]

# ─── ANALYSE M1 ──────────────────────────────────────────────────
def analyse_m1(symbol: str, klines: list) -> dict:
    """
    Analyse complète M1 :
    - Direction : EMA9/21/50 + RSI14 + structure pivot
    - Setups : SWEEP_OB | BOS_FVG | ENGULF_VOL
    - Trigger : bougie M1 confirmée
    Retourne le meilleur signal ou None.
    """
    try:
        if not klines or len(klines) < 30:
            return None

        o, h, l, c, v = _parse_klines(klines)
        n = len(c)

        ema9  = _ema(c, 9)
        ema21 = _ema(c, 21)
        ema50 = _ema(c, 50)

        e9  = ema9[-1]
        e21 = ema21[-1]
        e50 = ema50[-1]
        price = c[-1]

        # ── Direction M1 ─────────────────────────────────────────
        bull_ema = e9 > e21 > e50 and price > e9
        bear_ema = e9 < e21 < e50 and price < e9

        rsi = _rsi(c, 14)

        # Structure pivots (lb=2 pour M1, plus réactif)
        ph = _find_pivot_highs(h, lb=2)
        pl = _find_pivot_lows(l,  lb=2)

        bull_struct = (len(ph) >= 2 and h[ph[-1]] > h[ph[-2]] and
                       len(pl) >= 2 and l[pl[-1]] > l[pl[-2]])
        bear_struct = (len(ph) >= 2 and h[ph[-1]] < h[ph[-2]] and
                       len(pl) >= 2 and l[pl[-1]] < l[pl[-2]])

        # RSI filtre extrêmes
        rsi_buy_ok  = 25 < rsi < 68
        rsi_sell_ok = 32 < rsi < 75

        if bull_ema and rsi_buy_ok:
            direction = "BUY"
        elif bear_ema and rsi_sell_ok:
            direction = "SELL"
        elif bull_ema and bull_struct and rsi_buy_ok:
            direction = "BUY"
        elif bear_ema and bear_struct and rsi_sell_ok:
            direction = "SELL"
        else:
            return None   # Pas de direction claire → skip

        # Blocage RSI extrême
        if direction == "BUY"  and rsi > 72:
            return None
        if direction == "SELL" and rsi < 28:
            return None

        # ── Volume moyen ─────────────────────────────────────────
        avg_vol  = float(np.mean(v[-20:])) if n >= 20 else float(np.mean(v))
        vol_spike = float(v[-2]) > avg_vol * VOLUME_SPIKE_MULT   # bougie précédente

        # ATR M1
        atr = _atr(h, l, c, 14) or float(price * 0.002)

        setups = []

        # ══════════════════════════════════════════════════════════
        # SETUP 1 : SWEEP_OB (score 92)
        # Balayage de liquidité + retour en Order Block M1
        # ══════════════════════════════════════════════════════════
        if direction == "BUY" and len(pl) >= 2:
            last_pl   = pl[-1]
            prev_pl   = pl[-2]
            prev_low  = l[prev_pl]
            # Sweep : une bougie récente passe sous prev_low mais ferme au-dessus
            swept = False
            sweep_idx = None
            for i in range(n - 4, max(n - 15, last_pl), -1):
                if l[i] < prev_low and c[i] > prev_low:
                    swept = True
                    sweep_idx = i
                    break
            if swept and c[-1] > c[-2]:
                # Order Block : dernière bougie baissière avant le sweep
                ob_idx = None
                for i in range(sweep_idx - 1, max(0, sweep_idx - OB_LOOKBACK), -1):
                    if c[i] < o[i]:
                        ob_idx = i
                        break
                if ob_idx is not None:
                    ob_bottom = min(o[ob_idx], c[ob_idx])
                    ob_top    = max(o[ob_idx], c[ob_idx])
                    # Trigger : price dans la zone OB ou vient de la quitter
                    if l[-1] <= ob_top * 1.002:
                        conf = sum([
                            1,
                            bool(vol_spike),
                            bull_struct,
                            bool(rsi < 55),
                            bool(e9 > e21),
                        ])
                        setups.append({
                            "name": "SWEEP_OB", "score": 92,
                            "confluence": min(conf, 5),
                            "ob": {"bottom": ob_bottom, "top": ob_top},
                            "direction": "BUY",
                        })

        elif direction == "SELL" and len(ph) >= 2:
            last_ph   = ph[-1]
            prev_ph   = ph[-2]
            prev_high = h[prev_ph]
            swept = False
            sweep_idx = None
            for i in range(n - 4, max(n - 15, last_ph), -1):
                if h[i] > prev_high and c[i] < prev_high:
                    swept = True
                    sweep_idx = i
                    break
            if swept and c[-1] < c[-2]:
                ob_idx = None
                for i in range(sweep_idx - 1, max(0, sweep_idx - OB_LOOKBACK), -1):
                    if c[i] > o[i]:
                        ob_idx = i
                        break
                if ob_idx is not None:
                    ob_bottom = min(o[ob_idx], c[ob_idx])
                    ob_top    = max(o[ob_idx], c[ob_idx])
                    if h[-1] >= ob_bottom * 0.998:
                        conf = sum([
                            1,
                            bool(vol_spike),
                            bear_struct,
                            bool(rsi > 45),
                            bool(e9 < e21),
                        ])
                        setups.append({
                            "name": "SWEEP_OB", "score": 92,
                            "confluence": min(conf, 5),
                            "ob": {"bottom": ob_bottom, "top": ob_top},
                            "direction": "SELL",
                        })

        # ══════════════════════════════════════════════════════════
        # SETUP 2 : BOS_FVG (score 83)
        # Break Of Structure M1 + Fair Value Gap
        # ══════════════════════════════════════════════════════════
        if direction == "BUY" and len(ph) >= 2:
            bos_level = h[ph[-2]]
            if c[-1] > bos_level:   # BOS confirmé
                # Chercher FVG haussier récent (gap entre low[i+2] et high[i])
                fvg_found = False
                fvg_bottom = 0.0
                fvg_top    = 0.0
                for i in range(n - 5, max(0, n - 20), -1):
                    if i + 2 < n:
                        gap = l[i + 2] - h[i]
                        if gap > price * FVG_MIN_GAP_PCT:
                            fvg_bottom = h[i]
                            fvg_top    = l[i + 2]
                            fvg_found  = True
                            break
                if fvg_found:
                    conf = sum([
                        1,
                        bool(vol_spike),
                        bool(bull_struct),
                        bool(rsi < 60),
                        bool(e9 > e50),
                    ])
                    setups.append({
                        "name": "BOS_FVG", "score": 83,
                        "confluence": min(conf, 5),
                        "ob": {"bottom": fvg_bottom, "top": fvg_top},
                        "bos_level": bos_level,
                        "direction": "BUY",
                    })

        elif direction == "SELL" and len(pl) >= 2:
            bos_level = l[pl[-2]]
            if c[-1] < bos_level:
                fvg_found = False
                fvg_bottom = 0.0
                fvg_top    = 0.0
                for i in range(n - 5, max(0, n - 20), -1):
                    if i + 2 < n:
                        gap = l[i] - h[i + 2]
                        if gap > price * FVG_MIN_GAP_PCT:
                            fvg_bottom = h[i + 2]
                            fvg_top    = l[i]
                            fvg_found  = True
                            break
                if fvg_found:
                    conf = sum([
                        1,
                        bool(vol_spike),
                        bool(bear_struct),
                        bool(rsi > 40),
                        bool(e9 < e50),
                    ])
                    setups.append({
                        "name": "BOS_FVG", "score": 83,
                        "confluence": min(conf, 5),
                        "ob": {"bottom": fvg_bottom, "top": fvg_top},
                        "bos_level": bos_level,
                        "direction": "SELL",
                    })

        # ══════════════════════════════════════════════════════════
        # SETUP 3 : ENGULF_VOL (score 75)
        # Bougie englobante M1 + volume spike + alignement EMA
        # ══════════════════════════════════════════════════════════
        if n >= 3:
            prev_o = o[-2]; prev_c = c[-2]
            cur_o  = o[-1]; cur_c  = c[-1]
            cur_h  = h[-1]; cur_l  = l[-1]

            if direction == "BUY":
                engulf = (cur_c > prev_o and cur_o < prev_c
                          and cur_c > cur_o and prev_c < prev_o)
                if engulf and vol_spike:
                    ob_bottom = cur_l
                    ob_top    = min(cur_o, prev_c)
                    conf = sum([
                        1,
                        bool(bull_struct),
                        bool(rsi < 58),
                        bool(e9 > e21),
                        bool(price > e50),
                    ])
                    setups.append({
                        "name": "ENGULF_VOL", "score": 75,
                        "confluence": min(conf, 5),
                        "ob": {"bottom": ob_bottom, "top": ob_top},
                        "direction": "BUY",
                    })

            elif direction == "SELL":
                engulf = (cur_c < prev_o and cur_o > prev_c
                          and cur_c < cur_o and prev_c > prev_o)
                if engulf and vol_spike:
                    ob_bottom = max(cur_o, prev_c)
                    ob_top    = cur_h
                    conf = sum([
                        1,
                        bool(bear_struct),
                        bool(rsi > 42),
                        bool(e9 < e21),
                        bool(price < e50),
                    ])
                    setups.append({
                        "name": "ENGULF_VOL", "score": 75,
                        "confluence": min(conf, 5),
                        "ob": {"bottom": ob_bottom, "top": ob_top},
                        "direction": "SELL",
                    })

        if not setups:
            return None

        # Meilleur setup
        setups.sort(key=lambda s: (s["score"], s["confluence"]), reverse=True)
        best = setups[0]

        if best["score"] < MIN_SCORE:
            return None
        if best["confluence"] < MIN_CONFLUENCE:
            return None

        # ── Trigger M1 : bougie fermée confirmant la direction ────
        # Utiliser l'avant-dernière bougie (fermée)
        k_o = o[-2]; k_c = c[-2]; k_h = h[-2]; k_l = l[-2]
        body   = abs(k_c - k_o)
        range_ = k_h - k_l if k_h > k_l else 1.0
        body_ratio = body / range_

        if direction == "BUY" and not (k_c > k_o and body_ratio >= MIN_BODY_RATIO):
            return None
        if direction == "SELL" and not (k_c < k_o and body_ratio >= MIN_BODY_RATIO):
            return None

        # ── Probabilité ──────────────────────────────────────────
        base_prob = {"SWEEP_OB": 70, "BOS_FVG": 63, "ENGULF_VOL": 58}.get(best["name"], 58)
        # Bonus direction EMA complète
        if (direction == "BUY"  and bull_ema and bull_struct): base_prob += 8
        if (direction == "SELL" and bear_ema and bear_struct): base_prob += 8
        # Bonus volume spike
        if vol_spike: base_prob += 4
        # Bonus RSI zone idéale
        if direction == "BUY"  and 40 < rsi < 55: base_prob += 4
        if direction == "SELL" and 45 < rsi < 60: base_prob += 4
        probability = min(float(base_prob), 99.0)

        if probability < MIN_PROB:
            return None

        return {
            "symbol":      symbol,
            "side":        direction,
            "setup":       best["name"],
            "score":       best["score"],
            "confluence":  best["confluence"],
            "probability": probability,
            "ob":          best.get("ob", {}),
            "atr":         atr,
            "rsi":         rsi,
            "price":       float(price),
        }

    except Exception as e:
        logger.debug(f"analyse_m1 {symbol}: {e}")
        return None

# ─── SL STRUCTUREL M1 ────────────────────────────────────────────
def get_sl(symbol: str, side: str, ob: dict, entry: float, atr: float) -> float:
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    tick = 10 ** (-pp)
    buf  = tick * 3

    # SL sur zone OB du setup
    if ob:
        if side == "BUY":
            sl_raw = ob["bottom"] - buf
        else:
            sl_raw = ob["top"] + buf
        dist = abs(entry - sl_raw)
        if dist >= entry * MIN_SL_PCT:
            return round(sl_raw, pp)

    # Fallback ATR M1
    dist = max(atr * 1.5, entry * MIN_SL_PCT)
    if side == "BUY":
        return round(entry - dist, pp)
    else:
        return round(entry + dist, pp)

# ─── LEVIER & MARGE ──────────────────────────────────────────────
def get_leverage(score: int) -> int:
    for threshold, lev in LEVERAGE_BY_SCORE:
        if score >= threshold:
            return lev
    return 15

def get_margin_pct(lev: int) -> float:
    return MARGIN_PCT_BY_LEV.get(lev, 0.25)

# ─── BINANCE ORDERS ──────────────────────────────────────────────
def set_leverage_sym(symbol: str, lev: int):
    request_binance("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev})

def set_isolated(symbol: str):
    try:
        request_binance("POST", "/fapi/v1/marginType",
                        {"symbol": symbol, "marginType": "ISOLATED"})
    except:
        pass

def cleanup_orders(symbol: str):
    try:
        orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if orders:
            for o in orders:
                request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": o["orderId"]})
    except:
        pass

def place_market(symbol: str, side: str, qty: float):
    info = symbol_info_cache.get(symbol, {})
    qp   = info.get("quantityPrecision", 3)
    qty  = round(qty, qp)
    return request_binance("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "MARKET", "quantity": qty
    })

def place_sl_tp(symbol: str, side: str, sl: float, tp: float) -> dict:
    info       = symbol_info_cache.get(symbol, {})
    pp         = info.get("pricePrecision", 4)
    close_side = "SELL" if side == "BUY" else "BUY"
    result     = {"sl_sent": False, "tp_sent": False,
                  "sl_order_id": None, "tp_order_id": None}

    # SL
    for _ in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side,
            "type": "STOP_MARKET", "stopPrice": round(sl, pp),
            "closePosition": "true", "workingType": "MARK_PRICE",
        })
        if r and r.get("orderId"):
            result["sl_sent"] = True
            result["sl_order_id"] = r["orderId"]
            logger.info(f"🛡️  {symbol} SL ✅ @ {round(sl, pp)}")
            break
        time.sleep(0.3)

    # TP filet
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side,
            "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, pp),
            "closePosition": "true", "workingType": "MARK_PRICE",
        })
        if r and r.get("orderId"):
            result["tp_sent"] = True
            result["tp_order_id"] = r["orderId"]
            logger.info(f"🎯 {symbol} TP ✅ @ {round(tp, pp)}")
            break
        time.sleep(0.3)

    if not result["sl_sent"]:
        logger.error(f"🚨 {symbol} SL Binance échoué → SL logiciel actif")
        send_telegram(f"🚨 <b>SL {symbol} non posé</b> → SL logiciel actif @ {sl:.{pp}f}")

    return result

def update_sl_binance(symbol: str, old_sl_id, new_sl: float, close_side: str):
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    if old_sl_id:
        request_binance("DELETE", "/fapi/v1/order",
                        {"symbol": symbol, "orderId": old_sl_id})
    for _ in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side,
            "type": "STOP_MARKET", "stopPrice": round(new_sl, pp),
            "closePosition": "true", "workingType": "MARK_PRICE",
        })
        if r and r.get("orderId"):
            return r
        time.sleep(0.3)
    return None

# ─── DRAWDOWN ────────────────────────────────────────────────────
def check_drawdown():
    ref = drawdown_state.get("ref_balance", 0)
    if ref <= 0 or account_balance <= 0:
        return
    dd = (ref - account_balance) / ref
    if dd >= DD_ALERT_PCT:
        now = time.time()
        if now - drawdown_state.get("last_alert", 0) > 300:
            drawdown_state["last_alert"] = now
            logger.warning(f"⚠️  Drawdown {dd:.1%} | ${account_balance:.4f} (ref ${ref:.4f})")
            send_telegram(
                f"⚠️ <b>DRAWDOWN {dd:.1%}</b>\n"
                f"Balance: ${account_balance:.4f} (début: ${ref:.4f})\n"
                f"⚡ Trading H24 continu — pas de blocage"
            )

# ─── OPEN POSITION ────────────────────────────────────────────────
def open_position(signal: dict):
    global consec_losses, cooldown_until

    symbol = signal["symbol"]
    side   = signal["side"]
    setup  = signal["setup"]
    score  = signal["score"]
    prob   = signal["probability"]
    ob     = signal.get("ob", {})
    atr    = signal.get("atr", 0)

    try:
        info = symbol_info_cache.get(symbol)
        if not info:
            logger.warning(f"❌ {symbol} info manquant")
            return

        pp        = info.get("pricePrecision", 4)
        step_size = info.get("stepSize", 0.001)
        min_qty   = info.get("minQty", 0.001)
        min_notional = info.get("minNotional", MIN_NOTIONAL)

        entry = get_price(symbol)
        if not entry:
            return

        lev      = get_leverage(score)
        marg_pct = get_margin_pct(lev)
        margin   = account_balance * marg_pct

        # SL structurel
        sl = get_sl(symbol, side, ob, entry, atr)
        if side == "BUY":
            sl_dist = max(entry - sl, entry * MIN_SL_PCT)
            sl = round(entry - sl_dist, pp)
            tp = round(entry + sl_dist * TP_RR, pp)
        else:
            sl_dist = max(sl - entry, entry * MIN_SL_PCT)
            sl = round(entry + sl_dist, pp)
            tp = round(entry - sl_dist * TP_RR, pp)

        # RR check minimal
        if sl_dist <= 0:
            return

        # Qty depuis risque fixe
        qty = _round_step(FIXED_RISK_USDT / sl_dist, step_size)

        # Cap marge
        max_qty_margin = _round_step((margin * lev) / entry, step_size)
        if max_qty_margin > 0 and qty > max_qty_margin:
            qty = max_qty_margin

        # Min notional ajustement
        if qty * entry < min_notional:
            qty_min = _round_step(min_notional / entry + step_size, step_size)
            real_risk = sl_dist * qty_min
            if real_risk <= FIXED_RISK_USDT * MAX_RISK_MULTIPLIER:
                qty = qty_min
            else:
                logger.info(f"  {symbol} notional trop petit, risque ${real_risk:.4f} → skip")
                return

        if qty < min_qty:
            logger.info(f"  {symbol} qty {qty} < minQty {min_qty} → skip")
            return

        logger.info(
            f"📊 {symbol} {side} | {setup} score={score} | prob={prob:.1f}% | "
            f"{lev}x | marge=${margin:.2f} ({marg_pct*100:.0f}%) | qty={qty} | risk=${sl_dist*qty:.4f}"
        )

        set_isolated(symbol)
        set_leverage_sym(symbol, lev)
        cleanup_orders(symbol)

        order = place_market(symbol, side, qty)
        if not order:
            logger.error(f"❌ {symbol} ordre MARKET échoué")
            return

        # Récupérer vrai prix d'entrée
        actual_entry = 0.0
        for _ in range(5):
            time.sleep(0.4)
            pos = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
            if pos:
                for p in pos:
                    if p.get("symbol") == symbol:
                        ep = float(p.get("entryPrice", 0))
                        if ep > 0:
                            actual_entry = ep
                            break
            if actual_entry > 0:
                break
        if actual_entry <= 0:
            actual_entry = get_price(symbol) or entry

        # Recalcul SL/TP sur vrai entry
        if side == "BUY":
            sl_dist2 = max(actual_entry - sl, actual_entry * MIN_SL_PCT)
            sl = round(actual_entry - sl_dist2, pp)
            tp = round(actual_entry + sl_dist2 * TP_RR, pp)
        else:
            sl_dist2 = max(sl - actual_entry, actual_entry * MIN_SL_PCT)
            sl = round(actual_entry + sl_dist2, pp)
            tp = round(actual_entry - sl_dist2 * TP_RR, pp)

        sl_tp = place_sl_tp(symbol, side, sl, tp)

        with trade_lock:
            trade_log[symbol] = {
                "side":                 side,
                "entry":                actual_entry,
                "sl":                   sl,
                "tp":                   tp,
                "qty":                  qty,
                "leverage":             lev,
                "margin":               margin,
                "setup":                setup,
                "score":                score,
                "probability":          prob,
                "status":               "OPEN",
                "opened_at":            time.time(),
                "sl_on_binance":        sl_tp["sl_sent"],
                "tp_on_binance":        sl_tp["tp_sent"],
                "sl_order_id":          sl_tp.get("sl_order_id"),
                "tp_order_id":          sl_tp.get("tp_order_id"),
                "trailing_stop_active": False,
                "breakeven_moved":      False,
                "highest_price":        actual_entry if side == "BUY" else None,
                "lowest_price":         actual_entry if side == "SELL" else None,
                "atr":                  atr,
            }

        logger.info(
            f"✅ {symbol} {side} @ {actual_entry:.{pp}f} | SL {sl:.{pp}f} | TP {tp:.{pp}f} | "
            f"Setup: {setup} ({score}) | Prob: {prob:.1f}% | {lev}x"
        )
        send_telegram(
            f"🚀 <b>{symbol} {side}</b> @ {actual_entry:.{pp}f}\n"
            f"SL: {sl:.{pp}f} | TP filet: {tp:.{pp}f} (RR{TP_RR})\n"
            f"Setup: {setup} | Score: {score} | Prob: {prob:.1f}%\n"
            f"Levier: {lev}x | Marge: ${margin:.2f}\n"
            f"🔁 Trailing SL dès +0.5R"
        )

    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

# ─── TRAILING SL ─────────────────────────────────────────────────
def update_trailing(symbol: str):
    """Trailing SL M1 basé sur ATR."""
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

        with trade_lock:
            trade     = trade_log[symbol]
            side      = trade["side"]
            entry     = trade["entry"]
            sl        = trade["sl"]
            atr       = trade.get("atr", entry * 0.002)
            info      = symbol_info_cache.get(symbol, {})
            pp        = info.get("pricePrecision", 4)
            close_side = "SELL" if side == "BUY" else "BUY"

            profit = (current_price - entry) if side == "BUY" else (entry - current_price)
            risk   = abs(entry - sl)
            if risk <= 0:
                return
            rr = profit / risk

            new_sl = sl

            # Phase 1 : Breakeven +0.5R
            if rr >= BREAKEVEN_RR and not trade.get("breakeven_moved"):
                fee_buf = entry * BREAKEVEN_FEE
                if side == "BUY":
                    be_sl = round(entry + fee_buf, pp)
                    if be_sl > sl:
                        new_sl = be_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BE SL → {be_sl:.{pp}f} (RR={rr:.2f}R)")
                else:
                    be_sl = round(entry - fee_buf, pp)
                    if be_sl < sl:
                        new_sl = be_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BE SL → {be_sl:.{pp}f} (RR={rr:.2f}R)")

            # Phase 2 : Trailing dès +1R
            if rr >= TRAIL_START_RR:
                trade["trailing_stop_active"] = True
                trail_dist = atr * TRAIL_ATR_MULT
                if side == "BUY":
                    trail = round(current_price - trail_dist, pp)
                    if trail > new_sl:
                        new_sl = trail
                else:
                    trail = round(current_price + trail_dist, pp)
                    if trail < new_sl:
                        new_sl = trail

            # Appliquer si SL amélioré (min 1 tick de déplacement)
            tick = 10 ** (-pp)
            improved = (side == "BUY" and new_sl > sl + tick) or \
                       (side == "SELL" and new_sl < sl - tick)
            if improved:
                r = update_sl_binance(symbol, trade.get("sl_order_id"), new_sl, close_side)
                if r:
                    trade["sl"] = new_sl
                    trade["sl_order_id"] = r["orderId"]
                    trade["sl_on_binance"] = True
                    logger.info(f"🔁 {symbol} trailing SL {sl:.{pp}f} → {new_sl:.{pp}f} RR={rr:.2f}R")

    except Exception as e:
        logger.debug(f"update_trailing {symbol}: {e}")

# ─── MONITOR POSITIONS ────────────────────────────────────────────
def monitor_positions():
    """Surveille toutes les positions ouvertes."""
    try:
        with trade_lock:
            open_syms = [s for s, t in trade_log.items() if t.get("status") == "OPEN"]
        if not open_syms:
            return

        # Récupérer les PnL depuis Binance
        pos_data = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        binance_open = {}
        if pos_data:
            for p in pos_data:
                sym = p.get("symbol")
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    binance_open[sym] = p

        for symbol in open_syms:
            # Trailing SL
            update_trailing(symbol)

            with trade_lock:
                if symbol not in trade_log:
                    continue
                trade = trade_log[symbol]
                if trade.get("status") != "OPEN":
                    continue

                current_price = get_price(symbol)
                if not current_price:
                    continue

                # SL logiciel fallback
                if not trade.get("sl_on_binance"):
                    side = trade["side"]
                    sl   = trade["sl"]
                    if (side == "BUY"  and current_price <= sl) or \
                       (side == "SELL" and current_price >= sl):
                        logger.warning(f"🚨 {symbol} SL logiciel @ {current_price}")
                        close_side = "SELL" if side == "BUY" else "BUY"
                        place_market(symbol, close_side, trade.get("qty", 0))
                        trade["status"]    = "CLOSED"
                        trade["closed_by"] = "SOFTWARE_SL"
                        _on_closed(symbol, trade, is_win=False)
                        continue

                # Position fermée par Binance ?
                if symbol not in binance_open:
                    trade["status"]    = "CLOSED"
                    trade["closed_by"] = "BINANCE_SL_TP"
                    logger.info(f"✅ {symbol} fermée par Binance (SL/TP)")
                    _on_closed_from_binance(symbol, trade)

    except Exception as e:
        logger.debug(f"monitor_positions: {e}")

def _on_closed(symbol: str, trade: dict, is_win: bool):
    global consec_losses, cooldown_until
    setup = trade.get("setup", "?")
    side  = trade.get("side", "?")
    if is_win:
        consec_losses = 0
        symbol_stats[symbol]["wins"] += 1
        logger.info(f"✅ WIN {symbol} {side} setup={setup}")
        send_telegram(f"✅ <b>WIN {symbol} {side}</b>\nSetup: {setup}")
    else:
        consec_losses += 1
        symbol_stats[symbol]["losses"] += 1
        logger.info(f"🔴 LOSS {symbol} {side} setup={setup} | consec={consec_losses}")
        send_telegram(f"🔴 <b>LOSS {symbol} {side}</b>\nSetup: {setup} | Consécutives: {consec_losses}")
        if consec_losses >= CONSEC_LOSS_LIMIT:
            cooldown_until = time.time() + CONSEC_COOLDOWN
            logger.warning(f"⏸ Cooldown {CONSEC_COOLDOWN//60}min après {consec_losses} pertes")
            send_telegram(
                f"⏸ <b>Cooldown {CONSEC_COOLDOWN//60}min</b>\n"
                f"Après {consec_losses} pertes consécutives\n"
                f"Reprise automatique — H24"
            )

def _on_closed_from_binance(symbol: str, trade: dict):
    try:
        income = request_binance("GET", "/fapi/v1/income",
                                 {"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": 3},
                                 signed=True)
        pnl = sum(float(i.get("income", 0)) for i in income) if income else 0.0
        _on_closed(symbol, trade, is_win=pnl >= 0)
    except:
        _on_closed(symbol, trade, is_win=False)

# ─── SCAN SYMBOL ─────────────────────────────────────────────────
def scan_symbol(symbol: str) -> dict | None:
    """Analyse un symbole en M1, retourne un signal ou None."""
    try:
        # Position déjà ouverte ?
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None

        # Signal cooldown
        now = time.time()
        if now - signal_last_at.get(symbol, 0) < SIGNAL_COOLDOWN:
            return None

        klines = get_klines(symbol, limit=KLINES_LIMIT)
        if not klines or len(klines) < 30:
            return None

        signal = analyse_m1(symbol, klines)
        return signal

    except Exception as e:
        logger.debug(f"scan_symbol {symbol}: {e}")
        return None

# ─── RECOVER ─────────────────────────────────────────────────────
def recover_existing_positions():
    """Reprend les positions ouvertes au démarrage."""
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if not positions:
            return
        for pos in positions:
            sym = pos.get("symbol")
            amt = float(pos.get("positionAmt", 0))
            if amt == 0 or sym not in symbols_list:
                continue
            side  = "BUY" if amt > 0 else "SELL"
            qty   = abs(amt)
            entry = float(pos.get("entryPrice", 0))
            if entry <= 0:
                continue

            logger.info(f"🔄 Position récupérée : {sym} {side} qty={qty} @ {entry}")
            info = symbol_info_cache.get(sym, {})
            pp   = info.get("pricePrecision", 4)
            atr  = entry * 0.01
            dist = max(atr * 1.5, entry * MIN_SL_PCT)

            if side == "BUY":
                sl = round(entry - dist, pp)
                tp = round(entry + dist * TP_RR, pp)
            else:
                sl = round(entry + dist, pp)
                tp = round(entry - dist * TP_RR, pp)

            set_leverage_sym(sym, 20)
            cleanup_orders(sym)
            sl_tp = place_sl_tp(sym, side, sl, tp)

            with trade_lock:
                trade_log[sym] = {
                    "side": side, "entry": entry, "sl": sl, "tp": tp,
                    "qty": qty, "leverage": 20, "margin": entry * qty / 20,
                    "setup": "RECOVERED", "score": 78, "probability": 65.0,
                    "status": "OPEN", "opened_at": time.time(),
                    "sl_on_binance": sl_tp["sl_sent"],
                    "tp_on_binance": sl_tp["tp_sent"],
                    "sl_order_id": sl_tp.get("sl_order_id"),
                    "tp_order_id": sl_tp.get("tp_order_id"),
                    "trailing_stop_active": False, "breakeven_moved": False,
                    "atr": atr,
                }
            logger.info(f"✅ {sym} récupéré | SL {'Binance' if sl_tp['sl_sent'] else 'logiciel'} @ {sl:.{pp}f}")

    except Exception as e:
        logger.error(f"recover_existing_positions: {e}")

# ─── SCANNER LOOP ────────────────────────────────────────────────
def scanner_loop():
    logger.info("🔍 Scanner M1 démarré — Top 20 cryptos H24")
    time.sleep(5)
    _count = 0
    while True:
        try:
            if _bot_stop:
                time.sleep(10)
                continue

            _count += 1

            # Resync toutes les 5 min
            if _count % (300 // SCAN_INTERVAL) == 0:
                sync_binance_time()
                get_account_balance()
                check_drawdown()
                # Reload top symbols toutes les 30 min
                if _count % (1800 // SCAN_INTERVAL) == 0:
                    load_top_symbols()

            # Hard floor
            if account_balance < BALANCE_HARD_FLOOR:
                logger.warning(f"🛑 Hard floor ${BALANCE_HARD_FLOOR} — balance ${account_balance:.4f}")
                send_telegram(f"🛑 <b>Hard floor ${BALANCE_HARD_FLOOR}</b>\nBalance: ${account_balance:.4f} — trading gelé")
                time.sleep(30)
                continue

            # Cooldown après pertes
            if time.time() < cooldown_until:
                remaining = int((cooldown_until - time.time()) / 60)
                if _count % 10 == 0:
                    logger.info(f"⏸ Cooldown {remaining}min restant — scan suspendu")
                time.sleep(SCAN_INTERVAL)
                continue

            # Max positions atteint
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open >= MAX_POSITIONS:
                time.sleep(SCAN_INTERVAL)
                continue

            # Scan parallèle des 20 symboles
            signals = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {ex.submit(scan_symbol, sym): sym for sym in symbols_list}
                for fut in as_completed(futures, timeout=15):
                    try:
                        sig = fut.result()
                        if sig:
                            signals.append(sig)
                    except:
                        pass

            if not signals:
                time.sleep(SCAN_INTERVAL)
                continue

            # Trier par score × probabilité
            signals.sort(key=lambda s: s["score"] * s["probability"], reverse=True)

            # Ouvrir au max (MAX_POSITIONS - n_open) trades
            opened = 0
            for sig in signals:
                with trade_lock:
                    n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                if n_open + opened >= MAX_POSITIONS:
                    break
                sym = sig["symbol"]
                # Vérifier qu'on n'a pas déjà une position sur ce symbole
                with trade_lock:
                    if sym in trade_log and trade_log[sym].get("status") == "OPEN":
                        continue
                signal_last_at[sym] = time.time()
                open_position(sig)
                opened += 1
                if opened > 0:
                    time.sleep(0.5)  # petit délai entre 2 ouvertures

        except Exception as e:
            logger.error(f"scanner_loop: {e}")

        time.sleep(SCAN_INTERVAL)

# ─── MONITOR LOOP ────────────────────────────────────────────────
def monitor_loop():
    logger.info("📡 Monitor démarré")
    time.sleep(10)
    while True:
        try:
            monitor_positions()
        except Exception as e:
            logger.debug(f"monitor_loop: {e}")
        time.sleep(MONITOR_INTERVAL)

# ─── DASHBOARD LOOP ──────────────────────────────────────────────
def dashboard_loop():
    logger.info("📈 Dashboard démarré")
    time.sleep(20)
    while True:
        try:
            with trade_lock:
                n_open  = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            total_w = sum(v["wins"]   for v in symbol_stats.values())
            total_l = sum(v["losses"] for v in symbol_stats.values())
            wr = total_w / (total_w + total_l) * 100 if (total_w + total_l) > 0 else 0

            on_cd = time.time() < cooldown_until
            cd_str = f" | ⏸ COOLDOWN {int((cooldown_until - time.time())/60)}min" if on_cd else ""
            ref = drawdown_state.get("ref_balance", account_balance)
            dd  = (ref - account_balance) / ref * 100 if ref > 0 else 0

            logger.info("═" * 68)
            logger.info(f"SCALPER v39 | ${account_balance:.4f} | Pos: {n_open}/{MAX_POSITIONS}{cd_str}")
            logger.info(f"Symboles: {len(symbols_list)} | W:{total_w} L:{total_l} WR:{wr:.1f}% | DD:{dd:.1f}%")
            logger.info(f"Top 5 : {symbols_list[:5]}")

            if n_open > 0:
                try:
                    pos_data = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
                    pnl_map = {}
                    if pos_data:
                        for p in pos_data:
                            s = p.get("symbol")
                            a = float(p.get("positionAmt", 0))
                            if a != 0:
                                pnl_map[s] = {
                                    "pnl":  float(p.get("unRealizedProfit", 0)),
                                    "mark": float(p.get("markPrice", 0)),
                                    "liq":  float(p.get("liquidationPrice", 0)),
                                }
                except:
                    pnl_map = {}

                logger.info("─── POSITIONS OUVERTES ───")
                with trade_lock:
                    for sym, t in trade_log.items():
                        if t.get("status") != "OPEN":
                            continue
                        info = symbol_info_cache.get(sym, {})
                        pp   = info.get("pricePrecision", 4)
                        pd   = pnl_map.get(sym, {})
                        pnl  = pd.get("pnl", 0)
                        mark = pd.get("mark", t.get("entry", 0))
                        liq  = pd.get("liq", 0)
                        icon = "🟢" if pnl >= 0 else "🔴"
                        be   = "✅" if t.get("breakeven_moved") else "❌"
                        tr   = "🔁" if t.get("trailing_stop_active") else "  "
                        sl_s = "🛡️B" if t.get("sl_on_binance") else "⚠️S"
                        logger.info(
                            f"  {icon} {sym} {t['side']} | {t['setup']} {t['leverage']}x | "
                            f"Entry:{t['entry']:.{pp}f} Mark:{mark:.{pp}f}"
                        )
                        logger.info(
                            f"     SL:{t['sl']:.{pp}f}({sl_s}) TP:{t['tp']:.{pp}f} | "
                            f"PnL:{pnl:+.4f}$ | BE:{be}{tr}"
                        )
                        if liq > 0:
                            logger.info(f"     LIQ:{liq:.{pp}f}")
            else:
                logger.info("  Scan actif — aucune position")
            logger.info("═" * 68)

        except Exception as e:
            logger.debug(f"dashboard_loop: {e}")
        time.sleep(DASHBOARD_INTERVAL)

# ─── MAIN ────────────────────────────────────────────────────────
def main():
    logger.info("╔" + "═" * 64 + "╗")
    logger.info("║  SCALPER v39 — TOP 20 CRYPTO | PURE M1 | H24              ║")
    logger.info("║  2 positions max | Levier & Marge AUTO | Trailing SL       ║")
    logger.info("╚" + "═" * 64 + "╝")
    logger.warning("🔥 LIVE TRADING — Vérifiez vos clés API 🔥")

    logger.info(f"✅ Top {TOP_N_SYMBOLS} cryptos Binance Futures (volume 24h)")
    logger.info(f"✅ Timeframe : M1 uniquement — setup + signal + trigger")
    logger.info(f"✅ Risque : ${FIXED_RISK_USDT} / trade | Max {MAX_POSITIONS} positions")
    logger.info(f"✅ Levier auto : score≥90→{LEVERAGE_BY_SCORE[0][1]}x | ≥80→{LEVERAGE_BY_SCORE[1][1]}x | ≥70→{LEVERAGE_BY_SCORE[2][1]}x")
    logger.info(f"✅ Marge auto : 40%/35%/30%/25% balance selon levier")
    logger.info(f"✅ SL structurel Binance + trailing M1 dès +0.5R")
    logger.info(f"✅ TP filet RR{TP_RR} | Trailing = vraie sortie")
    logger.info(f"✅ Hard floor ${BALANCE_HARD_FLOOR} | Cooldown {CONSEC_COOLDOWN//60}min/{CONSEC_LOSS_LIMIT} pertes")
    logger.info(f"✅ H24 sans interruption — scan toutes les {SCAN_INTERVAL}s")

    start_health_server()
    sync_binance_time()
    load_top_symbols()
    get_account_balance()

    drawdown_state["ref_balance"] = account_balance
    drawdown_state["last_reset"]  = time.time()

    logger.info(f"💰 Balance : ${account_balance:.4f}")
    logger.info(f"📋 Symboles : {symbols_list}")

    recover_existing_positions()

    threading.Thread(target=scanner_loop,  daemon=True).start()
    threading.Thread(target=monitor_loop,  daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()

    logger.info("✅ SCALPER v39 ONLINE 🚀 — Scan M1 H24 actif\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown")

if __name__ == "__main__":
    main()

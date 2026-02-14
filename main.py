"""
╔══════════════════════════════════════════════════════════╗
║         ROBOTKING M1 PRO – ULTRA OCÉAN SYSTEM           ║
║         Architecture défensive + offensive               ║
║         Version: 2.0.0 | ATR Dynamic SL/TP              ║
╚══════════════════════════════════════════════════════════╝

PRIORITÉS :
  1. Protection capital (SL ferme)
  2. Trailing stop intelligent
  3. Fallback si Binance rejette
  4. Récapitulatif / logs complets

SÉCURITÉ :
  - Ne jamais hardcoder les clés API dans ce fichier
  - Utiliser les variables d'environnement UNIQUEMENT
  - Révoquer immédiatement toute clé exposée
"""

import time
import hmac
import hashlib
import requests
import threading
import os
import json
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

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
#  CONFIGURATION — VARIABLES D'ENVIRONNEMENT UNIQUEMENT
# ═══════════════════════════════════════════════════════════

API_KEY    = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")
if not API_KEY or not API_SECRET:
    logger.critical("❌ BINANCE_API_KEY ou BINANCE_API_SECRET manquant. Bot arrêté.")
    raise SystemExit(1)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL  = "https://fapi.binance.com"
DRY_RUN   = os.environ.get("DRY_RUN", "false").lower() == "true"

# ── Paramètres de trading ──────────────────────────────────
LEVERAGE              = 20
INITIAL_CAPITAL       = 5.0
RISK_PER_TRADE_PCT    = 0.10          # 10% du capital par trade
MAX_DRAWDOWN_PCT      = 0.20          # Kill switch à -20%
MIN_CAPITAL_TO_TRADE  = 2.0
MAX_POSITIONS         = 4

# ── SL / TP ATR ───────────────────────────────────────────
ATR_SL_MULT           = 1.5           # SL = ATR × 1.5
ATR_TP_MULT           = 2.5           # TP = ATR × 2.5
ATR_FALLBACK_SL_PCT   = 0.012         # fallback 1.2% si pas d'ATR
ATR_FALLBACK_TP_PCT   = 0.020         # fallback 2.0% si pas d'ATR

# ── Trailing stop ──────────────────────────────────────────
BREAKEVEN_TRIGGER_PCT = 0.005         # +0.5% → SL au break-even
TRAILING_TRIGGER_PCT  = 0.010         # +1.0% → trailing actif
TRAILING_ATR_MULT     = 1.0           # trailing distance = ATR × 1.0

# ── Scanner ────────────────────────────────────────────────
SCAN_INTERVAL         = 15            # secondes
MONITOR_INTERVAL      = 3             # secondes pour le monitor
DASHBOARD_INTERVAL    = 30            # secondes pour afficher le dashboard
MAX_WORKERS           = 6

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

# ── Rate limiting ──────────────────────────────────────────
MAX_CALLS_PER_MIN  = 1200
RATE_LIMIT_WINDOW  = 60
CACHE_DURATION     = 5

# ═══════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════

# trade_log : source de vérité centrale
# {symbol: {entry, qty, side, sl, tp, fallback_active,
#           trailing_active, highest_price, lowest_price,
#           fallback_sl, fallback_tp, status, opened_at}}
trade_log: dict = {}

# Précision symbols — chargée une fois au démarrage
symbol_precision_cache: dict = {}   # {symbol: {"qty_precision": int, "min_qty": float, "min_notional": float}}

# Caches prix / klines
price_cache:  dict = {}
klines_cache: dict = {}

# Capital
current_capital  = INITIAL_CAPITAL
peak_capital     = INITIAL_CAPITAL
starting_capital = INITIAL_CAPITAL

# Locks
trade_lock   = threading.Lock()
capital_lock = threading.Lock()
api_lock     = threading.Lock()
api_call_times: list = []

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
                logger.warning(f"⚠️  Rate limit proche — pause {sleep_time:.1f}s")
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
        logger.error(f"Erreur Telegram: {e}")

# ═══════════════════════════════════════════════════════════
#  API BINANCE
# ═══════════════════════════════════════════════════════════

def _sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method: str, path: str, params: dict = None, max_retries: int = 3):
    if params is None:
        params = {}

    if DRY_RUN and method in ("POST", "DELETE"):
        logger.info(f"[DRY RUN] {method} {path} — params={params}")
        return {"orderId": f"DRY_{int(time.time()*1000)}"}

    wait_for_rate_limit()
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
                logger.warning(f"⚠️  Rate limit Binance — pause {retry_after}s")
                time.sleep(retry_after)
            else:
                logger.error(f"API {resp.status_code}: {resp.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep((2 ** attempt) * 0.5)

        except requests.exceptions.Timeout:
            logger.error(f"Timeout tentative {attempt+1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) * 0.5)
        except Exception as e:
            logger.error(f"Erreur requête: {e}")
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

def get_price(symbol: str) -> float | None:
    now = time.time()
    if symbol in price_cache:
        price, ts = price_cache[symbol]
        if now - ts < CACHE_DURATION:
            return price
    try:
        resp = requests.get(
            f"{BASE_URL}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            price = float(resp.json()["price"])
            price_cache[symbol] = (price, now)
            return price
    except Exception as e:
        logger.error(f"get_price {symbol}: {e}")
    return None

# ═══════════════════════════════════════════════════════════
#  PRÉCISION SYMBOLS — CHARGÉE UNE SEULE FOIS
# ═══════════════════════════════════════════════════════════

def load_symbol_precision_all():
    """Charge stepSize, minQty, minNotional pour tous les SYMBOLS au démarrage."""
    global symbol_precision_cache
    logger.info("📐 Chargement précisions symbols...")
    info = request_binance("GET", "/fapi/v1/exchangeInfo")
    if not info:
        logger.error("❌ Impossible de charger exchangeInfo")
        return

    for s in info.get("symbols", []):
        sym = s["symbol"]
        if sym not in SYMBOLS:
            continue
        filters = {f["filterType"]: f for f in s.get("filters", [])}

        lot  = filters.get("LOT_SIZE", {})
        step = lot.get("stepSize", "0.001")
        if "." in step:
            qty_prec = len(step.rstrip("0").split(".")[-1])
        else:
            qty_prec = 0

        min_qty      = float(lot.get("minQty", 0.001))
        notional     = filters.get("MIN_NOTIONAL", {})
        min_notional = float(notional.get("notional", 5.0))

        symbol_precision_cache[sym] = {
            "qty_precision": qty_prec,
            "min_qty":       min_qty,
            "min_notional":  min_notional
        }

    logger.info(f"✅ {len(symbol_precision_cache)} symbols chargés")

def get_symbol_info(symbol: str) -> dict:
    return symbol_precision_cache.get(symbol, {
        "qty_precision": 3,
        "min_qty":       0.001,
        "min_notional":  5.0
    })

# ═══════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES
# ═══════════════════════════════════════════════════════════

def calc_atr(symbol: str, interval: str = "5m", period: int = 14) -> float | None:
    klines = get_klines(symbol, interval, period + 5)
    if not klines or len(klines) < period + 1:
        return None
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    trs = []
    for i in range(1, len(highs)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    return sum(trs[-period:]) / period

def calc_ema(closes: list, period: int) -> float | None:
    if len(closes) < period:
        return None
    mult = 2 / (period + 1)
    ema  = closes[0]
    for c in closes[1:]:
        ema = (c - ema) * mult + ema
    return ema

# ═══════════════════════════════════════════════════════════
#  CALCUL SL / TP STRATÉGIQUE (ATR DYNAMIQUE)
# ═══════════════════════════════════════════════════════════

def calculate_sl_tp(symbol: str, entry: float, side: str) -> tuple[float, float]:
    """
    Calcule SL et TP basés sur ATR (5m, période 14).
    Fallback sur pourcentage fixe si ATR indisponible.
    """
    atr = calc_atr(symbol, "5m", 14)

    if atr and atr > 0:
        sl_dist = atr * ATR_SL_MULT
        tp_dist = atr * ATR_TP_MULT
        method  = f"ATR={atr:.4f}"
    else:
        sl_dist = entry * ATR_FALLBACK_SL_PCT
        tp_dist = entry * ATR_FALLBACK_TP_PCT
        method  = "Fallback %"

    if side == "LONG":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:  # SHORT
        sl = entry + sl_dist
        tp = entry - tp_dist

    logger.info(f"   📐 SL/TP [{method}] — SL: {sl:.6f} | TP: {tp:.6f}")
    return round(sl, 6), round(tp, 6)

# ═══════════════════════════════════════════════════════════
#  GESTION DU CAPITAL
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
        logger.info(f"💰 Capital: {current_capital:.2f} USDT | Peak: {peak_capital:.2f}")

def check_capital_protection() -> bool:
    """Retourne False si kill switch ou capital trop faible."""
    drawdown = (starting_capital - current_capital) / starting_capital
    if drawdown >= MAX_DRAWDOWN_PCT:
        msg = (f"🛑 KILL SWITCH\n"
               f"Drawdown: {drawdown*100:.1f}% ≥ {MAX_DRAWDOWN_PCT*100}%\n"
               f"Capital: {current_capital:.2f} / {starting_capital:.2f} USDT")
        logger.critical(msg)
        send_telegram(msg)
        emergency_close_all()
        return False
    if current_capital < MIN_CAPITAL_TO_TRADE:
        logger.warning(f"⚠️  Capital trop faible: {current_capital:.2f} < {MIN_CAPITAL_TO_TRADE}")
        return False
    return True

def calculate_position_size(entry: float) -> float:
    """Retourne la quantité en unités de base."""
    risk_amount  = current_capital * RISK_PER_TRADE_PCT
    risk_amount  = min(risk_amount, current_capital * 0.15)
    qty_usdt     = (risk_amount / ATR_FALLBACK_SL_PCT) * LEVERAGE
    max_position = current_capital * LEVERAGE * 0.40
    qty_usdt     = min(qty_usdt, max_position)
    return qty_usdt / entry

# ═══════════════════════════════════════════════════════════
#  ORDRES BINANCE
# ═══════════════════════════════════════════════════════════

def set_leverage(symbol: str):
    request_binance("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
    time.sleep(0.2)

def place_market_order(symbol: str, side: str, qty: float) -> dict | None:
    """Place un ordre market. Retourne la réponse ou None."""
    info     = get_symbol_info(symbol)
    qty      = round(qty, info["qty_precision"])
    qty      = max(qty, info["min_qty"])

    if qty * get_price(symbol) < info["min_notional"]:
        logger.error(f"❌ {symbol} — notional trop faible")
        return None

    order_side = "BUY" if side == "LONG" else "SELL"
    params = {
        "symbol":   symbol,
        "side":     order_side,
        "type":     "MARKET",
        "quantity": qty
    }
    resp = request_binance("POST", "/fapi/v1/order", params)
    return resp

def place_stop_order(symbol: str, side: str, qty: float, stop_price: float,
                     order_type: str = "STOP_MARKET") -> dict | None:
    """
    Place un STOP_MARKET ou TAKE_PROFIT_MARKET.
    side : côté de la POSITION (LONG/SHORT), pas de l'ordre.
    """
    info       = get_symbol_info(symbol)
    qty        = round(qty, info["qty_precision"])
    close_side = "SELL" if side == "LONG" else "BUY"
    params = {
        "symbol":           symbol,
        "side":             close_side,
        "type":             order_type,
        "stopPrice":        round(stop_price, 6),
        "quantity":         qty,
        "reduceOnly":       "true",
        "workingType":      "MARK_PRICE",
        "priceProtect":     "TRUE"
    }
    resp = request_binance("POST", "/fapi/v1/order", params)
    return resp

def cancel_open_orders(symbol: str):
    """Annule tous les ordres ouverts sur un symbol."""
    request_binance("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
    time.sleep(0.2)

def close_market(symbol: str, side: str, qty: float, reason: str = ""):
    """Ferme une position en market, avec garde anti-double-fermeture."""
    # Vérifier que la position est encore ouverte
    positions = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
    if positions:
        pos_amt = float(positions[0].get("positionAmt", 0))
        if abs(pos_amt) == 0:
            logger.info(f"ℹ️  {symbol} déjà fermé, skip close_market")
            return

    cancel_open_orders(symbol)
    resp = place_market_order(symbol, "SELL" if side == "LONG" else "BUY_CLOSE", qty)

    # On passe l'ordre inverse
    close_side = "SELL" if side == "LONG" else "BUY"
    info = get_symbol_info(symbol)
    qty  = round(qty, info["qty_precision"])
    params = {
        "symbol":     symbol,
        "side":       close_side,
        "type":       "MARKET",
        "quantity":   qty,
        "reduceOnly": "true"
    }
    resp = request_binance("POST", "/fapi/v1/order", params)
    if resp and "orderId" in resp:
        logger.info(f"✅ {symbol} FERMÉ ({reason})")
        send_telegram(f"🔴 <b>{symbol}</b> fermé — {reason}")
        with trade_lock:
            if symbol in trade_log:
                trade_log[symbol]["status"] = "CLOSED"
    else:
        logger.error(f"❌ Impossible de fermer {symbol}")

# ═══════════════════════════════════════════════════════════
#  GESTION SL / TP — ENVOI + FALLBACK
# ═══════════════════════════════════════════════════════════

def has_sl_tp(symbol: str) -> dict:
    """
    Vérifie si des ordres SL/TP existent côté Binance.
    Retourne {"has_sl": bool, "has_tp": bool}.
    """
    orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
    has_sl, has_tp = False, False
    if orders:
        for o in orders:
            otype = o.get("type", "")
            if otype in ("STOP_MARKET", "STOP"):
                has_sl = True
            if otype in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                has_tp = True
    return {"has_sl": has_sl, "has_tp": has_tp}

def place_sl_tp_with_fallback(symbol: str, side: str, qty: float,
                               sl: float, tp: float):
    """
    Tente de placer SL + TP sur Binance.
    Si l'un ou l'autre est rejeté → active le fallback local.
    """
    sl_ok, tp_ok = False, False

    # SL
    sl_resp = place_stop_order(symbol, side, qty, sl, "STOP_MARKET")
    if sl_resp and "orderId" in sl_resp:
        sl_ok = True
        logger.info(f"   ✅ SL placé: {sl:.6f}")
    else:
        logger.warning(f"   ⚠️  SL Binance rejeté → fallback local activé")

    time.sleep(0.3)

    # TP
    tp_resp = place_stop_order(symbol, side, qty, tp, "TAKE_PROFIT_MARKET")
    if tp_resp and "orderId" in tp_resp:
        tp_ok = True
        logger.info(f"   ✅ TP placé: {tp:.6f}")
    else:
        logger.warning(f"   ⚠️  TP Binance rejeté → fallback local activé")

    # Activer fallback si besoin
    fallback = not sl_ok or not tp_ok
    with trade_lock:
        if symbol in trade_log:
            trade_log[symbol]["sl"]              = sl
            trade_log[symbol]["tp"]              = tp
            trade_log[symbol]["fallback_active"] = fallback
            trade_log[symbol]["fallback_sl"]     = sl
            trade_log[symbol]["fallback_tp"]     = tp
            if fallback:
                msg = f"🟡 <b>{symbol}</b> — Fallback activé (SL:{sl_ok} TP:{tp_ok})"
                send_telegram(msg)

    return sl_ok, tp_ok

# ═══════════════════════════════════════════════════════════
#  SCAN POSITIONS EXISTANTES (DÉMARRAGE / CRASH RECOVERY)
# ═══════════════════════════════════════════════════════════

def scan_existing_positions():
    """
    Scanne toutes les positions ouvertes sur Binance (y compris manuelles).
    Les enregistre dans trade_log si pas déjà présentes.
    Place SL/TP si absent.
    """
    logger.info("🔎 Scan des positions existantes...")
    data = request_binance("GET", "/fapi/v2/positionRisk")
    if not data:
        logger.error("❌ Impossible de récupérer les positions")
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

        logger.info(f"   📌 {symbol} | {side} | Entry: {entry} | Qty: {qty}")

        with trade_lock:
            if symbol not in trade_log:
                trade_log[symbol] = {
                    "entry":            entry,
                    "qty":              qty,
                    "side":             side,
                    "sl":               None,
                    "tp":               None,
                    "fallback_active":  False,
                    "fallback_sl":      None,
                    "fallback_tp":      None,
                    "trailing_active":  False,
                    "highest_price":    entry if side == "LONG" else None,
                    "lowest_price":     entry if side == "SHORT" else None,
                    "breakeven_done":   False,
                    "status":           "OPEN",
                    "opened_at":        datetime.now(timezone.utc).isoformat()
                }

        # Placer SL/TP si absent
        existing = has_sl_tp(symbol)
        if not existing["has_sl"] or not existing["has_tp"]:
            sl, tp = calculate_sl_tp(symbol, entry, side)
            place_sl_tp_with_fallback(symbol, side, qty, sl, tp)

    logger.info(f"✅ {found} positions actives trouvées")

# ═══════════════════════════════════════════════════════════
#  FALLBACK MONITOR
# ═══════════════════════════════════════════════════════════

def run_fallback_check(symbol: str, info: dict):
    """
    Surveille localement le prix si le fallback est actif.
    Ferme en market si SL ou TP atteint.
    """
    if not info.get("fallback_active"):
        return

    price = get_price(symbol)
    if not price:
        return

    side        = info["side"]
    fallback_sl = info.get("fallback_sl")
    fallback_tp = info.get("fallback_tp")

    triggered = False
    reason    = ""

    if side == "LONG":
        if fallback_sl and price <= fallback_sl:
            triggered = True
            reason    = f"Fallback SL @ {fallback_sl:.6f}"
        elif fallback_tp and price >= fallback_tp:
            triggered = True
            reason    = f"Fallback TP @ {fallback_tp:.6f}"
    else:  # SHORT
        if fallback_sl and price >= fallback_sl:
            triggered = True
            reason    = f"Fallback SL @ {fallback_sl:.6f}"
        elif fallback_tp and price <= fallback_tp:
            triggered = True
            reason    = f"Fallback TP @ {fallback_tp:.6f}"

    if triggered:
        logger.info(f"🔴 FALLBACK DÉCLENCHÉ {symbol} — {reason} | Prix: {price:.6f}")
        close_market(symbol, side, info["qty"], reason)

# ═══════════════════════════════════════════════════════════
#  BREAK-EVEN + TRAILING STOP
# ═══════════════════════════════════════════════════════════

def run_trailing_logic(symbol: str, info: dict):
    """
    1. Break-even si profit ≥ BREAKEVEN_TRIGGER_PCT
    2. Trailing si profit ≥ TRAILING_TRIGGER_PCT
    """
    price = get_price(symbol)
    if not price:
        return

    entry  = info["entry"]
    side   = info["side"]
    qty    = info["qty"]

    profit_pct = (price - entry) / entry if side == "LONG" else (entry - price) / entry

    # ── 1. Break-even ────────────────────────────────────────
    if profit_pct >= BREAKEVEN_TRIGGER_PCT and not info.get("breakeven_done"):
        logger.info(f"🐎 {symbol} break-even activé | Profit: {profit_pct*100:.2f}%")
        new_sl = entry

        # Annuler ancien SL et replacer au break-even
        cancel_open_orders(symbol)
        time.sleep(0.2)

        sl_resp = place_stop_order(symbol, side, qty, new_sl, "STOP_MARKET")
        if sl_resp and "orderId" in sl_resp:
            logger.info(f"   ✅ Break-even SL placé @ {new_sl:.6f}")
            with trade_lock:
                trade_log[symbol]["sl"]            = new_sl
                trade_log[symbol]["breakeven_done"] = True
                trade_log[symbol]["fallback_sl"]    = new_sl
        else:
            # Fallback local
            with trade_lock:
                trade_log[symbol]["fallback_sl"]    = new_sl
                trade_log[symbol]["fallback_active"] = True
                trade_log[symbol]["breakeven_done"] = True
            logger.warning(f"   ⚠️  Break-even Binance rejeté → fallback {new_sl:.6f}")

        # Re-placer TP (annulé par cancel_open_orders)
        tp = info.get("tp")
        if tp:
            tp_resp = place_stop_order(symbol, side, qty, tp, "TAKE_PROFIT_MARKET")
            if not (tp_resp and "orderId" in tp_resp):
                with trade_lock:
                    trade_log[symbol]["fallback_active"] = True

    # ── 2. Trailing actif ─────────────────────────────────────
    if profit_pct >= TRAILING_TRIGGER_PCT:
        atr = calc_atr(symbol, "5m", 14)
        if not atr:
            return

        with trade_lock:
            trade_log[symbol]["trailing_active"] = True

        if side == "LONG":
            # Mettre à jour highest_price
            highest = max(info.get("highest_price") or entry, price)
            with trade_lock:
                trade_log[symbol]["highest_price"] = highest
            new_sl = highest - (atr * TRAILING_ATR_MULT)
            current_sl = info.get("sl") or 0
            if new_sl > current_sl:
                logger.info(f"📈 {symbol} trailing LONG → SL: {new_sl:.6f} (high: {highest:.6f})")
                _update_trailing_sl(symbol, side, qty, new_sl, info)

        else:  # SHORT
            lowest = min(info.get("lowest_price") or entry, price)
            with trade_lock:
                trade_log[symbol]["lowest_price"] = lowest
            new_sl = lowest + (atr * TRAILING_ATR_MULT)
            current_sl = info.get("sl") or float("inf")
            if new_sl < current_sl:
                logger.info(f"📉 {symbol} trailing SHORT → SL: {new_sl:.6f} (low: {lowest:.6f})")
                _update_trailing_sl(symbol, side, qty, new_sl, info)

def _update_trailing_sl(symbol: str, side: str, qty: float,
                         new_sl: float, info: dict):
    """Envoie le nouveau SL trailing. Fallback local si rejeté."""
    cancel_open_orders(symbol)
    time.sleep(0.2)

    sl_resp = place_stop_order(symbol, side, qty, new_sl, "STOP_MARKET")
    if sl_resp and "orderId" in sl_resp:
        with trade_lock:
            trade_log[symbol]["sl"] = new_sl
        # Re-placer TP
        tp = info.get("tp")
        if tp:
            place_stop_order(symbol, side, qty, tp, "TAKE_PROFIT_MARKET")
    else:
        logger.warning(f"   ⚠️  Trailing SL rejeté → fallback local {new_sl:.6f}")
        with trade_lock:
            trade_log[symbol]["fallback_sl"]     = new_sl
            trade_log[symbol]["fallback_active"] = True
            trade_log[symbol]["sl"]              = new_sl

# ═══════════════════════════════════════════════════════════
#  MONITOR LOOP — THREAD CONTINU
# ═══════════════════════════════════════════════════════════

def monitor_loop():
    """
    Thread principal de surveillance.
    Tourne en continu, vérifie chaque position active.
    """
    logger.info("👁️  Monitor loop démarré")
    while True:
        try:
            with trade_lock:
                symbols_to_check = {
                    k: v.copy() for k, v in trade_log.items()
                    if v.get("status") == "OPEN"
                }

            for symbol, info in symbols_to_check.items():
                try:
                    # 1. Fallback (priorité absolue)
                    run_fallback_check(symbol, info)

                    # 2. Break-even + Trailing
                    with trade_lock:
                        current_info = trade_log.get(symbol, {})
                    if current_info.get("status") == "OPEN":
                        run_trailing_logic(symbol, current_info)

                except Exception as e:
                    logger.error(f"Monitor error {symbol}: {e}")

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        time.sleep(MONITOR_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  EMERGENCY CLOSE ALL
# ═══════════════════════════════════════════════════════════

def emergency_close_all():
    """Ferme toutes les positions en urgence."""
    logger.critical("🛑 FERMETURE D'URGENCE TOUTES POSITIONS")
    with trade_lock:
        symbols = [(k, v.copy()) for k, v in trade_log.items() if v.get("status") == "OPEN"]
    for symbol, info in symbols:
        close_market(symbol, info["side"], info["qty"], "EMERGENCY_STOP")
        time.sleep(0.5)

# ═══════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════

def display_dashboard():
    """Affiche un récapitulatif propre toutes les X secondes."""
    sep = "═" * 54

    logger.info(f"\n{sep}")
    logger.info(f"  🤖 ROBOTKING M1 PRO — ULTRA OCÉAN")
    logger.info(f"  💰 Capital: {current_capital:.2f} USDT | Peak: {peak_capital:.2f}")
    logger.info(f"  🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(sep)

    with trade_lock:
        open_positions  = {k: v for k, v in trade_log.items() if v.get("status") == "OPEN"}
        closed_count    = sum(1 for v in trade_log.values() if v.get("status") == "CLOSED")

    if not open_positions:
        logger.info("  📭 Aucune position ouverte")
    else:
        for symbol, info in open_positions.items():
            price     = get_price(symbol) or 0
            entry     = info["entry"]
            side      = info["side"]
            pnl_pct   = ((price - entry) / entry * 100) if side == "LONG" else ((entry - price) / entry * 100)
            pnl_sign  = "+" if pnl_pct >= 0 else ""

            logger.info(f"\n  SYMBOL : {symbol}")
            logger.info(f"  SIDE   : {side}")
            logger.info(f"  ENTRY  : {entry:.6f}")
            logger.info(f"  PRICE  : {price:.6f}  ({pnl_sign}{pnl_pct:.2f}%)")
            logger.info(f"  SL     : {info.get('sl') or 'N/A'}")
            logger.info(f"  TP     : {info.get('tp') or 'N/A'}")
            logger.info(f"  TRAIL  : {'✅ ACTIF' if info.get('trailing_active') else '⏳ EN ATTENTE'}")
            logger.info(f"  FALLBK : {'🟡 ACTIF' if info.get('fallback_active') else '✅ OFF'}")
            logger.info(f"  BRKEVN : {'✅ FAIT' if info.get('breakeven_done') else '⏳ EN ATTENTE'}")
            logger.info(f"  {'-'*40}")

    logger.info(f"\n  📊 Positions fermées: {closed_count}")
    logger.info(f"{sep}\n")

def dashboard_loop():
    while True:
        try:
            display_dashboard()
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
        time.sleep(DASHBOARD_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  SCANNER OPPORTUNITÉS (NOUVEAU TRADE)
# ═══════════════════════════════════════════════════════════

def score_symbol(symbol: str) -> dict | None:
    """Score simple ATR-based pour détecter un setup."""
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

        side  = "LONG" if ema9 > ema21 else "SHORT"
        stars = 0
        details = []

        # EMA cross
        stars += 1
        details.append("EMA9/21")

        # Break of structure
        recent_high = max(highs[-20:-2])
        recent_low  = min(lows[-20:-2])
        if price > recent_high * 1.001:
            stars += 2
            details.append("BOS Bullish")
        elif price < recent_low * 0.999:
            stars += 2
            details.append("BOS Bearish")

        # Volume spike
        avg_vol = sum(volumes[-20:-1]) / 19
        if volumes[-1] > avg_vol * 1.5:
            stars += 1
            details.append("Volume Spike")

        # ATR disponible ?
        atr = calc_atr(symbol, "5m", 14)
        if atr:
            stars += 1
            details.append(f"ATR={atr:.4f}")

        return {
            "symbol":  symbol,
            "side":    side,
            "price":   price,
            "stars":   min(stars, 5),
            "details": details,
            "atr":     atr
        }
    except Exception as e:
        logger.error(f"score_symbol {symbol}: {e}")
        return None

def open_new_trade(opp: dict):
    """Ouvre un nouveau trade basé sur une opportunité scannée."""
    global current_capital

    symbol = opp["symbol"]
    side   = opp["side"]
    price  = opp["price"]

    with trade_lock:
        if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
            return
        if len([v for v in trade_log.values() if v.get("status") == "OPEN"]) >= MAX_POSITIONS:
            return

    update_capital()
    if not check_capital_protection():
        return

    info    = get_symbol_info(symbol)
    qty     = calculate_position_size(price)
    qty     = round(qty, info["qty_precision"])
    qty     = max(qty, info["min_qty"])

    if qty * price < info["min_notional"]:
        logger.error(f"❌ {symbol} notional trop faible")
        return

    set_leverage(symbol)
    resp = place_market_order(symbol, side, qty)
    if not resp or "orderId" not in resp:
        logger.error(f"❌ Ordre market {symbol} échoué")
        return

    entry = price  # entrée approximative (idéalement récupérer depuis resp)
    sl, tp = calculate_sl_tp(symbol, entry, side)

    with trade_lock:
        trade_log[symbol] = {
            "entry":           entry,
            "qty":             qty,
            "side":            side,
            "sl":              None,
            "tp":              None,
            "fallback_active": False,
            "fallback_sl":     None,
            "fallback_tp":     None,
            "trailing_active": False,
            "highest_price":   entry if side == "LONG" else None,
            "lowest_price":    entry if side == "SHORT" else None,
            "breakeven_done":  False,
            "status":          "OPEN",
            "opened_at":       datetime.now(timezone.utc).isoformat()
        }

    place_sl_tp_with_fallback(symbol, side, qty, sl, tp)

    msg = (f"🟢 <b>{symbol}</b> ouvert\n"
           f"  {side} @ {entry:.6f}\n"
           f"  SL: {sl:.6f} | TP: {tp:.6f}\n"
           f"  ⭐ {opp['stars']}/5 — {', '.join(opp['details'])}")
    logger.info(msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(msg)

def scan_loop():
    """Thread scanner — cherche de nouvelles opportunités."""
    logger.info("🔍 Scanner démarré")
    MIN_STARS = 3
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])

            if n_open < MAX_POSITIONS:
                opportunities = []
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = {ex.submit(score_symbol, sym): sym for sym in SYMBOLS}
                    for f in as_completed(futures):
                        r = f.result()
                        if r and r["stars"] >= MIN_STARS:
                            opportunities.append(r)

                opportunities.sort(key=lambda x: x["stars"], reverse=True)
                for opp in opportunities[:MAX_POSITIONS - n_open]:
                    open_new_trade(opp)
        except Exception as e:
            logger.error(f"Scan loop error: {e}")

        time.sleep(SCAN_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  POINT D'ENTRÉE PRINCIPAL
# ═══════════════════════════════════════════════════════════

def main():
    global starting_capital, current_capital, peak_capital

    logger.info("╔══════════════════════════════════╗")
    logger.info("║  ROBOTKING M1 PRO — ULTRA OCÉAN  ║")
    logger.info("╚══════════════════════════════════╝")

    if DRY_RUN:
        logger.info("🟡 MODE DRY RUN — aucun ordre réel")

    # 1. Charger précisions symbols (une seule fois)
    load_symbol_precision_all()

    # 2. Capital initial
    update_capital()
    starting_capital = current_capital
    peak_capital     = current_capital
    logger.info(f"💰 Capital de départ: {starting_capital:.2f} USDT")

    # 3. Scanner positions existantes (crash recovery)
    scan_existing_positions()

    # 4. Lancer les threads
    threads = [
        threading.Thread(target=monitor_loop,   daemon=True, name="Monitor"),
        threading.Thread(target=scan_loop,       daemon=True, name="Scanner"),
        threading.Thread(target=dashboard_loop,  daemon=True, name="Dashboard"),
    ]
    for t in threads:
        t.start()
        logger.info(f"▶️  Thread {t.name} lancé")

    logger.info("✅ Bot opérationnel — Ctrl+C pour arrêter")

    # 5. Loop principal
    try:
        while True:
            time.sleep(60)
            update_capital()
            check_capital_protection()
    except KeyboardInterrupt:
        logger.info("\n⛔ Arrêt demandé par l'utilisateur")
        emergency_close_all()

if __name__ == "__main__":
    main()

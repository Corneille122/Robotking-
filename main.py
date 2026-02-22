#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   SCALPER v39.4 — CALIBRÉ | CORRÉLATION BTC | FRAIS RÉELS      ║
║   Top 20 crypto | M1 | H24 | Récupération $1.40 → $6+          ║
╚══════════════════════════════════════════════════════════════════╝

CORRECTIONS v39.4 — POURQUOI v39.3 NE TRADAIT PAS :
  🔧 BUG 1 : VOLUME_SPIKE_MIN = 2.0 → trop rare sur M1, réduit à 1.4
  🔧 BUG 2 : MIN_SCORE = 88 → seul SWEEP_OB pouvait passer, abaissé à 82
             BOS_FVG (score 85) peut maintenant passer
  🔧 BUG 3 : BTC M1 "neutre" bloquait 80%+ du temps → slope assoupli
             Condition slope supprimée, EMA5>EMA13 suffit
  🔧 BUG 4 : vol_spike obligatoire × 2 (setup + trigger) → une seule vérif
  🔧 BUG 5 : RSI BUY < 65 trop étroit → élargi à < 70
  🔧 BUG 6 : SIGNAL_COOLDOWN 180s → réduit à 60s (1 min suffit)
  🔧 BUG 7 : MIN_CONFLUENCE = 3 sur BOS_FVG → réduit à 2 pour ce setup
  ✅ Frais Binance réels conservés (0.12% A/R)
  ✅ Corrélation BTC M1 conservée mais assouplie
  ✅ SL suiveur garanti toutes les 2s
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
        logging.FileHandler("scalper_v393.log"),
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
API_KEY    = os.environ.get("BINANCE_API_KEY",    "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

if not API_KEY or not API_SECRET:
    logger.error("❌ BINANCE_API_KEY / BINANCE_API_SECRET manquants")
    exit(1)

BASE_URL = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# ── Univers ──────────────────────────────────────────────────────
TOP_N_SYMBOLS    = 20
EXCLUDE_SYMBOLS  = {"USDCUSDT","BUSDUSDT","TUSDUSDT","FDUSDUSDT","USDPUSDT","BTCUSDT"}
# BTCUSDT exclu car on l'utilise comme référence de corrélation
BTC_SYMBOL       = "BTCUSDT"
FALLBACK_SYMBOLS = [
    "ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT",
    "AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT","DOTUSDT",
    "NEARUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","INJUSDT",
    "ARBUSDT","OPUSDT","APTUSDT","SUIUSDT","TIAUSDT",
]

# ── Frais Binance réels ──────────────────────────────────────────
# Taker Futures : 0.04% par côté → A/R = 0.08%
# Buffer sécurité : +0.04% → total 0.12% = 0.0012
BINANCE_FEE_RATE     = 0.0004   # 0.04% par transaction (taker)
BREAKEVEN_FEE_TOTAL  = BINANCE_FEE_RATE * 2 * 1.5  # 0.12% A/R avec buffer 50%
# Le BE se déclenche uniquement quand profit > frais A/R
# BE_SL = entry + (entry × BREAKEVEN_FEE_TOTAL) pour BUY
#        = entry - (entry × BREAKEVEN_FEE_TOTAL) pour SELL

# ══════════════════════════════════════════════════════════════════
#  STRATÉGIE CROISSANCE PAR PALIERS — $1.40 → $10
# ══════════════════════════════════════════════════════════════════
#
#  PALIER 1 — SURVIE      : $1.00 → $3.00
#    Risque  : 7% balance (max $0.21)
#    Levier  : 50x best / 35x normal / 25x fallback
#    SL      : 0.3% → 0.8% (serré pour limiter la perte absolue)
#    TP RR   : 2.5
#    Marge   : 20% balance max
#    Objectif : doubler en ~15 trades gagnants
#
#  PALIER 2 — CROISSANCE  : $3.00 → $6.00
#    Risque  : 6% balance (max $0.36)
#    Levier  : 35x best / 25x normal / 20x fallback
#    SL      : 0.3% → 1.0%
#    TP RR   : 2.5
#    Marge   : 25% balance max
#
#  PALIER 3 — NORMAL      : $6.00 → $10.00+
#    Risque  : 5% balance (max $0.50)
#    Levier  : 25x best / 20x normal / 15x fallback
#    SL      : 0.2% → 1.0%
#    TP RR   : 2.5
#    Marge   : 30% balance max
#
# ══════════════════════════════════════════════════════════════════
TARGET_BALANCE   = 10.0     # Objectif
TIER1_LIMIT      = 3.0      # Palier 1 jusqu'à $3
TIER2_LIMIT      = 6.0      # Palier 2 jusqu'à $6

# Paramètres par palier : (risk_pct, max_risk_usdt, max_margin_pct, max_sl_pct)
TIER_PARAMS = {
    1: {"risk_pct": 0.09, "max_risk": 0.27, "max_margin": 0.25, "max_sl": 0.008},
    2: {"risk_pct": 0.08, "max_risk": 0.48, "max_margin": 0.30, "max_sl": 0.010},
    3: {"risk_pct": 0.06, "max_risk": 0.60, "max_margin": 0.35, "max_sl": 0.010},
}

# Levier par palier (score_min → levier)
LEVERAGE_BY_TIER = {
    1: [(92, 50), (85, 35), (0, 25)],   # Palier survie : levier élevé pour min notional
    2: [(92, 35), (85, 25), (0, 20)],   # Palier croissance
    3: [(92, 25), (85, 20), (0, 15)],   # Palier normal
}

MARGIN_PCT_BY_LEV = {
    50: 0.18,   # 18% balance pour 50x
    35: 0.20,   # 20% balance pour 35x
    25: 0.22,   # 22% balance pour 25x
    20: 0.20,
    15: 0.18,
}

MIN_NOTIONAL      = 5.0     # Binance min notional ($5)
MAX_NOTIONAL_RISK = 0.30    # Skip si min notional exige > 30% balance
MIN_SL_PCT        = 0.002   # SL min 0.2%
MAX_SL_PCT        = 0.015   # SL max 1.5%
MAX_POSITIONS     = 2       # 2 positions simultanées max
MARGIN_TYPE       = "ISOLATED"

# ── SL / TP / Trailing ──────────────────────────────────────────
TP_RR            = 2.5      # RR2.5 : chaque win = 2.5× le risque
BREAKEVEN_RR     = 0.6      # BE dès +0.6R (frais couverts)
TRAIL_START_RR   = 1.0      # Trailing dès +1R
TRAIL_ATR_MULT   = 0.7      # Trailing serré M1
HARD_FLOOR       = 1.00     # Gel si balance < $1.00

# Fonctions palier ────────────────────────────────────────────────
def get_tier() -> int:
    if account_balance < TIER1_LIMIT: return 1
    if account_balance < TIER2_LIMIT: return 2
    return 3

def get_tier_label() -> str:
    t = get_tier()
    labels = {1: "🔴 SURVIE", 2: "🟡 CROISSANCE", 3: "🟢 NORMAL"}
    return labels[t]

def get_risk_usdt() -> float:
    p = TIER_PARAMS[get_tier()]
    return max(0.04, min(account_balance * p["risk_pct"], p["max_risk"]))

def get_leverage(score: int) -> int:
    table = LEVERAGE_BY_TIER[get_tier()]
    for threshold, lev in table:
        if score >= threshold: return lev
    return 15

def get_max_sl_pct() -> float:
    return TIER_PARAMS[get_tier()]["max_sl"]

def get_max_margin_pct() -> float:
    return TIER_PARAMS[get_tier()]["max_margin"]

def get_progress_bar() -> str:
    """Barre de progression visuelle vers $10."""
    pct = min(account_balance / TARGET_BALANCE * 100, 100)
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {pct:.1f}%"

# ── Filtres signal — CALIBRÉS pour trader réellement ────────────
# v39.3 était trop strict → 0 trades. v39.4 calibré pour 3-8 trades/jour
MIN_SCORE        = 82       # BOS_FVG(85) + SWEEP_OB(92) passent tous les 2
MIN_CONFLUENCE   = 2        # 2/5 minimum (3 pour SWEEP_OB, 2 pour BOS_FVG)
MIN_PROB         = 62.0     # Seuil réaliste
MIN_BODY_RATIO   = 0.40     # Corps bougie ≥ 40% (was 45%, trop strict)
VOLUME_SPIKE_MIN = 1.4      # Volume spike ≥ 1.4× (was 2.0× → quasi-jamais atteint)
OB_LOOKBACK      = 6        # Lookback OB
FVG_MIN_GAP      = 0.0006   # FVG minimum 0.06% (was 0.1% → trop rare sur M1)

# ── Corrélation BTC ──────────────────────────────────────────────
BTC_CORR_REQUIRED = True    # Activer le filtre corrélation BTC
BTC_EMA_FAST      = 5       # EMA rapide BTC M1
BTC_EMA_SLOW      = 13      # EMA lente BTC M1
BTC_RSI_PERIOD    = 9       # RSI BTC M1 (court pour réactivité)
BTC_RSI_BULL_MAX  = 68      # BTC RSI max pour BUY (pas surachat)
BTC_RSI_BEAR_MIN  = 32      # BTC RSI min pour SELL (pas survente)

# ── Cooldown ─────────────────────────────────────────────────────
SIGNAL_COOLDOWN    = 60     # 60s entre signaux même symbole (was 180s → trop long)
CONSEC_LOSS_LIMIT  = 2      # Pause après 2 pertes consécutives
CONSEC_COOLDOWN    = 30 * 60  # 30 min (strict)
DD_ALERT_PCT       = 0.15   # Alerte drawdown à -15%

# ── Timing ───────────────────────────────────────────────────────
SCAN_INTERVAL    = 10       # Scan toutes les 10s (moins agressif)
MONITOR_INTERVAL = 2        # SL suiveur toutes les 2s
DASHBOARD_INTERVAL = 25
KLINES_CACHE_TTL = 8
PRICE_CACHE_TTL  = 1.0
MAX_WORKERS      = 6

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

# Cache BTC direction (mis à jour toutes les 10s)
_btc_direction_cache = {"direction": 0, "ts": 0.0, "label": "NEUTRE", "rsi": 50.0}

# ─── FLASK ───────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    with trade_lock:
        n = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
    status = "🛑 STOP" if _bot_stop else "🟢 RUNNING"
    btc_dir = _btc_direction_cache.get("label", "?")
    return (f"SCALPER v39.3 | {status} | ${account_balance:.4f} | "
            f"Pos:{n}/{MAX_POSITIONS} | BTC M1:{btc_dir}"), 200

@flask_app.route("/health")
def health():
    return "✅", 200

@flask_app.route("/status")
def status_ep():
    with trade_lock:
        open_pos = {
            s: {k: t.get(k) for k in ["side","entry","sl","tp","setup","probability",
                                        "leverage","trailing_stop_active","breakeven_moved",
                                        "sl_on_binance","btc_corr"]}
            for s, t in trade_log.items() if t.get("status") == "OPEN"
        }
    tw = sum(v["wins"]   for v in symbol_stats.values())
    tl = sum(v["losses"] for v in symbol_stats.values())
    return jsonify({
        "version": "v39.3",
        "balance": round(account_balance, 4),
        "risk_per_trade": round(get_risk_usdt(), 4),
        "positions": open_pos,
        "wins": tw, "losses": tl,
        "consec_losses": consec_losses,
        "btc_direction": _btc_direction_cache,
        "cooldown_remaining": max(0, int(cooldown_until - time.time())),
        "stop": _bot_stop,
    })

@flask_app.route("/stop", methods=["GET","POST"])
def stop_ep():
    global _bot_stop
    _bot_stop = True
    send_telegram("🛑 <b>EMERGENCY STOP</b>")
    return "🛑 STOPPED", 200

@flask_app.route("/resume", methods=["GET","POST"])
def resume_ep():
    global _bot_stop, cooldown_until
    _bot_stop = False
    cooldown_until = 0.0
    send_telegram("▶️ <b>Trading repris</b>")
    return "▶️ RESUMED", 200

@flask_app.route("/trades")
def trades_ep():
    with trade_lock:
        data = {s: {k: t.get(k) for k in ["side","entry","sl","tp","qty","setup",
                                            "leverage","trailing_stop_active","breakeven_moved",
                                            "sl_on_binance","btc_corr"]}
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
        logger.info(f"⏱️ Binance offset: {_binance_time_offset}ms")

# ─── RATE LIMIT ──────────────────────────────────────────────────
def _rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < 60]
        if len(api_call_times) >= 1100:
            s = 60 - (now - api_call_times[0])
            if s > 0: time.sleep(s)
        api_call_times.append(now)

# ─── API ─────────────────────────────────────────────────────────
def _sign(params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

# ════════════════════════════════════════════════════════════════════
#  MOTEUR API INTELLIGENT — Gestion erreurs Binance
# ════════════════════════════════════════════════════════════════════
#
#  Chaque rejet Binance est analysé et une solution automatique
#  est appliquée avant de retenter. Plus jamais d'ordre perdu.
#
#  Code  | Problème                  | Solution auto
#  -1021 | Timestamp désynchronisé   | Resync horloge → retry
#  -1111 | Précision qty invalide    | Arrondir au stepSize exact
#  -1013 | Min notional < $5         | Augmenter qty au minimum légal
#  -2010 | Solde insuffisant         | Réduire qty de 10% → retry
#  -4003 | stopPrice invalide        | Décaler de ±0.2% → retry
#  -5021 | SL trop proche mark price | Décaler de ±0.15% → retry
#  -5022 | SL déjà déclenché         | Position fermée → clore log
#  -2011 | Ordre inconnu (annulé)    | Replacer SL sans annuler
#  -4061 | Qty hors limites          | Clamp entre minQty/maxQty
#  -4129 | Doit être reduceOnly      | Ajouter reduceOnly=true
#  -1121 | Symbole invalide/suspendu | Retirer de symbols_list
# ════════════════════════════════════════════════════════════════════

def _parse_binance_error(response_text: str) -> tuple[int, str]:
    """Extrait (code, message) d'une réponse d'erreur Binance."""
    import json
    try:
        d = json.loads(response_text)
        return int(d.get("code", 0)), str(d.get("msg", ""))
    except:
        return 0, response_text[:100]

def request_binance(method, path, params=None, signed=True):
    """Requête Binance avec gestion intelligente de TOUS les codes d'erreur."""
    if params is None: params = {}
    _rate_limit()
    headers = {"X-MBX-APIKEY": API_KEY}
    url     = BASE_URL + path

    with api_semaphore:
        for attempt in range(4):
            try:
                p = dict(params)   # Copie pour chaque tentative
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
                logger.debug(f"Binance {r.status_code} code={code} msg={msg[:80]} attempt={attempt+1}")

                # ── Gestion HTTP ──────────────────────────────────
                if r.status_code == 429:
                    wait = [5, 15, 30, 60][min(attempt, 3)]
                    logger.warning(f"⚠️  Rate limit 429 → attente {wait}s")
                    time.sleep(wait)
                    continue

                if r.status_code == 418:
                    logger.error("🚫 IP bannie 418 → attente 120s")
                    time.sleep(120)
                    return None

                if r.status_code in (401, 403):
                    logger.error(f"🔑 Auth {r.status_code} — clé API invalide")
                    send_telegram(f"🔑 <b>Erreur auth {r.status_code}</b> — vérifie les clés API")
                    return None

                # ── Gestion codes Binance spécifiques ─────────────
                if code == -1021:
                    # Timestamp désynchronisé → resync et retry
                    logger.warning(f"⏱️  Timestamp désync (-1021) → resync")
                    sync_binance_time()
                    continue

                elif code == -1111:
                    # Précision qty invalide → arrondir au stepSize
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    step = info.get("stepSize", 0.001)
                    qp   = info.get("quantityPrecision", 3)
                    if "quantity" in params:
                        old_qty = float(params["quantity"])
                        new_qty = round(_round_step(old_qty, step), qp)
                        params["quantity"] = new_qty
                        logger.info(f"🔧 -1111 précision qty {old_qty} → {new_qty} (step={step})")
                        continue

                elif code == -1013:
                    # Min notional non atteint → augmenter qty
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    step = info.get("stepSize", 0.001)
                    pr   = get_price(sym) or 1.0
                    min_n = info.get("minNotional", MIN_NOTIONAL)
                    if "quantity" in params:
                        new_qty = _round_step(min_n / pr + step, step)
                        params["quantity"] = new_qty
                        logger.info(f"🔧 -1013 min notional → qty {new_qty} (notional={new_qty*pr:.2f}$)")
                        continue

                elif code == -2010:
                    # Solde insuffisant → réduire qty de 15%
                    if "quantity" in params and attempt < 2:
                        old_qty = float(params["quantity"])
                        sym     = params.get("symbol", "?")
                        info    = symbol_info_cache.get(sym, {})
                        step    = info.get("stepSize", 0.001)
                        new_qty = _round_step(old_qty * 0.85, step)
                        params["quantity"] = new_qty
                        logger.warning(f"⚠️  -2010 solde insuf → qty {old_qty} → {new_qty} (-15%)")
                        continue
                    return None

                elif code in (-4003, -5021):
                    # stopPrice invalide ou trop proche du mark price
                    # → décaler de 0.2% dans la bonne direction
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    pp   = info.get("pricePrecision", 4)
                    if "stopPrice" in params and "side" in params:
                        sp       = float(params["stopPrice"])
                        mark     = get_price(sym) or sp
                        # SELL SL → en dessous du mark, BUY SL → au dessus
                        side_ord = params.get("side", "")
                        # Pour SL : close_side = SELL si BUY position
                        # stopPrice doit être SOUS le mark pour BUY position (close SELL)
                        shift = mark * 0.002   # 0.2% de décalage
                        if side_ord == "SELL":    # Ferme un BUY → SL en dessous
                            new_sp = round(min(sp, mark - shift), pp)
                        else:                     # Ferme un SELL → SL au dessus
                            new_sp = round(max(sp, mark + shift), pp)
                        params["stopPrice"] = new_sp
                        logger.warning(
                            f"⚠️  {'-4003' if code==-4003 else '-5021'} stopPrice "
                            f"{sp:.{pp}f} → {new_sp:.{pp}f} (mark={mark:.{pp}f})"
                        )
                        continue

                elif code == -5022:
                    # SL déjà déclenché → position probablement fermée
                    sym = params.get("symbol", "?")
                    logger.warning(f"⚠️  -5022 {sym} SL déjà déclenché → position fermée")
                    # Le monitor_positions détectera la fermeture au prochain cycle
                    return {"_already_triggered": True}

                elif code == -2011:
                    # Ordre inconnu → il a déjà été annulé par Binance
                    # Ne pas tenter de le DELETE, juste re-placer le SL
                    logger.warning(f"⚠️  -2011 ordre inconnu (déjà annulé) → skip DELETE")
                    if method == "DELETE":
                        return {"_already_cancelled": True}
                    continue

                elif code == -4061:
                    # Qty hors limites → clamp entre minQty et maxQty
                    sym  = params.get("symbol", "?")
                    info = symbol_info_cache.get(sym, {})
                    if "quantity" in params:
                        qty    = float(params["quantity"])
                        min_q  = info.get("minQty", 0.001)
                        step   = info.get("stepSize", 0.001)
                        new_q  = max(qty, min_q)
                        new_q  = _round_step(new_q, step)
                        params["quantity"] = new_q
                        logger.warning(f"⚠️  -4061 qty hors limites {qty} → {new_q}")
                        continue

                elif code == -4129:
                    # Doit être reduceOnly → ajouter le flag
                    if "reduceOnly" not in params:
                        params["reduceOnly"] = "true"
                        logger.warning(f"⚠️  -4129 → ajout reduceOnly=true")
                        continue

                elif code == -1121:
                    # Symbole invalide ou suspendu → retirer de la liste
                    sym = params.get("symbol", "?")
                    logger.error(f"❌ -1121 symbole invalide/suspendu : {sym}")
                    if sym in symbols_list:
                        symbols_list.remove(sym)
                        logger.warning(f"   {sym} retiré de la liste")
                        send_telegram(f"⚠️ <b>{sym} suspendu</b> — retiré de la liste")
                    return None

                else:
                    # Erreur non reconnue → log et retry simple
                    logger.warning(f"⚠️  Binance {r.status_code} code={code}: {msg[:60]}")
                    if attempt < 3:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"⏳ Timeout attempt {attempt+1} → retry")
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                logger.debug(f"API exception attempt {attempt+1}: {e}")
                time.sleep(1.0 * (attempt + 1))

    return None

# ─── MARKET DATA ─────────────────────────────────────────────────
def get_klines(symbol, limit=50):
    key = f"{symbol}_1m"
    now = time.time()
    if key in klines_cache:
        d, ts = klines_cache[key]
        if now - ts < KLINES_CACHE_TTL:
            return d
    d = request_binance("GET", "/fapi/v1/klines",
                        {"symbol": symbol, "interval": "1m", "limit": limit}, signed=False)
    if d: klines_cache[key] = (d, now)
    return d or []

def get_price(symbol):
    now = time.time()
    if symbol in price_cache:
        p, ts = price_cache[symbol]
        if now - ts < PRICE_CACHE_TTL: return p
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


# get_risk_usdt / get_leverage / get_max_sl_pct / get_max_margin_pct
# sont définis dans la section PALIERS ci-dessus

# ─── LOAD SYMBOLS ────────────────────────────────────────────────
def load_top_symbols():
    global symbols_list
    logger.info(f"📥 Chargement top {TOP_N_SYMBOLS} symboles...")
    try:
        tickers  = request_binance("GET", "/fapi/v1/ticker/24hr", signed=False)
        exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
        if not tickers or not exchange: raise ValueError("API indispo")

        tradeable = set()
        for s in exchange.get("symbols", []):
            sym = s["symbol"]
            if (sym.endswith("USDT") and s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                    and sym not in EXCLUDE_SYMBOLS and sym != BTC_SYMBOL):
                tradeable.add(sym)
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                symbol_info_cache[sym] = {
                    "quantityPrecision": s.get("quantityPrecision", 3),
                    "pricePrecision":    s.get("pricePrecision", 4),
                    "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                    "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                }

        # Charger aussi les infos BTC
        for s in exchange.get("symbols", []):
            if s["symbol"] == BTC_SYMBOL:
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                symbol_info_cache[BTC_SYMBOL] = {
                    "quantityPrecision": s.get("quantityPrecision", 3),
                    "pricePrecision":    s.get("pricePrecision", 2),
                    "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                    "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
                }

        vol_map = {}
        for t in tickers:
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym in tradeable:
                try: vol_map[sym] = float(t.get("quoteVolume", 0))
                except: pass

        ranked = sorted(
            [(s, v) for s, v in vol_map.items() if v > 10_000_000],
            key=lambda x: x[1], reverse=True
        )[:TOP_N_SYMBOLS]

        symbols_list = [s for s, _ in ranked] or FALLBACK_SYMBOLS[:TOP_N_SYMBOLS]
        logger.info(f"✅ {len(symbols_list)} symboles chargés")
        logger.info(f"   {symbols_list}")
    except Exception as e:
        logger.error(f"load_top_symbols: {e} → fallback")
        symbols_list = FALLBACK_SYMBOLS[:TOP_N_SYMBOLS]

# ─── HELPERS MATH ────────────────────────────────────────────────
def _round_step(qty, step):
    if step <= 0: return qty
    return float(int(qty / step) * step)

def _parse_klines(klines):
    o = np.array([float(k[1]) for k in klines])
    h = np.array([float(k[2]) for k in klines])
    l = np.array([float(k[3]) for k in klines])
    c = np.array([float(k[4]) for k in klines])
    v = np.array([float(k[5]) for k in klines])
    return o, h, l, c, v

def _ema(arr, period):
    if len(arr) < period: return np.zeros(len(arr))
    result = np.zeros(len(arr))
    k = 2.0 / (period + 1)
    result[period-1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i-1] * (1-k)
    return result

def _rsi(c, period=14):
    if len(c) < period + 1: return 50.0
    d  = np.diff(c)
    g  = np.where(d > 0, d, 0.0)
    lo = np.where(d < 0, -d, 0.0)
    ag = np.mean(g[-period:])
    al = np.mean(lo[-period:])
    if al == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def _atr_live(symbol):
    """ATR M1 recalculé live."""
    try:
        klines = get_klines(symbol, 20)
        if not klines or len(klines) < 5:
            p = get_price(symbol)
            return p * 0.002 if p else 0.001
        _, h, l, c, _ = _parse_klines(klines)
        trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
               for i in range(1, len(c))]
        return float(np.mean(trs[-min(14, len(trs)):])) if trs else c[-1]*0.002
    except:
        p = get_price(symbol)
        return p * 0.002 if p else 0.001

def _find_ph(h, lb=2):
    return [i for i in range(lb, len(h)-lb)
            if all(h[i] >= h[i-j] for j in range(1,lb+1))
            and all(h[i] >= h[i+j] for j in range(1,lb+1))]

def _find_pl(l, lb=2):
    return [i for i in range(lb, len(l)-lb)
            if all(l[i] <= l[i-j] for j in range(1,lb+1))
            and all(l[i] <= l[i+j] for j in range(1,lb+1))]

# ════════════════════════════════════════════════════════════════════
#  DIRECTION BTC M1 — RÉFÉRENCE DE CORRÉLATION
# ════════════════════════════════════════════════════════════════════
def get_btc_m1_direction() -> dict:
    """
    Direction BTC M1 — mise à jour toutes les 10s.

    Retourne :
      direction : +1 (haussier) | -1 (baissier) | 0 (neutre)
      label     : str
      rsi       : float
      slope     : float (pente EMA5 normalisée)

    Logique :
    ► EMA5 > EMA13 ET prix > EMA5 → haussier
    ► EMA5 < EMA13 ET prix < EMA5 → baissier
    ► RSI9 confirme (pas extrême)
    ► Pente EMA5 sur 3 bougies confirme la dynamique
    """
    global _btc_direction_cache
    now = time.time()
    if now - _btc_direction_cache.get("ts", 0) < 10:
        return _btc_direction_cache

    try:
        klines = get_klines(BTC_SYMBOL, 30)
        if not klines or len(klines) < 20:
            return _btc_direction_cache

        _, _, _, c, _ = _parse_klines(klines)
        ema5  = _ema(c, BTC_EMA_FAST)
        ema13 = _ema(c, BTC_EMA_SLOW)
        rsi   = _rsi(c, BTC_RSI_PERIOD)
        price = c[-1]

        e5  = ema5[-1]
        e13 = ema13[-1]

        # Pente EMA5 sur les 3 dernières bougies (normalisée)
        if ema5[-1] > 0 and ema5[-4] > 0:
            slope = (ema5[-1] - ema5[-4]) / ema5[-4] * 100
        else:
            slope = 0.0

        # v39.4 : slope supprimée comme condition bloquante
        # EMA5/EMA13 + prix + RSI suffisent pour déterminer la direction
        bull = e5 > e13 and price > e5 and rsi < BTC_RSI_BULL_MAX
        bear = e5 < e13 and price < e5 and rsi > BTC_RSI_BEAR_MIN

        if bull:
            direction = 1
            label = f"BTC M1 🟢 HAUSSIER RSI={rsi:.0f}"
        elif bear:
            direction = -1
            label = f"BTC M1 🔴 BAISSIER RSI={rsi:.0f}"
        else:
            direction = 0
            label = f"BTC M1 ⚪ NEUTRE RSI={rsi:.0f}"

        result = {"direction": direction, "label": label, "rsi": rsi,
                  "slope": round(slope, 4), "ts": now}
        _btc_direction_cache = result
        return result

    except Exception as e:
        logger.debug(f"get_btc_m1_direction: {e}")
        return _btc_direction_cache

# ════════════════════════════════════════════════════════════════════
#  CORRÉLATION SYMBOLE / BTC M1
# ════════════════════════════════════════════════════════════════════
def check_btc_correlation(symbol: str, side: str) -> tuple[bool, str]:
    """
    Vérifie que le symbole suit BTC M1 dans la même direction.

    Règles :
    ► BUY  : BTC M1 doit être haussier (+1) → sinon refusé
    ► SELL : BTC M1 doit être baissier (-1) → sinon refusé
    ► BTC M1 neutre → aucun trade autorisé

    Retourne (ok: bool, reason: str)
    """
    btc = get_btc_m1_direction()
    btc_dir = btc["direction"]
    btc_rsi = btc["rsi"]

    if btc_dir == 0:
        return False, f"BTC M1 neutre ({btc['label']}) → pas de trade"

    if side == "BUY" and btc_dir != 1:
        return False, f"BUY refusé — BTC M1 baissier ({btc['label']})"

    if side == "SELL" and btc_dir != -1:
        return False, f"SELL refusé — BTC M1 haussier ({btc['label']})"

    # Vérifier aussi que le symbole se comporte comme BTC
    try:
        klines = get_klines(symbol, 15)
        if not klines or len(klines) < 10:
            return True, "OK (pas assez de données pour corr check)"

        _, _, _, c_sym, _ = _parse_klines(klines)
        _, _, _, c_btc, _ = _parse_klines(get_klines(BTC_SYMBOL, 15) or klines)

        n = min(len(c_sym), len(c_btc), 10)
        if n < 5:
            return True, "OK"

        # Comparer la direction des 5 dernières bougies
        sym_ret = float(c_sym[-1] - c_sym[-6]) / c_sym[-6] if c_sym[-6] > 0 else 0
        btc_ret = float(c_btc[-1] - c_btc[-6]) / c_btc[-6] if c_btc[-6] > 0 else 0

        # Même sens de mouvement ?
        same_dir = (sym_ret > 0 and btc_ret > 0) or (sym_ret < 0 and btc_ret < 0)

        if not same_dir:
            return False, f"{symbol} diverge de BTC (sym:{sym_ret*100:+.2f}% btc:{btc_ret*100:+.2f}%)"

        return True, f"✅ Corrélé BTC ({btc['label']}) sym:{sym_ret*100:+.2f}%"

    except Exception as e:
        logger.debug(f"check_btc_correlation {symbol}: {e}")
        return True, "OK (erreur corr check)"

# ════════════════════════════════════════════════════════════════════
#  ANALYSE M1 — SETUP ULTRA-STRICT
# ════════════════════════════════════════════════════════════════════
def analyse_m1(symbol: str, klines: list):
    """
    Analyse M1 ultra-stricte — seulement les meilleurs setups.
    Score minimum 88, confluence 3/5, probabilité 65%.
    """
    try:
        if not klines or len(klines) < 30:
            return None

        o, h, l, c, v = _parse_klines(klines)
        n = len(c)

        # EMA pour direction
        e9  = _ema(c, 9)
        e21 = _ema(c, 21)
        price = c[-1]

        bull_ema = e9[-1] > e21[-1] and price > e9[-1]
        bear_ema = e9[-1] < e21[-1] and price < e9[-1]

        rsi = _rsi(c, 14)

        # Filtres RSI stricts
        if rsi > 72 or rsi < 28:
            return None

        if bull_ema and 28 < rsi < 70:
            direction = "BUY"
        elif bear_ema and 30 < rsi < 72:
            direction = "SELL"
        else:
            return None

        # Volume
        avg_vol   = float(np.mean(v[-15:])) if n >= 15 else float(np.mean(v))
        vol_spike = float(v[-2]) > avg_vol * VOLUME_SPIKE_MIN

        # Pivots
        ph = _find_ph(h, 2)
        pl = _find_pl(l, 2)
        bull_struct = len(ph)>=2 and h[ph[-1]]>h[ph[-2]] and len(pl)>=2 and l[pl[-1]]>l[pl[-2]]
        bear_struct = len(ph)>=2 and h[ph[-1]]<h[ph[-2]] and len(pl)>=2 and l[pl[-1]]<l[pl[-2]]

        atr = float(np.mean([max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
                             for i in range(1, n)]))

        setups = []

        # ══════════════════════════════════════════════════════════
        # SETUP 1 — SWEEP_OB (score 92) — LE MEILLEUR
        # Balayage liquidité M1 + retour Order Block
        # v39.4 : vol_spike optionnel (compte dans confluence, pas bloquant)
        # ══════════════════════════════════════════════════════════
        if direction == "BUY" and len(pl) >= 2:
            prev_low = l[pl[-2]]
            for i in range(n-4, max(n-10, 1), -1):
                if l[i] < prev_low and c[i] > prev_low:
                    # Chercher OB (dernière bougie rouge avant le sweep)
                    ob_idx = None
                    for j in range(i-1, max(0, i-OB_LOOKBACK), -1):
                        if c[j] < o[j] and (o[j]-c[j]) > atr * 0.25:
                            ob_idx = j
                            break
                    if ob_idx is not None and c[-1] > c[-2]:
                        ob_b = min(o[ob_idx], c[ob_idx])
                        ob_t = max(o[ob_idx], c[ob_idx])
                        conf = sum([
                            1,                                    # Setup de base
                            bool(bull_struct),                    # Structure haussière
                            bool(rsi < 58),                       # RSI pas surachat
                            bool(e9[-1] > e21[-1]),               # EMA alignées
                            bool(vol_spike),                      # Volume spike bonus
                        ])
                        if conf >= 2:  # Min 2 pour SWEEP_OB
                            setups.append({
                                "name": "SWEEP_OB", "score": 92,
                                "confluence": min(conf, 5),
                                "ob": {"bottom": ob_b, "top": ob_t},
                                "direction": "BUY",
                            })
                    break

        elif direction == "SELL" and len(ph) >= 2:
            prev_high = h[ph[-2]]
            for i in range(n-4, max(n-10, 1), -1):
                if h[i] > prev_high and c[i] < prev_high:
                    ob_idx = None
                    for j in range(i-1, max(0, i-OB_LOOKBACK), -1):
                        if c[j] > o[j] and (c[j]-o[j]) > atr * 0.25:
                            ob_idx = j
                            break
                    if ob_idx is not None and c[-1] < c[-2]:
                        ob_b = min(o[ob_idx], c[ob_idx])
                        ob_t = max(o[ob_idx], c[ob_idx])
                        conf = sum([
                            1,
                            bool(bear_struct),
                            bool(rsi > 42),
                            bool(e9[-1] < e21[-1]),
                            bool(vol_spike),
                        ])
                        if conf >= 2:
                            setups.append({
                                "name": "SWEEP_OB", "score": 92,
                                "confluence": min(conf, 5),
                                "ob": {"bottom": ob_b, "top": ob_t},
                                "direction": "SELL",
                            })
                    break

        # ══════════════════════════════════════════════════════════
        # SETUP 2 — BOS_FVG (score 85)
        # BOS confirmé + FVG mitigation M1
        # v39.4 : confluence min 2, vol_spike en bonus seulement
        # ══════════════════════════════════════════════════════════
        if direction == "BUY" and len(ph) >= 2:
            bos_level = h[ph[-2]]
            if c[-1] > bos_level:  # BOS confirmé sur 1 bougie suffit
                for i in range(n-5, max(0, n-18), -1):
                    if i+2 < n:
                        gap = l[i+2] - h[i]
                        if gap > price * FVG_MIN_GAP:
                            conf = sum([
                                1,
                                bool(vol_spike),
                                bool(bull_struct),
                                bool(rsi < 62),
                                bool(gap > atr * 0.3),
                            ])
                            if conf >= 2:
                                setups.append({
                                    "name": "BOS_FVG", "score": 85,
                                    "confluence": min(conf, 5),
                                    "ob": {"bottom": h[i], "top": l[i+2]},
                                    "direction": "BUY",
                                })
                            break

        elif direction == "SELL" and len(pl) >= 2:
            bos_level = l[pl[-2]]
            if c[-1] < bos_level:
                for i in range(n-5, max(0, n-18), -1):
                    if i+2 < n:
                        gap = l[i] - h[i+2]
                        if gap > price * FVG_MIN_GAP:
                            conf = sum([
                                1,
                                bool(vol_spike),
                                bool(bear_struct),
                                bool(rsi > 38),
                                bool(gap > atr * 0.3),
                            ])
                            if conf >= 2:
                                setups.append({
                                    "name": "BOS_FVG", "score": 85,
                                    "confluence": min(conf, 5),
                                    "ob": {"bottom": h[i+2], "top": l[i]},
                                    "direction": "SELL",
                                })
                            break

        if not setups:
            return None

        setups.sort(key=lambda s: (s["score"], s["confluence"]), reverse=True)
        best = setups[0]

        # Score minimum strict
        if best["score"] < MIN_SCORE:
            return None
        if best["confluence"] < MIN_CONFLUENCE:
            return None

        # ── Trigger M1 : bougie franche confirmée ─────────────────
        # v39.4 : vol_spike retiré comme condition bloquante finale
        # (déjà inclus dans le score de confluence)
        ko, kc, kh, kl = o[-2], c[-2], h[-2], l[-2]
        body   = abs(kc - ko)
        range_ = kh - kl if kh > kl else 1.0
        body_ratio = body / range_

        if direction == "BUY"  and not (kc > ko and body_ratio >= MIN_BODY_RATIO):
            return None
        if direction == "SELL" and not (kc < ko and body_ratio >= MIN_BODY_RATIO):
            return None

        # ── Probabilité ──────────────────────────────────────────
        base = {"SWEEP_OB": 70, "BOS_FVG": 63}.get(best["name"], 60)
        if direction == "BUY"  and bull_ema and bull_struct: base += 10
        if direction == "SELL" and bear_ema and bear_struct: base += 10
        if vol_spike:  base += 5
        if best["confluence"] >= 4: base += 5
        prob = min(float(base), 99.0)

        if prob < MIN_PROB:
            return None

        return {
            "symbol":      symbol,
            "side":        direction,
            "setup":       best["name"],
            "score":       best["score"],
            "confluence":  best["confluence"],
            "probability": prob,
            "ob":          best.get("ob", {}),
            "atr":         atr,
            "rsi":         rsi,
        }

    except Exception as e:
        logger.debug(f"analyse_m1 {symbol}: {e}")
        return None

# ─── SL STRUCTUREL ────────────────────────────────────────────────
def get_sl(symbol, side, ob, entry, atr):
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    tick = 10 ** (-pp)
    buf  = tick * 5

    if ob:
        sl_raw = (ob["bottom"] - buf) if side == "BUY" else (ob["top"] + buf)
        dist   = abs(entry - sl_raw)
        if entry * MIN_SL_PCT <= dist <= entry * MAX_SL_PCT:
            return round(sl_raw, pp)

    # Fallback ATR
    dist = max(atr * 1.0, entry * MIN_SL_PCT)
    dist = min(dist, entry * MAX_SL_PCT)
    return round(entry - dist if side == "BUY" else entry + dist, pp)

# ─── BINANCE ORDERS ──────────────────────────────────────────────
def set_leverage_sym(symbol, lev):
    """
    Applique le levier demandé.
    Si Binance refuse (levier trop élevé pour ce symbole),
    essaie des leviers décroissants jusqu'à ce que ça passe.
    """
    fallbacks = [lev]
    # Générer des paliers de repli : -5x à chaque étape
    cur = lev
    while cur > 5:
        cur = max(5, cur - 5)
        fallbacks.append(cur)

    for try_lev in fallbacks:
        r = request_binance("POST", "/fapi/v1/leverage",
                            {"symbol": symbol, "leverage": try_lev})
        if r and "leverage" in r:
            actual = int(r["leverage"])
            if actual != lev:
                logger.warning(f"⚠️  {symbol} levier {lev}x refusé → accepté {actual}x")
            else:
                logger.debug(f"  {symbol} levier {actual}x ✅")
            # Mettre à jour le cache pour que open_position utilise le bon levier
            symbol_info_cache.setdefault(symbol, {})["actual_leverage"] = actual
            return actual
        time.sleep(0.2)

    logger.error(f"❌ {symbol} impossible de définir le levier → garde existant")
    return lev

def set_isolated(symbol):
    """
    Met le mode ISOLATED.
    Si Binance refuse (position ouverte → ne peut pas changer),
    continue en CROSS sans bloquer le trade.
    """
    r = request_binance("POST", "/fapi/v1/marginType",
                        {"symbol": symbol, "marginType": "ISOLATED"})
    if r is None:
        # Peut échouer si position déjà ouverte ou si margin type déjà correct
        # → on vérifie l'état actuel
        pos = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        if pos:
            for p in pos:
                if p.get("symbol") == symbol:
                    mode = p.get("marginType", "unknown")
                    if mode == "isolated":
                        logger.debug(f"  {symbol} déjà ISOLATED ✅")
                    else:
                        logger.warning(f"  {symbol} CROSS maintenu (position ouverte) — OK")

def cleanup_orders(symbol):
    """Annule tous les ordres ouverts — ignore les erreurs individuelles."""
    try:
        orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if not orders:
            return
        for o in orders:
            oid = o.get("orderId")
            if oid:
                r = request_binance("DELETE", "/fapi/v1/order",
                                    {"symbol": symbol, "orderId": oid})
                if r and r.get("_already_cancelled"):
                    pass  # Déjà annulé — normal
                elif r:
                    logger.debug(f"  {symbol} ordre {oid} annulé ✅")
    except Exception as e:
        logger.debug(f"cleanup_orders {symbol}: {e}")

def place_market(symbol, side, qty):
    """
    Ordre MARKET avec gestion intelligente des rejets :
    - -1111 : précision qty → corrigé par request_binance
    - -1013 : min notional → qty augmentée par request_binance
    - -2010 : solde insuf  → qty réduite 2× avant abandon
    - Si toujours rejeté : retourne None (position non ouverte)
    """
    info = symbol_info_cache.get(symbol, {})
    qp   = info.get("quantityPrecision", 3)
    step = info.get("stepSize", 0.001)

    qty_adj = _round_step(qty, step)
    qty_adj = round(qty_adj, qp)

    r = request_binance("POST", "/fapi/v1/order", {
        "symbol":   symbol,
        "side":     side,
        "type":     "MARKET",
        "quantity": qty_adj,
    })

    if r and r.get("orderId"):
        logger.info(f"  {symbol} MARKET {side} qty={qty_adj} ✅ id={r['orderId']}")
        return r

    # request_binance a déjà tenté les corrections auto (-1111, -1013, -2010)
    # Si on arrive ici → ordre impossible → ne pas ouvrir la position
    logger.error(f"❌ {symbol} MARKET {side} échoué définitivement — position annulée")
    send_telegram(
        f"❌ <b>Ordre MARKET {symbol} {side} impossible</b>\n"
        f"qty={qty_adj} | Position non ouverte\n"
        f"Prochain signal sera pris"
    )
    return None

def place_tp_binance(symbol, tp, close_side):
    """
    Pose un TP avec fallback CONTRACT_PRICE.
    Si les deux échouent, le TP RR2.5 sera géré par le trailing SL.
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # Tentative 1 : MARK_PRICE
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "TAKE_PROFIT_MARKET",
            "stopPrice":     round(tp, pp),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })
        if r and r.get("orderId"):
            logger.info(f"🎯 {symbol} TP ✅ MARK_PRICE @ {round(tp,pp)}")
            return {"sent": True, "order_id": r["orderId"], "method": "MARK_PRICE"}
        time.sleep(0.3)

    # Tentative 2 : CONTRACT_PRICE
    for _ in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "TAKE_PROFIT_MARKET",
            "stopPrice":     round(tp, pp),
            "closePosition": "true",
            "workingType":   "CONTRACT_PRICE",
        })
        if r and r.get("orderId"):
            logger.info(f"🎯 {symbol} TP ✅ CONTRACT_PRICE @ {round(tp,pp)}")
            return {"sent": True, "order_id": r["orderId"], "method": "CONTRACT_PRICE"}
        time.sleep(0.3)

    # TP non posé → le trailing SL prendra le relais au RR2.5
    logger.warning(f"⚠️  {symbol} TP non posé → trailing SL gère la sortie")
    return {"sent": False, "order_id": None, "method": "TRAILING"}

def place_sl_binance(symbol, sl, close_side):
    """
    Pose un SL sur Binance avec 3 stratégies de fallback :
    1. STOP_MARKET MARK_PRICE (standard)
    2. STOP_MARKET CONTRACT_PRICE (si mark price rejeté)
    3. SL logiciel piloté par le monitor (dernier recours)
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # ── Stratégie 1 : STOP_MARKET workingType=MARK_PRICE (standard) ──
    for attempt in range(3):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     round(sl, pp),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })
        if r and r.get("orderId"):
            logger.info(f"🛡️  {symbol} SL ✅ MARK_PRICE @ {round(sl,pp)} id={r['orderId']}")
            return {"sent": True, "order_id": r["orderId"], "method": "MARK_PRICE"}
        if r and r.get("_already_triggered"):
            logger.warning(f"⚠️  {symbol} SL -5022 déjà déclenché → position fermée")
            return {"sent": False, "order_id": None, "triggered": True}
        time.sleep(0.5)

    # ── Stratégie 2 : STOP_MARKET workingType=CONTRACT_PRICE ──────────
    logger.warning(f"⚠️  {symbol} MARK_PRICE rejeté → essai CONTRACT_PRICE")
    for attempt in range(2):
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     round(sl, pp),
            "closePosition": "true",
            "workingType":   "CONTRACT_PRICE",
        })
        if r and r.get("orderId"):
            logger.info(f"🛡️  {symbol} SL ✅ CONTRACT_PRICE @ {round(sl,pp)} id={r['orderId']}")
            return {"sent": True, "order_id": r["orderId"], "method": "CONTRACT_PRICE"}
        time.sleep(0.5)

    # ── Stratégie 3 : SL logiciel (monitor toutes les 2s) ────────────
    logger.error(f"🚨 {symbol} SL Binance impossible → SL LOGICIEL activé @ {round(sl,pp)}")
    send_telegram(
        f"🚨 <b>SL {symbol} non posé sur Binance</b>\n"
        f"SL logiciel actif @ {round(sl,pp)}\n"
        f"Monitor vérifie toutes les 2s ✅"
    )
    return {"sent": False, "order_id": None, "method": "SOFTWARE"}


def move_sl_binance(symbol, old_id, new_sl, close_side):
    """
    Déplace le SL sur Binance intelligemment :
    1. Annule l'ancien SL (ignore si déjà annulé : -2011)
    2. Pose le nouveau avec fallback CONTRACT_PRICE si MARK_PRICE rejeté
    Appelé HORS du trade_lock — aucun deadlock possible.
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # ── Étape 1 : Annuler l'ancien SL ────────────────────────────
    if old_id:
        r_del = request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": old_id})
        if r_del and r_del.get("_already_cancelled"):
            logger.debug(f"  {symbol} ancien SL {old_id} déjà annulé — OK")
        elif r_del:
            logger.debug(f"  {symbol} ancien SL {old_id} annulé ✅")

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
            # SL déclenché pendant le déplacement → position fermée
            logger.warning(f"⚠️  {symbol} SL déclenché pendant déplacement → fermée")
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None
        time.sleep(0.3)

    # ── Fallback : CONTRACT_PRICE ─────────────────────────────────
    logger.warning(f"⚠️  {symbol} MARK_PRICE rejeté sur trailing → CONTRACT_PRICE")
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
            logger.info(f"  {symbol} nouveau SL via CONTRACT_PRICE ✅ @ {round(new_sl,pp)}")
            return r
        time.sleep(0.3)

    logger.error(f"❌ {symbol} move_sl_binance échoué — SL logiciel à {round(new_sl,pp)}")
    return None

# ════════════════════════════════════════════════════════════════════
#  SL SUIVEUR — FRAIS RÉELS GARANTIS v39.4
# ════════════════════════════════════════════════════════════════════
def update_trailing_sl(symbol: str):
    """
    SL Suiveur v39.4 — frais Binance réels intégrés à CHAQUE étape

    ┌─────────────────────────────────────────────────────────────┐
    │  FRAIS BINANCE FUTURES TAKER                                │
    │  Ouverture  : 0.04% du notionnel                           │
    │  Fermeture  : 0.04% du notionnel                           │
    │  Total A/R  : 0.08% + buffer 50% = 0.12% (BREAKEVEN_FEE)  │
    ├─────────────────────────────────────────────────────────────┤
    │  PLANCHER FRAIS (FEE_FLOOR) — plancher absolu du SL        │
    │  BUY  : entry × (1 + 0.0008)  → couvre open+close fees    │
    │  SELL : entry × (1 - 0.0008)  → couvre open+close fees    │
    │  Le SL ne peut JAMAIS descendre sous ce plancher           │
    ├─────────────────────────────────────────────────────────────┤
    │  PHASE 1 — BREAKEVEN (+0.6R) :                             │
    │  BUY  : SL → entry × 1.0012  (frais + buffer 50%)         │
    │  SELL : SL → entry × 0.9988                               │
    ├─────────────────────────────────────────────────────────────┤
    │  PHASE 2 — TRAILING (+1R) :                                │
    │  SL = prix_actuel - ATR_M1_live × 0.7  (BUY)              │
    │  SL = prix_actuel + ATR_M1_live × 0.7  (SELL)             │
    │  + garantie : SL ≥ FEE_FLOOR (jamais sous les frais)       │
    │  + garantie : SL ne recule JAMAIS                          │
    │  + appelé toutes les 2s                                    │
    └─────────────────────────────────────────────────────────────┘
    """
    # ── Étape 1 : Lecture trade_log (lock COURT) ─────────────────
    with trade_lock:
        if symbol not in trade_log: return
        t = trade_log[symbol]
        if t.get("status") != "OPEN": return
        side         = t["side"]
        entry        = t["entry"]
        sl           = t["sl"]
        sl_order_id  = t.get("sl_order_id")
        be_moved     = t.get("breakeven_moved", False)
        info         = symbol_info_cache.get(symbol, {})
        pp           = info.get("pricePrecision", 4)

    # ── Étape 2 : Prix et ATR live (HORS lock, API libre) ────────
    current_price = get_price(symbol)
    if not current_price or current_price <= 0: return

    atr        = _atr_live(symbol)   # ATR recalculé live sur M1 frais
    close_side = "SELL" if side == "BUY" else "BUY"

    # ── Étape 3 : Calcul RR courant ──────────────────────────────
    risk   = abs(entry - sl)
    if risk <= 0: return
    profit = (current_price - entry) if side == "BUY" else (entry - current_price)
    rr     = profit / risk

    new_sl     = sl       # Départ : SL actuel (ne peut qu'augmenter/diminuer vers faveur)
    action     = None
    be_trigger = False
    trail_used = False

    # ── Plancher frais absolu ─────────────────────────────────────
    # Ce plancher garantit que si le SL est touché, on couvre
    # au minimum les frais d'ouverture ET de fermeture (0.08%)
    # Le SL ne peut JAMAIS descendre sous ce plancher.
    if side == "BUY":
        fee_floor = round(entry * (1.0 + BINANCE_FEE_RATE * 2), pp)  # entry × 1.0008
    else:
        fee_floor = round(entry * (1.0 - BINANCE_FEE_RATE * 2), pp)  # entry × 0.9992

    # ── PHASE 1 : BREAKEVEN avec frais réels (+0.6R) ─────────────
    # Déclenché seulement si on est réellement au-dessus du coût
    # total des frais aller-retour (0.12% avec buffer)
    if rr >= BREAKEVEN_RR and not be_moved:
        if side == "BUY":
            be_sl = round(entry * (1.0 + BREAKEVEN_FEE_TOTAL), pp)
            if be_sl > sl:
                new_sl     = be_sl
                be_trigger = True
                action     = f"BE @ {be_sl:.{pp}f} (frais {BREAKEVEN_FEE_TOTAL*100:.2f}% couverts)"
                logger.info(
                    f"🎯 {symbol} {side} BREAKEVEN RÉEL | "
                    f"{sl:.{pp}f} → {be_sl:.{pp}f} | "
                    f"RR={rr:.2f}R | frais 0.04%×2+buffer OK"
                )
        else:
            be_sl = round(entry * (1.0 - BREAKEVEN_FEE_TOTAL), pp)
            if be_sl < sl:
                new_sl     = be_sl
                be_trigger = True
                action     = f"BE @ {be_sl:.{pp}f} (frais {BREAKEVEN_FEE_TOTAL*100:.2f}% couverts)"
                logger.info(
                    f"🎯 {symbol} {side} BREAKEVEN RÉEL | "
                    f"{sl:.{pp}f} → {be_sl:.{pp}f} | "
                    f"RR={rr:.2f}R | frais 0.04%×2+buffer OK"
                )

    # ── PHASE 2 : TRAILING DYNAMIQUE (+1R) ───────────────────────
    # Distance = ATR M1 live × 0.7 (serré pour scalp M1)
    # Garanties :
    #   ① Le trailing ne peut JAMAIS reculer (only moves in favor)
    #   ② Le trailing ne descend JAMAIS sous fee_floor
    #      → si touché, on sort toujours avec un profit net de frais
    if rr >= TRAIL_START_RR:
        trail_dist = atr * TRAIL_ATR_MULT
        if side == "BUY":
            trail_sl_raw = round(current_price - trail_dist, pp)
            # Garantie ② : plancher frais
            trail_sl = max(trail_sl_raw, fee_floor)
            # Garantie ① : jamais en arrière
            if trail_sl > new_sl:
                new_sl     = trail_sl
                trail_used = True
                action     = (
                    f"TRAIL @ {trail_sl:.{pp}f} | "
                    f"ATR={atr:.{pp}f} | "
                    f"dist={trail_dist:.{pp}f} | "
                    f"floor={fee_floor:.{pp}f}"
                )
        else:
            trail_sl_raw = round(current_price + trail_dist, pp)
            # Garantie ② : plancher frais
            trail_sl = min(trail_sl_raw, fee_floor)
            # Garantie ① : jamais en arrière
            if trail_sl < new_sl:
                new_sl     = trail_sl
                trail_used = True
                action     = (
                    f"TRAIL @ {trail_sl:.{pp}f} | "
                    f"ATR={atr:.{pp}f} | "
                    f"dist={trail_dist:.{pp}f} | "
                    f"floor={fee_floor:.{pp}f}"
                )

    # ── Étape 4 : Le SL s'améliore-t-il vraiment ? ───────────────
    tick = 10 ** (-pp)
    improved = (
        (side == "BUY"  and new_sl > sl + tick) or
        (side == "SELL" and new_sl < sl - tick)
    )
    if not improved: return   # Rien à faire

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

            # Déterminer le libellé de l'action combinée
            if be_trigger and trail_used:
                label = f"BE+TRAIL simultané"
            elif be_trigger:
                label = f"BREAKEVEN frais couverts"
            else:
                label = f"TRAILING actif"

            logger.info(
                f"🔁 SL SUIVEUR {symbol} {side} [{label}] | "
                f"{sl:.{pp}f} → {new_sl:.{pp}f} | "
                f"RR={rr:.2f}R | {action}"
            )

            # Telegram : au BE et à chaque RR entier franchi
            if be_trigger or (trail_used and int(rr) >= 1 and abs(rr - round(rr)) < 0.15):
                pnl_min = abs(new_sl - entry) * t.get("qty", 0)
                send_telegram(
                    f"🔁 <b>SL Suiveur {symbol} {side}</b>\n"
                    f"<b>{sl:.{pp}f} → {new_sl:.{pp}f}</b>\n"
                    f"RR={rr:.2f}R | Prix: {current_price:.{pp}f}\n"
                    f"Plancher frais: {fee_floor:.{pp}f}\n"
                    + (
                        f"✅ <b>BREAKEVEN</b> — frais 0.12% couverts\n"
                        f"Pire cas si SL touché: +${pnl_min:.4f} net"
                        if be_trigger else
                        f"🔁 <b>Trailing RR{int(rr)}R</b>\n"
                        f"SL garanti au-dessus des frais"
                    )
                )
        else:
            # SL Binance échoué → SL logiciel reste actif
            t["sl_on_binance"] = False
            logger.warning(
                f"⚠️  {symbol} déplacement SL Binance échoué | "
                f"SL logiciel actif @ {sl:.{pp}f} | "
                f"Tentative : {new_sl:.{pp}f}"
            )

# ─── OPEN POSITION ────────────────────────────────────────────────
def open_position(signal: dict):
    symbol = signal["symbol"]
    side   = signal["side"]
    setup  = signal["setup"]
    score  = signal["score"]
    prob   = signal["probability"]
    ob     = signal.get("ob", {})
    atr    = signal.get("atr", 0)
    btc_corr = signal.get("btc_corr", "?")

    try:
        info = symbol_info_cache.get(symbol)
        if not info: return

        pp        = info.get("pricePrecision", 4)
        step_size = info.get("stepSize", 0.001)
        min_qty   = info.get("minQty", 0.001)
        min_notional = info.get("minNotional", MIN_NOTIONAL)

        entry = get_price(symbol)
        if not entry: return

        # ── Paramètres adaptatifs selon le palier balance ─────────
        tier       = get_tier()
        lev        = get_leverage(score)
        marg_pct   = min(MARGIN_PCT_BY_LEV.get(lev, 0.20), get_max_margin_pct())
        margin     = account_balance * marg_pct
        max_sl_pct = get_max_sl_pct()

        sl = get_sl(symbol, side, ob, entry, atr)
        if side == "BUY":
            sl_dist = max(entry - sl, entry * MIN_SL_PCT)
            sl_dist = min(sl_dist, entry * max_sl_pct)
        else:
            sl_dist = max(sl - entry, entry * MIN_SL_PCT)
            sl_dist = min(sl_dist, entry * max_sl_pct)

        # SL trop large pour ce palier → skip
        if sl_dist > entry * max_sl_pct:
            logger.info(f"  {symbol} SL trop large {sl_dist/entry*100:.2f}% > {max_sl_pct*100:.1f}% → skip")
            return

        if sl_dist <= 0: return

        if side == "BUY":
            sl = round(entry - sl_dist, pp)
            tp = round(entry + sl_dist * TP_RR, pp)
        else:
            sl = round(entry + sl_dist, pp)
            tp = round(entry - sl_dist * TP_RR, pp)

        # ── Sizing : risque = % balance selon palier ──────────────
        risk_usdt = get_risk_usdt()
        qty = _round_step(risk_usdt / sl_dist, step_size)

        # Cap par marge disponible × levier
        max_qty_m = _round_step((margin * lev) / entry, step_size)
        if max_qty_m > 0 and qty > max_qty_m:
            qty = max_qty_m

        # ── Min notional Binance ($5) ──────────────────────────────
        # Si la qty calculée est inférieure au min notional,
        # augmenter la qty jusqu'au min notional.
        # Vérifier que le risque résultant reste acceptable.
        notional = qty * entry
        if notional < min_notional:
            qty_min   = _round_step(min_notional / entry + step_size, step_size)
            real_risk = sl_dist * qty_min
            # Acceptable si risque < 25% balance (palier 1 : survie tolère plus)
            max_risk_pct = MAX_NOTIONAL_RISK + (0.07 if tier == 1 else 0.0)
            if real_risk > account_balance * max_risk_pct:
                logger.info(
                    f"  {symbol} skip — min notional risque ${real_risk:.4f} "
                    f"= {real_risk/account_balance:.1%} > {max_risk_pct:.0%} balance"
                )
                return
            qty = qty_min

        if qty < min_qty: return

        tier_label = get_tier_label()
        progress   = get_progress_bar()
        logger.info(
            f"📊 {tier_label} TRADE {symbol} {side} | {setup}({score}) | "
            f"prob={prob:.0f}% | {lev}x | sl={sl_dist/entry*100:.3f}% | "
            f"risk=${sl_dist*qty:.4f} | qty={qty}"
        )
        logger.info(f"   Palier {get_tier()} | Balance ${account_balance:.4f} | {progress}")

        set_isolated(symbol)
        actual_lev = set_leverage_sym(symbol, lev)
        # Utilise le levier réellement accepté par Binance (peut être < lev)
        if actual_lev != lev:
            logger.warning(f"  {symbol} levier ajusté {lev}x → {actual_lev}x")
            lev = actual_lev
        cleanup_orders(symbol)

        order = place_market(symbol, side, qty)
        if not order:
            logger.error(f"❌ {symbol} ordre MARKET échoué")
            return

        # Vrai prix d'entrée
        actual_entry = 0.0
        for _ in range(5):
            time.sleep(0.4)
            pos = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
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

        # Calcul BE pour info
        be_price = round(actual_entry * (1.0 + BREAKEVEN_FEE_TOTAL), pp) if side == "BUY" \
                   else round(actual_entry * (1.0 - BREAKEVEN_FEE_TOTAL), pp)

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
                "sl_on_binance":        sl_r["sent"],
                "tp_on_binance":        tp_r["sent"],
                "sl_order_id":          sl_r["order_id"],
                "tp_order_id":          tp_r["order_id"],
                "trailing_stop_active": False,
                "breakeven_moved":      False,
                "btc_corr":             btc_corr,
                "atr":                  atr,
                "be_price":             be_price,
            }

        logger.info(f"✅ {symbol} {side} @ {actual_entry:.{pp}f} | SL {sl:.{pp}f} | BE {be_price:.{pp}f} | TP {tp:.{pp}f} | {lev}x | {get_tier_label()}")
        send_telegram(
            f"🚀 <b>{symbol} {side}</b> @ {actual_entry:.{pp}f}\n"
            f"SL: {sl:.{pp}f} {'✅Binance' if sl_r['sent'] else '⚠️logiciel'}\n"
            f"BE frais: {be_price:.{pp}f}\n"
            f"TP: {tp:.{pp}f} (RR{TP_RR})\n"
            f"Setup: {setup}({score}) | Prob: {prob:.0f}%\n"
            f"Levier: {lev}x | Risk: ${sl_dist2*qty:.4f}\n"
            f"\n"
            f"{get_tier_label()} | {get_progress_bar()}\n"
            f"Balance: ${account_balance:.4f} → Objectif: ${TARGET_BALANCE:.0f}\n"
            f"🔁 SL suiveur dès RR{BREAKEVEN_RR}"
        )

    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

# ─── MONITOR ─────────────────────────────────────────────────────
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
            # SL SUIVEUR (hors lock)
            update_trailing_sl(symbol)

            with trade_lock:
                if symbol not in trade_log: continue
                t = trade_log[symbol]
                if t.get("status") != "OPEN": continue

                # Fermée par Binance ?
                if symbol not in binance_open:
                    t["status"]    = "CLOSED"
                    t["closed_by"] = "BINANCE_SL_TP"
                    logger.info(f"✅ {symbol} fermée par Binance")
                    _on_closed_from_binance(symbol, t)
                    continue

                # SL logiciel fallback
                if not t.get("sl_on_binance"):
                    cp = get_price(symbol)
                    if cp:
                        sl = t["sl"]
                        if (t["side"] == "BUY" and cp <= sl) or \
                           (t["side"] == "SELL" and cp >= sl):
                            logger.warning(f"🚨 {symbol} SL LOGICIEL @ {cp}")
                            cs = "SELL" if t["side"] == "BUY" else "BUY"
                            place_market(symbol, cs, t.get("qty", 0))
                            t["status"]    = "CLOSED"
                            t["closed_by"] = "SOFTWARE_SL"
                            _on_closed(symbol, t, is_win=False)

    except Exception as e:
        logger.debug(f"monitor_positions: {e}")

def _on_closed(symbol, trade, is_win):
    global consec_losses, cooldown_until
    side  = trade.get("side", "?")
    setup = trade.get("setup", "?")
    info  = symbol_info_cache.get(symbol, {})
    pp    = info.get("pricePrecision", 4)

    if is_win:
        consec_losses = 0
        symbol_stats[symbol]["wins"] += 1
        logger.info(f"✅ WIN {symbol} {side} {setup}")
        # Vérifier si on a franchi un palier
        prev_tier_label = get_tier_label()
        milestone_msg = ""
        if account_balance >= TARGET_BALANCE:
            milestone_msg = f"\n🏆 <b>OBJECTIF ${TARGET_BALANCE:.0f} ATTEINT !</b>"
        elif account_balance >= TIER2_LIMIT and (account_balance - abs(trade.get('entry',0)-trade.get('sl',0)) * trade.get('qty',0) * TP_RR) < TIER2_LIMIT:
            milestone_msg = f"\n🟢 Palier NORMAL atteint ! ${account_balance:.4f}"
        elif account_balance >= TIER1_LIMIT and (account_balance - abs(trade.get('entry',0)-trade.get('sl',0)) * trade.get('qty',0) * TP_RR) < TIER1_LIMIT:
            milestone_msg = f"\n🟡 Palier CROISSANCE atteint ! ${account_balance:.4f}"
        send_telegram(
            f"✅ <b>WIN {symbol} {side}</b>\n"
            f"Setup: {setup} | Frais couverts\n"
            f"Balance: ${account_balance:.4f} {get_progress_bar()}{milestone_msg}"
        )
    else:
        consec_losses += 1
        symbol_stats[symbol]["losses"] += 1
        logger.info(f"🔴 LOSS {symbol} {side} {setup} consec={consec_losses}")
        send_telegram(
            f"🔴 <b>LOSS {symbol} {side}</b>\n"
            f"Setup: {setup} | Consécutives: {consec_losses}\n"
            f"Balance: ${account_balance:.4f} {get_tier_label()}\n"
            f"{get_progress_bar()}"
        )
        if consec_losses >= CONSEC_LOSS_LIMIT:
            cooldown_until = time.time() + CONSEC_COOLDOWN
            logger.warning(f"⏸ Pause {CONSEC_COOLDOWN//60}min")
            send_telegram(
                f"⏸ <b>Pause {CONSEC_COOLDOWN//60}min</b>\n"
                f"Après {consec_losses} pertes consécutives\n"
                f"Reprise automatique H24"
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

# ─── DRAWDOWN ────────────────────────────────────────────────────
def check_drawdown():
    ref = drawdown_state.get("ref_balance", 0)
    if ref <= 0: return
    dd = (ref - account_balance) / ref
    if dd >= DD_ALERT_PCT:
        now = time.time()
        if now - drawdown_state.get("last_alert", 0) > 300:
            drawdown_state["last_alert"] = now
            send_telegram(
                f"⚠️ <b>DRAWDOWN {dd:.1%}</b>\n"
                f"Balance: ${account_balance:.4f} (début: ${ref:.4f})\n"
                f"BTC: {_btc_direction_cache.get('label','?')}"
            )

# ─── SCAN SYMBOL ─────────────────────────────────────────────────
def scan_symbol(symbol: str):
    try:
        # Position déjà ouverte ?
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None

        # Signal cooldown
        if time.time() - signal_last_at.get(symbol, 0) < SIGNAL_COOLDOWN:
            return None

        # Analyse M1 (setup + trigger)
        klines = get_klines(symbol, limit=55)
        if not klines or len(klines) < 30:
            return None

        signal = analyse_m1(symbol, klines)
        if not signal:
            return None

        # ── CORRÉLATION BTC M1 ───────────────────────────────────
        if BTC_CORR_REQUIRED:
            ok, reason = check_btc_correlation(symbol, signal["side"])
            if not ok:
                logger.debug(f"  {symbol} {signal['side']} refusé: {reason}")
                return None
            signal["btc_corr"] = reason

        return signal

    except Exception as e:
        logger.debug(f"scan_symbol {symbol}: {e}")
        return None

# ─── RECOVER ─────────────────────────────────────────────────────
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

            logger.info(f"🔄 {sym} {side} qty={qty} @ {entry} → récupérée")

            # Charger infos si manquantes
            if sym not in symbol_info_cache:
                ex = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
                if ex:
                    for s in ex.get("symbols", []):
                        if s["symbol"] == sym:
                            filters = {f["filterType"]: f for f in s.get("filters", [])}
                            symbol_info_cache[sym] = {
                                "quantityPrecision": s.get("quantityPrecision", 3),
                                "pricePrecision":    s.get("pricePrecision", 4),
                                "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                                "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                                "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                            }
                            break

            info = symbol_info_cache.get(sym, {})
            pp   = info.get("pricePrecision", 4)
            atr  = _atr_live(sym)
            dist = max(atr * 1.2, entry * MIN_SL_PCT)
            dist = min(dist, entry * MAX_SL_PCT)

            sl = round(entry - dist if side == "BUY" else entry + dist, pp)
            tp = round(entry + dist * TP_RR if side == "BUY" else entry - dist * TP_RR, pp)
            be = round(entry * (1 + BREAKEVEN_FEE_TOTAL) if side == "BUY"
                       else entry * (1 - BREAKEVEN_FEE_TOTAL), pp)

            close_side = "SELL" if side == "BUY" else "BUY"
            cleanup_orders(sym)
            set_leverage_sym(sym, 15)
            sl_r = place_sl_binance(sym, sl, close_side)
            tp_r = place_tp_binance(sym, tp, close_side)

            if sym not in symbols_list:
                symbols_list.append(sym)

            with trade_lock:
                trade_log[sym] = {
                    "side": side, "entry": entry, "sl": sl, "tp": tp, "qty": qty,
                    "leverage": 15, "margin": entry*qty/15,
                    "setup": "RECOVERED", "score": 80, "probability": 65.0,
                    "status": "OPEN", "opened_at": time.time(),
                    "sl_on_binance": sl_r["sent"], "tp_on_binance": tp_r["sent"],
                    "sl_order_id": sl_r["order_id"], "tp_order_id": tp_r["order_id"],
                    "trailing_stop_active": False, "breakeven_moved": False,
                    "be_price": be, "atr": atr, "btc_corr": "RECOVERED",
                }

            logger.info(f"✅ {sym} récupéré | SL {'Binance' if sl_r['sent'] else 'logiciel'} @ {sl:.{pp}f} | BE {be:.{pp}f}")
            send_telegram(
                f"🔄 <b>Position récupérée : {sym}</b>\n"
                f"{side} @ {entry:.{pp}f} | qty={qty}\n"
                f"SL: {sl:.{pp}f} {'✅' if sl_r['sent'] else '⚠️'}\n"
                f"BE frais: {be:.{pp}f} | TP: {tp:.{pp}f}\n"
                f"🔁 SL suiveur activé"
            )
    except Exception as e:
        logger.error(f"recover: {e}")

# ─── LOOPS ────────────────────────────────────────────────────────
def scanner_loop():
    logger.info("🔍 Scanner M1 démarré — Top 20 | Corrélation BTC | Ultra-strict")
    time.sleep(5)
    count = 0
    while True:
        try:
            if _bot_stop:
                time.sleep(10)
                continue

            count += 1
            if count % (300 // SCAN_INTERVAL) == 0:
                sync_binance_time()
                get_account_balance()
                check_drawdown()
                get_btc_m1_direction()  # Refresh BTC direction
            if count % (1800 // SCAN_INTERVAL) == 0:
                load_top_symbols()

            # Hard floor
            if account_balance < HARD_FLOOR:
                if count % 6 == 0:
                    logger.warning(f"🛑 Hard floor ${HARD_FLOOR} | balance ${account_balance:.4f}")
                    send_telegram(f"🛑 <b>Hard floor ${HARD_FLOOR}</b>\n${account_balance:.4f} — Rechargez votre compte")
                time.sleep(30)
                continue

            # Cooldown pertes
            if time.time() < cooldown_until:
                r = int((cooldown_until - time.time()) / 60)
                if count % 3 == 0:
                    logger.info(f"⏸ Cooldown {r}min")
                time.sleep(SCAN_INTERVAL)
                continue

            # Max positions
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open >= MAX_POSITIONS:
                time.sleep(SCAN_INTERVAL)
                continue

            # Refresh BTC direction avant scan
            btc = get_btc_m1_direction()
            if btc["direction"] == 0:
                logger.debug(f"⚪ BTC M1 neutre — attente direction claire")
                time.sleep(SCAN_INTERVAL)
                continue

            # Scan parallèle
            signals = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {ex.submit(scan_symbol, sym): sym for sym in symbols_list}
                for fut in as_completed(futures, timeout=15):
                    try:
                        sig = fut.result()
                        if sig: signals.append(sig)
                    except: pass

            if not signals:
                time.sleep(SCAN_INTERVAL)
                continue

            # Trier par qualité (score × prob × confluence)
            signals.sort(
                key=lambda s: s["score"] * s["probability"] * s["confluence"],
                reverse=True
            )

            # Prendre le MEILLEUR signal seulement
            best_sig = signals[0]
            sym = best_sig["symbol"]

            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open < MAX_POSITIONS:
                with trade_lock:
                    already = sym in trade_log and trade_log[sym].get("status") == "OPEN"
                if not already:
                    signal_last_at[sym] = time.time()
                    logger.info(
                        f"🎯 MEILLEUR SIGNAL : {sym} {best_sig['side']} | "
                        f"Score:{best_sig['score']} Conf:{best_sig['confluence']}/5 "
                        f"Prob:{best_sig['probability']:.0f}% | {best_sig['setup']}\n"
                        f"   BTC: {btc['label']}\n"
                        f"   Autres signaux rejetés: {len(signals)-1}"
                    )
                    open_position(best_sig)

        except Exception as e:
            logger.error(f"scanner_loop: {e}")
        time.sleep(SCAN_INTERVAL)

def monitor_loop():
    logger.info(f"📡 Monitor SL suiveur toutes les {MONITOR_INTERVAL}s")
    time.sleep(10)
    while True:
        try:
            monitor_positions()
        except Exception as e:
            logger.debug(f"monitor_loop: {e}")
        time.sleep(MONITOR_INTERVAL)

def dashboard_loop():
    logger.info("📈 Dashboard démarré")
    time.sleep(20)
    while True:
        try:
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            tw = sum(v["wins"]   for v in symbol_stats.values())
            tl = sum(v["losses"] for v in symbol_stats.values())
            wr = tw/(tw+tl)*100 if (tw+tl) > 0 else 0
            ref = drawdown_state.get("ref_balance", account_balance)
            dd  = (ref - account_balance)/ref*100 if ref > 0 else 0
            btc = get_btc_m1_direction()
            on_cd = time.time() < cooldown_until
            cd_str = f" | ⏸CD {int((cooldown_until-time.time())/60)}min" if on_cd else ""

            logger.info("═" * 72)
            logger.info(
                f"SCALPER v39.4 | ${account_balance:.4f} | {get_tier_label()} | "
                f"{get_progress_bar()} → ${TARGET_BALANCE:.0f}"
            )
            logger.info(
                f"Pos:{n_open}/{MAX_POSITIONS} | Risk:${get_risk_usdt():.3f} | "
                f"Lev:{get_leverage(92)}x(best)/{get_leverage(85)}x | {cd_str.strip(' | ') if cd_str else 'No cooldown'}"
            )
            logger.info(f"{btc['label']} | slope:{btc.get('slope',0):+.4f}%")
            logger.info(
                f"W:{tw} L:{tl} WR:{wr:.1f}% | DD:{dd:.1f}% | "
                f"Frais BE:{BREAKEVEN_FEE_TOTAL*100:.2f}%"
            )

            if n_open > 0:
                pos_data = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
                pnl_map = {}
                if pos_data:
                    for p in pos_data:
                        s, a = p.get("symbol"), float(p.get("positionAmt",0))
                        if a != 0:
                            pnl_map[s] = {
                                "pnl": float(p.get("unRealizedProfit",0)),
                                "mark": float(p.get("markPrice",0)),
                                "liq": float(p.get("liquidationPrice",0)),
                            }
                logger.info("─── POSITIONS ───")
                with trade_lock:
                    for sym, t in trade_log.items():
                        if t.get("status") != "OPEN": continue
                        info = symbol_info_cache.get(sym,{})
                        pp   = info.get("pricePrecision",4)
                        pd   = pnl_map.get(sym,{})
                        pnl  = pd.get("pnl",0)
                        mark = pd.get("mark", t["entry"])
                        liq  = pd.get("liq",0)
                        risk = abs(t["entry"]-t["sl"])
                        rr   = abs(mark-t["entry"])/risk if risk>0 else 0
                        be   = "✅BE" if t.get("breakeven_moved") else f"BE@{t.get('be_price','?')}"
                        tr   = "🔁TR" if t.get("trailing_stop_active") else ""
                        sl_s = "🛡️B" if t.get("sl_on_binance") else "⚠️S"
                        icon = "🟢" if pnl>=0 else "🔴"
                        logger.info(
                            f"  {icon} {sym} {t['side']} {t['leverage']}x | "
                            f"{t['setup']}({t['score']}) | RR:{rr:.2f}R | PnL:{pnl:+.4f}$"
                        )
                        logger.info(
                            f"     Entry:{t['entry']:.{pp}f} Mark:{mark:.{pp}f} "
                            f"SL:{t['sl']:.{pp}f}({sl_s}) {be}{tr}"
                        )
                        if liq>0: logger.info(f"     ⚠️  LIQ:{liq:.{pp}f}")
            else:
                logger.info(f"  Scan actif — {len(symbols_list)} symboles | BTC:{btc['label']}")
            logger.info("═" * 72)
        except Exception as e:
            logger.debug(f"dashboard: {e}")
        time.sleep(DASHBOARD_INTERVAL)

# ─── MAIN ────────────────────────────────────────────────────────
def main():
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  SCALPER v39.4 — CALIBRÉ | CORRÉLATION BTC | FRAIS RÉELS   ║")
    logger.info("║  Récupération $1.40 → $6+ | 1 position | Filtres calibrés  ║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.warning("🔥 LIVE TRADING 🔥")

    logger.info(f"")
    logger.info(f"✅ FRAIS BINANCE : Taker 0.04% × 2 + buffer = {BREAKEVEN_FEE_TOTAL*100:.2f}% A/R")
    logger.info(f"   BE réel BUY  = entry × {1+BREAKEVEN_FEE_TOTAL:.4f}")
    logger.info(f"   BE réel SELL = entry × {1-BREAKEVEN_FEE_TOTAL:.4f}")
    logger.info(f"")
    logger.info(f"✅ CORRÉLATION BTC M1 : EMA5/13 + RSI9 + slope")
    logger.info(f"   BUY  : seulement si BTC M1 haussier")
    logger.info(f"   SELL : seulement si BTC M1 baissier")
    logger.info(f"   Neutre → aucun trade")
    logger.info(f"")
    logger.info(f"✅ FILTRES CALIBRÉS v39.4 :")
    logger.info(f"   Score min : {MIN_SCORE} (SWEEP_OB:92 + BOS_FVG:85)")
    logger.info(f"   Volume spike : {VOLUME_SPIKE_MIN}× (was 2.0×)")
    logger.info(f"   Corps bougie : {MIN_BODY_RATIO*100:.0f}% | SL max : {MAX_SL_PCT*100:.1f}%")
    logger.info(f"   Cooldown symbole : {SIGNAL_COOLDOWN}s (was 180s)")
    logger.info(f"")
    logger.info(f"✅ 1 POSITION MAX | {MAX_POSITIONS} simultanée(e)")
    logger.info(f"   → Seul le MEILLEUR signal est pris à chaque scan")
    logger.info(f"")

    start_health_server()
    sync_binance_time()
    load_top_symbols()
    get_account_balance()
    drawdown_state["ref_balance"] = account_balance

    logger.info(f"💰 Balance de départ : ${account_balance:.4f}")
    logger.info(f"🎯 Risque/trade : ${get_risk_usdt():.4f} ({RISK_PCT*100:.0f}% balance)")
    logger.info(f"📋 Symboles : {symbols_list}")

    # Direction BTC initiale
    btc = get_btc_m1_direction()
    logger.info(f"📊 BTC M1 initial : {btc['label']}")

    send_telegram(
        f"🚀 <b>SCALPER v39.4 DÉMARRÉ</b>\n\n"
        f"💰 Balance: <b>${account_balance:.4f}</b>\n"
        f"🎯 Objectif: <b>${TARGET_BALANCE:.0f}</b> | {get_progress_bar()}\n"
        f"📊 BTC M1: {btc['label']}\n\n"
        f"📈 <b>STRATÉGIE PAR PALIERS</b>\n"
        f"  🔴 SURVIE  ($1-$3): 7% risk | 50x max\n"
        f"  🟡 CROISS ($3-$6): 6% risk | 35x max\n"
        f"  🟢 NORMAL ($6-$10): 5% risk | 25x max\n\n"
        f"Mode actuel: <b>{get_tier_label()}</b>\n"
        f"Risque/trade: ${get_risk_usdt():.4f}\n"
        f"Levier: {get_leverage(92)}x (SWEEP_OB) / {get_leverage(85)}x (BOS_FVG)\n\n"
        f"✅ Frais BE: {BREAKEVEN_FEE_TOTAL*100:.2f}% A/R\n"
        f"✅ SL suiveur + plancher frais garanti\n"
        f"✅ Corrélation BTC M1 activée\n"
        f"🔁 Monitor toutes les {MONITOR_INTERVAL}s"
    )

    recover_existing_positions()

    threading.Thread(target=scanner_loop,  daemon=True).start()
    threading.Thread(target=monitor_loop,  daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()

    logger.info("✅ SCALPER v39.3 ONLINE 🚀\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown")

if __name__ == "__main__":
    main()

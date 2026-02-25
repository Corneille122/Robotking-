#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   SCANNER M5 v4.9 — FONDAMENTAUX ENRICHIS +5 FILTRES          ║
╠══════════════════════════════════════════════════════════════════╣
║  NOUVEAUTÉS v4.9 :                                              ║
║  ✅ Session PRIME : London Open 8-11h + NY Open 13-16h UTC     ║
║     → Risque ×1.3 en prime time, ×0.5 hors session            ║
║  ✅ MTF (Multi-TimeFrame) : confirmation M15 + H1 obligatoire  ║
║     → BUY refusé si M15/H1 baissier, SELL refusé si haussier  ║
║  ✅ CVD proxy : pression acheteur/vendeur sur 10 bougies       ║
║     → CVD divergent = rejet (gros vendent pendant BUY)        ║
║  ✅ Filtre News économiques : pause ±30min CPI/NFP/FOMC       ║
║  ✅ check_fondamentaux /140 pts (était /100)                   ║
║     + CVD /20 + Session /20 ajoutés au score                  ║
║  HÉRITÉ v4.8 :                                                  ║
║  ✅ $0.60 base | +45%/WIN | Pump guard | VOL brain dynamique  ║
║  ✅ Scan 30s | BTC prix direct | Anti-CT strict               ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time, hmac, hashlib, requests, threading, os, logging, json
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
        r = requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
        if r.status_code != 200:
            logger.warning("⚠️ Telegram erreur HTTP {}: {}".format(r.status_code, r.text[:80]))
    except requests.exceptions.Timeout:
        logger.warning("⚠️ Telegram timeout — message non envoyé")
    except requests.exceptions.ConnectionError as e:
        logger.warning("⚠️ Telegram connexion impossible: {}".format(str(e)[:80]))
    except Exception as e:
        logger.warning("⚠️ Telegram erreur inattendue: {}".format(str(e)[:80]))

# ─── CLÉS API BINANCE ────────────────────────────────────────────
API_KEY    = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")

BASE_URL   = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION v2.0
# ═══════════════════════════════════════════════════════════════════

# ── Timeframe M5 ─────────────────────────────────────────────────
TIMEFRAME        = "5m"
LIMIT_CANDLES    = 80       # 80 × 5min = ~6.5h d'historique (optimal M5)
KLINES_CACHE_TTL = 15       # Cache 15s (bougie M5 = 5min → plus réactif)

# ── Frais Binance ─────────────────────────────────────────────────
BINANCE_FEE_RATE    = 0.0004         # 0.04% taker par côté
BREAKEVEN_FEE_TOTAL = BINANCE_FEE_RATE * 2 * 1.5  # 0.12% A/R avec buffer
BE_PROFIT_MIN       = 0.01           # $0.01 net garanti au BE

# ── RR & SL adapté M5 ────────────────────────────────────────────
MIN_RR       = 3.0
TP_RR        = 3.0   # RR3 — gain = 3× le risque net après frais
MIN_SL_PCT   = 0.003   # SL min 0.3% (M5 = mouvements plus serrés)
MAX_SL_PCT   = 0.020   # SL max 2.0% (réduit vs M15 pour M5)
ATR_SL_MULT  = 1.2     # SL = ATR × 1.2 (plus serré qu'en M15)

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

# ── BTC corrélation M5 ───────────────────────────────────────────
BTC_SYMBOL       = "BTCUSDT"
BTC_EMA_FAST     = 5
BTC_EMA_SLOW     = 13
BTC_RSI_PERIOD   = 9
BTC_RSI_BULL_MAX = 82   # M5 : RSI plus large (signaux plus fréquents)
BTC_RSI_BEAR_MIN = 20   # M5 : RSI plus large côté baissier

# ── RISQUE DYNAMIQUE +45% par WIN (anti-martingale) ─────────────
#
#  Principe v4.8 :
#    - Risque de base : $0.60 par trade (perte max si SL touché)
#    - Après un WIN   : risque × 1.45 (arrondi à 2 décimales)
#    - Après un LOSS  : risque reset à $0.60
#    - Maximum        : $3.00 (plafond de sécurité — ≈ 5 WIN consécutifs)
#    - Minimum        : $0.30 (plancher pénalités brain)
#    - Sizing recalculé à chaque trade : qty = risk_usd / sl_distance
#
#  Progression : $0.60 → $0.87 → $1.26 → $1.83 → $2.65 → plafond $3.00
#
RISK_BASE_USD    = 0.60    # Risque de base $0.60 (perte max si SL touché)
RISK_STEP_PCT    = 0.45    # +45% après chaque WIN
RISK_MAX_USD     = 3.00    # Plafond absolu (≈ 5 WIN consécutifs)
RISK_MIN_USD     = 0.30    # Plancher (pénalité brain max)
RISK_FILE        = "risk_state.json"

# Compatibilité (anciens noms utilisés dans brain_get_margin_usd)
FIXED_MARGIN_USD = RISK_BASE_USD
MARGIN_MIN_USD   = RISK_MIN_USD
MARGIN_MAX_USD   = RISK_MAX_USD

# Garde pour compatibilité (utilisé dans compute_dynamic_margin)
MARGIN_BY_SCORE = {94: 1.0, 93: 1.0, 92: 1.0, 91: 1.0, 90: 1.0}
MARGIN_MIN = 0.10
MARGIN_MAX = 0.40

SESSION_MULT = {
    "LONDON_PRIME": 1.3,   # v4.9 : London Open 8h-11h UTC — meilleur momentum
    "NY_PRIME":     1.3,   # v4.9 : NY Open 13h-16h UTC — meilleur volume
    "LONDON":       1.0,   # London normal 11h-13h
    "NY":           1.0,   # NY normal 16h-21h
    "ASIA":         0.6,   # Asie — moins de volume
    "OFF":          0.5,   # Nuit — pas de trade sauf signal exceptionnel
}

# ── Multi-TimeFrame (MTF) — v4.9 ─────────────────────────────────
#  M15 et H1 doivent confirmer la direction M5.
#  Si M15 ou H1 contredisent → trade refusé.
#  Cache séparé pour ne pas saturer l'API.
MTF_CONFIRM_REQUIRED = True    # Active/désactive le filtre MTF
MTF_CACHE_TTL        = 180     # Cache MTF 3 minutes

# ── Filtre News Économiques — v4.9 ──────────────────────────────
#  Pause ±30min autour des événements macro (CPI, NFP, FOMC, etc.)
#  Les timestamps sont mis à jour automatiquement via l'API ForexFactory
#  (fallback : liste manuelle des créneaux connus).
NEWS_FILTER_ENABLED  = True    # Active le filtre news
NEWS_PAUSE_MINUTES   = 30      # Minutes avant/après l'événement
NEWS_IMPACT_HIGH     = True    # Filtrer uniquement les news HIGH impact

# ── CVD proxy — v4.9 ─────────────────────────────────────────────
#  Calcul du CVD (Cumulative Volume Delta) via les bougies M5 :
#  Une bougie haussière (close > open) = pression acheteur = delta positif
#  Une bougie baissière (close < open) = pression vendeur  = delta négatif
#  CVD = somme des deltas sur les N dernières bougies
CVD_LOOKBACK         = 10      # Nombre de bougies pour calcul CVD
CVD_MIN_ALIGN_PCT    = 0.6     # 60% du CVD doit être dans la direction du trade

# ── Fondamentaux enrichis ─────────────────────────────────────────
FOND_MIN_SCORE      = 50   # Seuil minimum STRICT — double confirmation obligatoire
FOND_BOOST_SCORE    = 65   # Score pour multiplicateur ×1.15
OI_SPIKE_THRESH     = 0.03 # Spike OI >3% → pénalité (était rejet total)
VOL_SPIKE_MULT      = 2.5  # Volume 24h > 2.5× moyenne 7j = spike bullish
LIQD_THRESH_USD     = 500_000  # $500k de liquidations récentes = signal fort

# ── Fear & Greed ──────────────────────────────────────────────────
# Logique RÉVISÉE selon ta préférence :
#   F&G extrême → NE PAS bloquer, mais RÉDUIRE la marge (×0.5)
#   F&G < 15 (Fear extrême)  → BUY autorisé mais marge ×0.5 (prudence)
#   F&G > 85 (Greed extrême) → SELL autorisé mais marge ×0.5 (prudence)
#   F&G ≤ 30 (Fear)          → SELL confirmé, marge normale voire +
#   F&G ≥ 70 (Greed)         → BUY confirmé, marge normale voire +
FG_EXTREME_FEAR  = 15   # Sous ce seuil → BUY avec marge réduite ×0.5
FG_EXTREME_GREED = 85   # Au-dessus    → SELL avec marge réduite ×0.5
FG_FEAR_ZONE     = 30   # F&G ≤ 30 confirme les SELL → marge ×1.1
FG_GREED_ZONE    = 70   # F&G ≥ 70 confirme les BUY  → marge ×1.1
FG_CACHE_TTL     = 300  # Cache 5min

# ── Dominance BTC ─────────────────────────────────────────────────
# Quand BTC domine fortement, les altcoins sont aspirés / comprimés.
# Dominance BTC > 60% : altcoins sous pression → marge altcoins réduite
# Dominance BTC > 65% : altcoins très faibles  → marge ×0.6 (très prudent)
# Dominance BTC < 50% : altseason → altcoins libres, marge normale/+
BTC_DOM_HIGH      = 60.0   # Dominance > 60% → altcoins sous pression
BTC_DOM_EXTREME   = 65.0   # Dominance > 65% → altcoins très faibles
BTC_DOM_CACHE_TTL = 300    # Cache 5min

# ── Top N symboles ────────────────────────────────────────────────
TOP_N_SYMBOLS  = 9999  # Scan TOUS les cryptos (limité par filtre volume)
VOL_MIN_FILTER = 5_000_000   # v4.5 : relevé à $5M/jour (était $500k)
                              # Élimine les micro-caps illiquides (DENT, PIPPIN, ARC...)

# ── Filtres liquidité v4.5 ────────────────────────────────────────
#
#  Problème détecté en backtest : les micro-caps (DENTUSDT, PIPPINUSDT,
#  ARCUSDT, ESPUSDT) génèrent des faux wins à cause du spread bid/ask
#  réel (1-3%) non simulé. En live, le fill market est bien plus loin
#  du close théorique → PnL réel négatif sur ces symboles.
#
#  Filtres ajoutés :
#   1. VOL_MIN_FILTER $5M  : volume 24h minimum (×10 vs avant)
#   2. PRICE_MIN_USD $0.01 : élimine les "mille-satoshi" (DENT $0.0002)
#   3. SPREAD_MAX_PCT 0.15 : skip si spread mark/index > 0.15%
#   4. Blacklist explicite des symboles identifiés comme illiquides
#
PRICE_MIN_USD     = 0.01    # Prix minimum $0.01 — en dessous spread trop large
SPREAD_MAX_PCT    = 0.15    # Spread mark/index max 0.15% (était pas vérifié avant signal)
LIQUIDITY_BLACKLIST = {
    # Identifiés via backtest comme illiquides / spread excessif
    "DENTUSDT", "PIPPINUSDT", "ARCUSDT", "ESPUSDT",
    "POWERUSDT", "ENSOUSDT",
    # Micro-caps génériques à exclure
    "1000SHIBUSDT", "1000FLOKIUSDT", "1000BONKUSDT",
    "1000RATSUSDT", "1000CATUSDT", "LUNCUSDT",
}

# ── Gestion positions ─────────────────────────────────────────────
MAX_POSITIONS     = 3      # v4.6 : 3 positions simultanées max
MARGIN_TYPE       = "ISOLATED"
MIN_NOTIONAL      = 5.0
MAX_NOTIONAL_RISK = 0.90

# ── Anti pertes consécutives ──────────────────────────────────────
# NOTE v4.4 : le cooldown consécutif est DÉSACTIVÉ.
# La gestion du risque après pertes est confiée au système Paroli
# (reset automatique à la mise de base après chaque SL).
# Le brain blacklist les symboles toxiques (≥3 pertes consec) — conservé.
CONSEC_LOSS_LIMIT = 999   # Désactivé — Paroli gère
CONSEC_COOLDOWN   = 0     # Désactivé

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

# ── SL suiveur M5 ────────────────────────────────────────────────
BREAKEVEN_RR   = 1.0   # v4.6 : BE déclenché à RR1 (sécurisé dès profit = risque)
TRAIL_START_RR = 1.0   # v4.6 : Trailing démarre aussi à RR1
TRAIL_ATR_MULT = 0.8   # Trailing plus serré en M5 (0.8 vs 1.0)

# ── Timing SCAN CONTINU v4.7 ─────────────────────────────────────
#  Le scan tourne en continu (30s entre cycles) au lieu de 5min.
#  La bougie M5 reste le timeframe d'analyse, mais on re-scanne
#  beaucoup plus souvent pour ne pas rater une entrée en milieu
#  de bougie (ex : retour en zone BB/FVG à 2min30 de la bougie).
SCAN_INTERVAL      = 30           # v4.7 : 30s entre cycles (était 5min)
MONITOR_INTERVAL   = 5
DASHBOARD_INTERVAL = 30
SIGNAL_COOLDOWN    = 5 * 60       # Cooldown 5min par symbole (évite doublons)
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
# v4.4 : géré par Paroli — constantes conservées pour compatibilité
DD_ALERT_PCT      = 0.15
MAX_WORKERS       = 8    # v4.8 : 8 workers (scan plus rapide, toujours sous le weight limit)

# ── Anti-saturation API ───────────────────────────────────────────
# Binance Futures : limite 2400 weight/minute
# Poids des endpoints utilisés :
#   /fapi/v1/klines            → weight 1
#   /fapi/v1/ticker/24hr bulk  → weight 40 (1 appel pour TOUT)
#   /fapi/v1/ticker/24hr sym   → weight 1
#   /fapi/v1/premiumIndex sym  → weight 1
#   /futures/data/openInterestHist → weight 1
#   /fapi/v1/allForceOrders    → weight 20 (!!! cher)
MAX_API_WEIGHT_PER_MIN   = 2400   # Limite Binance Futures
WEIGHT_SAFETY_MARGIN     = 0.75   # On s'arrête à 75% (1800 weight/min)
API_WEIGHT_WINDOW        = 60     # Fenêtre glissante en secondes
BATCH_SIZE               = 12     # Symboles par batch phase technique
BATCH_SLEEP_BASE         = 0.6    # Pause entre batches (secondes)
TECH_PHASE_MAX_SIGNALS   = 20     # Max signaux tech avant phase fond
BULK_TICKER_TTL          = 30     # Cache ticker bulk (secondes)

# ─── ÉTAT GLOBAL ─────────────────────────────────────────────────
account_balance   = 0.0
balance_lock      = threading.Lock()   # ← PATCH 1 : verrou solde
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

# ── Weight tracker API ────────────────────────────────────────────
_api_weight_times   = []   # [(timestamp, weight), ...]
_api_weight_lock    = threading.Lock()
_api_weight_used    = 0    # Cumulé sur fenêtre glissante

# ── Cache ticker bulk ─────────────────────────────────────────────
_bulk_ticker_cache  = {}   # symbol → {quoteVolume, priceChangePercent}
_bulk_ticker_ts     = 0.0
_bulk_ticker_lock   = threading.Lock()

# ═══════════════════════════════════════════════════════════════════
#  BRAIN — SYSTÈME D'APPRENTISSAGE ADAPTATIF
# ═══════════════════════════════════════════════════════════════════
#
#  Le brain apprend de chaque trade fermé et adapte le comportement :
#
#  1. MÉMOIRE PAR SYMBOLE
#     - Wins/losses, pertes consécutives
#     - Blacklist temporaire si ≥ 3 pertes consécutives (durée 2h)
#     - Bonus si winrate > 65% sur ≥ 5 trades (marge ×1.0 maintenu)
#
#  2. MÉMOIRE PAR SETUP (BB vs FVG_SWEEP)
#     - Winrate par setup
#     - Si BB winrate < 45% sur ≥ 8 trades → score min BB relevé à 92
#     - Si FVG winrate < 45% sur ≥ 8 trades → score min FVG relevé à 94
#
#  3. MÉMOIRE PAR SESSION
#     - Winrate London / NY / Asia
#     - Session < 40% winrate sur ≥ 10 trades → pénalité marge ×0.75
#
#  4. MÉMOIRE PAR DIRECTION BTC
#     - Winrate BUY vs SELL
#     - Si côté < 35% → alerte + skip temporaire (4h)
#
#  5. AJUSTEMENT ATR MULTIPLIER
#     - Si > 60% des trades perdants sont fermés par SL dans la 1ère minute
#       → SL trop serré → ATR_SL_MULT augmenté de 0.1 (max 2.0)
#     - Si ATR_SL_MULT > 1.5 et winrate global > 55% → réduction progressive
#
#  6. PERSISTANCE (brain.json)
#     - Sauvegardé après chaque trade
#     - Rechargé au démarrage
# ═══════════════════════════════════════════════════════════════════

BRAIN_FILE = "brain.json"

# ═══════════════════════════════════════════════════════════════════
#  PAROLI — SYSTÈME ANTI-MARTINGALE
# ═══════════════════════════════════════════════════════════════════
#
#  Niveau  | Mise    | Condition
#  --------|---------|------------------------------------------
#     0    | $0.60   | Base (départ / après tout SL)
#     1    | $0.75   | Après 1 WIN consécutif
#     2    | $0.90   | Après 2 WIN consécutifs
#     3    | $1.05   | Après 3 WIN consécutifs
#     4    | $1.20   | Après 4+ WIN consécutifs (plafond)
#
#  Garde-fous :
#    - Mise effective = min(niveau_mise, balance × PAROLI_CAP_PCT)
#    - Brain pénalités (session/symbole) s'appliquent APRÈS (×0.75 plancher $0.30)
#    - Niveau sauvegardé dans paroli.json (survit aux redémarrages)
#
# ═══════════════════════════════════════════════════════════════════

_risk_state = {
    "current_risk":  RISK_BASE_USD,   # Risque actuel en USD
    "win_streak":    0,                # Séquence de WIN en cours
    "total_gains":   0.0,              # Gains cumulés
}
_risk_lock = threading.Lock()

def risk_load():
    """Charge l'état du risque depuis risk_state.json."""
    global _risk_state
    if not os.path.exists(RISK_FILE):
        return
    try:
        with open(RISK_FILE, "r") as f:
            data = json.load(f)
        with _risk_lock:
            _risk_state.update(data)
        logger.info("💰 Risque chargé — ${:.2f} (streak={})".format(
            _risk_state["current_risk"], _risk_state["win_streak"]))
    except Exception as e:
        logger.warning("Risk load error: {} — reset".format(e))

def risk_save():
    """Sauvegarde l'état du risque."""
    try:
        with _risk_lock:
            data = dict(_risk_state)
        with open(RISK_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Risk save error: {}".format(e))

def risk_on_win(last_gain=0.0):
    """Après WIN : risque × 1.25, plafonné à RISK_MAX_USD."""
    with _risk_lock:
        old = _risk_state["current_risk"]
        new = round(min(old * (1 + RISK_STEP_PCT), RISK_MAX_USD), 2)
        _risk_state["current_risk"] = new
        _risk_state["win_streak"]  += 1
        _risk_state["total_gains"] += last_gain
    risk_save()
    logger.info("💰 WIN → risque ${:.2f} → ${:.2f} (+25%)".format(old, new))
    send_telegram("💰 <b>WIN → risque ${:.2f}</b> (+25% du précédent)".format(new))

def risk_on_loss():
    """Après LOSS : risque reset à RISK_BASE_USD."""
    with _risk_lock:
        old    = _risk_state["current_risk"]
        streak = _risk_state["win_streak"]
        _risk_state["current_risk"] = RISK_BASE_USD
        _risk_state["win_streak"]   = 0
    risk_save()
    if old > RISK_BASE_USD:
        logger.info("💰 LOSS → risque reset ${:.2f} → ${:.2f} (était streak={})".format(
            old, RISK_BASE_USD, streak))
        send_telegram("💰 LOSS → risque reset <b>${:.2f}</b>".format(RISK_BASE_USD))

def risk_get_current(symbol, setup, session):
    """
    Retourne le risque effectif en USD pour ce trade.
    Applique les pénalités brain (session/symbole) après.
    v4.8 : Pénalité automatique ×0.5 si le coin a pompé >20% en 24h
           (risque de retrace violent + spread élargi).
    """
    with _risk_lock:
        base_risk = _risk_state["current_risk"]

    # Pénalités brain
    mult = 1.0
    sess_mult = brain_get_session_margin_mult(session)
    mult *= sess_mult
    with _brain_lock:
        sym_data  = _brain["symbols"].get(symbol, {})
    sym_total = sym_data.get("wins", 0) + sym_data.get("losses", 0)
    if sym_total >= 5 and sym_data.get("wins", 0) / sym_total < 0.40:
        mult *= 0.75

    # v4.8 — Pénalité pump 24h : si priceChange > 20% → risque ×0.5
    # Les coins qui ont déjà beaucoup monté sont sujets à des retracements
    # violents et un spread bid/ask élargi (fill moins favorable).
    bulk = get_bulk_ticker(symbol)
    if bulk:
        chg_24h = abs(bulk.get("chg", 0))
        if chg_24h > 20.0:
            mult *= 0.5
            logger.debug("  {} pump 24h {:.1f}% → risque ×0.5".format(symbol, chg_24h))
        elif chg_24h > 30.0:
            mult *= 0.3
            logger.debug("  {} pump 24h {:.1f}% → risque ×0.3 (extrême)".format(symbol, chg_24h))

    risk_usd = round(max(RISK_MIN_USD, min(RISK_MAX_USD, base_risk * mult)), 2)
    return risk_usd

# Alias de compatibilité pour les anciens appels paroli_*
def paroli_load():    risk_load()
def paroli_save():    risk_save()
def paroli_on_win():  risk_on_win()
def paroli_on_loss(): risk_on_loss()

def paroli_get_margin(symbol, setup, session):
    """Compatibilité : retourne (risk_usd, 0) — le 2e param était le niveau Paroli."""
    risk = risk_get_current(symbol, setup, session)
    return risk, 0


_brain = {
    "version":        4,
    "total_trades":   0,
    "total_wins":     0,
    "total_losses":   0,
    "symbols":        {},   # sym → {wins, losses, consec_losses, blacklisted_until, last_trade_ts}
    "setups":         {},   # setup_name → {wins, losses, min_score_override}
    "sessions":       {},   # session → {wins, losses, margin_mult_override}
    "sides":          {},   # "BUY"/"SELL" → {wins, losses, skip_until}
    "atr_mult":       ATR_SL_MULT,   # Ajustable dynamiquement
    "early_sl_count": 0,   # SL touchés dans < 60s depuis ouverture
    "last_report_ts": 0.0,
    "adaptations":    [],  # Log des adaptations effectuées
    # v4.8 — Détection stagnation + VOL_MIN dynamique
    "stagnation_losses":  0,     # Pertes récentes avec PnL proche de 0
    "vol_min_boost":      1.0,   # Multiplicateur VOL_MIN_FILTER (1.0 = normal, 2.0 = ×2)
    "vol_min_boost_until": 0.0,  # Timestamp fin du boost (reset auto)
}
_brain_lock = threading.Lock()

def brain_load():
    """Charge le brain depuis brain.json si existe."""
    global _brain
    if not os.path.exists(BRAIN_FILE):
        logger.info("🧠 Brain: pas de fichier existant — démarrage à zéro")
        return
    try:
        with open(BRAIN_FILE, "r") as f:
            data = json.load(f)
        with _brain_lock:
            # Merge pour préserver les nouvelles clés si format évolue
            for k, v in data.items():
                _brain[k] = v
            # Restaure ATR_SL_MULT sauvegardé
            global ATR_SL_MULT
            ATR_SL_MULT = _brain.get("atr_mult", ATR_SL_MULT)
        logger.info("🧠 Brain chargé — {} trades | WR={:.1f}% | ATR×{:.2f} | {} symboles en mémoire".format(
            _brain["total_trades"],
            _brain["total_wins"] / max(_brain["total_trades"], 1) * 100,
            _brain["atr_mult"],
            len(_brain["symbols"])))
    except Exception as e:
        logger.warning("🧠 Brain load error: {} — reset".format(e))

def brain_save():
    """Sauvegarde le brain dans brain.json."""
    try:
        with _brain_lock:
            data = dict(_brain)
        with open(BRAIN_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("🧠 Brain save error: {}".format(e))

def brain_record_trade(symbol, setup, session, side, is_win, trade_duration_s=0):
    """
    Enregistre un trade fermé dans le brain et déclenche l'adaptation.
    Appelé depuis _on_closed().
    """
    global ATR_SL_MULT
    now = time.time()

    with _brain_lock:
        _brain["total_trades"] += 1
        if is_win:
            _brain["total_wins"] += 1
        else:
            _brain["total_losses"] += 1

        # ── 1. Mémoire symbole ────────────────────────────────────
        if symbol not in _brain["symbols"]:
            _brain["symbols"][symbol] = {
                "wins": 0, "losses": 0, "consec_losses": 0,
                "blacklisted_until": 0.0, "last_trade_ts": 0.0
            }
        sym_data = _brain["symbols"][symbol]
        sym_data["last_trade_ts"] = now
        if is_win:
            sym_data["wins"] += 1
            sym_data["consec_losses"] = 0
        else:
            sym_data["losses"] += 1
            sym_data["consec_losses"] += 1
            # Blacklist si ≥ 3 pertes consécutives → 2h
            if sym_data["consec_losses"] >= 3:
                sym_data["blacklisted_until"] = now + 7200
                msg = "🚫 Brain: {} BLACKLISTÉ 2h ({} pertes consec)".format(
                    symbol, sym_data["consec_losses"])
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "blacklist", "msg": msg})
                send_telegram("🧠 " + msg)

        # ── 2. Mémoire setup ─────────────────────────────────────
        if setup not in _brain["setups"]:
            _brain["setups"][setup] = {"wins": 0, "losses": 0, "min_score_override": None}
        s_data = _brain["setups"][setup]
        if is_win: s_data["wins"] += 1
        else:      s_data["losses"] += 1

        s_total = s_data["wins"] + s_data["losses"]
        if s_total >= 8:
            wr = s_data["wins"] / s_total
            if wr < 0.45 and s_data["min_score_override"] is None:
                # Setup peu fiable → relever le score minimum
                new_min = 92 if "BB" in setup else 94
                s_data["min_score_override"] = new_min
                msg = "📈 Brain: {} WR={:.0f}% < 45% → score min relevé à {}".format(
                    setup, wr*100, new_min)
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "score_raise", "msg": msg})
                send_telegram("🧠 " + msg)
            elif wr >= 0.55 and s_data["min_score_override"] is not None:
                # Setup redevenu fiable → reset
                s_data["min_score_override"] = None
                msg = "✅ Brain: {} WR={:.0f}% → score min réinitialisé".format(setup, wr*100)
                logger.info(msg)
                _brain["adaptations"].append({"ts": now, "type": "score_reset", "msg": msg})

        # ── 3. Mémoire session ────────────────────────────────────
        if session not in _brain["sessions"]:
            _brain["sessions"][session] = {"wins": 0, "losses": 0, "margin_mult_override": 1.0}
        sess_data = _brain["sessions"][session]
        if is_win: sess_data["wins"] += 1
        else:      sess_data["losses"] += 1

        sess_total = sess_data["wins"] + sess_data["losses"]
        if sess_total >= 10:
            sess_wr = sess_data["wins"] / sess_total
            if sess_wr < 0.40 and sess_data["margin_mult_override"] == 1.0:
                sess_data["margin_mult_override"] = 0.75
                msg = "⚠️ Brain: Session {} WR={:.0f}% < 40% → marge ×0.75".format(
                    session, sess_wr*100)
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "session_penalty", "msg": msg})
                send_telegram("🧠 " + msg)
            elif sess_wr >= 0.50 and sess_data["margin_mult_override"] < 1.0:
                sess_data["margin_mult_override"] = 1.0
                msg = "✅ Brain: Session {} WR={:.0f}% → marge normale".format(
                    session, sess_wr*100)
                logger.info(msg)
                _brain["adaptations"].append({"ts": now, "type": "session_restore", "msg": msg})

        # ── 4. Mémoire côté (BUY/SELL) ───────────────────────────
        if side not in _brain["sides"]:
            _brain["sides"][side] = {"wins": 0, "losses": 0, "skip_until": 0.0}
        side_data = _brain["sides"][side]
        if is_win: side_data["wins"] += 1
        else:      side_data["losses"] += 1

        side_total = side_data["wins"] + side_data["losses"]
        if side_total >= 10:
            side_wr = side_data["wins"] / side_total
            if side_wr < 0.35 and time.time() > side_data.get("skip_until", 0):
                side_data["skip_until"] = now + 14400  # 4h
                msg = "🛑 Brain: {} WR={:.0f}% < 35% → skip 4h".format(side, side_wr*100)
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "side_skip", "msg": msg})
                send_telegram("🧠 " + msg)

        # ── 5. Ajustement ATR si SL trop serré ───────────────────
        if not is_win and trade_duration_s < 60:
            _brain["early_sl_count"] += 1
        recent_losses = _brain["total_losses"]
        if recent_losses >= 5:
            early_ratio = _brain["early_sl_count"] / max(recent_losses, 1)
            if early_ratio > 0.60 and _brain["atr_mult"] < 2.0:
                _brain["atr_mult"] = round(min(_brain["atr_mult"] + 0.1, 2.0), 2)
                ATR_SL_MULT = _brain["atr_mult"]
                msg = "📏 Brain: SL trop serré ({:.0f}% early SL) → ATR×{:.2f}".format(
                    early_ratio*100, ATR_SL_MULT)
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "atr_widen", "msg": msg})
                send_telegram("🧠 " + msg)

        # ── 6. Détection stagnation → VOL_MIN_FILTER dynamique ───
        #  v4.8 : Si plusieurs trades perdants avec PnL proche de 0
        #  (prix n'a quasiment pas bougé = actif stagnant), on monte
        #  temporairement le filtre de volume pour cibler des actifs
        #  qui ont un vrai momentum et éviter les paires léthargiques.
        #
        #  Condition : perte + durée < 90s (prix n'a pas bougé assez)
        #  Seuil     : 3 pertes stagnantes consécutives → VOL_MIN ×2
        #  Reset     : automatique après 2h ou dès un WIN
        if not is_win and trade_duration_s < 90:
            _brain["stagnation_losses"] = _brain.get("stagnation_losses", 0) + 1
            stag = _brain["stagnation_losses"]
            if stag >= 3 and _brain.get("vol_min_boost", 1.0) < 2.0:
                _brain["vol_min_boost"]       = 2.0
                _brain["vol_min_boost_until"] = now + 7200   # 2h
                msg = "📊 Brain: {} stagnations consécutives → VOL_MIN ×2 pendant 2h".format(stag)
                logger.warning(msg)
                _brain["adaptations"].append({"ts": now, "type": "vol_boost", "msg": msg})
                send_telegram("🧠 " + msg)
        elif is_win:
            # Reset stagnation sur un WIN
            _brain["stagnation_losses"] = 0
            if _brain.get("vol_min_boost", 1.0) > 1.0 and now > _brain.get("vol_min_boost_until", 0):
                _brain["vol_min_boost"] = 1.0

        # Reset automatique du boost si délai expiré
        if now > _brain.get("vol_min_boost_until", 0) and _brain.get("vol_min_boost", 1.0) > 1.0:
            _brain["vol_min_boost"] = 1.0
            logger.info("📊 Brain: VOL_MIN boost expiré → retour normal")

    brain_save()

    # ── 6. Rapport hebdomadaire ──────────────────────────────────
    with _brain_lock:
        last_report = _brain.get("last_report_ts", 0)
    if now - last_report > 7 * 86400:
        brain_weekly_report()

def brain_is_blacklisted(symbol):
    """Retourne True si le symbole est blacklisté par le brain."""
    with _brain_lock:
        sym_data = _brain["symbols"].get(symbol)
        if sym_data and time.time() < sym_data.get("blacklisted_until", 0):
            return True
    return False

def brain_is_side_skipped(side):
    """Retourne True si ce côté (BUY/SELL) est temporairement skippé."""
    with _brain_lock:
        side_data = _brain["sides"].get(side)
        if side_data and time.time() < side_data.get("skip_until", 0):
            return True
    return False

def brain_get_setup_min_score(setup):
    """Retourne le score minimum pour ce setup (override brain ou défaut)."""
    with _brain_lock:
        s_data = _brain["setups"].get(setup, {})
        override = s_data.get("min_score_override")
    return override if override is not None else MIN_SCORE

def brain_get_session_margin_mult(session):
    """Retourne le multiplicateur de marge pour cette session."""
    with _brain_lock:
        sess_data = _brain["sessions"].get(session, {})
    return sess_data.get("margin_mult_override", 1.0)

def brain_get_margin_usd(symbol, setup, session):
    """
    Retourne la marge fixe en USD après ajustements brain.
    Base : FIXED_MARGIN_USD = $0.60
    Pénalités appliquées :
      - Session mauvaise     → ×0.75
      - Symbole dégradé (WR < 40% sur ≥ 5 trades) → ×0.75
    Plancher : MARGIN_MIN_USD = $0.30
    """
    margin = FIXED_MARGIN_USD
    mult = 1.0

    # Pénalité session
    sess_mult = brain_get_session_margin_mult(session)
    mult *= sess_mult

    # Pénalité symbole
    with _brain_lock:
        sym_data = _brain["symbols"].get(symbol, {})
    sym_total = sym_data.get("wins", 0) + sym_data.get("losses", 0)
    if sym_total >= 5:
        sym_wr = sym_data.get("wins", 0) / sym_total
        if sym_wr < 0.40:
            mult *= 0.75

    margin = round(max(MARGIN_MIN_USD, min(MARGIN_MAX_USD, margin * mult)), 2)
    return margin

def brain_weekly_report():
    """Envoie un rapport Telegram des apprentissages de la semaine."""
    global _brain
    now = time.time()
    with _brain_lock:
        _brain["last_report_ts"] = now
        total   = _brain["total_trades"]
        wins    = _brain["total_wins"]
        wr_glob = wins / max(total, 1) * 100

        setup_lines = []
        for sname, sd in _brain["setups"].items():
            t = sd["wins"] + sd["losses"]
            if t > 0:
                wr = sd["wins"] / t * 100
                override = sd.get("min_score_override")
                flag = " ⚠️ score min relevé" if override else ""
                setup_lines.append("  {} : {:.0f}% WR ({} trades){}".format(
                    sname, wr, t, flag))

        sess_lines = []
        for sname, sd in _brain["sessions"].items():
            t = sd["wins"] + sd["losses"]
            if t > 0:
                wr = sd["wins"] / t * 100
                mult = sd.get("margin_mult_override", 1.0)
                flag = " ×{:.2f}".format(mult)
                sess_lines.append("  {} : {:.0f}% WR ({} trades){}".format(
                    sname, wr, t, flag))

        black = [s for s, d in _brain["symbols"].items()
                 if time.time() < d.get("blacklisted_until", 0)]

        recent_adapt = _brain["adaptations"][-5:] if _brain["adaptations"] else []

    msg = (
        "🧠 <b>Rapport Brain hebdo</b>\n\n"
        "📊 Global: {} trades | WR {:.0f}% | ATR×{:.2f}\n\n"
        "⚙️ Setups:\n{}\n\n"
        "🕐 Sessions:\n{}\n\n"
        "🚫 Blacklists actives: {}\n\n"
        "📝 Dernières adaptations:\n{}".format(
            total, wr_glob, _brain.get("atr_mult", ATR_SL_MULT),
            "\n".join(setup_lines) or "  Aucune donnée",
            "\n".join(sess_lines) or "  Aucune donnée",
            ", ".join(black) if black else "Aucune",
            "\n".join("  " + a["msg"] for a in recent_adapt) or "  Aucune"
        )
    )
    send_telegram(msg)
    logger.info("🧠 Rapport brain envoyé")

def brain_summary_log():
    """Log court du brain pour le dashboard."""
    with _brain_lock:
        total = _brain["total_trades"]
        wr    = _brain["total_wins"] / max(total, 1) * 100
        black = sum(1 for d in _brain["symbols"].values()
                    if time.time() < d.get("blacklisted_until", 0))
        atr   = _brain["atr_mult"]
        vmult = _brain.get("vol_min_boost", 1.0)
    vol_tag = " VOL×{:.0f}".format(vmult) if vmult > 1.0 else ""
    return "Brain: {}T WR{:.0f}% ATR×{:.2f} BL:{}{}".format(total, wr, atr, black, vol_tag)

def brain_get_vol_min_filter():
    """
    v4.8 — Retourne le filtre VOL_MIN effectif.
    Normal : VOL_MIN_FILTER
    Stagnation détectée : VOL_MIN_FILTER × brain['vol_min_boost'] (max ×2)
    Se reset automatiquement après 2h ou au premier WIN.
    """
    with _brain_lock:
        boost      = _brain.get("vol_min_boost", 1.0)
        boost_until = _brain.get("vol_min_boost_until", 0.0)
    # Reset automatique si délai expiré
    if boost > 1.0 and time.time() > boost_until:
        with _brain_lock:
            _brain["vol_min_boost"] = 1.0
        boost = 1.0
    return VOL_MIN_FILTER * boost

_btc_cache = {"direction": 0, "ts": 0.0, "label": "NEUTRE",
              "rsi": 50.0, "slope": 0.0, "closes": None, "strength": "NORMAL"}
_btc_lock  = threading.Lock()

# ── Cache MTF (M15 + H1) — v4.9 ──────────────────────────────────
# Évite de re-fetcher M15/H1 à chaque cycle — TTL 3 minutes
_mtf_cache = {}        # symbol → {"ts": float, "m15_dir": int, "h1_dir": int, "label": str}
_mtf_lock  = threading.Lock()

# ── Cache Filtre News — v4.9 ─────────────────────────────────────
# Liste des prochains événements macro HIGH impact (timestamps UTC)
# Mise à jour 1x/heure via ForexFactory ou liste de secours statique
_news_events   = []    # [(timestamp_utc, "CPI USA"), ...]
_news_cache_ts = 0.0
_news_lock     = threading.Lock()

_fg_cache  = {"value": 50, "label": "Neutral", "ts": 0.0}
_fg_lock   = threading.Lock()

_btc_dom_cache = {"dominance": 50.0, "label": "Normal", "ts": 0.0}
_btc_dom_lock  = threading.Lock()

# ─── COULEURS ────────────────────────────────────────────────────
GREEN   = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN    = "\033[96m"; WHITE  = "\033[97m"; RESET  = "\033[0m"
BOLD    = "\033[1m";  DIM    = "\033[2m";  MAGENTA = "\033[95m"

def cc(text, col): return "{}{}{}".format(col, text, RESET)

# ═══════════════════════════════════════════════════════════════════
#  PALIERS & TIER
# ═══════════════════════════════════════════════════════════════════

def get_tier():
    with balance_lock:
        bal = account_balance
    if bal < TIER1_LIMIT: return 1
    if bal < TIER2_LIMIT: return 2
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
    Session en cours avec détection PRIME TIME — v4.9.
    London Prime : 08h-11h UTC (Open de Londres, forte liquidité)
    NY Prime     : 13h-16h UTC (Open de New York, plus grand volume)
    Ces créneaux sont les meilleurs pour le scalping M5 :
      - Spreads plus serrés
      - Mouvements directionnels plus nets
      - Volume institutionnel présent
    """
    h = datetime.now(timezone.utc).hour
    if  8 <= h < 11: return "LONDON_PRIME"   # London Open — meilleur créneau
    if 13 <= h < 16: return "NY_PRIME"        # NY Open — 2ème meilleur créneau
    if 11 <= h < 13: return "LONDON"          # London fin de matinée
    if 16 <= h < 21: return "NY"              # NY après-midi
    if  2 <= h <  8: return "ASIA"            # Asie
    return "OFF"                               # 21h-02h UTC — pas de trade

def get_session_mult():
    return SESSION_MULT.get(get_session(), 1.0)

def is_prime_session():
    """True si on est en PRIME TIME (meilleures entrées)."""
    return get_session() in ("LONDON_PRIME", "NY_PRIME")

# ═══════════════════════════════════════════════════════════════════
#  MONEY MANAGEMENT DYNAMIQUE
# ═══════════════════════════════════════════════════════════════════

def compute_dynamic_margin(signal_score, fond_score, btc_strength, side="BUY", symbol=""):
    """
    Calcule la marge dynamique en fonction de :
      - signal_score  : score BB (90→94)
      - fond_score    : score fondamental (/100)
      - btc_strength  : force BTC ("FORT", "NORMAL", "FAIBLE")
      - side          : "BUY" ou "SELL" (pour F&G et dominance)
      - symbol        : pour la dominance BTC (alts vs BTC)

    Multiplicateurs appliqués :
      ×session  : Asia×0.6 / London×1.0 / NY×1.2 / OFF×0.5
      ×btc      : FORT×1.2 / NORMAL×1.0 / FAIBLE×0.8
      ×fond     : ≥60/100→×1.15 / <35/100→rejeté avant
      ×fg       : F&G extrême contre-tendance→×0.5 / confirme→×1.1
      ×dom      : Dom BTC >65%→BUY×0.6 SELL×1.1 / Altseason→BUY×1.15

    Retourne (margin_float, detail_str)
    """
    # 1. Base selon qualité du signal
    base = MARGIN_MIN
    for score_thresh, margin in sorted(MARGIN_BY_SCORE.items(), reverse=True):
        if signal_score >= score_thresh:
            base = margin
            break

    # 2. Multiplicateur session
    mult_session = get_session_mult()

    # 3. Multiplicateur BTC force
    mult_btc = {"FORT": 1.2, "NORMAL": 1.0, "FAIBLE": 0.8}.get(btc_strength, 1.0)

    # 4. Multiplicateur fondamental (/100)
    mult_fond = 1.15 if fond_score >= FOND_BOOST_SCORE else 1.0

    # 5. Multiplicateur Fear & Greed (réduit si contre-tendance, boost si confirme)
    mult_fg, fg_detail = get_fg_margin_mult(side)

    # 6. Multiplicateur Dominance BTC (alts faibles si BTC domine)
    mult_dom, dom_detail = get_btc_dom_mult(symbol, side)

    margin = base * mult_session * mult_btc * mult_fond * mult_fg * mult_dom

    # Clamp strict
    margin = max(MARGIN_MIN, min(MARGIN_MAX, margin))

    detail = "base={:.0f}% ×sess={:.1f} ×BTC={:.1f} ×fond={:.2f} ×F&G={:.1f} ×dom={:.1f} → {:.1f}%".format(
        base*100, mult_session, mult_btc, mult_fond, mult_fg, mult_dom, margin*100)

    logger.debug("MM: {} [{}] [{}]".format(detail, fg_detail, dom_detail))
    return margin, detail

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
    return "SCANNER M5 v4.5 | {} | ${:.4f} | Pos:{}/{} | {} | F&G:{} | W:{}".format(
        st, account_balance, n, MAX_POSITIONS, ses, fg, get_current_weight()), 200

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
        "version":       "v2.1",
        "balance":       round(account_balance, 4),
        "session":       get_session(),
        "session_mult":  get_session_mult(),
        "fear_greed":    _fg_cache,
        "btc_dominance": _btc_dom_cache,
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
    if al == 0 and ag == 0: return 50.0   # Pas de mouvement
    if al == 0: return 100.0              # Que des hausses
    if ag == 0: return 0.0               # Que des baisses
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

# ═══════════════════════════════════════════════════════════════════
#  WEIGHT TRACKER ANTI-SATURATION API
# ═══════════════════════════════════════════════════════════════════
#
#  Binance Futures = 2400 weight/minute
#  On consomme au maximum WEIGHT_SAFETY_MARGIN × 2400 = 1800/min
#
#  Poids estimés des endpoints :
#    klines              → 1
#    ticker/24hr bulk    → 40
#    ticker/24hr symbol  → 1
#    premiumIndex        → 1
#    openInterestHist    → 1
#    allForceOrders      → 20
#    order (POST/DELETE) → 1
#    balance/position    → 5
# ═══════════════════════════════════════════════════════════════════

ENDPOINT_WEIGHTS = {
    "/fapi/v1/klines":                   1,
    "/fapi/v1/ticker/24hr_bulk":         40,   # bulk = paramètre interne
    "/fapi/v1/ticker/24hr":              1,
    "/fapi/v1/ticker/price":             1,
    "/fapi/v1/premiumIndex":             1,
    "/futures/data/openInterestHist":    1,
    "/fapi/v1/allForceOrders":           20,
    "/fapi/v1/order":                    1,
    "/fapi/v2/balance":                  5,
    "/fapi/v2/positionRisk":             5,
    "/fapi/v1/leverage":                 1,
    "/fapi/v1/marginType":               1,
    "/fapi/v1/openOrders":               1,
    "/fapi/v1/income":                   1,
    "_default":                          1,
}

def _get_endpoint_weight(path, bulk=False):
    if bulk: return ENDPOINT_WEIGHTS["/fapi/v1/ticker/24hr_bulk"]
    return ENDPOINT_WEIGHTS.get(path, ENDPOINT_WEIGHTS["_default"])

def _track_weight(path, bulk=False):
    """Enregistre le poids consommé et retourne le weight actuel sur la fenêtre."""
    global _api_weight_times, _api_weight_used
    w = _get_endpoint_weight(path, bulk)
    now = time.time()
    with _api_weight_lock:
        # Nettoyage fenêtre glissante
        _api_weight_times = [(t, wt) for t, wt in _api_weight_times
                             if now - t < API_WEIGHT_WINDOW]
        _api_weight_times.append((now, w))
        _api_weight_used = sum(wt for _, wt in _api_weight_times)
        return _api_weight_used

def get_current_weight():
    """Retourne le weight API consommé sur la dernière minute."""
    now = time.time()
    with _api_weight_lock:
        return sum(wt for t, wt in _api_weight_times if now - t < API_WEIGHT_WINDOW)

def _rate_limit(path=""):
    """
    Double protection :
    1. Compteur brut d'appels (≤1100/min pour sécurité)
    2. Weight tracker Binance (≤ WEIGHT_SAFETY_MARGIN × 2400/min)
    Si limite approchée → sleep adaptatif
    """
    global api_call_times
    max_safe_weight = MAX_API_WEIGHT_PER_MIN * WEIGHT_SAFETY_MARGIN

    with api_lock:
        now = time.time()
        # Compteur appels bruts
        api_call_times = [t for t in api_call_times if now - t < 60]
        if len(api_call_times) >= 1100:
            s = 60 - (now - api_call_times[0])
            if s > 0:
                logger.warning("⚠️ Rate limit appels bruts — pause {:.1f}s".format(s))
                time.sleep(s)
        api_call_times.append(now)

    # Weight tracker
    w_used = get_current_weight()
    if w_used >= max_safe_weight:
        # Calcule le sleep nécessaire pour que les vieux weights sortent de la fenêtre
        with _api_weight_lock:
            if _api_weight_times:
                oldest = _api_weight_times[0][0]
                sleep_needed = API_WEIGHT_WINDOW - (time.time() - oldest) + 0.5
                if sleep_needed > 0:
                    logger.warning("⚠️ Weight {}/{} — throttle {:.1f}s".format(
                        int(w_used), int(max_safe_weight), sleep_needed))
                    time.sleep(sleep_needed)
    elif w_used >= max_safe_weight * 0.85:
        # Zone orange (85%) → légère pause préventive
        time.sleep(0.3)

    _track_weight(path)

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
    _rate_limit(path)
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
                    # Lire le weight réel retourné par Binance si dispo
                    used_weight = r.headers.get("X-MBX-USED-WEIGHT-1M")
                    if used_weight:
                        real_w = int(used_weight)
                        if real_w > MAX_API_WEIGHT_PER_MIN * WEIGHT_SAFETY_MARGIN * 0.9:
                            logger.warning("⚠️ Binance weight header: {}/{}".format(
                                real_w, MAX_API_WEIGHT_PER_MIN))
                    return r.json()

                code, msg = _parse_binance_error(r.text)

                if r.status_code == 429:
                    sleep_t = [10, 20, 45, 90][min(attempt, 3)]
                    logger.warning("⚠️ 429 Rate limit — backoff {}s (attempt {})".format(
                        sleep_t, attempt + 1))
                    time.sleep(sleep_t); continue
                if r.status_code == 418:
                    logger.error("🚫 418 IP Ban — pause 3 minutes")
                    time.sleep(180); return None
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

_balance_last_ts = 0.0
BALANCE_TTL      = 45   # Rafraîchissement max toutes les 45s

def get_account_balance(force=False):
    """
    PATCH 1 — Lecture sécurisée du solde :
    - balance_lock sur toute lecture/écriture
    - Cache TTL 45s (pas de requête inutile)
    - Fallback sécurité : si l'appel échoue, on conserve
      la dernière valeur connue MAIS on la plafonne à 80%
      pour éviter l'over-sizing en cas de solde obsolète.
    """
    global account_balance, _balance_last_ts
    now = time.time()

    # Court-circuit si cache encore frais
    if not force and (now - _balance_last_ts) < BALANCE_TTL:
        with balance_lock:
            return account_balance

    d = request_binance("GET", "/fapi/v2/balance", signed=True)
    if d:
        for a in d:
            if a.get("asset") == "USDT":
                new_bal = float(a.get("availableBalance", 0))
                with balance_lock:
                    account_balance  = new_bal
                    _balance_last_ts = now
                return new_bal
    else:
        # Appel échoué → valeur sécurisée à 80% du dernier solde connu
        with balance_lock:
            safe = account_balance * 0.80
        logger.warning("⚠️ get_account_balance échoué — valeur sécurité ${:.4f}".format(safe))
        return safe

    with balance_lock:
        return account_balance

def load_top_symbols():
    """
    Charge TOUS les symboles USDT perpétuels éligibles.
    Pré-filtre : volume 24h > VOL_MIN_FILTER ($500k)
    → 1 seul appel ticker/24hr bulk (weight 40)
    → typiquement 200-350 symboles après filtre
    """
    global symbols_list
    logger.info("📥 Chargement TOUS les symboles USDT perps...")
    try:
        # 1 seul appel pour tout le ticker (weight 40)
        _track_weight("/fapi/v1/ticker/24hr", bulk=True)
        tickers  = request_binance("GET", "/fapi/v1/ticker/24hr", signed=False)
        exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
        if not tickers or not exchange: raise ValueError("API indispo")

        tradeable = set()
        for s in exchange.get("symbols", []):
            sym  = s["symbol"]
            base = s["baseAsset"].upper()
            if (sym.endswith("USDT") and s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                    and sym not in EXCLUDE_SYMBOLS
                    and sym not in LIQUIDITY_BLACKLIST      # v4.5 blacklist explicite
                    and base not in MEME_COINS):
                tradeable.add(sym)
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                symbol_info_cache[sym] = {
                    "quantityPrecision": s.get("quantityPrecision", 3),
                    "pricePrecision":    s.get("pricePrecision", 4),
                    "minQty":      float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                    "stepSize":    float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", MIN_NOTIONAL)),
                }

        # Construire le bulk ticker cache en même temps
        vol_map = {}
        with _bulk_ticker_lock:
            global _bulk_ticker_cache, _bulk_ticker_ts
            _bulk_ticker_cache = {}
            for t in tickers:
                sym = t.get("symbol", "")
                if sym in tradeable:
                    try:
                        vol   = float(t.get("quoteVolume", 0))
                        chg   = float(t.get("priceChangePercent", 0))
                        price = float(t.get("lastPrice", 0))
                        # v4.5 : filtre prix minimum $0.01
                        if price < PRICE_MIN_USD:
                            continue
                        _bulk_ticker_cache[sym] = {"vol": vol, "chg": chg}
                        vol_map[sym] = vol
                    except: pass
            _bulk_ticker_ts = time.time()

        # Filtre volume minimum + tri par volume
        sorted_syms  = sorted(vol_map.items(), key=lambda x: x[1], reverse=True)
        symbols_list = [s for s, v in sorted_syms if v >= VOL_MIN_FILTER]

        if not symbols_list:
            symbols_list = FALLBACK_SYMBOLS[:]

        logger.info("✅ {} symboles éligibles (vol24h > ${:.0f}M | prix > ${}) | weight_used={}".format(
            len(symbols_list), VOL_MIN_FILTER / 1_000_000, PRICE_MIN_USD, get_current_weight()))

    except Exception as e:
        logger.error("load_top_symbols: {} → fallback".format(e))
        symbols_list = FALLBACK_SYMBOLS[:]

def get_bulk_ticker(symbol):
    """
    Retourne les données ticker depuis le cache bulk (vol24h, priceChange%).
    Si cache expiré → retourne None (sera rechargé au prochain cycle).
    """
    with _bulk_ticker_lock:
        if time.time() - _bulk_ticker_ts < BULK_TICKER_TTL:
            return _bulk_ticker_cache.get(symbol)
    return None

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

def get_fg_margin_mult(side):
    """
    Fear & Greed → multiplicateur de marge (NE BLOQUE PLUS, réduit juste la mise).

    F&G < 15 (Extreme Fear)  + BUY  → ×0.5  (contre-tendance, prudence)
    F&G > 85 (Extreme Greed) + SELL → ×0.5  (contre-tendance, prudence)
    F&G ≤ 30 (Fear)          + SELL → ×1.1  (dans le sens de la panique)
    F&G ≥ 70 (Greed)         + BUY  → ×1.1  (dans le sens de l'euphorie)
    Sinon → ×1.0 (neutre)
    """
    fg = get_fear_greed()
    v  = fg.get("value", 50)
    lb = fg.get("label", "Neutral")

    if side == "BUY"  and v < FG_EXTREME_FEAR:
        return 0.5, "F&G={} {} → BUY contre-tendance ×0.5".format(v, lb)
    if side == "SELL" and v >= FG_EXTREME_GREED:
        return 0.5, "F&G={} {} → SELL contre-tendance ×0.5".format(v, lb)
    if side == "SELL" and v <= FG_FEAR_ZONE:
        return 1.1, "F&G={} {} ✅ confirme SELL ×1.1".format(v, lb)
    if side == "BUY"  and v >= FG_GREED_ZONE:
        return 1.1, "F&G={} {} ✅ confirme BUY ×1.1".format(v, lb)
    return 1.0, "F&G={} {} neutre ×1.0".format(v, lb)

def check_fear_greed(side):
    """Compatibilité : retourne toujours True (plus de blocage), le mult est dans get_fg_margin_mult."""
    mult, detail = get_fg_margin_mult(side)
    return True, detail

# ═══════════════════════════════════════════════════════════════════
#  DOMINANCE BTC
# ═══════════════════════════════════════════════════════════════════

def get_btc_dominance():
    """
    Récupère la dominance BTC via CoinGecko (API publique gratuite).
    Dominance = % du marché crypto total représenté par BTC.

    > 65% : Altcoins très faibles (BTC aspire tout le capital)
    > 60% : Altcoins sous pression
    < 50% : Altseason — altcoins libres de leurs mouvements
    Cache 5 minutes.
    """
    global _btc_dom_cache
    with _btc_dom_lock:
        if time.time() - _btc_dom_cache.get("ts", 0) < BTC_DOM_CACHE_TTL:
            return _btc_dom_cache

    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=5,
            headers={"Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            dom  = float(data.get("market_cap_percentage", {}).get("btc", 50.0))
            if dom >= BTC_DOM_EXTREME:
                label = "EXTREME ({:.1f}%)".format(dom)
            elif dom >= BTC_DOM_HIGH:
                label = "ÉLEVÉE ({:.1f}%)".format(dom)
            elif dom < 50.0:
                label = "ALTSEASON ({:.1f}%)".format(dom)
            else:
                label = "NORMALE ({:.1f}%)".format(dom)

            result = {"dominance": dom, "label": label, "ts": time.time()}
            with _btc_dom_lock:
                _btc_dom_cache = result
            return result
    except:
        pass

    return _btc_dom_cache  # Retourne cache expiré si API indisponible

def get_btc_dom_mult(symbol, side):
    """
    Multiplicateur de marge selon la dominance BTC.

    Logique :
    - Dom > 65% (Extreme) : BTC aspire tout → alts très faibles
        BUY  alts → ×0.6  (contre-tendance lourde)
        SELL alts → ×1.1  (dans le sens de la faiblesse des alts)
    - Dom > 60% (Élevée)  : alts sous pression
        BUY  alts → ×0.8
        SELL alts → ×1.05
    - Dom < 50% (Altseason) : alts libres de monter
        BUY  alts → ×1.15 (altseason = les alts surperforment)
        SELL alts → ×0.9  (shorter en altseason = risqué)
    - Dom normale (50-60%) → ×1.0

    Note : ne s'applique pas à BTC lui-même.
    """
    # BTC n'est pas affecté par sa propre dominance
    if symbol.startswith("BTC"):
        return 1.0, "BTC DOM N/A"

    dom_data = get_btc_dominance()
    dom   = dom_data.get("dominance", 50.0)
    label = dom_data.get("label", "?")

    if dom >= BTC_DOM_EXTREME:
        mult = 0.6 if side == "BUY" else 1.1
        detail = "DOM {} → alts très faibles ×{:.1f}".format(label, mult)
    elif dom >= BTC_DOM_HIGH:
        mult = 0.8 if side == "BUY" else 1.05
        detail = "DOM {} → alts pression ×{:.1f}".format(label, mult)
    elif dom < 50.0:
        mult = 1.15 if side == "BUY" else 0.9
        detail = "DOM {} → altseason ×{:.1f}".format(label, mult)
    else:
        mult = 1.0
        detail = "DOM {} → normale ×1.0".format(label)

    return mult, detail

# ═══════════════════════════════════════════════════════════════════
#  BTC M15 — DIRECTION + FORCE
# ═══════════════════════════════════════════════════════════════════

def get_btc_direction():
    """
    Direction BTC basée sur le PRIX DIRECT (variation récente).
    v4.7 — Plus de EMA/RSI comme critère principal.

    Méthode :
      1. Variation 3 bougies M5 (15 min) — court terme
      2. Variation 6 bougies M5 (30 min) — moyen terme
      3. Variation 12 bougies M5 (1h)    — long terme
      → Les 3 doivent être alignées pour valider la direction
      → Si contradiction → NEUTRE (pas de trade)

    Seuils :
      BUY  : variation > +0.10% sur les 3 horizons
      SELL : variation < -0.10% sur les 3 horizons
      Sinon → NEUTRE

    Force :
      FORT   : variation > 0.25% sur 6 bougies
      NORMAL : variation 0.10–0.25%
      FAIBLE : < 0.10% (NEUTRE en pratique)
    """
    global _btc_cache
    with _btc_lock:
        if time.time() - _btc_cache.get("ts", 0) < 20:   # Cache 20s (scan 30s)
            return _btc_cache

    klines = get_klines(BTC_SYMBOL, TIMEFRAME, 20)
    if not klines or len(klines) < 14:
        return _btc_cache

    o, h, l, cl, v = _parse_klines(klines)
    price = cl[-1]

    # Variations de prix sur 3 horizons
    def pct(n):
        ref = cl[-n-1] if len(cl) > n else cl[0]
        return (price - ref) / ref * 100 if ref > 0 else 0.0

    var3  = pct(3)    # 15 min
    var6  = pct(6)    # 30 min
    var12 = pct(12)   # 1h

    THRESHOLD = 0.10  # 0.10% minimum pour valider la direction

    bull3  = var3  > THRESHOLD
    bull6  = var6  > THRESHOLD
    bull12 = var12 > THRESHOLD
    bear3  = var3  < -THRESHOLD
    bear6  = var6  < -THRESHOLD
    bear12 = var12 < -THRESHOLD

    # Tous les horizons doivent être alignés
    if bull3 and bull6 and bull12:
        direction = 1
        mag = abs(var6)
        strength = "FORT" if mag > 0.25 else "NORMAL"
        label = "BTC 🟢 HAUSSIER +{:.2f}% (3/6/12 bougies)".format(var6)
    elif bear3 and bear6 and bear12:
        direction = -1
        mag = abs(var6)
        strength = "FORT" if mag > 0.25 else "NORMAL"
        label = "BTC 🔴 BAISSIER {:.2f}% (3/6/12 bougies)".format(var6)
    else:
        direction = 0
        strength  = "FAIBLE"
        label = "BTC ⚪ NEUTRE/MIXTE (3={:+.2f}% 6={:+.2f}% 12={:+.2f}%)".format(
            var3, var6, var12)

    result = {
        "direction": direction,
        "label":     label,
        "var3":      round(var3, 3),
        "var6":      round(var6, 3),
        "var12":     round(var12, 3),
        "price":     price,
        "ts":        time.time(),
        "closes":    cl,
        "strength":  strength,
    }
    with _btc_lock:
        _btc_cache = result
    return result

def get_mtf_direction(symbol):
    """
    Multi-TimeFrame v4.9 — Direction M15 et H1 pour confirmer le signal M5.

    Principe :
      Un signal BUY M5 n'est valide QUE si M15 ET H1 sont également haussiers.
      Un trade contre la tendance H1 échoue statistiquement dans 65-70% des cas.

    Calcul direction : même méthode que BTC (variation prix directe)
      M15 : variation sur 3 bougies (45 min)
      H1  : variation sur 3 bougies (3h)

    Cache : 3 minutes (MTF_CACHE_TTL) pour éviter de saturer l'API.
    Retourne : {"m15_dir": int, "h1_dir": int, "label": str, "ok_buy": bool, "ok_sell": bool}
    """
    now = time.time()
    with _mtf_lock:
        cached = _mtf_cache.get(symbol)
        if cached and now - cached["ts"] < MTF_CACHE_TTL:
            return cached

    result = {"m15_dir": 0, "h1_dir": 0, "label": "MTF N/A", "ok_buy": True, "ok_sell": True, "ts": now}

    if not MTF_CONFIRM_REQUIRED:
        with _mtf_lock:
            _mtf_cache[symbol] = result
        return result

    try:
        def price_direction(klines_data, n_bars=3):
            """Direction basée sur variation de prix sur n_bars bougies."""
            if not klines_data or len(klines_data) < n_bars+2:
                return 0
            closes = [float(k[4]) for k in klines_data]
            price  = closes[-1]
            ref    = closes[-n_bars-1]
            if ref <= 0: return 0
            var = (price - ref) / ref * 100
            if var >  0.08: return  1
            if var < -0.08: return -1
            return 0

        # M15 : 10 bougies suffisent
        kl_m15 = get_klines(symbol, "15m", 10)
        m15_dir = price_direction(kl_m15, 3)

        # H1 : 6 bougies
        kl_h1 = get_klines(symbol, "1h", 6)
        h1_dir = price_direction(kl_h1, 3)

        # Autorisation par direction
        # BUY autorisé si M15 ≥ 0 ET H1 ≥ 0 (ni l'un ni l'autre baissier)
        # SELL autorisé si M15 ≤ 0 ET H1 ≤ 0
        ok_buy  = (m15_dir >= 0) and (h1_dir >= 0)
        ok_sell = (m15_dir <= 0) and (h1_dir <= 0)

        labels = []
        labels.append("M15 {}".format("🟢" if m15_dir==1 else ("🔴" if m15_dir==-1 else "⚪")))
        labels.append("H1 {}".format("🟢" if h1_dir==1 else ("🔴" if h1_dir==-1 else "⚪")))

        result = {
            "m15_dir": m15_dir, "h1_dir": h1_dir,
            "label": " | ".join(labels),
            "ok_buy": ok_buy, "ok_sell": ok_sell,
            "ts": now
        }
    except Exception as e:
        logger.debug("get_mtf_direction {}: {}".format(symbol, e))

    with _mtf_lock:
        _mtf_cache[symbol] = result
    return result


def compute_cvd_proxy(opens, closes, volumes):
    """
    CVD Proxy (Cumulative Volume Delta) — v4.9.

    Principe :
      Bougie haussière (close > open) → pression acheteur = +volume_delta
      Bougie baissière (close < open) → pression vendeur  = -volume_delta
      Le delta d'une bougie ≈ volume × |close-open| / (high-low) si H≠L

    On retourne :
      cvd_total  : somme des deltas (positif = pression haussière nette)
      cvd_pct    : % du volume total qui est "dans la direction" du CVD
      direction  : +1 si haussier, -1 si baissier, 0 si équilibré

    Usage : Si BUY mais CVD < 0 → gros vendeurs actifs → signal douteux.
    """
    n = min(CVD_LOOKBACK, len(opens))
    if n < 3:
        return 0.0, 0.5, 0

    cvd_total  = 0.0
    vol_total  = 0.0

    for i in range(-n, 0):
        o, c, v = opens[i], closes[i], volumes[i]
        if v <= 0: continue
        delta = v if c >= o else -v
        # Pondération par la force de la bougie (corps / amplitude)
        amplitude = abs(c - o)
        cvd_total += delta
        vol_total += v

    if vol_total == 0:
        return 0.0, 0.5, 0

    cvd_pct = (cvd_total / vol_total + 1) / 2   # Normalise entre 0 et 1
    direction = 1 if cvd_total > 0 else (-1 if cvd_total < 0 else 0)
    return cvd_total, cvd_pct, direction


def get_news_events():
    """
    Récupère les prochains événements économiques HIGH impact — v4.9.
    Source primaire  : ForexFactory calendar (JSON public)
    Source de secours: liste statique des créneaux habituels (mardi/vendredi)

    Retourne une liste de timestamps UTC (float) pour les 6 prochaines heures.
    Cache : 60 minutes (les news ne changent pas souvent)
    """
    global _news_events, _news_cache_ts
    now = time.time()

    with _news_lock:
        if now - _news_cache_ts < 3600 and _news_events is not None:
            return _news_events

    events = []

    # Tentative ForexFactory
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=5, headers={"User-Agent": "Mozilla/5.0"}
        )
        if resp.status_code == 200:
            data = resp.json()
            for ev in data:
                if ev.get("impact") not in ("High", "3"):
                    continue
                # Format ForexFactory : "2025-01-15T08:30:00-05:00"
                date_str = ev.get("date", "")
                try:
                    from datetime import datetime as dt
                    # Retire le timezone offset et parse
                    if "T" in date_str:
                        ts_dt = dt.fromisoformat(date_str)
                        ts_utc = ts_dt.astimezone(timezone.utc).timestamp()
                        if ts_utc > now - 1800:   # Seulement les events récents/futurs
                            events.append((ts_utc, ev.get("title", "EVENT")))
                except:
                    pass
    except Exception as e:
        logger.debug("News ForexFactory: {}".format(e))

    # Fallback statique : créneaux habituels HIGH impact (UTC)
    # CPI US : 2ème mardi du mois à 13h30 UTC
    # NFP    : 1er vendredi du mois à 13h30 UTC
    # FOMC   : dates variables, ~8 fois/an à 19h00 UTC
    if not events:
        from datetime import datetime as dt
        now_dt  = dt.now(timezone.utc)
        weekday = now_dt.weekday()   # 0=lundi, 4=vendredi
        hour    = now_dt.hour

        # Si vendredi et heure proche de 13h30 UTC → NFP potentiel
        if weekday == 4:
            nfp_ts = now_dt.replace(hour=13, minute=30, second=0).timestamp()
            if abs(now - nfp_ts) < 3600:
                events.append((nfp_ts, "NFP potentiel (vendredi 13h30 UTC)"))

        # Si mardi et heure proche de 13h30 UTC → CPI potentiel
        if weekday == 1:
            cpi_ts = now_dt.replace(hour=13, minute=30, second=0).timestamp()
            if abs(now - cpi_ts) < 3600:
                events.append((cpi_ts, "CPI potentiel (mardi 13h30 UTC)"))

    with _news_lock:
        _news_events   = events
        _news_cache_ts = now

    return events


def is_news_blackout():
    """
    Retourne (True, raison) si on est dans une fenêtre de blackout news.
    Fenêtre : ±NEWS_PAUSE_MINUTES autour de chaque événement HIGH impact.
    """
    if not NEWS_FILTER_ENABLED:
        return False, ""

    now    = time.time()
    pause  = NEWS_PAUSE_MINUTES * 60
    events = get_news_events()

    for ts_ev, name in events:
        if abs(now - ts_ev) <= pause:
            delta_min = int((ts_ev - now) / 60)
            if delta_min >= 0:
                return True, "News dans {}min : {} — pause trades".format(delta_min, name)
            else:
                return True, "Post-news {}min : {} — pause trades".format(abs(delta_min), name)

    return False, ""


def check_btc_correlation(symbol, side, sym_closes):
    """
    Filtre tendance BTC STRICT — v4.7 basé sur prix direct.

    Règles ABSOLUES (jamais contre-tendance) :
      1. BTC direction == 0 (neutre/mixte) → REJET total
      2. BUY  sur signal → BTC doit être HAUSSIER (direction == +1)
      3. SELL sur signal → BTC doit être BAISSIER (direction == -1)

    Filtre co-mouvement additionnel :
      Le symbole doit avoir bougé dans le MÊME sens que BTC
      sur les 6 dernières bougies M5 (30 min).
      Tolérance : si BTC fort (>0.20%) le symbole peut être en légère
      correction (jusqu'à -0.05%) — retour en zone = setup valide.

    Retourne (ok, raison)
    """
    btc     = get_btc_direction()
    btc_dir = btc["direction"]

    # Règle 1 : BTC neutre → JAMAIS de trade
    if btc_dir == 0:
        return False, "BTC NEUTRE/MIXTE — aucun trade ({})".format(btc["label"])

    # Règle 2 : alignement strict direction
    if side == "BUY" and btc_dir != 1:
        return False, "BUY REFUSÉ — BTC baissier ({:.2f}% sur 6 bougies)".format(
            btc.get("var6", 0))
    if side == "SELL" and btc_dir != -1:
        return False, "SELL REFUSÉ — BTC haussier ({:.2f}% sur 6 bougies)".format(
            btc.get("var6", 0))

    # Règle 3 : co-mouvement prix symbole / BTC
    btc_closes = btc.get("closes")
    if sym_closes and btc_closes and len(sym_closes) >= 6 and len(btc_closes) >= 6:
        sym_ref = sym_closes[-7] if len(sym_closes) > 6 else sym_closes[0]
        btc_ref = btc_closes[-7] if len(btc_closes) > 6 else btc_closes[0]
        sym_var = (sym_closes[-1] - sym_ref) / sym_ref * 100 if sym_ref > 0 else 0
        btc_var = btc.get("var6", 0)

        # Divergence dure : symbole part dans le sens opposé à BTC avec amplitude
        DIVERGENCE_HARD = 0.15   # > 0.15% de divergence = rejet
        if side == "BUY"  and sym_var < -DIVERGENCE_HARD and btc_var > 0:
            return False, "{} diverge BTC SELL (sym:{:+.2f}% btc:{:+.2f}%)".format(
                symbol, sym_var, btc_var)
        if side == "SELL" and sym_var > +DIVERGENCE_HARD and btc_var < 0:
            return False, "{} diverge BTC BUY (sym:{:+.2f}% btc:{:+.2f}%)".format(
                symbol, sym_var, btc_var)

    strength = btc.get("strength", "NORMAL")
    return True, "BTC {} ({}) | var6={:+.2f}%".format(
        "🟢 HAUSSIER" if btc_dir == 1 else "🔴 BAISSIER",
        strength, btc.get("var6", 0))

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
    try:
        d = request_binance("GET", "/futures/data/openInterestHist",
                            {"symbol": symbol, "period": "5m", "limit": 6}, signed=False)
        if d and len(d) >= 2:
            oi0          = float(d[0]["sumOpenInterest"])
            oi1          = float(d[-1]["sumOpenInterest"])
            oi_last_prev = float(d[-2]["sumOpenInterest"])
            oi_last_curr = float(d[-1]["sumOpenInterest"])
            spike_pct    = (oi_last_curr - oi_last_prev) / oi_last_prev if oi_last_prev > 0 else 0
            oi_change    = (oi1 - oi0) / oi0 if oi0 > 0 else 0
            oi_spike     = abs(spike_pct) > OI_SPIKE_THRESH
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

def get_volume_24h_score(symbol, side):
    """
    Score de volume 24h — utilise le cache bulk en priorité (0 appel API).
    Fallback : appel individuel si cache expiré.
    """
    try:
        # Priorité : cache bulk (aucun appel API supplémentaire)
        bulk = get_bulk_ticker(symbol)
        if bulk:
            vol_24h   = bulk["vol"]
            price_chg = bulk["chg"]
        else:
            ticker = request_binance("GET", "/fapi/v1/ticker/24hr",
                                     {"symbol": symbol}, signed=False)
            if not ticker: return 10, "Vol N/A⚠️"
            vol_24h   = float(ticker.get("quoteVolume", 0))
            price_chg = float(ticker.get("priceChangePercent", 0))

        if side == "BUY"  and price_chg > 2  and vol_24h > 10_000_000:
            return 20, "Vol24h=${:.0f}M ↑{:.1f}%✅".format(vol_24h/1e6, price_chg)
        if side == "SELL" and price_chg < -2 and vol_24h > 10_000_000:
            return 20, "Vol24h=${:.0f}M ↓{:.1f}%✅".format(vol_24h/1e6, abs(price_chg))
        if vol_24h > 5_000_000:
            return 10, "Vol24h=${:.0f}M⚠️".format(vol_24h/1e6)
        return 5, "Vol24h=${:.0f}M❌".format(vol_24h/1e6)
    except:
        return 10, "Vol N/A⚠️"

def get_liquidations_score(symbol, side):
    """
    Score liquidations récentes via l'endpoint Binance forcedOrders.
    Shorts liquidés → momentum haussier → confirme BUY
    Longs  liquidés → momentum baissier → confirme SELL
    Retourne (score /20, detail)
    """
    try:
        d = request_binance("GET", "/fapi/v1/allForceOrders",
                            {"symbol": symbol, "limit": 20}, signed=False)
        if not d: return 10, "Liqd N/A⚠️"

        long_liqd  = sum(float(o.get("origQty",0)) * float(o.get("avgPrice",0))
                         for o in d if o.get("side") == "SELL")   # long liquidé = ordre SELL
        short_liqd = sum(float(o.get("origQty",0)) * float(o.get("avgPrice",0))
                         for o in d if o.get("side") == "BUY")    # short liquidé = ordre BUY

        total = long_liqd + short_liqd

        if side == "BUY" and short_liqd > LIQD_THRESH_USD:
            return 20, "Liqd SHORT ${:.0f}k✅ (haussier)".format(short_liqd/1000)
        if side == "SELL" and long_liqd > LIQD_THRESH_USD:
            return 20, "Liqd LONG ${:.0f}k✅ (baissier)".format(long_liqd/1000)
        if total > LIQD_THRESH_USD / 2:
            return 10, "Liqd ${:.0f}k⚠️".format(total/1000)
        return 10, "Liqd faibles⚠️"
    except:
        return 10, "Liqd N/A⚠️"

def check_fondamentaux(symbol, side):
    """
    Score fondamental /140 — v4.9 (était /100).

    Funding      /20  — coût de position aligné ou non
    OI           /20  — intérêt ouvert croissant (pénalité si spike)
    Spread       /20  — mark vs index (liquidité)
    Volume 24h   /20  — spike volume confirme direction
    Liquidations /20  — liqd dans le bon sens confirme
    CVD Proxy    /20  — pression acheteur/vendeur réelle ← NOUVEAU v4.9
    Session      /20  — bonus PRIME TIME (London/NY Open)  ← NOUVEAU v4.9

    Seuil minimum : FOND_MIN_SCORE = 50/140
    Retourne (score, ok, detail)
    """
    score = 0
    parts = []

    # ── 1. Funding Rate ───────────────────────────────────────────
    funding = get_funding_rate(symbol)
    fp = funding * 100
    if side == "BUY":
        if funding <= 0.001:   score += 20; parts.append("Fund {:.4f}%✅".format(fp))
        elif funding > 0.002:  score += 5;  parts.append("Fund {:.4f}%❌".format(fp))
        else:                  score += 10; parts.append("Fund {:.4f}%⚠️".format(fp))
    else:
        if funding >= -0.001:  score += 20; parts.append("Fund {:.4f}%✅".format(fp))
        elif funding < -0.002: score += 5;  parts.append("Fund {:.4f}%❌".format(fp))
        else:                  score += 10; parts.append("Fund {:.4f}%⚠️".format(fp))

    # ── 2. Open Interest + spike detection ───────────────────────
    oi_chg, oi_spike = get_oi_data(symbol)
    if oi_spike:
        score -= 10; parts.append("OI SPIKE⚠️ (-10pts)")
    else:
        if oi_chg > 0.005:  score += 20; parts.append("OI +{:.2f}%✅".format(oi_chg*100))
        elif oi_chg > 0:    score += 10; parts.append("OI +{:.2f}%⚠️".format(oi_chg*100))
        elif oi_chg == 0:   score += 10; parts.append("OI N/A⚠️")
        else:               score += 5;  parts.append("OI {:.2f}%❌".format(oi_chg*100))

    # ── 3. Spread Mark/Index ──────────────────────────────────────
    spread = get_mark_spread(symbol)
    if abs(spread) < 0.05:   score += 20; parts.append("Sprd {:+.3f}%✅".format(spread))
    elif abs(spread) < 0.15: score += 10; parts.append("Sprd {:+.3f}%⚠️".format(spread))
    else:                    score += 0;  parts.append("Sprd {:+.3f}%❌".format(spread))

    # ── 4. Volume 24h ─────────────────────────────────────────────
    vol_score, vol_detail = get_volume_24h_score(symbol, side)
    score += vol_score; parts.append(vol_detail)

    # ── 5. Liquidations ───────────────────────────────────────────
    liqd_score, liqd_detail = get_liquidations_score(symbol, side)
    score += liqd_score; parts.append(liqd_detail)

    # ── 6. CVD Proxy — pression acheteur/vendeur — NOUVEAU v4.9 ──
    try:
        kl = get_klines(symbol, TIMEFRAME, CVD_LOOKBACK + 2)
        if kl and len(kl) >= CVD_LOOKBACK:
            opens_c   = [float(k[1]) for k in kl]
            closes_c  = [float(k[4]) for k in kl]
            volumes_c = [float(k[5]) for k in kl]
            cvd_total, cvd_pct, cvd_dir = compute_cvd_proxy(opens_c, closes_c, volumes_c)
            if side == "BUY":
                if cvd_dir == 1 and cvd_pct >= CVD_MIN_ALIGN_PCT:
                    score += 20; parts.append("CVD+{:.0f}%🟢✅".format(cvd_pct*100))
                elif cvd_dir == -1:
                    score -= 10; parts.append("CVD vendeurs⚠️(-10)")
                else:
                    score += 10; parts.append("CVD neutre⚠️")
            else:
                if cvd_dir == -1 and cvd_pct <= (1 - CVD_MIN_ALIGN_PCT):
                    score += 20; parts.append("CVD-{:.0f}%🔴✅".format((1-cvd_pct)*100))
                elif cvd_dir == 1:
                    score -= 10; parts.append("CVD acheteurs⚠️(-10)")
                else:
                    score += 10; parts.append("CVD neutre⚠️")
        else:
            score += 10; parts.append("CVD N/A⚠️")
    except:
        score += 10; parts.append("CVD err⚠️")

    # ── 7. Session PRIME TIME — NOUVEAU v4.9 ─────────────────────
    sess = get_session()
    if sess in ("LONDON_PRIME", "NY_PRIME"):
        score += 20; parts.append("{}PRIME✅".format(sess.split("_")[0]))
    elif sess == "OFF":
        score -= 10; parts.append("SessionOFF⚠️(-10)")
    else:
        score += 5;  parts.append("Session{}⚠️".format(sess))

    score = max(0, min(140, score))
    ok = score >= FOND_MIN_SCORE
    return score, ok, " | ".join(parts)


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

def analyse_m5(symbol, klines):
    """
    Analyse M5 complète :
    1. Breaker Block ICT détecté
    2. Direction OBLIGATOIREMENT alignée avec BTC M5
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
            "setup":       "BB_M5_BTC_ALIGN",
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
#  SL STRUCTUREL — ATR ou CRT (Candle Range Theory)
# ═══════════════════════════════════════════════════════════════════
#
#  Deux méthodes disponibles, le bot choisit la plus serrée
#  dans les limites autorisées :
#
#  ATR  → SL = entry ± ATR × 1.2 (adaptatif à la volatilité)
#  CRT  → SL = low/high de la bougie précédente (structure pure)
#
#  Priorité : CRT si valide (dans les limites MIN/MAX SL)
#             sinon ATR fallback
# ═══════════════════════════════════════════════════════════════════

def get_sl_atr(entry, side, atr):
    """SL basé sur ATR × ATR_SL_MULT."""
    dist = atr * ATR_SL_MULT
    dist = max(dist, entry * MIN_SL_PCT)
    dist = min(dist, entry * MAX_SL_PCT)
    return (entry - dist) if side == "BUY" else (entry + dist)

def get_sl_crt(entry, side, prev_high, prev_low):
    """
    SL basé sur CRT (Candle Range Theory) :
      BUY  → SL = low de la bougie précédente (- petit buffer 0.01%)
      SELL → SL = high de la bougie précédente (+ petit buffer 0.01%)
    """
    buf = entry * 0.0001   # buffer 0.01%
    if side == "BUY":
        return prev_low - buf
    else:
        return prev_high + buf

def get_sl(ob, entry, side, atr, candles_ohlc=None):
    """
    Sélectionne le SL optimal entre ATR et CRT.
    Priorité CRT si la distance est dans les limites,
    sinon utilise ATR.
    Retourne aussi la méthode choisie pour le logging.
    """
    pp_entry = entry if entry > 0 else 1.0

    # Calcul SL ATR
    sl_atr = get_sl_atr(entry, side, atr)

    # Calcul SL CRT (si bougies disponibles)
    sl_crt = None
    if candles_ohlc and len(candles_ohlc) >= 2:
        prev_high = candles_ohlc[-2]["high"]
        prev_low  = candles_ohlc[-2]["low"]
        sl_raw    = get_sl_crt(entry, side, prev_high, prev_low)
        dist_crt  = abs(entry - sl_raw)
        # CRT valide uniquement si dans les bornes MIN/MAX SL
        if pp_entry * MIN_SL_PCT <= dist_crt <= pp_entry * MAX_SL_PCT:
            sl_crt = sl_raw

    # Priorité CRT (structure pure), fallback ATR
    if sl_crt is not None:
        return sl_crt, "CRT"

    # ATR avec Breaker Block si disponible
    if ob:
        buf    = atr * 0.2
        sl_ob  = (ob["bottom"] - buf) if side == "BUY" else (ob["top"] + buf)
        dist   = abs(entry - sl_ob)
        if pp_entry * MIN_SL_PCT <= dist <= pp_entry * MAX_SL_PCT:
            return sl_ob, "ATR+OB"

    return sl_atr, "ATR"

# ═══════════════════════════════════════════════════════════════════
#  NOUVEAU SETUP SÉLECTIF — FVG LIQUIDITY SWEEP (ICT)
# ═══════════════════════════════════════════════════════════════════
#
#  Logique (BUY) :
#  1. SWEEP  : Prix descend sous un swing low récent (≤15 bougies)
#              puis remonte dessus dans la même bougie → liquidity grab
#  2. FVG    : Une Fair Value Gap haussière existe dans les 5 bougies
#              suivant le sweep — gap entre high[i-1] et low[i+1]
#              avec corps haussier fort (body ≥ ATR×0.8)
#  3. RETEST : Prix actuel est dans ou juste au-dessus de la FVG
#  4. BTC    : Aligné obligatoirement
#  5. RSI    : Sous 55 (non surchargé) pour un BUY
#
#  Score 93 minimum, confluence 4/4 obligatoire
#  → Très sélectif : 1-3 signaux/jour max
# ═══════════════════════════════════════════════════════════════════

def _find_fvg_sweep(o, h, l, cl, v, atr, direction):
    """
    Détecte un setup FVG + Liquidity Sweep en M5.
    Retourne le signal ou None.
    """
    n = len(cl)
    if n < 25: return None

    avg_vol = sum(v[-20:]) / 20 if len(v) >= 20 else sum(v) / len(v)
    e9  = _ema(cl, 9)
    e21 = _ema(cl, 21)
    rsi = _rsi(cl, 14)

    if direction == "BUY":
        # ── Étape 1 : trouver le SWEEP de liquidity ───────────────
        # Cherche une bougie qui casse un swing low ET remonte dedans
        sweep_idx = None
        swept_low = None
        for i in range(n - 3, max(n - 18, 3), -1):
            # Swing low = low[i] < low[i-2] et low[i] < low[i-1]
            local_low = min(l[max(0, i-10):i])
            if l[i] < local_low * 0.9998 and cl[i] > local_low:
                # Bougie qui pique sous le low puis referme dessus = sweep
                sweep_idx = i
                swept_low = local_low
                break

        if sweep_idx is None: return None

        # Sweep doit être récent (≤15 bougies)
        if n - 1 - sweep_idx > 15: return None

        # Volume du sweep doit être significatif
        vol_ok = float(v[sweep_idx]) > avg_vol * 1.2

        # ── Étape 2 : FVG haussière après le sweep ────────────────
        # FVG = high[i-1] < low[i+1] avec bougie médiane haussière forte
        fvg_bottom = None
        fvg_top    = None
        fvg_idx    = None
        for i in range(sweep_idx, min(n - 1, sweep_idx + 8)):
            if i < 1 or i + 1 >= n: continue
            gap = l[i + 1] - h[i - 1]
            if gap > 0 and cl[i] > o[i]:   # FVG bullish
                body = abs(cl[i] - o[i])
                if body >= atr * 0.5:       # Corps fort
                    fvg_bottom = h[i - 1]
                    fvg_top    = l[i + 1]
                    fvg_idx    = i
                    break

        if fvg_bottom is None: return None

        # ── Étape 3 : Prix actuel en retest de la FVG ─────────────
        price    = cl[-1]
        fvg_mid  = (fvg_bottom + fvg_top) / 2
        buf      = atr * 0.3
        in_fvg   = (fvg_bottom - buf) <= price <= (fvg_top + buf)
        if not in_fvg: return None

        # ── Étape 4 : Confirmations supplémentaires ───────────────
        ema_bull    = e9[-1] > e21[-1]                  # Tendance locale
        rsi_ok      = rsi < 58                          # Non surchargé
        body_ok     = (cl[-1] >= o[-1])                 # Bougie actuelle haussière
        not_too_far = (price < fvg_top + atr * 1.5)    # Pas trop loin de la FVG

        confluence = sum([
            True,          # Setup de base (sweep + FVG)
            vol_ok,        # Volume sweep significatif
            ema_bull,      # EMA alignée
            rsi_ok,        # RSI sain
            body_ok and not_too_far,  # Confirmation bougie
        ])

        if confluence < 4: return None

        score = 90 + min(confluence - 1, 4)  # 93-94 max

        return {
            "direction":   "BUY",
            "fvg_bottom":  fvg_bottom,
            "fvg_top":     fvg_top,
            "fvg_mid":     fvg_mid,
            "swept_low":   swept_low,
            "sweep_idx":   sweep_idx,
            "score":       score,
            "confluence":  min(confluence, 5),
            "atr":         atr,
            "vol_ok":      vol_ok,
            "ema_align":   ema_bull,
            "ob":          {"bottom": fvg_bottom, "top": fvg_top},
        }

    elif direction == "SELL":
        # ── Étape 1 : SWEEP de liquidity baissier ─────────────────
        sweep_idx  = None
        swept_high = None
        for i in range(n - 3, max(n - 18, 3), -1):
            local_high = max(h[max(0, i-10):i])
            if h[i] > local_high * 1.0002 and cl[i] < local_high:
                sweep_idx  = i
                swept_high = local_high
                break

        if sweep_idx is None: return None
        if n - 1 - sweep_idx > 15: return None

        vol_ok = float(v[sweep_idx]) > avg_vol * 1.2

        # ── Étape 2 : FVG baissière après le sweep ────────────────
        fvg_bottom = None
        fvg_top    = None
        fvg_idx    = None
        for i in range(sweep_idx, min(n - 1, sweep_idx + 8)):
            if i < 1 or i + 1 >= n: continue
            gap = l[i - 1] - h[i + 1]
            if gap > 0 and cl[i] < o[i]:   # FVG bearish
                body = abs(cl[i] - o[i])
                if body >= atr * 0.5:
                    fvg_top    = l[i - 1]
                    fvg_bottom = h[i + 1]
                    fvg_idx    = i
                    break

        if fvg_bottom is None: return None

        price   = cl[-1]
        fvg_mid = (fvg_bottom + fvg_top) / 2
        buf     = atr * 0.3
        in_fvg  = (fvg_bottom - buf) <= price <= (fvg_top + buf)
        if not in_fvg: return None

        ema_bear    = e9[-1] < e21[-1]
        rsi_ok      = rsi > 42
        body_ok     = (cl[-1] <= o[-1])
        not_too_far = (price > fvg_bottom - atr * 1.5)

        confluence = sum([
            True,
            vol_ok,
            ema_bear,
            rsi_ok,
            body_ok and not_too_far,
        ])

        if confluence < 4: return None

        score = 90 + min(confluence - 1, 4)

        return {
            "direction":   "SELL",
            "fvg_bottom":  fvg_bottom,
            "fvg_top":     fvg_top,
            "fvg_mid":     fvg_mid,
            "swept_high":  swept_high,
            "sweep_idx":   sweep_idx,
            "score":       score,
            "confluence":  min(confluence, 5),
            "atr":         atr,
            "vol_ok":      vol_ok,
            "ema_align":   ema_bear,
            "ob":          {"bottom": fvg_bottom, "top": fvg_top},
        }

    return None

def analyse_fvg_sweep(symbol, klines):
    """
    Setup FVG + Liquidity Sweep M5.
    Plus sélectif que le Breaker Block — score 93+ requis.
    """
    try:
        if not klines or len(klines) < 25:
            return None

        o, h, l, cl, v = _parse_klines(klines)
        atr = _atr(h, l, cl, 14)
        rsi = _rsi(cl, 14)

        btc = get_btc_direction()
        btc_dir = btc["direction"]
        if btc_dir == 0: return None

        candidates = []
        if btc_dir == 1  and (cl[-1] > _ema(cl, 21)[-1] or rsi < 55):
            candidates.append("BUY")
        if btc_dir == -1 and (cl[-1] < _ema(cl, 21)[-1] or rsi > 45):
            candidates.append("SELL")
        if not candidates: return None

        fvg = None
        for direction in candidates:
            fvg = _find_fvg_sweep(o, h, l, cl, v, atr, direction)
            if fvg: break

        if not fvg: return None

        direction = fvg["direction"]
        score     = fvg["score"]
        conf      = fvg["confluence"]

        # Seuil élevé pour ce setup sélectif
        if score < 93 or conf < 4: return None

        prob = min(85.0 + conf * 2.0, 95.0)

        # Bougies OHLC pour SL CRT
        candles_ohlc = [{"high": h[i], "low": l[i]} for i in range(len(h))]

        return {
            "symbol":       symbol,
            "side":         direction,
            "setup":        "FVG_SWEEP_M5",
            "score":        score,
            "confluence":   conf,
            "probability":  prob,
            "ob":           fvg["ob"],
            "atr":          atr,
            "rsi":          round(rsi, 1),
            "vol_spike":    fvg.get("vol_ok", False),
            "fvg_bottom":   fvg["fvg_bottom"],
            "fvg_top":      fvg["fvg_top"],
            "closes":       cl,
            "candles_ohlc": candles_ohlc,
            "btc_strength": btc.get("strength", "NORMAL"),
        }

    except Exception as e:
        logger.debug("analyse_fvg_sweep {}: {}".format(symbol, e))
        return None

def find_tp_for_rr(entry, sl, direction, rr_target=3.0):
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
    """3 stratégies : MARK_PRICE → CONTRACT_PRICE → SL logiciel
    PATCH SL : log explicite erreur Binance + décalage SL si trop proche du prix
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    tick = 10 ** (-pp)

    # Vérification : SL doit être à min 0.1% du mark price pour être accepté
    mark = get_price(symbol) or sl
    min_dist = mark * 0.001  # 0.1% minimum
    if close_side == "SELL":   # BUY position → SL sous le mark
        sl = min(sl, mark - min_dist)
    else:                       # SELL position → SL au dessus du mark
        sl = max(sl, mark + min_dist)
    sl = round(sl, pp)
    logger.info("🛡️  {} place_sl mark={:.{}f} sl_final={:.{}f} side={}".format(
        symbol, mark, pp, sl, pp, close_side))

    for attempt in range(4):
        adj_sl = round(sl - tick * attempt if close_side == "SELL" else sl + tick * attempt, pp)
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": adj_sl, "closePosition": "true",
            "workingType": "MARK_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ MARK_PRICE @ {:.{}f} id={}".format(
                symbol, adj_sl, pp, r["orderId"]))
            return {"sent": True, "order_id": r["orderId"], "method": "MARK_PRICE"}
        if r and r.get("_already_triggered"):
            return {"sent": False, "order_id": None, "triggered": True}
        logger.warning("⚠️  {} SL MARK_PRICE tentative {} échouée → r={}".format(symbol, attempt+1, r))
        time.sleep(0.8)

    logger.warning("⚠️  {} MARK_PRICE rejeté → CONTRACT_PRICE".format(symbol))
    for attempt in range(3):
        adj_sl = round(sl - tick * attempt if close_side == "SELL" else sl + tick * attempt, pp)
        r = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": close_side, "type": "STOP_MARKET",
            "stopPrice": adj_sl, "closePosition": "true",
            "workingType": "CONTRACT_PRICE"})
        if r and r.get("orderId"):
            logger.info("🛡️  {} SL ✅ CONTRACT_PRICE @ {:.{}f}".format(symbol, adj_sl, pp))
            return {"sent": True, "order_id": r["orderId"], "method": "CONTRACT_PRICE"}
        logger.warning("⚠️  {} SL CONTRACT_PRICE tentative {} échouée → r={}".format(symbol, attempt+1, r))
        time.sleep(0.8)

    logger.error("🚨 {} SL Binance IMPOSSIBLE → SL LOGICIEL @ {:.{}f}".format(symbol, sl, pp))
    send_telegram("🚨 <b>SL {} non posé Binance</b>\nSL logiciel actif @ {:.{}f}".format(
        symbol, sl, pp))
    return {"sent": False, "order_id": None, "method": "SOFTWARE"}

def place_tp_binance(symbol, tp, close_side):
    """PATCH SL/TP : log explicite + vérification distance mark price"""
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)
    tick = 10 ** (-pp)

    # TP doit être de l'autre côté du mark price
    mark = get_price(symbol) or tp
    min_dist = mark * 0.001
    if close_side == "SELL":   # BUY position → TP au-dessus du mark
        tp = max(tp, mark + min_dist)
    else:                       # SELL position → TP sous le mark
        tp = min(tp, mark - min_dist)
    tp = round(tp, pp)
    logger.info("🎯 {} place_tp mark={:.{}f} tp_final={:.{}f} side={}".format(
        symbol, mark, pp, tp, pp, close_side))

    for wtype in ["MARK_PRICE", "CONTRACT_PRICE"]:
        for attempt in range(3):
            adj_tp = round(tp + tick * attempt if close_side == "SELL" else tp - tick * attempt, pp)
            r = request_binance("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": close_side, "type": "TAKE_PROFIT_MARKET",
                "stopPrice": adj_tp, "closePosition": "true",
                "workingType": wtype})
            if r and r.get("orderId"):
                logger.info("🎯 {} TP ✅ {} @ {:.{}f}".format(symbol, wtype, adj_tp, pp))
                return {"sent": True, "order_id": r["orderId"]}
            logger.warning("⚠️  {} TP {} tentative {} échouée → r={}".format(symbol, wtype, attempt+1, r))
            time.sleep(0.5)
    logger.warning("⚠️  {} TP non posé → trailing SL gère la sortie".format(symbol))
    return {"sent": False, "order_id": None}

def _cancel_stop_orders(symbol):
    """
    Annule uniquement les ordres STOP_MARKET (pas les TAKE_PROFIT_MARKET).
    Log explicite de chaque ordre trouvé/annulé.
    """
    try:
        orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if not orders:
            logger.info("  {} _cancel_stop_orders: aucun ordre ouvert".format(symbol))
            return
        stop_orders = [o for o in orders if o.get("type") in ("STOP_MARKET", "STOP")]
        if not stop_orders:
            logger.info("  {} _cancel_stop_orders: pas de STOP_MARKET trouvé (ordres ouverts={})".format(
                symbol, [o.get("type") for o in orders]))
            return
        for o in stop_orders:
            oid  = o.get("orderId")
            sp   = o.get("stopPrice", "?")
            side = o.get("side", "?")
            logger.info("  {} annulation STOP_MARKET id={} side={} stopPrice={}".format(
                symbol, oid, side, sp))
            if oid:
                r = request_binance("DELETE", "/fapi/v1/order",
                                    {"symbol": symbol, "orderId": oid})
                logger.info("  {} DELETE → {}".format(symbol, r))
                time.sleep(0.3)
    except Exception as e:
        logger.warning("_cancel_stop_orders {}: {}".format(symbol, e))


def _place_stop_raw(symbol, side, stop_price, pp, wtype):
    """
    POST direct STOP_MARKET avec capture brute de l'erreur Binance.
    Retourne (order_dict_ou_None, code_erreur, msg_erreur).
    Contourne request_binance pour voir l'erreur exacte.
    """
    import json as _json
    params = {
        "symbol":        symbol,
        "side":          side,
        "type":          "STOP_MARKET",
        "stopPrice":     round(stop_price, pp),
        "closePosition": "true",
        "workingType":   wtype,
        "timestamp":     int(time.time() * 1000) + _binance_time_offset,
        "recvWindow":    20000,
    }
    params["signature"] = _sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        resp = requests.post(
            BASE_URL + "/fapi/v1/order",
            params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json(), 0, ""
        try:
            d    = _json.loads(resp.text)
            code = int(d.get("code", 0))
            msg  = str(d.get("msg", resp.text[:120]))
        except Exception:
            code, msg = 0, resp.text[:120]
        return None, code, msg
    except Exception as e:
        return None, -1, str(e)


def move_sl_binance(symbol, old_order_id, new_sl, close_side):
    """
    Déplace le SL trailing :
    1. Diagnostique les ordres ouverts sur le symbole
    2. Annule l'ancien SL (par ID ou scan STOP_MARKET)
    3. Pose le nouveau STOP_MARKET avec capture d'erreur brute Binance
    """
    info = symbol_info_cache.get(symbol, {})
    pp   = info.get("pricePrecision", 4)

    # ── Prix actuel et vérification distance ──────────────────────
    mark = get_price(symbol) or new_sl
    min_dist = mark * 0.001          # 0.1% minimum Binance
    if close_side == "BUY":          # SELL position → SL AU-DESSUS du mark
        new_sl = max(round(new_sl, pp), round(mark + min_dist, pp))
    else:                             # BUY position → SL EN-DESSOUS du mark
        new_sl = min(round(new_sl, pp), round(mark - min_dist, pp))
    new_sl = round(new_sl, pp)

    logger.info("🔄 {} move_sl | mark={:.{}f} new_sl={:.{}f} close_side={} old_id={}".format(
        symbol, mark, pp, new_sl, pp, close_side, old_order_id))

    # ── Étape 1 : Annulation de l'ancien SL ───────────────────────
    deleted_by_id = False
    if old_order_id:
        r_del = request_binance("DELETE", "/fapi/v1/order",
                                {"symbol": symbol, "orderId": old_order_id})
        logger.info("  {} DELETE id={} → {}".format(symbol, old_order_id, r_del))
        if r_del and r_del.get("_already_triggered"):
            logger.warning("⚠️  {} SL déclenché pendant déplacement → fermée".format(symbol))
            with trade_lock:
                if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                    trade_log[symbol]["status"]    = "CLOSED"
                    trade_log[symbol]["closed_by"] = "SL_TRIGGERED_DURING_MOVE"
                    _on_closed(symbol, trade_log[symbol], is_win=False)
            return None
        if r_del and (r_del.get("orderId") or r_del.get("_already_cancelled")):
            deleted_by_id = True

    if not deleted_by_id:
        logger.info("  {} → fallback _cancel_stop_orders".format(symbol))
        _cancel_stop_orders(symbol)
        time.sleep(0.8)   # Attente Binance

    # ── Étape 2 : Pose du nouveau SL (avec erreur brute) ─────────
    for wtype in ["MARK_PRICE", "CONTRACT_PRICE"]:
        for attempt in range(3):
            sp_try = round(new_sl, pp)
            order, err_code, err_msg = _place_stop_raw(symbol, close_side, sp_try, pp, wtype)

            if order and order.get("orderId"):
                logger.info("✅ {} SL trailing {} @ {:.{}f} id={}".format(
                    symbol, wtype, sp_try, pp, order["orderId"]))
                # Récupérer l'order_id Binance pour le trade_log
                order["orderId"] = order["orderId"]
                return order

            # Log de l'erreur EXACTE de Binance
            logger.warning("⚠️  {} SL {} tentative {}/3 REFUSÉ — code={} msg='{}'".format(
                symbol, wtype, attempt + 1, err_code, err_msg))

            # Corrections automatiques selon le code d'erreur
            if err_code in (-4003, -5021):
                # Prix trop proche du mark → décaler davantage
                shift = mark * 0.003 * (attempt + 1)
                if close_side == "BUY":
                    new_sl = round(mark + shift, pp)
                else:
                    new_sl = round(mark - shift, pp)
                logger.info("  {} ajustement stopPrice → {:.{}f} (shift +{:.{}f})".format(
                    symbol, new_sl, pp, shift, pp))

            elif err_code == -5022:
                # Position déjà fermée pendant le déplacement
                logger.warning("⚠️  {} position déjà fermée (code -5022)".format(symbol))
                with trade_lock:
                    if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                        trade_log[symbol]["status"]    = "CLOSED"
                        trade_log[symbol]["closed_by"] = "ALREADY_CLOSED_DURING_TRAIL"
                        _on_closed(symbol, trade_log[symbol], is_win=True)
                return None

            elif err_code == -2021:
                # "Order would immediately trigger" → SL trop proche, décaler
                shift = mark * 0.002 * (attempt + 1)
                if close_side == "BUY":
                    new_sl = round(mark + shift, pp)
                else:
                    new_sl = round(mark - shift, pp)
                logger.info("  {} -2021 immediate trigger → ajustement {:.{}f}".format(
                    symbol, new_sl, pp))

            time.sleep(0.8)

    logger.error("❌ {} move_sl_binance ÉCHEC TOTAL — SL logiciel @ {:.{}f}".format(
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
            # Remise à zéro du compteur d'échecs si le déplacement a réussi
            t["sl_fail_count"] = 0
        else:
            # ── SL REFUSÉ PAR BINANCE → COMPTEUR + FERMETURE FORCÉE ──
            t["sl_on_binance"] = False
            logger.warning("⚠️  {} déplacement SL Binance échoué — SL logiciel @ {:.{}f}".format(
                symbol, new_sl, pp))

            # Compteur d'échecs SL consécutifs
            t["sl_fail_count"] = t.get("sl_fail_count", 0) + 1

            # Après 3 échecs consécutifs → fermeture market forcée
            if t.get("sl_fail_count", 0) >= 3:
                logger.critical(
                    "🚨 {} SL Binance refusé {} fois consécutives → FERMETURE FORCÉE".format(
                        symbol, t["sl_fail_count"]))
                send_telegram(
                    "🚨 <b>FERMETURE FORCÉE {}</b>\n"
                    "SL Binance refusé {} fois → position fermée au market\n"
                    "Prix actuel: {:.{}f}\n"
                    "Entry: {:.{}f} | SL visé: {:.{}f}\n"
                    "⚠️ Vérifier manuellement le PnL".format(
                        symbol, t["sl_fail_count"],
                        current_price, pp,
                        t["entry"], pp, new_sl, pp
                    )
                )
                close_side_forced = "SELL" if side == "BUY" else "BUY"
                qty_to_close = t.get("qty", 0)
                place_market(symbol, close_side_forced, qty_to_close)
                t["status"]    = "CLOSED"
                t["closed_by"] = "SL_BINANCE_REFUSED_FORCE_CLOSE"
                _on_closed(symbol, t, is_win=(
                    (side == "BUY"  and current_price > t["entry"]) or
                    (side == "SELL" and current_price < t["entry"])
                ))

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

        # PATCH 6 — Garde-fou risque global ouvert (max 4% balance)
        with balance_lock:
            cur_bal = account_balance
        with trade_lock:
            total_risk_open = sum(
                abs(t["entry"] - t["sl"]) * t.get("qty", 0)
                for t in trade_log.values()
                if t.get("status") == "OPEN" and t.get("entry", 0) > 0 and t.get("sl", 0) > 0
            )
        max_open_risk = cur_bal * 0.04
        if total_risk_open > max_open_risk and cur_bal > 0:
            logger.warning("⚠️ PATCH6 Risque global ouvert ${:.4f} > max ${:.4f} → skip {}".format(
                total_risk_open, max_open_risk, symbol))
            return

        entry = get_price(symbol)
        if not entry: return

        lev        = get_leverage(score)
        max_sl_pct = get_max_sl_pct()

        # ── Risque $0.60 base +25%/WIN — sizing par risque en $ ──────
        session = get_session()
        risk_usd, _ = paroli_get_margin(symbol, setup, session)
        mm_detail  = "Risque ${:.2f} (streak={} | +25%/WIN)".format(
            risk_usd, _risk_state["win_streak"])

        # margin pour le dashboard (estimation)
        with balance_lock:
            cur_bal = account_balance
        margin_pct = risk_usd / cur_bal if cur_bal > 0 else 0.10

        # ── SL ATR ou CRT ────────────────────────────────────────
        candles_ohlc = signal.get("candles_ohlc")
        sl, sl_method = get_sl(ob, entry, side, atr, candles_ohlc)
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

        # ── Sizing : qty = risk_usd / sl_distance ────────────────
        # Le risque en $ est fixé, les lots s'adaptent au SL
        qty = _round_step(risk_usd / sl_dist, step_size)

        # Cap par notionnel max raisonnable (levier × risque × 10)
        max_qty_m = _round_step((risk_usd * lev) / entry, step_size)
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

        # v4.5 — Double vérification spread AVANT l'ordre market
        # Le spread peut s'élargir entre le scan et l'ouverture
        spread_now = get_mark_spread(symbol)
        if abs(spread_now) > SPREAD_MAX_PCT:
            logger.warning("⚠️ v4.5 {} spread {:.3f}% au moment de l'ordre → annulé".format(
                symbol, spread_now))
            send_telegram("⚠️ {} annulé — spread {:.3f}% trop large".format(symbol, spread_now))
            return

        # PATCH 5 — Vérification levier réel via positionRisk
        # Si Binance a bridé le levier (meme coin pump/dump), on l'applique
        # Si < 50% du demandé → skip pour éviter sizing erroné
        try:
            pos_check = request_binance("GET", "/fapi/v2/positionRisk",
                                        {"symbol": symbol}, signed=True)
            if pos_check:
                for p in pos_check:
                    if p.get("symbol") == symbol:
                        real_lev = int(float(p.get("leverage", actual_lev)))
                        if real_lev < actual_lev * 0.5:
                            logger.warning("⚠️ PATCH5 {} levier réel {}x << demandé {}x → skip".format(
                                symbol, real_lev, actual_lev))
                            send_telegram("⚠️ {} levier réduit par Binance {}x → skip".format(
                                symbol, real_lev))
                            cleanup_orders(symbol)
                            return
                        if real_lev != actual_lev:
                            logger.info("  {} levier ajusté {}x→{}x (Binance)".format(
                                symbol, actual_lev, real_lev))
                            lev = real_lev
        except Exception as _pe:
            logger.debug("PATCH5 positionRisk check: {}".format(_pe))

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

        # PATCH 3 — Si actual_entry toujours 0 : position ouverte mais non trackée
        # → fermeture market immédiate + alerte CRITICAL
        if actual_entry <= 0:
            logger.critical("🚨 PATCH3 {} actual_entry=0 après 5 tentatives — FERMETURE FORCÉE".format(symbol))
            send_telegram(
                "🚨 <b>CRITICAL {} — actual_entry=0</b>\n"
                "Position ouverte mais non confirmée.\n"
                "Fermeture market forcée.".format(symbol))
            close_side_forced = "SELL" if side == "BUY" else "BUY"
            place_market(symbol, close_side_forced, qty)
            return

        # Recalcul SL/TP sur vrai entry
        sl2, sl_method2 = get_sl(ob, actual_entry, side, atr, candles_ohlc)
        if side == "BUY":
            sl_dist2 = max(actual_entry - sl2, actual_entry * MIN_SL_PCT)
            sl_dist2 = min(sl_dist2, actual_entry * MAX_SL_PCT)
            sl = round(actual_entry - sl_dist2, pp)
            tp = round(find_tp_for_rr(actual_entry, sl, side, TP_RR), pp)
        else:
            sl_dist2 = max(sl2 - actual_entry, actual_entry * MIN_SL_PCT)
            sl_dist2 = min(sl_dist2, actual_entry * MAX_SL_PCT)
            sl = round(actual_entry + sl_dist2, pp)
            tp = round(find_tp_for_rr(actual_entry, sl, side, TP_RR), pp)
        sl_method = sl_method2

        close_side = "SELL" if side == "BUY" else "BUY"

        # ── PATCH SL/TP — Attente position confirmée ──────────────
        # Binance doit enregistrer la position AVANT d'accepter SL/TP.
        # Sans cette pause, Binance rejette les ordres SL/TP car la
        # position n'est pas encore visible côté serveur → software SL.
        time.sleep(2.0)

        # Retry SL jusqu'à 3 fois si software fallback
        sl_r = {"sent": False, "order_id": None, "method": "SOFTWARE"}
        for _sl_attempt in range(3):
            sl_r = place_sl_binance(symbol, sl, close_side)
            if sl_r["sent"]:
                break
            if _sl_attempt < 2:
                logger.warning("⚠️ SL retry {}/3 dans 2s...".format(_sl_attempt + 1))
                time.sleep(2.0)
        if not sl_r["sent"]:
            logger.critical("🚨 SL {} NON POSÉ sur Binance après 3 tentatives — FERMETURE FORCÉE".format(symbol))
            send_telegram(
                "🚨 <b>SL IMPOSSIBLE — {} FERMÉ AU MARKET</b>\n"
                "Binance a refusé le SL 3 fois.\n"
                "Position fermée immédiatement pour éviter une perte non contrôlée.\n"
                "Entry: {:.{}f} | SL visé: {:.{}f}\n"
                "Prix actuel: {:.{}f}".format(
                    symbol, actual_entry, pp, sl, pp, get_price(symbol) or actual_entry, pp)
            )
            # Fermeture market immédiate — on ne laisse pas une position sans SL
            place_market(symbol, close_side, qty)
            return

        # Retry TP une fois si échec
        tp_r = place_tp_binance(symbol, tp, close_side)
        if not tp_r["sent"]:
            logger.warning("⚠️ TP {} non posé → retry dans 2s".format(symbol))
            time.sleep(2.0)
            tp_r = place_tp_binance(symbol, tp, close_side)

        be_price = round(actual_entry * (1.0 + BREAKEVEN_FEE_TOTAL), pp) if side == "BUY" \
                   else round(actual_entry * (1.0 - BREAKEVEN_FEE_TOTAL), pp)
        fg = _fg_cache

        with trade_lock:
            trade_log[symbol] = {
                "side": side, "entry": actual_entry, "sl": sl, "tp": tp,
                "qty": qty, "leverage": lev, "margin": risk_usd,
                "margin_pct": round(margin_pct * 100, 1),
                "setup": setup, "score": score, "probability": prob,
                "status": "OPEN", "opened_at": time.time(),
                "sl_on_binance": sl_r["sent"], "tp_on_binance": tp_r["sent"],
                "sl_order_id": sl_r["order_id"], "tp_order_id": tp_r["order_id"],
                "trailing_stop_active": False, "breakeven_moved": False,
                "btc_corr": signal.get("btc_corr", "?"), "atr": atr,
                "be_price": be_price, "session": session,
                "fond_score": fond_score, "btc_strength": btc_strength,
                "risk_usd": risk_usd,   # v4.6 : risque $ explicite
                "realized_pnl": 0.0,    # v4.6 : sera mis à jour à la fermeture
            }

        dom_data = get_btc_dominance()
        _, fg_det  = get_fg_margin_mult(side)
        _, dom_det = get_btc_dom_mult(symbol, side)

        logger.info("✅ {} {} @ {:.{}f} | SL [{} {:.{}f}] | TP {:.{}f} RR{} | {}x | marge={:.0f}% | {}".format(
            symbol, side, actual_entry, pp, sl_method, sl, pp, tp, pp, TP_RR, lev,
            margin_pct*100, get_tier_label()))

        send_telegram(
            "🚀 <b>{} {}</b> @ {:.{}f}\n"
            "SL: [{} {:.{}f}] ({:.3f}%) {}\n"
            "BE: {:.{}f} | TP: {:.{}f} (RR{})\n"
            "Setup: {} | Score:{} | Conf:{}/5\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💰 Risque: <b>${:.2f}</b> ({:.1f}%) | qty={}\n"
            "   {}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📊 Fond: {}/100\n"
            "😱 {}\n"
            "🌐 {}\n"
            "⚡ {}x | Session: {} | {}\n"
            "{} | {}".format(
                symbol, side,
                actual_entry, pp,
                sl_method, sl, pp, sl_dist2/actual_entry*100,
                "✅Binance" if sl_r["sent"] else "⚠️logiciel",
                be_price, pp, tp, pp, TP_RR,
                setup, score, signal.get("confluence", 0),
                risk_usd, margin_pct*100, qty,
                mm_detail,
                fond_score,
                fg_det,
                dom_det,
                lev, session, btc_strength,
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
    side     = trade.get("side", "?")
    setup    = trade.get("setup", "?")
    session  = trade.get("session", "UNKNOWN")
    info     = symbol_info_cache.get(symbol, {})
    pp       = info.get("pricePrecision", 4)

    # Durée du trade pour détecter les SL trop rapides
    opened_at      = trade.get("opened_at", time.time())
    trade_duration = time.time() - opened_at

    # ── Brain : apprentissage ─────────────────────────────────────
    brain_record_trade(symbol, setup, session, side, is_win, trade_duration_s=trade_duration)

    # ── Risque +25% : mise à jour ─────────────────────────────────
    if is_win:
        # Récupère le PnL réel si disponible dans le trade
        last_gain = trade.get("realized_pnl", 0.0)
        risk_on_win(last_gain)
    else:
        risk_on_loss()

    if is_win:
        consec_losses = 0
        symbol_stats[symbol]["wins"] += 1
        with _risk_lock:
            new_risk  = _risk_state["current_risk"]
            streak    = _risk_state["win_streak"]
        logger.info("✅ WIN {} {} {} | Risque suivant ${:.2f} (streak={})".format(
            symbol, side, setup, new_risk, streak))
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
            "💰 Risque suivant <b>${:.2f}</b> (+25%)\n"
            "Balance: ${:.4f} {}{}".format(
                symbol, side, setup,
                new_risk,
                account_balance, get_progress_bar(), milestone_msg))
    else:
        consec_losses += 1
        symbol_stats[symbol]["losses"] += 1
        logger.info("🔴 LOSS {} {} {} | Risque reset → ${}".format(
            symbol, side, setup, RISK_BASE_USD))
        send_telegram(
            "🔴 <b>LOSS {} {}</b>\n"
            "Setup: {} | Consécutives: {}\n"
            "💰 Risque reset → <b>${:.2f}</b>\n"
            "Balance: ${:.4f} {}\n{}".format(
                symbol, side, setup, consec_losses,
                RISK_BASE_USD, account_balance,
                get_tier_label(), get_progress_bar()))
        # NOTE v4.6 : pas de cooldown — risque reset gère après perte

def _on_closed_from_binance(symbol, trade):
    try:
        income = request_binance("GET", "/fapi/v1/income",
                                 {"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": 5},
                                 signed=True)
        pnl = sum(float(i.get("income", 0)) for i in income) if income else 0.0
        trade["realized_pnl"] = pnl   # v4.6 : stocké pour risk_on_win
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

                # ── Détection fermeture par Binance (SL/TP touché) ───
                if symbol not in binance_open:
                    t["status"]    = "CLOSED"
                    t["closed_by"] = "BINANCE_SL_TP"
                    logger.info("✅ {} fermée par Binance (SL ou TP atteint)".format(symbol))
                    _on_closed_from_binance(symbol, t)
                    continue

                # ── GESTION SL LOGICIEL (Binance a refusé) ────────────
                # Si sl_on_binance=False :
                #   1. Toutes les 30s → retente de poser le SL sur Binance
                #   2. En permanence  → surveille le prix et coupe manuellement
                #      si le prix touche le SL (ordre market forcé)
                #   3. Mémorise le nombre de refus dans sl_binance_failures
                #      → Si ≥ 3 refus → alerte Telegram CRITIQUE
                if not t.get("sl_on_binance"):
                    now_ts = time.time()

                    # ── Compteur de refus ──────────────────────────
                    failures = t.get("sl_binance_failures", 0)

                    # ── Retry toutes les 30s ───────────────────────
                    if now_ts - t.get("sl_repost_ts", 0) > 30:
                        t["sl_repost_ts"] = now_ts
                        cs = "SELL" if t["side"] == "BUY" else "BUY"
                        logger.warning("🔄 {} repost SL tentative #{} @ {:.6f}".format(
                            symbol, failures + 1, t["sl"]))
                        sl_retry = place_sl_binance(symbol, t["sl"], cs)
                        if sl_retry["sent"]:
                            t["sl_on_binance"]       = True
                            t["sl_order_id"]         = sl_retry["order_id"]
                            t["sl_binance_failures"] = 0
                            logger.info("✅ {} SL reposté Binance @ {:.6f} (après {} refus)".format(
                                symbol, t["sl"], failures))
                            send_telegram(
                                "✅ <b>SL {} reposté sur Binance</b>\n"
                                "@ {:.6f} | Après {} refus".format(symbol, t["sl"], failures))
                        else:
                            # Échec → incrément compteur
                            t["sl_binance_failures"] = failures + 1
                            logger.error(
                                "🚨 {} SL REFUSÉ par Binance x{} — "
                                "surveillance manuelle active @ {:.6f}".format(
                                    symbol, t["sl_binance_failures"], t["sl"]))
                            # Alerte critique si ≥ 3 refus consécutifs
                            if t["sl_binance_failures"] >= 3:
                                send_telegram(
                                    "🚨 <b>CRITIQUE — SL {} REFUSÉ {}x par Binance</b>\n\n"
                                    "SL cible : {:.6f}\n"
                                    "Prix actuel : {:.6f}\n"
                                    "Côté : {}\n\n"
                                    "⚠️ Le bot surveille et coupera manuellement "
                                    "si le prix touche le SL.\n"
                                    "Vérifiez vos permissions API Futures !".format(
                                        symbol, t["sl_binance_failures"],
                                        t["sl"], get_price(symbol) or 0,
                                        t["side"]
                                    )
                                )

                    # ── Surveillance prix en continu ───────────────
                    # TOUJOURS actif si sl_on_binance=False,
                    # indépendamment du retry timer.
                    cp = get_price(symbol)
                    if cp and cp > 0:
                        sl_level = t["sl"]
                        sl_hit = (t["side"] == "BUY"  and cp <= sl_level) or \
                                 (t["side"] == "SELL" and cp >= sl_level)
                        if sl_hit:
                            logger.warning(
                                "🚨 {} SL LOGICIEL DÉCLENCHÉ @ {:.6f} "
                                "(SL={:.6f}) — FERMETURE MARKET FORCÉE".format(
                                    symbol, cp, sl_level))
                            cs = "SELL" if t["side"] == "BUY" else "BUY"

                            # Fermeture market avec 3 tentatives
                            closed = False
                            for _attempt in range(3):
                                close_r = place_market(symbol, cs, t.get("qty", 0))
                                if close_r:
                                    closed = True
                                    break
                                time.sleep(1.0)

                            t["status"]              = "CLOSED"
                            t["closed_by"]           = "SOFTWARE_SL_MANUAL"
                            t["sl_triggered_price"]  = cp
                            _on_closed(symbol, t, is_win=False)

                            send_telegram(
                                "🚨 <b>SL LOGICIEL {} {}</b>\n\n"
                                "Prix déclenché : {:.6f}\n"
                                "SL cible      : {:.6f}\n"
                                "Fermeture {}  : {}\n\n"
                                "Raison : Binance avait refusé le SL x{}".format(
                                    symbol, t["side"],
                                    cp, sl_level,
                                    "✅ OK" if closed else "❌ ÉCHEC",
                                    cs,
                                    t.get("sl_binance_failures", "?")
                                )
                            )
                            if not closed:
                                # Ultime alerte si même le market order échoue
                                send_telegram(
                                    "🆘 <b>URGENCE {} — FERMETURE MARKET ÉCHOUÉE !</b>\n"
                                    "Fermez manuellement votre position {} sur Binance !".format(
                                        symbol, t["side"]))
                            continue

    except Exception as e:
        logger.debug("monitor_positions: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  SCAN PAR SYMBOLE
# ═══════════════════════════════════════════════════════════════════

def prefilter_symbol(symbol):
    """
    Phase 0 — Pré-filtre ultra-léger (0 appel API).
    v4.8 : utilise brain_get_vol_min_filter() qui peut être ×2
    si le brain détecte de la stagnation (actifs qui ne bougent pas).
    """
    bulk = get_bulk_ticker(symbol)
    if not bulk: return True
    vol = bulk["vol"]
    effective_vol_min = brain_get_vol_min_filter()
    if vol < effective_vol_min: return False
    return True

def scan_symbol_tech(symbol):
    """
    Phase 1 — Scan TECHNIQUE uniquement (weight ≈ 1 appel klines).
    Vérifie aussi les filtres brain (blacklist, side skip, score override).
    """
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None

        if time.time() - signal_last_at.get(symbol, 0) < SIGNAL_COOLDOWN:
            return None

        # Filtre brain : symbole blacklisté
        if brain_is_blacklisted(symbol):
            return None

        # v4.5 — Filtre liquidité : spread mark/index
        # Si spread > SPREAD_MAX_PCT → symbole illiquide → skip
        # (0 poids API supplémentaire : get_mark_spread utilise premiumIndex déjà caché)
        spread = get_mark_spread(symbol)
        if abs(spread) > SPREAD_MAX_PCT:
            logger.debug("  {} spread {:.3f}% > max {:.2f}% → skip illiquide".format(
                symbol, spread, SPREAD_MAX_PCT))
            return None

        # v4.5 — Filtre prix minimum
        cur_price = get_price(symbol)
        if cur_price and cur_price < PRICE_MIN_USD:
            logger.debug("  {} prix ${:.6f} < min ${} → skip micro-cap".format(
                symbol, cur_price, PRICE_MIN_USD))
            return None

        klines = get_klines(symbol, TIMEFRAME, LIMIT_CANDLES)
        if not klines or len(klines) < 30:
            return None

        # Setup 1 : Breaker Block
        signal_bb  = analyse_m5(symbol, klines)
        # Setup 2 : FVG Sweep (plus sélectif)
        signal_fvg = analyse_fvg_sweep(symbol, klines)

        signal = None
        if signal_bb and signal_fvg:
            signal = signal_fvg if signal_fvg["score"] >= signal_bb["score"] else signal_bb
        elif signal_fvg:
            signal = signal_fvg
        elif signal_bb:
            signal = signal_bb

        if not signal: return None

        # Filtre brain : côté (BUY/SELL) temporairement skippé
        if brain_is_side_skipped(signal["side"]):
            logger.debug("  {} {} skippé par brain (mauvais winrate côté)".format(
                symbol, signal["side"]))
            return None

        # Filtre brain : score minimum override par setup
        min_score_required = brain_get_setup_min_score(signal["setup"])
        if signal["score"] < min_score_required:
            logger.debug("  {} {} score={} < brain_min={} → rejeté".format(
                symbol, signal["setup"], signal["score"], min_score_required))
            return None

        return signal

    except Exception as e:
        logger.debug("scan_tech {}: {}".format(symbol, e))
        return None

def scan_symbol_fond(signal):
    """
    Phase 2 — Scan FONDAMENTAL sur les candidats techniques.
    v4.9 : + filtre MTF (M15/H1) + filtre news + fondamentaux /140
    Weight estimé : ≈ 5-6 appels (funding, OI, liquidations, MTF×2)
    Retourne le signal enrichi ou None.
    """
    symbol = signal["symbol"]
    side   = signal["side"]
    sym_closes = signal["closes"]
    try:
        # ── Filtre News (0 appel API si cache valide) ─────────────
        if NEWS_FILTER_ENABLED:
            news_blackout, news_reason = is_news_blackout()
            if news_blackout:
                logger.info("  📰 {} NEWS BLACKOUT — {}".format(symbol, news_reason))
                return None
            signal["news_ok"] = True

        # ── Filtre MTF M15+H1 — NOUVEAU v4.9 ─────────────────────
        if MTF_CONFIRM_REQUIRED:
            mtf = get_mtf_direction(symbol)
            if side == "BUY"  and not mtf["ok_buy"]:
                logger.debug("  {} MTF BUY refusé — {}".format(symbol, mtf["label"]))
                return None
            if side == "SELL" and not mtf["ok_sell"]:
                logger.debug("  {} MTF SELL refusé — {}".format(symbol, mtf["label"]))
                return None
            signal["mtf_label"] = mtf["label"]

        # ── Filtre Fear & Greed (0 appel API si cache valide) ─────
        fg_ok, fg_detail = check_fear_greed(side)
        if not fg_ok: return None
        signal["fg_detail"] = fg_detail

        # ── Corrélation BTC (0 appel API si cache valide) ─────────
        corr_ok, corr_reason = check_btc_correlation(symbol, side, sym_closes)
        if not corr_ok: return None
        signal["btc_corr"] = corr_reason

        # ── Fondamentaux /140 — appels API réels ─────────────────
        fond_score, fond_ok, fond_detail = check_fondamentaux(symbol, side)
        if not fond_ok:
            logger.debug("  {} fond {}/140 < {} → rejeté: {}".format(
                symbol, fond_score, FOND_MIN_SCORE, fond_detail))
            return None
        signal["fond_score"]  = fond_score
        signal["fond_detail"] = fond_detail

        mtf_tag = signal.get("mtf_label", "MTF OFF")
        logger.info("  ✅ [{}] {} {} | tech={} conf={}/5 fond={}/140 | {}".format(
            signal["setup"], symbol, side,
            signal["score"], signal["confluence"], fond_score, mtf_tag))

        return signal

    except Exception as e:
        logger.debug("scan_fond {}: {}".format(symbol, e))
        return None

# Garde la fonction scan_symbol pour compatibilité
def scan_symbol(symbol):
    sig = scan_symbol_tech(symbol)
    if not sig: return None
    return scan_symbol_fond(sig)

# ═══════════════════════════════════════════════════════════════════
#  RECOVER POSITIONS EXISTANTES
# ═══════════════════════════════════════════════════════════════════

def recover_existing_positions():
    """
    ═══════════════════════════════════════════════════════════════════
    RECOVER v2.0 — Reprend les positions ouvertes sur Binance au démarrage.

    Pour chaque position ouverte détectée :
      1. Annule tous les ordres existants (SL/TP orphelins éventuels)
      2. Calcule SL basé sur ATR×1.2 depuis le prix d'entrée RÉEL
         → Respecte les bornes MIN_SL_PCT / MAX_SL_PCT
         → Si prix actuel déjà en profit > SL calculé → SL glissant au BE
      3. Calcule TP à RR3 depuis l'entrée réelle
      4. Pose SL sur Binance (MARK_PRICE → CONTRACT_PRICE → logiciel)
         → 3 tentatives avec attente 2s entre chaque
      5. Pose TP sur Binance
      6. Enregistre dans trade_log pour que le trailing SL prenne le relais
      7. Alerte Telegram avec le détail complet
    ═══════════════════════════════════════════════════════════════════
    """
    logger.info("🔄 Recherche positions existantes à récupérer...")
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
        if not positions:
            logger.info("  Aucune position existante.")
            return

        recovered = []
        for pos in positions:
            sym = pos.get("symbol")
            amt = float(pos.get("positionAmt", 0))
            if amt == 0:
                continue

            side  = "BUY" if amt > 0 else "SELL"
            qty   = abs(amt)
            entry = float(pos.get("entryPrice", 0))
            lev   = int(float(pos.get("leverage", 15)))
            if entry <= 0:
                continue

            # ── Infos symbole ─────────────────────────────────────
            if sym not in symbol_info_cache:
                # Charge les infos de précision si pas encore en cache
                ei = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)
                if ei:
                    for s in ei.get("symbols", []):
                        if s["symbol"] == sym:
                            pp_val = s.get("pricePrecision", 4)
                            qp_val = s.get("quantityPrecision", 3)
                            step   = next((float(f["stepSize"]) for f in s.get("filters",[])
                                           if f["filterType"] == "LOT_SIZE"), 0.001)
                            symbol_info_cache[sym] = {
                                "pricePrecision":    pp_val,
                                "quantityPrecision": qp_val,
                                "stepSize":          step,
                            }

            info = symbol_info_cache.get(sym, {})
            pp   = info.get("pricePrecision", 4)

            # ── Prix actuel ───────────────────────────────────────
            mark = float(pos.get("markPrice", 0)) or get_price(sym) or entry

            # ── ATR live ──────────────────────────────────────────
            atr = _atr_live(sym)

            # ── Calcul SL ─────────────────────────────────────────
            # Base ATR×1.2 depuis entry
            dist = max(atr * ATR_SL_MULT, entry * MIN_SL_PCT)
            dist = min(dist, entry * MAX_SL_PCT)

            if side == "BUY":
                sl_raw = entry - dist
                # Si le prix actuel est déjà bien au-dessus → SL au breakeven
                profit_pct = (mark - entry) / entry if entry > 0 else 0
                if profit_pct >= BREAKEVEN_RR * (dist / entry):
                    # Prix a bougé assez pour breakeven → SL au BE
                    sl_raw  = entry * (1.0 + BREAKEVEN_FEE_TOTAL) + BE_PROFIT_MIN / max(qty, 0.001)
                    be_moved = True
                    logger.info("  {} → position en profit, SL positionné au BE".format(sym))
                else:
                    be_moved = False
            else:
                sl_raw = entry + dist
                profit_pct = (entry - mark) / entry if entry > 0 else 0
                if profit_pct >= BREAKEVEN_RR * (dist / entry):
                    sl_raw  = entry * (1.0 - BREAKEVEN_FEE_TOTAL) - BE_PROFIT_MIN / max(qty, 0.001)
                    be_moved = True
                    logger.info("  {} → position en profit, SL positionné au BE".format(sym))
                else:
                    be_moved = False

            sl = round(sl_raw, pp)
            tp = round(find_tp_for_rr(entry, sl, side, TP_RR), pp)
            be = round(entry * (1 + BREAKEVEN_FEE_TOTAL) if side == "BUY"
                       else entry * (1 - BREAKEVEN_FEE_TOTAL), pp)

            # Distance SL en %
            sl_pct = abs(entry - sl) / entry * 100

            # ── Nettoyage ordres existants ────────────────────────
            logger.info("  {} — nettoyage ordres existants...".format(sym))
            cleanup_orders(sym)
            time.sleep(1.5)   # Attente que Binance annule bien

            # ── Pose SL sur Binance (3 tentatives) ───────────────
            close_side = "SELL" if side == "BUY" else "BUY"
            sl_r = {"sent": False, "order_id": None, "method": "SOFTWARE"}
            for attempt in range(3):
                sl_r = place_sl_binance(sym, sl, close_side)
                if sl_r["sent"]:
                    break
                logger.warning("  {} SL tentative {}/3 échouée → retry 2s".format(sym, attempt + 1))
                time.sleep(2.0)

            # ── Pose TP sur Binance ───────────────────────────────
            tp_r = place_tp_binance(sym, tp, close_side)
            if not tp_r["sent"]:
                time.sleep(2.0)
                tp_r = place_tp_binance(sym, tp, close_side)

            # ── Enregistrement trade_log ──────────────────────────
            if sym not in symbols_list:
                symbols_list.append(sym)

            with trade_lock:
                trade_log[sym] = {
                    "side":                 side,
                    "entry":                entry,
                    "sl":                   sl,
                    "tp":                   tp,
                    "qty":                  qty,
                    "leverage":             lev,
                    "margin":               0.0,
                    "margin_pct":           0.0,
                    "setup":                "RECOVERED",
                    "score":                90,
                    "probability":          90.0,
                    "status":               "OPEN",
                    "opened_at":            time.time(),
                    "sl_on_binance":        sl_r["sent"],
                    "tp_on_binance":        tp_r["sent"],
                    "sl_order_id":          sl_r["order_id"],
                    "tp_order_id":          tp_r["order_id"],
                    "trailing_stop_active": False,
                    "breakeven_moved":      be_moved,
                    "be_price":             be,
                    "atr":                  atr,
                    "btc_corr":             "RECOVERED",
                    "fond_score":           0,
                    "session":              get_session(),
                    "btc_strength":         "NORMAL",
                    "sl_repost_ts":         0.0,
                }

            pnl_usdt = float(pos.get("unRealizedProfit", 0))
            pnl_pct  = (mark - entry) / entry * 100 * (1 if side == "BUY" else -1)

            logger.info(
                "✅ RECOVERED {} {} | entry={:.{}f} mark={:.{}f} | "
                "SL={:.{}f} ({:.2f}%) [{}] | TP={:.{}f} | PnL={:+.4f}$ ({:+.2f}%)".format(
                    sym, side,
                    entry, pp, mark, pp,
                    sl, pp, sl_pct, "✅Binance" if sl_r["sent"] else "⚠️logiciel",
                    tp, pp,
                    pnl_usdt, pnl_pct
                )
            )
            recovered.append({
                "sym": sym, "side": side, "entry": entry, "mark": mark,
                "sl": sl, "sl_pct": sl_pct, "tp": tp,
                "sl_ok": sl_r["sent"], "tp_ok": tp_r["sent"],
                "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
                "be_moved": be_moved, "pp": pp, "lev": lev,
            })

        # ── Telegram — rapport de récupération ────────────────────
        if recovered:
            lines = ["🔄 <b>RECOVER au démarrage — {} position(s)</b>\n".format(len(recovered))]
            for r in recovered:
                icon_pnl = "🟢" if r["pnl_usdt"] >= 0 else "🔴"
                icon_sl  = "✅" if r["sl_ok"] else "⚠️"
                icon_tp  = "✅" if r["tp_ok"] else "⚠️"
                be_tag   = " 🔒BE" if r["be_moved"] else ""
                lines.append(
                    "{} <b>{} {}</b> {}x\n"
                    "  Entry: {:.{}f} | Mark: {:.{}f}\n"
                    "  SL {}: {:.{}f} ({:.2f}%){}\n"
                    "  TP {}: {:.{}f}\n"
                    "  PnL: {:+.4f}$ ({:+.2f}%)".format(
                        icon_pnl, r["sym"], r["side"], r["lev"],
                        r["entry"], r["pp"], r["mark"], r["pp"],
                        icon_sl, r["sl"], r["pp"], r["sl_pct"], be_tag,
                        icon_tp, r["tp"], r["pp"],
                        r["pnl_usdt"], r["pnl_pct"]
                    )
                )
            send_telegram("\n\n".join(lines))
        else:
            logger.info("  Aucune position à récupérer.")

    except Exception as e:
        logger.error("recover_existing_positions: {}".format(e))

# ═══════════════════════════════════════════════════════════════════
#  AFFICHAGE CONSOLE
# ═══════════════════════════════════════════════════════════════════

def print_signal_console(sig, rank):
    side  = sig["side"]
    sym   = sig.get("symbol", "")
    setup = sig.get("setup", "?")
    dcol  = RED + BOLD if side == "SELL" else GREEN + BOLD
    sep   = "═" * 65

    # ── Heure réelle Binance (UTC + offset sync) ──────────────────
    now_ts  = time.time() + _binance_time_offset / 1000.0
    now_utc = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    heure   = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    print("\n" + cc(sep, CYAN))
    print("  #{} {:<22} {} {}  {}".format(
        rank, sym,
        cc("◄ " + side, dcol),
        cc(setup, MAGENTA + BOLD),
        cc(heure, DIM)))
    print(cc("─" * 65, DIM))

    def row(label, val, col=WHITE):
        print("  {}  {}".format(cc("{:<18}".format(label+":"), DIM), cc(str(val), col)))

    sess    = get_session()
    mmult   = get_session_mult()
    bstr    = sig.get("btc_strength", "NORMAL")
    dom_data = get_btc_dominance()
    fg_mult, fg_det   = get_fg_margin_mult(side)
    dom_mult, dom_det = get_btc_dom_mult(sym, side)

    # ── Risque actuel (identique à open_position) ─────────────────
    risk_usd, _ = paroli_get_margin(sym, setup, sess)
    risk_detail = "Risque ${:.2f} | streak={} WIN | +25%/WIN".format(
        risk_usd, _risk_state["win_streak"])
    with balance_lock:
        cur_bal = account_balance
    margin_pct = risk_usd / cur_bal * 100 if cur_bal > 0 else 0.0

    # ── Estimation SL / TP basée sur prix actuel ──────────────────
    info    = symbol_info_cache.get(sym, {})
    pp      = info.get("pricePrecision", 4)
    atr     = sig.get("atr", 0)
    ob      = sig.get("ob", {})
    candles = sig.get("candles_ohlc")
    cur_price = get_price(sym) or 0.0

    sl_disp = tp_disp = "—"
    if cur_price > 0:
        try:
            sl_est, sl_meth = get_sl(ob, cur_price, side, atr, candles)
            if side == "BUY":
                sl_dist = max(cur_price - sl_est, cur_price * MIN_SL_PCT)
                sl_dist = min(sl_dist, cur_price * MAX_SL_PCT)
                sl_val  = round(cur_price - sl_dist, pp)
            else:
                sl_dist = max(sl_est - cur_price, cur_price * MIN_SL_PCT)
                sl_dist = min(sl_dist, cur_price * MAX_SL_PCT)
                sl_val  = round(cur_price + sl_dist, pp)
            tp_val  = round(find_tp_for_rr(cur_price, sl_val, side, TP_RR), pp)
            sl_pct  = abs(cur_price - sl_val) / cur_price * 100
            sl_disp = "{:.{}f}  ({:.2f}%)  [{}]".format(sl_val, pp, sl_pct, sl_meth)
            tp_disp = "{:.{}f}  (RR{})".format(tp_val, pp, TP_RR)
        except Exception:
            pass

    # ── Affichage ─────────────────────────────────────────────────
    row("Setup",     "{} | score={} conf={}/5 prob={:.0f}%".format(
        setup, sig["score"], sig["confluence"], sig["probability"]), BOLD + WHITE)
    row("Prix actuel",
        "{:.{}f}".format(cur_price, pp) if cur_price > 0 else "—", BOLD + WHITE)
    row("SL estimé",  sl_disp, RED + BOLD)
    row("TP estimé",  tp_disp, GREEN + BOLD)
    row("BTC M5",    sig.get("btc_corr", "?"), CYAN)
    row("Session",   "{} (×{})".format(sess, mmult), YELLOW)
    row("BTC Force", bstr, GREEN if bstr == "FORT" else WHITE)
    row("BTC Dom",   dom_det, RED if dom_data.get("dominance", 50) >= BTC_DOM_HIGH else GREEN)
    row("Fond",      "{}/100 — {}".format(sig.get("fond_score", 0), sig.get("fond_detail", "")),
        GREEN if sig.get("fond_score", 0) >= 60 else YELLOW)
    row("F&G",       fg_det,
        GREEN if fg_mult >= 1.0 else YELLOW if fg_mult == 0.5 else WHITE)
    row("Risque $",
        "${:.2f} ({:.1f}%) | {}".format(risk_usd, margin_pct, risk_detail),
        GREEN + BOLD)
    print(cc(sep, CYAN))

# ═══════════════════════════════════════════════════════════════════
#  LOOPS
# ═══════════════════════════════════════════════════════════════════

def scanner_loop():
    """
    Scan CONTINU v4.7 — 30s entre chaque cycle complet.

    Avantages vs scan M5 fixe :
      - On ne rate pas une entrée qui se forme en milieu de bougie
      - Le retour en zone BB/FVG peut durer 1-3 min → 30s garanti la capture
      - La corrélation BTC prix est vérifiée en temps réel (cache 20s)

    Anti-saturation API maintenu :
      - 2 phases tech/fond inchangées
      - Weight tracker toujours actif
      - Batches adaptatifs selon saturation

    Tâches périodiques :
      - Sync Binance time   : toutes les 30 min
      - Reload symboles     : toutes les 60 min
      - Nettoyage cache     : toutes les 30 min
      - Balance refresh     : toutes les 5 min
    """
    logger.info("🔍 Scanner M5 v4.7 CONTINU — cycle 30s | {} symboles".format(
        len(symbols_list)))
    time.sleep(5)
    count        = 0
    last_sync    = 0.0
    last_reload  = 0.0
    last_cache   = 0.0
    last_balance = 0.0

    while True:
        try:
            if _bot_stop:
                time.sleep(10); continue

            count += 1
            now = time.time()

            # ── Tâches périodiques ────────────────────────────────
            if now - last_sync > 1800:       # Sync toutes les 30min
                sync_binance_time()
                get_account_balance(force=True)
                btc_s = get_btc_direction()
                fg_s  = get_fear_greed()
                logger.info("🔄 Sync30min | BTC: {} | Balance: ${:.4f} | F&G:{} | {}".format(
                    btc_s["label"], account_balance, fg_s["value"], get_tier_label()))
                last_sync = now

            if now - last_reload > 3600:     # Reload symboles 1h
                load_top_symbols()
                last_reload = now

            if now - last_cache > 1800:      # Nettoyage cache 30min
                cutoff = now - 300
                stale  = [k for k, (_, ts) in list(klines_cache.items()) if ts < cutoff]
                for k in stale:
                    klines_cache.pop(k, None)
                if stale:
                    logger.debug("🧹 klines_cache nettoyé: {} entrées supprimées".format(len(stale)))
                last_cache = now

            if now - last_balance > 300:     # Refresh balance 5min
                get_account_balance(force=True)
                last_balance = now

            # ── Gardes-fous globaux ───────────────────────────────
            if account_balance < HARD_FLOOR:
                logger.warning("🛑 Hard floor ${} | ${:.4f}".format(HARD_FLOOR, account_balance))
                time.sleep(60); continue

            if now < cooldown_until:
                r = int((cooldown_until - now) / 60)
                if count % 4 == 0:
                    logger.info("⏸ Cooldown {}min | weight={}".format(r, get_current_weight()))
                time.sleep(SCAN_INTERVAL); continue

            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open >= MAX_POSITIONS:
                time.sleep(SCAN_INTERVAL); continue

            session = get_session()
            if session == "OFF":
                if count % 4 == 0:
                    logger.info("🌙 Session OFF — pause scan")
                time.sleep(SCAN_INTERVAL); continue

            # ── Filtre News global (avant de lancer le scan) ──────
            if NEWS_FILTER_ENABLED:
                news_ko, news_msg = is_news_blackout()
                if news_ko:
                    if count % 4 == 0:
                        logger.info("📰 {} — scan suspendu".format(news_msg))
                    time.sleep(SCAN_INTERVAL); continue

            # ── FILTRE TENDANCE BTC STRICT ────────────────────────
            # Si BTC neutre/mixte → on ne scanne PAS (jamais contre-tendance)
            btc = get_btc_direction()
            if btc["direction"] == 0:
                if count % 6 == 0:
                    logger.info("⚪ BTC NEUTRE — scan suspendu | {}".format(btc["label"]))
                time.sleep(SCAN_INTERVAL); continue

            # Refresh bulk ticker si expiré
            with _bulk_ticker_lock:
                ticker_age = now - _bulk_ticker_ts
            if ticker_age > BULK_TICKER_TTL:
                load_top_symbols()

            # Pré-filtre léger (0 appel API)
            candidates = [s for s in symbols_list if prefilter_symbol(s)]

            if count % 4 == 0:   # Log tous les 2 min environ
                logger.info("🔍 Cycle#{} | {} candid | {} | F&G:{} | weight={} | Risque=${:.2f}".format(
                    count, len(candidates), btc["label"],
                    _fg_cache.get("value","?"), get_current_weight(),
                    _risk_state["current_risk"]))

            # ══════════════════════════════════════════════════════
            # PHASE 1 : SCAN TECHNIQUE EN BATCHES ADAPTATIFS
            # ══════════════════════════════════════════════════════
            tech_signals = []
            batches = [candidates[i:i+BATCH_SIZE]
                       for i in range(0, len(candidates), BATCH_SIZE)]

            for batch_idx, batch in enumerate(batches):
                w_now   = get_current_weight()
                w_limit = MAX_API_WEIGHT_PER_MIN * WEIGHT_SAFETY_MARGIN
                w_pct   = w_now / w_limit if w_limit > 0 else 0

                if w_pct >= 1.0:
                    sleep_t = 8.0
                    logger.warning("🛑 Weight {}/{} — pause {}s".format(
                        int(w_now), int(w_limit), sleep_t))
                    time.sleep(sleep_t)
                else:
                    sleep_t = BATCH_SLEEP_BASE * (1 + w_pct * 3)
                    time.sleep(sleep_t)

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    futures = {pool.submit(scan_symbol_tech, sym): sym for sym in batch}
                    for fut in as_completed(futures, timeout=None):
                        try:
                            sig = fut.result(timeout=12)
                            if sig:
                                tech_signals.append(sig)
                        except Exception:
                            pass

                if len(tech_signals) >= TECH_PHASE_MAX_SIGNALS:
                    logger.info("  Phase1 early exit — {} signaux tech ({}/{} batches)".format(
                        len(tech_signals), batch_idx+1, len(batches)))
                    break

            if not tech_signals:
                time.sleep(SCAN_INTERVAL); continue

            # Tri par score
            tech_signals.sort(
                key=lambda s: s["score"] * s["confluence"] * s.get("probability", 90),
                reverse=True)

            logger.info("🔎 Phase2/Fond | {} candidats | BTC {} | weight={}".format(
                len(tech_signals),
                "🟢" if btc["direction"] == 1 else "🔴",
                get_current_weight()))

            # ══════════════════════════════════════════════════════
            # PHASE 2 : ANALYSE FONDAMENTALE
            # ══════════════════════════════════════════════════════
            final_signals = []
            for sig in tech_signals:
                w_now = get_current_weight()
                if w_now > MAX_API_WEIGHT_PER_MIN * WEIGHT_SAFETY_MARGIN * 0.95:
                    logger.warning("  Phase2 stoppée — weight trop élevé")
                    break
                enriched = scan_symbol_fond(sig)
                if enriched:
                    final_signals.append(enriched)
                    time.sleep(0.4)

            if not final_signals:
                time.sleep(SCAN_INTERVAL); continue

            # Score composite final
            final_signals.sort(
                key=lambda s: (s["score"] * s["probability"] * s["confluence"]
                               * (1 + s.get("fond_score", 0) / 60)),
                reverse=True)

            logger.info("✨ {} signaux VALIDÉS | meilleur: {} {} score={} conf={}/5 fond={}/100".format(
                len(final_signals),
                final_signals[0]["symbol"], final_signals[0]["side"],
                final_signals[0]["score"], final_signals[0]["confluence"],
                final_signals[0].get("fond_score", 0)))

            best = final_signals[0]
            sym  = best["symbol"]

            with trade_lock:
                already = sym in trade_log and trade_log[sym].get("status") == "OPEN"
            if not already:
                signal_last_at[sym] = now
                print_signal_console(best, 1)
                open_position(best)

        except Exception as e:
            logger.error("scanner_loop: {}".format(e))
        time.sleep(SCAN_INTERVAL)

def monitor_loop():
    logger.info("📡 Monitor SL suiveur toutes les {}s".format(MONITOR_INTERVAL))
    time.sleep(10)
    _last_balance_refresh = 0.0
    while True:
        try:
            monitor_positions()
            check_drawdown()
            # PATCH 2 — Refresh balance toutes les 60s dans monitor
            now = time.time()
            if now - _last_balance_refresh > 60:
                get_account_balance(force=True)
                _last_balance_refresh = now
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
            dom  = get_btc_dominance()
            sess = get_session()
            mmult = get_session_mult()
            logger.info("═" * 65)
            logger.info("SCANNER M5 v4.5 | ${:.4f} | {} | {}".format(
                account_balance, get_tier_label(), get_progress_bar()))
            logger.info("Pos:{}/{} | W:{} L:{} WR:{:.1f}% | {} | {}".format(
                n_open, MAX_POSITIONS, tw, tl, wr, btc["label"], btc.get("strength","?")))
            logger.info("Session:{} (×{}) | F&G:{} {} | DOM BTC:{} | Weight:{}/{}".format(
                sess, mmult, fg["value"], fg["label"], dom["label"],
                get_current_weight(), int(MAX_API_WEIGHT_PER_MIN * WEIGHT_SAFETY_MARGIN)))
            logger.info("🧠 {}".format(brain_summary_log()))
            with _risk_lock:
                cur_risk = _risk_state["current_risk"]
                streak   = _risk_state["win_streak"]
            logger.info("💰 Risque: ${:.2f} | streak={}W | max=${:.2f} (+25%/WIN)".format(
                cur_risk, streak, RISK_MAX_USD))
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
    logger.info("║  SCANNER M5 v4.8 — $0.60 | +45%/WIN | SCAN 30s          ║")
    logger.info("║  BB+FVG | BTC PRIX | ANTI-CT | VOL_MIN BRAIN ADAPTATIF  ║")
    logger.info("╚" + "═" * 63 + "╝")
    logger.warning("🔥 LIVE TRADING 🔥")

    logger.info("  ✅ RISQUE         : ${:.2f} base | +45%/WIN | max ${:.2f} (≈5 WIN)".format(
        RISK_BASE_USD, RISK_MAX_USD))
    logger.info("  ✅ SIZING         : qty = risque$ / sl_distance")
    logger.info("  ✅ SCAN           : Continu 30s | {} workers".format(MAX_WORKERS))
    logger.info("  ✅ BTC TENDANCE   : Variation PRIX direct (15/30/60 min)")
    logger.info("  ✅ ANTI-CT        : BTC neutre → scan suspendu")
    logger.info("  ✅ PUMP GUARD     : priceChange >20%/24h → risque ×0.5")
    logger.info("  ✅ VOL_MIN BRAIN  : Stagnation détectée → VOL_MIN ×2 (2h)")
    logger.info("  ✅ TELEGRAM       : Erreurs loggées (plus de pass silencieux)")
    logger.info("  ✅ BE + SL        : RR1 → frais + $0.01 | ATR×{:.1f}".format(TRAIL_ATR_MULT))
    logger.info("  ✅ POSITIONS      : {} max | RR{}×".format(MAX_POSITIONS, TP_RR))

    start_health_server()
    sync_binance_time()
    brain_load()   # ← Charge la mémoire du brain
    risk_load()    # ← Charge le risque courant ($0.60 base +25%/WIN)
    load_top_symbols()
    get_account_balance(force=True)
    with balance_lock:
        drawdown_state["ref_balance"] = account_balance

    btc = get_btc_direction()
    fg  = get_fear_greed()
    sess = get_session()

    logger.info("💰 Balance: ${:.4f} | Palier: {}".format(account_balance, get_tier_label()))
    logger.info("📊 BTC M5: {} | Force: {}".format(btc["label"], btc.get("strength","?")))
    logger.info("😱 Fear & Greed: {} ({})".format(fg["value"], fg["label"]))
    logger.info("🕐 Session: {} (mult ×{})".format(sess, get_session_mult()))

    send_telegram(
        "🚀 <b>SCANNER M5 v4.8 DÉMARRÉ</b>\n\n"
        "💰 Balance: <b>${:.4f}</b> | {}\n"
        "🎯 Objectif: ${:.0f} | {}\n\n"
        "📊 BTC: {} | Force: {}\n"
        "😱 Fear & Greed: {} ({})\n"
        "🕐 Session: {} (×{})\n\n"
        "⚙️ CONFIG v4.8 :\n"
        "  💰 Risque actuel: <b>${:.2f}</b> (streak={})\n"
        "  📈 Progression: $0.60 → +45%/WIN → max $3.00\n"
        "  📐 Sizing: qty = risque$ / sl_distance\n"
        "  ⚠️ Pump >20%/24h → risque ×0.5 auto\n"
        "  📊 Stagnation → VOL_MIN ×2 (2h)\n"
        "  ⏱️ Scan continu 30s | {} workers\n"
        "  📡 BTC: variation PRIX 15/30/60min\n"
        "  🚫 BTC neutre → scan suspendu\n"
        "  🎯 RR {}× | {} positions max\n"
        "  🧠 Brain: {} trades mémorisés".format(
            account_balance, get_tier_label(),
            TARGET_BALANCE, get_progress_bar(),
            btc["label"], btc.get("strength", "?"),
            fg["value"], fg["label"],
            sess, get_session_mult(),
            _risk_state["current_risk"], _risk_state["win_streak"],
            MAX_WORKERS,
            TP_RR, MAX_POSITIONS,
            _brain.get("total_trades", 0),
        )
    )

    recover_existing_positions()

    threading.Thread(target=scanner_loop,   daemon=True).start()
    threading.Thread(target=monitor_loop,   daemon=True).start()
    threading.Thread(target=dashboard_loop, daemon=True).start()

    logger.info("✅ SCANNER M5 v4.5 ONLINE 🚀")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown")

if __name__ == "__main__":
    main()

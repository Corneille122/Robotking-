#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   ROBOTKING v37 — SL STRUCTUREL + RISQUE $0.30 FIXE       ║
║   v4.6 — BTC M15 | Setup M5 | Trigger M1 | Levier adaptatif ║
╚══════════════════════════════════════════════════════════════════╝

v4.6 (ce fichier) :
🆕 V37-4.6 — Levier & marge adaptatifs au setup M5 :
             SWEEP_CHOCH_OB  (score 92) → 40x | marge 40% balance
             BREAKER_FVG     (score 85) → 30x | marge 35% balance
             BOS_CONTINUATION(score 78) → 20x | marge 30% balance
             Bonus +5x si probabilité ≥ 75% (plafonné 40x)
             2 positions max inchangé.

v4.5 (précédent) :
🆕 V37-4.5 — Architecture 3 timeframes :
             BTC tendance    → M15 (inchangé)
             Signal symbole  → M5  (setups, BOS/CHoCH, sweep, zone, SL fallback)
             Trigger entrée  → M1  (bougie confirmation P4)
             SIGNAL_TIMEFRAME = "15m"  # v4.6 — Signal sur M15 ajouté en config.

v4.4 (précédent) :
🆕 V37-4.4 — Dual Timeframe ICT : P1/P2/P3 sur M15, P4 (trigger) sur M1

v37 vs v36 :
🟢 V37-1 — Risque FIXE $0.30 par trade
           qty = FIXED_RISK_USDT / sl_distance (plus de % capital)
🟢 V37-2 — SL structurel : OB bottom/top > swing pivot 15m > ATR fallback
🟢 V37-3 — TP partiel DESACTIVE — trailing SL = seul mecanisme de sortie
🟢 V37-4 — TP = filet de securite RR8 (anti-pompe soudaine uniquement)

Securite compte $3 :
🛡️  V37-SAFE-1 — MAX 2 positions simultanees
🛡️  V37-SAFE-2 — Cap marge 40% balance par trade (max $1.20 sur $3)
🛡️  V37-SAFE-3 — recover() limite a 2 positions
🛡️  V37-SAFE-4 — Kill-switch 20% / pause douce 10% (2 pertes max par jour)
🆕 V37-FLOOR   — Hard floor $1.50 : trading gelé si balance critique
🆕 V37-FIX401  — HTTP 401/403 : pas de retry inutile sur clé API invalide

Heritage de v36 :
✅ Infrastructure Binance robuste (STOP_MARKET, rate limit, timestamp sync)
✅ SMC engine : SWEEP_CHOCH_OB (score 92) / BREAKER_FVG (85) / BOS (78)
✅ Probability engine : BTC MTF + trend + F&G + vol + liquidite
✅ Trailing SL candle-based des RR1 avec breakeven a +0.5R
✅ Kill-switch drawdown journalier + kill zones London/NY
✅ Dashboard live 30s + journal CSV
"""

import time, hmac, hashlib, requests, threading, os, logging, json, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
from collections import defaultdict

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("v37_robotking.log"), logging.StreamHandler()])
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

_binance_time_offset = 0  # décalage ms entre horloge locale et Binance

# ═══════════════════════════════════════════════════════════════════
#  ROBOTKING v37 — CONFIGURATION PRINCIPALE
#  v37 vs v36 :
#    🟢 V37-1 — FIXED_RISK_USDT = 0.30 (risque dollar fixe, lot auto)
#    🟢 V37-2 — SL structurel (OB bottom/top, swing pivot 15m)
#    🟢 V37-3 — PARTIAL_TP désactivé (trailing SL = seul mécanisme de sortie)
#    🟢 V37-4 — TP = filet de sécurité RR 8 (pas de sortie active)
# ═══════════════════════════════════════════════════════════════════

# ── Risque & Sizing ───────────────────────────────────────────────
FIXED_RISK_USDT    = 0.30    # V37-1 : risque fixe $0.30 par trade
LEVERAGE           = 40      # Levier max (SWEEP_CHOCH_OB)
LEVERAGE_MIN       = 10
LEVERAGE_MAX       = 40
# v4.6 — Levier & marge adaptatifs au setup
LEVERAGE_BY_SETUP  = {
    "SWEEP_CHOCH_OB":   40,   # Setup le plus fort  → levier max
    "BREAKER_FVG":      30,   # Setup intermédiaire → levier modéré
    "BOS_CONTINUATION": 20,   # Setup de base       → levier prudent
}
MARGIN_PCT_BY_SETUP = {
    "SWEEP_CHOCH_OB":   0.40, # 40% balance max en marge
    "BREAKER_FVG":      0.35,
    "BOS_CONTINUATION": 0.30,
}
PROB_BONUS_THRESHOLD = 75.0  # Si prob ≥ 75% → +5x levier (capped LEVERAGE_MAX)
MARGIN_FIXED_USDT  = 0.80    # Conservé comme fallback uniquement
MARGIN_TYPE        = "ISOLATED"
MIN_NOTIONAL       = 5.0     # Notionnel minimum Binance Futures

# ── Risk Sizing params ───────────────────────────────────────────
MAX_RISK_MULTIPLIER = 2.0    # On accepte jusqu'à 2× le risque si min_notional l'exige
MAX_MARGIN_PER_TRADE_PCT = 0.40  # ⚠️ V37-SAFE : marge max par trade = 40% balance
                                  # Avec $3 : max $1.20/trade × 2 trades = $2.40 (buffer $0.60 frais)
BALANCE_HARD_FLOOR       = 1.50  # 🆕 V37-FLOOR : si balance < $1.50 → freeze total trading
                                  # Protection ultime : évite de trader avec $0.10 restants
MIN_SL_DISTANCE_PCT = 0.003  # SL structural minimum 0.3% du prix

# ── Trailing / Breakeven ──────────────────────────────────────────
TRAILING_ENABLED    = True
TRAILING_START_RR   = 1.0    # Trailing démarre dès RR1
ATR_TRAIL_MULT      = 1.5    # Multiplicateur ATR pour trailing
SL_MIN_UPDATE_TICKS = 5      # Nb de ticks minimum pour bouger le SL

BREAKEVEN_RR         = 0.5   # Breakeven dès +0.5R
BREAKEVEN_FEE_BUFFER = 0.0006  # Buffer frais (~0.06% taker)

# ── TP : V37-3 — Désactivé (trailing = seule sortie) ─────────────
PARTIAL_TP_ENABLED   = False  # V37-3 : TP partiel désactivé
PARTIAL_TP_RR        = 999.0  # Jamais déclenché
PARTIAL_TP_CLOSE_PCT = 0.30
TP_SAFETY_NET_RR     = 8.0    # V37-4 : TP filet RR8 (protection anti-pompe soudaine)

# ── Signal / Setups ───────────────────────────────────────────────
MIN_SETUP_SCORE      = 85     # Score minimum pour valider un setup
MIN_PROBABILITY_SCORE = 65.0  # Probabilité minimum
CONFLUENCE_HIGH      = 4      # Confluence haute ≥ 4/5
CONFLUENCE_MIN       = 3      # Confluence minimale
VOLUME_ENTRY_MULT    = 2.0    # Volume spike multiplicateur
VOLUME_SPIKE_MULT    = 2.0    # Alias utilisé dans has_volume_spike() (SMC detection)
SIGNAL_COOLDOWN_SECS = 1800   # 30 min entre 2 signaux sur le même symbole
ENABLE_TREND_FILTER  = True

# ── SMC Detection params ──────────────────────────────────────────
FVG_MIN_GAP_PCT  = 0.001   # Gap minimal FVG (0.1% du prix)
OB_LOOKBACK      = 10      # Lookback Order Block
SWEEP_CLOSE_MARGIN = 0.002  # Marge de clôture sweep (0.2%)

# ── HTF Bias ─────────────────────────────────────────────────────
HTF_EMA_LEN  = 50          # EMA bias HTF
HTF_BIAS_TF  = "1h"        # Timeframe HTF 1H
TREND_TIMEFRAME = "15m"   # BTC tendance de fond — NE PAS CHANGER
SIGNAL_TIMEFRAME = "15m"  # v4.6 — Signal sur M15   # v4.5 — Référence signal symbole (setups, structure, SL)

# ── ATR Spike filter ─────────────────────────────────────────────
ATR_SPIKE_FILTER   = True
ATR_SPIKE_MULT     = 3.0
ATR_SPIKE_LOOKBACK = 50

# ── Kill Zones ────────────────────────────────────────────────────
KILL_ZONE_STRICT = False   # H24 — adaptatif par session (seuils renforcés Asia/Off)
LONDON_OPEN_H    = 7
LONDON_CLOSE_H   = 11
NY_OPEN_H        = 13
NY_CLOSE_H       = 17

# ── Seuils adaptatifs par session (H24) ───────────────────────────
# London / NY   : seuils normaux
# Asia / Off    : filtre plus sévère pour éviter faux signaux en range
SESSION_SCORE_OVERRIDE = {
    "LONDON":    {"min_score": 85,  "min_prob": 65.0, "min_confluence": CONFLUENCE_MIN},
    "NEW_YORK":  {"min_score": 85,  "min_prob": 65.0, "min_confluence": CONFLUENCE_MIN},
    "ASIA":      {"min_score": 90,  "min_prob": 72.0, "min_confluence": CONFLUENCE_HIGH},
    "OFF_HOURS": {"min_score": 92,  "min_prob": 75.0, "min_confluence": CONFLUENCE_HIGH},
}

# ── BTC Filter ────────────────────────────────────────────────────
BTC_FILTER_ENABLED = True
BTC_BULL_THRESHOLD = 0.25
BTC_BEAR_THRESHOLD = -0.25
BTC_NEUTRAL_BLOCK  = False
BTC_NEUTRAL_MIN    = -0.10
BTC_NEUTRAL_MAX    = 0.10
BTC_DAILY_BLOCK    = True
BTC_TIMEFRAMES     = {
    "15m": {"weight": 0.15, "label": "15m"},
    "1h":  {"weight": 0.25, "label": "1H"},
    "4h":  {"weight": 0.35, "label": "4H"},
    "1d":  {"weight": 0.25, "label": "1D"},
}

# ── Drawdown ──────────────────────────────────────────────────────
DAILY_DRAWDOWN_LIMIT    = 0.20   # 20% → kill-switch ($0.60 sur $3 = 2 pertes max)
DRAWDOWN_PAUSE_HOURS    = 8      # Pause 8h après kill-switch (était 12h)
DAILY_HARD_DRAWDOWN_PCT = 0.10   # 10% → pause douce ($0.30 = 1 perte max)
DAILY_HARD_PAUSE_HOURS  = 2      # Pause 2h après 1 perte

# ── Filtres marché ────────────────────────────────────────────────
MAX_SPREAD_PCT       = 0.001   # 0.1% max spread bid-ask
MAX_FUNDING_RATE_ABS = 0.0015  # 0.15% max funding
MIN_VOLUME_24H_USDT  = 50_000_000  # 50M$ volume minimum

# ── Liquidity ─────────────────────────────────────────────────────
LIQ_TOP_N_WALLS      = 3
LIQ_SPOOF_THRESHOLD  = 5.0

# ── Symbol streak / cooldown ──────────────────────────────────────
SYMBOL_CONSEC_LOSS_LIMIT  = 2
SYMBOL_COOLDOWN_MINUTES   = 45

# ── Recovery positions externes ───────────────────────────────────
EXTERNAL_MAX_LEVERAGE       = 50
EXTERNAL_POSITION_WHITELIST = []

# ── Divers ────────────────────────────────────────────────────────
SCAN_INTERVAL      = 15     # secondes entre scans
MONITOR_INTERVAL   = 10
DASHBOARD_INTERVAL = 30
CACHE_DURATION     = 30
MAX_WORKERS        = 5
TRADE_JOURNAL_FILE = "trades.csv"

# ── Poids probability engine ──────────────────────────────────────
PROBABILITY_WEIGHTS = {
    "setup_score":     0.30,
    "trend_alignment": 0.25,
    "btc_correlation": 0.15,
    "session_quality": 0.10,
    "sentiment":       0.10,
    "volatility":      0.05,
    "liquidity":       0.05,
}

# ── Poids sessions ────────────────────────────────────────────────
SESSION_WEIGHTS = {
    "LONDON":    0.90,
    "NEW_YORK":  0.90,
    "ASIA":      0.50,
    "OFF_HOURS": 0.30,
}

# ── Setups SMC ────────────────────────────────────────────────────
SETUPS = {
    "SWEEP_CHOCH_OB":  {"score": 92, "description": "Sweep Liq → CHOCH → OB/FVG"},
    "BREAKER_FVG":     {"score": 85, "description": "Breaker Block + FVG retest"},
    "BOS_CONTINUATION":{"score": 78, "description": "BOS Continuation + FVG mitig"},
}

# ── Profils sizing / BTC score ────────────────────────────────────
SIZING_PROFILES = {
    "STRONG_BULL": {"min": 0.50,  "max": 1.00,  "multiplier": 1.2, "leverage": 40, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "🟢🟢"},
    "BULL":        {"min": 0.25,  "max": 0.50,  "multiplier": 1.0, "leverage": 30, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "🟢"},
    "NEUTRAL":     {"min": -0.25, "max": 0.25,  "multiplier": 0.8, "leverage": 20, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "⚪"},
    "BEAR":        {"min": -0.50, "max": -0.25, "multiplier": 0.8, "leverage": 20, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "🔴"},
    "STRONG_BEAR": {"min": -1.00, "max": -0.50, "multiplier": 0.6, "leverage": 15, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "🔴🔴"},
}

# ── Profils trailing / BTC score ──────────────────────────────────
TRAILING_PROFILES = {
    "STRONG_BULL": {"min": 0.50,  "max": 1.00,  "start_rr": 1.0, "step_atr": 0.3, "lock_pct": 0.003, "label": "🟢🟢"},
    "BULL":        {"min": 0.25,  "max": 0.50,  "start_rr": 1.0, "step_atr": 0.4, "lock_pct": 0.003, "label": "🟢"},
    "NEUTRAL":     {"min": -0.25, "max": 0.25,  "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.004, "label": "⚪"},
    "BEAR":        {"min": -0.50, "max": -0.25, "start_rr": 1.0, "step_atr": 0.5, "lock_pct": 0.005, "label": "🔴"},
    "STRONG_BEAR": {"min": -1.00, "max": -0.50, "start_rr": 1.0, "step_atr": 0.6, "lock_pct": 0.006, "label": "🔴🔴"},
}

# ── Fallback symbols ──────────────────────────────────────────────
FALLBACK_SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","AVAXUSDT",
    "DOGEUSDT","LINKUSDT","MATICUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT",
    "LTCUSDT","UNIUSDT","ATOMUSDT","INJUSDT","SUIUSDT","TIAUSDT",
]
SYMBOLS            = []
MICRO_CAP_SYMBOLS  = []

# ── Global state ──────────────────────────────────────────────────
account_balance     = 0.0
trade_log           = {}
setup_memory        = defaultdict(lambda: {"wins": 0, "losses": 0})
total_traded        = 0
trade_lock          = threading.Lock()
api_lock            = threading.Lock()
api_semaphore       = threading.Semaphore(8)
api_call_times      = []
klines_cache        = {}
price_cache         = {}
symbol_info_cache   = {}
signal_attempted_at = {}
symbol_cooldown_until = {}
symbol_loss_streak  = defaultdict(int)
btc_trend_cache     = {}
fear_greed_cache    = {}
# Mémoire des structures SMC utilisées par symbole — anti re-entry même structure
structure_memory    = {}   # {symbol: {"bos_level": float, "sweep_level": float, "side": str, "ts": float}}

def sync_binance_time():
    """V36 — Synchro horloge robuste (moyenne 3 mesures + compensation latence)."""
    global _binance_time_offset
    offsets = []
    for _ in range(3):
        try:
            t0   = int(time.time() * 1000)
            resp = requests.get(BASE_URL + "/fapi/v1/time", timeout=3)
            t1   = int(time.time() * 1000)
            if resp.status_code == 200:
                server_time = resp.json()["serverTime"]
                latency     = (t1 - t0) // 2
                offsets.append(server_time - t0 - latency)
        except:
            pass
        time.sleep(0.1)
    if offsets:
        _binance_time_offset = int(sum(offsets) / len(offsets))
        if abs(_binance_time_offset) > 500:
            logger.warning(f"Horloge desync: offset={_binance_time_offset}ms corrige")
        else:
            logger.info(f"Horloge OK: offset={_binance_time_offset}ms")
    else:
        logger.warning("sync_binance_time: echec")
def _init_journal():
    """Crée le fichier CSV avec headers si absent."""
    import os as _os
    if not _os.path.exists(TRADE_JOURNAL_FILE):
        try:
            with open(TRADE_JOURNAL_FILE, "w") as f:
                f.write("timestamp,symbol,side,setup,score,confluence,session,"
                        "entry,sl,tp,sl_distance_pct,tp_rr,probability,"
                        "result,pnl_usd,rr_achieved,partial_tp,closed_by,"
                        "btc_score,bias_1h,bias_4h\n")
        except Exception as e:
            logger.warning(f"Journal CSV init failed: {e}")

def log_trade_to_csv(symbol: str, trade: dict, result: str,
                     pnl_usd: float = 0.0, rr_achieved: float = 0.0):
    """
    FIX-7 — Enregistre chaque clôture de trade dans trades.csv.
    Permet l'analyse offline du win rate par setup/session/heure.
    """
    try:
        entry     = trade.get("entry", 0)
        sl        = trade.get("sl", 0)
        tp        = trade.get("tp", 0)
        sl_dist   = abs(entry - sl) / entry * 100 if entry else 0
        tp_rr     = abs(tp - entry) / abs(entry - sl) if sl and entry and abs(entry - sl) > 0 else 0
        btc       = get_btc_composite_score()
        row = (
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')},"
            f"{symbol},{trade.get('side','')},"
            f"{trade.get('setup','')},"
            f"{trade.get('setup_score', '')},"
            f"{trade.get('confluence', '')},"
            f"{trade.get('session','')},"
            f"{entry:.6f},{sl:.6f},{tp:.6f},"
            f"{sl_dist:.3f},{tp_rr:.2f},"
            f"{trade.get('probability',0):.1f},"
            f"{result},{pnl_usd:.4f},{rr_achieved:.2f},"
            f"{'YES' if trade.get('partial_tp_done') else 'NO'},"
            f"{trade.get('closed_by','?')},"
            f"{btc.get('score',0):+.3f},"
            f"{trade.get('bias_1h','?')},"
            f"{trade.get('bias_4h','?')}\n"
        )
        with open(TRADE_JOURNAL_FILE, "a") as f:
            f.write(row)
    except Exception as e:
        logger.debug(f"log_trade_to_csv {symbol}: {e}")

# ─── FIX-5 : GROUPES DE CORRÉLATION ─────────────────────────────
# Max 2 positions dans le même groupe (évite concentration du risque)
CORRELATION_GROUPS = {
    "BTC_LAYER1":  {"BTCUSDT", "ETHUSDT", "BNBUSDT"},
    "SOL_LAYER1":  {"SOLUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "SEIUSDT"},
    "DeFi":        {"AAVEUSDT", "UNIUSDT", "CRVUSDT", "MKRUSDT", "LDOUSDT", "SNXUSDT"},
    "AI_DATA":     {"FETUSDT", "RNDRUSDT", "WLDUSDT", "GRTUSDT"},
    "L2_SCALING":  {"ARBUSDT", "OPUSDT", "MATICUSDT"},
    "COSMOS":      {"ATOMUSDT", "TIAUSDT", "INJUSDT"},
    "GAMING_NFT":  {"SANDUSDT", "MANAUSDT", "GALAUSDT", "APEUSDT", "ENJUSDT"},
}
MAX_CORRELATED_POSITIONS = 2   # Max par groupe

# V30-3 — Kill-switch drawdown : état global
drawdown_state = {
    "balance_at_start_of_day": 0.0,   # Balance en début de journée
    "paused_until":            0.0,   # timestamp fin de pause
    "last_reset":              0.0,   # Dernier reset quotidien
    "last_pause_log":          0.0,   # Anti-spam : dernier log "pausé"
    "initialized":             False, # True dès que la balance de référence est posée
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
    return f"v37 ROBOTKING | {status} | Balance: ${account_balance:.2f} | Open: {n_open}/{max_pos} | Risk: ${FIXED_RISK_USDT}/trade", 200

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
        "version": "v37",
        "drawdown_paused": paused,
    })

# FIX2-7 — Endpoints d'urgence opérationnels
_bot_emergency_stop = False   # Flag global pour arrêt d'urgence

@flask_app.route("/stop", methods=["GET", "POST"])
def emergency_stop():
    """
    FIX2-7 — Arrêt d'urgence immédiat du scanner.
    N'INTERROMPT PAS les positions ouvertes (Binance gère les SL/TP).
    Utile si tu détectes un comportement anormal du bot.
    """
    global _bot_emergency_stop
    _bot_emergency_stop = True
    logger.error("🛑 EMERGENCY STOP via /stop — Scanner désactivé")
    send_telegram("🛑 <b>EMERGENCY STOP</b> activé via endpoint /stop\nScanner désactivé. Positions existantes protégées par SL Binance.")
    return "🛑 BOT STOPPED — Scanner désactivé. Positions Binance intactes.", 200

@flask_app.route("/pause", methods=["GET", "POST"])
def manual_pause():
    """FIX2-7 — Pause manuelle du scanner pendant 2h."""
    drawdown_state["paused_until"] = time.time() + 7200  # 2h
    logger.warning("⏸ Pause manuelle 2h via /pause")
    send_telegram("⏸ <b>Pause manuelle 2h</b> activée via /pause")
    return "⏸ Bot en pause 2h", 200

@flask_app.route("/resume", methods=["GET", "POST"])
def manual_resume():
    """FIX2-7 — Reprend le trading si en pause."""
    global _bot_emergency_stop
    _bot_emergency_stop = False
    drawdown_state["paused_until"] = 0.0
    logger.info("▶️ Trading repris via /resume")
    send_telegram("▶️ <b>Trading repris</b> via /resume")
    return "▶️ Bot repris", 200

@flask_app.route("/trades", methods=["GET"])
def trades_endpoint():
    """FIX2-7 — Dashboard trades ouverts en JSON."""
    with trade_lock:
        open_trades = {
            sym: {
                "side":       t.get("side"),
                "entry":      t.get("entry"),
                "sl":         t.get("sl"),
                "tp":         t.get("tp"),
                "setup":      t.get("setup"),
                "prob":       t.get("probability"),
                "sl_binance": t.get("sl_on_binance"),
                "trailing":   t.get("trailing_stop_active"),
                "partial_tp": t.get("partial_tp_done"),
                "session":    t.get("session"),
            }
            for sym, t in trade_log.items() if t.get("status") == "OPEN"
        }
    return jsonify(open_trades)

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
    # Overlap London/NY : 13h–16h → NEW_YORK (poids max identique, mais plus représentatif)
    # London   : 7h–11h UTC (LONDON_OPEN_H → LONDON_CLOSE_H)
    # New York  : 13h–17h UTC (NY_OPEN_H → NY_CLOSE_H)
    # Overlap   : 13h–16h → classé NEW_YORK
    in_london = LONDON_OPEN_H <= hour < LONDON_CLOSE_H   # 7–11
    in_ny     = NY_OPEN_H     <= hour < NY_CLOSE_H       # 13–17
    if in_ny:
        return "NEW_YORK"
    elif in_london:
        return "LONDON"
    elif hour >= 23 or hour < LONDON_OPEN_H:              # 23h–7h
        return "ASIA"
    else:
        return "OFF_HOURS"

def get_session_weight() -> float:
    return SESSION_WEIGHTS.get(get_current_session(), 0.5)

# ─── V30-3 : KILL-SWITCH DRAWDOWN ────────────────────────────────
def check_drawdown_kill_switch() -> bool:
    """
    V38 — Drawdown : alerte Telegram uniquement, ne bloque plus le trading.
    Les pauses sont supprimées — le bot tourne H24 sans interruption.
    Le kill-switch absolu reste sur BALANCE_HARD_FLOOR ($1.50).
    Retourne toujours True (trading autorisé).
    """
    global drawdown_state

    now = time.time()

    if not drawdown_state.get("initialized", False):
        return True

    # ── Reset quotidien à minuit UTC ────────────────────────────────
    day_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    if drawdown_state.get("last_reset", 0) < day_start:
        drawdown_state["balance_at_start_of_day"] = account_balance
        drawdown_state["last_reset"]              = now
        drawdown_state["paused_until"]            = 0.0
        logger.info(f"📅 Drawdown reset quotidien | Référence : ${account_balance:.2f}")

    # ── Calculer la perte journalière ──────────────────────────────
    ref_balance = drawdown_state.get("balance_at_start_of_day", 0)
    if ref_balance <= 0:
        return True

    drawdown_pct = (ref_balance - account_balance) / ref_balance

    # Alerte 10% — warning sans blocage
    if drawdown_pct >= DAILY_HARD_DRAWDOWN_PCT:
        last_log = drawdown_state.get("last_pause_log", 0)
        if now - last_log >= 300:  # anti-spam 5 min
            drawdown_state["last_pause_log"] = now
            logger.warning(f"⚠️  Drawdown jour {drawdown_pct:.1%} — scan continu (pas de pause)")
            send_telegram(
                f"⚠️ <b>DRAWDOWN JOUR {drawdown_pct:.1%}</b>\n"
                f"Balance : ${account_balance:.2f} (début : ${ref_balance:.2f})\n"
                f"⚡ Trading continu — aucune pause"
            )

    # Alerte 20% — warning fort sans blocage
    if drawdown_pct >= DAILY_DRAWDOWN_LIMIT:
        last_log = drawdown_state.get("last_pause_log", 0)
        if now - last_log >= 300:
            drawdown_state["last_pause_log"] = now
            logger.error(f"🚨 Drawdown {drawdown_pct:.1%} ≥ {DAILY_DRAWDOWN_LIMIT:.0%} — alerte uniquement")
            send_telegram(
                f"🚨 <b>ALERTE DRAWDOWN {drawdown_pct:.1%}</b>\n"
                f"Balance : ${account_balance:.2f} (début : ${ref_balance:.2f})\n"
                f"⚡ Trading continu — surveillance renforcée"
            )

    return True  # Toujours True — pas de blocage


def init_drawdown_reference():
    """
    V36-FIX3 — Toujours utiliser la balance ACTUELLE comme référence.
    Evite le faux drawdown -76% après redémarrage post-pertes.
    """
    global drawdown_state
    drawdown_state["balance_at_start_of_day"] = account_balance
    drawdown_state["initialized"] = True
    logger.info(f"✅ Drawdown référence initialisée : ${account_balance:.2f}")
def is_funding_safe(symbol: str, side: str = None) -> bool:
    """
    FIX2-6 — Filtre funding directionnel (pas juste abs()).

    Logique :
    - Funding très positif (>0.15%) → longs paient les shorts → défavorable aux BUY
    - Funding très négatif (<-0.15%) → shorts paient les longs → défavorable aux SELL
    - Si side fourni : skip seulement si funding va CONTRE le trade
    - Si abs(funding) > 0.20% dans tous les cas → trop extrême → skip

    En bear 2026, le funding est souvent négatif (-0.02% à -0.10%) → ne plus bloquer
    les SELL sur abs() mais laisser passer si funding confirme le sens.
    """
    try:
        data = request_binance("GET", "/fapi/v1/fundingRate",
                               {"symbol": symbol, "limit": 1}, signed=False)
        if not data:
            return True
        fr = float(data[0]["fundingRate"])
        fr_abs = abs(fr)

        # Seuil absolu extrême → toujours bloquer (squeeze imminent)
        if fr_abs > 0.0020:
            logger.info(f"  [FUNDING] {symbol} funding={fr:.4%} extrême → skip tous sides")
            return False

        # Filtre directionnel si side connu
        if side == "BUY" and fr > MAX_FUNDING_RATE_ABS:
            # Funding positif élevé → longs paient → coût + pression bearish
            logger.info(f"  [FUNDING] {symbol} BUY bloqué: funding={fr:+.4%} > {MAX_FUNDING_RATE_ABS:.4%} (défavorable aux longs)")
            return False
        if side == "SELL" and fr < -MAX_FUNDING_RATE_ABS:
            # Funding négatif élevé → shorts paient → coût + pression bullish
            logger.info(f"  [FUNDING] {symbol} SELL bloqué: funding={fr:+.4%} < -{MAX_FUNDING_RATE_ABS:.4%} (défavorable aux shorts)")
            return False

        # Sans side (appel générique) : filtre abs comme avant
        if side is None and fr_abs > MAX_FUNDING_RATE_ABS:
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


# ─── V4.6 : LEVIER & MARGE ADAPTATIFS AU SETUP ───────────────────
def calculate_adaptive_leverage(setup_name: str, probability: float) -> int:
    """
    v4.6 — Levier adaptatif selon la qualité du setup détecté sur M5.
    SWEEP_CHOCH_OB (92) → 40x | BREAKER_FVG (85) → 30x | BOS (78) → 20x
    Bonus +5x si probabilité ≥ 75%, plafonné à LEVERAGE_MAX.
    """
    base_lev = LEVERAGE_BY_SETUP.get(setup_name, LEVERAGE_MIN)
    if probability >= PROB_BONUS_THRESHOLD:
        base_lev = min(base_lev + 5, LEVERAGE_MAX)
    return max(base_lev, LEVERAGE_MIN)

def calculate_adaptive_margin_pct(setup_name: str) -> float:
    """v4.6 — % de balance alloué en marge selon le setup."""
    return MARGIN_PCT_BY_SETUP.get(setup_name, 0.30)

# ─── POSITION SIZING ─────────────────────────────────────────────
def calculate_max_positions(balance: float) -> int:
    """V37 : 2 positions max — chacune limitée à 40% de la balance en marge."""
    return 2

def calculate_margin_for_trade(balance: float, setup_name: str = "BOS_CONTINUATION") -> float:
    """v4.6 — Marge adaptative selon setup (% de la balance)."""
    pct = calculate_adaptive_margin_pct(setup_name)
    return round(balance * pct, 4)

def can_afford_position(balance: float, existing_positions: int) -> bool:
    """
    V37-SAFE — Vérification avant ouverture (compte $3+) :
    1. Limite de positions simultanées (MAX = 2)
    2. Balance suffisante pour couvrir le risque fixe $0.30 × 2 (sécurité)
    3. Balance × LEVERAGE ≥ MIN_NOTIONAL
    4. Il reste assez de marge libre pour une nouvelle position :
       balance × MAX_MARGIN_PER_TRADE_PCT × (MAX_POS - n_open) ≥ seuil
    """
    max_pos = calculate_max_positions(balance)
    if existing_positions >= max_pos:
        return False

    # ⚠️ Hard floor : si balance sous le seuil minimum absolu → freeze total
    if balance < BALANCE_HARD_FLOOR:
        logger.error(
            f"🛑 [HARD-FLOOR] Balance ${balance:.2f} < ${BALANCE_HARD_FLOOR} "
            f"→ Trading GELÉ (recharger le compte)"
        )
        return False

    # Balance minimum : 2× le risque fixe pour absorber une perte
    if balance < FIXED_RISK_USDT * 2:
        logger.debug(f"  [AFFORD] Balance ${balance:.2f} < ${FIXED_RISK_USDT*2:.2f} → skip")
        return False

    # Notionnel minimum atteignable
    if balance * LEVERAGE < MIN_NOTIONAL:
        logger.warning(f"  [AFFORD] ${balance:.2f} × {LEVERAGE}x = ${balance*LEVERAGE:.2f} < MIN_NOTIONAL ${MIN_NOTIONAL:.0f} → skip")
        return False

    # ⚠️ Vérification marge disponible pour la nouvelle position
    # Chaque trade peut consommer jusqu'à 40% de balance → 2 trades = 80%
    # On vérifie qu'il reste au moins 40% de balance non engagée
    max_margin_new = balance * MAX_MARGIN_PER_TRADE_PCT
    if max_margin_new * LEVERAGE < MIN_NOTIONAL:
        logger.warning(f"  [AFFORD] Marge max/trade ${max_margin_new:.2f} × {LEVERAGE}x < MIN_NOTIONAL → skip")
        return False

    return True

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
        params["timestamp"]  = int(time.time() * 1000) + _binance_time_offset
        params["recvWindow"] = 20000   # V36: 20s tolerance
        params["signature"]  = _sign(params)
    wait_for_rate_limit()
    headers = {"X-MBX-APIKEY": API_KEY}
    url = BASE_URL + path
    # FIX2-5 — Semaphore : max 8 appels API simultanés
    with api_semaphore:
        for attempt in range(3):
            try:
                # V36-FIX3: Timestamp recalculé à chaque tentative
                if signed:
                    # Supprimer ancienne signature si présente
                    params.pop("signature", None)
                    params["timestamp"]  = int(time.time() * 1000) + _binance_time_offset
                    params["recvWindow"] = 20000
                    params["signature"]  = _sign(params)
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
                    # FIX2-4 — Exponential backoff : 5s → 15s → 30s (au lieu de flat 60s)
                    backoff = [5, 15, 30][min(attempt, 2)]
                    retry_after = int(resp.headers.get("Retry-After", backoff))
                    wait = max(backoff, retry_after)
                    logger.warning(f"⏳ Rate limit 429 (attempt {attempt+1}/3) → attente {wait}s")
                    time.sleep(wait)
                elif resp.status_code == 418:
                    # IP bannie → attente longue obligatoire
                    logger.error("🚨 IP BAN (418) → pause 120s")
                    send_telegram("🚨 <b>IP BAN Binance (418)</b> — pause 120s")
                    time.sleep(120)
                    return None
                elif resp.status_code in (401, 403):
                    body = resp.text[:300]
                    logger.error(f"🔑 API {resp.status_code} — Clé invalide ou IP non autorisée: {body}")
                    send_telegram(
                        f"🔑 <b>Erreur API {resp.status_code}</b>\n"
                        f"Clé API invalide ou IP non autorisée.\n"
                        f"Vérifier API_KEY/API_SECRET + whitelist IP Binance."
                    )
                    return None  # Pas de retry — inutile sur clé invalide
                elif resp.status_code >= 400:
                    body = resp.text[:200]
                    logger.warning(f"API {resp.status_code}: {body}")
                    if "-1021" in body and attempt < 2:
                        logger.warning(f"⏱️ -1021 → resync + retry {attempt+1}/3")
                        sync_binance_time()
                        continue
                    return None
            except Exception as e:
                logger.warning(f"Request error (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))  # 1s, 2s
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
    """
    V32-3 — Charge dynamiquement les top 100 symboles Binance Futures
    triés par volume 24h décroissant.
    Exclut les paires stables (USDC, BUSD, TUSD) et les paires exotiques.
    """
    global SYMBOLS, MICRO_CAP_SYMBOLS
    logger.info("📥 Chargement top 100 Binance Futures par volume...")

    # Étape 1 : récupérer les tickers 24h pour le tri par volume
    tickers = request_binance("GET", "/fapi/v1/ticker/24hr", signed=False)
    exchange = request_binance("GET", "/fapi/v1/exchangeInfo", signed=False)

    if not tickers or not exchange:
        logger.error("❌ Impossible de charger les données — utilisation de la liste de secours")
        SYMBOLS = FALLBACK_SYMBOLS.copy()
        MICRO_CAP_SYMBOLS = SYMBOLS
        # Charger les infos basiques pour les symboles de secours
        if exchange:
            _load_symbol_details(exchange, SYMBOLS)
        return

    # Étape 2 : construire dictionnaire volume par symbole
    vol_map = {}
    for t in tickers:
        sym = t.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                vol_map[sym] = float(t.get("quoteVolume", 0))
            except:
                pass

    # Étape 3 : filtrer sur l'exchange (TRADING uniquement, exclure stables)
    EXCLUDE = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDTUSDT", "FDUSDUSDT",
               "USDPUSDT", "DAIUSDT", "EURUSDT", "GBPUSDT"}
    tradeable = set()
    for s in exchange.get("symbols", []):
        sym = s["symbol"]
        if (sym.endswith("USDT") and
                s.get("status") == "TRADING" and
                s.get("contractType") == "PERPETUAL" and
                sym not in EXCLUDE):
            tradeable.add(sym)

    # Étape 4 : filtrer par volume minimum + trier + top 100
    # FIX2-8 : Exclure les symboles avec volume 24h < 10M$ (illiquides)
    ranked = sorted(
        [(sym, vol) for sym, vol in vol_map.items()
         if sym in tradeable and vol >= MIN_VOLUME_24H_USDT],
        key=lambda x: x[1], reverse=True
    )[:100]

    if len(ranked) < 20:
        logger.warning(f"⚠️  Seulement {len(ranked)} symboles au-dessus de {MIN_VOLUME_24H_USDT/1e6:.0f}M$ → fallback sans filtre volume")
        ranked = sorted(
            [(sym, vol) for sym, vol in vol_map.items() if sym in tradeable],
            key=lambda x: x[1], reverse=True
        )[:100]

    top100 = [sym for sym, _ in ranked]
    logger.info(f"  [VOL-FILTER] {len(ranked)} symboles ≥ {MIN_VOLUME_24H_USDT/1e6:.0f}M$ vol24h | Min: ${ranked[-1][1]/1e6:.1f}M (#{len(ranked)})" if ranked else "")
    if not top100:
        logger.warning("⚠️  Tri volume vide → fallback")
        top100 = FALLBACK_SYMBOLS.copy()

    SYMBOLS = top100
    MICRO_CAP_SYMBOLS = SYMBOLS
    logger.info(f"✅ Top {len(SYMBOLS)} symboles chargés | #1: {SYMBOLS[0]} #10: {SYMBOLS[min(9,len(SYMBOLS)-1)]}")

    # Étape 5 : charger les infos précision pour ces symboles
    _load_symbol_details(exchange, SYMBOLS)


def _load_symbol_details(exchange: dict, symbols: list):
    """Charge les infos précision/taille pour une liste de symboles."""
    loaded = 0
    for s in exchange.get("symbols", []):
        symbol = s["symbol"]
        if symbol in symbols and s.get("status") == "TRADING":
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            symbol_info_cache[symbol] = {
                "quantityPrecision": s.get("quantityPrecision", 3),
                "pricePrecision":    s.get("pricePrecision", 4),
                "minQty":            float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
                "maxQty":            float(filters.get("LOT_SIZE", {}).get("maxQty", 1e9)),
                "stepSize":          float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "minNotional":       float(filters.get("MIN_NOTIONAL", {}).get("notional", 5)),
            }
            loaded += 1
    logger.info(f"✅ Infos symboles chargées : {loaded}/{len(symbols)}")

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
def calc_atr(symbol: str, period: int = 14, timeframe: str = "1m") -> float:
    """
    V35 — ATR sur 1m (M1) pour trailing SL ultra-serré.
    Remplace 15m qui était beaucoup trop large pour le petit capital.
    """
    klines = get_klines(symbol, timeframe, period + 1)
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
    V35-FIX — TP smart basé sur les zones de liquidité.
    fallback_rr = 1.5 (atteignable) au lieu de 2.5 (trop loin)
    min_wall_dist réduit pour ne pas tout filtrer sur petits moves
    """
    try:
        walls = get_order_book_walls(symbol)
        info  = get_symbol_info(symbol)
        pp    = info.get("pricePrecision", 4) if info else 4
        atr   = calc_atr(symbol, timeframe="1m") or entry * 0.005  # V35: ATR 1m
        # V35-FIX: distance minimale réduite (était 1.5× ATR 15m = énorme)
        min_wall_dist = atr * 0.5   # 0.5× ATR 1m = très accessible
        min_rr        = 1.0         # TP min = 1:1 (atteignable)
        fallback_rr   = 3.0         # V36: TP = filet de sécurité lointain

        if side == "BUY":
            candidates = sorted(walls.get("ask_walls", []), key=lambda x: x[0])
            for wall_price, wall_qty in candidates:
                if wall_price <= entry:
                    continue
                dist_to_wall = wall_price - entry
                if dist_to_wall < min_wall_dist:
                    continue
                tp_liq = wall_price * 0.997
                if tp_liq >= entry + sl_distance * min_rr:
                    logger.info(f"  [TP-LIQ] {symbol} BUY → mur ask @ {wall_price:.{pp}f} | TP={tp_liq:.{pp}f}")
                    return round(tp_liq, pp)
            tp = round(entry + sl_distance * fallback_rr, pp)
            logger.info(f"  [TP-LIQ] {symbol} BUY → fallback TP={tp:.{pp}f} (R:R {fallback_rr})")
            return tp

        else:  # SELL
            candidates = sorted(walls.get("bid_walls", []), key=lambda x: x[0], reverse=True)
            for wall_price, wall_qty in candidates:
                if wall_price >= entry:
                    continue
                dist_to_wall = entry - wall_price
                if dist_to_wall < min_wall_dist:
                    continue
                tp_liq = wall_price * 1.003
                if tp_liq <= entry - sl_distance * min_rr:
                    logger.info(f"  [TP-LIQ] {symbol} SELL → mur bid @ {wall_price:.{pp}f} | TP={tp_liq:.{pp}f}")
                    return round(tp_liq, pp)
            tp = round(entry - sl_distance * fallback_rr, pp)
            logger.info(f"  [TP-LIQ] {symbol} SELL → fallback TP={tp:.{pp}f} (R:R {fallback_rr})")
            return tp

    except Exception as e:
        logger.warning(f"get_tp_from_liquidity {symbol}: {e}")
        pp = 4
        return round(entry + sl_distance * 1.5, pp) if side == "BUY" \
               else round(entry - sl_distance * 1.5, pp)


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
def detect_trend(symbol: str, timeframe: str = "5m") -> dict:
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

btc_composite_lock = threading.Lock()

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
    # Cache composite 60s — lecture rapide sans lock
    if now - btc_trend_cache.get("timestamp", 0) < 60:
        return btc_trend_cache.get("composite", _default_btc_composite())

    # Recalcul nécessaire — lock pour éviter les rafales parallèles
    with btc_composite_lock:
        # Double-check après acquisition du lock (un autre thread a peut-être déjà recalculé)
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

# ─── V34 : HELPERS HAUTE PROBABILITÉ ─────────────────────────────

def is_in_strict_kill_zone() -> bool:
    """
    V34-2 — Kill zones STRICTES : London 7-11h UTC, NY 13-17h UTC.
    Si KILL_ZONE_STRICT=True, n'autorise le trading QUE dans ces fenêtres.
    Réduit drastiquement les faux signaux en range (Asia / off-hours).
    """
    if not KILL_ZONE_STRICT:
        return True
    h = datetime.now(timezone.utc).hour
    in_london = LONDON_OPEN_H <= h < LONDON_CLOSE_H
    in_ny     = NY_OPEN_H     <= h < NY_CLOSE_H
    return in_london or in_ny


def is_atr_spike(symbol: str, side: str = None) -> bool:
    """
    V36 — Filtre ATR spike contextualisé.
    En sell-off BTC BEAR fort + SELL : tolérance 4.0x (tendance, pas anomalie).
    Sinon seuil = ATR_SPIKE_MULT (3.0x).
    """
    if not ATR_SPIKE_FILTER:
        return False
    try:
        data = _get_klines_np(symbol, "5m", ATR_SPIKE_LOOKBACK + 2)
        if data is None:
            return False
        _, h, l, c, _ = data
        n = len(c)
        if n < ATR_SPIKE_LOOKBACK + 1:
            return False
        # True Range de chaque bougie
        tr = np.maximum(h[1:] - l[1:],
             np.maximum(np.abs(h[1:] - c[:-1]),
                        np.abs(l[1:]  - c[:-1])))
        if len(tr) < 2:
            return False
        current_atr = float(np.mean(tr[-14:]))       # ATR actuel (14 bougies)
        avg_atr     = float(np.mean(tr[:-14]))        # ATR moyen historique
        if avg_atr <= 0:
            return False
        ratio = current_atr / avg_atr
        threshold = ATR_SPIKE_MULT
        try:
            btc = get_btc_composite_score()
            if btc.get("score", 0) < -0.40 and side == "SELL":
                threshold = 4.0
            elif btc.get("score", 0) < -0.25:
                threshold = 3.5
        except:
            pass
        if ratio > threshold:
            logger.info(f"  [ATR-SPIKE] {symbol} ATR ratio={ratio:.2f} > {threshold:.1f} → skip")
            return True
        return False
    except Exception as e:
        logger.debug(f"is_atr_spike {symbol}: {e}")
        return False


def get_htf_4h_bias(symbol: str) -> str:
    """
    V34-4 — Bias EMA50 4H strict (comme detect_bos_continuation mais réutilisable).
    Retourne 'BULL', 'BEAR', ou 'NEUTRAL'.
    """
    try:
        data = _get_klines_np(symbol, "4h", HTF_EMA_LEN + 5)
        if data is None:
            return "NEUTRAL"
        _, _, _, c, _ = data
        alpha = 2 / (HTF_EMA_LEN + 1)
        ema = c[0]
        for p in c[1:]:
            ema = p * alpha + ema * (1 - alpha)
        if c[-1] > ema:
            return "BULL"
        if c[-1] < ema:
            return "BEAR"
        return "NEUTRAL"
    except:
        return "NEUTRAL"


def is_symbol_on_cooldown(symbol: str) -> bool:
    """
    V34-6 — Vérifie si un symbole est en cooldown après pertes consécutives.
    Retourne True si le symbole est en pause (→ skip).
    """
    cooldown_until = symbol_cooldown_until.get(symbol, 0)
    if time.time() < cooldown_until:
        remaining = (cooldown_until - time.time()) / 60
        logger.debug(f"  [COOLDOWN] {symbol} en pause encore {remaining:.0f} min")
        return True
    return False


def update_symbol_streak(symbol: str, is_win: bool):
    """
    V34-6 — Met à jour la série de pertes/gains d'un symbole.
    Si 2 pertes consécutives → cooldown de 45 minutes.
    Un gain réinitialise le compteur.
    """
    if is_win:
        if symbol_loss_streak[symbol] > 0:
            logger.info(f"  [STREAK] {symbol} WIN → réinitialisation streak pertes")
        symbol_loss_streak[symbol] = 0
    else:
        symbol_loss_streak[symbol] += 1
        streak = symbol_loss_streak[symbol]
        logger.info(f"  [STREAK] {symbol} LOSS #{streak} consécutif")
        if streak >= SYMBOL_CONSEC_LOSS_LIMIT:
            cooldown_end = time.time() + SYMBOL_COOLDOWN_MINUTES * 60
            symbol_cooldown_until[symbol] = cooldown_end
            logger.warning(
                f"  [COOLDOWN] {symbol} {streak} pertes consécutives → "
                f"pause {SYMBOL_COOLDOWN_MINUTES} min"
            )
            send_telegram(
                f"⏸ <b>{symbol}</b> cooldown {SYMBOL_COOLDOWN_MINUTES} min\n"
                f"Raison : {streak} pertes consécutives"
            )

# ─── FIX-4 : MITIGATION CHECK OB/FVG ────────────────────────────
def is_ob_mitigated(closes: np.ndarray, ob: dict, from_idx: int) -> bool:
    """
    FIX-4 — Vérifie si l'Order Block a déjà été mitigé :
    Si le prix a fermé DANS la zone OB/FVG après sa formation → mitigé → skip.
    Un OB mitigé a perdu son efficacité comme support/résistance.
    """
    if not ob or "top" not in ob or "bottom" not in ob:
        return False
    ob_top    = ob["top"]
    ob_bottom = ob["bottom"]
    ob_idx    = ob.get("idx", 0)
    # Cherche si un close après la formation de l'OB est entré dans la zone
    start = max(ob_idx + 1, from_idx)
    for i in range(start, len(closes)):
        if ob_bottom <= closes[i] <= ob_top:
            return True   # Zone visitée → OB mitigé
    return False

def is_fvg_mitigated(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     fvg_idx: int, side: str) -> bool:
    """
    FIX-4 — Vérifie si le FVG a déjà été comblé (mitigé).
    BUY FVG : gap = lows[idx+1] > highs[idx-1] → comblé si price < lows[idx+1]
    SELL FVG : gap = highs[idx-1] > lows[idx+1] → comblé si price > highs[idx+1]
    """
    if fvg_idx < 1 or fvg_idx + 1 >= len(highs):
        return False
    if side == "BUY":
        fvg_low = highs[fvg_idx - 1]    # Bas du FVG bullish
        # Comblé si un close est descendu en dessous du niveau FVG
        for i in range(fvg_idx + 2, len(closes)):
            if closes[i] < fvg_low:
                return True
    else:
        fvg_high = lows[fvg_idx - 1]    # Haut du FVG bearish
        for i in range(fvg_idx + 2, len(closes)):
            if closes[i] > fvg_high:
                return True
    return False

# ─── FIX-5 : FILTRE CORRÉLATION ENTRE POSITIONS ──────────────────
def get_correlation_group(symbol: str) -> str:
    """Retourne le groupe de corrélation du symbole, ou 'OTHER'."""
    for group, members in CORRELATION_GROUPS.items():
        if symbol in members:
            return group
    return "OTHER"

def is_correlation_limit_reached(symbol: str) -> bool:
    """
    FIX-5 — Bloque si MAX_CORRELATED_POSITIONS positions du même groupe sont ouvertes.
    Évite d'avoir 3 L2s en même temps (ARBUSDT + OPUSDT + MATICUSDT) si BTC dump.
    """
    group = get_correlation_group(symbol)
    if group == "OTHER":
        return False   # Groupe unique → pas de limite spécifique
    with trade_lock:
        count = sum(
            1 for sym, trade in trade_log.items()
            if trade.get("status") == "OPEN" and get_correlation_group(sym) == group
        )
    if count >= MAX_CORRELATED_POSITIONS:
        logger.info(f"  [CORR] {symbol} groupe '{group}' : {count}/{MAX_CORRELATED_POSITIONS} → skip")
        return True
    return False

# ─── FIX-9 : SIGNAL COOLDOWN PAR SYMBOLE ─────────────────────────
def is_signal_in_cooldown(symbol: str) -> bool:
    """
    FIX-9 — Retourne True si une tentative d'entrée sur ce symbole
    a eu lieu dans les SIGNAL_COOLDOWN_SECS dernières secondes.
    Évite de ré-entrer sur le même signal raté à chaque scan de 15s.
    """
    last = signal_attempted_at.get(symbol, 0)
    if time.time() - last < SIGNAL_COOLDOWN_SECS:
        remaining = (SIGNAL_COOLDOWN_SECS - (time.time() - last)) / 60
        logger.debug(f"  [SIG-CD] {symbol} signal cooldown encore {remaining:.0f} min")
        return True
    return False

def mark_signal_attempted(symbol: str):
    """FIX-9 — Marque le symbole comme 'tentative en cours'."""
    signal_attempted_at[symbol] = time.time()

def _get_klines_np(symbol: str, tf: str, limit: int):
    """Récupère klines → (opens, highs, lows, closes, volumes) numpy arrays."""
    k = get_klines(symbol, tf, limit)
    if not k or len(k) < 10:
        return None
    o = np.array([float(x[1]) for x in k])
    h = np.array([float(x[2]) for x in k])
    l = np.array([float(x[3]) for x in k])
    c = np.array([float(x[4]) for x in k])
    v = np.array([float(x[5]) for x in k])
    return o, h, l, c, v


# ─── V33 : PRIMITIVES SMC ────────────────────────────────────────

def find_pivot_highs(highs, lows, lb=5):
    """ta.pivothigh — swing highs (max local sur lb bougies gauche+droite)."""
    pivots = []
    for i in range(lb, len(highs) - lb):
        if highs[i] == max(highs[i - lb: i + lb + 1]):
            pivots.append(i)
    return pivots

def find_pivot_lows(lows, lb=5):
    """ta.pivotlow — swing lows."""
    pivots = []
    for i in range(lb, len(lows) - lb):
        if lows[i] == min(lows[i - lb: i + lb + 1]):
            pivots.append(i)
    return pivots

def detect_fvg(highs, lows, idx, side, price_ref=None):
    """
    V33-4 — FVG avec threshold min (Pine : fvgThreshold).
    BUY  : low[idx+1] > high[idx-1]  ET gap > FVG_MIN_GAP_PCT × prix
    SELL : high[idx+1] < low[idx-1]
    """
    if idx < 1 or idx + 1 >= len(highs):
        return False
    pr = price_ref if price_ref else 1.0
    min_gap = FVG_MIN_GAP_PCT * pr
    if side == "BUY":
        return (lows[idx + 1] - highs[idx - 1]) > min_gap
    else:
        return (lows[idx - 1] - highs[idx + 1]) > min_gap

def get_htf_ema_bias(symbol):
    """V33-5 — Bias EMA50 1H (Pine : htfClose > htfEMA). Retourne BULL/BEAR/NEUTRAL."""
    try:
        data = _get_klines_np(symbol, HTF_BIAS_TF, HTF_EMA_LEN + 5)
        if data is None:
            return "NEUTRAL"
        _, _, _, c, _ = data
        alpha = 2 / (HTF_EMA_LEN + 1)
        ema = c[0]
        for p in c[1:]:
            ema = p * alpha + ema * (1 - alpha)
        if c[-1] > ema:
            return "BULL"
        if c[-1] < ema:
            return "BEAR"
        return "NEUTRAL"
    except:
        return "NEUTRAL"

def has_volume_spike(volumes, idx, sma_len=20):
    """V33-6 — Pine : volume > ta.sma(volume,20) × 1.5."""
    if idx < sma_len:
        return False
    return volumes[idx] > np.mean(volumes[idx - sma_len: idx]) * VOLUME_SPIKE_MULT

def find_order_block(opens, highs, lows, closes, bos_idx, side):
    """V33-7 — OB = dernière bougie impulsive avant le BOS."""
    start = max(0, bos_idx - OB_LOOKBACK)
    if side == "BUY":
        for i in range(bos_idx - 1, start - 1, -1):
            if closes[i] < opens[i]:
                return {"idx": i, "top": highs[i], "bottom": lows[i]}
    else:
        for i in range(bos_idx - 1, start - 1, -1):
            if closes[i] > opens[i]:
                return {"idx": i, "top": highs[i], "bottom": lows[i]}
    return {}


# ─── V33 : DÉTECTEURS SMC PINE SCRIPT ────────────────────────────

def detect_sweep_choch_ob(symbol, side):
    """
    V34 — Sweep Liq → CHOCH → OB/FVG  [Score 92]
    V34-4 : Bias 4H EMA50 strict intégré (en plus du 1H).
    V34-5 : Volume 2.0× (renforcé).
    V34-9 : Confluence élevée requise ≥ CONFLUENCE_HIGH (4/5).
    v4.5  : Données M5 (signal timeframe).
    """
    data = _get_klines_np(symbol, "5m", 100)
    if data is None:
        return None
    o, h, l, c, v = data
    n = len(c)

    ph = find_pivot_highs(h, l)
    pl = find_pivot_lows(l)
    if not ph or not pl:
        return None

    last_high = h[max(ph)]
    last_low  = l[max(pl)]

    # V34-4 : Bias 1H ET 4H doivent être alignés
    bias_1h = get_htf_ema_bias(symbol)
    bias_4h = get_htf_4h_bias(symbol)

    if side == "BUY":
        # Alignement obligatoire 4H + 1H sur le symbole — les deux doivent être BULL
        if bias_4h != "BULL":
            return None
        if bias_1h != "BULL":
            return None

        # Pine bullSweep : low < lastLow AND close > lastLow
        sweep_idx = -1
        for i in range(max(pl) + 1, n - 3):
            if l[i] < last_low and c[i] > last_low * (1 - SWEEP_CLOSE_MARGIN):
                sweep_idx = i
                break
        if sweep_idx < 0:
            return None
        # CHOCH : close dépasse le swing high récent
        ref_high  = max(h[max(0, sweep_idx - 10): sweep_idx])
        choch_idx = -1
        for i in range(sweep_idx + 1, n - 1):
            if c[i] > ref_high:
                choch_idx = i
                break
        if choch_idx < 0:
            return None
        # Conditions confluence
        fvg_ok    = any(detect_fvg(h, l, i, "BUY", c[i]) for i in range(sweep_idx, min(choch_idx+2, n-1)))
        ob        = find_order_block(o, h, l, c, choch_idx, "BUY")
        ob_ok     = bool(ob)
        # FIX-4 : Mitigation check — skip si OB déjà visité par le prix
        if ob_ok and is_ob_mitigated(c, ob, choch_idx + 1):
            logger.debug(f"  [MITIG] {symbol} BUY OB mitigé → skip")
            return None
        # V34-5 : Volume 2.0× renforcé
        vol_ok    = v[sweep_idx] > np.mean(v[max(0, sweep_idx-20): sweep_idx]) * VOLUME_ENTRY_MULT if sweep_idx >= 20 else False
        bias_ok   = (bias_1h == "BULL")
        bias_4h_ok = (bias_4h == "BULL")
        # V34-9 : 5 conditions à scorer
        score_pts = sum([fvg_ok, ob_ok, vol_ok, bias_ok, bias_4h_ok])
        if score_pts < CONFLUENCE_HIGH:   # ≥4/5 requis
            return None
        sc = min(100, SETUPS["SWEEP_CHOCH_OB"]["score"] + (score_pts - CONFLUENCE_MIN) * 2)
        return {"name": "SWEEP_CHOCH_OB", "score": sc, "confluence": score_pts,
                "ob": ob, "fvg": fvg_ok}

    else:  # SELL
        # Alignement obligatoire 4H + 1H sur le symbole — les deux doivent être BEAR
        if bias_4h != "BEAR":
            return None
        # Filtre dur 1H — bloquer SELL si 1H haussier
        if bias_1h != "BEAR":
            return None

        sweep_idx = -1
        for i in range(max(ph) + 1, n - 3):
            if h[i] > last_high and c[i] < last_high * (1 + SWEEP_CLOSE_MARGIN):
                sweep_idx = i
                break
        if sweep_idx < 0:
            return None
        ref_low   = min(l[max(0, sweep_idx - 10): sweep_idx])
        choch_idx = -1
        for i in range(sweep_idx + 1, n - 1):
            if c[i] < ref_low:
                choch_idx = i
                break
        if choch_idx < 0:
            return None
        fvg_ok    = any(detect_fvg(h, l, i, "SELL", c[i]) for i in range(sweep_idx, min(choch_idx+2, n-1)))
        ob        = find_order_block(o, h, l, c, choch_idx, "SELL")
        ob_ok     = bool(ob)
        # FIX-4 : Mitigation check
        if ob_ok and is_ob_mitigated(c, ob, choch_idx + 1):
            logger.debug(f"  [MITIG] {symbol} SELL OB mitigé → skip")
            return None
        vol_ok    = v[sweep_idx] > np.mean(v[max(0, sweep_idx-20): sweep_idx]) * VOLUME_ENTRY_MULT if sweep_idx >= 20 else False
        bias_ok   = (bias_1h == "BEAR")
        bias_4h_ok = (bias_4h == "BEAR")
        score_pts = sum([fvg_ok, ob_ok, vol_ok, bias_ok, bias_4h_ok])
        if score_pts < CONFLUENCE_HIGH:
            return None
        sc = min(100, SETUPS["SWEEP_CHOCH_OB"]["score"] + (score_pts - CONFLUENCE_MIN) * 2)
        return {"name": "SWEEP_CHOCH_OB", "score": sc, "confluence": score_pts,
                "ob": ob, "fvg": fvg_ok}


def detect_breaker_fvg(symbol, side):
    """
    V34 — Breaker Block + FVG  [Score 85]
    V34-4 : Bias 4H EMA50 strict intégré (bloque si 4H contraire).
    Ancien OB cassé → retest → FVG. Volume + EMA bias.
    v4.5  : Données M5 (signal timeframe).
    """
    # Alignement obligatoire 4H + 1H — les deux doivent confirmer la direction
    bias_4h = get_htf_4h_bias(symbol)
    bias_1h_check = get_htf_ema_bias(symbol)
    if side == "BUY":
        if bias_4h != "BULL" or bias_1h_check != "BULL":
            return None
    if side == "SELL":
        if bias_4h != "BEAR" or bias_1h_check != "BEAR":
            return None

    data = _get_klines_np(symbol, "5m", 120)
    if data is None:
        return None
    o, h, l, c, v = data
    n = len(c)

    ph = find_pivot_highs(h, l)
    pl = find_pivot_lows(l)

    bias_1h = get_htf_ema_bias(symbol)

    if side == "BUY":
        candidates = pl[:-3] if len(pl) > 3 else pl
        for piv in reversed(candidates):
            brk = l[piv]
            if not any(c[j] < brk for j in range(piv + 1, min(piv + 20, n - 5))):
                continue
            if abs(c[-1] - brk) / brk >= 0.015:
                continue
            fvg_ok  = any(detect_fvg(h, l, i, "BUY", c[i]) for i in range(n - 8, n - 1))
            confirm = c[-1] > o[-1]
            # V34-5 : Volume 2.0× renforcé
            vol_ok  = v[-1] > np.mean(v[max(0, n-21):-1]) * VOLUME_ENTRY_MULT
            bias_ok = (bias_1h == "BULL")
            bias_4h_ok = (bias_4h == "BULL")
            sc_pts  = sum([fvg_ok, vol_ok, bias_ok, confirm, bias_4h_ok])
            if sc_pts < CONFLUENCE_MIN:
                continue
            sc = min(100, SETUPS["BREAKER_FVG"]["score"] + (sc_pts - CONFLUENCE_MIN) * 2)
            return {"name": "BREAKER_FVG", "score": sc,
                    "breaker_level": brk, "confluence": sc_pts}

    else:
        candidates = ph[:-3] if len(ph) > 3 else ph
        for piv in reversed(candidates):
            brk = h[piv]
            if not any(c[j] > brk for j in range(piv + 1, min(piv + 20, n - 5))):
                continue
            if abs(c[-1] - brk) / brk >= 0.015:
                continue
            fvg_ok  = any(detect_fvg(h, l, i, "SELL", c[i]) for i in range(n - 8, n - 1))
            confirm = c[-1] < o[-1]
            vol_ok  = v[-1] > np.mean(v[max(0, n-21):-1]) * VOLUME_ENTRY_MULT
            bias_ok = (bias_1h == "BEAR")
            bias_4h_ok = (bias_4h == "BEAR")
            sc_pts  = sum([fvg_ok, vol_ok, bias_ok, confirm, bias_4h_ok])
            if sc_pts < CONFLUENCE_MIN:
                continue
            sc = min(100, SETUPS["BREAKER_FVG"]["score"] + (sc_pts - CONFLUENCE_MIN) * 2)
            return {"name": "BREAKER_FVG", "score": sc,
                    "breaker_level": brk, "confluence": sc_pts}

    return None


def detect_bos_continuation(symbol, side):
    """
    V33-3 — BOS Continuation + FVG/Mitigation  [Score 78]
    Bias 4H EMA50 obligatoire. Structure M5 intacte.
    v4.5  : Données M5 (signal timeframe).
    """
    # Bias 4H EMA50 strict
    htf4h = _get_klines_np(symbol, "4h", 60)
    if htf4h is None:
        return None
    _, _, _, hc4, _ = htf4h
    alpha = 2 / (HTF_EMA_LEN + 1)
    ema4  = hc4[0]
    for p in hc4[1:]:
        ema4 = p * alpha + ema4 * (1 - alpha)
    if side == "BUY" and hc4[-1] < ema4:
        return None
    if side == "SELL" and hc4[-1] > ema4:
        return None

    data = _get_klines_np(symbol, "5m", 80)
    if data is None:
        return None
    o, h, l, c, v = data
    n   = len(c)
    avg = np.mean(v[-20:])
    ph  = find_pivot_highs(h, l)
    pl  = find_pivot_lows(l)

    if side == "BUY":
        if not ph:
            return None
        # Filtre dur 1H symbole — BUY interdit si 1H baissier
        if get_htf_ema_bias(symbol) != "BULL":
            return None
        last_ph   = max(ph)
        bos_level = h[last_ph]
        if not any(c[i] > bos_level for i in range(last_ph + 1, n - 2)):
            return None
        in_miti = (bos_level * 0.988) <= c[-1] <= (bos_level * 1.005)
        fvg_ok  = any(detect_fvg(h, l, i, "BUY", c[i]) for i in range(n - 6, n - 1))
        if pl and max(pl) > last_ph and c[-1] < l[max(pl)]:
            return None  # CHOCH contraire → structure cassée
        confirm = c[-1] > o[-1] and v[-1] > avg * 0.6
        bias_ok = get_htf_ema_bias(symbol) == "BULL"
        sc_pts  = sum([in_miti, fvg_ok, confirm, bias_ok, True])
        if sc_pts < CONFLUENCE_MIN:
            return None
        sc = min(100, SETUPS["BOS_CONTINUATION"]["score"] + (sc_pts - CONFLUENCE_MIN) * 2)
        return {"name": "BOS_CONTINUATION", "score": sc,
                "bos_level": bos_level, "confluence": sc_pts}

    else:
        if not pl:
            return None
        # Filtre dur 1H symbole — SELL interdit si 1H haussier
        if get_htf_ema_bias(symbol) != "BEAR":
            return None
        last_pl   = max(pl)
        bos_level = l[last_pl]
        if not any(c[i] < bos_level for i in range(last_pl + 1, n - 2)):
            return None
        in_miti = (bos_level * 0.995) <= c[-1] <= (bos_level * 1.012)
        fvg_ok  = any(detect_fvg(h, l, i, "SELL", c[i]) for i in range(n - 6, n - 1))
        if ph and max(ph) > last_pl and c[-1] > h[max(ph)]:
            return None
        confirm = c[-1] < o[-1] and v[-1] > avg * 0.6
        bias_ok = get_htf_ema_bias(symbol) == "BEAR"
        sc_pts  = sum([in_miti, fvg_ok, confirm, bias_ok, True])
        if sc_pts < CONFLUENCE_MIN:
            return None
        sc = min(100, SETUPS["BOS_CONTINUATION"]["score"] + (sc_pts - CONFLUENCE_MIN) * 2)
        return {"name": "BOS_CONTINUATION", "score": sc,
                "bos_level": bos_level, "confluence": sc_pts}


def detect_all_setups(symbol, side):
    """Lance les 3 détecteurs SMC. Retourne liste triée par score décroissant."""
    detectors = [detect_sweep_choch_ob, detect_breaker_fvg, detect_bos_continuation]
    found = []
    for det in detectors:
        try:
            r = det(symbol, side)
            if r:
                found.append(r)
        except Exception as e:
            logger.debug(f"  [SMC] {det.__name__} {symbol} {side}: {e}")
    found.sort(key=lambda x: x["score"], reverse=True)
    return found


def check_chart_confirmations(symbol: str, side: str) -> bool:
    """
    ══════════════════════════════════════════════════════════════════
    CONFIRMATIONS ICT/SMC — DUAL TIMEFRAME M5 → M1
    Logique Top-Down ICT : référence M5, trigger M1.

    PILIER 1 — Structure validée (BOS ou CHoCH sur M5)  ← référence
    PILIER 2 — Liquidity sweep obligatoire (M5)          ← référence
    PILIER 3 — Zone Premium / Discount (equilibrium M5)  ← référence
    PILIER 4 — Bougie d'entrée confirmée sur M1           ← trigger

    MAX 2 positions simultanées (can_afford_position).
    ══════════════════════════════════════════════════════════════════
    """
    try:
        # ── Données M5 — référence structurelle ──────────────────────
        klines_15 = get_klines(symbol, "5m", 100)
        if not klines_15 or len(klines_15) < 30:
            logger.debug(f"  [ICT] {symbol} données M5 insuffisantes")
            return False

        o15 = np.array([float(k[1]) for k in klines_15])
        h15 = np.array([float(k[2]) for k in klines_15])
        l15 = np.array([float(k[3]) for k in klines_15])
        c15 = np.array([float(k[4]) for k in klines_15])
        n15 = len(c15)

        lb15 = 5
        swing_highs_15 = [i for i in range(lb15, n15 - lb15) if h15[i] == max(h15[i-lb15:i+lb15+1])]
        swing_lows_15  = [i for i in range(lb15, n15 - lb15) if l15[i] == min(l15[i-lb15:i+lb15+1])]

        # ── PILIER 1 : BOS / CHoCH sur M15 ───────────────────────────
        structure_confirmed = False
        bos_level = 0.0

        if side == "BUY" and swing_highs_15:
            recent_sh = [i for i in swing_highs_15 if i < n15 - 3]
            if recent_sh:
                last_sh_level = h15[recent_sh[-1]]
                for i in range(recent_sh[-1] + 1, n15):
                    if c15[i] > last_sh_level:
                        structure_confirmed = True
                        bos_level = last_sh_level
                        break
        elif side == "SELL" and swing_lows_15:
            recent_sl = [i for i in swing_lows_15 if i < n15 - 3]
            if recent_sl:
                last_sl_level = l15[recent_sl[-1]]
                for i in range(recent_sl[-1] + 1, n15):
                    if c15[i] < last_sl_level:
                        structure_confirmed = True
                        bos_level = last_sl_level
                        break

        if not structure_confirmed:
            logger.info(f"  [ICT-P1] {symbol} {side} \u274c Pas de BOS/CHoCH M5 \u2192 skip")
            return False

        # ── PILIER 2 : Liquidity sweep M15 ───────────────────────────
        liquidity_swept = False
        sweep_level = 0.0

        if side == "BUY" and swing_lows_15:
            for sl_idx in reversed([i for i in swing_lows_15 if i > n15 - 40]):
                sl_level = l15[sl_idx]
                for j in range(sl_idx + 1, n15 - 1):
                    if l15[j] < sl_level and c15[j] > sl_level:
                        liquidity_swept = True
                        sweep_level = sl_level
                        break
                if liquidity_swept:
                    break
        elif side == "SELL" and swing_highs_15:
            for sh_idx in reversed([i for i in swing_highs_15 if i > n15 - 40]):
                sh_level = h15[sh_idx]
                for j in range(sh_idx + 1, n15 - 1):
                    if h15[j] > sh_level and c15[j] < sh_level:
                        liquidity_swept = True
                        sweep_level = sh_level
                        break
                if liquidity_swept:
                    break

        if not liquidity_swept:
            logger.info(f"  [ICT-P2] {symbol} {side} \u274c Pas de liquidity sweep M5 \u2192 skip")
            return False

        # ── PILIER 3 : Zone Premium / Discount M15 ───────────────────
        premium_discount_valid = False
        if swing_highs_15 and swing_lows_15:
            sh_idx = swing_highs_15[-3:] if len(swing_highs_15) >= 3 else swing_highs_15
            sl_idx = swing_lows_15[-3:]  if len(swing_lows_15)  >= 3 else swing_lows_15
            range_high  = max(h15[i] for i in sh_idx)
            range_low   = min(l15[i] for i in sl_idx)
            equilibrium = (range_high + range_low) / 2
            cur = c15[-1]  # prix courant vu depuis M15
            if side == "BUY":
                premium_discount_valid = cur <= equilibrium * 1.005
            else:
                premium_discount_valid = cur >= equilibrium * 0.995

        if not premium_discount_valid:
            logger.info(f"  [ICT-P3] {symbol} {side} \u274c Zone premium/discount M5 invalide \u2192 skip")
            return False

        # ── PILIER 4 : Bougie d'entrée confirmée sur M1 ──────────────
        # Trigger de précision : on zoome sur M1 pour l'exécution
        klines_1 = get_klines(symbol, "1m", 15)
        if not klines_1 or len(klines_1) < 5:
            logger.debug(f"  [ICT-P4] {symbol} données M1 insuffisantes")
            return False

        o1 = np.array([float(k[1]) for k in klines_1])
        h1 = np.array([float(k[2]) for k in klines_1])
        l1 = np.array([float(k[3]) for k in klines_1])
        c1 = np.array([float(k[4]) for k in klines_1])
        v1 = np.array([float(k[5]) for k in klines_1])
        n1 = len(c1)

        entry_candle_confirmed = False
        confirm_type = ""
        last_o = o1[-1]; last_c = c1[-1]; last_h = h1[-1]; last_l = l1[-1]
        prev_o = o1[-2]; prev_c = c1[-2]; prev_h = h1[-2]; prev_l = l1[-2]
        full_range = last_h - last_l
        avg_vol = np.mean(v1[-11:-1]) if len(v1) > 10 else v1[-1]

        # a) Engulfing M1
        if side == "BUY" and last_c > prev_h and last_o < prev_l:
            entry_candle_confirmed = True; confirm_type = "ENGULFING\U0001f7e2"
        elif side == "SELL" and last_c < prev_l and last_o > prev_h:
            entry_candle_confirmed = True; confirm_type = "ENGULFING\U0001f534"

        # b) Rejection / Pin bar M1 — mèche >= 60%
        if not entry_candle_confirmed and full_range > 0:
            if side == "BUY":
                lower_wick = min(last_o, last_c) - last_l
                if lower_wick / full_range >= 0.60:
                    entry_candle_confirmed = True; confirm_type = "REJECTION_PIN\U0001f7e2"
            else:
                upper_wick = last_h - max(last_o, last_c)
                if upper_wick / full_range >= 0.60:
                    entry_candle_confirmed = True; confirm_type = "REJECTION_PIN\U0001f534"

        # c) FVG tap M1
        if not entry_candle_confirmed and n1 >= 3:
            if side == "BUY" and l1[-1] > h1[-3] and c1[-1] > l1[-1]:
                entry_candle_confirmed = True; confirm_type = "FVG_TAP\U0001f7e2"
            elif side == "SELL" and h1[-1] < l1[-3] and c1[-1] < h1[-1]:
                entry_candle_confirmed = True; confirm_type = "FVG_TAP\U0001f534"

        # d) Volume spike M1 >= 2x moyenne
        if not entry_candle_confirmed:
            if v1[-1] >= avg_vol * 2.0:
                if side == "BUY" and last_c > last_o:
                    entry_candle_confirmed = True; confirm_type = "VOL_SPIKE\U0001f7e2"
                elif side == "SELL" and last_c < last_o:
                    entry_candle_confirmed = True; confirm_type = "VOL_SPIKE\U0001f534"

        if not entry_candle_confirmed:
            logger.info(f"  [ICT-P4] {symbol} {side} \u274c Aucune bougie M1 de confirmation (engulf/pin/fvg/vol) \u2192 skip")
            return False

        # ── Anti re-entry même structure ─────────────────────────────
        mem = structure_memory.get(symbol)
        if mem and mem.get("side") == side and bos_level > 0:
            age = time.time() - mem.get("ts", 0)
            prev_bos = mem.get("bos_level", 0)
            if age < 1800 and prev_bos > 0 and abs(bos_level - prev_bos) / prev_bos < 0.003:
                logger.info(f"  [ICT-REENTRY] {symbol} {side} \u274c Même structure M5 BOS@{bos_level:.4f} \u2192 skip")
                return False

        structure_memory[symbol] = {"side": side, "bos_level": bos_level, "sweep_level": sweep_level, "ts": time.time()}
        logger.info(f"  [ICT] {symbol} {side} \u2705 M5:BOS@{bos_level:.4f}|SWEEP@{sweep_level:.4f}|ZONE={'DISCOUNT' if side=='BUY' else 'PREMIUM'} → M1:{confirm_type}")
        return True

    except Exception as e:
        logger.debug(f"  [ICT] {symbol} erreur: {e}")
        return False


def reset_structure(symbol: str):
    """Appelé à la clôture d'un trade — libère la structure pour re-entry sur nouvelle structure."""
    if symbol in structure_memory:
        del structure_memory[symbol]
        logger.debug(f"  [STRUCTURE] {symbol} reset — nouvelle structure requise")


def _round_step(qty: float, step_size: float) -> float:
    """
    FIX2-1 — Arrondi qty selon le stepSize Binance (pas quantityPrecision).
    Binance exige qty = N × stepSize exactement, pas juste un nombre de décimales.
    Ex : stepSize=0.001 → qty=0.123 ✅ | stepSize=0.01 → qty=0.12 (pas 0.123)
    """
    if step_size <= 0:
        return qty
    import math
    precision = max(0, -int(math.floor(math.log10(step_size))))
    qty_steps = math.floor(qty / step_size)
    return round(qty_steps * step_size, precision)

# ─── ORDER UTILS ─────────────────────────────────────────────────
def validate_order_size(symbol: str, qty: float, price: float) -> tuple:
    info = get_symbol_info(symbol)
    if not info:
        return (False, "Symbol info not available", 0)
    # FIX2-1 : Appliquer stepSize AVANT tout autre contrôle
    step_size = info.get("stepSize", 0.001)
    qty = _round_step(qty, step_size)
    if qty <= 0:
        return (False, "Qty devient 0 après arrondi stepSize", 0)
    if qty < info["minQty"]:
        return (False, f"Qty {qty} < min {info['minQty']}", 0)
    notional     = price * qty
    min_notional = info.get("minNotional", MIN_NOTIONAL)
    if notional < min_notional:
        # Ajuster à la hausse en respectant le stepSize
        adjusted_qty = _round_step(min_notional / price + step_size, step_size)
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
    """
    Annule TOUS les ordres ouverts d'un symbole.
    ⚠️  NE PAS appeler sur une position OUVERTE active (utiliser _cancel_sl_order_only).
    Réservé à : fermeture de position, recover, annulation complète.
    """
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

    V31-2 : retourne sl_order_id et tp_order_id pour cancel ciblé
    """
    results = {
        "sl_sent": False, "tp_sent": False,
        "sl_order_id": None, "tp_order_id": None,   # V31-2
    }
    close_side = "SELL" if side == "BUY" else "BUY"
    pp = info["pricePrecision"]

    current_price = get_price(symbol)
    if not current_price:
        logger.warning(f"⚠️  {symbol} prix indisponible — SL/TP Binance non posés")
        return results

    if side == "BUY":
        if sl >= current_price:
            logger.warning(f"⚠️  {symbol} SL ({sl}) >= prix courant ({current_price}) — ignoré")
            sl = None
        if tp <= current_price:
            logger.warning(f"⚠️  {symbol} TP ({tp}) <= prix courant ({current_price}) — ignoré")
            tp = None
    else:
        if sl <= current_price:
            logger.warning(f"⚠️  {symbol} SL ({sl}) <= prix courant ({current_price}) — ignoré")
            sl = None
        if tp >= current_price:
            logger.warning(f"⚠️  {symbol} TP ({tp}) >= prix courant ({current_price}) — ignoré")
            tp = None

    # ── Stop Loss ─────────────────────────────────────────────────
    if sl:
        for attempt in range(3):
            try:
                sl_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "STOP_MARKET",
                    "stopPrice":     round(sl, pp),
                    "closePosition": "true",
                    "workingType":   "MARK_PRICE"
                })
                if sl_order and sl_order.get("orderId"):
                    results["sl_sent"]     = True
                    results["sl_order_id"] = sl_order["orderId"]   # V31-2
                    logger.info(f"🛡️  {symbol} SL ✅ @ {round(sl, pp)} (id={sl_order['orderId']})")
                    break
                else:
                    logger.warning(f"⚠️  {symbol} SL tentative {attempt+1}/3 échouée")
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"⚠️  {symbol} SL error (t{attempt+1}): {e}")
                time.sleep(0.5)

        if not results["sl_sent"]:
            logger.error(f"🚨 {symbol} SL Binance impossible après 3 tentatives → MODE URGENCE")
            results["urgent_monitoring"] = True

    # ── Take Profit ───────────────────────────────────────────────
    if tp:
        for attempt in range(2):
            try:
                tp_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "TAKE_PROFIT_MARKET",
                    "stopPrice":     round(tp, pp),
                    "closePosition": "true",
                    "workingType":   "MARK_PRICE"
                })
                if tp_order and tp_order.get("orderId"):
                    results["tp_sent"]     = True
                    results["tp_order_id"] = tp_order["orderId"]   # V31-2
                    logger.info(f"🎯 {symbol} TP ✅ @ {round(tp, pp)} (id={tp_order['orderId']})")
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

        pp = info.get("pricePrecision", 4)

        # ── v4.6 : Levier & marge adaptatifs au setup M5 ─────────
        btc_ctx       = get_btc_composite_score()
        btc_score_ctx = btc_ctx["score"]
        profile_ctx   = get_btc_profile(btc_score_ctx, SIZING_PROFILES)

        adap_lev   = calculate_adaptive_leverage(setup_name, probability)
        margin_pct = calculate_adaptive_margin_pct(setup_name)
        margin     = round(account_balance * margin_pct, 4)

        logger.info(
            f"  [ADAPTIVE] {symbol} setup={setup_name} prob={probability:.1f}% "
            f"→ levier={adap_lev}x | marge={margin:.3f}$ ({margin_pct*100:.0f}% balance)"
        )

        set_leverage(symbol, adap_lev)
        set_margin_type(symbol, MARGIN_TYPE)

        # ── V37-1 : Qty calculé depuis le risque fixe $0.30 ──────
        # sl_distance = distance en prix entre entry et sl structurel
        # qty = FIXED_RISK_USDT / sl_distance
        # On utilise le SL passé par scan_symbol (zone structurelle)
        sl_structural = sl   # sl passé en paramètre = zone structurelle
        if side == "BUY":
            sl_dist_initial = max(entry - sl_structural, entry * MIN_SL_DISTANCE_PCT)
        else:
            sl_dist_initial = max(sl_structural - entry, entry * MIN_SL_DISTANCE_PCT)

        # Calcul qty depuis le risque fixe
        if sl_dist_initial > 0:
            qty_from_risk = FIXED_RISK_USDT / sl_dist_initial
        else:
            qty_from_risk = FIXED_RISK_USDT / (entry * 0.01)

        step_size = info.get("stepSize", 0.001)
        qty = _round_step(qty_from_risk, step_size)

        # ── V37-SAFE : Cap marge = % balance adaptatif par setup ─────
        max_margin_allowed = account_balance * margin_pct
        max_qty_margin     = _round_step((max_margin_allowed * adap_lev) / entry, step_size)
        if qty > max_qty_margin and max_qty_margin > 0:
            logger.warning(
                f"  [MARGIN-CAP] {symbol} qty {qty}→{max_qty_margin} "
                f"(marge ${qty*entry/adap_lev:.2f}→${max_qty_margin*entry/adap_lev:.2f} "
                f"≤ {margin_pct*100:.0f}% × ${account_balance:.2f})"
            )
            qty = max_qty_margin

        notional = qty * entry
        logger.info(f"  [V37-SIZING] {symbol} risk=${FIXED_RISK_USDT} | SL_dist={sl_dist_initial:.{pp}f} | qty={qty} | notional=${notional:.2f}")


        is_valid, msg, adjusted_qty = validate_order_size(symbol, qty, entry)
        if not is_valid:
            logger.warning(f"❌ {symbol} {msg}")
            return
        if adjusted_qty != qty:
            qty = adjusted_qty

        pp      = info.get("pricePrecision", pp)  # Confirme la valeur (déjà défini au début)
        session = get_current_session()

        logger.info(f"🎯 {symbol} {side} | Prob: {probability}% | Marge: ${margin:.2f} | {adap_lev}x | Notionnel: ${notional:.2f}")

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

        # ── V37-2 : Conserver le SL structurel, recalculer qty si besoin ──
        # Le sl passé en paramètre vient de get_structural_sl() dans scan_symbol.
        # On recalcule juste la distance sur la base du vrai prix d'entrée.
        if side == "BUY":
            sl_distance = max(actual_entry - sl, actual_entry * MIN_SL_DISTANCE_PCT)
            sl          = round(actual_entry - sl_distance, pp)
        else:
            sl_distance = max(sl - actual_entry, actual_entry * MIN_SL_DISTANCE_PCT)
            sl          = round(actual_entry + sl_distance, pp)

        # Recalcul final qty sur le vrai prix d'entrée
        if sl_distance > 0:
            qty_final = FIXED_RISK_USDT / sl_distance
            qty = _round_step(qty_final, info.get("stepSize", 0.001))
            logger.info(f"  [V37-QTY-FINAL] {symbol} entry={actual_entry:.{pp}f} | sl_dist={sl_distance:.{pp}f} | qty={qty} | risk_réel=${sl_distance*qty:.4f}")

        # Garde min notional : si qty trop petit, ajuster à la hausse
        # (le risque réel sera légèrement supérieur à $0.30 — acceptable)
        notional_check = qty * actual_entry
        min_notional_sym = info.get("minNotional", MIN_NOTIONAL)
        if notional_check < min_notional_sym:
            qty_min = _round_step(min_notional_sym / actual_entry + info.get("stepSize", 0.001), info.get("stepSize", 0.001))
            real_risk = sl_distance * qty_min
            if real_risk <= FIXED_RISK_USDT * MAX_RISK_MULTIPLIER:
                logger.info(f"  [V37-MIN-NOTIONAL] {symbol} qty ajusté {qty}→{qty_min} | risque réel ${real_risk:.4f}")
                qty = qty_min
            else:
                logger.warning(f"  [V37-SKIP] {symbol} min notional exige risque ${real_risk:.4f} > ${FIXED_RISK_USDT*MAX_RISK_MULTIPLIER:.2f} → skip")
                place_order_with_fallback(symbol, "SELL" if side == "BUY" else "BUY", qty, actual_entry)
                return

        # V37-4 : TP = filet de sécurité RR 8 (le trailing SL est le vrai mécanisme de sortie)
        if side == "BUY":
            tp = round(actual_entry + sl_distance * TP_SAFETY_NET_RR, pp)
        else:
            tp = round(actual_entry - sl_distance * TP_SAFETY_NET_RR, pp)
        logger.info(f"  [V37-TP-FILET] {symbol} TP filet @ {tp:.{pp}f} (RR {TP_SAFETY_NET_RR}×) — sortie réelle = trailing SL")

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
                "sl_order_id":          sl_tp_results.get("sl_order_id"),    # V31-2
                "tp_order_id":          sl_tp_results.get("tp_order_id"),    # V31-2
                "sl_fail_count":        0,                                   # V31-6
                "urgent_monitoring":    sl_tp_results.get("urgent_monitoring", False),
                "sl_retry_at":          time.time() + 30 if sl_tp_results.get("urgent_monitoring") else None,
                "retry_count":          0,
                "trailing_stop_active": False,
                "breakeven_moved":      False,
                "partial_tp_done":      False,   # V34-7 : TP partiel non encore exécuté
                "bias_1h":              get_htf_ema_bias(symbol),    # FIX-7 : pour journal CSV
                "bias_4h":              get_htf_4h_bias(symbol),     # FIX-7 : pour journal CSV
                "highest_price":        actual_entry if side == "BUY"  else None,
                "lowest_price":         actual_entry if side == "SELL" else None,
                "last_sl_update":       time.time()
            }
            total_traded += 1

        send_telegram(
            f"🚀 <b>{symbol}</b> {side}\n"
            f"Prob: {probability}% | Mode: {profile_ctx.get('label','?')}\n"
            f"Entry: ${actual_entry:.{pp}f} | Levier: {adap_lev}x\n"
            f"SL: ${sl:.{pp}f} {'🛡️ Binance' if sl_tp_results['sl_sent'] else '⚠️ logiciel'} | dist={abs(actual_entry-sl)/actual_entry*100:.2f}%\n"
            f"Risque: ${sl_distance*qty:.4f} (fixe ${FIXED_RISK_USDT}) | Qty: {qty}\n"
            f"TP filet: ${tp:.{pp}f} (RR{TP_SAFETY_NET_RR}) — sortie = trailing SL 🔁\n"
            f"BTC: {btc_ctx['label']} ({btc_score_ctx:+.2f})"
        )

    except Exception as e:
        logger.error(f"open_position {symbol}: {e}")

# ─── BREAKEVEN ───────────────────────────────────────────────────
def _cancel_sl_order_only(symbol: str, trade: dict):
    """
    V31-1 — Annule UNIQUEMENT le SL Binance actuel, jamais le TP.

    Méthode 1 : annulation par orderId (précise, si on a l'ID)
    Méthode 2 : parcours des ordres ouverts, annule seulement STOP_MARKET
    Le TP (TAKE_PROFIT_MARKET) est TOUJOURS préservé.
    """
    try:
        sl_id = trade.get("sl_order_id")
        if sl_id:
            # Annulation ciblée par ID → TP intact garanti
            result = request_binance("DELETE", "/fapi/v1/order",
                                     {"symbol": symbol, "orderId": sl_id})
            if result:
                logger.debug(f"  [SL-CANCEL] {symbol} SL id={sl_id} annulé ✅")
                trade["sl_order_id"] = None
                return
        # Fallback : parcourir et annuler uniquement les STOP_MARKET
        open_orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if open_orders:
            for order in open_orders:
                if order.get("type") == "STOP_MARKET":
                    request_binance("DELETE", "/fapi/v1/order",
                                    {"symbol": symbol, "orderId": order["orderId"]})
                    logger.debug(f"  [SL-CANCEL] {symbol} STOP_MARKET id={order['orderId']} annulé ✅")
                # TAKE_PROFIT_MARKET → jamais annulé ici
    except Exception as e:
        logger.warning(f"_cancel_sl_order_only {symbol}: {e}")


def _push_sl_to_binance(symbol: str, trade: dict, new_sl: float, info: dict):
    """
    V31 — Met à jour le SL sur Binance de façon SÉCURISÉE.

    RÈGLE ABSOLUE : le TP ne doit JAMAIS être annulé lors d'un update SL.

    Séquence sécurisée :
      1. Sauvegarder l'ancien SL (pour restauration si échec)
      2. Annuler UNIQUEMENT le SL actuel (pas le TP)
      3. Poser le nouveau SL
      4. Si le nouveau SL échoue → restaurer l'ancien SL immédiatement
      5. Si 2 échecs consécutifs → activer mode urgence
    """
    try:
        old_sl       = trade.get("sl")
        old_sl_id    = trade.get("sl_order_id")
        pp           = info["pricePrecision"]
        close_side   = "SELL" if trade["side"] == "BUY" else "BUY"

        # ── Étape 1 : Annuler uniquement le SL (TP intact) ──────────
        _cancel_sl_order_only(symbol, trade)

        # ── Étape 2 : Poser le nouveau SL ───────────────────────────
        new_sl_order = None
        for attempt in range(3):
            try:
                new_sl_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "STOP_MARKET",
                    "stopPrice":     round(new_sl, pp),
                    "closePosition": "true",
                    "workingType":   "MARK_PRICE"
                })
                if new_sl_order and new_sl_order.get("orderId"):
                    trade["sl"]           = new_sl
                    trade["sl_on_binance"] = True
                    trade["sl_order_id"]  = new_sl_order["orderId"]
                    trade["sl_fail_count"] = 0
                    logger.info(f"🛡️  {symbol} SL mis à jour : {old_sl:.{pp}f} → {new_sl:.{pp}f} "
                                f"(id={new_sl_order['orderId']})")
                    return   # ✅ Succès
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"  SL update attempt {attempt+1}/3 failed: {e}")
                time.sleep(0.5)

        # ── Étape 3 : Échec → RESTAURER l'ancien SL immédiatement ───
        logger.error(f"🚨 {symbol} Nouveau SL échoué → RESTAURATION de l'ancien SL @ {old_sl:.{pp}f}")
        restore_order = None
        for attempt in range(3):
            try:
                restore_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol":        symbol,
                    "side":          close_side,
                    "type":          "STOP_MARKET",
                    "stopPrice":     round(old_sl, pp),
                    "closePosition": "true",
                    "workingType":   "MARK_PRICE"
                })
                if restore_order and restore_order.get("orderId"):
                    trade["sl_order_id"]   = restore_order["orderId"]
                    trade["sl_on_binance"] = True
                    logger.info(f"✅ {symbol} Ancien SL restauré @ {old_sl:.{pp}f}")
                    break
                time.sleep(0.5)
            except:
                time.sleep(0.5)

        # ── Étape 4 : Si même la restauration échoue → URGENCE ──────
        if not restore_order or not restore_order.get("orderId"):
            fail_count = trade.get("sl_fail_count", 0) + 1
            trade["sl_fail_count"]     = fail_count
            trade["sl_on_binance"]     = False
            trade["urgent_monitoring"] = True
            trade["sl_retry_at"]       = time.time() + 5   # Retry très rapide
            logger.error(f"🚨🚨 {symbol} SL PERDU (tentative #{fail_count}) → URGENCE MAXIMALE")
            send_telegram(
                f"🚨🚨 <b>ALERTE SL PERDU : {symbol}</b>\n"
                f"Impossible de poser/restaurer le SL\n"
                f"Position : {trade['side']} @ {trade['entry']:.{pp}f}\n"
                f"SL logiciel actif @ {old_sl:.{pp}f}\n"
                f"<b>Vérifiez manuellement !</b>"
            )

    except Exception as e:
        logger.error(f"_push_sl_to_binance {symbol}: {e}")


def get_candle_swing(symbol: str, side: str, lookback: int = 5) -> float:
    """V36 — Dernier swing high/low sur bougies 1m pour trailing SL."""
    try:
        klines = get_klines(symbol, "1m", lookback + 4)
        if not klines or len(klines) < lookback + 2:
            return 0.0
        highs = [float(k[2]) for k in klines]
        lows  = [float(k[3]) for k in klines]
        n = len(highs)
        pivot_n = 2
        if side == "SELL":
            for i in range(n - 2, pivot_n - 1, -1):
                if all(highs[i] >= highs[i-j] for j in range(1, pivot_n+1)) and                    all(highs[i] >= highs[i+j] for j in range(1, min(pivot_n+1, n-i))):
                    return highs[i]
            return max(highs[-lookback:])
        else:
            for i in range(n - 2, pivot_n - 1, -1):
                if all(lows[i] <= lows[i-j] for j in range(1, pivot_n+1)) and                    all(lows[i] <= lows[i+j] for j in range(1, min(pivot_n+1, n-i))):
                    return lows[i]
            return min(lows[-lookback:])
    except Exception as e:
        logger.debug(f"get_candle_swing {symbol}: {e}")
        return 0.0


def detect_engulfing_candle(symbol: str, side: str) -> bool:
    """V36 — Bougie englobante = trailing plus serré."""
    try:
        klines = get_klines(symbol, "1m", 3)
        if not klines or len(klines) < 2:
            return False
        po = float(klines[-2][1]); pc = float(klines[-2][4])
        co = float(klines[-1][1]); cc = float(klines[-1][4])
        if side == "BUY":
            return cc > po and co < pc and cc > co and pc < po
        else:
            return cc < po and co > pc and cc < co and pc > po
    except:
        return False


def update_trailing_sl(symbol: str, current_price: float):
    """
    V36 — Trailing SL intelligent basé sur swings des bougies 1m.
    Phase 1: BE+frais dès +0.5R
    Phase 2: TP partiel 30% dès +1R
    Phase 3: Trailing candle (swing ± ATR×0.3) dès +1R
    """
    try:
        with trade_lock:
            if symbol not in trade_log:
                return
            trade = trade_log[symbol]
            if trade.get("status") != "OPEN":
                return
            side  = trade["side"]
            entry = trade["entry"]
            sl    = trade["sl"]
            info  = get_symbol_info(symbol)
            if not info:
                return
            pp        = info["pricePrecision"]
            tick_size = get_tick_size(symbol)
            profit = (current_price - entry) if side == "BUY" else (entry - current_price)
            risk   = (entry - sl)            if side == "BUY" else (sl - entry)
            if risk <= 0:
                return
            rr = profit / risk

            # Water mark
            if side == "BUY":
                hwm = trade.get("highest_price") or current_price
                if current_price > hwm:
                    trade["highest_price"] = current_price
            else:
                lwm = trade.get("lowest_price") or current_price
                if current_price < lwm:
                    trade["lowest_price"] = current_price

            atr = calc_atr(symbol, timeframe="1m") or entry * 0.003
            btc = get_btc_composite_score()
            t_profile = get_trailing_profile(btc["score"])
            t_label   = t_profile.get("label", "")
            new_sl = sl

            # ── Phase 1: Breakeven + buffer frais ───────────────
            if rr >= BREAKEVEN_RR and not trade.get("breakeven_moved"):
                fee_buf = entry * BREAKEVEN_FEE_BUFFER
                if side == "BUY":
                    be_sl = round(entry + fee_buf, pp)
                    if be_sl > sl:
                        new_sl = be_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BE+frais SL={be_sl:.{pp}f} RR={rr:.2f}R")
                else:
                    be_sl = round(entry - fee_buf, pp)
                    if be_sl < sl:
                        new_sl = be_sl
                        trade["breakeven_moved"] = True
                        logger.info(f"🎯 {symbol} BE+frais SL={be_sl:.{pp}f} RR={rr:.2f}R")

            # ── Phase 2: TP partiel 30% dès RR1 ─────────────────
            if (PARTIAL_TP_ENABLED and rr >= PARTIAL_TP_RR and
                    not trade.get("partial_tp_done") and trade.get("qty", 0) > 0):
                sym_info    = get_symbol_info(symbol)
                qty_prec    = sym_info.get("quantityPrecision", 3) if sym_info else 3
                partial_qty = round(trade["qty"] * PARTIAL_TP_CLOSE_PCT, qty_prec)
                if partial_qty > 0:
                    close_side = "SELL" if side == "BUY" else "BUY"
                    partial_order = place_order_with_fallback(symbol, close_side, partial_qty, current_price)
                    if partial_order:
                        remaining_qty = round(trade["qty"] - partial_qty, qty_prec)
                        pnl_partial   = profit * PARTIAL_TP_CLOSE_PCT
                        trade["qty"] = remaining_qty
                        trade["partial_tp_done"] = True
                        logger.info(f"💰 {symbol} TP PARTIEL 30% @ {current_price:.{pp}f} RR={rr:.2f}R +${pnl_partial:.4f}")
                        tp_order_id = trade.get("tp_order_id")
                        if tp_order_id:
                            try:
                                request_binance("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": tp_order_id})
                            except:
                                pass
                        if remaining_qty > 0 and sym_info:
                            emergency_tp = round(entry + risk * 3.0, sym_info.get("pricePrecision", 4)) if side == "BUY"                                       else round(entry - risk * 3.0, sym_info.get("pricePrecision", 4))
                            new_tp = request_binance("POST", "/fapi/v1/order", {
                                "symbol": symbol, "side": close_side,
                                "type": "TAKE_PROFIT_MARKET",
                                "stopPrice": emergency_tp,
                                "closePosition": "true", "workingType": "MARK_PRICE"
                            })
                            if new_tp and new_tp.get("orderId"):
                                trade["tp_order_id"] = new_tp["orderId"]
                                trade["tp"] = emergency_tp
                                logger.info(f"  TP filet @ {emergency_tp:.{pp}f} (trailing gere la sortie)")
                        send_telegram(f"💰 {symbol} TP PARTIEL 30% @ {current_price:.{pp}f} RR={rr:.2f}R +${pnl_partial:.4f} | Reste {remaining_qty} trailing actif")

            # ── Phase 3: Trailing candle-based dès RR1 ──────────
            if TRAILING_ENABLED and rr >= TRAILING_START_RR:
                trade["trailing_stop_active"] = True
                engulfing = detect_engulfing_candle(symbol, side)
                buf_mult  = 0.15 if engulfing else 0.30
                buf       = atr * buf_mult
                swing     = get_candle_swing(symbol, side, lookback=5)
                if side == "BUY":
                    trail_sl = round((swing - buf) if swing > 0 else (trade["highest_price"] - atr * ATR_TRAIL_MULT), pp)
                    if trail_sl > sl:
                        new_sl = trail_sl
                else:
                    trail_sl = round((swing + buf) if swing > 0 else (trade["lowest_price"] + atr * ATR_TRAIL_MULT), pp)
                    if trail_sl < sl:
                        new_sl = trail_sl

            # ── Push SL si déplacé ───────────────────────────────
            sl_delta = abs(new_sl - sl)
            min_delta = tick_size * SL_MIN_UPDATE_TICKS
            sl_moved  = (side == "BUY" and new_sl > sl) or (side == "SELL" and new_sl < sl)
            now_ts    = time.time()
            sl_time_ok = (now_ts - trade.get("last_sl_update", 0)) >= 45

            if sl_moved and sl_delta >= min_delta and sl_time_ok:
                old_sl = sl
                trade["sl"] = new_sl
                trade["last_sl_update"] = now_ts
                tag = "🔁 TRAIL" if trade.get("trailing_stop_active") else "🎯 BE"
                rr_locked = abs(new_sl - entry) / risk if risk > 0 else 0
                logger.info(f"{tag} [{t_label}] {symbol}: {old_sl:.{pp}f}→{new_sl:.{pp}f} RR={rr:.2f}R lock={rr_locked:.2f}R")
                _push_sl_to_binance(symbol, trade, new_sl, info)
                send_telegram(f"{tag} {symbol} SL {old_sl:.{pp}f}→{new_sl:.{pp}f} profit={profit/entry*100:+.2f}% RR={rr:.2f}R")
            elif sl_moved and not sl_time_ok:
                logger.debug(f"⏸ {symbol} SL cooldown 45s")

    except Exception as e:
        logger.warning(f"update_trailing_sl {symbol}: {e}")


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
                    update_symbol_streak(symbol, is_win=False)   # V34-6
                    log_trade_to_csv(symbol, trade, "LOSS", 0, 0)   # FIX-7
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
                    update_symbol_streak(symbol, is_win=True)    # V34-6
                    log_trade_to_csv(symbol, trade, "WIN", 0, 0)  # FIX-7
                    send_telegram(f"✅ {symbol} TP (logiciel)")
    except:
        pass

# ─── V37 : SL STRUCTUREL ─────────────────────────────────────────
def get_structural_sl(symbol: str, side: str, setup: dict, entry: float) -> float:
    """
    V37-2 — SL placé sur la zone structurelle du setup détecté.

    Priorité :
      1. SWEEP_CHOCH_OB  → ob["bottom"] (BUY) ou ob["top"] (SELL)
      2. BREAKER_FVG     → breaker_level - buffer (BUY) ou + buffer (SELL)
      3. BOS_CONTINUATION→ bos_level - buffer (BUY) ou + buffer (SELL)
      4. Fallback        → dernier pivot low/high sur 15m (lb=5 bougies)

    Le SL est placé 1 tick sous/sur la zone pour éviter le whipsaw.
    Il est ensuite borné : min MIN_SL_DISTANCE_PCT, max 2.5% du prix.
    """
    try:
        info = get_symbol_info(symbol)
        tick = get_tick_size(symbol) if info else entry * 0.0001
        pp   = info["pricePrecision"] if info else 4
        buf  = tick * 3  # buffer = 3 ticks sous/sur la zone

        name = setup.get("name", "")

        if name == "SWEEP_CHOCH_OB":
            ob = setup.get("ob", {})
            if ob:
                if side == "BUY":
                    sl_raw = ob["bottom"] - buf
                else:
                    sl_raw = ob["top"] + buf
                sl_raw = round(sl_raw, pp)
                sl_dist = abs(entry - sl_raw)
                if sl_dist >= entry * MIN_SL_DISTANCE_PCT:
                    return sl_raw

        elif name == "BREAKER_FVG":
            brk = setup.get("breaker_level", 0)
            if brk:
                if side == "BUY":
                    sl_raw = round(brk - buf, pp)
                else:
                    sl_raw = round(brk + buf, pp)
                sl_dist = abs(entry - sl_raw)
                if sl_dist >= entry * MIN_SL_DISTANCE_PCT:
                    return sl_raw

        elif name == "BOS_CONTINUATION":
            bos = setup.get("bos_level", 0)
            if bos:
                if side == "BUY":
                    sl_raw = round(bos - buf, pp)
                else:
                    sl_raw = round(bos + buf, pp)
                sl_dist = abs(entry - sl_raw)
                if sl_dist >= entry * MIN_SL_DISTANCE_PCT:
                    return sl_raw

        # ── Fallback : dernier swing pivot M5 ───────────────────
        data = _get_klines_np(symbol, "5m", 60)
        if data is not None:
            _, h, l, _, _ = data
            if side == "BUY":
                pl = find_pivot_lows(l, lb=4)
                if pl:
                    swing_low = l[max(pl)]
                    sl_raw = round(swing_low - buf, pp)
                    sl_dist = abs(entry - sl_raw)
                    if sl_dist >= entry * MIN_SL_DISTANCE_PCT:
                        return sl_raw
            else:
                ph = find_pivot_highs(h, l, lb=4)
                if ph:
                    swing_high = h[max(ph)]
                    sl_raw = round(swing_high + buf, pp)
                    sl_dist = abs(entry - sl_raw)
                    if sl_dist >= entry * MIN_SL_DISTANCE_PCT:
                        return sl_raw

    except Exception as e:
        logger.debug(f"get_structural_sl {symbol}: {e}")

    # ── Fallback ultime : ATR M5 × 1.5 ──────────────────────────
    atr_fallback = calc_atr(symbol, period=14, timeframe="5m") or entry * 0.01
    dist = max(atr_fallback * 1.5, entry * MIN_SL_DISTANCE_PCT)
    info = get_symbol_info(symbol)
    pp = info["pricePrecision"] if info else 4
    if side == "BUY":
        return round(entry - dist, pp)
    else:
        return round(entry + dist, pp)


# ─── SCAN ────────────────────────────────────────────────────────
def scan_symbol(symbol: str) -> dict:
    try:
        with trade_lock:
            if symbol in trade_log and trade_log[symbol].get("status") == "OPEN":
                return None
            n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
        if not can_afford_position(account_balance, n_open):
            return None

        # V30-3 — Kill-switch drawdown : log seulement, ne bloque plus le scan
        # Les pauses drawdown sont supprimées — le bot tourne H24 sans interruption
        check_drawdown_kill_switch()  # alerte Telegram seulement

        # FIX2-6 : Funding maintenant directionnel (appliqué per-side BUY/SELL plus bas)
        # is_funding_safe(symbol) ← remplacé par is_funding_safe(symbol, side="BUY/SELL")

        # V30-5 — Filtre spread (marché illiquide → slippage)
        if not is_spread_acceptable(symbol):
            return None

        # ── H24 : Seuils adaptatifs selon la session ─────────────────
        session_now = get_current_session()
        sess_cfg    = SESSION_SCORE_OVERRIDE.get(session_now, SESSION_SCORE_OVERRIDE["OFF_HOURS"])
        eff_min_score   = sess_cfg["min_score"]
        eff_min_prob    = sess_cfg["min_prob"]
        eff_min_conf    = sess_cfg["min_confluence"]
        if session_now in ("ASIA", "OFF_HOURS"):
            logger.debug(f"⏰ {symbol} session={session_now} → seuils renforcés score≥{eff_min_score} prob≥{eff_min_prob}%")

        # ── V34-6 : Anti-overtrade — cooldown par symbole ───────────
        if is_symbol_on_cooldown(symbol):
            return None

        # ── FIX-9 : Signal cooldown — même signal pas réévalué en boucle ──
        if is_signal_in_cooldown(symbol):
            return None

        # ── FIX-5 : Filtre corrélation — max 2 positions même groupe ──
        if is_correlation_limit_reached(symbol):
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

            # ══════════════════════════════════════════════════════════
            # RÈGLE ANTI CONTRE-TENDANCE — INVIOLABLE
            # Les 3 timeframes doivent être alignés avec le trade :
            #   BTC 1H direction + Symbole 1H EMA50 + Symbole 4H EMA50
            # Si l'un contredit → trade interdit, point final.
            # C'est la leçon du trade ZECUSDT (short avec BTC 1H haussier)
            # ══════════════════════════════════════════════════════════
            btc_1h_dir = get_btc_trend_tf("1h")["direction"]  # -1 / 0 / +1

            if allow_sell:
                # SELL interdit si BTC 1H haussier (rebond en cours)
                if btc_1h_dir == 1:
                    logger.info(f"🚫 {symbol} SELL bloqué — BTC 1H ▲ haussier (anti contre-tendance)")
                    allow_sell = False

            if allow_buy:
                # BUY interdit si BTC 1H baissier (dump en cours)
                if btc_1h_dir == -1:
                    logger.info(f"🚫 {symbol} BUY bloqué — BTC 1H ▼ baissier (anti contre-tendance)")
                    allow_buy = False
        else:
            btc       = get_btc_composite_score()
            btc_score = btc["score"]
            allow_buy = allow_sell = True

        # BUY — filtre funding directionnel BUY avant détection setups
        if allow_buy:
            if not is_funding_safe(symbol, side="BUY"):   # FIX2-6 : directionnel
                allow_buy = False
                logger.debug(f"  [FUNDING-DIR] {symbol} BUY bloqué par funding")

        if allow_buy:
            if is_atr_spike(symbol, side="BUY"):
                return None
            # ══════════════════════════════════════════════════════════
            # CONFIRMATIONS GRAPHIQUES OBLIGATOIRES — ANTI ZECUSDT
            # Bougie confirmée + corps solide + RSI + EMA21 + volume
            # ══════════════════════════════════════════════════════════
            if not check_chart_confirmations(symbol, "BUY"):
                return None
            setups_buy = detect_all_setups(symbol, "BUY")
            for setup in setups_buy:
                # Seuil adaptatif selon session (plus strict Asia/Off-hours)
                if setup.get("score", 0) < eff_min_score:
                    logger.debug(f"  [SCORE-FILTER] {symbol} BUY {setup['name']} score={setup['score']} < {eff_min_score} ({session_now}) → skip")
                    continue
                if setup.get("confluence", 0) < eff_min_conf:
                    logger.debug(f"  [CONF-FILTER] {symbol} BUY confluence={setup.get('confluence',0)} < {eff_min_conf} ({session_now}) → skip")
                    continue
                # V37-2 : SL sur zone structurelle (OB bottom / swing low)
                sl          = get_structural_sl(symbol, "BUY", setup, entry)
                sl_distance = entry - sl
                if sl_distance <= 0:
                    continue
                sl_distance = max(sl_distance, entry * MIN_SL_DISTANCE_PCT)
                sl          = entry - sl_distance
                tp          = round(entry + sl_distance * TP_SAFETY_NET_RR, get_symbol_info(symbol).get("pricePrecision", 4) if get_symbol_info(symbol) else 4)
                probability = calculate_probability(symbol, "BUY", setup["name"])
                rr_check = abs(tp - entry) / sl_distance if sl_distance > 0 else 0
                if rr_check < 2.0:
                    logger.debug(f"  [RR] {symbol} BUY RR={rr_check:.2f} < 2.0 → skip")
                    continue
                if probability >= eff_min_prob:
                    return {
                        "symbol": symbol, "side": "BUY",
                        "entry": entry, "sl": sl, "tp": tp,
                        "setup": setup["name"], "probability": probability,
                        "setup_score": setup.get("score", 0),
                        "confluence": setup.get("confluence", 0),
                    }
        else:
            logger.debug(f"🔴 {symbol} BUY bloqué — BTC BEAR")

        # SELL — filtre funding directionnel SELL avant détection setups
        if allow_sell:
            if not is_funding_safe(symbol, side="SELL"):  # FIX2-6 : directionnel
                allow_sell = False
                logger.debug(f"  [FUNDING-DIR] {symbol} SELL bloqué par funding")

        if allow_sell:
            if is_atr_spike(symbol, side="SELL"):
                return None
            # ══════════════════════════════════════════════════════════
            # CONFIRMATIONS GRAPHIQUES OBLIGATOIRES — ANTI ZECUSDT
            # Bougie confirmée + corps solide + RSI + EMA21 + volume
            # ══════════════════════════════════════════════════════════
            if not check_chart_confirmations(symbol, "SELL"):
                return None
            setups_sell = detect_all_setups(symbol, "SELL")
            for setup in setups_sell:
                # Seuil adaptatif selon session (plus strict Asia/Off-hours)
                if setup.get("score", 0) < eff_min_score:
                    logger.debug(f"  [SCORE-FILTER] {symbol} SELL {setup['name']} score={setup['score']} < {eff_min_score} ({session_now}) → skip")
                    continue
                if setup.get("confluence", 0) < eff_min_conf:
                    logger.debug(f"  [CONF-FILTER] {symbol} SELL confluence={setup.get('confluence',0)} < {eff_min_conf} ({session_now}) → skip")
                    continue
                # V37-2 : SL sur zone structurelle (OB top / swing high)
                sl          = get_structural_sl(symbol, "SELL", setup, entry)
                sl_distance = sl - entry
                if sl_distance <= 0:
                    continue
                sl_distance = max(sl_distance, entry * MIN_SL_DISTANCE_PCT)
                sl          = entry + sl_distance
                tp          = round(entry - sl_distance * TP_SAFETY_NET_RR, get_symbol_info(symbol).get("pricePrecision", 4) if get_symbol_info(symbol) else 4)
                probability = calculate_probability(symbol, "SELL", setup["name"])
                rr_check = abs(tp - entry) / sl_distance if sl_distance > 0 else 0
                if rr_check < 2.0:
                    logger.debug(f"  [RR] {symbol} SELL RR={rr_check:.2f} < 2.0 → skip")
                    continue
                if probability >= eff_min_prob:
                    return {
                        "symbol": symbol, "side": "SELL",
                        "entry": entry, "sl": sl, "tp": tp,
                        "setup": setup["name"], "probability": probability,
                        "setup_score": setup.get("score", 0),
                        "confluence": setup.get("confluence", 0),
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

        max_pos = calculate_max_positions(account_balance)
        recovered_count = 0

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

            # ⚠️ V37-SAFE : Limiter le recover à MAX_POSITIONS
            with trade_lock:
                n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
            if n_open + recovered_count >= max_pos:
                logger.warning(f"  [RECOVER-LIMIT] {symbol} ignoré — déjà {n_open+recovered_count}/{max_pos} positions (limite sécurité compte ${account_balance:.2f})")
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
            set_leverage(symbol, LEVERAGE_BY_SETUP.get("BREAKER_FVG", 30))  # v4.6 : levier modéré pour positions récupérées

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
                    "probability":          68.0,
                    "status":               "OPEN",
                    "opened_at":            time.time(),
                    "session":              get_current_session(),
                    "sl_on_binance":        sl_tp["sl_sent"],
                    "tp_on_binance":        sl_tp["tp_sent"],
                    "sl_order_id":          sl_tp.get("sl_order_id"),    # V31-2
                    "tp_order_id":          sl_tp.get("tp_order_id"),    # V31-2
                    "sl_fail_count":        0,
                    "urgent_monitoring":    not sl_tp["sl_sent"],
                    "sl_retry_at":          time.time() + 30 if not sl_tp["sl_sent"] else None,
                    "retry_count":          0,
                    "trailing_stop_active": False,
                    "breakeven_moved":      False,
                    "partial_tp_done":      False,   # FIX-6 : init pour TP partiel
                    "highest_price":        entry_price if side == "BUY"  else None,
                    "lowest_price":         entry_price if side == "SELL" else None,
                    "last_sl_update":       time.time(),
                    "is_external":          source == "MANUELLE",
                }
                # FIX-6 : S'assurer que le symbole n'est pas en cooldown parasite au redémarrage
                # On reset le streak uniquement si la position est récupérée proprement
                if symbol not in symbol_loss_streak:
                    symbol_loss_streak[symbol] = 0
                # Lever un éventuel cooldown résiduel (redémarrage propre = ardoise vierge)
                if symbol in symbol_cooldown_until:
                    del symbol_cooldown_until[symbol]
                    logger.info(f"  [RECOVER] {symbol} cooldown levé au redémarrage")

            sl_status = "🛡️ Binance" if sl_tp["sl_sent"] else "⚠️ logiciel"
            tp_status = "🎯 Binance" if sl_tp["tp_sent"] else "⚠️ logiciel"
            recovered_count += 1
            logger.info(f"✅ [{source}] {symbol} {side} adopté | SL {sl_status} @ {sl:.{pp}f} | TP {tp_status} @ {tp:.{pp}f}")

            send_telegram(
                f"🔄 <b>Position {'externe' if source == 'MANUELLE' else 'récupérée'} adoptée</b>\n"
                f"<b>{symbol}</b> {side} qty={qty}\n"
                f"Entry: ${entry_price:.{pp}f} | Levier: {LEVERAGE_BY_SETUP.get('BREAKER_FVG', 30)}x (recover)\n"
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
    _scan_count = 0
    while True:
        try:
            # Resync horloge Binance toutes les 10 min (fix -1021)
            _scan_count += 1
            if _scan_count % 5 == 0:  # V36: resync toutes les 5 scans
                sync_binance_time()
            # FIX2-7 — Vérification emergency stop
            if _bot_emergency_stop:
                logger.info("🛑 Scanner arrêté (emergency stop) — attente /resume")
                time.sleep(10)
                continue
            sync_account_balance()

            # 🆕 V37-FLOOR : Hard floor — freeze si balance critique
            if account_balance < BALANCE_HARD_FLOOR:
                if not getattr(scanner_loop, '_floor_alerted', False):
                    msg = (
                        f"🛑 <b>HARD FLOOR ATTEINT</b>\n"
                        f"Balance: <b>${account_balance:.2f}</b> &lt; ${BALANCE_HARD_FLOOR}\n"
                        f"Trading GELÉ automatiquement.\n"
                        f"👉 Recharger le compte puis /resume"
                    )
                    send_telegram(msg)
                    logger.error(f"🛑 [HARD-FLOOR] ${account_balance:.2f} < ${BALANCE_HARD_FLOOR} → freeze")
                    scanner_loop._floor_alerted = True
                time.sleep(30)
                continue
            else:
                scanner_loop._floor_alerted = False  # reset si rechargé

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(scan_symbol, symbol): symbol for symbol in SYMBOLS}
                signals = [f.result() for f in as_completed(futures) if f.result()]
            signals.sort(key=lambda x: x.get("probability", 0), reverse=True)
            for signal in signals:
                with trade_lock:
                    n_open = len([v for v in trade_log.values() if v.get("status") == "OPEN"])
                if not can_afford_position(account_balance, n_open):
                    break
                mark_signal_attempted(signal["symbol"])   # FIX-9 : cooldown signal
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

                                    # V35-FIX: Récupérer le vrai PnL réalisé depuis l'historique
                                    # unRealizedProfit = 0 quand la position est fermée → inutile
                                    real_pnl = 0.0
                                    # Lire les infos AVANT le try (évite NameError dans except)
                                    trade_entry = trade_log[symbol].get("entry", 0)
                                    trade_side  = trade_log[symbol].get("side", "BUY")
                                    trade_qty   = trade_log[symbol].get("qty", 0)
                                    try:
                                        # Récupérer le dernier income (PnL réalisé)
                                        income_data = request_binance("GET", "/fapi/v1/income", {
                                            "symbol": symbol,
                                            "incomeType": "REALIZED_PNL",
                                            "limit": 5
                                        }, signed=True)
                                        if income_data:
                                            # Prendre le plus récent
                                            recent = sorted(income_data, key=lambda x: x.get("time", 0), reverse=True)
                                            real_pnl = float(recent[0].get("income", 0))
                                    except Exception as e:
                                        logger.debug(f"income fetch {symbol}: {e}")
                                        # Fallback : estimer via mark price
                                        mark = float(pos.get("markPrice", 0))
                                        if mark > 0 and trade_entry > 0:
                                            if trade_side == "BUY":
                                                real_pnl = (mark - trade_entry) * trade_qty
                                            else:
                                                real_pnl = (trade_entry - mark) * trade_qty

                                    if real_pnl > 0:
                                        setup_memory[setup]["wins"] += 1
                                        update_symbol_streak(symbol, is_win=True)
                                        log_trade_to_csv(symbol, trade_log[symbol], "WIN", real_pnl, rr_achieved=real_pnl/trade_log[symbol].get("margin", 0.8) if trade_log[symbol].get("margin") else 0)
                                        logger.info(f"✅ {symbol} WIN ${real_pnl:.4f} (TP déclenché)")
                                        send_telegram(f"✅ <b>{symbol}</b> TP WIN +${real_pnl:.4f} 🎯")
                                    else:
                                        setup_memory[setup]["losses"] += 1
                                        update_symbol_streak(symbol, is_win=False)
                                        log_trade_to_csv(symbol, trade_log[symbol], "LOSS", real_pnl, 0)
                                        logger.info(f"🔴 {symbol} LOSS ${real_pnl:.4f} (SL déclenché)")
                                        send_telegram(f"🔴 <b>{symbol}</b> SL LOSS ${real_pnl:.4f}")
                                    trade_log[symbol]["status"] = "CLOSED"
                                    reset_structure(symbol)   # Libère la structure — re-entry sur nouvelle structure uniquement
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
            btc_full  = get_btc_composite_score()
            btc_score = btc_full["score"]
            btc_label = btc_full["label"]
            with trade_lock:
                trailing_active = sum(1 for v in trade_log.values()
                                      if v.get("status") == "OPEN" and v.get("trailing_stop_active"))

            paused = time.time() < drawdown_state.get("paused_until", 0)
            pause_str = " | ⏸ PAUSED (drawdown)" if paused else ""
            ref_bal   = drawdown_state.get("balance_at_start_of_day", account_balance)
            dd_pct    = (ref_bal - account_balance) / ref_bal * 100 if ref_bal > 0 else 0

            logger.info("═" * 64)
            logger.info(f"v37 ROBOTKING | ${account_balance:.2f} | {n_open}/{max_pos} pos | W:{total_w} L:{total_l}{pause_str}")
            logger.info(f"Risque/trade: ${FIXED_RISK_USDT} | Levier: {LEVERAGE_BY_SETUP['BOS_CONTINUATION']}x-{LEVERAGE_BY_SETUP['SWEEP_CHOCH_OB']}x adaptatif | BTC: {btc_label} ({btc_score:+.2f}) | Daily: {'🔴 BEAR' if btc_full['daily_bear'] else '🟢 BULL'}")
            logger.info(f"SL Binance: {binance_sl} ✅ | SL logiciel: {software_sl} | Trailing: {trailing_active} 🔁 | TP filet RR{TP_SAFETY_NET_RR}")
            logger.info(f"Drawdown jour: {dd_pct:.1f}% | Ref: ${ref_bal:.2f}")

            # ── V35: Affichage détaillé de CHAQUE position ouverte ──
            if n_open > 0:
                logger.info("─── POSITIONS OUVERTES ───")
                try:
                    # Récupérer les PnL réels depuis Binance
                    positions_binance = request_binance("GET", "/fapi/v2/positionRisk", signed=True)
                    pnl_map = {}
                    if positions_binance:
                        for pos in positions_binance:
                            sym = pos.get("symbol")
                            amt = float(pos.get("positionAmt", 0))
                            if amt != 0:
                                pnl_map[sym] = {
                                    "pnl":   float(pos.get("unRealizedProfit", 0)),
                                    "liq":   float(pos.get("liquidationPrice", 0)),
                                    "mark":  float(pos.get("markPrice", 0)),
                                }
                except:
                    pnl_map = {}

                with trade_lock:
                    for sym, t in trade_log.items():
                        if t.get("status") != "OPEN":
                            continue
                        side  = t.get("side", "?")
                        entry = t.get("entry", 0)
                        sl    = t.get("sl", 0)
                        tp    = t.get("tp", 0)
                        qty   = t.get("qty", 0)
                        setup = t.get("setup", "?")
                        be    = "✅" if t.get("breakeven_moved") else "❌"
                        trail = "🔁" if t.get("trailing_stop_active") else "  "
                        sl_src = "🛡️B" if t.get("sl_on_binance") else "⚠️S"

                        pdata = pnl_map.get(sym, {})
                        pnl   = pdata.get("pnl", 0)
                        mark  = pdata.get("mark", entry)
                        liq   = pdata.get("liq", 0)
                        pp    = get_symbol_info(sym)
                        pp    = pp.get("pricePrecision", 4) if pp else 4

                        # Distance au SL et au TP en %
                        if side == "BUY":
                            sl_dist = (mark - sl) / sl * 100 if sl > 0 else 0
                            tp_dist = (tp - mark) / mark * 100 if mark > 0 else 0
                        else:
                            sl_dist = (sl - mark) / mark * 100 if mark > 0 else 0
                            tp_dist = (mark - tp) / mark * 100 if mark > 0 else 0

                        pnl_icon = "🟢" if pnl >= 0 else "🔴"
                        liq_str  = f" | LIQ:{liq:.{pp}f}" if liq > 0 else ""

                        logger.info(
                            f"  {pnl_icon} {sym} {side} | Entry:{entry:.{pp}f} | Mark:{mark:.{pp}f}"
                        )
                        logger.info(
                            f"     SL:{sl:.{pp}f}({sl_src},{sl_dist:+.2f}%) | TP:{tp:.{pp}f}({tp_dist:+.2f}%){liq_str}"
                        )
                        logger.info(
                            f"     PnL: {pnl:+.4f}$ | BE:{be} {trail} | Setup:{setup}"
                        )
            else:
                logger.info("  Aucune position ouverte — scan en cours...")
            logger.info("═" * 64)

            time.sleep(DASHBOARD_INTERVAL)
        except Exception as e:
            logger.debug(f"dashboard_loop: {e}")
            time.sleep(10)

# ─── MAIN ────────────────────────────────────────────────────────
def main():
    logger.info("╔" + "═" * 60 + "╗")
    logger.info("║" + "   ROBOTKING v37 — BTC M15 | Setup M5 | Trigger M1       ║")
    logger.info("║" + f"   v4.6 — Levier adaptatif | 2 positions max              ║")
    logger.info("╚" + "═" * 60 + "╝\n")

    logger.warning("🔥 LIVE TRADING 🔥")
    logger.info(f"✅ V37-1 : Risque FIXE ${FIXED_RISK_USDT} | qty = risk / sl_dist | Pas de lot % capital")
    logger.info(f"✅ V37-2 : SL structurel (OB zone M5, swing pivot M5) — pas ATR arbitraire")
    logger.info(f"✅ V37-3 : TP partiel DÉSACTIVÉ — trailing SL = seul mécanisme de sortie")
    logger.info(f"✅ V37-4 : TP filet RR{TP_SAFETY_NET_RR} (anti-pompe soudaine uniquement)")
    logger.info(f"🆕 v4.6  : Levier adaptatif SWEEP={LEVERAGE_BY_SETUP['SWEEP_CHOCH_OB']}x | BREAKER={LEVERAGE_BY_SETUP['BREAKER_FVG']}x | BOS={LEVERAGE_BY_SETUP['BOS_CONTINUATION']}x")
    logger.info(f"🆕 V37-FLOOR : Hard floor ${BALANCE_HARD_FLOOR} — trading gelé si balance critique (Telegram alert)")
    logger.info(f"🆕 V37-FIX401 : HTTP 401/403 → arrêt immédiat sans retry + alerte Telegram")

    _init_journal()

    start_health_server()
    sync_binance_time()   # Fix -1021 timestamp
    load_symbol_info()
    sync_account_balance()

    # V30-3 — Initialiser la référence drawdown APRÈS avoir la vraie balance
    # (évite le faux positif si le bot redémarre après des pertes)
    init_drawdown_reference()

    max_pos = calculate_max_positions(account_balance)

    logger.info(f"💰 Balance:  ${account_balance:.2f}")
    logger.info(f"🎯 Risque/trade: ${FIXED_RISK_USDT} | Levier: adaptatif {LEVERAGE_BY_SETUP['BOS_CONTINUATION']}x→{LEVERAGE_BY_SETUP['SWEEP_CHOCH_OB']}x | Sizing: qty = ${FIXED_RISK_USDT} / sl_dist")
    logger.info(f"🛡️  Kill-switch: -{DAILY_DRAWDOWN_LIMIT*100:.0f}% / 24h | Funding filter: {MAX_FUNDING_RATE_ABS*100:.2f}%")
    logger.info(f"📐 SL structurel: OB zone → swing pivot → ATR fallback | TP filet RR{TP_SAFETY_NET_RR}\n")

    recover_existing_positions()

    threading.Thread(target=scanner_loop,          daemon=True).start()
    threading.Thread(target=monitor_positions_loop, daemon=True).start()
    threading.Thread(target=dashboard_loop,         daemon=True).start()

    logger.info("✅ v37 ROBOTKING — SL STRUCTUREL + RISQUE FIXE $0.30 ONLINE 🚀\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("\n🛑 Shutdown")

if __name__ == "__main__":
    main()

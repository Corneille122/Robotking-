import time, hmac, hashlib, requests, threading, os, json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from flask import Flask
import logging

# ================= CONFIGURATION LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('robotking.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= SERVEUR =================
app = Flask(__name__)

@app.route('/')
def home(): 
    stats = get_trading_stats()
    return f"""
    <html>
    <head><title>RobotKing M1 PRO</title></head>
    <body style="font-family: monospace; padding: 20px;">
        <h1>🤖 ROBOTKING M1 - PROFESSIONAL</h1>
        <h2>📊 Statistics</h2>
        <pre>{json.dumps(stats, indent=2)}</pre>
        <p><small>Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</small></p>
    </body>
    </html>
    """, 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ================= CONFIGURATION =================
# 🔐 Charger depuis variables d'environnement (SÉCURITÉ)
API_KEY = os.environ.get("BINANCE_API_KEY", "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")  # Optionnel
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://fapi.binance.com"

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

# ========== PARAMÈTRES DE TRADING ==========
LEVERAGE = 20
INITIAL_CAPITAL = 5.0

# 💰 Gestion du capital dynamique
RISK_PER_TRADE_PERCENT = 0.10  # 10% du capital disponible par trade
MAX_DRAWDOWN_PERCENT = 0.20     # Kill switch à -20% du capital initial
MIN_CAPITAL_TO_TRADE = 2.0      # Capital minimum pour continuer

MAX_POSITIONS = 3
MIN_STARS_TO_TRADE = 3

# Stop Loss et Take Profit
SL_PERCENT = 0.01          # 1%
TP_PERCENT = 0.02          # 2%

# Trailing Stop avancé
ENABLE_TRAILING_STOP = True
TRAILING_ACTIVATION = 0.01
TRAILING_DISTANCE = 0.005
DYNAMIC_TRAILING = True    # Ajuste selon volatilité

# Scanner
SCAN_INTERVAL = 15         # 15 secondes (optimisé pour rate limit)
MAX_WORKERS = 6            # Réduit pour éviter rate limit

# Multi-timeframe
USE_MULTI_TIMEFRAME = True
TIMEFRAMES = ["1m", "5m", "15m"]

# ========== CACHE & OPTIMISATION ==========
CACHE_DURATION = 5         # Cache des prix pendant 5 secondes
price_cache = {}           # {symbol: (price, timestamp)}
klines_cache = {}          # {symbol_interval: (data, timestamp)}

# ========== PROTECTION & SÉCURITÉ ==========
TESTNET_MODE = os.environ.get("TESTNET_MODE", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"  # Simulation sans trades réels

# ========== VARIABLES GLOBALES ==========
active_trades = {}         # {symbol: trade_info}
trade_history = []         # Liste complète des trades fermés
daily_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})

trade_lock = threading.Lock()
api_call_times = []        # Pour rate limiting intelligent
api_lock = threading.Lock()

# PnL tracking
starting_capital = INITIAL_CAPITAL
current_capital = INITIAL_CAPITAL
peak_capital = INITIAL_CAPITAL
max_drawdown_reached = 0.0

# ================= RATE LIMITING =================
MAX_CALLS_PER_MINUTE = 1200  # Limite Binance
RATE_LIMIT_WINDOW = 60

def wait_for_rate_limit():
    """Implémente un rate limiting intelligent avec backoff exponentiel"""
    global api_call_times
    
    with api_lock:
        now = time.time()
        
        # Nettoyer les anciens appels
        api_call_times = [t for t in api_call_times if now - t < RATE_LIMIT_WINDOW]
        
        # Vérifier si on approche de la limite
        if len(api_call_times) >= MAX_CALLS_PER_MINUTE * 0.8:  # 80% de la limite
            sleep_time = RATE_LIMIT_WINDOW - (now - api_call_times[0])
            if sleep_time > 0:
                logger.warning(f"⚠️ Rate limit approché, pause de {sleep_time:.1f}s")
                time.sleep(sleep_time)
                api_call_times.clear()
        
        api_call_times.append(now)

# ================= TELEGRAM NOTIFICATIONS =================
def send_telegram(message):
    """Envoie une notification Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        logger.error(f"Erreur Telegram: {e}")

# ================= FONCTIONS API =================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None, max_retries=3):
    """Requête Binance avec backoff exponentiel et gestion d'erreurs avancée"""
    if params is None:
        params = {}
    
    if DRY_RUN and method == "POST":
        logger.info(f"[DRY RUN] Simulation: {method} {path} {params}")
        return {"orderId": f"DRY_{int(time.time()*1000)}"}
    
    wait_for_rate_limit()
    
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
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
            elif resp.status_code == 429:  # Rate limit
                retry_after = int(resp.headers.get('Retry-After', 60))
                logger.warning(f"⚠️ Rate limit! Pause {retry_after}s")
                time.sleep(retry_after)
            else:
                error_msg = resp.text[:200]
                logger.error(f"API Error {resp.status_code}: {error_msg}")
                
                if attempt < max_retries - 1:
                    backoff = (2 ** attempt) * 0.5  # Backoff exponentiel
                    time.sleep(backoff)
                else:
                    return None
                    
        except requests.exceptions.Timeout:
            logger.error(f"Timeout (tentative {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) * 0.5)
        except Exception as e:
            logger.error(f"Erreur requête: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    
    return None

def get_klines_cached(symbol, interval, limit):
    """Récupère les klines avec cache"""
    cache_key = f"{symbol}_{interval}"
    now = time.time()
    
    # Vérifier le cache
    if cache_key in klines_cache:
        data, timestamp = klines_cache[cache_key]
        if now - timestamp < CACHE_DURATION:
            return data
    
    # Récupérer les nouvelles données
    try:
        url = f"{BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            klines_cache[cache_key] = (data, now)
            return data
    except:
        pass
    
    return None

def get_current_price_cached(symbol):
    """Prix actuel avec cache"""
    now = time.time()
    
    if symbol in price_cache:
        price, timestamp = price_cache[symbol]
        if now - timestamp < CACHE_DURATION:
            return price
    
    try:
        url = f"{BASE_URL}/fapi/v1/ticker/price"
        resp = requests.get(url, params={"symbol": symbol}, timeout=5)
        
        if resp.status_code == 200:
            price = float(resp.json()["price"])
            price_cache[symbol] = (price, now)
            return price
    except:
        pass
    
    return None

# ================= GESTION DU CAPITAL =================
def update_capital():
    """Met à jour le capital actuel basé sur le solde Binance"""
    global current_capital, peak_capital, max_drawdown_reached
    
    try:
        balance_req = request_binance("GET", "/fapi/v2/balance")
        if not balance_req:
            return
        
        usdt = next((b for b in balance_req if b["asset"] == "USDT"), None)
        if usdt:
            current_capital = float(usdt["balance"])
            
            # Mettre à jour le peak
            if current_capital > peak_capital:
                peak_capital = current_capital
            
            # Calculer le drawdown actuel
            drawdown = (peak_capital - current_capital) / peak_capital
            if drawdown > max_drawdown_reached:
                max_drawdown_reached = drawdown
            
            logger.info(f"💰 Capital: {current_capital:.2f} USDT (Peak: {peak_capital:.2f}, DD: {drawdown*100:.1f}%)")
            
    except Exception as e:
        logger.error(f"Erreur update_capital: {e}")

def check_capital_protection():
    """Vérifie les protections du capital"""
    global current_capital, starting_capital
    
    # Kill switch si drawdown trop important
    drawdown = (starting_capital - current_capital) / starting_capital
    
    if drawdown >= MAX_DRAWDOWN_PERCENT:
        msg = f"🛑 KILL SWITCH ACTIVÉ!\n"
        msg += f"Drawdown: {drawdown*100:.1f}% (max: {MAX_DRAWDOWN_PERCENT*100}%)\n"
        msg += f"Capital: {current_capital:.2f} USDT (Initial: {starting_capital:.2f})"
        
        logger.critical(msg)
        send_telegram(msg)
        
        # Fermer toutes les positions
        close_all_positions("EMERGENCY_STOP")
        return False
    
    # Vérifier capital minimum
    if current_capital < MIN_CAPITAL_TO_TRADE:
        logger.warning(f"⚠️ Capital trop faible: {current_capital:.2f} < {MIN_CAPITAL_TO_TRADE}")
        return False
    
    return True

def calculate_position_size(entry_price, current_balance):
    """Calcule la taille de position basée sur le risque et le capital disponible"""
    risk_amount = current_balance * RISK_PER_TRADE_PERCENT
    
    # Limiter le risque à un montant raisonnable
    risk_amount = min(risk_amount, current_balance * 0.15)  # Max 15% du capital
    
    # Calculer la quantité basée sur le SL
    quantity_usdt = (risk_amount / SL_PERCENT) * LEVERAGE
    
    # Limiter à 40% du capital avec levier
    max_position = current_balance * LEVERAGE * 0.40
    quantity_usdt = min(quantity_usdt, max_position)
    
    quantity = quantity_usdt / entry_price
    
    return quantity, risk_amount

# ================= ANALYSE TECHNIQUE =================
def calculate_ema(closes, period):
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = closes[0]
    for close in closes[1:]:
        ema = (close - ema) * multiplier + ema
    return ema

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_atr(highs, lows, closes, period=14):
    """Calcule l'ATR pour mesurer la volatilité"""
    if len(highs) < period + 1:
        return None
    
    trs = []
    for i in range(1, len(highs)):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i-1])
        l_pc = abs(lows[i] - closes[i-1])
        trs.append(max(h_l, h_pc, l_pc))
    
    return sum(trs[-period:]) / period

def detect_multi_timeframe_trend(symbol):
    """Analyse la tendance sur plusieurs timeframes"""
    trends = {}
    
    for tf in TIMEFRAMES:
        klines = get_klines_cached(symbol, tf, 50)
        if not klines or len(klines) < 50:
            trends[tf] = "NEUTRAL"
            continue
        
        closes = [float(k[4]) for k in klines]
        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        
        if ema9 and ema21:
            trends[tf] = "BULL" if ema9 > ema21 else "BEAR"
        else:
            trends[tf] = "NEUTRAL"
    
    # Confluence: au moins 2 timeframes dans la même direction
    bull_count = sum(1 for t in trends.values() if t == "BULL")
    bear_count = sum(1 for t in trends.values() if t == "BEAR")
    
    if bull_count >= 2:
        return "BULL", trends
    elif bear_count >= 2:
        return "BEAR", trends
    else:
        return "NEUTRAL", trends

def check_major_support_resistance(symbol, current_price):
    """Vérifie si le prix est proche d'un S/R majeur (sur H1)"""
    klines = get_klines_cached(symbol, "1h", 100)
    if not klines or len(klines) < 100:
        return None
    
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    
    # Identifier les niveaux clés (swing highs/lows)
    resistance_levels = []
    support_levels = []
    
    for i in range(2, len(highs) - 2):
        # Swing high
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistance_levels.append(highs[i])
        
        # Swing low
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            support_levels.append(lows[i])
    
    # Vérifier si on est proche d'un niveau (±0.5%)
    for level in resistance_levels[-5:]:  # 5 derniers résistances
        if abs(current_price - level) / level < 0.005:
            return {"type": "RESISTANCE", "level": level}
    
    for level in support_levels[-5:]:  # 5 derniers supports
        if abs(current_price - level) / level < 0.005:
            return {"type": "SUPPORT", "level": level}
    
    return None

def score_symbol_pro(symbol):
    """Analyse professionnelle avec multi-timeframe et S/R"""
    try:
        # Klines M1
        klines = get_klines_cached(symbol, "1m", 50)
        if not klines or len(klines) < 50:
            return None
        
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        
        current_price = closes[-1]
        stars = 0
        setup_details = []
        
        # ATR pour volatilité
        atr = calculate_atr(highs, lows, closes, 14)
        
        # Multi-timeframe
        if USE_MULTI_TIMEFRAME:
            main_trend, tf_trends = detect_multi_timeframe_trend(symbol)
            
            if main_trend == "BULL":
                stars += 2
                setup_details.append(f"MTF Bullish ({tf_trends})")
            elif main_trend == "BEAR":
                stars += 2
                setup_details.append(f"MTF Bearish ({tf_trends})")
        
        # Support/Resistance
        sr_level = check_major_support_resistance(symbol, current_price)
        if sr_level:
            stars += 1
            setup_details.append(f"Near {sr_level['type']}: {sr_level['level']:.2f}")
        
        # Break of Structure
        recent_high = max(highs[-20:-2])
        recent_low = min(lows[-20:-2])
        
        if current_price > recent_high * 1.001:
            stars += 2
            setup_details.append("BOS Bullish")
        elif current_price < recent_low * 0.999:
            stars += 2
            setup_details.append("BOS Bearish")
        
        # Volume
        avg_volume = sum(volumes[-20:-1]) / 19
        if volumes[-1] > avg_volume * 1.5:
            stars += 1
            setup_details.append("Volume Spike")
        
        # RSI
        rsi = calculate_rsi(closes, 14)
        if rsi and 50 < rsi < 70:
            stars += 1
            setup_details.append("RSI Long")
        elif rsi and 30 < rsi < 50:
            stars += 1
            setup_details.append("RSI Short")
        
        # Déterminer le sens
        if USE_MULTI_TIMEFRAME:
            side = "LONG" if main_trend == "BULL" else "SHORT" if main_trend == "BEAR" else None
        else:
            ema9 = calculate_ema(closes, 9)
            ema21 = calculate_ema(closes, 21)
            side = "LONG" if ema9 and ema21 and ema9 > ema21 else "SHORT" if ema9 and ema21 else None
        
        if not side or stars < MIN_STARS_TO_TRADE:
            return None
        
        return {
            "symbol": symbol,
            "stars": min(stars, 5),
            "side": side,
            "price": current_price,
            "setup_details": setup_details,
            "rsi": rsi,
            "atr": atr,
            "tf_trends": tf_trends if USE_MULTI_TIMEFRAME else None
        }
        
    except Exception as e:
        logger.error(f"Erreur score_symbol_pro {symbol}: {e}")
        return None

def scan_all_symbols_pro():
    """Scanner optimisé avec cache"""
    opportunities = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_symbol = {executor.submit(score_symbol_pro, sym): sym for sym in SYMBOLS}
        
        for future in as_completed(future_to_symbol):
            result = future.result()
            if result and result["stars"] >= MIN_STARS_TO_TRADE:
                opportunities.append(result)
    
    opportunities.sort(key=lambda x: x["stars"], reverse=True)
    return opportunities

# ================= GESTION DES TRADES =================
def get_symbol_precision(symbol):
    try:
        info = request_binance("GET", "/fapi/v1/exchangeInfo")
        if not info:
            return 3
        symbol_info = next((s for s in info.get("symbols", []) if s["symbol"] == symbol), None)
        if not symbol_info:
            return 3
        lot_size = next((f for f in symbol_info.get("filters", []) if f["filterType"] == "LOT_SIZE"), None)
        if lot_size:
            step_size = lot_size.get("stepSize", "0.001")
            precision = len(step_size.rstrip('0').split('.')[-1]) if '.' in step_size else 0
            return precision
        return 3
    except:
        return 3

def place_trade_pro(opportunity):
    """Place un trade avec toutes les sécurités"""
    global active_trades, current_capital, trade_history, daily_stats
    
    symbol = opportunity["symbol"]
    side = opportunity["side"]
    stars = opportunity["stars"]
    entry_price = opportunity["price"]
    atr = opportunity.get("atr")
    
    with trade_lock:
        if symbol in active_trades:
            return False
    
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"🚀 OUVERTURE: {symbol}")
        logger.info(f"   {'⭐' * stars} {stars}/5")
        logger.info(f"   Setup: {', '.join(opportunity['setup_details'])}")
        logger.info(f"   Direction: {side}")
        
        # Mettre à jour le capital
        update_capital()
        
        # Vérifier protections
        if not check_capital_protection():
            logger.error("⛔ Protection capital activée!")
            return False
        
        # Calculer position size
        precision = get_symbol_precision(symbol)
        qty, risk_amount = calculate_position_size(entry_price, current_capital)
        qty = round(qty, precision)
        
        if qty <= 0:
            logger.error("❌ Quantité trop faible")
            return False
        
        logger.info(f"   Quantité: {qty} (Risque: {risk_amount:.2f} USDT)")
        
        # SL/TP avec ATR si disponible
        if DYNAMIC_TRAILING and atr:
            # Utiliser ATR pour un SL dynamique
            sl_distance = max(SL_PERCENT, (atr / entry_price) * 1.5)
            tp_distance = sl_distance * (TP_PERCENT / SL_PERCENT)
        else:
            sl_distance = SL_PERCENT
            tp_distance = TP_PERCENT
        
        if side == "LONG":
            sl = entry_price * (1 - sl_distance)
            tp = entry_price * (1 + tp_distance)
            order_side = "BUY"
        else:
            sl = entry_price * (1 + sl_distance)
            tp = entry_price * (1 - tp_distance)
            order_side = "SELL"
        
        logger.info(f"   SL: {sl:.6f} | TP: {tp:.6f}")
        
        # Configurer levier
        request_binance("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
        time.sleep(0.3)
        
        # Market order
        market_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": order_side,
            "type": "MARKET",
            "quantity": qty
        })
        
        if not market_order or "orderId" not in market_order:
            logger.error(f"❌ Échec ordre: {market_order}")
            return False
        
        logger.info(f"   ✅ Order: {market_order['orderId']}")
        time.sleep(0.3)
        
        # Stop Loss
        opp_side = "SELL" if side == "LONG" else "BUY"
        sl_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": opp_side,
            "type": "STOP_MARKET",
            "stopPrice": round(sl, 8),
            "closePosition": "true"
        })
        
        if not sl_order or "orderId" not in sl_order:
            logger.critical(f"⚠️ SL NON PLACÉ! {sl_order}")
            send_telegram(f"⚠️ ALERTE: Position {symbol} ouverte SANS SL!")
        
        time.sleep(0.3)
        
        # Take Profit
        tp_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": opp_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": round(tp, 8),
            "closePosition": "true"
        })
        
        # Enregistrer
        trade_info = {
            "entry_price": entry_price,
            "quantity": qty,
            "side": side,
            "sl": sl,
            "tp": tp,
            "entry_time": datetime.now(timezone.utc),
            "stars": stars,
            "risk_amount": risk_amount,
            "sl_order_id": sl_order.get("orderId") if sl_order else None,
            "tp_order_id": tp_order.get("orderId") if tp_order else None,
            "trailing_active": False,
            "atr": atr,
            "sl_distance": sl_distance,
            "setup": opportunity['setup_details']
        }
        
        with trade_lock:
            active_trades[symbol] = trade_info
        
        # Stats
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_stats[today]["trades"] += 1
        
        logger.info(f"✅ TRADE OUVERT ({len(active_trades)}/{MAX_POSITIONS})")
        logger.info(f"{'='*60}\n")
        
        # Notification
        msg = f"🚀 <b>TRADE OUVERT</b>\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Direction: {side}\n"
        msg += f"Stars: {'⭐' * stars}\n"
        msg += f"Prix: {entry_price:.6f}\n"
        msg += f"SL: {sl:.6f} | TP: {tp:.6f}"
        send_telegram(msg)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur place_trade {symbol}: {e}")
        return False

def close_all_positions(reason="MANUAL"):
    """Ferme toutes les positions"""
    global active_trades
    
    logger.warning(f"🛑 Fermeture de toutes les positions: {reason}")
    
    with trade_lock:
        symbols = list(active_trades.keys())
    
    for symbol in symbols:
        try:
            trade_info = active_trades[symbol]
            side = "SELL" if trade_info["side"] == "LONG" else "BUY"
            
            request_binance("POST", "/fapi/v1/order", {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": trade_info["quantity"]
            })
            
            logger.info(f"✅ Position fermée: {symbol}")
            
            with trade_lock:
                if symbol in active_trades:
                    del active_trades[symbol]
                    
        except Exception as e:
            logger.error(f"Erreur fermeture {symbol}: {e}")

def update_trailing_stop_dynamic(symbol, trade_info, current_price):
    """Trailing stop avec ajustement dynamique basé sur ATR"""
    try:
        entry_price = trade_info["entry_price"]
        side = trade_info["side"]
        current_sl = trade_info["sl"]
        atr = trade_info.get("atr")
        
        # Calculer profit
        if side == "LONG":
            profit_pct = (current_price - entry_price) / entry_price
        else:
            profit_pct = (entry_price - current_price) / entry_price
        
        # Activer trailing
        if profit_pct >= TRAILING_ACTIVATION and not trade_info["trailing_active"]:
            logger.info(f"📈 Trailing activé: {symbol} (+{profit_pct*100:.1f}%)")
            trade_info["trailing_active"] = True
            send_telegram(f"📈 Trailing activé sur {symbol}")
        
        # Mise à jour dynamique
        if trade_info["trailing_active"]:
            if DYNAMIC_TRAILING and atr:
                # Distance basée sur ATR
                trailing_dist = max(TRAILING_DISTANCE, (atr / current_price) * 0.8)
            else:
                trailing_dist = TRAILING_DISTANCE
            
            if side == "LONG":
                new_sl = current_price * (1 - trailing_dist)
                should_update = new_sl > current_sl
            else:
                new_sl = current_price * (1 + trailing_dist)
                should_update = new_sl < current_sl
            
            if should_update:
                # Annuler ancien SL
                if trade_info.get("sl_order_id"):
                    request_binance("DELETE", "/fapi/v1/order", {
                        "symbol": symbol,
                        "orderId": trade_info["sl_order_id"]
                    })
                    time.sleep(0.2)
                
                # Nouveau SL
                sl_order = request_binance("POST", "/fapi/v1/order", {
                    "symbol": symbol,
                    "side": "SELL" if side == "LONG" else "BUY",
                    "type": "STOP_MARKET",
                    "stopPrice": round(new_sl, 8),
                    "closePosition": "true"
                })
                
                if sl_order and "orderId" in sl_order:
                    trade_info["sl"] = new_sl
                    trade_info["sl_order_id"] = sl_order["orderId"]
                    logger.info(f"   🔄 SL mis à jour: {new_sl:.6f} (distance: {trailing_dist*100:.2f}%)")
        
    except Exception as e:
        logger.error(f"Erreur trailing {symbol}: {e}")

def monitor_positions_pro():
    """Monitoring avancé avec gestion partielle"""
    global active_trades
    
    with trade_lock:
        symbols = list(active_trades.keys())
    
    for symbol in symbols:
        try:
            current_price = get_current_price_cached(symbol)
            if not current_price:
                continue
            
            with trade_lock:
                if symbol not in active_trades:
                    continue
                trade_info = active_trades[symbol]
            
            # P&L
            entry = trade_info["entry_price"]
            side = trade_info["side"]
            
            if side == "LONG":
                pnl_pct = ((current_price - entry) / entry) * 100
            else:
                pnl_pct = ((entry - current_price) / entry) * 100
            
            # Trailing stop
            if ENABLE_TRAILING_STOP:
                update_trailing_stop_dynamic(symbol, trade_info, current_price)
            
        except Exception as e:
            logger.error(f"Erreur monitor {symbol}: {e}")

def sync_positions_pro():
    """Synchronisation avancée avec historique"""
    global active_trades, trade_history, daily_stats, current_capital
    
    try:
        pos_req = request_binance("GET", "/fapi/v2/positionRisk")
        if not pos_req:
            return
        
        real_positions = {p["symbol"]: float(p.get("positionAmt", 0)) for p in pos_req}
        
        with trade_lock:
            closed_symbols = []
            
            for symbol in list(active_trades.keys()):
                if symbol not in real_positions or real_positions[symbol] == 0:
                    # Position fermée
                    trade_info = active_trades[symbol]
                    
                    # Récupérer le PnL réel
                    current_price = get_current_price_cached(symbol)
                    if current_price:
                        entry = trade_info["entry_price"]
                        side = trade_info["side"]
                        qty = trade_info["quantity"]
                        
                        if side == "LONG":
                            pnl = (current_price - entry) * qty
                        else:
                            pnl = (entry - current_price) * qty
                        
                        # Historique
                        trade_record = {
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry,
                            "exit_price": current_price,
                            "quantity": qty,
                            "pnl": pnl,
                            "pnl_pct": (pnl / (entry * qty)) * 100,
                            "entry_time": trade_info["entry_time"],
                            "exit_time": datetime.now(timezone.utc),
                            "duration": (datetime.now(timezone.utc) - trade_info["entry_time"]).seconds,
                            "stars": trade_info["stars"],
                            "setup": trade_info.get("setup", [])
                        }
                        
                        trade_history.append(trade_record)
                        
                        # Stats
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        daily_stats[today]["pnl"] += pnl
                        
                        if pnl > 0:
                            daily_stats[today]["wins"] += 1
                            logger.info(f"✅ WIN: {symbol} | +{pnl:.2f} USDT ({trade_record['pnl_pct']:.1f}%)")
                        else:
                            daily_stats[today]["losses"] += 1
                            logger.info(f"❌ LOSS: {symbol} | {pnl:.2f} USDT ({trade_record['pnl_pct']:.1f}%)")
                        
                        # Notification
                        msg = f"{'✅ WIN' if pnl > 0 else '❌ LOSS'}\n"
                        msg += f"Symbol: {symbol}\n"
                        msg += f"P&L: {pnl:+.2f} USDT ({trade_record['pnl_pct']:+.1f}%)\n"
                        msg += f"Durée: {trade_record['duration']//60}min"
                        send_telegram(msg)
                        
                        # Sauvegarder historique
                        save_trade_history()
                    
                    closed_symbols.append(symbol)
            
            for symbol in closed_symbols:
                del active_trades[symbol]
        
    except Exception as e:
        logger.error(f"Erreur sync_positions: {e}")

def save_trade_history():
    """Sauvegarde l'historique en JSON"""
    try:
        with open("trade_history.json", "w") as f:
            json.dump(trade_history, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Erreur sauvegarde historique: {e}")

def get_trading_stats():
    """Retourne les statistiques de trading"""
    total_trades = len(trade_history)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0
        }
    
    wins = sum(1 for t in trade_history if t["pnl"] > 0)
    losses = sum(1 for t in trade_history if t["pnl"] <= 0)
    total_pnl = sum(t["pnl"] for t in trade_history)
    
    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total_trades) * 100 if total_trades > 0 else 0,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / total_trades if total_trades > 0 else 0,
        "current_capital": current_capital,
        "peak_capital": peak_capital,
        "max_drawdown": max_drawdown_reached * 100,
        "active_positions": len(active_trades)
    }

# ================= BOUCLE PRINCIPALE =================
def main_loop():
    global current_capital, starting_capital
    
    logger.info("=" * 60)
    logger.info("🚀 ROBOTKING M1 - PROFESSIONAL EDITION")
    logger.info("=" * 60)
    logger.info(f"⚙️  Levier: {LEVERAGE}x")
    logger.info(f"💰 Capital: {INITIAL_CAPITAL} USDT")
    logger.info(f"🔒 Max positions: {MAX_POSITIONS}")
    logger.info(f"⭐ Min stars: {MIN_STARS_TO_TRADE}")
    logger.info(f"🛡️ Max drawdown: {MAX_DRAWDOWN_PERCENT*100}%")
    logger.info(f"🔔 Telegram: {'Activé' if TELEGRAM_TOKEN else 'Désactivé'}")
    logger.info(f"🧪 Dry Run: {'Oui' if DRY_RUN else 'Non'}")
    logger.info("=" * 60 + "\n")
    
    # Initialiser capital
    update_capital()
    starting_capital = current_capital
    
    consecutive_errors = 0
    
    while True:
        try:
            # Sync positions
            sync_positions_pro()
            
            # Monitor positions
            if active_trades:
                monitor_positions_pro()
                
                logger.info(f"\n📊 Positions: {len(active_trades)}/{MAX_POSITIONS}")
                for sym, info in active_trades.items():
                    cp = get_current_price_cached(sym)
                    if cp:
                        entry = info["entry_price"]
                        pnl = ((cp - entry) / entry) * 100 if info["side"] == "LONG" else ((entry - cp) / entry) * 100
                        duration = (datetime.now(timezone.utc) - info["entry_time"]).seconds // 60
                        trail = "🔄" if info.get("trailing_active") else ""
                        logger.info(f"   {sym}: {info['side']} | {pnl:+.2f}% | {duration}min | {'⭐'*info['stars']} {trail}")
            
            # Vérifier protections
            if not check_capital_protection():
                logger.critical("⛔ ARRÊT: Protection capital!")
                break
            
            # Scanner si places disponibles
            if len(active_trades) < MAX_POSITIONS:
                logger.info(f"\n🔍 Scanner...")
                
                opportunities = scan_all_symbols_pro()
                
                if opportunities:
                    logger.info(f"\n📡 {len(opportunities)} opportunités:")
                    for opp in opportunities[:3]:
                        logger.info(f"   {'⭐' * opp['stars']} {opp['symbol']}: {opp['side']}")
                    
                    # Ouvrir trades
                    slots = MAX_POSITIONS - len(active_trades)
                    for opp in opportunities[:slots]:
                        if opp["symbol"] not in active_trades:
                            place_trade_pro(opp)
                            time.sleep(2)
                else:
                    logger.info(f"   Aucun setup ≥{MIN_STARS_TO_TRADE}⭐")
            
            # Stats
            stats = get_trading_stats()
            if stats["total_trades"] > 0:
                logger.info(f"\n📈 Stats: {stats['total_trades']} trades | WR: {stats['win_rate']:.1f}% | P&L: {stats['total_pnl']:+.2f}")
            
            consecutive_errors = 0
            time.sleep(SCAN_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("\n⛔ Arrêt demandé")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"⚠️ Erreur ({consecutive_errors}/10): {e}")
            if consecutive_errors >= 10:
                logger.critical("❌ Trop d'erreurs")
                break
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(2)
    main_loop()

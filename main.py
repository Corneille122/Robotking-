import time, hmac, hashlib, requests, threading, os, json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from flask import Flask
import logging

# ================= LOGGING =================
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
    <head><title>RobotKing Pro</title></head>
    <body style="font-family: monospace; padding: 20px;">
        <h1>🤖 ROBOTKING M1 PRO - COMPLETE</h1>
        <h2>📊 Statistics</h2>
        <pre>{json.dumps(stats, indent=2)}</pre>
        <p><small>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</small></p>
    </body>
    </html>
    """, 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ================= CONFIGURATION =================
API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://fapi.binance.com"

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

# ========== PARAMÈTRES TRADING ==========
LEVERAGE = 20
INITIAL_CAPITAL = 4.0
BASE_MARGIN_PER_TRADE = 0.6
MAX_POSITIONS = 4

SL_PERCENT = 0.01
TP_PERCENT = 0.02

# Gestion rejets d'ordre
MAX_ORDER_RETRIES = 3
QUANTITY_INCREMENT_FACTOR = 1.15
MIN_MARGIN_INCREMENT = 0.1

# Croissance exponentielle
GROWTH_MULTIPLIER_THRESHOLD = 6.0
RISK_INCREASE_PERCENT = 0.50

# Trailing Stop
ENABLE_TRAILING_STOP = True
TRAILING_ACTIVATION = 0.01
TRAILING_DISTANCE = 0.005

# Partial TP
ENABLE_PARTIAL_TP = True
TP1_PERCENT = 0.01
TP2_PERCENT = 0.02
TP3_PERCENT = 0.05

# Time-based
MAX_TRADE_DURATION_MIN = 60
STALE_TRADE_THRESHOLD = 0.003

# Sécurité
EMERGENCY_SL_BUFFER = 0.002
FORCE_CLOSE_MAX_RETRIES = 5

# Positions existantes
MANAGE_EXISTING_POSITIONS = True
AUTO_ADD_SL_TP = True
AUTO_CLOSE_NO_PLAN = False

# Sessions
SESSION_STAR_REQUIREMENTS = {
    "LONDON_NY_OVERLAP": 4,
    "LONDON": 4,
    "NY": 4,
    "ASIAN": 5,
    "OFF_HOURS": 5
}

# Zones clés
MIN_TOUCHES_ZONE = 2
ZONE_PROXIMITY_PERCENT = 0.003

# Scanner
SCAN_INTERVAL_NO_TRADES = 15
SCAN_INTERVAL_ACTIVE = 30
MONITOR_INTERVAL = 5
MAX_WORKERS = 6

# Horaires sessions
LONDON_OPEN = 7
LONDON_CLOSE = 16
NY_OPEN = 13
NY_CLOSE = 22
ASIAN_OPEN = 0
ASIAN_CLOSE = 9

# Cache
CACHE_DURATION = 8
price_cache = {}
klines_cache = {}
symbol_info_cache = {}

# Mode
TESTNET_MODE = os.environ.get("TESTNET_MODE", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# ========== VARIABLES GLOBALES ==========
active_trades = {}
trade_history = []

trading_state = {
    "starting_capital": INITIAL_CAPITAL,
    "current_capital": INITIAL_CAPITAL,
    "peak_capital": INITIAL_CAPITAL,
    "growth_tier": 0,
    "current_risk_multiplier": 1.0,
    "current_margin_per_trade": BASE_MARGIN_PER_TRADE,
    "current_max_positions": MAX_POSITIONS,
    "consecutive_losses": 0,
    "in_recovery_mode": False,
    "recovery_trades_limit": MAX_POSITIONS,
    "last_loss_capital": 0.0
}

daily_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})

trade_lock = threading.Lock()
api_call_times = []
api_lock = threading.Lock()

# Rate limiting
MAX_CALLS_PER_MINUTE = 1200
RATE_LIMIT_WINDOW = 60

# ================= RATE LIMITING =================
def wait_for_rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < RATE_LIMIT_WINDOW]
        if len(api_call_times) >= MAX_CALLS_PER_MINUTE * 0.8:
            sleep_time = RATE_LIMIT_WINDOW - (now - api_call_times[0])
            if sleep_time > 0:
                logger.warning(f"⚠️ Rate limit pause {sleep_time:.1f}s")
                time.sleep(sleep_time)
                api_call_times.clear()
        api_call_times.append(now)

# ================= TELEGRAM =================
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# ================= API BINANCE =================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None, max_retries=3):
    if params is None:
        params = {}
    
    if DRY_RUN and method == "POST":
        logger.info(f"[DRY RUN] {method} {path}")
        return {"orderId": f"DRY_{int(time.time()*1000)}", "avgPrice": "50000.0", "executedQty": "0.001"}
    
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
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get('Retry-After', 60))
                logger.warning(f"Rate limit {retry_after}s")
                time.sleep(retry_after)
            else:
                error_data = resp.json() if resp.text else {}
                logger.error(f"API Error {resp.status_code}: {error_data}")
                return {"error": error_data, "status_code": resp.status_code}
        except Exception as e:
            logger.error(f"Request error: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None

# ================= CACHE PRIX =================
def get_current_price_cached(symbol):
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

def get_klines_cached(symbol, interval, limit):
    cache_key = f"{symbol}_{interval}"
    now = time.time()
    
    if cache_key in klines_cache:
        data, timestamp = klines_cache[cache_key]
        if now - timestamp < CACHE_DURATION:
            return data
    
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

# ================= PRÉCISION SYMBOLES =================
def get_symbol_info(symbol):
    if symbol in symbol_info_cache:
        return symbol_info_cache[symbol]
    
    try:
        info = request_binance("GET", "/fapi/v1/exchangeInfo")
        if not info or "symbols" not in info:
            return get_default_symbol_info()
        
        symbol_data = next((s for s in info["symbols"] if s["symbol"] == symbol), None)
        if not symbol_data:
            return get_default_symbol_info()
        
        filters = {f["filterType"]: f for f in symbol_data.get("filters", [])}
        
        lot_size = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        min_notional = filters.get("MIN_NOTIONAL", {})
        
        symbol_info = {
            "quantityPrecision": symbol_data.get("quantityPrecision", 3),
            "pricePrecision": symbol_data.get("pricePrecision", 2),
            "minQty": float(lot_size.get("minQty", 0.001)),
            "maxQty": float(lot_size.get("maxQty", 10000)),
            "stepSize": float(lot_size.get("stepSize", 0.001)),
            "tickSize": float(price_filter.get("tickSize", 0.01)),
            "minNotional": float(min_notional.get("notional", 5.0))
        }
        
        symbol_info_cache[symbol] = symbol_info
        return symbol_info
        
    except Exception as e:
        logger.error(f"Error get_symbol_info {symbol}: {e}")
        return get_default_symbol_info()

def get_default_symbol_info():
    return {
        "quantityPrecision": 3,
        "pricePrecision": 2,
        "minQty": 0.001,
        "maxQty": 10000,
        "stepSize": 0.001,
        "tickSize": 0.01,
        "minNotional": 5.0
    }

def round_quantity(quantity, symbol_info):
    step_size = symbol_info["stepSize"]
    precision = symbol_info["quantityPrecision"]
    rounded = round(quantity / step_size) * step_size
    rounded = round(rounded, precision)
    rounded = max(symbol_info["minQty"], rounded)
    rounded = min(symbol_info["maxQty"], rounded)
    return rounded

def round_price(price, symbol_info):
    tick_size = symbol_info["tickSize"]
    precision = symbol_info["pricePrecision"]
    rounded = round(price / tick_size) * tick_size
    rounded = round(rounded, precision)
    return rounded

# ================= RÉCUPÉRATION PRIX FILL =================
def get_order_fill_price(symbol, order_id, max_wait=10):
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            order_info = request_binance("GET", "/fapi/v1/order", {
                "symbol": symbol,
                "orderId": order_id
            })
            
            if not order_info:
                time.sleep(0.5)
                continue
            
            status = order_info.get("status")
            
            if status == "FILLED":
                avg_price = float(order_info.get("avgPrice", 0))
                executed_qty = float(order_info.get("executedQty", 0))
                
                if avg_price > 0:
                    logger.info(f"✅ Fill @ ${avg_price:.6f} (qty: {executed_qty})")
                    return avg_price
            
            elif status in ["NEW", "PARTIALLY_FILLED"]:
                time.sleep(0.5)
                continue
            
            else:
                logger.error(f"❌ Ordre {status}")
                return None
        
        except Exception as e:
            logger.error(f"Error get_order_fill_price: {e}")
            time.sleep(0.5)
    
    logger.error(f"⏱️ Timeout fill")
    return None

# ================= PLACEMENT ORDRE ROBUSTE =================
def place_market_order_with_retry(symbol, side, quantity, position_side, margin_budget, max_retries=MAX_ORDER_RETRIES):
    symbol_info = get_symbol_info(symbol)
    current_margin = margin_budget
    current_qty = quantity
    
    for attempt in range(max_retries):
        try:
            rounded_qty = round_quantity(current_qty, symbol_info)
            
            current_price = get_current_price_cached(symbol)
            if not current_price:
                logger.error(f"❌ Prix indisponible {symbol}")
                return {"success": False}
            
            notional = rounded_qty * current_price
            
            if notional < symbol_info["minNotional"]:
                logger.warning(f"⚠️ Notional faible: ${notional:.2f} < ${symbol_info['minNotional']:.2f}")
                needed_qty = symbol_info["minNotional"] / current_price
                current_qty = needed_qty * 1.1
                current_margin = (current_qty * current_price) / LEVERAGE
                logger.info(f"   Ajustement: qty={current_qty:.6f}, margin=${current_margin:.2f}")
                continue
            
            logger.info(f"🎯 Tentative {attempt + 1}/{max_retries}")
            logger.info(f"   Qty: {rounded_qty} | Notional: ${notional:.2f}")
            
            order_params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": rounded_qty,
                "positionSide": position_side
            }
            
            order_result = request_binance("POST", "/fapi/v1/order", order_params)
            
            if not order_result:
                logger.error(f"❌ Échec requête")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return {"success": False}
            
            if "error" in order_result:
                error = order_result["error"]
                error_code = error.get("code")
                error_msg = error.get("msg", "")
                
                logger.error(f"❌ Binance: {error_code} - {error_msg}")
                
                if "LOT_SIZE" in error_msg or error_code == -1111:
                    logger.warning(f"   → Augmentation qty")
                    current_qty *= QUANTITY_INCREMENT_FACTOR
                    current_margin += MIN_MARGIN_INCREMENT
                
                elif "MIN_NOTIONAL" in error_msg or error_code == -1013:
                    logger.warning(f"   → Augmentation notional")
                    current_qty *= QUANTITY_INCREMENT_FACTOR
                    current_margin += MIN_MARGIN_INCREMENT
                
                elif "PRICE_FILTER" in error_msg:
                    logger.warning(f"   → Ajustement prix")
                
                else:
                    logger.error(f"   → Erreur non gérée: {error_msg}")
                    return {"success": False}
                
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    return {"success": False}
            
            order_id = order_result.get("orderId")
            logger.info(f"✅ Ordre: {order_id}")
            
            fill_price = get_order_fill_price(symbol, order_id)
            
            if not fill_price:
                logger.error(f"❌ Prix fill non récupéré")
                fill_price = current_price
            
            return {
                "success": True,
                "order_id": order_id,
                "fill_price": fill_price,
                "executed_qty": rounded_qty,
                "margin_used": current_margin
            }
            
        except Exception as e:
            logger.error(f"❌ Exception {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return {"success": False}
    
    return {"success": False}

# ================= PLACEMENT SL/TP =================
def place_sl_tp_orders(symbol, side, entry_price, quantity, position_side):
    symbol_info = get_symbol_info(symbol)
    
    if side == "LONG":
        sl_price = entry_price * (1 - SL_PERCENT)
        tp_price = entry_price * (1 + TP_PERCENT)
    else:
        sl_price = entry_price * (1 + SL_PERCENT)
        tp_price = entry_price * (1 - TP_PERCENT)
    
    sl_price = round_price(sl_price, symbol_info)
    tp_price = round_price(tp_price, symbol_info)
    
    logger.info(f"🛡️ SL/TP basés sur entry: ${entry_price:.6f}")
    logger.info(f"   SL: ${sl_price:.6f} | TP: ${tp_price:.6f}")
    
    # Placer SL
    sl_params = {
        "symbol": symbol,
        "side": "SELL" if side == "LONG" else "BUY",
        "type": "STOP_MARKET",
        "stopPrice": sl_price,
        "closePosition": "true",
        "positionSide": position_side
    }
    
    sl_result = request_binance("POST", "/fapi/v1/order", sl_params)
    
    if not sl_result or "error" in sl_result:
        logger.error(f"❌ Échec SL")
        sl_order_id = None
    else:
        sl_order_id = sl_result.get("orderId")
        logger.info(f"✅ SL: {sl_order_id}")
    
    # Placer TP
    tp_params = {
        "symbol": symbol,
        "side": "SELL" if side == "LONG" else "BUY",
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": tp_price,
        "closePosition": "true",
        "positionSide": position_side
    }
    
    tp_result = request_binance("POST", "/fapi/v1/order", tp_params)
    
    if not tp_result or "error" in tp_result:
        logger.error(f"❌ Échec TP")
        tp_order_id = None
    else:
        tp_order_id = tp_result.get("orderId")
        logger.info(f"✅ TP: {tp_order_id}")
    
    return {
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "sl_price": sl_price,
        "tp_price": tp_price
    }

# ================= GESTION POSITIONS EXISTANTES =================
def get_all_open_positions():
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk")
        
        if not positions:
            return {}
        
        open_positions = {}
        
        for pos in positions:
            qty = float(pos["positionAmt"])
            
            if abs(qty) > 0:
                symbol = pos["symbol"]
                
                open_positions[symbol] = {
                    "symbol": symbol,
                    "quantity": abs(qty),
                    "side": "LONG" if qty > 0 else "SHORT",
                    "entry_price": float(pos["entryPrice"]),
                    "mark_price": float(pos["markPrice"]),
                    "unrealized_pnl": float(pos["unRealizedProfit"]),
                    "leverage": int(pos["leverage"])
                }
        
        return open_positions
        
    except Exception as e:
        logger.error(f"Error get_all_open_positions: {e}")
        return {}

def get_open_orders(symbol=None):
    try:
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        orders = request_binance("GET", "/fapi/v1/openOrders", params)
        
        if not orders:
            return []
        
        return orders
        
    except Exception as e:
        logger.error(f"Error get_open_orders: {e}")
        return []

def has_stop_loss(symbol, side):
    orders = get_open_orders(symbol)
    
    for order in orders:
        if order["type"] in ["STOP_MARKET", "STOP"] and order["positionSide"] == side:
            return True, order
    
    return False, None

def has_take_profit(symbol, side):
    orders = get_open_orders(symbol)
    
    for order in orders:
        if order["type"] in ["TAKE_PROFIT_MARKET", "TAKE_PROFIT"] and order["positionSide"] == side:
            return True, order
    
    return False, None

def add_missing_sl_tp(symbol, position):
    side = position["side"]
    entry_price = position["entry_price"]
    
    logger.warning(f"🛡️ Ajout SL/TP: {symbol} {side}")
    
    symbol_info = get_symbol_info(symbol)
    
    if side == "LONG":
        sl_price = entry_price * (1 - SL_PERCENT)
        tp_price = entry_price * (1 + TP_PERCENT)
    else:
        sl_price = entry_price * (1 + SL_PERCENT)
        tp_price = entry_price * (1 - TP_PERCENT)
    
    sl_price = round_price(sl_price, symbol_info)
    tp_price = round_price(tp_price, symbol_info)
    
    has_sl, sl_order = has_stop_loss(symbol, side)
    
    if not has_sl:
        logger.warning(f"   ⚠️ Pas de SL")
        
        sl_params = {
            "symbol": symbol,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": sl_price,
            "closePosition": "true",
            "positionSide": side
        }
        
        sl_result = request_binance("POST", "/fapi/v1/order", sl_params)
        
        if sl_result:
            logger.info(f"   ✅ SL @ {sl_price}")
        else:
            logger.error(f"   ❌ Échec SL")
    else:
        logger.info(f"   ✅ SL existe @ {float(sl_order['stopPrice'])}")
        sl_price = float(sl_order["stopPrice"])
    
    has_tp, tp_order = has_take_profit(symbol, side)
    
    if not has_tp:
        logger.warning(f"   ⚠️ Pas de TP")
        
        tp_params = {
            "symbol": symbol,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp_price,
            "closePosition": "true",
            "positionSide": side
        }
        
        tp_result = request_binance("POST", "/fapi/v1/order", tp_params)
        
        if tp_result:
            logger.info(f"   ✅ TP @ {tp_price}")
        else:
            logger.error(f"   ❌ Échec TP")
    else:
        logger.info(f"   ✅ TP existe @ {float(tp_order['stopPrice'])}")
        tp_price = float(tp_order["stopPrice"])
    
    return sl_price, tp_price

def import_existing_positions():
    logger.info("\n" + "="*60)
    logger.info("🔄 IMPORT POSITIONS EXISTANTES")
    logger.info("="*60)
    
    positions = get_all_open_positions()
    
    if not positions:
        logger.info("✅ Aucune position existante")
        return
    
    logger.info(f"📊 {len(positions)} position(s)")
    
    for symbol, position in positions.items():
        logger.info(f"\n📍 {symbol} {position['side']}")
        logger.info(f"   Qty: {position['quantity']}")
        logger.info(f"   Entry: ${position['entry_price']:.6f}")
        logger.info(f"   PnL: ${position['unrealized_pnl']:+.2f}")
        
        if AUTO_ADD_SL_TP:
            sl_price, tp_price = add_missing_sl_tp(symbol, position)
        else:
            has_sl, sl_order = has_stop_loss(symbol, position["side"])
            has_tp, tp_order = has_take_profit(symbol, position["side"])
            
            sl_price = float(sl_order["stopPrice"]) if has_sl else None
            tp_price = float(tp_order["stopPrice"]) if has_tp else None
            
            if not has_sl or not has_tp:
                logger.warning(f"   ⚠️ Position sans protection")
                
                if AUTO_CLOSE_NO_PLAN:
                    logger.critical(f"   🛑 Fermeture")
                    force_close_position(symbol, position["side"])
                    continue
        
        with trade_lock:
            active_trades[symbol] = {
                "symbol": symbol,
                "side": position["side"],
                "entry_price": position["entry_price"],
                "quantity": position["quantity"],
                "margin": BASE_MARGIN_PER_TRADE,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "order_id": "IMPORTED",
                "sl_order_id": None,
                "tp_order_id": None,
                "entry_time": datetime.now(timezone.utc),
                "stars": 0,
                "zone": None,
                "session": "IMPORTED",
                "trailing_active": False,
                "highest_price": position["mark_price"] if position["side"] == "LONG" else None,
                "lowest_price": position["mark_price"] if position["side"] == "SHORT" else None,
                "tp1_hit": False,
                "tp2_hit": False,
                "imported": True
            }
        
        logger.info(f"   ✅ Importée")
        
        msg = f"📍 IMPORTÉE\n{symbol} {position['side']}\nEntry: ${position['entry_price']:.6f}\nSL: ${sl_price:.6f} | TP: ${tp_price:.6f}"
        send_telegram(msg)
    
    logger.info("\n" + "="*60)
    logger.info(f"✅ Import: {len(active_trades)} position(s)")
    logger.info("="*60 + "\n")

def update_stop_loss(symbol, side, new_sl_price):
    try:
        has_sl, sl_order = has_stop_loss(symbol, side)
        
        if has_sl:
            request_binance("DELETE", "/fapi/v1/order", {
                "symbol": symbol,
                "orderId": sl_order["orderId"]
            })
            time.sleep(0.2)
        
        symbol_info = get_symbol_info(symbol)
        new_sl_price = round_price(new_sl_price, symbol_info)
        
        sl_params = {
            "symbol": symbol,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": new_sl_price,
            "closePosition": "true",
            "positionSide": side
        }
        
        result = request_binance("POST", "/fapi/v1/order", sl_params)
        
        if result:
            logger.info(f"✅ SL modifié: {symbol} → ${new_sl_price:.6f}")
            return True
        else:
            logger.error(f"❌ Échec SL")
            return False
            
    except Exception as e:
        logger.error(f"Error update_stop_loss {symbol}: {e}")
        return False

# ================= POSITION RÉELLE =================
def get_actual_position(symbol):
    try:
        positions = request_binance("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        
        if not positions:
            return None
        
        for pos in positions:
            if pos["symbol"] == symbol:
                qty = float(pos["positionAmt"])
                if abs(qty) > 0:
                    return {
                        "symbol": symbol,
                        "quantity": abs(qty),
                        "side": "LONG" if qty > 0 else "SHORT",
                        "entry_price": float(pos["entryPrice"]),
                        "mark_price": float(pos["markPrice"]),
                        "unrealized_pnl": float(pos["unRealizedProfit"])
                    }
        
        return None
        
    except Exception as e:
        logger.error(f"Error get_actual_position {symbol}: {e}")
        return None

def verify_position_closed(symbol, max_attempts=3):
    for attempt in range(max_attempts):
        position = get_actual_position(symbol)
        
        if position is None:
            logger.info(f"✅ {symbol} fermée")
            return True
        
        logger.warning(f"⚠️ {symbol} encore ouverte ({attempt + 1}/{max_attempts})")
        time.sleep(1)
    
    return False

def force_close_position(symbol, side, max_retries=FORCE_CLOSE_MAX_RETRIES):
    logger.critical(f"🚨 FORCE CLOSE: {symbol} {side}")
    
    for retry in range(max_retries):
        try:
            logger.info(f"🔧 Annulation ordres {symbol}")
            request_binance("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
            time.sleep(0.5)
            
            position = get_actual_position(symbol)
            
            if position is None:
                logger.info(f"✅ {symbol} déjà fermée")
                return True
            
            quantity = position["quantity"]
            actual_side = position["side"]
            
            logger.warning(f"⚠️ Position: {quantity} {actual_side}")
            
            close_side = "SELL" if actual_side == "LONG" else "BUY"
            
            close_params = {
                "symbol": symbol,
                "side": close_side,
                "type": "MARKET",
                "quantity": quantity,
                "positionSide": actual_side
            }
            
            result = request_binance("POST", "/fapi/v1/order", close_params)
            
            if not result:
                logger.error(f"❌ Échec ({retry + 1})")
                time.sleep(2)
                continue
            
            logger.info(f"✅ Ordre: {result.get('orderId')}")
            time.sleep(2)
            
            if verify_position_closed(symbol):
                logger.info(f"✅✅ FERMÉE: {symbol}")
                msg = f"🚨 FORCE CLOSE OK\n{symbol} {actual_side}\nQty: {quantity}"
                send_telegram(msg)
                return True
            
        except Exception as e:
            logger.error(f"❌ Error ({retry + 1}): {e}")
            time.sleep(2)
    
    logger.critical(f"🚨🚨 ÉCHEC: {symbol}")
    msg = f"🚨🚨 CRITIQUE\n{symbol} NE PEUT PAS ÊTRE FERMÉ"
    send_telegram(msg)
    
    return False

# ================= CROISSANCE EXPONENTIELLE =================
def update_capital_and_growth():
    global trading_state
    
    try:
        balance_req = request_binance("GET", "/fapi/v2/balance")
        if not balance_req:
            return
        
        usdt = next((b for b in balance_req if b["asset"] == "USDT"), None)
        if not usdt:
            return
        
        current_capital = float(usdt["balance"])
        trading_state["current_capital"] = current_capital
        
        if current_capital > trading_state["peak_capital"]:
            trading_state["peak_capital"] = current_capital
        
        growth_ratio = current_capital / trading_state["starting_capital"]
        new_tier = int(growth_ratio / GROWTH_MULTIPLIER_THRESHOLD)
        
        if new_tier > trading_state["growth_tier"]:
            old_tier = trading_state["growth_tier"]
            trading_state["growth_tier"] = new_tier
            trading_state["current_risk_multiplier"] = 1.0 + (new_tier * RISK_INCREASE_PERCENT)
            trading_state["current_margin_per_trade"] = BASE_MARGIN_PER_TRADE * trading_state["current_risk_multiplier"]
            trading_state["current_max_positions"] = MAX_POSITIONS + new_tier
            
            msg = f"🚀 TIER {new_tier}!\nCapital: ${current_capital:.2f} (x{growth_ratio:.1f})\nRisk: x{trading_state['current_risk_multiplier']:.2f}\nMarge: ${trading_state['current_margin_per_trade']:.2f}$"
            logger.critical(msg)
            send_telegram(msg)
        
        logger.info(f"💰 ${current_capital:.2f} | Tier {trading_state['growth_tier']} | x{trading_state['current_risk_multiplier']:.2f}")
        
    except Exception as e:
        logger.error(f"Error update_capital: {e}")

def handle_loss_recovery():
    global trading_state
    
    if trading_state["consecutive_losses"] > 0:
        if not trading_state["in_recovery_mode"]:
            trading_state["in_recovery_mode"] = True
            trading_state["last_loss_capital"] = trading_state["current_capital"]
            trading_state["recovery_trades_limit"] = max(1, int(trading_state["current_max_positions"] * 0.5))
            
            msg = f"🛡️ RECOVERY\nPertes: {trading_state['consecutive_losses']}\nTrades: {trading_state['recovery_trades_limit']}"
            logger.warning(msg)
            send_telegram(msg)
    
    if trading_state["in_recovery_mode"]:
        if trading_state["current_capital"] >= trading_state["last_loss_capital"]:
            trading_state["in_recovery_mode"] = False
            trading_state["consecutive_losses"] = 0
            trading_state["recovery_trades_limit"] = trading_state["current_max_positions"]
            
            msg = f"✅ RECOVERY OK\nCapital: ${trading_state['current_capital']:.2f}"
            logger.info(msg)
            send_telegram(msg)

def get_max_positions_allowed():
    if trading_state["in_recovery_mode"]:
        return trading_state["recovery_trades_limit"]
    return trading_state["current_max_positions"]

def get_session_and_requirements():
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    
    if 13 <= hour < 16:
        return "LONDON_NY_OVERLAP", SESSION_STAR_REQUIREMENTS["LONDON_NY_OVERLAP"]
    elif LONDON_OPEN <= hour < LONDON_CLOSE:
        return "LONDON", SESSION_STAR_REQUIREMENTS["LONDON"]
    elif NY_OPEN <= hour < NY_CLOSE:
        return "NY", SESSION_STAR_REQUIREMENTS["NY"]
    elif ASIAN_OPEN <= hour < ASIAN_CLOSE:
        return "ASIAN", SESSION_STAR_REQUIREMENTS["ASIAN"]
    else:
        return "OFF_HOURS", SESSION_STAR_REQUIREMENTS["OFF_HOURS"]

# ================= ZONES CLÉS (simplifié pour espace) =================
def identify_key_zones(symbol, timeframe="15m", lookback=100):
    klines = get_klines_cached(symbol, timeframe, lookback)
    if not klines or len(klines) < lookback:
        return []
    
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    zones = []
    
    for i in range(3, len(highs) - 3):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i-3] and
            highs[i] > highs[i+1] and highs[i] > highs[i+2] and highs[i] > highs[i+3]):
            level = highs[i]
            touches = count_touches(level, highs + lows, tolerance=0.003)
            if touches >= MIN_TOUCHES_ZONE:
                zones.append({"type": "RESISTANCE", "level": level, "touches": touches, "timeframe": timeframe, "strength": touches})
    
    for i in range(3, len(lows) - 3):
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i-3] and
            lows[i] < lows[i+1] and lows[i] < lows[i+2] and lows[i] < lows[i+3]):
            level = lows[i]
            touches = count_touches(level, highs + lows, tolerance=0.003)
            if touches >= MIN_TOUCHES_ZONE:
                zones.append({"type": "SUPPORT", "level": level, "touches": touches, "timeframe": timeframe, "strength": touches})
    
    zones.sort(key=lambda x: x["strength"], reverse=True)
    return zones[:10]

def count_touches(level, prices, tolerance=0.003):
    return sum(1 for price in prices if abs(price - level) / level <= tolerance)

def is_price_near_zone(current_price, zone, tolerance=ZONE_PROXIMITY_PERCENT):
    return abs(current_price - zone["level"]) / zone["level"] <= tolerance

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

def detect_bos(symbol):
    klines = get_klines_cached(symbol, "5m", 50)
    if not klines or len(klines) < 50:
        return None
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    recent_high = max(highs[-20:-2])
    recent_low = min(lows[-20:-2])
    current_price = float(klines[-1][4])
    if current_price > recent_high * 1.002:
        return "BULLISH_BOS"
    elif current_price < recent_low * 0.998:
        return "BEARISH_BOS"
    return None

def score_symbol_key_zones(symbol):
    try:
        current_price = get_current_price_cached(symbol)
        if not current_price:
            return None
        
        zones_m15 = identify_key_zones(symbol, "15m", 100)
        zones_h1 = identify_key_zones(symbol, "1h", 100)
        all_zones = zones_m15 + zones_h1
        
        if not all_zones:
            return None
        
        nearest_zone = min(all_zones, key=lambda z: abs(current_price - z["level"]) / z["level"])
        
        if not is_price_near_zone(current_price, nearest_zone):
            return None
        
        stars = 0
        setup_details = []
        
        if nearest_zone["touches"] >= 3:
            stars += 2
            setup_details.append(f"{nearest_zone['type']} ({nearest_zone['touches']}T)")
        else:
            stars += 1
            setup_details.append(f"{nearest_zone['type']} ({nearest_zone['touches']}T)")
        
        m15_zones = [z for z in zones_m15 if is_price_near_zone(current_price, z, 0.005)]
        h1_zones = [z for z in zones_h1 if is_price_near_zone(current_price, z, 0.005)]
        
        if m15_zones and h1_zones:
            stars += 2
            setup_details.append("M15+H1")
        
        bos = detect_bos(symbol)
        if bos and ((bos == "BULLISH_BOS" and nearest_zone["type"] == "SUPPORT") or 
                    (bos == "BEARISH_BOS" and nearest_zone["type"] == "RESISTANCE")):
            stars += 1
            setup_details.append("BOS")
        
        klines_m1 = get_klines_cached(symbol, "1m", 50)
        if klines_m1 and len(klines_m1) >= 50:
            closes = [float(k[4]) for k in klines_m1]
            ema9 = calculate_ema(closes, 9)
            ema21 = calculate_ema(closes, 21)
            
            if nearest_zone["type"] == "SUPPORT" and ema9 and ema21 and ema9 > ema21:
                stars += 1
                setup_details.append("Trend↗")
            elif nearest_zone["type"] == "RESISTANCE" and ema9 and ema21 and ema9 < ema21:
                stars += 1
                setup_details.append("Trend↘")
            
            rsi = calculate_rsi(closes, 14)
            if rsi:
                if nearest_zone["type"] == "SUPPORT" and 30 < rsi < 50:
                    stars += 1
                    setup_details.append(f"RSI{rsi:.0f}")
                elif nearest_zone["type"] == "RESISTANCE" and 50 < rsi < 70:
                    stars += 1
                    setup_details.append(f"RSI{rsi:.0f}")
        
        side = "LONG" if nearest_zone["type"] == "SUPPORT" else "SHORT"
        session, min_stars_required = get_session_and_requirements()
        
        if stars < min_stars_required:
            return None
        
        return {
            "symbol": symbol,
            "stars": min(stars, 5),
            "side": side,
            "price": current_price,
            "zone": nearest_zone,
            "setup_details": setup_details,
            "session": session
        }
        
    except Exception as e:
        logger.error(f"Error score {symbol}: {e}")
        return None

def scan_all_symbols_zones():
    opportunities = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_symbol = {executor.submit(score_symbol_key_zones, sym): sym for sym in SYMBOLS}
        for future in as_completed(future_to_symbol):
            result = future.result()
            if result:
                opportunities.append(result)
    opportunities.sort(key=lambda x: x["stars"], reverse=True)
    return opportunities

# ================= PLACEMENT TRADE =================
def place_trade_zones(opportunity):
    global active_trades, trading_state
    
    symbol = opportunity["symbol"]
    side = opportunity["side"]
    stars = opportunity["stars"]
    zone = opportunity["zone"]
    session = opportunity["session"]
    
    with trade_lock:
        if symbol in active_trades:
            return False
        
        max_allowed = get_max_positions_allowed()
        if len(active_trades) >= max_allowed:
            logger.info(f"⚠️ MAX {max_allowed} positions")
            return False
    
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"🎯 {symbol} {'⭐' * stars} {side} | {session}")
        logger.info(f"   Tier {trading_state['growth_tier']} | x{trading_state['current_risk_multiplier']:.2f}")
        
        margin = trading_state["current_margin_per_trade"]
        if stars == 5:
            margin *= 1.2
        
        logger.info(f"   Marge: ${margin:.2f}")
        
        request_binance("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
        
        current_price = get_current_price_cached(symbol)
        if not current_price:
            logger.error(f"❌ Prix indisponible")
            return False
        
        notional_value = margin * LEVERAGE
        quantity = notional_value / current_price
        
        logger.info(f"   Prix: ${current_price:.6f} | Qty: {quantity:.6f}")
        
        order_result = place_market_order_with_retry(
            symbol=symbol,
            side="BUY" if side == "LONG" else "SELL",
            quantity=quantity,
            position_side=side,
            margin_budget=margin
        )
        
        if not order_result["success"]:
            logger.error(f"❌ Échec {symbol}")
            return False
        
        order_id = order_result["order_id"]
        fill_price = order_result["fill_price"]
        executed_qty = order_result["executed_qty"]
        margin_used = order_result["margin_used"]
        
        logger.info(f"✅ Position ouverte!")
        logger.info(f"   Fill: ${fill_price:.6f} | Qty: {executed_qty}")
        
        time.sleep(1)
        
        actual_position = get_actual_position(symbol)
        if not actual_position:
            logger.error(f"❌ Position non détectée")
            return False
        
        logger.info(f"✅ Confirmée: {actual_position['quantity']} {actual_position['side']}")
        
        sl_tp_result = place_sl_tp_orders(
            symbol=symbol,
            side=side,
            entry_price=fill_price,
            quantity=executed_qty,
            position_side=side
        )
        
        sl_price = sl_tp_result["sl_price"]
        tp_price = sl_tp_result["tp_price"]
        sl_order_id = sl_tp_result["sl_order_id"]
        tp_order_id = sl_tp_result["tp_order_id"]
        
        with trade_lock:
            active_trades[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry_price": fill_price,
                "quantity": executed_qty,
                "margin": margin_used,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "order_id": order_id,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "entry_time": datetime.now(timezone.utc),
                "stars": stars,
                "zone": zone,
                "session": session,
                "trailing_active": False,
                "highest_price": fill_price if side == "LONG" else None,
                "lowest_price": fill_price if side == "SHORT" else None,
                "tp1_hit": False,
                "tp2_hit": False,
                "imported": False
            }
        
        msg = f"🎯 {symbol} {side}\n{'⭐' * stars}\nEntry: ${fill_price:.6f}\nSL: ${sl_price:.6f} | TP: ${tp_price:.6f}"
        send_telegram(msg)
        
        logger.info(f"{'='*60}\n")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error {symbol}: {e}")
        return False

# ================= MONITORING =================
def monitor_positions():
    global active_trades, trading_state
    
    while True:
        try:
            time.sleep(MONITOR_INTERVAL)
            
            with trade_lock:
                symbols_to_check = list(active_trades.keys())
            
            if not symbols_to_check:
                continue
            
            logger.info(f"\n🔍 MONITORING {len(symbols_to_check)} pos")
            
            for symbol in symbols_to_check:
                trade_info = active_trades.get(symbol)
                if not trade_info:
                    continue
                
                actual_position = get_actual_position(symbol)
                
                if actual_position is None:
                    logger.warning(f"⚠️ {symbol} fermée sur Binance")
                    with trade_lock:
                        if symbol in active_trades:
                            del active_trades[symbol]
                    continue
                
                current_price = get_current_price_cached(symbol)
                if not current_price:
                    continue
                
                side = trade_info["side"]
                entry_price = trade_info["entry_price"]
                sl_price = trade_info["sl_price"]
                tp_price = trade_info["tp_price"]
                
                if side == "LONG":
                    pnl_percent = (current_price - entry_price) / entry_price
                else:
                    pnl_percent = (entry_price - current_price) / entry_price
                
                pnl_usdt = pnl_percent * trade_info["margin"] * LEVERAGE
                
                duration_min = (datetime.now(timezone.utc) - trade_info["entry_time"]).total_seconds() / 60
                
                if duration_min > 30 and abs(pnl_percent) < STALE_TRADE_THRESHOLD:
                    logger.warning(f"⏱️ STALE: {symbol}")
                    force_close_and_cleanup(symbol, side, "STALE")
                    continue
                
                if duration_min > MAX_TRADE_DURATION_MIN:
                    logger.warning(f"⏱️ MAX DURATION: {symbol}")
                    force_close_and_cleanup(symbol, side, "MAX_DURATION")
                    continue
                
                should_close = False
                close_reason = None
                
                if side == "LONG":
                    if current_price <= sl_price * (1 + EMERGENCY_SL_BUFFER):
                        should_close = True
                        close_reason = "SL_HIT"
                    elif current_price >= tp_price:
                        should_close = True
                        close_reason = "TP_HIT"
                else:
                    if current_price >= sl_price * (1 - EMERGENCY_SL_BUFFER):
                        should_close = True
                        close_reason = "SL_HIT"
                    elif current_price <= tp_price:
                        should_close = True
                        close_reason = "TP_HIT"
                
                if should_close:
                    logger.critical(f"🚨 {close_reason}: {symbol}")
                    force_close_and_cleanup(symbol, side, close_reason)
                    continue
                
                if ENABLE_PARTIAL_TP and not trade_info.get("imported", False):
                    handle_partial_tp(symbol, current_price, trade_info)
                
                if ENABLE_TRAILING_STOP:
                    update_trailing_stop_dynamic(symbol, current_price, trade_info)
                
                logger.info(f"📊 {symbol} | ${current_price:.6f} | ${pnl_usdt:+.2f} ({pnl_percent*100:+.2f}%)")
        
        except Exception as e:
            logger.error(f"Error monitor: {e}")

def update_trailing_stop_dynamic(symbol, current_price, trade_info):
    side = trade_info["side"]
    entry_price = trade_info["entry_price"]
    
    if side == "LONG":
        pnl = (current_price - entry_price) / entry_price
        
        if pnl >= TRAILING_ACTIVATION and not trade_info["trailing_active"]:
            trade_info["trailing_active"] = True
            trade_info["highest_price"] = current_price
            logger.info(f"🔄 Trailing: {symbol}")
        
        if trade_info["trailing_active"]:
            if current_price > trade_info["highest_price"]:
                trade_info["highest_price"] = current_price
            
            new_sl = trade_info["highest_price"] * (1 - TRAILING_DISTANCE)
            
            if new_sl > trade_info["sl_price"]:
                if update_stop_loss(symbol, side, new_sl):
                    trade_info["sl_price"] = round(new_sl, 6)
    
    else:
        pnl = (entry_price - current_price) / entry_price
        
        if pnl >= TRAILING_ACTIVATION and not trade_info["trailing_active"]:
            trade_info["trailing_active"] = True
            trade_info["lowest_price"] = current_price
            logger.info(f"🔄 Trailing: {symbol}")
        
        if trade_info["trailing_active"]:
            if current_price < trade_info["lowest_price"]:
                trade_info["lowest_price"] = current_price
            
            new_sl = trade_info["lowest_price"] * (1 + TRAILING_DISTANCE)
            
            if new_sl < trade_info["sl_price"]:
                if update_stop_loss(symbol, side, new_sl):
                    trade_info["sl_price"] = round(new_sl, 6)

def handle_partial_tp(symbol, current_price, trade_info):
    side = trade_info["side"]
    entry_price = trade_info["entry_price"]
    
    if side == "LONG":
        pnl_percent = (current_price - entry_price) / entry_price
    else:
        pnl_percent = (entry_price - current_price) / entry_price
    
    if pnl_percent >= TP1_PERCENT and not trade_info["tp1_hit"]:
        logger.info(f"💰 TP1: {symbol}")
        trade_info["tp1_hit"] = True
    elif pnl_percent >= TP2_PERCENT and not trade_info["tp2_hit"]:
        logger.info(f"💰 TP2: {symbol}")
        trade_info["tp2_hit"] = True

def force_close_and_cleanup(symbol, side, reason):
    global active_trades, trade_history, daily_stats, trading_state
    
    with trade_lock:
        trade_info = active_trades.get(symbol)
        if not trade_info:
            return
    
    try:
        success = force_close_position(symbol, side)
        
        if not success:
            logger.critical(f"🚨 ÉCHEC: {symbol}")
            return
        
        current_price = get_current_price_cached(symbol)
        if not current_price:
            current_price = trade_info["entry_price"]
        
        entry_price = trade_info["entry_price"]
        margin = trade_info["margin"]
        
        if side == "LONG":
            pnl_percent = (current_price - entry_price) / entry_price
        else:
            pnl_percent = (entry_price - current_price) / entry_price
        
        pnl_usdt = pnl_percent * margin * LEVERAGE
        trading_state["current_capital"] += pnl_usdt
        
        duration = (datetime.now(timezone.utc) - trade_info["entry_time"]).total_seconds() / 60
        
        trade_history.append({
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": current_price,
            "pnl_usdt": pnl_usdt,
            "pnl_percent": pnl_percent * 100,
            "duration_min": duration,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        today = datetime.now(timezone.utc).date().isoformat()
        daily_stats[today]["trades"] += 1
        if pnl_usdt > 0:
            daily_stats[today]["wins"] += 1
            trading_state["consecutive_losses"] = 0
        else:
            daily_stats[today]["losses"] += 1
            trading_state["consecutive_losses"] += 1
        daily_stats[today]["pnl"] += pnl_usdt
        
        with trade_lock:
            del active_trades[symbol]
        
        emoji = "✅" if pnl_usdt > 0 else "❌"
        logger.info(f"{emoji} {symbol} | {reason} | ${pnl_usdt:+.2f}")
        
        if pnl_usdt < 0:
            handle_loss_recovery()
        
        update_capital_and_growth()
        
    except Exception as e:
        logger.error(f"Error cleanup {symbol}: {e}")

def get_trading_stats():
    total_trades = len(trade_history)
    wins = sum(1 for t in trade_history if t["pnl_usdt"] > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(t["pnl_usdt"] for t in trade_history)
    today = datetime.now(timezone.utc).date().isoformat()
    today_stats = daily_stats[today]
    session, min_stars = get_session_and_requirements()
    
    return {
        "capital_initial": f"{INITIAL_CAPITAL:.2f}$",
        "capital_actuel": f"{trading_state['current_capital']:.2f}$",
        "profit_total": f"{total_pnl:+.2f}$",
        "profit_pourcent": f"{(total_pnl/INITIAL_CAPITAL*100):+.1f}%" if INITIAL_CAPITAL > 0 else "0%",
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": f"{win_rate:.1f}%",
        "growth_tier": trading_state["growth_tier"],
        "risk_multiplier": f"x{trading_state['current_risk_multiplier']:.2f}",
        "marge_par_trade": f"{trading_state['current_margin_per_trade']:.2f}$",
        "max_positions": trading_state["current_max_positions"],
        "active_positions": len(active_trades),
        "in_recovery": trading_state["in_recovery_mode"],
        "consecutive_losses": trading_state["consecutive_losses"],
        "session": session,
        "min_stars_required": min_stars,
        "today_trades": today_stats["trades"],
        "today_pnl": f"{today_stats['pnl']:.2f}$"
    }

# ================= MAIN LOOP =================
def trading_loop():
    logger.info("🚀 ROBOTKING M1 PRO - COMPLETE VERSION")
    logger.info("✅ SL/TP basés sur prix réel")
    logger.info("✅ Retry auto si rejeté")
    logger.info("✅ Ajustement qty/marge auto")
    logger.info("✅ Import positions existantes")
    logger.info("✅ Force close si Binance bug")
    logger.info("✅ Trailing stop dynamique")
    logger.info("✅ Croissance exponentielle")
    
    if MANAGE_EXISTING_POSITIONS:
        import_existing_positions()
    
    while True:
        try:
            update_capital_and_growth()
            handle_loss_recovery()
            
            session, min_stars = get_session_and_requirements()
            
            with trade_lock:
                nb_positions = len(active_trades)
            
            max_allowed = get_max_positions_allowed()
            
            logger.info(f"\n🔍 SCAN | {session} | {min_stars}⭐ | {nb_positions}/{max_allowed}")
            
            opportunities = scan_all_symbols_zones()
            
            if not opportunities:
                logger.info("❌ Aucune opportunité")
                time.sleep(SCAN_INTERVAL_NO_TRADES if nb_positions == 0 else SCAN_INTERVAL_ACTIVE)
                continue
            
            logger.info(f"\n📈 Top {min(5, len(opportunities))}:")
            for i, opp in enumerate(opportunities[:5], 1):
                logger.info(f"{i}. {opp['symbol']} {'⭐'*opp['stars']} {opp['side']}")
            
            if nb_positions < max_allowed:
                best = opportunities[0]
                with trade_lock:
                    if best["symbol"] not in active_trades:
                        success = place_trade_zones(best)
                        if success:
                            logger.info(f"✅ {best['symbol']}")
                        else:
                            logger.warning(f"❌ {best['symbol']}")
            else:
                logger.info(f"⚠️ MAX {max_allowed} positions")
            
            sleep_time = SCAN_INTERVAL_ACTIVE if nb_positions > 0 else SCAN_INTERVAL_NO_TRADES
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Error loop: {e}")
            time.sleep(SCAN_INTERVAL_NO_TRADES)

# ================= DÉMARRAGE =================
if __name__ == "__main__":
    monitor_thread = threading.Thread(target=monitor_positions, daemon=True)
    monitor_thread.start()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    try:
        trading_loop()
    except KeyboardInterrupt:
        logger.info("\n🛑 Arrêt demandé")
        logger.info("👋 RobotKing arrêté")

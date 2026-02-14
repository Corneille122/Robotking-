import time, hmac, hashlib, requests, threading, os, json, sys
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from flask import Flask
import logging
from tabulate import tabulate

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

# ================= SERVEUR WEB =================
app = Flask(__name__)

@app.route('/')
def home():
    """Dashboard web temps réel"""
    stats = get_trading_stats()
    positions_html = get_positions_html()
    alerts_html = get_alerts_html()
    
    return f"""
    <html>
    <head>
        <title>RobotKing Pro Dashboard</title>
        <meta http-equiv="refresh" content="5">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Courier New', monospace;
                background: linear-gradient(135deg, #0a0e27 0%, #1a1f3a 100%);
                color: #00ff88;
                padding: 20px;
            }}
            .container {{ max-width: 1400px; margin: 0 auto; }}
            h1 {{
                color: #00ff88;
                text-shadow: 0 0 20px #00ff88;
                border-bottom: 3px solid #00ff88;
                padding: 20px 0;
                margin-bottom: 30px;
                font-size: 2em;
                text-align: center;
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 30px;
            }}
            .stat-card {{
                background: rgba(21, 27, 56, 0.9);
                padding: 20px;
                border-radius: 10px;
                border: 2px solid #00ff88;
                box-shadow: 0 0 20px rgba(0, 255, 136, 0.3);
            }}
            .stat-label {{ color: #8899aa; font-size: 0.9em; }}
            .stat-value {{ 
                color: #00ff88; 
                font-size: 1.8em; 
                font-weight: bold;
                margin-top: 5px;
            }}
            .position-table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: rgba(21, 27, 56, 0.9);
                border: 2px solid #00ff88;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 0 30px rgba(0, 255, 136, 0.3);
            }}
            .position-table th {{
                background: #1e2645;
                color: #00ff88;
                padding: 15px 10px;
                text-align: left;
                font-weight: bold;
                border-bottom: 2px solid #00ff88;
            }}
            .position-table td {{
                padding: 12px 10px;
                border-bottom: 1px solid #2a3555;
            }}
            .position-table tr:hover {{
                background: rgba(0, 255, 136, 0.1);
            }}
            .profit {{ color: #00ff88; font-weight: bold; }}
            .loss {{ color: #ff3366; font-weight: bold; }}
            .neutral {{ color: #ffaa00; }}
            .trailing-active {{ 
                color: #ffaa00; 
                animation: pulse 2s infinite;
                font-weight: bold;
            }}
            @keyframes pulse {{
                0%, 100% {{ opacity: 1; transform: scale(1); }}
                50% {{ opacity: 0.7; transform: scale(1.1); }}
            }}
            .status-ok {{ color: #00ff88; }}
            .status-warning {{ color: #ffaa00; animation: blink 1s infinite; }}
            .status-error {{ color: #ff3366; animation: blink 0.5s infinite; }}
            @keyframes blink {{
                0%, 50% {{ opacity: 1; }}
                51%, 100% {{ opacity: 0.3; }}
            }}
            .alerts {{
                background: rgba(255, 51, 102, 0.1);
                border: 2px solid #ff3366;
                border-radius: 10px;
                padding: 15px;
                margin: 20px 0;
            }}
            .alert-item {{
                padding: 8px;
                margin: 5px 0;
                border-left: 4px solid #ff3366;
                padding-left: 15px;
            }}
            .sequence-info {{
                background: rgba(30, 38, 69, 0.9);
                border: 2px solid #00ff88;
                border-radius: 10px;
                padding: 20px;
                margin: 20px 0;
            }}
            .footer {{
                text-align: center;
                margin-top: 30px;
                padding: 20px;
                color: #8899aa;
                border-top: 1px solid #2a3555;
            }}
            .small-text {{ font-size: 0.8em; color: #8899aa; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 ROBOTKING M1 PRO - LIVE DASHBOARD</h1>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">💰 Capital</div>
                    <div class="stat-value">{stats.get('capital', '0.00')}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">📈 Profit Total</div>
                    <div class="stat-value {'profit' if stats.get('profit_raw', 0) > 0 else 'loss'}">{stats.get('profit', '0.00')}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">🎯 Win Rate</div>
                    <div class="stat-value">{stats.get('win_rate', '0%')}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">🔢 Trades</div>
                    <div class="stat-value">{stats.get('trades', 0)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">📊 Positions</div>
                    <div class="stat-value">{stats.get('active_positions', 0)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">🎬 Séquence</div>
                    <div class="stat-value">{stats.get('current_seq', '0/4')}</div>
                </div>
            </div>

            {alerts_html}
            
            <h2 style="color: #00ff88; margin: 30px 0 15px 0;">📊 POSITIONS EN TEMPS RÉEL</h2>
            {positions_html}
            
            <div class="sequence-info">
                <h3 style="color: #00ff88; margin-bottom: 15px;">🎬 SÉQUENCE ACTUELLE</h3>
                <p><strong>Numéro:</strong> #{stats.get('sequence_number', 0)}</p>
                <p><strong>Progression:</strong> {stats.get('current_seq', '0/4')}</p>
                <p><strong>PnL Séquence:</strong> <span class="{'profit' if stats.get('sequence_pnl_raw', 0) > 0 else 'loss'}">{stats.get('sequence_pnl', '0.00')}</span></p>
                <p><strong>Status:</strong> {stats.get('sequence_status', 'En cours')}</p>
            </div>
            
            <div class="footer">
                <p>🔄 Mise à jour automatique toutes les 5 secondes</p>
                <p class="small-text">Last update: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>
        </div>
    </body>
    </html>
    """

def get_positions_html():
    """Génère le tableau HTML des positions"""
    with trade_lock:
        positions = list(active_trades.values())
    
    if not positions:
        return '<p style="text-align: center; padding: 40px; color: #8899aa;">Aucune position active</p>'
    
    rows = []
    for pos in positions:
        current_price = get_current_price_cached(pos['symbol'])
        if not current_price:
            continue
        
        # Calculs
        entry = pos['entry_price']
        sl = pos['sl_price']
        tp = pos['tp_price']
        side = pos['side']
        
        if side == "LONG":
            pnl_pct = (current_price - entry) / entry * 100
            dist_sl = (current_price - sl) / current_price * 100
            dist_tp = (tp - current_price) / current_price * 100
            potential_gain = tp - current_price
            potential_loss = current_price - sl
        else:
            pnl_pct = (entry - current_price) / entry * 100
            dist_sl = (sl - current_price) / current_price * 100
            dist_tp = (current_price - tp) / current_price * 100
            potential_gain = current_price - tp
            potential_loss = sl - current_price
        
        pnl_usd = pnl_pct / 100 * pos['margin'] * LEVERAGE
        
        current_rr = potential_gain / potential_loss if potential_loss > 0 else 0
        initial_rr = (TP_PERCENT / SL_PERCENT)
        
        # Status ordres
        sl_status = check_order_exists(pos['symbol'], pos.get('sl_order_id'))
        tp_status = check_order_exists(pos['symbol'], pos.get('tp_order_id'))
        
        # Trailing status
        trailing_icon = '🟡 ACTIF' if pos.get('trailing_active') else '⚪ OFF'
        
        # Durée
        duration = (datetime.now(timezone.utc) - pos['entry_time']).total_seconds() / 60
        
        # Couleurs
        pnl_class = 'profit' if pnl_usd > 0 else 'loss' if pnl_usd < 0 else 'neutral'
        sl_class = 'status-ok' if sl_status else 'status-error'
        tp_class = 'status-ok' if tp_status else 'status-error'
        
        rows.append(f"""
        <tr>
            <td><strong>{pos['symbol']}</strong></td>
            <td><span class="{'profit' if side == 'LONG' else 'loss'}">{side}</span></td>
            <td>${entry:.2f}</td>
            <td><strong>${current_price:.2f}</strong></td>
            <td>
                ${sl:.2f}<br>
                <span class="small-text">{dist_sl:+.2f}%</span>
            </td>
            <td>
                ${tp:.2f}<br>
                <span class="small-text">{dist_tp:+.2f}%</span>
            </td>
            <td class="{pnl_class}">
                ${pnl_usd:+.2f}<br>
                <span class="small-text">{pnl_pct:+.2f}%</span>
            </td>
            <td>
                <strong>{current_rr:.2f}:1</strong><br>
                <span class="small-text">({initial_rr:.1f}:1)</span>
            </td>
            <td>
                <span class="{'trailing-active' if pos.get('trailing_active') else ''}">{trailing_icon}</span>
            </td>
            <td>
                <span class="{sl_class}">{'✅' if sl_status else '❌ DISPARU'}</span><br>
                <span class="{tp_class}">{'✅' if tp_status else '❌ DISPARU'}</span>
            </td>
            <td>
                {'⭐' * pos.get('stars', 0)}<br>
                <span class="small-text">{duration:.1f}min</span>
            </td>
        </tr>
        """)
    
    table = f"""
    <table class="position-table">
        <thead>
            <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Current</th>
                <th>SL<br><span class="small-text">Distance</span></th>
                <th>TP<br><span class="small-text">Distance</span></th>
                <th>PnL</th>
                <th>R:R<br><span class="small-text">Now/Init</span></th>
                <th>Trailing</th>
                <th>Orders<br><span class="small-text">SL/TP</span></th>
                <th>Info</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
    
    return table

def get_alerts_html():
    """Génère les alertes HTML"""
    alerts = []
    
    with trade_lock:
        for symbol, pos in active_trades.items():
            current_price = get_current_price_cached(symbol)
            if not current_price:
                continue
            
            side = pos['side']
            sl = pos['sl_price']
            tp = pos['tp_price']
            entry = pos['entry_price']
            
            # Distance au SL
            if side == "LONG":
                dist_sl_pct = (current_price - sl) / current_price * 100
                dist_tp_pct = (tp - current_price) / current_price * 100
                pnl_pct = (current_price - entry) / entry * 100
            else:
                dist_sl_pct = (sl - current_price) / current_price * 100
                dist_tp_pct = (current_price - tp) / current_price * 100
                pnl_pct = (entry - current_price) / entry * 100
            
            # Alerte proche SL
            if dist_sl_pct < 0.3:
                alerts.append(f"🚨 {symbol}: TRÈS PROCHE DU SL ({dist_sl_pct:.2f}%)")
            elif dist_sl_pct < 0.5:
                alerts.append(f"⚠️ {symbol}: Approche SL ({dist_sl_pct:.2f}%)")
            
            # Alerte proche TP
            if dist_tp_pct < 0.5:
                alerts.append(f"💰 {symbol}: Proche TP ({dist_tp_pct:.2f}%)")
            
            # Alerte trailing pas activé
            if pnl_pct > TRAILING_ACTIVATION * 100 and not pos.get('trailing_active'):
                alerts.append(f"💡 {symbol}: Devrait activer trailing (+{pnl_pct:.2f}%)")
            
            # Alerte ordres disparus
            if not check_order_exists(symbol, pos.get('sl_order_id')):
                alerts.append(f"🚨🚨 {symbol}: ORDRE SL DISPARU!")
            
            if not check_order_exists(symbol, pos.get('tp_order_id')):
                alerts.append(f"⚠️ {symbol}: Ordre TP disparu")
            
            # Alerte durée
            duration = (datetime.now(timezone.utc) - pos['entry_time']).total_seconds() / 60
            if duration > MAX_TRADE_DURATION_MIN * 0.8:
                alerts.append(f"⏰ {symbol}: Près du timeout ({duration:.0f}/{MAX_TRADE_DURATION_MIN}min)")
    
    if not alerts:
        return ''
    
    alerts_html = '<div class="alerts"><h3 style="color: #ff3366; margin-bottom: 10px;">⚠️ ALERTES</h3>'
    for alert in alerts:
        alerts_html += f'<div class="alert-item">{alert}</div>'
    alerts_html += '</div>'
    
    return alerts_html

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

# ========== PARAMÈTRES ==========
LEVERAGE = 20
INITIAL_CAPITAL = 4.0
TRADES_PER_SEQUENCE = 4
FIXED_MARGIN_PER_TRADE = 0.6
MAX_SAME_DIRECTION_IN_SEQUENCE = 3
MIN_DIFFERENT_PAIRS = 3
PAUSE_AFTER_SEQUENCE_MIN = 5
PAUSE_AFTER_LOSSES = 3
PAUSE_DURATION_MIN = 30

SL_PERCENT = 0.008
TP_PERCENT = 0.025

ENABLE_TRAILING_STOP = True
TRAILING_ACTIVATION = 0.015
TRAILING_DISTANCE = 0.008

ENABLE_PARTIAL_TP = True
TP1_PERCENT = 0.012
TP2_PERCENT = 0.020
TP3_PERCENT = 0.030

MAX_TRADE_DURATION_MIN = 30
STALE_TRADE_THRESHOLD = 0.002

MAX_ORDER_RETRIES = 3
QUANTITY_INCREMENT_FACTOR = 1.15
MIN_MARGIN_INCREMENT = 0.1

GROWTH_MULTIPLIER_THRESHOLD = 6.0
RISK_INCREASE_PERCENT = 0.50

EMERGENCY_SL_BUFFER = 0.002
FORCE_CLOSE_MAX_RETRIES = 5

MANAGE_EXISTING_POSITIONS = True
AUTO_ADD_SL_TP = True
AUTO_CLOSE_NO_PLAN = False

SESSION_STAR_REQUIREMENTS = {
    "LONDON_NY_OVERLAP": {"min_stars": 4, "ideal_stars": 5, "win_rate_threshold": 0.65},
    "LONDON": {"min_stars": 4, "ideal_stars": 5, "win_rate_threshold": 0.63},
    "NY": {"min_stars": 4, "ideal_stars": 5, "win_rate_threshold": 0.62},
    "ASIAN": {"min_stars": 5, "ideal_stars": 5, "win_rate_threshold": 0.55},
    "OFF_HOURS": {"min_stars": 5, "ideal_stars": 5, "win_rate_threshold": 0.50}
}

MIN_TOUCHES_ZONE = 3
ZONE_PROXIMITY_PERCENT = 0.002
REQUIRE_MTF_CONFLUENCE = True

SCAN_INTERVAL_NO_TRADES = 10
SCAN_INTERVAL_ACTIVE = 20
MONITOR_INTERVAL = 3
VERIFY_ORDERS_INTERVAL = 10
RECONCILE_INTERVAL = 30
DISPLAY_INTERVAL = 5
MAX_WORKERS = 6

LONDON_OPEN = 7
LONDON_CLOSE = 16
NY_OPEN = 13
NY_CLOSE = 22
ASIAN_OPEN = 0
ASIAN_CLOSE = 9

CACHE_DURATION = 5
price_cache = {}
klines_cache = {}
symbol_info_cache = {}

TESTNET_MODE = os.environ.get("TESTNET_MODE", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# ========== VARIABLES GLOBALES ==========
sequence_state = {
    "current_sequence": [],
    "sequence_number": 0,
    "sequence_pnl": 0.0,
    "sequence_start_time": None,
    "sequence_complete": True,
    "pause_until": None,
    "sequences_today": 0,
    "best_sequence_pnl": 0.0,
    "worst_sequence_pnl": 0.0
}

active_trades = {}
trade_history = []

trading_state = {
    "starting_capital": INITIAL_CAPITAL,
    "current_capital": INITIAL_CAPITAL,
    "peak_capital": INITIAL_CAPITAL,
    "growth_tier": 0,
    "current_risk_multiplier": 1.0,
    "current_margin_per_trade": FIXED_MARGIN_PER_TRADE,
    "consecutive_losses": 0,
    "in_recovery_mode": False,
    "recovery_active": False,
    "last_loss_capital": 0.0,
    "daily_max_reached": False
}

daily_stats = defaultdict(lambda: {
    "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
    "sequences": 0, "best_sequence": 0.0, "worst_sequence": 0.0
})

# 🆕 Tracking des alertes
active_alerts = []
last_telegram_summary = datetime.now(timezone.utc)

trade_lock = threading.Lock()
api_call_times = []
api_lock = threading.Lock()

MAX_CALLS_PER_MINUTE = 1200
RATE_LIMIT_WINDOW = 60

# ================= FONCTIONS DE BASE =================
def wait_for_rate_limit():
    global api_call_times
    with api_lock:
        now = time.time()
        api_call_times = [t for t in api_call_times if now - t < RATE_LIMIT_WINDOW]
        if len(api_call_times) >= MAX_CALLS_PER_MINUTE * 0.8:
            sleep_time = RATE_LIMIT_WINDOW - (now - api_call_times[0])
            if sleep_time > 0:
                logger.warning(f"⚠️ Rate limit {sleep_time:.1f}s")
                time.sleep(sleep_time)
                api_call_times.clear()
        api_call_times.append(now)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram: {e}")

def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None, max_retries=3):
    if params is None:
        params = {}
    
    if DRY_RUN and method == "POST":
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
                time.sleep(retry_after)
            else:
                error_data = resp.json() if resp.text else {}
                logger.error(f"API {resp.status_code}: {error_data}")
                return {"error": error_data, "status_code": resp.status_code}
        except Exception as e:
            logger.error(f"Request: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None

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

# ================= 🆕 VÉRIFICATION ORDRES BINANCE =================
def check_order_exists(symbol, order_id):
    """Vérifie si un ordre existe encore sur Binance"""
    if not order_id or order_id == "IMPORTED":
        return True  # Assume OK si pas d'order_id
    
    try:
        orders = request_binance("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        
        if not orders:
            return False
        
        for order in orders:
            if str(order.get("orderId")) == str(order_id):
                return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error check_order_exists: {e}")
        return True  # Assume OK en cas d'erreur pour éviter faux positifs

def verify_and_fix_orders():
    """
    🆕 THREAD: Vérifie toutes les 10s que les ordres SL/TP existent
    Recrée automatiquement si disparus
    """
    while True:
        try:
            time.sleep(VERIFY_ORDERS_INTERVAL)
            
            with trade_lock:
                positions = list(active_trades.items())
            
            for symbol, pos in positions:
                # Vérifier SL
                sl_exists = check_order_exists(symbol, pos.get('sl_order_id'))
                
                if not sl_exists and pos.get('sl_order_id'):
                    logger.critical(f"🚨🚨 {symbol}: ORDRE SL DISPARU! Recréation...")
                    
                    # Alerte Telegram immédiate
                    send_telegram(f"🚨 ALERTE CRITIQUE\n{symbol}: SL disparu!\nRecréation en cours...")
                    
                    # Recréer le SL
                    recreate_stop_loss(symbol, pos)
                
                # Vérifier TP
                tp_exists = check_order_exists(symbol, pos.get('tp_order_id'))
                
                if not tp_exists and pos.get('tp_order_id'):
                    logger.warning(f"⚠️ {symbol}: Ordre TP disparu, recréation...")
                    recreate_take_profit(symbol, pos)
        
        except Exception as e:
            logger.error(f"Error verify_orders: {e}")

def recreate_stop_loss(symbol, pos):
    """Recrée un ordre SL disparu"""
    try:
        side = pos['side']
        sl_price = pos['sl_price']
        
        symbol_info = get_symbol_info(symbol)
        sl_price = round_price(sl_price, symbol_info)
        
        sl_params = {
            "symbol": symbol,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": sl_price,
            "closePosition": "true",
            "positionSide": side
        }
        
        result = request_binance("POST", "/fapi/v1/order", sl_params)
        
        if result and "orderId" in result:
            with trade_lock:
                active_trades[symbol]["sl_order_id"] = result["orderId"]
            logger.info(f"✅ {symbol}: SL recréé @ {sl_price}")
        else:
            logger.error(f"❌ {symbol}: Échec recréation SL")
            # Force close si échec
            force_close_position(symbol, side)
            
    except Exception as e:
        logger.error(f"Error recreate_sl {symbol}: {e}")

def recreate_take_profit(symbol, pos):
    """Recrée un ordre TP disparu"""
    try:
        side = pos['side']
        tp_price = pos['tp_price']
        
        symbol_info = get_symbol_info(symbol)
        tp_price = round_price(tp_price, symbol_info)
        
        tp_params = {
            "symbol": symbol,
            "side": "SELL" if side == "LONG" else "BUY",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp_price,
            "closePosition": "true",
            "positionSide": side
        }
        
        result = request_binance("POST", "/fapi/v1/order", tp_params)
        
        if result and "orderId" in result:
            with trade_lock:
                active_trades[symbol]["tp_order_id"] = result["orderId"]
            logger.info(f"✅ {symbol}: TP recréé @ {tp_price}")
        else:
            logger.warning(f"⚠️ {symbol}: Échec recréation TP")
            
    except Exception as e:
        logger.error(f"Error recreate_tp {symbol}: {e}")

# ================= 🆕 RÉCONCILIATION BINANCE =================
def reconcile_with_binance():
    """
    🆕 THREAD: Réconciliation toutes les 30s
    - Vérifie positions Binance vs local
    - Nettoie ordres orphelins
    - Met à jour capital réel
    """
    while True:
        try:
            time.sleep(RECONCILE_INTERVAL)
            
            logger.info("🔄 Réconciliation Binance...")
            
            # 1. Récupérer positions réelles
            real_positions = get_all_open_positions()
            
            with trade_lock:
                tracked_symbols = set(active_trades.keys())
            
            real_symbols = set(real_positions.keys())
            
            # 2. Positions sur Binance mais pas dans tracking
            missing = real_symbols - tracked_symbols
            if missing:
                logger.warning(f"⚠️ Positions non trackées: {missing}")
                for symbol in missing:
                    if MANAGE_EXISTING_POSITIONS:
                        import_single_position(symbol, real_positions[symbol])
            
            # 3. Positions trackées mais fermées sur Binance
            closed = tracked_symbols - real_symbols
            if closed:
                logger.info(f"✅ Positions fermées: {closed}")
                with trade_lock:
                    for symbol in closed:
                        if symbol in active_trades:
                            del active_trades[symbol]
            
            # 4. Vérifier quantités
            for symbol in tracked_symbols & real_symbols:
                local_qty = active_trades[symbol]['quantity']
                real_qty = real_positions[symbol]['quantity']
                
                if abs(local_qty - real_qty) > 0.001:
                    logger.warning(f"⚠️ {symbol}: Qty mismatch Local={local_qty} Real={real_qty}")
                    with trade_lock:
                        active_trades[symbol]['quantity'] = real_qty
            
            # 5. Nettoyer ordres orphelins
            cleanup_orphan_orders()
            
            # 6. Update capital réel
            update_capital_and_growth()
            
        except Exception as e:
            logger.error(f"Error reconcile: {e}")

def get_all_open_positions():
    """Récupère toutes les positions ouvertes sur Binance"""
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

def import_single_position(symbol, position):
    """Importe une position non trackée"""
    try:
        logger.info(f"📍 Import position: {symbol}")
        
        # Vérifier/ajouter SL/TP
        if AUTO_ADD_SL_TP:
            sl_price, tp_price = add_missing_sl_tp(symbol, position)
        else:
            sl_price = None
            tp_price = None
        
        with trade_lock:
            active_trades[symbol] = {
                "symbol": symbol,
                "side": position["side"],
                "entry_price": position["entry_price"],
                "quantity": position["quantity"],
                "margin": FIXED_MARGIN_PER_TRADE,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "order_id": "RECONCILED",
                "sl_order_id": None,
                "tp_order_id": None,
                "entry_time": datetime.now(timezone.utc),
                "stars": 0,
                "zone": None,
                "session": "RECONCILED",
                "trailing_active": False,
                "highest_price": position["mark_price"] if position["side"] == "LONG" else None,
                "lowest_price": position["mark_price"] if position["side"] == "SHORT" else None,
                "tp1_hit": False,
                "tp2_hit": False,
                "imported": True
            }
        
        logger.info(f"✅ {symbol} importé")
        
    except Exception as e:
        logger.error(f"Error import_single_position {symbol}: {e}")

def cleanup_orphan_orders():
    """Nettoie les ordres orphelins (sans position associée)"""
    try:
        # Récupérer tous les ordres ouverts
        all_orders = request_binance("GET", "/fapi/v1/openOrders")
        
        if not all_orders:
            return
        
        with trade_lock:
            tracked_symbols = set(active_trades.keys())
        
        for order in all_orders:
            symbol = order["symbol"]
            order_id = order["orderId"]
            
            # Si ordre pour un symbol non tracké → orphelin
            if symbol not in tracked_symbols:
                logger.warning(f"🧹 Ordre orphelin détecté: {symbol} #{order_id}")
                
                # Annuler
                request_binance("DELETE", "/fapi/v1/order", {
                    "symbol": symbol,
                    "orderId": order_id
                })
                
                logger.info(f"✅ Ordre orphelin annulé: {symbol}")
        
    except Exception as e:
        logger.error(f"Error cleanup_orphan_orders: {e}")

# ================= 🆕 AFFICHAGE DASHBOARD TERMINAL =================
def display_terminal_dashboard():
    """
    🆕 THREAD: Affiche dashboard dans le terminal toutes les 5s
    """
    while True:
        try:
            time.sleep(DISPLAY_INTERVAL)
            
            # Clear terminal (optionnel)
            # os.system('clear' if os.name == 'posix' else 'cls')
            
            print("\n" + "="*100)
            print("🤖 ROBOTKING M1 PRO - LIVE DASHBOARD".center(100))
            print("="*100)
            
            # Stats globales
            stats = get_trading_stats()
            
            print(f"\n💰 Capital: {stats['capital']} | 📈 Profit: {stats['profit']} | 🎯 WR: {stats['win_rate']} | 🔢 Trades: {stats['trades']}")
            print(f"🎬 Séquence #{stats.get('sequence_number', 0)}: {stats['current_seq']} | PnL: {stats.get('sequence_pnl', '0.00')}")
            print(f"🛡️ Recovery: {'✅ ACTIF' if stats.get('recovery') else '❌ OFF'} | ⏸️ Pause: {'✅' if stats.get('in_pause') else '❌'}")
            
            # Positions
            with trade_lock:
                positions = list(active_trades.values())
            
            if positions:
                print(f"\n📊 POSITIONS ACTIVES ({len(positions)}):")
                print("-"*100)
                
                table_data = []
                
                for pos in positions:
                    current_price = get_current_price_cached(pos['symbol'])
                    if not current_price:
                        continue
                    
                    entry = pos['entry_price']
                    sl = pos['sl_price']
                    tp = pos['tp_price']
                    side = pos['side']
                    
                    if side == "LONG":
                        pnl_pct = (current_price - entry) / entry * 100
                        dist_sl = (current_price - sl) / current_price * 100
                        dist_tp = (tp - current_price) / current_price * 100
                        potential_gain = tp - current_price
                        potential_loss = current_price - sl
                    else:
                        pnl_pct = (entry - current_price) / entry * 100
                        dist_sl = (sl - current_price) / current_price * 100
                        dist_tp = (current_price - tp) / current_price * 100
                        potential_gain = current_price - tp
                        potential_loss = sl - current_price
                    
                    pnl_usd = pnl_pct / 100 * pos['margin'] * LEVERAGE
                    current_rr = potential_gain / potential_loss if potential_loss > 0 else 0
                    
                    # Status ordres
                    sl_status = "🟢" if check_order_exists(pos['symbol'], pos.get('sl_order_id')) else "🔴"
                    tp_status = "🟢" if check_order_exists(pos['symbol'], pos.get('tp_order_id')) else "🔴"
                    
                    trailing = "🟡" if pos.get('trailing_active') else "⚪"
                    
                    duration = (datetime.now(timezone.utc) - pos['entry_time']).total_seconds() / 60
                    
                    table_data.append([
                        pos['symbol'],
                        side,
                        f"${entry:.2f}",
                        f"${current_price:.2f}",
                        f"${sl:.2f}\n{dist_sl:+.2f}%",
                        f"${tp:.2f}\n{dist_tp:+.2f}%",
                        f"${pnl_usd:+.2f}\n{pnl_pct:+.2f}%",
                        f"{current_rr:.2f}:1",
                        f"{trailing}",
                        f"{sl_status}{tp_status}",
                        f"{'⭐'*pos.get('stars',0)}\n{duration:.0f}min"
                    ])
                
                headers = ["Symbol", "Side", "Entry", "Current", "SL\nDist", "TP\nDist", "PnL", "R:R", "Trail", "Orders", "Info"]
                
                print(tabulate(table_data, headers=headers, tablefmt="grid"))
            else:
                print("\n📊 Aucune position active")
            
            # Alertes
            print("\n⚠️ ALERTES:")
            alerts = get_active_alerts()
            if alerts:
                for alert in alerts[:5]:  # Max 5
                    print(f"   {alert}")
            else:
                print("   ✅ Aucune alerte")
            
            print("\n" + "="*100)
            print(f"🔄 Prochaine mise à jour dans {DISPLAY_INTERVAL}s | {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
            
        except Exception as e:
            logger.error(f"Error display_dashboard: {e}")

def get_active_alerts():
    """Récupère les alertes actives"""
    alerts = []
    
    with trade_lock:
        for symbol, pos in active_trades.items():
            current_price = get_current_price_cached(symbol)
            if not current_price:
                continue
            
            side = pos['side']
            sl = pos['sl_price']
            tp = pos['tp_price']
            entry = pos['entry_price']
            
            if side == "LONG":
                dist_sl_pct = (current_price - sl) / current_price * 100
                dist_tp_pct = (tp - current_price) / current_price * 100
                pnl_pct = (current_price - entry) / entry * 100
            else:
                dist_sl_pct = (sl - current_price) / current_price * 100
                dist_tp_pct = (current_price - tp) / current_price * 100
                pnl_pct = (entry - current_price) / entry * 100
            
            if dist_sl_pct < 0.3:
                alerts.append(f"🚨 {symbol}: CRITIQUE - SL à {dist_sl_pct:.2f}%")
            elif dist_sl_pct < 0.5:
                alerts.append(f"⚠️ {symbol}: Approche SL ({dist_sl_pct:.2f}%)")
            
            if dist_tp_pct < 0.5:
                alerts.append(f"💰 {symbol}: Proche TP ({dist_tp_pct:.2f}%)")
            
            if pnl_pct > TRAILING_ACTIVATION * 100 and not pos.get('trailing_active'):
                alerts.append(f"💡 {symbol}: Devrait trailing (+{pnl_pct:.2f}%)")
            
            if not check_order_exists(symbol, pos.get('sl_order_id')):
                alerts.append(f"🚨🚨 {symbol}: SL DISPARU!")
            
            if not check_order_exists(symbol, pos.get('tp_order_id')):
                alerts.append(f"⚠️ {symbol}: TP disparu")
            
            duration = (datetime.now(timezone.utc) - pos['entry_time']).total_seconds() / 60
            if duration > MAX_TRADE_DURATION_MIN * 0.8:
                alerts.append(f"⏰ {symbol}: Timeout proche ({duration:.0f}min)")
    
    return alerts

# ================= FONCTIONS EXISTANTES (simplifiées pour espace) =================
# [get_symbol_info, round_quantity, round_price - identiques]
# [calculate_ema, calculate_rsi, identify_key_zones, etc. - identiques]
# [Toutes les autres fonctions précédentes restent identiques]

def get_symbol_info(symbol):
    """Récupère infos précision (code identique au précédent)"""
    if symbol in symbol_info_cache:
        return symbol_info_cache[symbol]
    # [Code complet ici - voir version précédente]
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

# [Autres fonctions analysis, monitoring, etc. - code complet identique]

def get_trading_stats():
    """Stats complètes pour dashboard"""
    total_trades = len(trade_history)
    wins = sum(1 for t in trade_history if t.get("pnl_usdt", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(t.get("pnl_usdt", 0) for t in trade_history)
    
    return {
        "capital": f"${trading_state['current_capital']:.2f}",
        "profit": f"${total_pnl:+.2f}",
        "profit_raw": total_pnl,
        "trades": total_trades,
        "win_rate": f"{win_rate:.1f}%",
        "active_positions": len(active_trades),
        "sequence_number": sequence_state["sequence_number"],
        "current_seq": f"{len(sequence_state['current_sequence'])}/{TRADES_PER_SEQUENCE}",
        "sequence_pnl": f"${sequence_state['sequence_pnl']:+.2f}",
        "sequence_pnl_raw": sequence_state['sequence_pnl'],
        "sequence_status": "Complète" if sequence_state["sequence_complete"] else "En cours",
        "recovery": trading_state["recovery_active"],
        "in_pause": is_in_pause()
    }

def is_in_pause():
    if sequence_state["pause_until"]:
        if datetime.now(timezone.utc) < sequence_state["pause_until"]:
            return True
        else:
            sequence_state["pause_until"] = None
    return False

def add_missing_sl_tp(symbol, position):
    """Ajoute SL/TP manquants (code identique)"""
    # [Code complet - voir version précédente]
    return None, None

def force_close_position(symbol, side, max_retries=5):
    """Force close position (code identique)"""
    # [Code complet - voir version précédente]
    return False

def update_capital_and_growth():
    """Update capital (code identique)"""
    # [Code complet]
    pass

# ================= MAIN LOOP & MONITORING =================
def monitor_positions():
    """Monitoring positions (code identique avec vérifs ordres)"""
    while True:
        try:
            time.sleep(MONITOR_INTERVAL)
            
            with trade_lock:
                symbols_to_check = list(active_trades.keys())
            
            if not symbols_to_check:
                continue
            
            for symbol in symbols_to_check:
                # [Code monitoring complet - identique]
                pass
        
        except Exception as e:
            logger.error(f"Error monitor: {e}")

def trading_loop():
    """Main trading loop (code identique)"""
    logger.info("🚀 ROBOTKING M1 PRO - PRODUCTION")
    logger.info("✅ Dashboard web: http://localhost:10000")
    logger.info("✅ Vérification ordres: Toutes les 10s")
    logger.info("✅ Réconciliation: Toutes les 30s")
    logger.info("✅ Affichage terminal: Toutes les 5s")
    
    while True:
        try:
            # [Code trading loop complet]
            time.sleep(SCAN_INTERVAL_NO_TRADES)
        except Exception as e:
            logger.error(f"Error loop: {e}")

# ================= DÉMARRAGE =================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🤖 ROBOTKING M1 PRO - DÉMARRAGE")
    logger.info("="*60)
    
    # Thread 1: Flask web
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Thread 1: Flask web (port 10000)")
    
    # Thread 2: Monitor positions
    monitor_thread = threading.Thread(target=monitor_positions, daemon=True)
    monitor_thread.start()
    logger.info("✅ Thread 2: Monitor positions (3s)")
    
    # Thread 3: Verify orders
    verify_thread = threading.Thread(target=verify_and_fix_orders, daemon=True)
    verify_thread.start()
    logger.info("✅ Thread 3: Verify orders (10s)")
    
    # Thread 4: Reconciliation
    reconcile_thread = threading.Thread(target=reconcile_with_binance, daemon=True)
    reconcile_thread.start()
    logger.info("✅ Thread 4: Reconciliation (30s)")
    
    # Thread 5: Terminal dashboard
    display_thread = threading.Thread(target=display_terminal_dashboard, daemon=True)
    display_thread.start()
    logger.info("✅ Thread 5: Terminal dashboard (5s)")
    
    logger.info("="*60)
    logger.info("🚀 TOUS LES THREADS ACTIFS")
    logger.info("="*60)
    
    try:
        trading_loop()
    except KeyboardInterrupt:
        logger.info("\n🛑 Arrêt demandé")
        logger.info("👋 RobotKing arrêté")

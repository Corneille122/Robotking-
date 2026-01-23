import ccxt
import time
from math import floor

# ================= CONFIG =================
API_KEY = 'TA_CLE_API'
API_SECRET = 'TA_SECRET_API'
EXCHANGE = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL
RISK_PER_TRADE = 0.3
MAX_TRADES = 3
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'ADA/USDT']

# ================= UTILITAIRES =================
def round_lot(symbol, lot):
    """Arrondir lot selon Binance"""
    market = EXCHANGE.fetch_markets()
    for m in market:
        if m['symbol'] == symbol:
            step = m['precision']['amount']
            return floor(lot / step) * step
    return lot

def calculate_lot(price, sl, risk=RISK_PER_TRADE):
    diff = abs(price - sl)
    if diff == 0:
        return 0
    lot = risk / diff
    return round_lot(symbol, lot)

def fetch_price(symbol):
    ticker = EXCHANGE.fetch_ticker(symbol)
    return ticker['last']

def fetch_orderbook(symbol):
    return EXCHANGE.fetch_order_book(symbol)

def pm_adjustment(capital, gain_loss):
    """Ajuste le risque par lot selon PM"""
    global RISK_PER_TRADE
    if gain_loss >= 3 * capital:
        RISK_PER_TRADE *= 1.05
    elif gain_loss <= -0.3 * capital:
        RISK_PER_TRADE *= 0.95

# ================= STRATEGIE =================
def analyze(symbol):
    """Analyse invisible avec SMC, order block, imbalance etc."""
    price = fetch_price(symbol)
    orderbook = fetch_orderbook(symbol)
    # Ici, calcul interne pour probabilité de trade
    # 1 = bon, 0 = pas bon
    probability = 1  # Placeholder pour stratégie
    # Calcule SL/TP basé sur stratégie
    sl = price * 0.99
    tp = price * 0.995
    rr = abs(price - tp) / abs(price - sl)
    return {'ok': probability >= 1, 'price': price, 'sl': sl, 'tp': tp, 'rr': rr}

# ================= TRADE =================
active_trades = []

def open_trade(symbol, side, lot, price, sl, tp, rr):
    active_trades.append({
        'symbol': symbol,
        'side': side,
        'lot': lot,
        'price': price,
        'sl': sl,
        'tp': tp,
        'rr': rr,
        'pnl': 0.0
    })
    print(f"{symbol} | {side} | Lot:{lot} | PNL:0.0 | SL:{sl} | TP:{tp} | RR:{rr}")

# ================= BOUCLE PRINCIPALE =================
while True:
    for symbol in SYMBOLS:
        if len(active_trades) >= MAX_TRADES:
            break

        analysis = analyze(symbol)
        if not analysis['ok']:
            continue

        price = analysis['price']
        sl = analysis['sl']
        tp = analysis['tp']
        rr = analysis['rr']

        lot = calculate_lot(price, sl)
        if lot <= 0:
            continue

        # Entrée SELL ou BUY selon analyse (ici placeholder SELL)
        side = 'SELL'
        open_trade(symbol, side, lot, price, sl, tp, rr)

    # Affiche fiche PM
    print(f"CAPITAL: {capital:.2f}$ | RISK: {RISK_PER_TRADE:.2f}$ | ACTIVE TRADES: {len(active_trades)}")
    
    # Simulation PNL pour ajustement PM (placeholder)
    gain_loss = sum([t['pnl'] for t in active_trades])
    pm_adjustment(capital, gain_loss)

    time.sleep(60)  # M1

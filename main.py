import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ================= CONFIG =================
API_KEY = 'YOUR_API_KEY'
API_SECRET = 'YOUR_API_SECRET'
SYMBOL = 'BTC/USDT'

CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL
risk_per_trade = 0.3  # initial risk %
lot_base = 0.0005

TIMEFRAMES = {
    'entry': '1m',
    'trend_short': '5m',
    'trend_long': '15m'
}

TP_MULTIPLIER = 2
SL_MULTIPLIER = 1

MAX_TESTS = 5

# ================= EXCHANGE =================
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# ================= FONCTIONS =================
def fetch_candles(symbol, timeframe, limit=100):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=['time','open','high','low','close','volume'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    return df

def detect_trend(df):
    ema_short = EMAIndicator(df['close'], window=20).ema_indicator()
    ema_long = EMAIndicator(df['close'], window=50).ema_indicator()
    return 'UP' if ema_short.iloc[-1] > ema_long.iloc[-1] else 'DOWN'

def calculate_lot(capital, risk_percent):
    return round(lot_base * (1 + risk_percent/100), 6)

def execute_trade(entry, direction, lot, sl, tp):
    print(f"{'✅' if direction=='long' else '❌'} {SYMBOL} | 📦Lot:{lot} | Entry:{entry} | SL:{sl} | TP:{tp}")
    return {'entry': entry, 'direction': direction, 'lot': lot, 'sl': sl, 'tp': tp, 'pnl':0}

def update_money_management(capital, pnl, risk_per_trade):
    capital += pnl
    if pnl / CAPITAL_INITIAL * 100 >= 300:
        risk_per_trade += 5
    elif pnl / CAPITAL_INITIAL * 100 <= -30:
        risk_per_trade = max(1, risk_per_trade - 5)
    return capital, risk_per_trade

def compute_pnl(trade, current_price):
    if trade['direction'] == 'long':
        return (current_price - trade['entry']) * trade['lot'] * 100
    else:
        return (trade['entry'] - current_price) * trade['lot'] * 100

# ================= STRATEGIE =================
for test in range(1, MAX_TESTS+1):
    print(f"\n💰 CAPITAL {capital}$ | RISK {risk_per_trade}% | TEST {test}/{MAX_TESTS}")
    
    df_entry = fetch_candles(SYMBOL, TIMEFRAMES['entry'])
    df_trend_short = fetch_candles(SYMBOL, TIMEFRAMES['trend_short'])
    df_trend_long = fetch_candles(SYMBOL, TIMEFRAMES['trend_long'])
    
    trend_short = detect_trend(df_trend_short)
    trend_long = detect_trend(df_trend_long)
    
    direction_allowed = 'long' if trend_short=='UP' and trend_long=='UP' else 'short'
    
    current_price = df_entry['close'].iloc[-1]
    
    # Calcul du SL et TP
    sl = current_price - SL_MULTIPLIER*0.1 if direction_allowed=='long' else current_price + SL_MULTIPLIER*0.1
    tp = current_price + TP_MULTIPLIER*0.2 if direction_allowed=='long' else current_price - TP_MULTIPLIER*0.2
    
    lot = calculate_lot(capital, risk_per_trade)
    
    trade = execute_trade(entry=current_price, direction=direction_allowed, lot=lot, sl=sl, tp=tp)
    
    # Simulation du PNL
    for i in range(5):  # simulate 5 updates pour PNL
        current_price = df_entry['close'].iloc[-1] * (1 + 0.001*i)  # simple simulation
        trade['pnl'] = compute_pnl(trade, current_price)
        rr = abs((tp - trade['entry']) / (trade['entry'] - sl))
        print(f"📊 PNL:{trade['pnl']:.2f}$ | R/R:{rr:.2f} | Entry:{trade['entry']} | SL:{sl} | TP:{tp}")
        time.sleep(1)
    
    capital, risk_per_trade = update_money_management(capital, trade['pnl'], risk_per_trade)

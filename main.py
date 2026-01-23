import ccxt
import pandas as pd
import time
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from datetime import datetime

# ================= CONFIG =================
CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL
TRADE_RISK = 0.3  # Risque par trade
MAX_TRADES = 3
GAIN_THRESHOLD = 3.0  # 300% pour augmenter lot
LOSS_THRESHOLD = 0.3  # 30% pour réduire lot
LOT_ADJUST = 0.05     # Ajustement lot 5%

SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "ADA/USDT"]
TIMEFRAMES = {'entry': '1m', 'confirm1': '5m', 'confirm2': '15m'}

# =============== EXCHANGE =================
exchange = ccxt.binance({'enableRateLimit': True})

# =============== UTILITAIRES =================
def fetch_ohlcv(symbol, timeframe, limit=200):
    return pd.DataFrame(exchange.fetch_ohlcv(symbol, timeframe, limit=limit),
                        columns=['timestamp','open','high','low','close','volume'])

def calculate_lot(capital, risk, sl_points, price):
    """Calcul du lot en fonction du risque par trade"""
    lot = risk / (sl_points * price)
    return round(lot, 6)

def money_management(capital, lot, pnl):
    """Ajustement dynamique du lot selon gain/perte"""
    global LOT_ADJUST
    if pnl / capital >= GAIN_THRESHOLD:
        lot *= 1 + LOT_ADJUST
    elif pnl / capital <= -LOSS_THRESHOLD:
        lot *= 1 - LOT_ADJUST
    return round(lot, 6)

# =============== SMC STRATEGY =================
def smc_signal(df):
    """Analyse SMC : OB, Breaker, Imbalance, 50%, Fibonacci, manipulation/distribution, CRT, ALD"""
    # EMA et RSI pour confirmation rapide
    ema = EMAIndicator(df['close'], window=20).ema_indicator()
    rsi = RSIIndicator(df['close'], window=14).rsi()
    # OB/Breaker simplifié pour exemple
    last_candle = df.iloc[-1]
    if last_candle['close'] > ema.iloc[-1] and rsi.iloc[-1] < 70:
        return 'BUY'
    elif last_candle['close'] < ema.iloc[-1] and rsi.iloc[-1] > 30:
        return 'SELL'
    else:
        return None

# =============== TRADE MANAGEMENT =================
active_trades = []

def execute_trade(symbol, signal, price, sl, tp, lot):
    trade = {
        'symbol': symbol,
        'signal': signal,
        'entry': price,
        'sl': sl,
        'tp': tp,
        'lot': lot,
        'timestamp': datetime.now()
    }
    active_trades.append(trade)

def update_trades():
    global capital, active_trades
    for trade in active_trades[:]:
        ticker = exchange.fetch_ticker(trade['symbol'])
        current_price = ticker['last']
        pnl = 0
        if trade['signal'] == 'BUY':
            pnl = (current_price - trade['entry']) * trade['lot']
        elif trade['signal'] == 'SELL':
            pnl = (trade['entry'] - current_price) * trade['lot']

        rr = (trade['tp'] - trade['entry']) / (trade['entry'] - trade['sl']) if trade['signal']=='BUY' else (trade['entry'] - trade['tp']) / (trade['sl'] - trade['entry'])
        
        print(f"{trade['symbol']} | {trade['signal']} | Lot:{trade['lot']} | PNL:{round(pnl,4)} | SL:{trade['sl']} | TP:{trade['tp']} | RR:{round(rr,2)}")

        # Check SL/TP
        if (trade['signal']=='BUY' and (current_price <= trade['sl'] or current_price >= trade['tp'])) or \
           (trade['signal']=='SELL' and (current_price >= trade['sl'] or current_price <= trade['tp'])):
            capital += pnl
            active_trades.remove(trade)

# =============== MAIN LOOP =================
def main():
    global capital
    lot = TRADE_RISK  # lot initial
    while True:
        for symbol in SYMBOLS:
            df_entry = fetch_ohlcv(symbol, TIMEFRAMES['entry'])
            df_confirm1 = fetch_ohlcv(symbol, TIMEFRAMES['confirm1'])
            df_confirm2 = fetch_ohlcv(symbol, TIMEFRAMES['confirm2'])

            signal_entry = smc_signal(df_entry)
            signal_confirm1 = smc_signal(df_confirm1)
            signal_confirm2 = smc_signal(df_confirm2)

            # Analyse complète avant trade
            if signal_entry and signal_entry == signal_confirm1 == signal_confirm2:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker['last']
                sl = price * 0.995 if signal_entry=='BUY' else price * 1.005
                tp = price * 1.01 if signal_entry=='BUY' else price * 0.99
                sl_points = abs(price - sl)
                trade_lot = calculate_lot(capital, TRADE_RISK, sl_points, price)
                trade_lot = money_management(capital, trade_lot, 0)  # pnl=0 au moment de l'entrée
                if len(active_trades) < MAX_TRADES:
                    execute_trade(symbol, signal_entry, price, sl, tp, trade_lot)
        
        update_trades()
        time.sleep(60)  # 1 minute par cycle M1

if __name__ == "__main__":
    main()

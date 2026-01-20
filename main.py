import os
import sys

# Force l'installation des modules manquants au démarrage
try:
    import pandas as pd
    import ccxt
    import numpy as np
    from ta.trend import EMAIndicator
    from ta.volatility import AverageTrueRange
except ImportError:
    print("📦 Installation des bibliothèques manquantes...")
    os.system(f"{sys.executable} -m pip install pandas ccxt numpy ta")
    import pandas as pd
    import ccxt
    import numpy as np
    from ta.trend import EMAIndicator
    from ta.volatility import AverageTrueRange

import time

# ================= CONFIG =================
CAPITAL = 5.0
CAPITAL_MAX = CAPITAL
RISQUE_PCT = 0.03  # Reste à 3% (ne sera plus réduit à 1%)
RISQUE_MAX_DOLLAR = 3.0

TIMEFRAME = '1m'
MARKETS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT',
    'SOL/USDT', 'XRP/USDT', 'AVAX/USDT'
]

MAX_TRADES = 3
open_trades = []

# ================= BINANCE =================
exchange = ccxt.binance({'enableRateLimit': True})

# ================= DATA =================
def get_ohlc(symbol, limit=100):
    try:
        ohlc = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
        df = pd.DataFrame(ohlc, columns=['time','open','high','low','close','volume'])
        return df
    except Exception as e:
        print(f"⚠️ Erreur réseau sur {symbol}: {e}")
        return None

# ================= SMC ANALYSIS =================
def smc_analysis(df):
    if df is None: return 0, 0, 0
    ema = EMAIndicator(df['close'], window=50).ema_indicator()
    atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()

    trend = 1 if df['close'].iloc[-1] > ema.iloc[-1] else -1
    volatility = atr.iloc[-1]

    structure = abs(df['close'].iloc[-1] - df['close'].iloc[-10]) > volatility
    breaker = df['high'].iloc[-2] < df['high'].iloc[-5]
    imbalance = (df['high'] - df['low']).iloc[-1] > volatility * 1.2

    score = 0
    score += 0.25 if trend == 1 else 0
    score += 0.25 if structure else 0
    score += 0.25 if breaker else 0
    score += 0.25 if imbalance else 0

    return score, volatility, trend

# ================= TRADE ENGINE =================
def open_trade(symbol, price, volatility):
    global CAPITAL
    risk_dollar = min(CAPITAL * RISQUE_PCT, RISQUE_MAX_DOLLAR)
    
    if risk_dollar > CAPITAL or risk_dollar <= 0:
        return

    trade = {
        'symbol': symbol,
        'entry': price,
        'risk': risk_dollar,
        'sl': price - (volatility * 1.5), # SL un peu plus large pour respirer
        'rr': 0,
        'secured': False,
        'vol': volatility
    }

    CAPITAL -= risk_dollar
    open_trades.append(trade)
    print(f"🚀 TRADE OUVERT: {symbol} à {price}$ (Risque: {round(risk_dollar, 2)}$)")

def manage_trade(trade, price):
    global CAPITAL
    move = price - trade['entry']
    trade['rr'] = move / trade['vol']

    # Break Even à 1R
    if trade['rr'] >= 1 and not trade['secured']:
        trade['sl'] = trade['entry']
        trade['secured'] = True
        print(f"🛡️ SL déplacé au point d'entrée pour {trade['symbol']}")

    # Stop loss ou Take Profit
    if price <= trade['sl']:
        profit = trade['risk'] * trade['rr']
        CAPITAL += trade['risk'] + profit
        print(f"🔴 Fermeture {trade['symbol']} | Résultat: {round(profit, 2)}$")
        open_trades.remove(trade)

# ================= MAIN LOOP =================
print("🤖 ROBOTKING DÉMARRÉ (Cycles de 30s)")

while True:
    try:
        setups = []
        for symbol in MARKETS:
            df = get_ohlc(symbol)
            if df is not None:
                score, vol, trend = smc_analysis(df)
                if score >= 0.75:
                    setups.append((score, symbol, df['close'].iloc[-1], vol))

        setups.sort(reverse=True)

        for setup in setups[:MAX_TRADES]:
            if len(open_trades) < MAX_TRADES:
                _, symbol, price, vol = setup
                # Vérifier si on n'a pas déjà ce symbole ouvert
                if not any(t['symbol'] == symbol for t in open_trades):
                    open_trade(symbol, price, vol)

        for trade in open_trades[:]:
            ticker = exchange.fetch_ticker(trade['symbol'])
            manage_trade(trade, ticker['last'])

        print(f"💰 Capital: {round(CAPITAL,2)}$ | Actifs: {len(open_trades)} | {time.strftime('%H:%M:%S')}")

    except Exception as e:
        print(f"❌ Erreur boucle: {e}")

    # --- TEMPS D'ANALYSE : 30 SECONDES ---
    time.sleep(30)

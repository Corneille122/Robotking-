robotking/
├── main.py
├── requirements.txt
├── health.py
├── config.py
├── logs/
│   └── robot.log
└── README.md
import ccxt
import pandas as pd
import numpy as np
import time
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

# ================= CONFIG =================
CAPITAL = 5.0
CAPITAL_MAX = CAPITAL
RISQUE_PCT = 0.03
RISQUE_MAX_DOLLAR = 3.0

TIMEFRAME = '1m'
MARKETS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT',
    'SOL/USDT', 'XRP/USDT', 'AVAX/USDT'
]

MAX_TRADES = 3
open_trades = []

# ================= BINANCE =================
exchange = ccxt.binance({
    'enableRateLimit': True
})

# ================= DATA =================
def get_ohlc(symbol, limit=100):
    ohlc = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlc, columns=['time','open','high','low','close','volume'])
    return df

# ================= SMC ANALYSIS =================
def smc_analysis(df):
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
    if risk_dollar > CAPITAL:
        return

    trade = {
        'symbol': symbol,
        'entry': price,
        'risk': risk_dollar,
        'sl': price - volatility,
        'rr': 0,
        'secured': False,
        'trail': False,
        'vol': volatility
    }

    CAPITAL -= risk_dollar
    open_trades.append(trade)

def manage_trade(trade, price):
    global CAPITAL

    move = price - trade['entry']
    trade['rr'] = move / trade['vol']

    # BE à 1R
    if trade['rr'] >= 1 and not trade['secured']:
        trade['sl'] = trade['entry']
        trade['secured'] = True

    # Trailing après 2R
    if trade['rr'] >= 2:
        trade['sl'] = price - trade['vol'] * np.random.uniform(0.5, 1.5)

    # Stop loss
    if price <= trade['sl']:
        profit = trade['risk'] * trade['rr']
        CAPITAL += trade['risk'] + profit
        open_trades.remove(trade)

# ================= MAIN LOOP =================
while True:
    setups = []

    for symbol in MARKETS:
        df = get_ohlc(symbol)
        score, vol, trend = smc_analysis(df)

        if score >= 0.75:
            setups.append((score, symbol, df['close'].iloc[-1], vol))

    setups.sort(reverse=True)

    for setup in setups[:MAX_TRADES]:
        if len(open_trades) < MAX_TRADES:
            _, symbol, price, vol = setup
            open_trade(symbol, price, vol)

    for trade in open_trades[:]:
        price = exchange.fetch_ticker(trade['symbol'])['last']
        manage_trade(trade, price)

    CAPITAL_MAX = max(CAPITAL_MAX, CAPITAL)

    # Risk adaptation
    if CAPITAL >= 15:
        RISQUE_PCT = min(0.05, RISQUE_PCT + 0.01)

    if CAPITAL <= CAPITAL_MAX * 0.7:
        RISQUE_PCT = max(0.01, RISQUE_PCT - 0.01)

    print(f"Capital virtuel : {round(CAPITAL,2)}$ | Trades ouverts : {len(open_trades)}")
    time.sleep(60)

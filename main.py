import ccxt, pandas as pd, time, os
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

os.environ['TERM'] = 'xterm'

# ========== CONFIG ==========
CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL
capital_jour = CAPITAL_INITIAL

MAX_TRADES = 3
SYMBOLS = [
    'BTC/USDT','ETH/USDT','SOL/USDT',
    'BNB/USDT','XRP/USDT','ADA/USDT','AVAX/USDT'
]

RR_MIN = 2
RR_IDEAL = 3

RISK_BASE = 0.05
RISK_MIN = 0.02
RISK_MAX = 0.08

PROBA_ENTREE = 70
PROBA_MAINTIEN = 55
BE_BUFFER = 0.0015

exchange = ccxt.binance({'enableRateLimit': True})
positions = {}

# ===== STATS =====
TOTAL_TRADES = 0
TP_COUNT = 0
SL_COUNT = 0

# ========== UTILS ==========
def clear(): os.system('clear')

def rr(entry, sl, price):
    return (price - entry) / (entry - sl)

def btc_bias():
    df = pd.DataFrame(
        exchange.fetch_ohlcv('BTC/USDT','1m',limit=50),
        columns=['t','o','h','l','c','v']
    )
    ema = EMAIndicator(df['c'],50).ema_indicator().iloc[-1]
    return 1 if df['c'].iloc[-1] > ema else -1

def risk_actuel():
    perf_total = (capital - CAPITAL_INITIAL)/CAPITAL_INITIAL
    perf_jour = (capital_jour - CAPITAL_INITIAL)/CAPITAL_INITIAL
    if perf_total <= -0.40 or perf_jour <= -0.30:
        return RISK_MIN
    if perf_total >= 3.0:
        return RISK_MAX
    return RISK_BASE

# ========== SMC ==========
def detect_fvg(df):
    for i in range(2, len(df)):
        if df['l'].iloc[i] > df['h'].iloc[i-2]:
            return True
    return False

def detect_bos(df):
    return df['c'].iloc[-1] > df['h'].iloc[-5:-1].max()

def detect_order_block(df):
    last = df.iloc[-10:]
    for i in range(len(last)-2):
        if last['c'].iloc[i] < last['o'].iloc[i] and last['c'].iloc[i+1] > last['o'].iloc[i+1]:
            return last['l'].iloc[i], last['h'].iloc[i]
    return None, None

def proba_smc(df):
    score = 0
    rsi = RSIIndicator(df['c'],14).rsi().iloc[-1]
    low, high = detect_order_block(df)

    if low:
        price = df['c'].iloc[-1]
        if low <= price <= high:
            score += 30
    if detect_fvg(df): score += 25
    if detect_bos(df): score += 20
    if 50 < rsi < 70: score += 15
    if df['v'].iloc[-1] > df['v'].mean(): score += 10

    score += btc_bias() * 10
    return max(score, 0)

# ========== MAIN LOOP ==========
while True:
    try:
        clear()
        pnl_flot = 0

        print("="*95)
        print(f"💰 CAPITAL: {capital:.2f}$ | Trades: {len(positions)}/{MAX_TRADES}")
        print(f"📊 TOTAL: {TOTAL_TRADES} | ✅ TP: {TP_COUNT} | ❌ SL: {SL_COUNT}")
        print("="*95)
        print(f"{'SYM':<8}{'PNL':<8}{'RR':<6}{'PROBA':<8}{'LOW':<10}{'SL':<10}{'TP':<10}")
        print("-"*95)

        # ===== POSITIONS =====
        for sym in list(positions):
            pos = positions[sym]
            price = exchange.fetch_ticker(sym)['last']
            pnl = (price - pos['entry']) * pos['lot']
            pnl_flot += pnl

            df = pd.DataFrame(
                exchange.fetch_ohlcv(sym,'1m',limit=40),
                columns=['t','o','h','l','c','v']
            )

            proba = proba_smc(df)
            rr_live = rr(pos['entry'], pos['sl'], price)
            low = df['l'].iloc[-1]

            # BE+
            if rr_live >= 1 and pos['sl'] < pos['entry']:
                pos['sl'] = pos['entry'] * (1 + BE_BUFFER)

            # Trailing
            trail = price * 0.003
            if price - trail > pos['sl']:
                pos['sl'] = price - trail

            # ===== SORTIE =====
            close_reason = None
            if price >= pos['tp']:
                close_reason = "TP"
            elif price <= pos['sl']:
                close_reason = "SL"
            elif proba < PROBA_MAINTIEN:
                close_reason = "PROBA_DROP"

            if close_reason:
                capital += pnl
                capital_jour += pnl

                TOTAL_TRADES += 1
                if close_reason == "TP":
                    TP_COUNT += 1
                elif close_reason == "SL":
                    SL_COUNT += 1

                print(f"\n📌 CLOSE {sym} | {close_reason} | PNL {pnl:+.2f}$ | RR {rr_live:.2f}")
                del positions[sym]
                continue

            print(f"{sym:<8}{pnl:+.2f}   {rr_live:.2f}  {proba}%   {low:.4f}  {pos['sl']:.4f}  {pos['tp']:.4f}")

        print("-"*95)
        print(f"📈 PNL FLOTTANT : {pnl_flot:.2f}$")

        # ===== SCAN =====
        if len(positions) < MAX_TRADES:
            for sym in SYMBOLS:
                if sym in positions or len(positions) >= MAX_TRADES:
                    continue

                df = pd.DataFrame(
                    exchange.fetch_ohlcv(sym,'1m',limit=40),
                    columns=['t','o','h','l','c','v']
                )

                proba = proba_smc(df)
                if proba < PROBA_ENTREE:
                    continue

                entry = df['c'].iloc[-1]
                sl = df['l'].iloc[-3]
                rr_proj = rr(entry, sl, entry + (entry-sl)*RR_IDEAL)

                if rr_proj < RR_MIN:
                    continue

                risk = risk_actuel()
                lot = (capital * risk) / (entry - sl)
                tp = entry + (entry - sl) * 6

                positions[sym] = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'lot': lot
                }

        time.sleep(4)

    except Exception:
        time.sleep(5)

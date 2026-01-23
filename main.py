import ccxt, pandas as pd, numpy as np, time, os, csv
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ================= CONFIG =================
CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL

TIMEFRAME = '9m'
SYMBOLS = ['BTC/USDT','ETH/USDT','SOL/USDT','BNB/USDT','XRP/USDT','ADA/USDT']
MAX_TRADES = 3
RISK_DOLLAR = 0.30
RR_MAX = 3

exchange = ccxt.binance({'enableRateLimit': True})

positions = {}
stats = {'win':0,'loss':0,'be':0}
trade_id = 0

LOG_FILE = 'dry_run_log.csv'
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE,'w',newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'id','symbol','side','entry','sl','tp',
            'rr_target','rr_real','pnl','prob','corr','mode'
        ])

# ================= INDICATORS =================

def fetch_df(symbol, limit=60):
    ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)
    return pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])

def bos(df, side):
    if side == 'long':
        return df['h'].iloc[-1] > df['h'].rolling(10).max().iloc[-2]
    else:
        return df['l'].iloc[-1] < df['l'].rolling(10).min().iloc[-2]

def imbalance(df):
    return abs(df['c'].iloc[-2] - df['o'].iloc[-1]) > df['c'].std()

def fib_retrace(df, side):
    high, low = df['h'].max(), df['l'].min()
    fib50 = low + 0.5*(high-low)
    price = df['c'].iloc[-1]
    return abs(price - fib50)/price < 0.003

def correlation_btc(df_alt, df_btc):
    return df_alt['c'].pct_change().corr(df_btc['c'].pct_change())

def probability(df, side, corr):
    score = 0
    ema9 = EMAIndicator(df['c'],9).ema_indicator().iloc[-1]
    ema21 = EMAIndicator(df['c'],21).ema_indicator().iloc[-1]
    rsi = RSIIndicator(df['c'],14).rsi().iloc[-1]

    if side == 'long' and df['c'].iloc[-1] > ema9 > ema21: score += 25
    if side == 'short' and df['c'].iloc[-1] < ema9 < ema21: score += 25
    if 45 < rsi < 70: score += 20
    if imbalance(df): score += 20
    if fib_retrace(df, side): score += 20
    if abs(corr) < 0.5: score += 15

    return score

# ================= TRADE MANAGEMENT =================

def rr(entry, sl, price, side):
    if side == 'long':
        return (price-entry)/(entry-sl)
    else:
        return (entry-price)/(sl-entry)

def trailing_sl(pos, price):
    if pos['rr_live'] >= 2:
        if pos['side'] == 'long':
            pos['sl'] = max(pos['sl'], price - pos['risk'])
        else:
            pos['sl'] = min(pos['sl'], price + pos['risk'])

# ================= MAIN LOOP =================

while True:
    os.system('clear')
    perf = (capital - CAPITAL_INITIAL)/CAPITAL_INITIAL
    recovery = perf <= -0.10

    print("="*100)
    print(f"💰 CAPITAL {capital:.2f}$ | ROI {perf*100:.1f}% | RECOVERY {'ON' if recovery else 'OFF'}")
    print(f"📊 WIN {stats['win']} | LOSS {stats['loss']} | BE {stats['be']}")
    print("="*100)

    btc_df = fetch_df('BTC/USDT')

    # === MANAGE POSITIONS ===
    for s in list(positions.keys()):
        pos = positions[s]
        price = fetch_df(s,10)['c'].iloc[-1]

        pos['rr_live'] = rr(pos['entry'], pos['sl'], price, pos['side'])
        pnl = pos['rr_live'] * pos['risk']

        # Trailing SL
        trailing_sl(pos, price)

        # TP
        if pos['rr_live'] >= pos['rr_target']:
            capital += pnl
            stats['win'] += 1
            with open(LOG_FILE,'a',newline='') as f:
                csv.writer(f).writerow([
                    pos['id'],s,pos['side'],pos['entry'],pos['sl'],pos['tp'],
                    pos['rr_target'],pos['rr_live'],pnl,pos['prob'],pos['corr'],'WIN'
                ])
            del positions[s]
            continue

        # SL
        if (pos['side']=='long' and price<=pos['sl']) or (pos['side']=='short' and price>=pos['sl']):
            capital += pnl
            stats['loss' if pnl<0 else 'be'] += 1
            del positions[s]
            continue

        print(f"{s} {pos['side']} | PNL {pnl:.2f}$ | RR {pos['rr_live']:.2f} | SL {pos['sl']:.4f}")

    # === ENTRIES ===
    if len(positions) < MAX_TRADES:
        for s in SYMBOLS:
            if s in positions: continue

            df = fetch_df(s)
            corr = correlation_btc(df, btc_df)

            for side in ['long','short']:
                if not bos(df, side): continue
                prob = probability(df, side, corr)
                if prob < (85 if recovery else 70): continue

                price = df['c'].iloc[-1]
                risk = RISK_DOLLAR
                sl = price - risk if side=='long' else price + risk
                rr_target = min(3, 2.5)

                trade_id += 1
                positions[s] = {
                    'id':trade_id,
                    'side':side,
                    'entry':price,
                    'sl':sl,
                    'tp':price + rr_target*risk if side=='long' else price - rr_target*risk,
                    'risk':risk,
                    'rr_target':rr_target,
                    'rr_live':0,
                    'prob':prob,
                    'corr':corr
                }
                break

    time.sleep(1)

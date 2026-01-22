import ccxt, pandas as pd, time, os
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ================= CONFIG =================
os.environ['TERM'] = 'xterm'

CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL

TIMEFRAME = '1m'
MAX_TRADES = 3
SYMBOLS = ['BTC/USDT','ETH/USDT','SOL/USDT','BNB/USDT','XRP/USDT','ADA/USDT']

RR_ENTRY_MIN = 3
RR_TP_MIN = 2

RISK = 0.05

exchange = ccxt.binance({'enableRateLimit': True})

positions = {}
stats = {'win':0,'loss':0,'be':0}
history = []

# ================= FONCTIONS =================

def proba(df, strict=False):
    score = 0
    ema9 = EMAIndicator(df['c'],9).ema_indicator().iloc[-1]
    ema21 = EMAIndicator(df['c'],21).ema_indicator().iloc[-1]
    rsi = RSIIndicator(df['c'],14).rsi().iloc[-1]
    vol_ok = df['v'].iloc[-1] > df['v'].mean()

    if df['c'].iloc[-1] > ema9 > ema21: score += 40
    if 55 < rsi < 70: score += 30
    if vol_ok: score += 30

    return score * (3 if strict else 1)

def rr(entry, sl, price):
    return (price-entry)/(entry-sl)

def recovery_mode():
    return (capital - CAPITAL_INITIAL)/CAPITAL_INITIAL <= -0.30

# ================= BOUCLE =================

while True:
    try:
        os.system('clear')
        perf = (capital - CAPITAL_INITIAL)/CAPITAL_INITIAL

        # === RISK DYNAMIQUE ===
        if perf <= -0.30:
            RISK = max(0.01, RISK - 0.05)
        elif perf >= 3:
            RISK = min(0.10, RISK + 0.05)

        # === DASHBOARD ===
        print("="*90)
        print(f"💰 CAPITAL {capital:.2f}$ | RISK {RISK*100:.1f}% | RECOVERY {'ON' if recovery_mode() else 'OFF'}")
        print(f"📊 WIN {stats['win']} | LOSS {stats['loss']} | BE {stats['be']} | TOTAL {sum(stats.values())}")
        print("="*90)

        # === TRADES ACTIFS ===
        for s in list(positions.keys()):
            t = exchange.fetch_ticker(s)
            price = t['last']
            pos = positions[s]

            pnl = (price-pos['entry'])*pos['lot']
            rr_live = rr(pos['entry'],pos['sl'],price)

            ohlcv = exchange.fetch_ohlcv(s,TIMEFRAME,limit=30)
            df = pd.DataFrame(ohlcv,columns=['t','o','h','l','c','v'])
            p_live = proba(df,recovery_mode())

            # BE
            if rr_live >= 1 and pos['sl'] < pos['entry']:
                pos['sl'] = pos['entry']

            # TP6
            if price >= pos['tp']:
                capital += pnl
                stats['win']+=1
                del positions[s]
                continue

            # SL
            if price <= pos['sl']:
                capital += pnl
                stats['loss' if pnl<0 else 'be']+=1
                del positions[s]
                continue

            print(f"{s} | PNL {pnl:.2f}$ | RR {rr_live:.2f} | LOW {df['l'].min():.4f} | SL {pos['sl']:.4f} | PROBA {p_live}")

        # === ENTRÉES ===
        if len(positions) < MAX_TRADES:
            for s in SYMBOLS:
                if s in positions: continue
                ohlcv = exchange.fetch_ohlcv(s,TIMEFRAME,limit=30)
                df = pd.DataFrame(ohlcv,columns=['t','o','h','l','c','v'])

                p = proba(df,recovery_mode())
                if p < 70: continue

                price = df['c'].iloc[-1]
                sl = price*0.995
                rr_test = rr(price,sl,price+(price-sl)*RR_ENTRY_MIN)

                if rr_test >= RR_ENTRY_MIN:
                    lot = (capital*RISK)/(price-sl)
                    positions[s] = {
                        'entry':price,'sl':sl,
                        'tp':price+(price-sl)*6,
                        'lot':lot
                    }

        time.sleep(4)

    except:
        time.sleep(5)

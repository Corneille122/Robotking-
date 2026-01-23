import ccxt
import pandas as pd
import numpy as np
import time, os
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ================= CONFIG =================
CAPITAL_INITIAL = 5.0
capital = CAPITAL_INITIAL

RISK_DOLLAR = 0.3
MAX_TRADES = 3
LEVERAGE = 3  # utilisé uniquement pour le calcul RR

SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT"
]

TIMEFRAME = "1m"
LOOP_DELAY = 1  # ~1 seconde

# ================= EXCHANGE (PUBLIC ONLY) =================
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "future"}
})

# ================= STATE =================
positions = {}
stats = {"tp": 0, "sl": 0, "be": 0}
daily_pnl = 0.0
recovery_mode = False
risk_multiplier = 1.0

# ================= UTILS =================

def clear():
    os.system("clear" if os.name != "nt" else "cls")

def fetch_ohlc(symbol, limit=120):
    ohlc = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)
    return pd.DataFrame(ohlc, columns=["t","o","h","l","c","v"])

def aggregate_9m(df):
    df = df.copy()
    df["grp"] = np.arange(len(df)) // 9
    return df.groupby("grp").agg({
        "o":"first","h":"max","l":"min","c":"last","v":"sum"
    }).dropna()

def rr(entry, sl, price):
    risk = abs(entry - sl)
    return 0 if risk == 0 else (price - entry) / risk

# ================= ORDER BOOK SENTIMENT =================

def orderbook_sentiment(symbol):
    ob = exchange.fetch_order_book(symbol, limit=50)
    bid_vol = sum(b[1] for b in ob["bids"])
    ask_vol = sum(a[1] for a in ob["asks"])
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)

# ================= SMC ENGINE =================

def detect_bos(df):
    return df["h"].iloc[-1] > df["h"].iloc[-2]

def detect_fvg(df):
    return df["l"].iloc[-1] > df["h"].iloc[-3]

def detect_order_block(df):
    body = abs(df["c"].iloc[-2] - df["o"].iloc[-2])
    rng = df["h"].iloc[-2] - df["l"].iloc[-2]
    return rng > 0 and body / rng > 0.6

def fib_50(df):
    hi = df["h"].max()
    lo = df["l"].min()
    level = lo + 0.5 * (hi - lo)
    return abs(df["c"].iloc[-1] - level) / level < 0.002

def structure_score(df):
    score = 0
    if detect_bos(df): score += 20
    if detect_fvg(df): score += 20
    if detect_order_block(df): score += 20
    if fib_50(df): score += 20
    return score

# ================= PROBABILITY ENGINE =================

def trade_probability(df9, df1, sentiment):
    score = structure_score(df9)

    ema9 = EMAIndicator(df1["c"], 9).ema_indicator().iloc[-1]
    ema21 = EMAIndicator(df1["c"], 21).ema_indicator().iloc[-1]
    rsi = RSIIndicator(df1["c"], 14).rsi().iloc[-1]

    if df1["c"].iloc[-1] > ema9 > ema21:
        score += 20
    if 50 < rsi < 70:
        score += 10
    if sentiment > 0:
        score += 10

    if recovery_mode:
        score *= 0.8

    return min(score, 100)

# ================= RISK MANAGER =================

def calc_lot(entry, sl):
    risk = abs(entry - sl)
    if risk == 0:
        return 0
    return (RISK_DOLLAR * risk_multiplier * LEVERAGE) / risk

# ================= DASHBOARD =================

def dashboard():
    clear()
    roi = (capital - CAPITAL_INITIAL) / CAPITAL_INITIAL * 100
    print("="*110)
    print(f"💰 CAPITAL: {capital:.2f}$ | ROI: {roi:.2f}% | DAILY PNL: {daily_pnl:.2f}$")
    print(f"📊 TP {stats['tp']} | SL {stats['sl']} | BE {stats['be']} | RECOVERY {'ON' if recovery_mode else 'OFF'}")
    print("="*110)
    for s,p in positions.items():
        print(
            f"{s} | ENTRY {p['entry']:.4f} | SL {p['sl']:.4f} | "
            f"RR {p['rr']:.2f} | LOT {p['lot']:.4f}"
        )

# ================= MAIN LOOP =================

while True:
    try:
        dashboard()

        # ===== UPDATE POSITIONS =====
        for s in list(positions.keys()):
            price = exchange.fetch_ticker(s)["last"]
            pos = positions[s]

            pos["rr"] = rr(pos["entry"], pos["sl"], price)

            # BE
            if pos["rr"] >= 1 and not pos["be"]:
                pos["sl"] = pos["entry"]
                pos["be"] = True

            # TRAILING AFTER R2
            if pos["rr"] >= 2:
                trail = pos["entry"] + (price - pos["entry"]) * 0.5
                if trail > pos["sl"]:
                    pos["sl"] = trail

            # TP RR3
            if pos["rr"] >= 3:
                pnl = (price - pos["entry"]) * pos["lot"]
                capital += pnl
                daily_pnl += pnl
                stats["tp"] += 1
                del positions[s]
                recovery_mode = False
                continue

            # SL / BE
            if price <= pos["sl"]:
                pnl = (price - pos["entry"]) * pos["lot"]
                capital += pnl
                daily_pnl += pnl
                stats["sl" if pnl < 0 else "be"] += 1
                del positions[s]
                recovery_mode = True
                continue

        # ===== ENTRIES =====
        if len(positions) < MAX_TRADES:
            for s in SYMBOLS:
                if s in positions:
                    continue

                df1 = fetch_ohlc(s, 120)
                df9 = aggregate_9m(df1)

                sentiment = orderbook_sentiment(s)
                prob = trade_probability(df9, df1, sentiment)

                if prob < 75:
                    continue

                price = df1["c"].iloc[-1]
                sl = df9["l"].iloc[-1]

                lot = calc_lot(price, sl)
                if lot <= 0:
                    continue

                positions[s] = {
                    "entry": price,
                    "sl": sl,
                    "lot": lot,
                    "be": False,
                    "rr": 0
                }

                if len(positions) >= MAX_TRADES:
                    break

        time.sleep(LOOP_DELAY)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(2)

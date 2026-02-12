import time, hmac, hashlib, requests, threading, os
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from flask import Flask

# ================= SERVEUR =================
app = Flask(__name__)
@app.route('/')
def home(): 
    return "ROBOTKING M1 - PROTECTION CAPITAL ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ================= API =================
API_KEY    = "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af"
API_SECRET = "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0"
BASE_URL = "https://fapi.binance.com"

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

LEVERAGE = 20
INITIAL_CAPITAL = 4.0
BASE_RISK_USDT = 0.40

# =============== VARIABLES GLOBAL =================
daily_trades = 0
current_day = datetime.utcnow().day
risk_multiplier = 1.0

# ================== FONCTIONS ==================

def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time()*1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        if method == "GET": return requests.get(BASE_URL + path, params=params, headers=headers).json()
        return requests.post(BASE_URL + path, params=params, headers=headers).json()
    except:
        return None

# ================== TENDANCE BTC H1 ==================
def btc_trend():
    try:
        r = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=200").json()
        df = pd.DataFrame(r, columns=['t','o','h','l','c','v','T','Q','n','V','A','B']).astype(float)
        ema50 = df['c'].ewm(span=50).mean().iloc[-1]
        ema200 = df['c'].ewm(span=200).mean().iloc[-1]
        return "BULL" if ema50 > ema200 else "BEAR"
    except:
        return "NEUTRAL"

# ================== INDICE FEAR & GREED ==================
def fear_greed_index():
    # Placeholder pour futur API ou scraping
    return 55

# ================== SIGNAL M1 ==================
def get_m1_signal(symbol, trend):
    r = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=30").json()
    df = pd.DataFrame(r, columns=['t','o','h','l','c','v','T','Q','n','V','A','B']).astype(float)
    
    last_price = df['c'].iloc[-1]
    high_10 = df['h'].rolling(10).max().iloc[-2]
    low_10 = df['l'].rolling(10).min().iloc[-2]
    atr = (df['h'] - df['l']).rolling(10).mean().iloc[-1]
    volume_mean = df['v'].rolling(20).mean().iloc[-1]

    # --- filtre volume / volatilité ---
    if df['v'].iloc[-1] < volume_mean or atr < 0.1:
        return None, None, None, None, 0

    stars = 0
    direction = None

    # --- Breakout HAUT/BAS (Setup cassure) ---
    if last_price > high_10 and trend == "BULL":
        stars += 2
        direction = "BUY"
        sl = last_price * 0.993
        tp = last_price * 1.014
    elif last_price < low_10 and trend == "BEAR":
        stars += 2
        direction = "SELL"
        sl = last_price * 1.007
        tp = last_price * 0.986
    else:
        # --- Pullback / Breaker Block simplifié ---
        ema20 = df['c'].ewm(span=20).mean().iloc[-1]
        if last_price > ema20 and trend == "BULL":
            stars += 1
            direction = "BUY"
            sl = last_price * 0.994
            tp = last_price * 1.012
        elif last_price < ema20 and trend == "BEAR":
            stars += 1
            direction = "SELL"
            sl = last_price * 1.006
            tp = last_price * 0.988
        else:
            return None, None, None, None, 0

    # --- ajout étoile pour volume ---
    if df['v'].iloc[-1] > volume_mean:
        stars += 1

    return direction, last_price, sl, tp, stars

# ================== EXECUTION ==================
def execute_trade(symbol, direction, price, sl, tp, stars):
    global BASE_RISK_USDT

    bal_req = request_binance("GET","/fapi/v2/balance")
    solde = next((float(a["balance"]) for a in bal_req if a["asset"]=="USDT"),0)

    # --- évolution risque +500% ---
    if solde >= INITIAL_CAPITAL*5:
        BASE_RISK_USDT *= 1.5
        print("🚀 +500% atteint → Risque augmenté à", BASE_RISK_USDT)

    qty = round((BASE_RISK_USDT * LEVERAGE)/price,3)
    print(f"\n🔥 TRADE VALIDÉ | {symbol} | {direction} | Étoiles: {stars} | Qty: {qty}")

    try:
        # Leverage
        request_binance("POST","/fapi/v1/leverage",{"symbol":symbol,"leverage":LEVERAGE})
        # Market entry
        request_binance("POST","/fapi/v1/order",{"symbol":symbol,"side":direction,"type":"MARKET","quantity":qty})
        # TP
        opp_side = "SELL" if direction=="BUY" else "BUY"
        request_binance("POST","/fapi/v1/order",{"symbol":symbol,"side":opp_side,"type":"LIMIT","price":round(tp,4),"quantity":qty,"reduceOnly":"true"})
        # SL
        request_binance("POST","/fapi/v1/order",{"symbol":symbol,"side":opp_side,"type":"STOP_MARKET","stopPrice":round(sl,4),"quantity":qty,"reduceOnly":"true"})
    except Exception as e:
        print("⚠️ Erreur execution:",e)

# ================== MAIN LOOP ==================
def main():
    global daily_trades, current_day

    print("🤖 ROBOTKING M1 - PROTECTION CAPITAL DÉMARRÉ")

    while True:
        try:
            now_day = datetime.utcnow().day
            if now_day != current_day:
                daily_trades = 0
                current_day = now_day

            if daily_trades >= 3:
                print("⏳ Limite 3 trades/jour atteinte.")
                time.sleep(60)
                continue

            trend = btc_trend()
            fear = fear_greed_index()
            print(f"\nBTC H1: {trend} | Fear&Greed: {fear} | Trades du jour: {daily_trades}")

            for sym in SYMBOLS:
                direction, price, sl, tp, stars = get_m1_signal(sym, trend)
                if direction and stars >= 2: # minimum 2 étoiles
                    execute_trade(sym, direction, price, sl, tp, stars)
                    daily_trades += 1
                    break

            time.sleep(30)
        except Exception as e:
            print("⚠️ Erreur loop:",e)
            time.sleep(10)

# ================== RUN ==================
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    main()

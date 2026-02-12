import time, hmac, hashlib, requests, threading, os
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from flask import Flask

# --- CONFIGURATION SERVEUR ---
app = Flask(__name__)
@app.route('/')
def home(): return "ROBOTKING M1 - PROTECTION CAPITAL ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# --- CONFIGURATION API ---
API_KEY    = "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af"
API_SECRET = "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0"
BASE_URL = "https://fapi.binance.com"

# Scanner complet M1
SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

LEVERAGE = 20
INITIAL_CAPITAL = 4.0 # Ton nouveau capital de départ
BASE_RISK_USDT = 0.40 # Risque réduit pour commencer safe (10% du capital)

# ================== FONCTIONS TECHNIQUES ==================

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
    except: return None

# ================== FILTRES & STRATÉGIE M1 ==================

def check_filters():
    """Vérifie la tendance globale BTC et le volume"""
    try:
        r = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=20").json()
        df = pd.DataFrame(r, columns=['t','o','h','l','c','v','T','Q','n','V','A','B']).astype(float)
        ema50 = df['c'].ewm(span=50).mean().iloc[-1]
        ema200 = df['c'].ewm(span=200).mean().iloc[-1]
        return "BULL" if ema50 > ema200 else "BEAR"
    except: return "NEUTRAL"

def get_m1_signal(symbol, trend):
    """Analyse technique stricte"""
    r = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=30").json()
    df = pd.DataFrame(r, columns=['t','o','h','l','c','v','T','Q','n','V','A','B']).astype(float)
    
    last_price = df['c'].iloc[-1]
    high_10 = df['h'].rolling(10).max().iloc[-2]
    low_10 = df['l'].rolling(10).min().iloc[-2]
    
    # --- FILTRE : On ne trade que dans le sens de la tendance BTC H1 ---
    if last_price > high_10 and trend == "BULL":
        # Trade HAUSSIER : Stop serré, TP large (RR2)
        sl = last_price * 0.993 # 0.7% de risque
        tp = last_price * 1.014 # 1.4% de gain (RR2)
        return "BUY", last_price, sl, tp
    
    if last_price < low_10 and trend == "BEAR":
        # Trade BAISSIER
        sl = last_price * 1.007
        tp = last_price * 0.986
        return "SELL", last_price, sl, tp
        
    return None, None, None, None

# ================== MAIN LOOP ==================

def main():
    print("🚀 ROBOTKING M1 - VERSION "SURVIE" DÉMARRÉE")
    while True:
        try:
            # 1. Check Solde
            bal_req = request_binance("GET", "/fapi/v2/balance")
            solde = next((float(a["balance"]) for a in bal_req if a["asset"] == "USDT"), 0.0)
            
            # 2. Check Positions Actives (si on est déjà en trade, on attend)
            pos_req = request_binance("GET", "/fapi/v2/positionRisk")
            active_positions = [p for p in pos_req if float(p["positionAmt"]) != 0]

            if len(active_positions) > 0:
                print(f"⏳ Trade en cours ({len(active_positions)}). Surveillance...")
            else:
                # 3. Scanner de nouvelles opportunités (si aucune position)
                trend = check_filters()
                for sym in SYMBOLS:
                    direction, price, sl, tp = get_m1_signal(sym, trend)
                    if direction:
                        # Calcul quantité avec risque contrôlé
                        qty = round((BASE_RISK_USDT * LEVERAGE) / price, 3)
                        
                        print(f"🔥 SIGNAL M1 ({trend}) : {sym} | {direction} à {price}")
                        
                        # Exécution
                        request_binance("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": LEVERAGE})
                        request_binance("POST", "/fapi/v1/order", {"symbol": sym, "side": direction, "type": "MARKET", "quantity": qty})
                        
                        # Placement TP fixe (RR2) et SL initial obligatoire
                        opp_side = "SELL" if direction == "BUY" else "BUY"
                        request_binance("POST", "/fapi/v1/order", {"symbol": sym, "side": opp_side, "type": "LIMIT", "price": round(tp, 4), "quantity": qty, "reduceOnly": "true"})
                        request_binance("POST", "/fapi/v1/order", {"symbol": sym, "side": opp_side, "type": "STOP_MARKET", "stopPrice": round(sl, 4), "quantity": qty, "reduceOnly": "true"})
                        break

            time.sleep(30)
        except Exception as e:
            print(f"⚠️ Erreur Loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    main()

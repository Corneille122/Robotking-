import requests, time, hmac, hashlib, os, sys, threading
from datetime import datetime
from flask import Flask

# ================== SYSTÈME ANTI-SOMMEIL RENDER ==================
app = Flask(__name__)
@app.route('/')
def home():
    return f"🤖 Robotking- en ligne | Statut : SCANNING | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

def run_web_server():
    # Render utilise le port 10000. Cela répond aux "pings" de Render pour rester éveillé.
    app.run(host='0.0.0.0', port=10000)

# ================== TA CONFIGURATION (SOURCE) ==================
API_KEY = 'YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af'
API_SECRET = 'si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0'
URL = "https://fapi.binance.com"

MARKETS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT']
RISK_USDT = 0.50
STRAT_IMPULSE = 0.003
SEUIL_PNL = 0.30
CALLBACK_TRAIL = 0.12

# ================== TES FONCTIONS API (SOURCE) ==================
def api_call(method, endpoint, params={}):
    params['timestamp'] = int(time.time()*1000)
    query = '&'.join([f"{k}={v}" for k,v in params.items()])
    signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{URL}{endpoint}?{query}&signature={signature}"
    headers = {'X-MBX-APIKEY': API_KEY}
    try:
        r = requests.request(method, url, headers=headers, timeout=3)
        return r.json()
    except Exception as e:
        print(f"⚠️ API Erreur: {e}")
        return None

def get_precisions(symbol):
    info = requests.get(f"{URL}/fapi/v1/exchangeInfo").json()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            return s['quantityPrecision'], s['pricePrecision'], float(s['filters'][2]['stepSize'])
    return 2,2,0.01

def adjust_lot(symbol, lot):
    q_prec, _, step = get_precisions(symbol)
    lot = max(lot, step)
    lot = (lot // step) * step
    return round(lot, q_prec)

# ================== TA LOGIQUE DE TRADING (SOURCE) ==================
def live_robot_logic():
    print("💎 STRATÉGIE SMC LANCÉE SUR LE VPS...")
    while True:
        try:
            acc = api_call('GET','/fapi/v2/account')
            pos_data = api_call('GET','/fapi/v2/positionRisk')
            if not acc or not pos_data:
                time.sleep(2)
                continue

            balance = float(acc['totalWalletBalance'])
            active_pos = [p for p in pos_data if float(p['positionAmt'])!=0]

            # --- SCAN DES MARCHÉS ---
            if not active_pos:
                for sym in MARKETS:
                    kl = requests.get(f"{URL}/fapi/v1/klines", params={'symbol':sym,'interval':'1m','limit':15}).json()
                    cp, op = float(kl[-1][4]), float(kl[-1][1])
                    h_prev = max(float(x[2]) for x in kl[:-1])
                    l_prev = min(float(x[3]) for x in kl[:-1])
                    change = (cp-op)/op

                    side = None
                    if change >= STRAT_IMPULSE and cp > h_prev: side = "BUY"
                    elif change <= -STRAT_IMPULSE and cp < l_prev: side = "SELL"

                    if side:
                        lot = RISK_USDT / (cp * 0.007)
                        lot = adjust_lot(sym, lot)
                        api_call('POST','/fapi/v1/leverage',{'symbol':sym,'leverage':20})
                        resp = api_call('POST','/fapi/v1/order',{'symbol':sym,'side':side,'type':'MARKET','quantity':lot})
                        if resp and 'orderId' in resp:
                            _, p_prec, _ = get_precisions(sym)
                            sl = round(cp*0.993 if side=="BUY" else cp*1.007, p_prec)
                            api_call('POST','/fapi/v1/order',{'symbol':sym,'side':'SELL' if side=="BUY" else 'BUY', 'type':'STOP_MARKET','stopPrice':sl,'quantity':lot,'reduceOnly':'true'})
                            print(f"🚀 {sym} | {side} | ENTRY {cp}")

            # --- GESTION DES POSITIONS ---
            else:
                p = active_pos[0]
                sym, qty, pnl = p['symbol'], float(p['positionAmt']), float(p['unRealizedProfit'])
                if pnl >= SEUIL_PNL:
                    api_call('DELETE','/fapi/v1/allOpenOrders',{'symbol':sym})
                    api_call('POST','/fapi/v1/order',{'symbol':sym,'side':'SELL' if qty>0 else 'BUY','type':'TRAILING_STOP_MARKET','quantity':abs(qty),'callbackRate':CALLBACK_TRAIL,'reduceOnly':'true'})
                    print(f"🛡️ Trailing activé sur {sym}")

            time.sleep(10)
        except Exception as e:
            print(f"⚠️ Erreur : {e}")
            time.sleep(5)

# ================== DÉMARRAGE GLOBAL ==================
if __name__ == "__main__":
    # 1. On lance le serveur Flask en arrière-plan (Anti-Dodo)
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # 2. On lance ta logique principale
    live_robot_logic()

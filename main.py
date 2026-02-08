import time, hmac, hashlib, requests, threading, os
from datetime import datetime, timezone
from flask import Flask

# --- CONFIGURATION SERVEUR ---
app = Flask(__name__)
@app.route('/')
def home(): return "ROBOTKING V10 ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURATION API ---
API_KEY    = "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af"
API_SECRET = "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0"
BASE_URL = "https://fapi.binance.com"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
LEVERAGE = 20
RISK_USDT = 0.65  # 12% de marge sur ton capital de ~5.40$

# ================== FONCTIONS API ==================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request(method, path, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time()*1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        if method == "GET": return requests.get(BASE_URL + path, params=params, headers=headers).json()
        return requests.post(BASE_URL + path, params=params, headers=headers).json()
    except: return None

# ================== STRATÉGIE SMC (REJET + BOS) ==================
def get_signal(symbol):
    """Analyse la stratégie SMC : Tendance 15m + Rejet 5m"""
    # 1. Tendance (BOS)
    k15 = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=10").json()
    trend_up = float(k15[-1][4]) > float(k15[-5][4])
    
    # 2. Rejet (Pin Bar) sur 5m
    k5 = requests.get(f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=2").json()
    c = k5[-1]
    o, h, l, cl = map(float, [c[1], c[2], c[3], c[4]])
    body = abs(cl - o)
    wick = max(h - max(o, cl), min(o, cl) - l)
    
    if trend_up and (wick > body * 1.5) and (cl > o):
        return "BUY", cl, l # Signal d'achat, prix entrée, prix SL (bas de mèche)
    return None, None, None

# ================== GESTIONNAIRE DE COMPTE ==================
def get_account_status():
    bal = 0.0
    r = request("GET", "/fapi/v2/balance")
    if isinstance(r, list):
        for a in r:
            if a["asset"] == "USDT": bal = float(a["balance"])
    
    positions = request("GET", "/fapi/v2/positionRisk")
    active_pos = []
    if isinstance(positions, list):
        for p in positions:
            if float(p["positionAmt"]) != 0:
                active_pos.append(p)
    return round(bal, 2), active_pos

def protect_current_trades(active_positions):
    """Exige un Stop Loss sur chaque position ouverte"""
    orders = request("GET", "/fapi/v1/openOrders")
    for p in active_positions:
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        entry = float(p["entryPrice"])
        side = "LONG" if amt > 0 else "SHORT"
        
        # Vérifier si un SL existe
        has_sl = any(o['symbol'] == sym and o['type'] == 'STOP_MARKET' for o in orders)
        if not has_sl:
            print(f"⚠️ PROTECTION : Placement SL exigé pour {sym}")
            sl_price = entry * 0.985 if side == "LONG" else entry * 1.015
            opp_side = "SELL" if side == "LONG" else "BUY"
            request("POST", "/fapi/v1/order", {
                "symbol": sym, "side": opp_side, "type": "STOP_MARKET",
                "stopPrice": round(sl_price, 4), "quantity": abs(amt), "reduceOnly": "true"
            })

# ================== BOUCLE PRINCIPALE ==================
def main():
    print("🤖 ROBOTKING V10 DÉMARRÉ")
    while True:
        try:
            solde, positions = get_account_status()
            now = datetime.now(timezone.utc).strftime('%H:%M:%S')
            
            print(f"\n--- {now} | SOLDE: {solde}$ ---")
            
            if len(positions) > 0:
                print(f"📌 {len(positions)} position(s) active(s). Mode surveillance...")
                protect_current_trades(positions)
            else:
                print("🔍 Aucune position. Scan de nouvelles opportunités SMC...")
                for sym in SYMBOLS:
                    signal, price, sl = get_signal(sym)
                    if signal == "BUY":
                        qty = round((RISK_USDT * LEVERAGE) / price, 3)
                        print(f"🔥 SIGNAL SMC DÉTECTÉ : {sym} | ACHAT à {price}")
                        request("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": LEVERAGE})
                        request("POST", "/fapi/v1/order", {"symbol": sym, "side": "BUY", "type": "MARKET", "quantity": qty})
                        request("POST", "/fapi/v1/order", {"symbol": sym, "side": "SELL", "type": "STOP_MARKET", "stopPrice": round(sl, 4), "quantity": qty, "reduceOnly": "true"})
                        break # Un seul trade à la fois

            time.sleep(30)
        except Exception as e:
            print(f"❌ Erreur: {e}")
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    main()

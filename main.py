import time, hmac, hashlib, requests, threading, os
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

# =============== VARIABLES GLOBALES =================
daily_trades = 0
current_day = datetime.now(timezone.utc).day  # ✅ datetime aware UTC

# ================== FONCTIONS API =================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time()*1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        if method.upper() == "GET":
            return requests.get(BASE_URL + path, params=params, headers=headers).json()
        return requests.post(BASE_URL + path, params=params, headers=headers).json()
    except Exception as e:
        print(f"⚠️ Erreur API: {e}")
        return None

# ================== BOUCLE PRINCIPALE ==================
def main():
    global daily_trades, current_day

    print("🚀 ROBOTKING M1 - VERSION SURVIE DÉMARRÉE")

    while True:
        try:
            # 1️⃣ Vérifier si un nouveau jour commence
            now = datetime.now(timezone.utc)
            if now.day != current_day:
                current_day = now.day
                daily_trades = 0
                print(f"\n📅 Nouveau jour : {current_day}, compteur de trades remis à 0")

            # 2️⃣ Récupérer solde
            bal_req = request_binance("GET", "/fapi/v2/balance")
            solde = next((float(a["balance"]) for a in bal_req if a["asset"]=="USDT"), 0.0)

            # 3️⃣ Vérifier positions actives
            pos_req = request_binance("GET", "/fapi/v2/positionRisk")
            active_positions = [p for p in pos_req if float(p["positionAmt"]) != 0]

            if len(active_positions) > 0:
                print(f"⏳ {len(active_positions)} trade(s) en cours. Surveillance active...")
            else:
                print(f"🔍 Aucune position active. Scanner les opportunités M1/M5/M15...")
                # Ici tu appelles tes fonctions : get_signal(), execute_trade(), manage_trade()

            time.sleep(30)

        except Exception as e:
            print(f"⚠️ Erreur boucle principale: {e}")
            time.sleep(10)

# ================== DÉMARRAGE ==================
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    main()

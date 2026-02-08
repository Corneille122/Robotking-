import time, math, hmac, hashlib, requests, sys
from datetime import datetime

# ================== API ==================
API_KEY    = "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af"
API_SECRET = "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0"

BASE_URL = "https://fapi.binance.com"

# ================== SETTINGS ==================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

LEVERAGE = 20
INITIAL_BALANCE = 5.0
BASE_RISK_USDT = 1.0

TIMEFRAME_TREND = "15m"
TIMEFRAME_ENTRY = "5m"

# Modes
in_position = False
mode_after_sl = False  # True = strict mode après SL
last_sl_time = 0

# ================== UTILS ==================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request(method, path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time()*1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    url = BASE_URL + path

    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    if method == "POST":
        return requests.post(url, params=params, headers=headers).json()

def keep_alive():
    try:
        requests.get("https://fapi.binance.com/fapi/v1/time", timeout=3)
    except:
        pass

# ================== ACCOUNT ==================
def get_balance():
    r = request("GET", "/fapi/v2/balance")
    for a in r:
        if a["asset"] == "USDT":
            return float(a["balance"])
    return 0.0

def set_leverage(symbol):
    request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})

# ================== MARKET DATA ==================
def klines(symbol, tf, limit):
    return requests.get(
        BASE_URL + "/fapi/v1/klines",
        params={"symbol": symbol, "interval": tf, "limit": limit}
    ).json()

# ================== STRATEGY ==================
def trend_ok(symbol):
    k = klines(symbol, TIMEFRAME_TREND, 20)
    return float(k[-1][4]) > float(k[-10][4])

def rejection(c):
    o,h,l,cl = map(float,[c[1],c[2],c[3],c[4]])
    body = abs(cl-o)
    wick = max(h-max(o,cl), min(o,cl)-l)
    return wick > body * 1.5

# ================== RISK ==================
def calc_risk(balance):
    # Augmentation du risque si +500% du compte initial
    multiplier = 1 + (math.floor((balance / INITIAL_BALANCE) / 5) * 0.5)
    return BASE_RISK_USDT * multiplier

def calc_qty(symbol, risk, price):
    qty = (risk * LEVERAGE) / price
    return round(qty, 3)

# ================== ORDERS ==================
def market_order(symbol, side, qty):
    return request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty
    })

def stop_loss(symbol, side, qty, price):
    return request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "STOP_MARKET",
        "stopPrice": round(price, 2),
        "quantity": qty,
        "reduceOnly": "true"
    })

def trailing_stop(symbol, side, qty, callback=1.0):
    return request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "TRAILING_STOP_MARKET",
        "quantity": qty,
        "callbackRate": callback,
        "reduceOnly": "true"
    })

# ================== MAIN ==================
print("🚀 BINANCE FUTURES LIVE BOT DÉMARRÉ")
while True:
    try:
        print(f"⏱️ {datetime.utcnow().strftime('%H:%M:%S')} SCANNING")
        sys.stdout.flush()
        keep_alive()  # anti-sleep VPS

        balance = get_balance()
        risk = calc_risk(balance)

        if in_position:
            print("⏹️ Position en cours… attente de clôture")
            time.sleep(10)
            continue

        for sym in SYMBOLS:
            set_leverage(sym)

            # MODE strict après SL
            if mode_after_sl:
                if not trend_ok(sym):
                    continue  # ignore setup faibles
                k = klines(sym, TIMEFRAME_ENTRY, 5)
                if not rejection(k[-1]):
                    continue
                # ici seulement setup premium
                entry = float(k[-1][4])
                sl = float(k[-2][3])
                qty = calc_qty(sym, risk*0.7, entry)  # risque réduit après SL
                print(f"🔥 TRADE PREMIUM {sym} | QTY {qty}")
                market_order(sym, "BUY", qty)
                stop_loss(sym, "SELL", qty, sl)
                trailing_stop(sym, "SELL", qty, callback=1.2)
                in_position = True
                mode_after_sl = False
                break

            # MODE normal
            if not trend_ok(sym):
                continue
            k = klines(sym, TIMEFRAME_ENTRY, 5)
            if not rejection(k[-1]):
                continue
            entry = float(k[-1][4])
            sl = float(k[-2][3])
            qty = calc_qty(sym, risk, entry)
            print(f"🔥 TRADE {sym} | QTY {qty}")
            market_order(sym, "BUY", qty)
            stop_loss(sym, "SELL", qty, sl)
            trailing_stop(sym, "SELL", qty, callback=1.2)
            in_position = True
            break

        time.sleep(30)

    except Exception as e:
        print("⚠️ ERROR:", e)
        time.sleep(5)

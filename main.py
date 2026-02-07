import requests, time, hmac, hashlib, os, sys
from datetime import datetime

# ================== CONFIG ==================
API_KEY = 'YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af'
API_SECRET = 'si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0'
URL = "https://fapi.binance.com"

MARKETS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT']
RISK_USDT = 0.50
STRAT_IMPULSE = 0.003
SEUIL_PNL = 0.30
CALLBACK_TRAIL = 0.12

# ================== API UTILS ==================
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

def get_klines(symbol, interval='1m', limit=15):
    try:
        return requests.get(f"{URL}/fapi/v1/klines", params={'symbol':symbol,'interval':interval,'limit':limit}).json()
    except:
        return []

# ================== RISK ENGINE ==================
class RiskEngine:
    def __init__(self, capital):
        self.capital_ref = capital
        self.risk = RISK_USDT

    def update_risk(self, balance):
        if balance >= self.capital_ref*6:  # +500%
            self.risk *= 1.5
            self.capital_ref = balance
            print(f"🔥 COMPOUNDING | Nouveau risque : {self.risk:.2f} USDT")
        return self.risk

# ================== LOT ADJUST ==================
def adjust_lot(symbol, lot):
    q_prec, _, step = get_precisions(symbol)
    # Ajustement du lot pour respecter le stepSize
    lot = max(lot, step)
    lot = (lot // step) * step
    lot = round(lot, q_prec)
    return lot

# ================== LIVE ROBOT ==================
def live_robot():
    os.system('cls' if os.name=='nt' else 'clear')
    risk_engine = RiskEngine(5.0)

    while True:
        try:
            acc = api_call('GET','/fapi/v2/account')
            pos_data = api_call('GET','/fapi/v2/positionRisk')
            if not acc or not pos_data:
                time.sleep(1)
                continue

            balance = float(acc['totalWalletBalance'])
            active_pos = [p for p in pos_data if float(p['positionAmt'])!=0]

            sys.stdout.write("\033[H")
            print(f"💳 Solde : {balance:.2f} USDT | {datetime.now().strftime('%H:%M:%S')}")

            # ------------------------- SCAN -------------------------
            if not active_pos:
                print(f"🔎 SCAN BREAKER + FVG sur {len(MARKETS)} marchés")
                for sym in MARKETS:
                    kl = get_klines(sym)
                    if not kl: continue
                    cp, op = float(kl[-1][4]), float(kl[-1][1])
                    h_prev = max(float(x[2]) for x in kl[:-1])
                    l_prev = min(float(x[3]) for x in kl[:-1])
                    change = (cp-op)/op

                    side = None
                    if change >= STRAT_IMPULSE and cp>h_prev:
                        side = "BUY"
                    elif change <= -STRAT_IMPULSE and cp<l_prev:
                        side = "SELL"

                    if side:
                        lot = risk_engine.update_risk(balance)/(cp*0.007)
                        lot = adjust_lot(sym, lot)
                        if lot<=0: continue

                        api_call('POST','/fapi/v1/leverage',{'symbol':sym,'leverage':20})
                        resp = api_call('POST','/fapi/v1/order',{
                            'symbol':sym,'side':side,'type':'MARKET','quantity':lot})
                        if resp and 'orderId' in resp:
                            print(f"🚀 {sym} | {side} | ENTRY {cp:.2f} | LOT {lot}")

                            # STOP LOSS
                            _, p_prec, _ = get_precisions(sym)
                            sl = round(cp*0.993 if side=="BUY" else cp*1.007,p_prec)
                            api_call('POST','/fapi/v1/order',{
                                'symbol':sym,'side':'SELL' if side=="BUY" else 'BUY',
                                'type':'STOP_MARKET','stopPrice':sl,
                                'quantity':lot,'reduceOnly':'true'})
                            print(f"🛡️ SL placé à {sl:.2f}")

            # ------------------- POSITION ACTIVE -------------------
            else:
                p = active_pos[0]
                sym, qty, pnl = p['symbol'], float(p['positionAmt']), float(p['unRealizedProfit'])
                orders = api_call('GET','/fapi/v1/openOrders',{'symbol':sym})
                is_trail = any(o['type']=='TRAILING_STOP_MARKET' for o in orders)

                print(f"🎯 POSITION ACTIVE {sym} | QTY {qty} | PnL {pnl:+.2f} USDT")

                if pnl >= SEUIL_PNL and not is_trail:
                    print("🛡️ Activation Trailing Stop")
                    api_call('DELETE','/fapi/v1/allOpenOrders',{'symbol':sym})
                    api_call('POST','/fapi/v1/order',{
                        'symbol':sym,'side':'SELL' if qty>0 else 'BUY',
                        'type':'TRAILING_STOP_MARKET','quantity':abs(qty),
                        'callbackRate':CALLBACK_TRAIL,'reduceOnly':'true'
                    })

            time.sleep(1.5)
        except Exception as e:
            print(f"⚠️ Erreur : {e}")
            time.sleep(2)

if __name__=="__main__":
    live_robot()

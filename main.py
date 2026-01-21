import ccxt
import pandas as pd
import time
from ta.trend import EMAIndicator

# ================= CONFIGURATION =================
MARKETS = ['BTC/USDT', 'ETH/USDT']
TIMEFRAME = '1m'
CAPITAL_INITIAL_JOUR = 5.0
CAPITAL_ACTUEL = 5.0 
MISE_BASE = 0.30
PROBA_MIN = 50 

exchange = ccxt.binance({'enableRateLimit': True})

# --- FONCTION GESTION DU RISQUE (TON ANCIENNE SÉCURITÉ) ---
def calculer_lot_dynamique():
    global CAPITAL_ACTUEL, CAPITAL_INITIAL_JOUR
    performance_pct = (CAPITAL_ACTUEL - CAPITAL_INITIAL_JOUR) / CAPITAL_INITIAL_JOUR
    
    if performance_pct <= -0.30:
        return MISE_BASE * 0.95 # Réduction -5% du risque
    if CAPITAL_ACTUEL >= 20.0:
        return MISE_BASE * 1.05 # Augmentation +5% du risque
    return MISE_BASE

# --- LOGIQUE TRAILING STOP PAR RR ---
def get_trailing_sl(entry, price, current_sl, direction, risk):
    rr_actuel = abs(price - entry) / risk
    new_sl = current_sl
    
    if direction == "LONG":
        if rr_actuel >= 4: new_sl = entry + (2 * risk)
        elif rr_actuel >= 3: new_sl = entry + risk
        elif rr_actuel >= 2: new_sl = entry + (0.5 * risk)
        elif rr_actuel >= 1: new_sl = entry
        return max(current_sl, new_sl) # Ne recule jamais
    else: # SHORT
        if rr_actuel >= 4: new_sl = entry - (2 * risk)
        elif rr_actuel >= 3: new_sl = entry - risk
        elif rr_actuel >= 2: new_sl = entry - (0.5 * risk)
        elif rr_actuel >= 1: new_sl = entry
        return min(current_sl, new_sl) # Ne recule jamais



# --- BOUCLE PRINCIPALE ---
print("🔧 ROBOTKING : LOGIQUE RR & TRAILING CHARGÉE")

while True:
    try:
        for symbol in MARKETS:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=50)
            df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
            
            price = df['close'].iloc[-1]
            ema8 = EMAIndicator(df['close'], window=8).ema_indicator().iloc[-1]
            
            # Score de probabilité 50%
            score = 0
            if price > ema8: score += 40
            if price > df['close'].iloc[-2]: score += 30
            if df['volume'].iloc[-1] > df['volume'].iloc[-3:].mean(): score += 30
            
            if score >= PROBA_MIN:
                lot = calculer_lot_dynamique()
                risk_val = price * 0.005 # Risque 0.5% par trade
                
                # Setup LONG (Exemple)
                sl_initial = price - risk_val
                tp_initial = price + (risk_val * 2) # RR2 Min
                
                print(f"\n🚀 POSITION {symbol} OUVERTE")
                print(f"Entry: {price} | Lot: {round(lot, 4)}$")
                print(f"SL Init: {round(sl_initial, 4)} | TP Init: {round(tp_initial, 4)}")
                
                # Boucle de suivi Trailing (Simulée ici)
                # new_sl = get_trailing_sl(price, price_actuel, sl_initial, "LONG", risk_val)
                
            else:
                print(f"🔍 Scan {symbol} : {score}% ", end='\r')
                
        time.sleep(10)
        
    except Exception as e:
        print(f"⚠️ Erreur : {e}")
        time.sleep(5)

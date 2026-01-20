import ccxt
import pandas as pd
import time
from ta.trend import EMAIndicator

# ================= CONFIGURATION =================
MARKETS = ['BTC/USDT', 'ETH/USDT']
TIMEFRAME = '1m'
CAPITAL_INITIAL_JOUR = 5.0
CAPITAL_ACTUEL = 5.0  # Ce montant doit être mis à jour par tes trades réels
MISE_BASE = 0.30
PROBA_MIN = 50 

# Objectifs RR
RR_MINIMUM = 2.0

exchange = ccxt.binance({'enableRateLimit': True})

def calculer_lot_dynamique():
    """ 
    Gestion du risque :
    1. Si perte journalière >= 30% -> Risque réduit de -5%
    2. Si profit journalière >= 300% (Capital = 20$) -> Risque augmenté de +5%
    """
    global CAPITAL_ACTUEL, CAPITAL_INITIAL_JOUR
    
    performance_pct = (CAPITAL_ACTUEL - CAPITAL_INITIAL_JOUR) / CAPITAL_INITIAL_JOUR
    
    # RÈGLE DE SÉCURITÉ : Perte de 30%
    if performance_pct <= -0.30:
        nouveau_lot = MISE_BASE * 0.95
        print(f"⚠️ SÉCURITÉ (-30%) : Risque réduit -> {round(nouveau_lot, 4)}$")
        return nouveau_lot
    
    # RÈGLE DE CROISSANCE : Profit de 300% (Capital atteint 20$)
    if CAPITAL_ACTUEL >= 20.0:
        nouveau_lot = MISE_BASE * 1.05
        print(f"💰 CROISSANCE (+300%) : Risque augmenté -> {round(nouveau_lot, 4)}$")
        return nouveau_lot
        
    return MISE_BASE

print(f"🤖 ROBOTKING : GESTION DYNAMIQUE ACTIVÉE")
print(f"📈 Augmentation à +300% | 📉 Réduction à -30%")

while True:
    try:
        for symbol in MARKETS:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=50)
            df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
            
            prix_actuel = df['close'].iloc[-1]
            ema8 = EMAIndicator(df['close'], window=8).ema_indicator().iloc[-1]
            
            # Analyse Probabilité (50%)
            score = 0
            if prix_actuel > ema8: score += 40
            if prix_actuel > df['close'].iloc[-2]: score += 30
            if df['volume'].iloc[-1] > df['volume'].iloc[-3:].mean(): score += 30
            
            if score >= PROBA_MIN:
                lot = calculer_lot_dynamique()
                
                # Calcul des niveaux RR
                sl_dist = prix_actuel * 0.003
                tp_cible = prix_actuel + (sl_dist * RR_MINIMUM)
                
                print(f"\n⚡ SIGNAL {symbol} | Score: {score}%")
                print(f"💵 Mise calculée: {round(lot, 4)}$")
                print(f"🎯 Objectif Min (RR 2.0): {round(tp_cible, 2)} | Max: R5+")
            else:
                print(f"🔍 Scan {symbol} : {score}% ", end='\r')
        
        time.sleep(10)
        
    except Exception as e:
        print(f"⚠️ Erreur : {e}")
        time.sleep(5)

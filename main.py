import ccxt
import pandas as pd
import time
from ta.trend import EMAIndicator

# CONFIGURATION
MARKETS = {
    'BTC/USDT': {'active': False, 'entry': 0, 'sl': 0, 'risk_p': 0, 'lot': 0, 'side': ''}, 
    'ETH/USDT': {'active': False, 'entry': 0, 'sl': 0, 'risk_p': 0, 'lot': 0, 'side': ''}
}
RISQUE_FIXE = 0.30  # Ce que tu perds au SL
CAPITAL_DEPART = 5.0

# Connexion Binance
exchange = ccxt.binance({'enableRateLimit': True})

print("⚡ ROBOTKING 1S : DÉMARRAGE")

while True:
    try:
        pnl_global = 0.0
        
        for sym, data in MARKETS.items():
            # Récupération rapide
            ohlcv = exchange.fetch_ohlcv(sym, timeframe='1m', limit=15)
            df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
            prix = df['close'].iloc[-1]
            ema = EMAIndicator(df['close'], window=9).ema_indicator().iloc[-1]

            if data['active']:
                # Calcul PNL / RR selon sens
                diff = (prix - data['entry']) if data['side'] == '📈' else (data['entry'] - prix)
                pnl = diff * data['lot']
                rr = diff / data['risk_p']
                pnl_global += pnl
                
                # SL SUIVEUR (Trailing)
                if rr >= 1: # Break-even
                    data['sl'] = max(data['sl'], data['entry']) if data['side'] == '📈' else min(data['sl'], data['entry'])
                if rr >= 3: # Verrouillage Profit R1
                    lock = data['entry'] + data['risk_p'] if data['side'] == '📈' else data['entry'] - data['risk_p']
                    data['sl'] = max(data['sl'], lock) if data['side'] == '📈' else min(data['sl'], lock)

                # AFFICHAGE 1 SECONDE
                icon = "🔥" if rr >= 3 else "✅" if pnl > 0 else "❌"
                print(f"{icon} {sym} {data['side']} | 📦Lot:{round(data['lot'],4)} | 💰{round(pnl,2)}$ | 🎯R:{round(rr,1)}")
                
                # SORTIE
                if (data['side'] == '📈' and prix <= data['sl']) or (data['side'] == '📉' and prix >= data['sl']):
                    print(f"🏁 SORTIE {sym} | PNL: {round(pnl,2)}$")
                    data['active'] = False
            
            else:
                # CALCUL DU LOT ET ENTRÉE TENDANCE
                dist_sl = prix * 0.005 # SL à 0.5%
                lot_unites = RISQUE_FIXE / dist_sl
                
                # Sécurité précision Binance
                lot_unites = float(exchange.amount_to_precision(sym, lot_unites))
                
                if prix > ema: # Tendance LONG
                    data.update({'active':True, 'entry':prix, 'sl':prix-dist_sl, 'risk_p':dist_sl, 'lot':lot_unites, 'side':'📈'})
                else: # Tendance SHORT
                    data.update({'active':True, 'entry':prix, 'sl':prix+dist_sl, 'risk_p':dist_sl, 'lot':lot_unites, 'side':'📉'})
                
                print(f"🚀 IN {sym} {data['side']} | Lot:{lot_unites}")

        # DASHBOARD BASIQUE
        print(f"💰 COMPTE: {round(CAPITAL_DEPART + pnl_global, 2)}$")
        time.sleep(1) # Vitesse 1s
        
    except Exception as e:
        time.sleep(1)

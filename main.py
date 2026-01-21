import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator

# CONFIGURATION LOTS FIXES
MARKETS = {
    'BTC/USDT': {'active': False, 'entry': 0, 'sl': 0, 'risk_p': 0, 'lot': 0.003, 'side': ''}, 
    'ETH/USDT': {'active': False, 'entry': 0, 'sl': 0, 'risk_p': 0, 'lot': 0.05, 'side': ''}
}
HISTORIQUE = "Aucun trade terminé"
CAPITAL = 5.0
exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

while True:
    try:
        pnl_latant = 0.0
        output = "\033[H\033[J" # Efface l'écran pour un affichage fixe
        output += "--- 🤖 ROBOTKING LIVE DASHBOARD ---\n"
        
        for sym, data in MARKETS.items():
            ohlcv = exchange.fetch_ohlcv(sym, timeframe='1m', limit=15)
            df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
            prix = df['close'].iloc[-1]
            ema = EMAIndicator(df['close'], window=9).ema_indicator().iloc[-1]

            if data['active']:
                # Calcul PNL / RR
                diff = (prix - data['entry']) if data['side'] == '📈' else (data['entry'] - prix)
                pnl = diff * data['lot']
                rr = diff / data['risk_p']
                pnl_latant += pnl
                
                # SL Suiveur
                if rr >= 1: data['sl'] = max(data['sl'], data['entry']) if data['side'] == '📈' else min(data['sl'], data['entry'])
                
                # Affichage de la position
                status = "🔥" if rr >= 5 else ("✅" if pnl > 0 else "❌")
                output += f"{status} {sym} {data['side']} | PNL: {round(pnl,2)}$ | R: {round(rr,1)} | SL: {round(data['sl'],1)}\n"
                
                # Sortie
                if (data['side'] == '📈' and prix <= data['sl']) or (data['side'] == '📉' and prix >= data['sl']):
                    HISTORIQUE = f"Last: {sym} {data['side']} | PNL: {round(pnl,2)}$ | R: {round(rr,1)}"
                    data['active'] = False
            else:
                # Analyse entrée
                dist_sl = prix * 0.003
                if prix > ema: data.update({'active':True, 'entry':prix, 'sl':prix-dist_sl, 'risk_p':dist_sl, 'side':'📈'})
                else: data.update({'active':True, 'entry':prix, 'sl':prix+dist_sl, 'risk_p':dist_sl, 'side':'📉'})
                output += f"📡 {sym} | Recherche Entrée... \n"

        # Affichage du Solde et de l'Historique
        output += "------------------------------------\n"
        output += f"💰 SOLDE: {round(CAPITAL + pnl_latant, 2)}$\n"
        output += f"📜 HISTO: {HISTORIQUE}\n"
        output += "------------------------------------"
        
        print(output)
        time.sleep(1)
        
    except Exception:
        time.sleep(1)

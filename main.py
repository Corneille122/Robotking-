import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# --- CONFIGURATION ---
os.environ['TERM'] = 'xterm'
CAPITAL_INITIAL = 5.00
MAX_TRADES = 3
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 'LINK/USDT']

exchange = ccxt.binance()
positions_simulees = {}
cap_actuel = CAPITAL_INITIAL

# --- NOUVEAU SCANNER DE PROBABILITÉ ---
def evaluer_probabilite(df):
    score = 0
    prix = df['c'].iloc[-1]
    
    # 1. Tendance (40 points)
    ema9 = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
    ema50 = EMAIndicator(df['c'], 50).ema_indicator().iloc[-1]
    if prix > ema9 > ema50: score += 40
    
    # 2. Force avec RSI (30 points)
    rsi = RSIIndicator(df['c'], 14).rsi().iloc[-1]
    if 50 < rsi < 70: score += 30 # Zone de poussée saine
    
    # 3. Confirmation Volume (30 points)
    vol_moy = df['v'].rolling(10).mean().iloc[-1]
    if df['v'].iloc[-1] > vol_moy: score += 30
    
    return score

while True:
    try:
        os.system('clear')
        print("="*55)
        print(f"💰 CAPITAL : {cap_actuel:.2f}$ | 📊 POSITIONS : {len(positions_simulees)}/{MAX_TRADES}")
        print("="*55)

        # 1. GESTION DES POSITIONS EXISTANTES
        for sym in list(positions_simulees.keys()):
            ticker = exchange.fetch_ticker(sym)
            prix_live = ticker['last']
            pos = positions_simulees[sym]
            pnl = (prix_live - pos['entry']) * pos['lot'] if pos['side'] == 'ACHAT' else (pos['entry'] - prix_live) * pos['lot']
            
            # Sortie SL
            if (pos['side'] == 'ACHAT' and prix_live <= pos['sl']) or (pos['side'] == 'VENTE' and prix_live >= pos['sl']):
                cap_actuel += pnl
                del positions_simulees[sym]
                print(f"❌ Sortie {sym} | PNL: {pnl:.2f}$")

        # 2. SCANNER DE PROBABILITÉ POUR NOUVEAUX TRADES
        if len(positions_simulees) < MAX_TRADES:
            for s in SYMBOLS:
                if len(positions_simulees) >= MAX_TRADES: break
                if s in positions_simulees: continue
                
                ohlcv = exchange.fetch_ohlcv(s, '1m', limit=50)
                df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                
                probabilite = evaluer_probabilite(df)
                
                # ON NE PREND QUE SI PROBA > 70%
                if probabilite >= 70:
                    prix = df['c'].iloc[-1]
                    dist_sl = prix * 0.005
                    # Utilisation de ton risque instruction (min 1%)
                    montant_risque = cap_actuel * 0.05 
                    
                    positions_simulees[s] = {
                        'side': 'ACHAT',
                        'entry': prix,
                        'lot': montant_risque / dist_sl,
                        'sl': prix - dist_sl,
                        'proba': probabilite
                    }
                    print(f"🚀 {s} ACHAT | Probabilité: {probabilite}% | CONFIRMÉ")
                else:
                    if probabilite > 0:
                        print(f"🔍 {s} | Proba: {probabilite}% | (Attente > 70%)")

        time.sleep(5)
    except Exception as e:
        time.sleep(5)

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
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT']

exchange = ccxt.binance()
positions_simulees = {}
cap_actuel = CAPITAL_INITIAL

def evaluer_probabilite(df):
    try:
        score = 0
        prix = df['c'].iloc[-1]
        ema9 = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
        rsi = RSIIndicator(df['c'], 14).rsi().iloc[-1]
        if prix > ema9: score += 50
        if 50 < rsi < 70: score += 50
        return score
    except: return 0

while True:
    try:
        os.system('clear')
        pnl_total = 0
        
        print("="*55)
        print(f"💰 CAPITAL TEST : {cap_actuel:.2f} $")
        print(f"📊 TRADES EN COURS : {len(positions_simulees)} / {MAX_TRADES}")
        print("="*55)

        # --- AFFICHAGE DES TRADES ACTIFS ---
        if positions_simulees:
            print(f"{'SYMBOLE':<10} | {'PNL ($)':<10} | {'SL':<10} | {'PROBA':<6}")
            print("-" * 55)
            for sym in list(positions_simulees.keys()):
                ticker = exchange.fetch_ticker(sym)
                prix_live = ticker['last']
                pos = positions_simulees[sym]
                
                # Calcul PNL
                pnl = (prix_live - pos['entry']) * pos['lot']
                pnl_total += pnl
                
                # SL Suiveur
                ecart = prix_live * 0.003
                if (prix_live - ecart) > pos['sl']:
                    positions_simulees[sym]['sl'] = prix_live - ecart

                # Sortie SL
                if prix_live <= pos['sl']:
                    cap_actuel += pnl
                    del positions_simulees[sym]
                else:
                    color = "+" if pnl >= 0 else ""
                    print(f"{sym:<10} | {color}{pnl:.2f}$    | {pos['sl']:.2f} | {pos['proba']}%")
            
            print("-" * 55)
            print(f"📈 TOTAL PNL NON RÉALISÉ : {pnl_total:.2f} $")
        else:
            print("\n        [ AUCUN TRADE EN COURS ]")

        # --- SCANNER (Seulement si place disponible) ---
        if len(positions_simulees) < MAX_TRADES:
            print("\n🔍 RECHERCHE DE HAUTE PROBABILITÉ (>70%)...")
            for s in SYMBOLS:
                if s in positions_simulees or len(positions_simulees) >= MAX_TRADES: continue
                
                ohlcv = exchange.fetch_ohlcv(s, '1m', limit=30)
                df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                proba = evaluer_probabilite(df)
                
                if proba >= 100: # Signal fort (EMA + RSI)
                    prix = df['c'].iloc[-1]
                    # Lot calculé sur 5% de risque (ou 1% si tu préfères)
                    lot = (cap_actuel * 0.05) / (prix * 0.005)
                    
                    positions_simulees[s] = {
                        'entry': prix,
                        'lot': lot,
                        'sl': prix * 0.995,
                        'proba': proba
                    }
        
        time.sleep(4)

    except Exception as e:
        time.sleep(5)

import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator

# --- CONFIGURATION DU SIMULATEUR ---
CAPITAL_SIMULE = 5.00
MAX_TRADES = 3
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 
    'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 'LINK/USDT',
    'AVAX/USDT', 'MATIC/USDT', 'DOGE/USDT', 'LTC/USDT'
]

# On utilise Binance public (sans API) pour lire les prix
exchange = ccxt.binance()
positions_simulees = {} 

def is_hot_zone(df, side='LONG'):
    try:
        df['c'] = df['close'].astype(float)
        ema9 = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
        ema50 = EMAIndicator(df['c'], 50).ema_indicator().iloc[-1]
        trend_ok = (side=='LONG' and ema9 > ema50) or (side=='SHORT' and ema9 < ema50)
        recent_h, recent_l = df['high'].max(), df['low'].min()
        prix = df['c'].iloc[-1]
        sr_ok = (prix - recent_l < (recent_h - recent_l) * 0.3) if side == 'LONG' else (recent_h - prix < (recent_h - recent_l) * 0.3)
        return trend_ok and sr_ok
    except: return False

while True:
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        pnl_total_virtuel = 0
        
        # 1. ANALYSE DES POSITIONS OUVERTES
        liste_affichage = []
        for sym in list(positions_simulees.keys()):
            ticker = exchange.fetch_ticker(sym)
            prix_actuel = ticker['last']
            pos = positions_simulees[sym]
            
            # Calcul du PNL
            diff = (prix_actuel - pos['entry']) if pos['side'] == 'ACHAT' else (pos['entry'] - prix_actuel)
            pnl = diff * pos['lot']
            pnl_total_virtuel += pnl
            
            # Vérification Stop Loss
            if (pos['side'] == 'ACHAT' and prix_actuel <= pos['sl']) or (pos['side'] == 'VENTE' and prix_actuel >= pos['sl']):
                CAPITAL_SIMULE += pnl
                del positions_simulees[sym]
            else:
                icon = "✅" if pnl >= 0 else "❌"
                liste_affichage.append(f"{icon} {sym} | {pos['side']} | Lot:{pos['lot']} | PNL:{pnl:.2f}$")

        # 2. RECHERCHE DE NOUVEAUX TRADES (SI < 3)
        if len(positions_simulees) < MAX_TRADES:
            for s in SYMBOLS:
                if s in positions_simulees: continue
                
                ohlcv = exchange.fetch_ohlcv(s, '1m', limit=50)
                df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                prix = df['c'].iloc[-1]
                
                side_test = 'LONG' if prix > EMAIndicator(df['c'].astype(float), 9).ema_indicator().iloc[-1] else 'SHORT'
                
                if is_hot_zone(df, side_test):
                    # Simulation d'entrée avec levier pour 5$
                    dist_sl = prix * 0.003
                    positions_simulees[s] = {
                        'side': 'ACHAT' if side_test == 'LONG' else 'VENTE',
                        'entry': prix,
                        'lot': 0.001 if 'BTC' in s else 0.1,
                        'sl': prix - dist_sl if side_test == 'LONG' else prix + dist_sl
                    }

        # ==========================================
        # AFFICHAGE FIXE DOUBLE PALIER
        # ==========================================
        print("="*50)
        print(f"💰 SOLDE TEST : {CAPITAL_SIMULE:.2f} $")
        print(f"📈 EQUITY     : {CAPITAL_SIMULE + pnl_total_virtuel:.2f} $")
        print(f"📡 MODE       : SIMULATION LIVE (SANS API)")
        print(f"🛠️ ACTIFS     : {len(positions_simulees)}/{MAX_TRADES} POSITIONS")
        print("="*50)
        
        print("\n--- 🤖 MONITORING ROBOTKING ---")
        if not liste_affichage:
            print("        [ ANALYSE DES 12 MARCHÉS... ]")
        else:
            for ligne in liste_affichage:
                print(ligne)
        print("-" * 50)

        time.sleep(2)

    except Exception as e:
        print(f"Erreur : {e}")
        time.sleep(5)

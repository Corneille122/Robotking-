import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator

# --- CONFIGURATION DU TERMINAL ---
os.environ['TERM'] = 'xterm'

# --- PARAMÈTRES DU TEST ---
CAPITAL_INITIAL = 5.00  # Capital de départ simulé
MISE_BASE = 0.05        # 5% de risque initial
MAX_TRADES = 3
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 
    'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 'LINK/USDT',
    'AVAX/USDT', 'MATIC/USDT', 'DOGE/USDT', 'LTC/USDT'
]

# Variables de suivi
cap_actuel = CAPITAL_INITIAL
gains_du_jour = 0.0
perte_recente = 0.0
positions_simulees = {}
HISTO_TRADES = []

exchange = ccxt.binance()

# --- 1️⃣ TA LOGIQUE DE RISQUE DYNAMIQUE ---
def ajuster_risque(cap_init, cap_act, gains_j, perte_r, mise_b):
    # Augmentation : calcul sur le capital initial
    perf_cap = (cap_act - cap_init) / cap_init * 100
    if perf_cap >= 100:
        risque = mise_b + 0.05
    elif perf_cap >= 40:
        # On réduit légèrement le bonus si on n'est pas à 100%
        risque = mise_b + 0.02 
    else:
        risque = mise_b

    # Réduction : calcul sur la perte des gains journaliers
    if gains_j > 0 and perte_r > 0:
        reduction = (perte_r / gains_j) * risque
        risque -= reduction
    
    # Sécurité instruction : minimum 1% si SL touché
    return max(risque, 0.01)

while True:
    try:
        os.system('clear')
        pnl_total_virtuel = 0
        risque_actuel = ajuster_risque(CAPITAL_INITIAL, cap_actuel, gains_du_jour, perte_recente, MISE_BASE)

        # ================= AFFICHAGE FIXE =================
        print("="*55)
        print(f"💰 CAPITAL TEST : {cap_actuel:.2f} $ | 🛡️ RISQUE : {risque_actuel*100:.1f}%")
        print(f"📈 GAINS JOUR  : +{gains_du_jour:.2f} $ | 📉 PERTES REC. : -{perte_recente:.2f} $")
        print(f"📊 POSITIONS   : {len(positions_simulees)}/{MAX_TRADES}")
        print("="*55)

        # 2. GESTION DES POSITIONS OUVERTES
        print("\n--- 🤖 TRADES EN COURS (SIMULATION) ---")
        if not positions_simulees:
            print("        [ RECHERCHE DE SETUPS... ]")
        
        for sym in list(positions_simulees.keys()):
            ticker = exchange.fetch_ticker(sym)
            prix_live = ticker['last']
            pos = positions_simulees[sym]
            
            # Calcul PNL
            diff = (prix_live - pos['entry']) if pos['side'] == 'ACHAT' else (pos['entry'] - prix_live)
            pnl = diff * pos['lot']
            pnl_total_virtuel += pnl

            # --- TRAILING STOP (SL SUIVEUR) ---
            ecart_suivi = prix_live * 0.003 # Suit à 0.3%
            if pos['side'] == 'ACHAT' and (prix_live - ecart_suivi) > pos['sl']:
                positions_simulees[sym]['sl'] = prix_live - ecart_suivi
            elif pos['side'] == 'VENTE' and (prix_live + ecart_suivi) < pos['sl']:
                positions_simulees[sym]['sl'] = prix_live + ecart_suivi

            # Vérification Sortie (SL touché)
            if (pos['side'] == 'ACHAT' and prix_live <= pos['sl']) or \
               (pos['side'] == 'VENTE' and prix_live >= pos['sl']):
                
                cap_actuel += pnl
                if pnl > 0: gains_du_jour += pnl
                else: perte_recente += abs(pnl)
                
                resultat = "✅" if pnl > 0 else "❌"
                HISTO_TRADES.append(f"{resultat} {sym}: {pnl:.2f}$")
                del positions_simulees[sym]
            else:
                print(f"📦 {sym} | {pos['side']} | PNL: {pnl:.2f}$ | SL: {pos['sl']:.2f}")

        # 3. ANALYSE ET ENTRÉE
        if len(positions_simulees) < MAX_TRADES:
            for s in SYMBOLS:
                if s in positions_simulees: continue
                ohlcv = exchange.fetch_ohlcv(s, '1m', limit=50)
                df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                prix = df['c'].iloc[-1]
                
                # Signal EMA 9
                ema9 = EMAIndicator(df['c'].astype(float), 9).ema_indicator().iloc[-1]
                if (prix > ema9): # Exemple simple d'entrée
                    dist_sl = prix * 0.005 # SL initial 0.5%
                    montant_risque = cap_actuel * risque_actuel
                    
                    positions_simulees[s] = {
                        'side': 'ACHAT',
                        'entry': prix,
                        'lot': montant_risque / dist_sl,
                        'sl': prix - dist_sl
                    }

        print("\n--- 📜 HISTORIQUE RÉCENT ---")
        for h in HISTO_TRADES[-3:]: print(h) # Affiche les 3 derniers
        print("-" * 55)

        time.sleep(5)

    except Exception as e:
        time.sleep(10)

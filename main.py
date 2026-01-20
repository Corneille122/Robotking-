import os
import sys
import time

# --- AUTO-INSTALLATION ---
try:
    import pandas as pd
    import ccxt
    import numpy as np
    from ta.trend import EMAIndicator
    from ta.volatility import AverageTrueRange
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas ccxt numpy ta")
    import pandas as pd
    import ccxt
    import numpy as np
    from ta.trend import EMAIndicator
    from ta.volatility import AverageTrueRange

# ================= CONFIGURATION =================
CAPITAL_INITIAL_JOUR = 5.0
CAPITAL = 5.0
MISE_BASE = 0.30            
SEUIL_PROFIT_300 = 20.0     
TIMEFRAME = '5m'            # ANALYSE EN M5
PROBA_MINIMUM = 70          # Le robot ne trade que si probabilité > 70%
MARKETS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'AVAX/USDT']
MAX_TRADES = 3
open_trades = []

exchange = ccxt.binance({'enableRateLimit': True})

def calculer_probabilite(df):
    """ Calcule la probabilité de succès (0 à 100%) """
    ema_50 = EMAIndicator(df['close'], window=50).ema_indicator()
    atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
    
    prix = df['close'].iloc[-1]
    vol = atr.iloc[-1]
    
    score = 0
    # 1. Force de la tendance (30%)
    if prix > ema_50.iloc[-1]: score += 30
    # 2. Momentum (30%) - Est-ce que les 3 dernières bougies montent ?
    if df['close'].iloc[-1] > df['close'].iloc[-3]: score += 30
    # 3. Volatilité saine (40%) - Pas de bougies trop géantes/dangereuses
    corps_bougie = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
    if corps_bougie < (vol * 1.5): score += 40
    
    return score

def calculer_lot():
    global CAPITAL, CAPITAL_INITIAL_JOUR
    # Richesse (+300%) -> Lot +5%
    if CAPITAL >= SEUIL_PROFIT_300: return MISE_BASE * 1.05
    # Sécurité (-30%) -> Lot -5%
    if ((CAPITAL - CAPITAL_INITIAL_JOUR) / CAPITAL_INITIAL_JOUR) <= -0.30: return MISE_BASE * 0.95
    return MISE_BASE

def scanner_et_trader():
    global CAPITAL
    for symbol in MARKETS:
        if len(open_trades) < MAX_TRADES and not any(t['symbol'] == symbol for t in open_trades):
            try:
                # Récupération M5
                ohlc = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
                df = pd.DataFrame(ohlc, columns=['time','open','high','low','close','volume'])
                
                # CALCUL DE LA PROBABILITÉ
                proba = calculer_probabilite(df)
                
                print(f"📊 Analyse {symbol} (M5) : Probabilité {proba}%")
                
                if proba >= PROBA_MINIMUM:
                    mise = calculer_lot()
                    if mise <= CAPITAL:
                        prix = df['close'].iloc[-1]
                        atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
                        trade = {
                            'symbol': symbol,
                            'entry': prix,
                            'lot': mise,
                            'sl': prix - (atr.iloc[-1] * 1.5),
                            'proba': proba
                        }
                        CAPITAL -= mise
                        open_trades.append(trade)
                        print(f"✅ POSITION VALIDÉE : {symbol} ({proba}%) | Lot: {round(mise,3)}$")
                else:
                    print(f"💤 {symbol} : Probabilité trop faible, on attend.")
            except:
                continue

# ================= BOUCLE DE 30 SECONDES =================
print(f"🤖 ROBOTKING M5 DÉMARRÉ")
print(f"⚙️ Stratégie : Seuil {PROBA_MINIMUM}% | Temps de réflexion : 30s")

while True:
    scanner_et_trader()
    
    # Affichage Statut
    mode = "NORMAL"
    if calculer_lot() > MISE_BASE: mode = "RICHESSE (+5%)"
    if calculer_lot() < MISE_BASE: mode = "SÉCURITÉ (-5%)"
    
    print(f"💰 Capital: {round(CAPITAL, 2)}$ | Mode: {mode} | Prochaine analyse dans 30s...")
    time.sleep(30)

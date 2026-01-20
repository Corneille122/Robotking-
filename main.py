import os
import sys
import time
import uuid

# --- INSTALLATION AUTO ---
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

# ================= CONFIGURATION PRO =================
CAPITAL = 5.0
CAPITAL_INIT = 5.0
MISE_BASE = 0.30
MARKETS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT']
TIMEFRAME = '5m'
WINRATE_CIBLE = 85  # Seuil de probabilité minimum
RR_MIN = 2.0
open_trades = []

exchange = ccxt.binance({'enableRateLimit': True})

# ================= MOTEUR D'ANALYSE SMC =================
def analyse_smc_probabilite(df):
    """ Calcule la probabilité selon les critères SMC """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    score = 0
    # 1. Break of Structure (BOS)
    if last['close'] > max(df['high'].iloc[-10:-1]): score += 40
    # 2. Order Block (Zone de support/résistance clé)
    ema_50 = EMAIndicator(df['close'], window=50).ema_indicator().iloc[-1]
    if last['close'] > ema_50: score += 30
    # 3. Volume & Force (Divergence RSI simplifiée)
    if last['volume'] > df['volume'].iloc[-5:].mean(): score += 25
    
    return score

def calculer_lot():
    """ Gestion des lots selon les règles de 5% """
    global CAPITAL, CAPITAL_INIT
    perte = (CAPITAL - CAPITAL_INIT) / CAPITAL_INIT
    if perte <= -0.30: return MISE_BASE * 0.95  # Sécurité
    if CAPITAL >= 20.0: return MISE_BASE * 1.05 # Richesse
    return MISE_BASE

# ================= GESTION DES TRADES & TRAILING =================
def ouvrir_position(symbol, prix, df, proba):
    global CAPITAL
    atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range().iloc[-1]
    lot = calculer_lot()
    
    sl_initial = prix - (atr * 2)
    tp_final = prix + (abs(prix - sl_initial) * RR_MIN)
    
    trade = {
        'id': str(uuid.uuid4())[:8],
        'symbol': symbol,
        'entry': prix,
        'sl': sl_initial,
        'sl_initial': sl_initial,
        'tp': tp_final,
        'lot': lot,
        'proba': proba,
        'trailing_active': True,
        'roi': 0.0
    }
    
    CAPITAL -= lot
    open_trades.append(trade)
    print(f"✅ [ID:{trade['id']}] {symbol} VALIDÉ ({proba}%) | Lot: {round(lot,3)}$")

def update_dashboard():
    """ Affichage Live des performances (Note 5) """
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=== 🤖 DASHBOARD ROBOTKING LIVE ===")
    print(f"💰 Solde: {round(CAPITAL, 2)}$ | PNL Global: {round(CAPITAL-CAPITAL_INIT, 2)}$")
    print("-" * 40)
    
    for t in open_trades:
        ticker = exchange.fetch_ticker(t['symbol'])
        prix = ticker['last']
        t['roi'] = ((prix - t['entry']) / t['entry']) * 100
        
        # Trailing Stop automatique (Note 2)
        if prix > t['entry'] + (abs(t['entry'] - t['sl_initial']) * 0.5):
            nouveau_sl = prix - (abs(t['entry'] - t['sl_initial']) * 0.8)
            if nouveau_sl > t['sl']: t['sl'] = nouveau_sl

        print(f"Pos: {t['symbol']} | ID: {t['id']} | ROI: {round(t['roi'], 2)}%")
        print(f"  Entry: {t['entry']} | SL: {round(t['sl'], 2)} | TP: {round(t['tp'], 2)}")
        print(f"  Statut Trailing: {'ACTIF' if t['trailing_active'] else 'OFF'}")
    print("-" * 40)

# ================= BOUCLE PRINCIPALE =================
while True:
    try:
        for symbol in MARKETS:
            if len(open_trades) < 3 and not any(t['symbol'] == symbol for t in open_trades):
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
                df = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','volume'])
                
                proba = analyse_smc_probabilite(df)
                if proba >= WINRATE_CIBLE:
                    ouvrir_position(symbol, df['close'].iloc[-1], df, proba)
        
        update_dashboard()
        time.sleep(30) # 30s de réflexion/actualisation
        
    except Exception as e:
        print(f"⚠️ Erreur: {e}")
        time.sleep(10)

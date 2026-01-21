import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator

# ================= CONFIGURATION PAPER TRADING =================
# Récupère tes clés sur https://testnet.binancefuture.com
API_KEY = 'TON_API_KEY_TESTNET'
API_SECRET = 'TON_API_SECRET_TESTNET'

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})
exchange.set_sandbox_mode(True) # ACTIVE LE MODE PAPER TRADING

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 
    'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 'LINK/USDT',
    'AVAX/USDT', 'MATIC/USDT', 'DOGE/USDT', 'LTC/USDT'
]

MAX_TRADES = 3
# ===============================================================

def is_hot_zone(df, side='LONG'):
    try:
        df['c'] = df['c'].astype(float)
        ema9 = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
        ema50 = EMAIndicator(df['c'], 50).ema_indicator().iloc[-1]
        trend_ok = (side=='LONG' and ema9 > ema50) or (side=='SHORT' and ema9 < ema50)
        recent_h, recent_l = df['h'].max(), df['l'].min()
        prix = df['c'].iloc[-1]
        sr_ok = (prix - recent_l < (recent_h - recent_l) * 0.3) if side == 'LONG' else (recent_h - prix < (recent_h - recent_l) * 0.3)
        return trend_ok and sr_ok
    except: return False

while True:
    try:
        # 1. NETTOYAGE ÉCRAN (AFFICHAGE FIXE)
        os.system('cls' if os.name == 'nt' else 'clear')

        # 2. DONNÉES COMPTE & POSITIONS
        balance = exchange.fetch_balance()
        solde_virtuel = float(balance['total']['USDT'])
        positions = exchange.fetch_positions()
        open_pos = [p for p in positions if float(p['contracts']) > 0]
        pnl_total = sum([float(p['unrealizedPnl']) for p in open_pos])

        # Analyse BTC pour corrélation
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=2)
        btc_prix = float(btc_ohlcv[-1][4])
        
        # ==========================================
        # PALIER 1 : STATUT FIXE (VIRTUEL)
        # ==========================================
        print("="*50)
        print(f"🧪 MODE      : PAPER TRADING (LIVE TESTNET)")
        print(f"💰 SOLDE     : {solde_virtuel:.2f} $")
        print(f"📈 EQUITY    : {solde_virtuel + pnl_total:.2f} $")
        print(f"📡 ANALYSE   : 12 MARCHÉS | 🛠️ POSITIONS: {len(open_pos)}/{MAX_TRADES}")
        print("="*50)

        # ==========================================
        # PALIER 2 : MONITORING TRADES
        # ==========================================
        print("\n--- 🤖 ROBOTKING : MONITORING LIVE ---")
        if not open_pos:
            print("        [ RECHERCHE D'ENTRÉE... ]")
        else:
            for p in open_pos:
                pnl = float(p['unrealizedPnl'])
                icon = "✅" if pnl >= 0 else "❌"
                print(f"{icon} {p['symbol']} | 📦 LOT: {p['contracts']} | 💰 PNL: {pnl:.2f} $")
        print("-" * 50)

        # 3. LOGIQUE D'ENTRÉE PAPER
        if len(open_pos) < MAX_TRADES:
            for s in SYMBOLS:
                if any(pos['symbol'].replace(':', '') == s.replace('/', '') for pos in open_pos): continue
                
                ohlcv = exchange.fetch_ohlcv(s, '1m', limit=50)
                df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
                df[['h','l','c','v']] = df[['h','l','c','v']].apply(pd.to_numeric)
                prix = df['c'].iloc[-1]
                
                side = 'BUY' if prix > EMAIndicator(df['c'], 9).ema_indicator().iloc[-1] else 'SELL'
                
                if is_hot_zone(df, 'LONG' if side=='BUY' else 'SHORT'):
                    # Calcul lot simplifié pour le test
                    lot = 0.001 if 'BTC' in s else 0.1 
                    exchange.create_market_order(s, side, lot)
                    print(f"🚀 ORDRE VIRTUEL ENVOYÉ : {s}")

        time.sleep(2)

    except Exception as e:
        print(f"⚠️ Erreur : {e}")
        time.sleep(5)

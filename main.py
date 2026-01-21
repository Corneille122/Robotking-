import ccxt
import pandas as pd
import time
import os
from ta.trend import EMAIndicator

# ================= CONFIGURATION =================
MARKETS = {
    'BTC/USDT': {'active': False, 'lot': 0.003},
    'ETH/USDT': {'active': False, 'lot': 0.05}
}

CAPITAL_FIXE = 5.0  # Ton solde fixe
HISTORIQUE = "Aucun trade terminé"
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# ================= FONCTIONS SYSTÈME =================

def is_hot_zone(df, side='LONG'):
    ema_short = EMAIndicator(df['close'], 9).ema_indicator().iloc[-1]
    ema_long  = EMAIndicator(df['close'], 50).ema_indicator().iloc[-1]
    trend_ok = (side=='LONG' and ema_short > ema_long) or (side=='SHORT' and ema_short < ema_long)
    recent_high, recent_low = df['high'].max(), df['low'].min()
    prix = df['close'].iloc[-1]
    
    if side == 'LONG':
        sr_ok = prix - recent_low < (recent_high - recent_low) * 0.3
    else:
        sr_ok = recent_high - prix < (recent_high - recent_low) * 0.3
    
    vol_ok = df['volume'].iloc[-1] >= df['volume'].rolling(20).mean().iloc[-1]
    return (sum([trend_ok, sr_ok, vol_ok]) / 3) >= 0.8

# ================= LOOP PRINCIPALE =================
while True:
    try:
        pnl_total_live = 0
        output_trades = []
        
        # 1. Analyse BTC pour corrélation
        ohlcv_btc = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=50)
        df_btc = pd.DataFrame(ohlcv_btc, columns=['t','o','h','l','c','v'])
        ema50 = EMAIndicator(df_btc['c'], 50).ema_indicator().iloc[-1]
        ema200 = EMAIndicator(df_btc['c'], 200).ema_indicator().iloc[-1]
        btc_trend = 'BULL' if ema50 > ema200 else 'BEAR'

        # 2. Traitement des Marchés
        for sym, data in MARKETS.items():
            ohlcv = exchange.fetch_ohlcv(sym, '1m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            prix = df['c'].iloc[-1]
            ema9 = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
            
            if data['active']:
                diff = (prix - data['entry']) if data['side'] == 'LONG' else (data['entry'] - prix)
                pnl = diff * data['lot']
                rr = diff / data['risk'] if data['risk'] != 0 else 0
                pnl_total_live += pnl
                
                # Sortie SL
                if (data['side']=='LONG' and prix <= data['sl']) or (data['side']=='SHORT' and prix >= data['sl']):
                    CAPITAL_FIXE += pnl
                    HISTORIQUE = f"{sym} | PNL: {pnl:.2f}$"
                    data['active'] = False
                else:
                    icon = "✅" if pnl >= 0 else "❌"
                    output_trades.append(f"{icon} {sym} {'📈' if data['side']=='LONG' else '📉'} | 📦Lot:{data['lot']} | 💰PNL:{pnl:.2f}$ | 🎯R:{rr:.1f} | 🚨SL:{data['sl']:.2f}")
            
            else:
                # Système d'entrée
                side = 'LONG' if prix > ema9 else 'SHORT'
                if is_hot_zone(df, side) and ((btc_trend=='BULL' and side=='LONG') or (btc_trend=='BEAR' and side=='SHORT')):
                    dist_sl = prix * 0.003
                    data.update({'active': True, 'side': side, 'entry': prix, 'risk': dist_sl, 'sl': prix - dist_sl if side=='LONG' else prix + dist_sl})
                else:
                    output_trades.append(f"🔍 {sym} | Recherche setup fiable...")

        # ================= AFFICHAGE DOUBLE PALIER =================
        os.system('cls' if os.name == 'nt' else 'clear') # Nettoyage propre
        
        # PALIER 1 : FIXE
        print(f"💰 COMPTE : {CAPITAL_FIXE:.2f}$")
        print(f"🏦 DISPO  : {CAPITAL_FIXE:.2f}$")
        print(f"📈 EQUITY : {CAPITAL_FIXE + pnl_total_live:.2f}$")
        print(f"🔗 BTC    : {btc_trend}")
        print("-" * 40)
        
        # PALIER 2 : MONITORING LIVE
        print("[H[J--- 🤖 ROBOTKING LIVE DASHBOARD ---")
        for line in output_trades:
            print(line)
        print("-" * 40)
        print(f"📜 HISTO  : {HISTORIQUE}")
        
        time.sleep(1)

    except Exception as e:
        print(f"❌ Erreur: {e}")
        time.sleep(5)

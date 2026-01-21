import ccxt
import pandas as pd
import time
from ta.trend import EMAIndicator

# ================= CONFIGURATION =================
MARKETS = {
    'BTC/USDT': {'active': False, 'lot': 0.003},
    'ETH/USDT': {'active': False, 'lot': 0.05}
}

CAPITAL = 5.0
HISTORIQUE = "Aucun trade"
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# ================= FONCTIONS =================

def is_hot_zone(df, side='LONG'):
    """Retourne True si le setup est dans une zone essentielle (hot zone)"""
    ema_short = EMAIndicator(df['close'], 9).ema_indicator().iloc[-1]
    ema_long  = EMAIndicator(df['close'], 50).ema_indicator().iloc[-1]
    trend_ok = (side=='LONG' and ema_short > ema_long) or (side=='SHORT' and ema_short < ema_long)
    
    recent_high = df['high'].max()
    recent_low  = df['low'].min()
    prix = df['close'].iloc[-1]
    
    if side == 'LONG':
        sr_ok = prix - recent_low < (recent_high - recent_low) * 0.3
    else:
        sr_ok = recent_high - prix < (recent_high - recent_low) * 0.3
    
    vol_ok = df['volume'].iloc[-1] >= df['volume'].rolling(20).mean().iloc[-1]
    
    score = sum([trend_ok, sr_ok, vol_ok]) / 3
    return score >= 0.8

def trailing_sl(entry, risk, rr):
    """Calcule le SL suivant le RR atteint"""
    if rr >= 10: return entry + risk * 5
    if rr >= 5:  return entry + risk * 3
    if rr >= 3:  return entry + risk * 2
    if rr >= 2:  return entry + risk
    if rr >= 1:  return entry
    return None

def rr_bar(rr, max_rr=5):
    """Affichage simple barre RR"""
    size = 20
    filled = int(min(rr / max_rr, 1) * size)
    return "█" * filled + "░" * (size - filled)

# ================= LOOP PRINCIPALE =================
while True:
    try:
        pnl_latant = 0
        print("\n" + "="*40)
        print("🤖 ROBOTKING SIMPLE DASHBOARD")
        print("="*40)
        
        # Analyse BTC pour corrélation
        ohlcv_btc = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=50)
        df_btc = pd.DataFrame(ohlcv_btc, columns=['t','o','h','l','c','v'])
        ema50 = EMAIndicator(df_btc['c'], 50).ema_indicator().iloc[-1]
        ema200 = EMAIndicator(df_btc['c'], 200).ema_indicator().iloc[-1]
        btc_trend = 'BULL' if ema50 > ema200 else 'BEAR'
        
        print(f"🔗 BTC Trend: {btc_trend}")
        
        for sym, data in MARKETS.items():
            ohlcv = exchange.fetch_ohlcv(sym, '1m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            prix = df['c'].iloc[-1]
            ema = EMAIndicator(df['c'], 9).ema_indicator().iloc[-1]
            
            # --- Trade actif ---
            if data['active']:
                diff = (prix - data['entry']) if data['side'] == 'LONG' else (data['entry'] - prix)
                pnl = diff * data['lot']
                rr = diff / data['risk'] if data['risk'] != 0 else 0
                pnl_latant += pnl
                
                # Trailing SL
                new_sl = trailing_sl(data['entry'], data['risk'], rr)
                if new_sl:
                    if data['side'] == 'LONG': data['sl'] = max(data['sl'], new_sl)
                    else: data['sl'] = min(data['sl'], new_sl)
                
                # Sortie
                exit_condition = (data['side']=='LONG' and prix <= data['sl']) or \
                                 (data['side']=='SHORT' and prix >= data['sl'])
                if exit_condition:
                    CAPITAL += pnl
                    HISTORIQUE = f"{sym} {data['side']} | PNL: {pnl:.2f}$"
                    # Ajuste lot
                    if rr >= 0.95: data['lot'] *= 1.05  # série gagnante
                    elif rr <= -0.3: data['lot'] *= 0.95  # drawdown
                    data['active'] = False
                    print(f"❌ Trade fermé: {HISTORIQUE}")
                
                # Affichage trade actif
                else:
                    print(f"{sym} | {data['side']} | Entry: {data['entry']:.2f} | SL: {data['sl']:.2f} | R: {rr:.2f} | PNL: {pnl:.2f}$")
                    print(rr_bar(rr))
            
            # --- Analyse entrée ---
            else:
                side = 'LONG' if prix > ema else 'SHORT'
                # Vérifie zone essentielle + corrélation BTC
                if is_hot_zone(df, side) and ((btc_trend=='BULL' and side=='LONG') or (btc_trend=='BEAR' and side=='SHORT')):
                    dist_sl = prix * 0.003
                    data.update({
                        'active': True,
                        'side': side,
                        'entry': prix,
                        'sl': prix - dist_sl if side=='LONG' else prix + dist_sl,
                        'risk': dist_sl
                    })
                    print(f"🚀 HOT ZONE TRADE {sym} | {side} | Entry: {prix:.2f}")
                else:
                    print(f"{sym} | Pas de setup fiable → attente")
        
        print("-"*40)
        print(f"💰 Solde: {CAPITAL + pnl_latant:.2f}$ | Historique: {HISTORIQUE}")
        time.sleep(2)
    
    except Exception as e:
        print("❌ Erreur:", e)
        time.sleep(5)

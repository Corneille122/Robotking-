"""
ROBOTKING M1 - VERSION FINALE ULTRA-SÉCURISÉE
- Vérifie si prix atteint SL/TP
- Ferme MANUELLEMENT si Binance n'a pas fermé
- Trailing stop PHYSIQUE sur Binance
- Double vérification permanente
"""

import time, hmac, hashlib, requests, threading, os, json
from datetime import datetime, timezone
from flask import Flask
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('robotking.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= SERVEUR =================
app = Flask(__name__)

@app.route('/')
def home():
    stats = get_trading_stats()
    positions_html = get_positions_html()
    
    return f"""<html><head><title>RobotKing</title><meta http-equiv="refresh" content="3">
    <style>
        body {{ font-family: monospace; background: #0a0e27; color: #00ff88; padding: 20px; }}
        .stat {{ background: #1a1f3a; padding: 15px; margin: 10px; border: 2px solid #00ff88; border-radius: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 10px; border: 1px solid #00ff88; }}
        .profit {{ color: #00ff88; }}
        .loss {{ color: #ff3366; }}
    </style></head><body>
    <h1>🤖 ROBOTKING M1 - ULTRA SECURE</h1>
    <div class="stat">💰 Capital: ${stats['capital']:.2f} | 📈 Profit: ${stats['profit']:+.2f} | 📊 Positions: {stats['active_positions']}/4</div>
    {positions_html}
    <p style="text-align:center; color: #8899aa;">Refresh 3s | {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</p>
    </body></html>"""

def get_positions_html():
    positions = get_managed_positions()
    if not positions:
        return '<p>Aucune position</p>'
    
    rows = []
    for p in positions:
        pnl_class = 'profit' if p['pnl_usdt'] > 0 else 'loss'
        rows.append(f"<tr><td>{p['symbol']}</td><td>{p['side']}</td><td>${p['entry_price']:.6f}</td><td>${p['current_price']:.6f}</td><td class='{pnl_class}'>${p['pnl_usdt']:+.2f} ({p['pnl_pct']:+.2f}%)</td><td>SL: ${p['current_sl']:.6f}</td><td>TP: ${p['current_tp']:.6f}</td></tr>")
    
    return f"<table><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Current</th><th>PnL</th><th>SL</th><th>TP</th></tr>{''.join(rows)}</table>"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), use_reloader=False)

# ================= API =================
API_KEY = "YQL8N4sxGb6YF3RmfhaQIv2MMNuoB3AcQqf7x1YaVzARKoGb1TKjumwUVNZDW3af"
API_SECRET = "si08ii320XMByW4VY1VRt5zRJNnB3QrYBJc3QkDOdKHLZGKxyTo5CHxz7nd4CuQ0"
BASE_URL = "https://fapi.binance.com"

SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","ATOMUSDT","LTCUSDT","TRXUSDT","APTUSDT",
    "OPUSDT","ARBUSDT","INJUSDT","SUIUSDT","FTMUSDT",
    "NEARUSDT","FILUSDT","RUNEUSDT","PEPEUSDT"
]

LEVERAGE = 20
BASE_MARGIN = 0.50
MAX_POSITIONS = 4

# Configuration trailing
TRAILING_ACTIVATION = 0.008  # +0.8% pour activer
TRAILING_DISTANCE = 0.004    # -0.4% distance

# Variables globales
managed_positions = {}  # {symbol: {entry, sl, tp, sl_order_id, tp_order_id, trailing_active, highest/lowest}}
active_symbols = set()
trade_history = []
starting_capital = 5.0

# ================= FONCTIONS API =================
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def request_binance(method, path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time()*1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    
    for attempt in range(3):
        try:
            if method == "GET":
                resp = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
            elif method == "POST":
                resp = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(BASE_URL + path, params=params, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                if attempt < 2:
                    time.sleep(0.5)
        except:
            if attempt < 2:
                time.sleep(1)
    return None

def get_active_positions():
    try:
        pos_req = request_binance("GET", "/fapi/v2/positionRisk")
        if not pos_req:
            return []
        return [p for p in pos_req if float(p.get("positionAmt", 0)) != 0]
    except:
        return []

def get_balance():
    try:
        bal = request_binance("GET", "/fapi/v2/balance")
        if not bal:
            return 0.0
        usdt = next((a for a in bal if a.get("asset") == "USDT"), None)
        return float(usdt.get("balance", 0)) if usdt else 0.0
    except:
        return 0.0

def get_current_price(symbol):
    try:
        resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/price?symbol={symbol}", timeout=5)
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except:
        pass
    return None

def close_position_force(symbol, side, reason):
    """
    ⚠️ FERMETURE FORCÉE MANUELLE
    Utilisé quand Binance n'a pas fermé automatiquement
    """
    try:
        logger.warning(f"🔴 FERMETURE FORCÉE: {symbol} ({reason})")
        
        close_side = "SELL" if side == "LONG" else "BUY"
        
        # Méthode 1: closePosition
        result = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": close_side,
            "type": "MARKET",
            "closePosition": "true"
        })
        
        if result and "orderId" in result:
            logger.info(f"   ✅ Position fermée: {result['orderId']}")
            
            # Nettoyer
            if symbol in managed_positions:
                del managed_positions[symbol]
            if symbol in active_symbols:
                active_symbols.remove(symbol)
            
            return True
        
        # Méthode 2: Si closePosition échoue, utiliser la quantité
        time.sleep(0.5)
        positions = get_active_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        
        if pos:
            qty = abs(float(pos["positionAmt"]))
            if qty > 0:
                result2 = request_binance("POST", "/fapi/v1/order", {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "MARKET",
                    "quantity": qty
                })
                
                if result2 and "orderId" in result2:
                    logger.info(f"   ✅ Fermé avec quantité: {result2['orderId']}")
                    
                    if symbol in managed_positions:
                        del managed_positions[symbol]
                    if symbol in active_symbols:
                        active_symbols.remove(symbol)
                    
                    return True
        
        logger.error(f"   ❌ Échec fermeture forcée!")
        return False
        
    except Exception as e:
        logger.error(f"Erreur close_position_force: {e}")
        return False

def update_sl_order(symbol, new_sl_price, old_sl_order_id):
    """
    ⚠️ DÉPLACE PHYSIQUEMENT L'ORDRE SL SUR BINANCE
    Pour trailing stop
    """
    try:
        pos_data = managed_positions[symbol]
        side = pos_data["side"]
        
        logger.info(f"🔄 Déplacement SL: {symbol} → ${new_sl_price:.6f}")
        
        # 1. Annuler ancien SL
        if old_sl_order_id:
            cancel_result = request_binance("DELETE", "/fapi/v1/order", {
                "symbol": symbol,
                "orderId": old_sl_order_id
            })
            logger.info(f"   ❌ Ancien SL annulé")
            time.sleep(0.2)
        
        # 2. Créer nouveau SL
        close_side = "SELL" if side == "LONG" else "BUY"
        
        new_sl_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "stopPrice": round(new_sl_price, 8),
            "closePosition": "true"
        })
        
        if new_sl_order and "orderId" in new_sl_order:
            logger.info(f"   ✅ Nouveau SL créé: {new_sl_order['orderId']}")
            
            # Mettre à jour
            pos_data["current_sl"] = new_sl_price
            pos_data["sl_order_id"] = new_sl_order["orderId"]
            
            return True
        else:
            logger.error(f"   ❌ Échec création nouveau SL: {new_sl_order}")
            
            # CRITIQUE: Si on ne peut pas créer le SL, on surveille manuellement
            pos_data["manual_sl_monitoring"] = True
            
            return False
        
    except Exception as e:
        logger.error(f"Erreur update_sl_order: {e}")
        # Activer surveillance manuelle
        managed_positions[symbol]["manual_sl_monitoring"] = True
        return False

def check_if_sl_tp_hit(symbol, current_price, pos_data):
    """
    ⚠️ VÉRIFICATION CRITIQUE
    Vérifie si le prix a atteint SL ou TP
    Si oui ET position toujours ouverte → FERMER MANUELLEMENT
    """
    side = pos_data["side"]
    current_sl = pos_data["current_sl"]
    current_tp = pos_data["current_tp"]
    
    # Vérifier si SL atteint
    sl_hit = False
    tp_hit = False
    
    if side == "LONG":
        # SL atteint si prix <= SL
        if current_price <= current_sl * 1.0005:  # Marge 0.05%
            sl_hit = True
        
        # TP atteint si prix >= TP
        if current_price >= current_tp * 0.9995:
            tp_hit = True
    
    else:  # SHORT
        # SL atteint si prix >= SL
        if current_price >= current_sl * 0.9995:
            sl_hit = True
        
        # TP atteint si prix <= TP
        if current_price <= current_tp * 1.0005:
            tp_hit = True
    
    # Si SL atteint
    if sl_hit:
        logger.warning(f"⚠️ SL ATTEINT: {symbol} @ ${current_price:.6f} (SL: ${current_sl:.6f})")
        
        # Vérifier si position toujours ouverte
        positions = get_active_positions()
        still_open = any(p["symbol"] == symbol for p in positions)
        
        if still_open:
            logger.warning(f"   🔴 Position toujours ouverte! Fermeture forcée...")
            close_position_force(symbol, side, f"SL atteint @ ${current_price:.6f}")
        else:
            logger.info(f"   ✅ Position déjà fermée par Binance")
            
            if symbol in managed_positions:
                del managed_positions[symbol]
            if symbol in active_symbols:
                active_symbols.remove(symbol)
    
    # Si TP atteint
    if tp_hit:
        logger.info(f"💰 TP ATTEINT: {symbol} @ ${current_price:.6f} (TP: ${current_tp:.6f})")
        
        # Vérifier si position toujours ouverte
        positions = get_active_positions()
        still_open = any(p["symbol"] == symbol for p in positions)
        
        if still_open:
            logger.warning(f"   🔴 Position toujours ouverte! Fermeture forcée...")
            close_position_force(symbol, side, f"TP atteint @ ${current_price:.6f}")
        else:
            logger.info(f"   ✅ Position déjà fermée par Binance")
            
            if symbol in managed_positions:
                del managed_positions[symbol]
            if symbol in active_symbols:
                active_symbols.remove(symbol)

def manage_trailing_stop(symbol, current_price, pos_data):
    """
    Gère le trailing stop avec déplacement PHYSIQUE de l'ordre SL
    """
    side = pos_data["side"]
    entry_price = pos_data["entry_price"]
    current_sl = pos_data["current_sl"]
    
    # Calculer profit actuel
    if side == "LONG":
        profit_pct = (current_price - entry_price) / entry_price
    else:
        profit_pct = (entry_price - current_price) / entry_price
    
    # 1. Activer trailing si profit >= seuil
    if not pos_data.get("trailing_active", False):
        if profit_pct >= TRAILING_ACTIVATION:
            logger.info(f"🟡 TRAILING ACTIVÉ: {symbol} (+{profit_pct*100:.2f}%)")
            pos_data["trailing_active"] = True
            pos_data["highest_price"] = current_price if side == "LONG" else entry_price
            pos_data["lowest_price"] = current_price if side == "SHORT" else entry_price
    
    # 2. Si trailing actif, déplacer le SL
    if pos_data.get("trailing_active", False):
        if side == "LONG":
            # Mettre à jour le plus haut
            if current_price > pos_data["highest_price"]:
                pos_data["highest_price"] = current_price
                
                # Calculer nouveau SL
                new_sl = current_price * (1 - TRAILING_DISTANCE)
                
                # Déplacer SL seulement si nouveau SL > ancien SL
                if new_sl > current_sl * 1.002:  # Au moins 0.2% de différence
                    logger.info(f"   📈 High: ${current_price:.6f} → Nouveau SL: ${new_sl:.6f}")
                    
                    # DÉPLACER PHYSIQUEMENT sur Binance
                    success = update_sl_order(symbol, new_sl, pos_data.get("sl_order_id"))
                    
                    if not success:
                        # Si échec, activer surveillance manuelle
                        logger.warning(f"   ⚠️ Échec déplacement SL, surveillance manuelle active")
        
        else:  # SHORT
            # Mettre à jour le plus bas
            if current_price < pos_data["lowest_price"]:
                pos_data["lowest_price"] = current_price
                
                # Calculer nouveau SL
                new_sl = current_price * (1 + TRAILING_DISTANCE)
                
                # Déplacer SL seulement si nouveau SL < ancien SL
                if new_sl < current_sl * 0.998:
                    logger.info(f"   📉 Low: ${current_price:.6f} → Nouveau SL: ${new_sl:.6f}")
                    
                    # DÉPLACER PHYSIQUEMENT sur Binance
                    success = update_sl_order(symbol, new_sl, pos_data.get("sl_order_id"))
                    
                    if not success:
                        logger.warning(f"   ⚠️ Échec déplacement SL, surveillance manuelle active")

def get_managed_positions():
    """Récupère et enrichit les positions"""
    positions = get_active_positions()
    managed_data = []
    
    for pos in positions:
        symbol = pos["symbol"]
        position_amt = float(pos["positionAmt"])
        entry_price = float(pos["entryPrice"])
        mark_price = float(pos["markPrice"])
        unrealized_pnl = float(pos["unRealizedProfit"])
        
        side = "LONG" if position_amt > 0 else "SHORT"
        
        if side == "LONG":
            pnl_pct = ((mark_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - mark_price) / entry_price) * 100
        
        # Récupérer SL/TP actuels
        if symbol in managed_positions:
            current_sl = managed_positions[symbol].get("current_sl", 0)
            current_tp = managed_positions[symbol].get("current_tp", 0)
        else:
            current_sl = 0
            current_tp = 0
        
        managed_data.append({
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "current_price": mark_price,
            "pnl_usdt": unrealized_pnl,
            "pnl_pct": pnl_pct,
            "current_sl": current_sl,
            "current_tp": current_tp
        })
    
    return managed_data

def manage_all_positions():
    """
    🎯 FONCTION PRINCIPALE DE GESTION
    1. Vérifie si SL/TP atteints → Ferme si Binance n'a pas fermé
    2. Gère trailing stop → Déplace SL physiquement
    """
    positions = get_active_positions()
    
    if not positions:
        return
    
    for pos in positions:
        symbol = pos["symbol"]
        
        if symbol not in managed_positions:
            continue
        
        current_price = get_current_price(symbol)
        if not current_price:
            continue
        
        pos_data = managed_positions[symbol]
        
        # 1. VÉRIFIER SI SL/TP ATTEINTS
        check_if_sl_tp_hit(symbol, current_price, pos_data)
        
        # Vérifier si position toujours là (peut avoir été fermée ci-dessus)
        if symbol not in managed_positions:
            continue
        
        # 2. GÉRER TRAILING STOP
        manage_trailing_stop(symbol, current_price, pos_data)

# ================= ANALYSE =================
def get_klines(symbol, interval, limit):
    try:
        resp = requests.get(f"{BASE_URL}/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

def calculate_ema(closes, period):
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = closes[0]
    for close in closes[1:]:
        ema = (close - ema) * multiplier + ema
    return ema

def check_btc_trend():
    try:
        klines = get_klines("BTCUSDT", "1h", 200)
        if not klines or len(klines) < 200:
            return "NEUTRAL"
        closes = [float(k[4]) for k in klines]
        ema50 = calculate_ema(closes[-50:], 50)
        ema200 = calculate_ema(closes, 200)
        if not ema50 or not ema200:
            return "NEUTRAL"
        return "BULL" if ema50 > ema200 else "BEAR"
    except:
        return "NEUTRAL"

def score_stars(symbol):
    try:
        klines = get_klines(symbol, "1m", 50)
        if not klines or len(klines) < 50:
            return 0
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        
        stars = 0
        recent_high = max(highs[-20:-1])
        recent_low = min(lows[-20:-1])
        current_price = closes[-1]
        
        if current_price > recent_high * 1.001:
            stars += 2
        elif current_price < recent_low * 0.999:
            stars += 2
        
        for i in range(-5, -1):
            if lows[i] > highs[i-1] * 1.001 or highs[i] < lows[i-1] * 0.999:
                stars += 1
                break
        
        avg_volume = sum(volumes[-20:-1]) / 19
        if volumes[-1] > avg_volume * 1.5:
            stars += 1
        
        if abs(closes[-1] - closes[-5]) / closes[-5] > 0.003:
            stars += 1
        
        return min(stars, 5)
    except:
        return 0

def get_symbol_precision(symbol):
    try:
        info = request_binance("GET", "/fapi/v1/exchangeInfo")
        if not info:
            return 3
        symbol_info = next((s for s in info.get("symbols", []) if s["symbol"] == symbol), None)
        if not symbol_info:
            return 3
        lot_size = next((f for f in symbol_info.get("filters", []) if f["filterType"] == "LOT_SIZE"), None)
        if lot_size:
            step_size = lot_size.get("stepSize", "0.001")
            precision = len(step_size.rstrip('0').split('.')[-1]) if '.' in step_size else 0
            return precision
        return 3
    except:
        return 3

def place_order(symbol, side, qty, sl, tp):
    global active_symbols
    
    try:
        logger.info(f"\n🔥 {symbol} {side}")
        
        request_binance("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
        time.sleep(0.3)
        
        market_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty
        })
        
        if not market_order or "orderId" not in market_order:
            return False
        
        logger.info(f"   ✅ {market_order['orderId']}")
        time.sleep(0.3)
        
        opp_side = "SELL" if side == "BUY" else "BUY"
        
        sl_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": opp_side,
            "type": "STOP_MARKET",
            "stopPrice": round(sl, 8),
            "closePosition": "true"
        })
        
        sl_order_id = sl_order.get("orderId") if sl_order else None
        
        time.sleep(0.3)
        
        tp_order = request_binance("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": opp_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": round(tp, 8),
            "closePosition": "true"
        })
        
        tp_order_id = tp_order.get("orderId") if tp_order else None
        
        active_symbols.add(symbol)
        
        # Stocker dans managed_positions
        entry_price = get_current_price(symbol)
        if entry_price:
            managed_positions[symbol] = {
                "entry_price": entry_price,
                "side": "LONG" if side == "BUY" else "SHORT",
                "current_sl": sl,
                "current_tp": tp,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "trailing_active": False,
                "highest_price": entry_price,
                "lowest_price": entry_price,
                "manual_sl_monitoring": False
            }
        
        logger.info(f"✅ OUVERT")
        return True
        
    except Exception as e:
        logger.error(f"Erreur: {e}")
        return False

def get_trading_stats():
    balance = get_balance()
    positions = get_active_positions()
    return {
        "capital": balance,
        "profit": balance - starting_capital,
        "active_positions": len(positions)
    }

# ================= MAIN =================
def main_loop():
    global active_symbols
    
    logger.info("🚀 ROBOTKING M1 - ULTRA SECURE START")
    logger.info(f"📊 Dashboard: http://localhost:10000\n")
    
    while True:
        try:
            # 🎯 PRIORITÉ: Gérer positions
            manage_all_positions()
            
            # Sync
            positions = get_active_positions()
            active_symbols = {p["symbol"] for p in positions}
            
            # Scanner si places
            if len(active_symbols) < MAX_POSITIONS:
                trend = check_btc_trend()
                
                if trend != "NEUTRAL":
                    candidates = []
                    for sym in SYMBOLS:
                        if sym not in active_symbols:
                            stars = score_stars(sym)
                            if stars >= 3:
                                candidates.append((sym, stars))
                    
                    if candidates:
                        candidates.sort(key=lambda x: x[1], reverse=True)
                        best, _ = candidates[0]
                        
                        price = get_current_price(best)
                        if price:
                            prec = get_symbol_precision(best)
                            qty = round((BASE_MARGIN * LEVERAGE) / price, prec)
                            
                            side = "BUY" if trend == "BULL" else "SELL"
                            
                            if side == "BUY":
                                sl = price * 0.99
                                tp = price * 1.02
                            else:
                                sl = price * 1.01
                                tp = price * 0.98
                            
                            place_order(best, side, qty, sl, tp)
            
            time.sleep(3)
            
        except KeyboardInterrupt:
            logger.info("\n⛔ Arrêt")
            break
        except Exception as e:
            logger.error(f"Erreur: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(2)
    main_loop()

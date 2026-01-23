import requests
import time
import os

# ================== CONFIGURATION STRATÉGIQUE ==================
CAPITAL_INITIAL = 5.0
RISQUE_BTC = 0.6             # Risque 0.6$ spécifié pour BTC
RISQUE_ALTS = 0.3            # Risque 0.3$ pour les Alts
MAX_TRADES = 3               # 3 trades simultanés max
MIN_LOT_BTC = 0.003          # Lot minimum BTC
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"]
RR_TARGET = 2.0

def get_live_data(symbol):
    """Analyse M1 avec exécution 1s"""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=20"
    try:
        data = requests.get(url, timeout=1).json()
        current_price = float(data[-1][4])
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        
        # Volatilité ATR M1
        vol = sum([highs[i] - lows[i] for i in range(-5, 0)]) / 5
        
        # Détection SMC : FVG & Liquidité
        fvg_up = float(data[-3][2]) < float(data[-1][3])
        fvg_down = float(data[-3][3]) > float(data[-1][2])
        sweep_low = current_price < min(lows[-15:-1]) # A balayé les bas
        sweep_high = current_price > max(highs[-15:-1]) # A balayé les hauts
        
        is_bullish = current_price > float(data[-1][1])
        return current_price, vol, fvg_up, fvg_down, is_bullish, sweep_low, sweep_high
    except: return None, 0, False, False, False, False, False

class ActiveTrade:
    def __init__(self, symbol, direction, price, vol, risk):
        self.symbol, self.direction, self.entry_price = symbol, direction, price
        self.risk_usd = risk
        self.sl_dist = max(vol * 1.5, price * 0.0008)
        self.sl = price - self.sl_dist if direction == "BUY" else price + self.sl_dist
        self.tp = price + (self.sl_dist * RR_TARGET) if direction == "BUY" else price - (self.sl_dist * RR_TARGET)
        
        calc_lot = risk / self.sl_dist
        self.lot = max(calc_lot, MIN_LOT_BTC) if symbol == "BTCUSDT" else round(calc_lot, 4)
        self.pnl, self.rr_dyn, self.active = 0.0, 0.0, True

    def refresh(self, price, vol):
        self.pnl = (price - self.entry_price) * self.lot if self.direction == "BUY" else (self.entry_price - price) * self.lot
        self.rr_dyn = self.pnl / self.risk_usd if self.risk_usd > 0 else 0
        # Trailing Stop serré
        if self.direction == "BUY":
            if price - (vol * 1.2) > self.sl: self.sl = price - (vol * 1.2)
            if price <= self.sl or price >= self.tp: self.active = False
        else:
            if price + (vol * 1.2) < self.sl: self.sl = price + (vol * 1.2)
            if price >= self.sl or price <= self.tp: self.active = False

class SoroRobot:
    def __init__(self):
        self.capital = CAPITAL_INITIAL
        self.trades, self.history = [], {"W": 0, "L": 0}
        self.btc_state = "SCAN"

    def apply_money_management(self):
        """Règles : +300% / -30%"""
        if self.capital >= CAPITAL_INITIAL * 4: return 1.05
        if self.capital <= CAPITAL_INITIAL * 0.7: return 0.95
        return 1.0

    def render(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"--- 💠 ALPHA-TERMINAL M1 | BTC MODE: {self.btc_state} ---")
        print(f"💰 CAPITAL: {self.capital:.2f}$ | SCORE: {self.history['W']}W - {self.history['L']}L")
        print("=" * 125)
        print(f"{'SYMBOLE':<10} | {'DIR':<5} | {'LOT':<8} | {'RR DYN':<7} | {'ENTRÉE':<10} | {'LIVE':<10} | {'SL':<10} | {'TP':<10} | {'PNL'}")
        print("-" * 125)
        for t in self.trades:
            c = "\033[92m" if t.pnl > 0 else "\033[91m"
            print(f"{t.symbol:<10} | {t.direction:<5} | {t.lot:<8.4f} | {t.rr_dyn:>5.2f}x | {t.entry_price:<10.2f} | {t.entry_price+t.pnl/t.lot:<10.2f} | {t.sl:<10.2f} | {t.tp:<10.2f} | {c}{t.pnl:>8.2f}$\033[0m")
        print("=" * 125)

    def start(self):
        while True:
            mod = self.apply_money_management()
            # 1. Analyse Maître BTC
            bp, bv, bf_u, bf_d, b_bull, b_sw_l, b_sw_h = get_live_data("BTCUSDT")
            self.btc_state = "BULL" if b_bull else "BEAR"

            for sym in SYMBOLS:
                if not any(tr.symbol == sym for tr in self.trades) and len(self.trades) < MAX_TRADES:
                    p, v, f_u, f_d, bull, sw_l, sw_h = get_live_data(sym)
                    if p:
                        risk = (RISQUE_BTC if sym == "BTCUSDT" else RISQUE_ALTS) * mod
                        # LOGIQUE M1 OPTIMISÉE
                        if f_u and b_bull: # FVG + Corrélation BTC
                            self.trades.append(ActiveTrade(sym, "BUY", p, v, risk))
                        elif f_d and not b_bull:
                            self.trades.append(ActiveTrade(sym, "SELL", p, v, risk))

            for t in self.trades[:]:
                p, v, _, _, _, _, _ = get_live_data(t.symbol)
                if p:
                    t.refresh(p, v)
                    if not t.active:
                        self.capital += t.pnl
                        self.history["W" if t.pnl > 0 else "L"] += 1
                        self.trades.remove(t)
            self.render()
            time.sleep(1)

if __name__ == "__main__":
    SoroRobot().start()

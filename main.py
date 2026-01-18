import ccxt
import pandas as pd
import time

# --- CONFIGURATION TEST 24H ---
exchange = ccxt.binance({
    'apiKey': 'TES_CLES_TESTNET',
    'secret': 'TES_SECRET_TESTNET',
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})
exchange.set_sandbox_mode(True) # Mode Testnet activé

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
SOLDE_INITIAL = 10.0
RISQUE_NORMAL = 0.50 # Ton réglage 0.5$
RISQUE_REDUIT = 0.10 # Ta règle du 1% (0.10$ sur 10$)
DERNIER_GAIN = True  # Devient False si le robot perd

def calculate_size(symbol, price, balance):
    global DERNIER_GAIN
    # Règle du 13/01 : 1% si perte, sinon 0.5$
    risk = RISQUE_NORMAL if DERNIER_GAIN else RISQUE_REDUIT
    # Levier x20 pour permettre les petits montants
    quantity = (risk * 20) / price
    return exchange.amount_to_precision(symbol, quantity)

print("🚀 ROBOT SMC V17 - TEST FUTURES DÉMARRÉ")

while True:
    try:
        balance = exchange.fetch_balance()['total']['USDT']
        for symbol in SYMBOLS:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker['last']
            print(f"[{time.strftime('%H:%M')}] Scan {symbol}: {price}$ | Solde: {balance}$")
            
            # Ici le robot cherche le signal SMC...
            # Si signal : qty = calculate_size(symbol, price, balance)
            
        time.sleep(300)
    except Exception as e:
        print(f"Erreur: {e}")
        time.sleep(10)

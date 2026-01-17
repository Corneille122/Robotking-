import requests
import time

# --- CONFIGURATION DU ROBOT 24H ---
SYMBOLE = "BTCUSDT"
SOLDE_INITIAL = 5.0
RISQUE_NORMAL = 0.30
RISQUE_REDUIT = 0.05  # Règle du 1% après une perte
DERNIER_GAIN = True

print("🚀 ROBOT SMC V14 EN LIGNE - TEST 24H DÉMARRÉ")

while True:
    try:
        # Le robot scanne le prix sur Binance
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOLE}"
        prix = float(requests.get(url).json()['price'])
        
        # Logique simplifiée pour les logs du test
        print(f"[{time.strftime('%H:%M:%S')}] Scan {SYMBOLE} | Prix: {prix} | Solde: {SOLDE_INITIAL}$")
        
        # Attend 5 minutes (bougie M5)
        time.sleep(300) 
    except Exception as e:
        print(f"⚠️ Erreur de connexion : {e}")
        time.sleep(60)

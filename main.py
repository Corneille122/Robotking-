import ccxt
import time
import random

# Connexion aux prix réels de Binance
exchange = ccxt.binance()

# --- VOS RÉGLAGES ---
CAPITAL_DE_DEPART = 5.0
SYMBOLE = 'BTC/USDT'
TEMPS_ANALYSE = 60  # 1 minute d'observation pour calculer la probabilité
PAUSE_BOUGIE = 300  # 5 minutes entre chaque cycle

class RobotLeader:
    def __init__(self):
        self.capital = CAPITAL_DE_DEPART
        self.lot_size = 0.05  # 5% au début

    def analyser_probabilite(self):
        print(f"\n🔍 DEBUT DE L'ANALYSE (Attente de {TEMPS_ANALYSE}s)...")
        # Premier relevé de prix
        p1 = exchange.fetch_ticker(SYMBOLE)['last']
        time.sleep(TEMPS_ANALYSE)
        # Deuxième relevé de prix après 1 minute
        p2 = exchange.fetch_ticker(SYMBOLE)['last']
        
        variation = ((p2 - p1) / p1) * 100
        # Calcul du setup : plus le mouvement est fort, plus la probabilité grimpe
        proba = min(abs(variation) * 2000, 99) 
        return p2, variation, proba

    def run(self):
        print(f"🚀 Robotking lancé avec {self.capital}$ sur {SYMBOLE}")
        
        while self.capital > 0.5:
            try:
                prix_live, changement, proba = self.analyser_probabilite()
                
                print(f"📊 Live BTC: {prix_live}$ | Probabilité de réussite: {round(proba, 1)}%")
                
                # SEUIL DE PROBABILITÉ (Le robot ne trade que si setup > 60%)
                if proba > 60:
                    montant_trade = self.capital * self.lot_size
                    print(f"⚡ Setup validé ! Position de {round(montant_trade, 2)}$")
                    
                    # Simulation de l'issue du trade
                    if changement > 0: # Le prix montait pendant l'analyse
                        gain = montant_trade * 0.4
                        self.capital += gain
                        print(f"✅ GAGNÉ : +{round(gain, 2)}$ | Capital: {round(self.capital, 2)}$")
                        self.lot_size = 0.05 # Reste ou revient à 5%
                    else:
                        self.capital -= montant_trade
                        print(f"❌ PERDU : -{round(montant_trade, 2)}$")
                        # VOTRE RÈGLE DE SÉCURITÉ
                        self.lot_size = 0.01
                        print(f"⚠️ Alerte Stop Loss : Prochain lot réduit à 1% ({round(self.capital * 0.01, 2)}$)")
                else:
                    print("💤 Probabilité trop faible. Pas de trade pour ce cycle.")

                print(f"⏳ Attente de {PAUSE_BOUGIE/60} min avant le prochain cycle...")
                time.sleep(PAUSE_BOUGIE)

            except Exception as e:
                print(f"Erreur connexion : {e}")
                time.sleep(10)

# Lancement
bot = RobotLeader()
bot.run()

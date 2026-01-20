import requests
import pandas as pd
import time

# ================= CONFIG =================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LEVERAGE = 20
MISE_MINIMALE = 0.3      
CAPITAL_INITIAL = 5.0    
TEMPS_REFLEXION = 30     # 30 secondes d'analyse
# =========================================

class RobotKingPro:
    def __init__(self):
        self.solde = CAPITAL_INITIAL
        self.capital_max = CAPITAL_INITIAL
        self.risque_actuel = 0.05

    def analyser_set_up(self, symbol):
        """ Phase de calcul du Pour et du Contre (30s) """
        print(f"🔍 Analyse de {symbol} en cours... (Attente de 30s)")
        
        # 1. État au début des 30s
        prix_debut = 50000 # Simulation via API
        
        time.sleep(TEMPS_REFLEXION) # PAUSE DE RÉFLEXION
        
        # 2. État à la fin des 30s
        prix_fin = 50050 # Simulation via API
        
        variation = ((prix_fin - prix_debut) / prix_debut) * 100
        
        # Le "Pour" : Le prix confirme la direction
        # Le "Contre" : Le prix hésite ou fait du surplace
        if abs(variation) > 0.02: 
            return True, prix_fin # Set-up validé
        return False, prix_fin # Set-up rejeté (trop d'hésitation)

    def ajuster_gestion_risque(self):
        # Hausse du risque si ROI > 300%
        if self.solde >= (self.capital_max * 3.0):
            self.risque_actuel += 0.05
            self.capital_max = self.solde
            print(f"🚀 Risque augmenté (+5%)")
            
        # Baisse du risque si Perte > 30%
        if self.solde <= (self.capital_max * 0.70):
            self.risque_actuel = max(0.01, self.risque_actuel - 0.05)
            self.capital_max = self.solde
            print(f"⚠️ Risque réduit (-5%)")

    def executer(self):
        print(f"🤖 Robotking V17 en ligne | Mise de base: {MISE_MINIMALE}$")
        while True:
            self.ajuster_gestion_risque()
            
            for symbol in SYMBOLS:
                # Étape de réflexion de 30 secondes
                valide, prix = self.analyser_set_up(symbol)
                
                if valide:
                    mise = max(MISE_MINIMALE, self.solde * self.risque_actuel)
                    print(f"✅ Set-up validé sur {symbol} ! Entrée avec {round(mise, 2)}$")
                else:
                    print(f"❌ Set-up rejeté sur {symbol} (Manque de conviction)")
            
            print(f"⏳ Repos avant le prochain cycle de 5 minutes...")
            time.sleep(300)

# Lancement
bot = RobotKingPro()
bot.executer()

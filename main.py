#!/usr/bin/env python3
"""
AlphaBot Pro — @leaderodg_bot
VERSION 4
- Scan BTC + FOREX (EUR/USD, GBP/USD, XAU/USD, ETH...)
- VIP : 5$/mois OU dépôt unique 50$ (accès permanent)
- Parrainage : 1 filleul = 7 jours VIP gratuits
- Affiliation renforcée
- Dépôt : Telegram Wallet + Binance (TRC-20 uniquement)
- Canal VIP : @leadres
"""

import asyncio
import random
import json
import os
import statistics
from datetime import datetime, timedelta

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# ═══════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════

BOT_TOKEN        = '6950706659:AAGXw-27ebhWLm2HfG7lzC7EckpwCPS_JFg'
CHANNEL_ID       = "-1003757467015"
LIEN_GROUPE      = "https://t.me/+ty6G7ms4XpQzMDhk"
BOT_USERNAME     = "leaderodg_bot"
ADMIN_ID         = 6982051442
ADMIN_USERNAME   = "@leaderOdg"
COMMISSION_PCT   = 20

# ─── API Binance (trading automatique) ─────────────────
# Sur Binance : Compte → Gestion API → Créer API
# Active uniquement : "Enable Spot & Margin Trading"
BINANCE_API_KEY       = "REMPLACE_PAR_TA_CLE_API_BINANCE"
BINANCE_API_SECRET    = "REMPLACE_PAR_TON_SECRET_API_BINANCE"
BINANCE_TRADING_ACTIF = False   # Mets True pour activer les trades réels
MISE_PAR_TRADE_USDT   = 10      # Montant USDT par trade (ex: 10$)

# ─── Alertes Email ─────────────────────────────────────
# Gmail → Mon compte → Sécurité → Mots de passe des applications
EMAIL_ACTIF        = False
EMAIL_EXPEDITEUR   = "tonbot@gmail.com"
EMAIL_MOT_PASSE    = "xxxx xxxx xxxx xxxx"   # mot de passe app Gmail (16 caractères)
EMAIL_DESTINATAIRE = "tonemail@gmail.com"

# ─── Tarifs VIP ────────────────────────────────────────
PRIX_MENSUEL     = 5      # 5 USDT/mois
PRIX_DEPOT_VIP   = 50     # 50 USDT = accès VIP permanent (ou longue durée)

# ─── Canal VIP ─────────────────────────────────────────
CANAL_VIP        = "https://t.me/leadres"
CANAL_VIP_ID     = "@leadres"

# ─── Adresses de dépôt USDT ────────────────────────────
# ⚠️  Remplace par tes vraies adresses !
ADRESSE_TRC20        = "TJuPBihvzgb6ffGLw4WnqC33Av38kwU7XE"       # Binance TRC-20 uniquement
ADRESSE_TELEGRAM_TON = "REMPLACE_PAR_TON_ADRESSE_TELEGRAM"    # @wallet Telegram (TON/USDT)

# ─── Parrainage ─────────────────────────────────────────
JOURS_VIP_PAR_PARRAINAGE = 7

# ─── Binance affiliation ────────────────────────────────
LIEN_BINANCE     = "https://www.binance.com/register?ref=439082242"  # Lien d'affiliation Binance

DB_FILE = "alphabot_db.json"

# ═══════════════════════════════════════════════════════
# ALERTES EMAIL
# ═══════════════════════════════════════════════════════

def envoyer_email(sujet, corps):
    """Envoie une alerte par email si EMAIL_ACTIF = True"""
    if not EMAIL_ACTIF:
        return
    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL_EXPEDITEUR
        msg["To"]      = EMAIL_DESTINATAIRE
        msg["Subject"] = sujet
        msg.attach(MIMEText(corps, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_EXPEDITEUR, EMAIL_MOT_PASSE)
            server.send_message(msg)
        print("[EMAIL] Alerte envoyée : " + sujet)
    except Exception as e:
        print("[EMAIL] Erreur : " + str(e))


# ═══════════════════════════════════════════════════════
# TRADING BINANCE AUTOMATIQUE
# ═══════════════════════════════════════════════════════

def executer_trade_binance(signal):
    """
    Exécute un ordre MARKET sur Binance via l'API.
    Nécessite : BINANCE_TRADING_ACTIF = True + clés API valides.
    """
    if not BINANCE_TRADING_ACTIF:
        print("[BINANCE] Trading désactivé (BINANCE_TRADING_ACTIF = False)")
        return None
    try:
        from binance.client import Client
        from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

        client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

        # Calcul de la quantité à acheter
        symbol   = signal["paire"].replace("/", "").replace(" (Or)", "")
        ticker   = client.get_symbol_ticker(symbol=symbol)
        prix     = float(ticker["price"])
        quantite = round(MISE_PAR_TRADE_USDT / prix, 4)

        side = SIDE_BUY if signal["direction"] == "LONG" else SIDE_SELL

        ordre = client.order_market(
            symbol   = symbol,
            side     = side,
            quantity = quantite
        )

        resultat = (
            "TRADE EXECUTE SUR BINANCE\n"
            "Paire    : " + symbol + "\n"
            "Direction: " + signal["direction"] + "\n"
            "Quantite : " + str(quantite) + "\n"
            "Prix     : " + str(prix) + " USDT\n"
            "Mise     : ~" + str(MISE_PAR_TRADE_USDT) + " USDT\n"
            "Order ID : " + str(ordre.get("orderId", "N/A"))
        )
        print("[BINANCE] " + resultat)

        # Alerte email
        envoyer_email(
            "⚡ AlphaBot — Trade " + signal["direction"] + " " + symbol,
            resultat
        )
        return ordre

    except ImportError:
        print("[BINANCE] Installe python-binance : pip install python-binance")
        return None
    except Exception as e:
        print("[BINANCE] Erreur trade : " + str(e))
        envoyer_email("⚠️ AlphaBot — Erreur Trade", "Erreur : " + str(e))
        return None


# ═══════════════════════════════════════════════════════
# PAIRES FOREX & CRYPTO A SCANNER
# ═══════════════════════════════════════════════════════

# Paires Crypto Futures sur Binance
PAIRES_CRYPTO = [
    {"symbol": "BTCUSDT",  "nom": "BTC/USDT",  "emoji": "₿"},
    {"symbol": "ETHUSDT",  "nom": "ETH/USDT",  "emoji": "Ξ"},
    {"symbol": "SOLUSDT",  "nom": "SOL/USDT",  "emoji": "◎"},
    {"symbol": "BNBUSDT",  "nom": "BNB/USDT",  "emoji": "🔶"},
    {"symbol": "XRPUSDT",  "nom": "XRP/USDT",  "emoji": "💧"},
]

# Paires Forex via API publique gratuite (exchangerate-api / frankfurter)
PAIRES_FOREX = [
    {"base": "EUR", "quote": "USD", "nom": "EUR/USD", "emoji": "🇪🇺"},
    {"base": "GBP", "quote": "USD", "nom": "GBP/USD", "emoji": "🇬🇧"},
    {"base": "USD", "quote": "JPY", "nom": "USD/JPY", "emoji": "🇯🇵"},
    {"base": "USD", "quote": "CHF", "nom": "USD/CHF", "emoji": "🇨🇭"},
    {"base": "XAU", "quote": "USD", "nom": "XAU/USD (Or)", "emoji": "🥇"},
]

# ═══════════════════════════════════════════════════════
# BASE DE DONNÉES
# ═══════════════════════════════════════════════════════

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"affilies": {}, "ventes": [], "partages": {}, "vip_members": {}}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def get_affilie(user_id):
    return load_db()["affilies"].get(str(user_id))

def creer_affilie(user_id, username, nom):
    db   = load_db()
    code = "ALPHA" + str(user_id)[-4:]
    db["affilies"][str(user_id)] = {
        "user_id":      user_id,
        "username":     username or "inconnu",
        "nom":          nom,
        "code":         code,
        "clics":        0,
        "inscriptions": 0,
        "ventes":       0,
        "gains":        0.0,
        "partages":     0,
        "vip_jours":    0,
        "rejoint_le":   datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    save_db(db)
    return db["affilies"][str(user_id)]

def enregistrer_vente(code_affilie, montant):
    db         = load_db()
    commission = montant * COMMISSION_PCT / 100
    for uid, aff in db["affilies"].items():
        if aff["code"] == code_affilie:
            db["affilies"][uid]["ventes"] += 1
            db["affilies"][uid]["gains"]  += commission
            db["ventes"].append({
                "code":       code_affilie,
                "montant":    montant,
                "commission": commission,
                "date":       datetime.now().strftime("%d/%m/%Y %H:%M"),
            })
            save_db(db)
            return commission, int(uid)
    return 0, None

def enregistrer_parrainage(parrain_uid):
    db  = load_db()
    uid = str(parrain_uid)
    if uid in db["affilies"]:
        db["affilies"][uid]["inscriptions"] += 1
        db["affilies"][uid]["vip_jours"] = db["affilies"][uid].get("vip_jours", 0) + JOURS_VIP_PAR_PARRAINAGE
        save_db(db)
        return db["affilies"][uid]["vip_jours"]
    return 0

def enregistrer_partage(user_id):
    db  = load_db()
    uid = str(user_id)
    db["partages"][uid] = db["partages"].get(uid, 0) + 1
    if uid in db["affilies"]:
        db["affilies"][uid]["partages"] = db["affilies"][uid].get("partages", 0) + 1
    save_db(db)
    return db["partages"][uid]

def get_palier(nb):
    if nb >= 50: return ("🏆 ELITE",  "3 mois VIP + badge Elite + 30% commission a vie")
    if nb >= 20: return ("🥇 GOLD",   "1 mois VIP + 25% commission 60 jours")
    if nb >= 10: return ("🥈 SILVER", "7 jours premium + commission 22%")
    if nb >= 5:  return ("🥉 BRONZE", "3 jours premium + sticker exclusif")
    if nb >= 1:  return ("⭐ STARTER","1 jour premium offert")
    return None

# ═══════════════════════════════════════════════════════
# DONNÉES MARCHÉ
# ═══════════════════════════════════════════════════════

def get_crypto_ticker(symbol):
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol}, timeout=5
        )
        d = r.json()
        return {
            "price":  float(d["lastPrice"]),
            "change": float(d["priceChangePercent"]),
            "high":   float(d["highPrice"]),
            "low":    float(d["lowPrice"]),
        }
    except:
        return None

def get_btc():
    return get_crypto_ticker("BTCUSDT")

def get_candles(symbol="BTCUSDT", interval="1h", limit=50):
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5
        )
        return [
            {"open": float(c[1]), "high": float(c[2]),
             "low":  float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in r.json()
        ]
    except:
        return []

def get_forex_prix(base, quote):
    """
    Récupère le prix Forex via l'API publique Frankfurter.
    Supporte EUR, GBP, USD, JPY, CHF.
    Pour XAU (or), utilise une API alternative.
    """
    try:
        if base == "XAU":
            # Or via Metals-API (gratuit limité) ou fallback Binance XAUUSDT
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "XAUUSDT"}, timeout=5
            )
            price = float(r.json()["price"])
            return {"price": price, "change": 0.0}
        else:
            r = requests.get(
                "https://api.frankfurter.app/latest",
                params={"from": base, "to": quote}, timeout=5
            )
            d = r.json()
            price = d["rates"][quote]
            return {"price": price, "change": 0.0}
    except:
        return None

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except:
        return {"value": 50, "label": "Neutral"}

# ═══════════════════════════════════════════════════════
# DÉTECTION ORDER BLOCK (Crypto + Forex)
# ═══════════════════════════════════════════════════════

def detecter_order_block(candles, nom_paire="BTC/USDT", type_marche="CRYPTO"):
    if len(candles) < 10:
        return None
    corps_moy = statistics.mean([abs(x["close"] - x["open"]) for x in candles])
    signal    = None
    for i in range(3, len(candles) - 3):
        c      = candles[i]
        apres1 = candles[i + 1]
        apres2 = candles[i + 2]
        apres3 = candles[i + 3]
        corps  = abs(c["close"] - c["open"])
        forte  = corps > corps_moy * 1.5
        spread = c["high"] - c["low"]
        if spread == 0:
            continue

        if (c["close"] < c["open"] and forte
                and apres1["close"] > apres1["open"]
                and apres2["close"] > apres2["open"]
                and apres3["high"] > c["high"]):
            signal = {
                "paire":      nom_paire,
                "marche":     type_marche,
                "type":       "ORDER BLOCK HAUSSIER",
                "direction":  "LONG",
                "zone_haute": round(c["high"], 5 if type_marche == "FOREX" else 1),
                "zone_basse": round(c["low"],  5 if type_marche == "FOREX" else 1),
                "entree":     round(c["high"], 5 if type_marche == "FOREX" else 1),
                "sl":         round(c["low"]  - spread * 0.1, 5 if type_marche == "FOREX" else 1),
                "tp1":        round(c["high"] + spread * 1.5, 5 if type_marche == "FOREX" else 1),
                "tp2":        round(c["high"] + spread * 3.0, 5 if type_marche == "FOREX" else 1),
                "tp3":        round(c["high"] + spread * 5.0, 5 if type_marche == "FOREX" else 1),
                "force":      round(corps / corps_moy, 2),
                "volume":     round(c["volume"], 2),
            }
        elif (c["close"] > c["open"] and forte
                and apres1["close"] < apres1["open"]
                and apres2["close"] < apres2["open"]
                and apres3["low"] < c["low"]):
            signal = {
                "paire":      nom_paire,
                "marche":     type_marche,
                "type":       "ORDER BLOCK BAISSIER",
                "direction":  "SHORT",
                "zone_haute": round(c["high"], 5 if type_marche == "FOREX" else 1),
                "zone_basse": round(c["low"],  5 if type_marche == "FOREX" else 1),
                "entree":     round(c["low"],  5 if type_marche == "FOREX" else 1),
                "sl":         round(c["high"] + spread * 0.1, 5 if type_marche == "FOREX" else 1),
                "tp1":        round(c["low"]  - spread * 1.5, 5 if type_marche == "FOREX" else 1),
                "tp2":        round(c["low"]  - spread * 3.0, 5 if type_marche == "FOREX" else 1),
                "tp3":        round(c["low"]  - spread * 5.0, 5 if type_marche == "FOREX" else 1),
                "force":      round(corps / corps_moy, 2),
                "volume":     round(c["volume"], 2),
            }
    return signal

def fondamental_confirme(signal, fg, btc):
    if signal["marche"] == "FOREX":
        # Pour le Forex : on vérifie juste la force OB
        return signal["force"] >= 1.6
    if not fg or not btc:
        return False
    if signal["direction"] == "LONG":
        return fg["value"] >= 40 and btc["change"] > 0.3
    return fg["value"] <= 60 and btc["change"] < -0.3

# ═══════════════════════════════════════════════════════
# CITATIONS MOTIVATION
# ═══════════════════════════════════════════════════════

MOTIVATIONS = [
    ("La discipline est le pont entre les objectifs et les accomplissements.", "Jim Rohn"),
    ("Le succes ne vient pas a toi. Tu vas a lui.", "Marva Collins"),
    ("Le seul mauvais trade est celui qu'on ne coupe pas a temps.", "AlphaBot Pro"),
    ("La peur de perdre est plus grande que le desir de gagner. Maitrise-la.", "AlphaBot Pro"),
    ("Un trader patient bat un trader impatient a chaque fois.", "AlphaBot Pro"),
    ("Ne tradez pas pour vous amuser. Tradez pour gagner.", "Jesse Livermore"),
    ("Les marches recompensent la patience et punissent l'impulsivite.", "AlphaBot Pro"),
    ("Protegez votre capital d'abord. Les profits viennent naturellement.", "Paul Tudor Jones"),
    ("Le trading est un marathon, pas un sprint.", "AlphaBot Pro"),
    ("Votre mindset fait 80% de votre performance.", "AlphaBot Pro"),
    ("Coupez les pertes vite. Laissez courir les profits.", "Regle d'or"),
    ("La richesse est construite lentement, prudemment, constamment.", "Warren Buffett"),
    ("Les meilleurs traders perdent souvent. Ils gerent juste mieux.", "AlphaBot Pro"),
    ("Chaque expert a ete un jour un debutant.", "Helen Hayes"),
    ("Un bon systeme + de la discipline = resultats constants.", "AlphaBot Pro"),
    ("Celui qui maitrise ses emotions maitrise les marches.", "AlphaBot Pro"),
    ("Pas de trade sans plan. Pas de plan sans discipline.", "AlphaBot Pro"),
    ("Le risque vient de ne pas savoir ce que tu fais.", "Warren Buffett"),
]

# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

def fg_label(val):
    if val < 25:  return "Peur Extreme"
    if val < 45:  return "Peur"
    if val < 55:  return "Neutre"
    if val < 75:  return "Cupidite"
    return "Cupidite Extreme"

def fg_emoji(val):
    if val < 25:  return "😱"
    if val < 45:  return "😰"
    if val < 55:  return "😐"
    if val < 75:  return "😊"
    return "🤑"

def date_fr():
    now   = datetime.now()
    jours = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
    mois  = ["","Janvier","Fevrier","Mars","Avril","Mai","Juin",
              "Juillet","Aout","Septembre","Octobre","Novembre","Decembre"]
    return jours[now.weekday()] + " " + str(now.day) + " " + mois[now.month] + " " + str(now.year)

def ligne_btc(btc):
    if not btc:
        return "BTC : chargement..."
    e = "📈" if btc["change"] >= 0 else "📉"
    return e + " BTC : " + "{:,.0f}".format(btc["price"]) + "$ (" + "{:+.2f}".format(btc["change"]) + "%)"

def sep():
    return "─" * 30

# ═══════════════════════════════════════════════════════
# BLOC FONDAMENTAL
# ═══════════════════════════════════════════════════════

def construire_bloc_fondamental(signal, fg, btc):
    is_long = signal["direction"] == "LONG"
    lignes  = []

    if signal["marche"] == "FOREX":
        # Bloc fondamental Forex
        lignes.append("📌 Marche : FOREX — " + signal["paire"])
        force = signal["force"]
        if force >= 2.5:
            lignes.append("🧱 Force OB : " + str(force) + "x — Structure institutionnelle tres forte")
        elif force >= 1.8:
            lignes.append("🧱 Force OB : " + str(force) + "x — Imbalance confirmee")
        else:
            lignes.append("🧱 Force OB : " + str(force) + "x — Zone d'interet moderee")
        lignes.append("📍 Zone OB : " + str(signal["zone_basse"]) + " — " + str(signal["zone_haute"]))
        lignes.append("   ✅ Prix revenu tester la zone — reaction attendue")
        lignes.append("")
        if is_long:
            lignes.append("🔎 CONCLUSION : OB haussier valide sur " + signal["paire"])
            lignes.append("   → Demande institutionnelle presente. Setup VALIDE ✅")
        else:
            lignes.append("🔎 CONCLUSION : OB baissier valide sur " + signal["paire"])
            lignes.append("   → Offre institutionnelle presente. Setup VALIDE ✅")
        return "\n".join(lignes)

    # Bloc fondamental Crypto
    fgv = fg["value"] if fg else 50
    if is_long:
        fg_txt = ("✅ Peur extreme (" + str(fgv) + ") — acheteurs au plus bas, timing parfait" if fgv < 35
             else "✅ Peur (" + str(fgv) + ") — marche sous-evalue, biais LONG"               if fgv < 50
             else "⚠️  Cupidite (" + str(fgv) + ") — vigilance, possible retournement")
    else:
        fg_txt = ("✅ Cupidite extreme (" + str(fgv) + ") — vendeurs au sommet, timing parfait" if fgv > 65
             else "✅ Cupidite (" + str(fgv) + ") — marche survalorise, biais SHORT"             if fgv > 50
             else "⚠️  Peur (" + str(fgv) + ") — marche deja baissier, surveiller")

    chg = btc["change"] if btc else 0
    if is_long:
        mom_txt = ("✅ Momentum fort +" + "{:.2f}".format(chg) + "% — tendance haussiere" if chg > 1.5
              else "✅ Momentum positif +" + "{:.2f}".format(chg) + "% — biais haussier"  if chg > 0
              else "⚠️  Retracement " + "{:.2f}".format(chg) + "% — entree sur correction")
    else:
        mom_txt = ("✅ Momentum baissier " + "{:.2f}".format(chg) + "% — SHORT confirme"     if chg < -1.5
              else "✅ Momentum negatif " + "{:.2f}".format(chg) + "% — biais baissier"       if chg < 0
              else "⚠️  Rejet sur hausse +" + "{:.2f}".format(chg) + "% — surveiller")

    force = signal["force"]
    ob_txt = ("✅ OB ultra-fort (" + str(force) + "x) — institution fortement impliquee" if force >= 2.5
         else "✅ OB fort (" + str(force) + "x) — demande/offre institutionnelle"        if force >= 1.8
         else "🟡 OB modere (" + str(force) + "x) — zone valide, surveiller le retour")

    lignes = [
        "📊 Fear & Greed : " + str(fgv) + "/100",
        "   " + fg_txt,
        "📈 Momentum BTC 24h : " + "{:+.2f}".format(chg) + "%",
        "   " + mom_txt,
        "🧱 Force Order Block : " + str(force) + "x la moyenne",
        "   " + ob_txt,
        "📍 Zone OB : " + str(signal["zone_basse"]) + " — " + str(signal["zone_haute"]),
        "   ✅ Prix dans la zone — reaction attendue",
        "",
    ]
    if is_long:
        lignes += ["🔎 CONCLUSION : OB haussier + pression acheteuse confirmee",
                   "   → Setup VALIDE ✅"]
    else:
        lignes += ["🔎 CONCLUSION : OB baissier + pression vendeuse confirmee",
                   "   → Setup VALIDE ✅"]
    return "\n".join(lignes)


STICKERS_LONG  = ["🚀", "🟢", "💚", "📈", "⚡", "🔥", "💎", "🏆"]
STICKERS_SHORT = ["🔴", "📉", "⚠️", "🔽", "💥", "🛑", "🎯"]
STICKERS_MOTIV = ["💪", "🧠", "🎯", "⭐", "🏅", "🔑", "💡", "🙌"]

# ═══════════════════════════════════════════════════════
# SIGNAL — FORMAT UNIFIÉ CRYPTO + FOREX
# ═══════════════════════════════════════════════════════

async def envoyer_signal_ob(bot, signal, fg, btc):
    rr        = round(abs(signal["tp1"] - signal["entree"]) /
                      max(abs(signal["entree"] - signal["sl"]), 0.00001), 2)
    is_long   = signal["direction"] == "LONG"
    is_forex  = signal["marche"] == "FOREX"
    dir_txt   = "🟢 LONG — ACHAT" if is_long else "🔴 SHORT — VENTE"
    marche_tag = "🌍 FOREX" if is_forex else "₿ CRYPTO FUTURES"
    stickers  = " ".join(random.sample(STICKERS_LONG if is_long else STICKERS_SHORT, 3))
    s_motiv   = " ".join(random.sample(STICKERS_MOTIV, 3))
    citation, auteur = random.choice(MOTIVATIONS)
    bloc_fond = construire_bloc_fondamental(signal, fg, btc)
    now_str   = datetime.now().strftime("%d/%m/%Y a %H:%M")

    dec = 5 if is_forex else 1
    def pct(tp):
        return "{:+.4f}".format(tp - signal["entree"]) if is_forex else "{:+.2f}".format(100 * (tp - signal["entree"]) / max(signal["entree"], 1)) + "%"
    def pct_sl():
        return "{:+.4f}".format(signal["sl"] - signal["entree"]) if is_forex else "{:+.2f}".format(100 * (signal["sl"] - signal["entree"]) / max(signal["entree"], 1)) + "%"

    levier_txt = ("x20 a x50 (recommande)" if is_forex else "x5 a x10 (recommande)")

    msg = (
        stickers + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡  SIGNAL ORDER BLOCK — ALPHABOT PRO\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "📌  IDENTIFICATION\n"
        "┌──────────────────────────────\n"
        "│ Marche    :  " + marche_tag + "\n"
        "│ Paire     :  " + signal["paire"] + "\n"
        "│ Setup     :  " + signal["type"] + "\n"
        "│ Timeframe :  1H\n"
        "│ Signal    :  " + now_str + "\n"
        "└──────────────────────────────\n\n"

        "🎯  DIRECTION\n"
        "┌──────────────────────────────\n"
        "│  " + dir_txt + "\n"
        "└──────────────────────────────\n\n"

        "🏹  POINT D'ENTREE\n"
        "┌──────────────────────────────\n"
        "│  💲 Entree  :  " + str(signal["entree"]) + "\n"
        "│  📍 Zone OB :  " + str(signal["zone_basse"]) + " — " + str(signal["zone_haute"]) + "\n"
        "│  ⏳ Levier  :  " + levier_txt + "\n"
        "│  💼 Risque  :  1-2% du capital max\n"
        "└──────────────────────────────\n\n"

        "💰  STOP LOSS & TAKE PROFIT\n"
        "┌──────────────────────────────\n"
        "│  🛑 Stop Loss :  " + str(signal["sl"]) + "  (" + pct_sl() + ")\n"
        "│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "│  🎯 TP 1  :  " + str(signal["tp1"]) + "  (" + pct(signal["tp1"]) + ")  → Partiel 40%\n"
        "│  🎯 TP 2  :  " + str(signal["tp2"]) + "  (" + pct(signal["tp2"]) + ")  → Partiel 35%\n"
        "│  🏆 TP 3  :  " + str(signal["tp3"]) + "  (" + pct(signal["tp3"]) + ")  → Solde  25%\n"
        "│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "│  ⚖️  Ratio R/R  :  " + str(rr) + " : 1\n"
        "└──────────────────────────────\n\n"

        "🔬  VALIDATION FONDAMENTALE\n"
        "┌──────────────────────────────\n"
        + "\n".join("│  " + l for l in bloc_fond.split("\n")) + "\n"
        "└──────────────────────────────\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + s_motiv + "  MINDSET DU TRADER\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\"" + citation + "\"\n"
        "  — " + auteur + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔔 Active les notifs — ne rate aucun signal !\n"
        "📡 Groupe gratuit : " + LIEN_GROUPE + "\n"
        "💎 VIP exclusif   : " + CANAL_VIP + "\n\n"
        "💳 Depot VIP (TRC-20) :\n"
        "   " + ADRESSE_TRC20 + "\n"
        "📩 Hash TX → @leaderOdg\n\n"
        "⚠️  Pas un conseil financier.\n"
        "@leaderOdg — AlphaBot Pro"
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg)

    # ── Exécution trade Binance automatique ──
    executer_trade_binance(signal)

    # ── Alerte email ──
    envoyer_email(
        "⚡ Signal " + signal["direction"] + " — " + signal["paire"],
        "Signal AlphaBot Pro\n\n"
        "Paire     : " + signal["paire"] + "\n"
        "Direction : " + signal["direction"] + "\n"
        "Entrée    : " + str(signal["entree"]) + "\n"
        "SL        : " + str(signal["sl"]) + "\n"
        "TP1       : " + str(signal["tp1"]) + "\n"
        "TP2       : " + str(signal["tp2"]) + "\n"
        "TP3       : " + str(signal["tp3"]) + "\n\n"
        "Pas un conseil financier."
    )


# ═══════════════════════════════════════════════════════
# SCANNER MULTI-PAIRES (CRYPTO + FOREX)
# ═══════════════════════════════════════════════════════

async def scanner_order_blocks(bot):
    print("[SCANNER] Analyse multi-paires en cours...")
    btc = get_btc()
    fg  = get_fear_greed()

    # ── Scan Crypto ──
    for paire in PAIRES_CRYPTO:
        try:
            candles = get_candles(paire["symbol"], interval="1h", limit=50)
            signal  = detecter_order_block(candles, paire["nom"], "CRYPTO")
            if signal:
                print("[SCANNER CRYPTO] Signal : " + signal["paire"] + " — " + signal["type"])
                if fondamental_confirme(signal, fg, btc):
                    print("[SCANNER] Confirme — Envoi signal " + signal["paire"])
                    await envoyer_signal_ob(bot, signal, fg, btc)
                    await asyncio.sleep(3)  # pause entre signaux
                else:
                    print("[SCANNER] " + signal["paire"] + " non confirme — ignore")
        except Exception as e:
            print("[SCANNER CRYPTO ERROR] " + paire["nom"] + " : " + str(e))

    # ── Scan Forex (simulation candles via prix spot) ──
    # Note : pour le vrai Forex H1, utilise Twelve Data ou Alpha Vantage avec cle API
    # Ici on scanne les paires crypto-forex disponibles sur Binance
    FOREX_BINANCE = [
        {"symbol": "EURUSDT",  "nom": "EUR/USD",      "emoji": "🇪🇺"},
        {"symbol": "GBPUSDT",  "nom": "GBP/USD",      "emoji": "🇬🇧"},
        {"symbol": "XAUUSDT",  "nom": "XAU/USD (Or)", "emoji": "🥇"},
    ]
    for paire in FOREX_BINANCE:
        try:
            candles = get_candles(paire["symbol"], interval="1h", limit=50)
            signal  = detecter_order_block(candles, paire["nom"], "FOREX")
            if signal:
                print("[SCANNER FOREX] Signal : " + signal["paire"] + " — " + signal["type"])
                if fondamental_confirme(signal, fg, btc):
                    print("[SCANNER] Confirme — Envoi signal " + signal["paire"])
                    await envoyer_signal_ob(bot, signal, fg, btc)
                    await asyncio.sleep(3)
                else:
                    print("[SCANNER] " + signal["paire"] + " non confirme — ignore")
        except Exception as e:
            print("[SCANNER FOREX ERROR] " + paire["nom"] + " : " + str(e))

    print("[SCANNER] Scan multi-paires termine.")


# ═══════════════════════════════════════════════════════
# MESSAGES CANAL
# ═══════════════════════════════════════════════════════

async def envoyer_bonjour(bot):
    btc = get_btc()
    fg  = get_fear_greed()
    now = datetime.now()
    citation, auteur = random.choice(MOTIVATIONS)
    msg = (
        "🌅 Bonjour la famille AlphaBot Pro !\n"
        + sep() + "\n"
        "📅 " + date_fr() + " | " + now.strftime("%H:%M") + "\n"
        + sep() + "\n\n"
        + ligne_btc(btc) + "\n"
        + fg_emoji(fg["value"]) + " Fear & Greed : " + str(fg["value"]) + " — " + fg_label(fg["value"]) + "\n\n"
        + sep() + "\n"
        "💬 Citation du jour :\n"
        "\"" + citation + "\"\n"
        "  — " + auteur + "\n\n"
        + sep() + "\n"
        "📊 Marchés surveilles aujourd'hui :\n"
        "₿  BTC, ETH, SOL, BNB, XRP\n"
        "🌍 EUR/USD, GBP/USD, XAU/USD (Or)\n\n"
        "🔔 Scanner actif — signaux en continu.\n\n"
        "📡 Gratuit : " + LIEN_GROUPE + "\n"
        "💎 VIP (5$/mois ou depot 50$) : " + CANAL_VIP + "\n\n"
        "Pas un conseil financier."
    )
    images_matin = [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800",
        "https://images.unsplash.com/photo-1642790106117-e829e14a795f?w=800",
        "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=800",
    ]
    img_url = random.choice(images_matin)
    try:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=img_url, caption=msg)
    except Exception:
        await bot.send_message(chat_id=CHANNEL_ID, text=msg)
    citation, auteur = random.choice(MOTIVATIONS)
    e = random.choice(["💪", "🔥", "⭐", "🚀", "🎯", "💎", "🏆", "🧠"])
    msg = (
        e + " AlphaBot Pro — Mindset du Trader\n"
        + sep() + "\n\n"
        "\"" + citation + "\"\n"
        "  — " + auteur + "\n\n"
        + sep() + "\n"
        "🔔 Scanner actif — Crypto & Forex — 24h/24\n\n"
        "💎 Canal VIP : " + CANAL_VIP + "\n"
        "📡 Gratuit : " + LIEN_GROUPE + "\n\n"
        "🤝 Parraine un ami → 7 jours VIP OFFERTS !\n"
        "Tape /affiliation pour ton lien unique."
    )
    # Liste d'images crypto/forex motivationnelles (URLs publiques)
    images_crypto = [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800",
        "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=800",
        "https://images.unsplash.com/photo-1605792657660-596af9009e82?w=800",
        "https://images.unsplash.com/photo-1518186285589-2f7649de83e0?w=800",
    ]
    img_url = random.choice(images_crypto)
    try:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=img_url, caption=msg)
    except Exception:
        await bot.send_message(chat_id=CHANNEL_ID, text=msg)


async def envoyer_analyse(bot):
    btc = get_btc()
    if not btc:
        return
    fg = get_fear_greed()
    tendance = ("HAUSSIERE 📈" if btc["change"] > 0.5
           else "BAISSIERE 📉" if btc["change"] < -0.5
           else "NEUTRE ➡️")
    msg = (
        "📊 Analyse Marche — AlphaBot Pro\n"
        + sep() + "\n"
        + ligne_btc(btc) + "\n"
        "Haut 24h : " + "{:,.0f}".format(btc["high"]) + "$\n"
        "Bas  24h : " + "{:,.0f}".format(btc["low"]) + "$\n"
        "Tendance : " + tendance + "\n"
        + fg_emoji(fg["value"]) + " F&G : " + str(fg["value"]) + " — " + fg_label(fg["value"]) + "\n\n"
        + sep() + "\n"
        "🌍 Rappel : scanner actif sur\n"
        "₿  BTC / ETH / SOL / BNB / XRP\n"
        "💱 EUR/USD / GBP/USD / XAU/USD\n\n"
        + sep() + "\n"
        "⚠️  Gestion du risque :\n"
        "→ Max 1-2% du capital par trade\n"
        "→ SL obligatoire avant d'entrer\n"
        "→ RR minimum : 1:2\n\n"
        "💎 VIP : " + CANAL_VIP + "\n"
        "Pas un conseil financier."
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg)


async def envoyer_bonsoir(bot):
    btc  = get_btc()
    fg   = get_fear_greed()
    citation, auteur = random.choice(MOTIVATIONS)
    perf    = "positive 📈" if btc and btc["change"] >= 0 else "negative 📉"
    btc_txt = ("{:,.0f}".format(btc["price"]) + "$ (" + "{:+.2f}".format(btc["change"]) + "%)") if btc else "N/A"
    msg = (
        "🌙 Bonsoir la famille AlphaBot Pro !\n"
        + sep() + "\n"
        "Bilan du " + datetime.now().strftime("%d/%m/%Y") + "\n\n"
        "BTC : " + btc_txt + " — Journee " + perf + "\n"
        + fg_emoji(fg["value"]) + " F&G : " + str(fg["value"]) + " — " + fg_label(fg["value"]) + "\n\n"
        + sep() + "\n"
        "\"" + citation + "\"\n"
        "  — " + auteur + "\n\n"
        + sep() + "\n"
        "🌙 Bonne nuit — le scanner continue.\n"
        "Les signaux Crypto & Forex tournent meme pendant que tu dors.\n\n"
        "💎 VIP (5$/mois ou 50$ depot) : " + CANAL_VIP + "\n"
        "📡 Gratuit : " + LIEN_GROUPE + "\n\n"
        "Pas un conseil financier."
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg)


async def envoyer_rappel_communaute(bot):
    db = load_db()
    nb_affilies  = len(db["affilies"])
    total_ventes = len(db["ventes"])
    total_parts  = sum(db["partages"].values()) if db["partages"] else 0
    msg = (
        "🤝 AFFILIATION — Gagne avec AlphaBot Pro !\n"
        + sep() + "\n\n"
        "📣 3 façons de gagner :\n\n"
        "1️⃣  AFFILIATION : Partage ton lien\n"
        "   → " + str(COMMISSION_PCT) + "% de commission sur chaque abonnement VIP\n"
        "   → " + str(round(PRIX_MENSUEL * COMMISSION_PCT / 100, 2)) + "$ par vente a 5$/mois\n\n"
        "2️⃣  PARRAINAGE : 1 ami rejoint via ton lien\n"
        "   → 7 jours VIP GRATUITS pour toi !\n\n"
        "3️⃣  PALIERS : Plus tu partages, plus tu gagnes\n"
        "   1  partage → 1 jour VIP offert\n"
        "   5  partages → 3 jours + sticker\n"
        "   10 partages → 7 jours + commission 22%\n"
        "   20 partages → 1 mois VIP + 25%\n"
        "   50 partages → 3 mois Elite + 30% a vie\n\n"
        + sep() + "\n"
        "COMMUNAUTE :\n"
        "👥 Affilies : " + str(nb_affilies) + "\n"
        "💰 Ventes   : " + str(total_ventes) + "\n"
        "📢 Partages : " + str(total_parts) + "\n\n"
        + sep() + "\n"
        "Tape /affiliation pour ton lien unique 🔗\n"
        "📡 Groupe : " + LIEN_GROUPE + "\n"
        "💎 VIP : " + CANAL_VIP + "\n"
        "Contact : " + ADMIN_USERNAME
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg)


async def envoyer_bienvenue_lancement(bot):
    btc = get_btc()
    fg  = get_fear_greed()
    now = datetime.now()
    citation, auteur = random.choice(MOTIVATIONS)

    # ── MESSAGE 1 : Présentation naturelle ──
    msg1 = (
        "⚡ BIENVENUE SUR ALPHABOT PRO ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Je suis Leader — trader professionnel actif sur les marchés mondiaux.\n\n"
        "Après des années de trading sur les Crypto Futures et le Forex, "
        "j'ai créé ce groupe pour partager mes signaux avec une communauté sérieuse.\n\n"
        "Pas pour vendre du rêve. Pour trader ensemble — avec discipline et méthode.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CE QU'ON FAIT ICI :\n\n"
        "✅ Signaux gratuits — Crypto & Forex\n"
        "✅ Éducation — Order Block / Smart Money\n"
        "✅ Gestion du risque — capital protégé\n"
        "✅ Résultats constants, pas des coups\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CE QU'ON TRADE :\n\n"
        "₿  Crypto Futures : BTC, ETH, SOL, BNB, XRP\n"
        "🌍 Forex : EUR/USD, GBP/USD, XAU/USD (Or)\n"
        "📡 Scanner automatique — 24h/24\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "— @leaderOdg"
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg1)
    await asyncio.sleep(3)

    # ── MESSAGE 2 : Motivation naturelle ──
    citation2, auteur2 = random.choice(MOTIVATIONS)
    msg2 = (
        "💪 MINDSET DU TRADER\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "La majorité des gens regardent les marchés monter sans jamais passer à l'action.\n\n"
        "Ils attendent le bon moment... qui ne vient jamais.\n\n"
        "Ceux qui réussissent ont un système, respectent le risque, et tiennent leur plan.\n\n"
        "Tu es ici. C'est déjà un pas dans la bonne direction. 🚀\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\"" + citation2 + "\"\n"
        "  — " + auteur2 + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "LES 5 RÈGLES D'OR :\n\n"
        "1️⃣  Ne risque jamais plus de 2% par trade\n"
        "2️⃣  Pose toujours ton Stop Loss avant d'entrer\n"
        "3️⃣  Suis le signal — pas tes émotions\n"
        "4️⃣  TP1 atteint ? Sécurise une partie\n"
        "5️⃣  Patience. La régularité bat tout.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "AlphaBot Pro — @leaderOdg"
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg2)
    await asyncio.sleep(3)

    # ── MESSAGE 3 : Bot en ligne + depot ──
    msg3 = (
        "📡 BOT EN LIGNE — " + now.strftime("%d/%m/%Y %H:%M") + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + ligne_btc(btc) + "\n"
        + fg_emoji(fg["value"]) + " Fear & Greed : " + str(fg["value"]) + " — " + fg_label(fg["value"]) + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔔 COMMENT FONCTIONNE LE BOT :\n"
        "┌──────────────────────────────\n"
        "│  📡 Scan Order Block toutes les heures\n"
        "│  ₿  Crypto : BTC, ETH, SOL, BNB, XRP\n"
        "│  🌍 Forex  : EUR/USD, GBP/USD, XAU/USD\n"
        "│  ✅ Signal envoye seulement si confirme\n"
        "│  💬 Analyses : 07h, 12h, 18h, 21h\n"
        "│  💪 Motivation : 10h et 15h\n"
        "└──────────────────────────────\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 ACCES VIP — " + CANAL_VIP + "\n"
        "┌──────────────────────────────\n"
        "│  → 5 USDT/mois\n"
        "│  → 50 USDT depot unique (recommande)\n"
        "│  → 1 ami parraine = 7 jours VIP OFFERTS\n"
        "└──────────────────────────────\n\n"
        "💳 DEPOT USDT TRC-20 :\n"
        + ADRESSE_TRC20 + "\n\n"
        "📩 Apres depot → envoie hash TX a @leaderOdg\n"
        "⏱️  Acces active sous 24h max\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 Groupe : " + LIEN_GROUPE + "\n"
        "💎 VIP : " + CANAL_VIP + "\n"
        "🤝 /affiliation  |  💳 /payer  |  ℹ️ /vip\n\n"
        "⚠️  Pas un conseil financier.\n"
        "AlphaBot Pro — @leaderOdg"
    )
    await bot.send_message(chat_id=CHANNEL_ID, text=msg3)

    # ── MESSAGE 4 : Image de motivation crypto ──
    await asyncio.sleep(2)
    images_motivation = [
        "https://i.imgur.com/4XqpP8Q.jpg",   # graphique crypto bull
    ]
    legende_img = (
        "📊 Le marché ne dort jamais. Ton scanner non plus.\n\n"
        "💎 VIP : " + CANAL_VIP + "\n"
        "📡 Gratuit : " + LIEN_GROUPE + "\n"
        "🤝 Parraine 1 ami → 7 jours VIP offerts !"
    )
    try:
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo="https://i.imgur.com/4XqpP8Q.jpg",
            caption=legende_img
        )
    except Exception:
        # Si l'image ne charge pas, on envoie juste le texte
        await bot.send_message(chat_id=CHANNEL_ID, text=legende_img)

    print("[BOT] Messages de bienvenue envoyes (4 blocs)")


# ═══════════════════════════════════════════════════════
# HANDLER VOCAL
# ═══════════════════════════════════════════════════════

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    s    = random.choice(["⚡", "🔍", "📡", "🧠", "🔬"])
    attente = await update.message.reply_text(
        s + " Vocal recu " + user.first_name + " !\n\n"
        "🔍 Scan Order Block en cours...\n"
        "📡 BTC + ETH + EUR/USD + XAU/USD...\n"
        "⏳ Analyse en temps reel — quelques secondes..."
    )
    try:
        btc = get_btc()
        fg  = get_fear_greed()
        signal_trouve = None

        # Chercher un signal valide sur toutes les paires
        paires_a_scanner = [
            ("BTCUSDT",  "BTC/USDT",      "CRYPTO"),
            ("ETHUSDT",  "ETH/USDT",      "CRYPTO"),
            ("EURUSDT",  "EUR/USD",        "FOREX"),
            ("XAUUSDT",  "XAU/USD (Or)",  "FOREX"),
        ]
        for symbol, nom, marche in paires_a_scanner:
            candles = get_candles(symbol, interval="1h", limit=50)
            signal  = detecter_order_block(candles, nom, marche)
            if signal and fondamental_confirme(signal, fg, btc):
                signal_trouve = signal
                break

        await attente.delete()

        if signal_trouve:
            await update.message.reply_text(
                "✅ Signal detecte : " + signal_trouve["paire"] + " !\nVoici le setup :\n"
            )
            await envoyer_signal_ob(ctx.bot, signal_trouve, fg, btc)
        else:
            await update.message.reply_text(
                "📡 Scan termine — Aucun OB confirme pour l'instant\n\n"
                + ligne_btc(btc) + "\n"
                + fg_emoji(fg["value"]) + " F&G : " + str(fg["value"]) + "\n\n"
                "🔍 Crypto & Forex scannés — marche en consolidation.\n"
                "✅ Le scanner tourne 24h/24 — signal automatique a venir.\n\n"
                "💡 \"" + random.choice(MOTIVATIONS)[0] + "\"\n\n"
                "💎 VIP : " + CANAL_VIP
            )
    except Exception as e:
        await attente.delete()
        await update.message.reply_text("⚠️ Erreur scan : " + str(e) + "\nScanner reste actif. ✅")


# ═══════════════════════════════════════════════════════
# COMMANDES UTILISATEUR
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args

    # Parrainage
    if args:
        db = load_db()
        for uid, aff in db["affilies"].items():
            if aff["code"] == args[0] and str(user.id) != uid:
                jours_vip = enregistrer_parrainage(uid)
                try:
                    await ctx.bot.send_message(
                        chat_id=int(uid),
                        text=(
                            "🎉 Nouveau filleul — " + user.first_name + " vient de rejoindre !\n\n"
                            "🎁 RECOMPENSE : " + str(JOURS_VIP_PAR_PARRAINAGE) + " jours VIP OFFERTS !\n"
                            "Total VIP cumule : " + str(jours_vip) + " jours\n\n"
                            "📩 Contacte " + ADMIN_USERNAME + " pour activer ton VIP.\n"
                            "Continue a partager pour en gagner encore !"
                        )
                    )
                except:
                    pass
                break

    keyboard = [
        [InlineKeyboardButton("📡 Groupe Gratuit (Signaux)",    url=LIEN_GROUPE)],
        [InlineKeyboardButton("💎 Rejoindre le Canal VIP",      url=CANAL_VIP)],
        [InlineKeyboardButton("💳 Payer / Adresses de dépôt",   callback_data="payer")],
        [InlineKeyboardButton("🤝 Devenir Affilié",             callback_data="affiliation")],
        [InlineKeyboardButton("📊 Mon Tableau de Bord",         callback_data="dashboard")],
        [InlineKeyboardButton("🎁 Partager & Récompenses",      callback_data="partage")],
        [InlineKeyboardButton("💰 Mes Gains",                   callback_data="gains")],
        [InlineKeyboardButton("❓ Comment ça marche ?",         callback_data="aide")],
        [InlineKeyboardButton("🔑 Créer compte Binance",        url=LIEN_BINANCE)],
    ]
    await update.message.reply_text(
        "👋 Bonjour " + user.first_name + " !\n\n"
        "⚡ Bienvenue sur AlphaBot Pro\n"
        + sep() + "\n"
        "📊 Signaux Crypto & Forex — 100% automatiques\n"
        "🌍 BTC, ETH, SOL + EUR/USD, GBP/USD, Or\n\n"
        "💎 ACCES VIP :\n"
        "→ 5$ USDT/mois\n"
        "→ OU depot unique 50$ USDT\n\n"
        "🎁 Parraine 1 ami = 7 jours VIP GRATUITS !\n"
        "🤝 Commission 20% sur chaque vente\n\n"
        "📡 Pas de compte ? Pas de probleme !\n"
        "→ Cree un compte Binance & depose 50$ pour commencer\n"
        + sep() + "\n"
        "Choisis une option 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💎 Rejoindre le Canal VIP",    url=CANAL_VIP)],
        [InlineKeyboardButton("💳 Voir adresses de dépôt",   callback_data="payer")],
        [InlineKeyboardButton("📩 Contacter l'admin",         url="https://t.me/leaderOdg")],
    ]
    await update.message.reply_text(
        "💎 CANAL VIP — AlphaBot Pro\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "2 options pour rejoindre le VIP :\n\n"
        "🔵 OPTION 1 — Abonnement mensuel\n"
        "   → " + str(PRIX_MENSUEL) + " USDT / mois\n\n"
        "🟡 OPTION 2 — Dépôt unique (recommandé)\n"
        "   → Dépose " + str(PRIX_DEPOT_VIP) + " USDT une seule fois\n"
        "   → Accès VIP activé immédiatement\n"
        "   → Pas de compte requis au départ\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Ce que tu reçois en VIP :\n"
        "→ Signaux Crypto Futures exclusifs\n"
        "→ Signaux Forex (EUR/USD, GBP/USD, Or)\n"
        "→ Alertes prioritaires avant les moves\n"
        "→ Setups R/R optimisé (objectif +10%)\n"
        "→ Formations avancées en live\n"
        "→ Q&A privé avec l'analyste\n"
        "→ Scanner 24h/24 — Crypto + Forex\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💳 Paiement : USDT (TRC-20 / Telegram Wallet)\n"
        "📩 Contact : " + ADMIN_USERNAME + "\n\n"
        "🎁 Parraine 1 ami = 7 jours VIP GRATUITS sans payer !\n"
        "Tape /affiliation pour ton lien.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_payer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💎 Rejoindre le VIP",     url=CANAL_VIP)],
        [InlineKeyboardButton("📩 Confirmer paiement",   url="https://t.me/leaderOdg")],
    ]
    await update.message.reply_text(
        "💳 DÉPÔT USDT — AlphaBot Pro\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choisis ton montant :\n"
        "→ " + str(PRIX_MENSUEL) + " USDT = 1 mois VIP\n"
        "→ " + str(PRIX_DEPOT_VIP) + " USDT = Accès VIP (dépôt unique)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 TELEGRAM WALLET (le plus simple)\n"
        "Ouvre @wallet sur Telegram → Envoyer USDT\n"
        "Adresse :\n"
        + ADRESSE_TELEGRAM_TON + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔵 BINANCE — Réseau TRC-20 uniquement\n"
        + ADRESSE_TRC20 + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️  APRES ENVOI :\n"
        "1. Envoie le hash/capture a " + ADMIN_USERNAME + "\n"
        "2. Accès VIP activé sous 24h max\n"
        "3. Tu reçois le lien du canal " + CANAL_VIP + "\n\n"
        "📩 Contact : " + ADMIN_USERNAME,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_affiliation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    aff  = get_affilie(user.id) or creer_affilie(user.id, user.username, user.first_name)
    lien = "https://t.me/" + BOT_USERNAME + "?start=" + aff["code"]
    palier    = get_palier(aff.get("partages", 0))
    vip_jours = aff.get("vip_jours", 0)
    commission_5  = round(PRIX_MENSUEL  * COMMISSION_PCT / 100, 2)
    commission_50 = round(PRIX_DEPOT_VIP * COMMISSION_PCT / 100, 2)

    msg = (
        "🤝 Ton espace affilié — AlphaBot Pro\n"
        + sep() + "\n"
        "Code : " + aff["code"] + "\n"
        "Lien : " + lien + "\n\n"
        + sep() + "\n"
        "📊 Statistiques :\n"
        "Inscriptions : " + str(aff["inscriptions"]) + "\n"
        "Ventes       : " + str(aff["ventes"]) + "\n"
        "Gains        : " + str(round(aff["gains"], 2)) + " USDT\n"
        "Partages     : " + str(aff.get("partages", 0)) + "\n"
        "VIP gagné    : " + str(vip_jours) + " jours\n\n"
    )
    if vip_jours > 0:
        msg += "🎁 " + str(vip_jours) + " jours VIP cumules — contacte " + ADMIN_USERNAME + " pour activer.\n\n"
    if palier:
        msg += "Palier : " + palier[0] + "\n" + palier[1] + "\n\n"

    msg += (
        + sep() + "\n"
        "💰 Tes commissions :\n"
        "→ Vente 5$/mois   = +" + str(commission_5) + " USDT/vente\n"
        "→ Depot 50$ VIP   = +" + str(commission_50) + " USDT/vente\n\n"
        "🎁 Chaque filleul qui rejoint = 7 jours VIP GRATUITS !\n\n"
        "Partage sur TikTok / Insta / WhatsApp / Facebook\n"
        "Paiement USDT — Contact : " + ADMIN_USERNAME
    )
    keyboard = [
        [InlineKeyboardButton("📋 Copier mon lien",      callback_data="copier_" + aff["code"])],
        [InlineKeyboardButton("💎 Canal VIP",            url=CANAL_VIP)],
        [InlineKeyboardButton("📡 Groupe gratuit",       url=LIEN_GROUPE)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    aff  = get_affilie(user.id)
    if not aff:
        await update.message.reply_text("Tape /affiliation d'abord.")
        return
    db     = load_db()
    ventes = [v for v in db["ventes"] if v["code"] == aff["code"]]
    palier = get_palier(aff.get("partages", 0))
    msg    = (
        "📊 Tableau de Bord — " + aff["nom"] + "\n"
        + sep() + "\n"
        "Code : " + aff["code"] + "\n"
        "Depuis : " + aff["rejoint_le"] + "\n\n"
        "Inscriptions : " + str(aff["inscriptions"]) + "\n"
        "Ventes       : " + str(aff["ventes"]) + "\n"
        "Gains        : " + str(round(aff["gains"], 2)) + " USDT\n"
        "Partages     : " + str(aff.get("partages", 0)) + "\n"
        "VIP gagné    : " + str(aff.get("vip_jours", 0)) + " jours\n"
    )
    if palier:
        msg += "\nPalier : " + palier[0] + "\n" + palier[1] + "\n"
    msg += "\nDernières ventes :\n"
    for v in ventes[-5:] if ventes else []:
        msg += v["date"] + " — +" + str(round(v["commission"], 2)) + " USDT\n"
    if not ventes:
        msg += "Aucune vente encore. Partage ton lien !\n"
    pot = aff["inscriptions"] * PRIX_MENSUEL * COMMISSION_PCT / 100
    msg += "\nPotentiel mensuel : " + str(round(pot, 2)) + " USDT/mois"
    await update.message.reply_text(msg)


async def cmd_partage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    total  = enregistrer_partage(user.id)
    palier = get_palier(total)
    msg    = "🎉 Partage enregistré !\nTotal : " + str(total) + " partage(s)\n\n"
    if palier:
        msg += "Palier : " + palier[0] + "\n" + palier[1] + "\n\n"
    prochain = next((s for s in [1, 5, 10, 20, 50] if total < s), None)
    if prochain:
        msg += "Encore " + str(prochain - total) + " partage(s) pour le prochain palier !\n\n"
    msg += "Lien à partager :\n" + LIEN_GROUPE
    await update.message.reply_text(msg)


async def cmd_binance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔑 Crée ton compte Binance ici :\n\n"
        + LIEN_BINANCE + "\n\n"
        "💡 Dépose 50$ USDT pour accéder au VIP !\n"
        "📩 Après dépôt, contacte : " + ADMIN_USERNAME
    )


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Accès refusé.")
        return
    db          = load_db()
    total_gains = sum(v["commission"] for v in db["ventes"])
    total_parts = sum(db["partages"].values()) if db["partages"] else 0
    msg = (
        "🔐 ADMIN — AlphaBot Pro\n"
        + sep() + "\n"
        "Affiliés    : " + str(len(db["affilies"])) + "\n"
        "Ventes      : " + str(len(db["ventes"])) + "\n"
        "Commissions : " + str(round(total_gains, 2)) + " USDT\n"
        "Partages    : " + str(total_parts) + "\n\n"
        "Top 5 affiliés :\n"
    )
    top = sorted(db["affilies"].values(), key=lambda x: x["gains"], reverse=True)[:5]
    for i, aff in enumerate(top, 1):
        msg += (str(i) + ". " + aff["nom"] + " — " + str(aff["ventes"])
                + " ventes — " + str(round(aff["gains"], 2)) + " USDT"
                + " | VIP: " + str(aff.get("vip_jours", 0)) + "j\n")
    msg += "\n/valider_vente CODE [montant]\n/valider_paiement USER_ID [jours]"
    await update.message.reply_text(msg)


async def cmd_valider_vente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /valider_vente CODE [montant]")
        return
    code       = args[0].upper()
    montant    = float(args[1]) if len(args) > 1 else PRIX_MENSUEL
    commission, uid = enregistrer_vente(code, montant)
    if commission > 0:
        await update.message.reply_text(
            "✅ Vente validée !\nCode : " + code + "\nMontant : " + str(montant) + " USDT\nCommission : " + str(round(commission, 2)) + " USDT"
        )
        if uid:
            db  = load_db()
            aff = db["affilies"].get(str(uid))
            if aff:
                try:
                    await ctx.bot.send_message(
                        chat_id=uid,
                        text="💰 Nouvelle commission !\n+" + str(round(commission, 2)) + " USDT\nTotal : " + str(round(aff["gains"], 2)) + " USDT\n\nContinue à partager !"
                    )
                except:
                    pass
    else:
        await update.message.reply_text("Code " + code + " introuvable.")


async def cmd_valider_paiement(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /valider_paiement USER_ID [jours]\nEx: /valider_paiement 123456 30")
        return
    uid   = args[0]
    jours = int(args[1]) if len(args) > 1 else 30
    try:
        await ctx.bot.send_message(
            chat_id=int(uid),
            text=(
                "✅ Paiement VIP confirmé !\n\n"
                "💎 Accès activé pour " + str(jours) + " jours.\n\n"
                "Rejoins le canal maintenant :\n" + CANAL_VIP + "\n\n"
                "Merci pour ta confiance ! 🙏\n"
                "Des questions ? " + ADMIN_USERNAME
            )
        )
        await update.message.reply_text("✅ VIP activé pour user " + uid + " (" + str(jours) + " jours). Notification envoyée.")
    except Exception as e:
        await update.message.reply_text("Erreur : " + str(e))


# ═══════════════════════════════════════════════════════
# CALLBACKS BOUTONS
# ═══════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user

    if query.data == "affiliation":
        aff  = get_affilie(user.id) or creer_affilie(user.id, user.username, user.first_name)
        lien = "https://t.me/" + BOT_USERNAME + "?start=" + aff["code"]
        await query.message.reply_text(
            "🤝 Ton espace affilié\n\n"
            "Code : " + aff["code"] + "\n"
            "Lien : " + lien + "\n\n"
            "Gains : " + str(round(aff["gains"], 2)) + " USDT\n"
            "Ventes : " + str(aff["ventes"]) + "\n"
            "VIP gagné : " + str(aff.get("vip_jours", 0)) + " jours\n\n"
            "🎁 1 filleul = 7 jours VIP OFFERTS !\n\n"
            "Commission :\n"
            "→ 5$/mois = " + str(round(PRIX_MENSUEL * COMMISSION_PCT / 100, 2)) + " USDT\n"
            "→ 50$ dépôt = " + str(round(PRIX_DEPOT_VIP * COMMISSION_PCT / 100, 2)) + " USDT\n\n"
            "Contact : " + ADMIN_USERNAME
        )

    elif query.data == "payer":
        keyboard = [
            [InlineKeyboardButton("💎 Rejoindre le VIP",    url=CANAL_VIP)],
            [InlineKeyboardButton("📩 Confirmer paiement",  url="https://t.me/leaderOdg")],
        ]
        await query.message.reply_text(
            "💳 DÉPÔT USDT\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "→ " + str(PRIX_MENSUEL) + " USDT = 1 mois VIP\n"
            "→ " + str(PRIX_DEPOT_VIP) + " USDT = Accès VIP (dépôt unique)\n\n"
            "📱 TELEGRAM WALLET :\n" + ADRESSE_TELEGRAM_TON + "\n\n"
            "🔵 TRC-20 uniquement (Binance) :\n" + ADRESSE_TRC20 + "\n\n"
            "Après envoi → hash TX à " + ADMIN_USERNAME,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "dashboard":
        aff = get_affilie(user.id)
        if aff:
            await query.message.reply_text(
                "📊 " + aff["nom"] + "\n\n"
                "Gains : " + str(round(aff["gains"], 2)) + " USDT\n"
                "Ventes : " + str(aff["ventes"]) + "\n"
                "Inscriptions : " + str(aff["inscriptions"]) + "\n"
                "Partages : " + str(aff.get("partages", 0)) + "\n"
                "VIP gagné : " + str(aff.get("vip_jours", 0)) + " jours"
            )
        else:
            await query.message.reply_text("Tape /affiliation d'abord.")

    elif query.data == "partage":
        db       = load_db()
        nb_parts = db["partages"].get(str(user.id), 0)
        palier   = get_palier(nb_parts)
        keyboard = [
            [InlineKeyboardButton("📡 Partager le groupe", url=LIEN_GROUPE)],
            [InlineKeyboardButton("💎 Canal VIP",          url=CANAL_VIP)],
        ]
        await query.message.reply_text(
            "🎁 Partage & Récompenses\n\n"
            "Tes partages : " + str(nb_parts) + "\n"
            "Palier actuel : " + (palier[0] if palier else "Aucun encore") + "\n\n"
            "Paliers :\n"
            "1  → 1 jour VIP offert\n"
            "5  → 3 jours + sticker\n"
            "10 → 7 jours + 22% commission\n"
            "20 → 1 mois VIP + 25%\n"
            "50 → 3 mois Elite + 30% à vie\n\n"
            "🎁 1 filleul rejoint = 7 jours VIP GRATUITS !\n\n"
            "Partage puis tape /partage pour déclarer !",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "gains":
        aff = get_affilie(user.id)
        if aff:
            await query.message.reply_text(
                "💰 Tes gains : " + str(round(aff["gains"], 2)) + " USDT\n"
                "Ventes : " + str(aff["ventes"]) + "\n"
                "VIP gagné : " + str(aff.get("vip_jours", 0)) + " jours\n\n"
                "Paiement dès 10 USDT\nContact : " + ADMIN_USERNAME
            )
        else:
            await query.message.reply_text("Tape /affiliation d'abord.")

    elif query.data == "aide":
        await query.message.reply_text(
            "❓ Comment ça marche ?\n\n"
            "1. Tape /affiliation → reçois ton lien unique\n"
            "2. Partage sur TikTok / Insta / WhatsApp / Facebook\n"
            "3. Quelqu'un s'abonne via ton lien\n"
            "4. Tu reçois " + str(COMMISSION_PCT) + "% du paiement\n\n"
            "Commissions :\n"
            "→ Abonnement 5$/mois   = " + str(round(PRIX_MENSUEL * COMMISSION_PCT / 100, 2)) + " USDT\n"
            "→ Dépôt VIP 50$        = " + str(round(PRIX_DEPOT_VIP * COMMISSION_PCT / 100, 2)) + " USDT\n\n"
            "🎁 BONUS PARRAINAGE :\n"
            "1 ami rejoint via ton lien = 7 jours VIP GRATUITS pour toi !\n\n"
            "💡 Pas de compte nécessaire pour commencer !\n"
            "→ Dis à tes amis de déposer 50$ sur Binance via ton lien :\n"
            + LIEN_BINANCE + "\n\n"
            "Paiement USDT — Contact : " + ADMIN_USERNAME
        )

    elif query.data.startswith("copier_"):
        code = query.data.replace("copier_", "")
        lien = "https://t.me/" + BOT_USERNAME + "?start=" + code
        await query.message.reply_text(
            "📋 Ton lien d'affiliation :\n\n" + lien + "\n\n"
            "Appuie longtemps pour copier 👆"
        )


# ═══════════════════════════════════════════════════════
# PLANIFICATEUR AUTOMATIQUE
# ═══════════════════════════════════════════════════════

async def planificateur(bot):
    last = {}
    print("[PLANIFICATEUR] Demarre")
    while True:
        now   = datetime.now()
        h, m  = now.hour, now.minute
        jour  = now.weekday()
        today = now.date()

        if h == 7  and m < 2 and last.get("bonjour")    != today:
            await envoyer_bonjour(bot); last["bonjour"] = today
        elif h == 10 and m < 2 and last.get("motiv1")   != today:
            await envoyer_motivation(bot); last["motiv1"] = today
        elif h == 12 and m < 2 and last.get("analyse1") != today:
            await envoyer_analyse(bot); last["analyse1"] = today
        elif h == 15 and m < 2 and last.get("motiv2")   != today:
            await envoyer_motivation(bot); last["motiv2"] = today
        elif h == 18 and m < 2 and last.get("analyse2") != today:
            await envoyer_analyse(bot); last["analyse2"] = today
        elif h == 21 and m < 2 and last.get("bonsoir")  != today:
            await envoyer_bonsoir(bot); last["bonsoir"] = today
        elif h == 14 and m < 2 and jour in [2, 5] and last.get("communaute") != today:
            await envoyer_rappel_communaute(bot); last["communaute"] = today

        ob_key = "ob_" + str(h) + "_" + str(today)
        if m < 2 and ob_key not in last:
            await scanner_order_blocks(bot)
            last[ob_key] = True

        await asyncio.sleep(60)


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

async def main():
    print("==========================================")
    print("  AlphaBot Pro V3 — @leaderodg_bot")
    print("==========================================")
    print("Canal VIP  : " + CANAL_VIP)
    print("VIP 5$/mois | Dépôt 50$ USDT")
    print("Parrainage : " + str(JOURS_VIP_PAR_PARRAINAGE) + " jours VIP / filleul")
    print("Scanner    : BTC, ETH, SOL, BNB, XRP + EUR/USD, GBP/USD, XAU/USD")
    print()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",            cmd_start))
    app.add_handler(CommandHandler("vip",              cmd_vip))
    app.add_handler(CommandHandler("payer",            cmd_payer))
    app.add_handler(CommandHandler("affiliation",      cmd_affiliation))
    app.add_handler(CommandHandler("dashboard",        cmd_dashboard))
    app.add_handler(CommandHandler("partage",          cmd_partage))
    app.add_handler(CommandHandler("binance",          cmd_binance))
    app.add_handler(CommandHandler("admin",            cmd_admin))
    app.add_handler(CommandHandler("valider_vente",    cmd_valider_vente))
    app.add_handler(CommandHandler("valider_paiement", cmd_valider_paiement))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print("✅ Bot en ligne !")
        await envoyer_bienvenue_lancement(app.bot)
        await planificateur(app.bot)
        await app.updater.stop()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())

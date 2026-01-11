import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import sys

# ================= CONFIG =================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2
MIN_SCORE = 7.5

STOCKS = {
    # --- BANKS & FINANCE ---
    "HDFCBANK.NS": "Bank", "ICICIBANK.NS": "Bank", "SBIN.NS": "Bank",
    "AXISBANK.NS": "Bank", "KOTAKBANK.NS": "Bank", "INDUSINDBK.NS": "Bank",
    "BAJFINANCE.NS": "Finance", "BAJAJFINSV.NS": "Finance",
    "PFC.NS": "Finance", "REC.NS": "Finance", "JIOFIN.NS": "Finance",
    "CHOLAFIN.NS": "Finance", "SHRIRAMFIN.NS": "Finance",

    # --- IT & TECH ---
    "TCS.NS": "IT", "INFY.NS": "IT", "HCLTECH.NS": "IT",
    "WIPRO.NS": "IT", "TECHM.NS": "IT", "LTIM.NS": "IT",
    "KPITTECH.NS": "Tech", "PERSISTENT.NS": "Tech", "COFORGE.NS": "Tech",
    "ZOMATO.NS": "Tech", "NAUKRI.NS": "Tech", "PBFINTECH.NS": "Tech",

    # --- AUTO & AUTO ANCILLARY ---
    "MARUTI.NS": "Auto", "TATAMOTORS.NS": "Auto", "M&M.NS": "Auto",
    "BAJAJ-AUTO.NS": "Auto", "EICHERMOT.NS": "Auto", "TVSMOTOR.NS": "Auto",
    "HEROMOTOCO.NS": "Auto", "TIINDIA.NS": "Auto Ancillary",
    "BHARATFORG.NS": "Auto Ancillary", "MOTHERSON.NS": "Auto Ancillary",

    # --- ENERGY, OIL & POWER ---
    "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "COALINDIA.NS": "Energy",
    "NTPC.NS": "Power", "POWERGRID.NS": "Power", "TATAPOWER.NS": "Power",
    "ADANIGREEN.NS": "Power", "ADANIPOWER.NS": "Power", "JSWENERGY.NS": "Power",
    "IOC.NS": "Oil & Gas", "BPCL.NS": "Oil & Gas",

    # --- DEFENSE, RAIL & PSU ---
    "HAL.NS": "Defense", "BEL.NS": "Defense", "MAZDOCK.NS": "Defense",
    "COCHINSHIP.NS": "Defense", "BDL.NS": "Defense",
    "RVNL.NS": "Railways", "IRFC.NS": "Railways", "IRCON.NS": "Railways",
    "IRCTC.NS": "Railways",

    # --- CAPITAL GOODS & INFRA ---
    "LT.NS": "Infra", "ABB.NS": "Cap Goods", "SIEMENS.NS": "Cap Goods",
    "CGPOWER.NS": "Cap Goods", "CUMMINSIND.NS": "Cap Goods",
    "ADANIENT.NS": "Infra", "GMRINFRA.NS": "Infra",

    # --- CONSUMER (FMCG, RETAIL) ---
    "ITC.NS": "FMCG", "HINDUNILVR.NS": "FMCG", "NESTLEIND.NS": "FMCG",
    "BRITANNIA.NS": "FMCG", "VBL.NS": "FMCG", "TATACONSUM.NS": "FMCG",
    "TITAN.NS": "Retail", "TRENT.NS": "Retail", "DMART.NS": "Retail",
    "ABFRL.NS": "Retail", "HAVELLS.NS": "Consumer Durables", "DIXON.NS": "Electronics",

    # --- PHARMA & HEALTHCARE ---
    "SUNPHARMA.NS": "Pharma", "CIPLA.NS": "Pharma", "DRREDDY.NS": "Pharma",
    "DIVISLAB.NS": "Pharma", "LUPIN.NS": "Pharma", "AUROPHARMA.NS": "Pharma",
    "APOLLOHOSP.NS": "Healthcare", "MAXHEALTH.NS": "Healthcare",

    # --- METALS & MINING ---
    "TATASTEEL.NS": "Metals", "JSWSTEEL.NS": "Metals", "HINDALCO.NS": "Metals",
    "VEDL.NS": "Metals", "NMDC.NS": "Mining", "JINDALSTEL.NS": "Metals",

    # --- REALTY ---
    "DLF.NS": "Realty", "GODREJPROP.NS": "Realty", "LODHA.NS": "Realty",
    "PHOENIXLTD.NS": "Realty"
}

# ================= MEMORY =================
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)

def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h, f)

def is_duplicate(ticker):
    h = load_history()
    if ticker in h:
        last = datetime.datetime.strptime(h[ticker], "%Y-%m-%d").date()
        return (datetime.date.today() - last).days < 5
    return False

def update_history(ticker):
    h = load_history()
    h[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_history(h)

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)

# ================= MARKET FILTER =================
def is_market_bullish():
    nifty = yf.download("^NSEI", period="1y", progress=False)
    nifty["EMA50"] = ta.ema(nifty["Close"], 50)
    nifty["RSI"] = ta.rsi(nifty["Close"], 14)

    return (
        nifty["Close"].iloc[-1] > nifty["EMA50"].iloc[-1] and
        nifty["RSI"].iloc[-1] > 40
    )

# ================= ANALYSIS =================
def analyze_stock(ticker, sector, nifty_close):
    try:
        df = yf.download(ticker, period="1y", progress=False)
        if df.empty or len(df) < 200:
            return None

        df["EMA20"] = ta.ema(df["Close"], 20)
        df["EMA50"] = ta.ema(df["Close"], 50)
        df["EMA200"] = ta.ema(df["Close"], 200)
        df["RSI"] = ta.rsi(df["Close"], 14)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        avg_vol = df["Volume"].rolling(20).mean().iloc[-1]

        # --- Trend ---
        if curr["Close"] < curr["EMA200"] or curr["EMA50"] < curr["EMA200"]:
            return None

        # --- Relative Strength vs NIFTY ---
        stock_ret = curr["Close"] / df["Close"].iloc[-21]
        nifty_ret = nifty_close.iloc[-1] / nifty_close.iloc[-21]
        if stock_ret < nifty_ret:
            return None

        score = 5.0
        reasons = []

        vol_x = curr["Volume"] / avg_vol
        if vol_x > 1.5:
            score += min(vol_x, 3)
            reasons.append(f"Volume expansion ({round(vol_x,1)}x)")

        if curr["Close"] > curr["EMA20"]:
            score += 1
            reasons.append("Price above 20 EMA")

        setup = None

        # --- Breakout ---
        last_10 = df.iloc[-11:-1]
        range_high = last_10["High"].max()
        range_low = last_10["Low"].min()
        width = (range_high - range_low) / range_low

        if curr["Close"] > range_high and width < 0.08 and curr["RSI"] < 70:
            setup = "🚀 Breakout"
            score += 2
            reasons.append("Tight consolidation breakout")

        # --- Pullback ---
        elif abs(curr["Close"] - curr["EMA20"]) / curr["Close"] < 0.03 and 45 <= curr["RSI"] <= 60:
            setup = "🧲 Pullback"
            score += 1.5
            reasons.append("RSI reset + EMA20 support")

        if not setup or score < MIN_SCORE:
            return None

        # --- Stops ---
        stop_loss = range_low * 0.995 if setup == "🚀 Breakout" else curr["EMA50"]

        return {
            "symbol": ticker.replace(".NS", ""),
            "sector": sector,
            "setup": setup,
            "entry": round(curr["Close"], 1),
            "sl": round(stop_loss, 1),
            "t1": round(curr["Close"] * 1.10, 1),
            "t2": round(curr["Close"] * 1.25, 1),
            "score": round(score, 2),
            "reasons": reasons
        }

    except:
        return None

# ================= RUNNER =================
def run_scan():
    print("🔍 Running Swing Bot")

    if not is_market_bullish():
        print("❌ Market weak – no trades")
        return

    nifty_close = yf.download("^NSEI", period="1y", progress=False)["Close"]
    signals = []

    for ticker, sector in STOCKS.items():
        if is_duplicate(ticker):
            continue
        s = analyze_stock(ticker, sector, nifty_close)
        if s:
            signals.append(s)

    signals.sort(key=lambda x: x["score"], reverse=True)

    for s in signals[:MAX_ALERTS_PER_DAY]:
        msg = f"""
🚀 *SWING TRADE ALERT*

📌 *Stock:* {s['symbol']}
🏢 *Sector:* {s['sector']}
🛠 *Setup:* {s['setup']}
📊 *Score:* {s['score']}/10

🧠 *Reasoning:*
""" + "\n".join([f"• {r}" for r in s["reasons"]]) + f"""

📍 *Entry:* {s['entry']}
🎯 *Targets:* {s['t1']} / {s['t2']}
⛔ *Stop Loss:* {s['sl']}

_Not financial advice_
"""
        send_telegram(msg)
        update_history(s["symbol"] + ".NS")
        print(f"✅ Alert sent: {s['symbol']}")

# ================= MAIN =================
if __name__ == "__main__":
    run_scan()

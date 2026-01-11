import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import sys

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Error: Telegram tokens not found in GitHub Secrets.")
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2  # Send top 2 best stocks
MIN_SCORE = 7.0         # Minimum quality score to trigger alert

# ================= FULL STOCK UNIVERSE =================
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

# ================= MEMORY SYSTEM =================
def load_history():
    if not os.path.exists(HISTORY_FILE): return {}
    try:
        with open(HISTORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f: json.dump(history, f)

def is_duplicate_alert(ticker):
    history = load_history()
    if ticker in history:
        last_date = datetime.datetime.strptime(history[ticker], "%Y-%m-%d").date()
        # Don't alert same stock for 5 days
        if (datetime.date.today() - last_date).days < 5: return True
    return False

def update_history(ticker):
    history = load_history()
    history[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_history(history)

# ================= TELEGRAM SENDER =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_close):
    try:
        # Download Stock Data
        df = yf.download(ticker, period="1y", progress=False)
        
        # --- SAFETY FIX: Handle yfinance MultiIndex ---
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                temp = pd.DataFrame()
                temp['Close'] = df.xs('Close', level=0, axis=1)[ticker]
                temp['High'] = df.xs('High', level=0, axis=1)[ticker]
                temp['Low'] = df.xs('Low', level=0, axis=1)[ticker]
                temp['Volume'] = df.xs('Volume', level=0, axis=1)[ticker]
                df = temp
            except:
                return None
        
        if len(df) < 200: return None
        # ----------------------------------------------

        # Indicators
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        df = df.dropna()
        if len(df) < 5: return None

        curr = df.iloc[-1]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
        
        # 1. Trend Filter (Must be in Uptrend)
        if curr['Close'] < curr['EMA_200']: return None
        if curr['EMA_50'] < curr['EMA_200']: return None

        # 2. Relative Strength vs Nifty (Check if stock is beating index)
        # We try to calculate it, but if dates don't align, we skip the check rather than crashing
        try:
            stock_ret = curr["Close"] / df["Close"].iloc[-21]
            
            # Find matching Nifty date
            nifty_idx = nifty_close.index.searchsorted(curr.name)
            if nifty_idx >= len(nifty_close): nifty_idx = -1
            nifty_val = nifty_close.iloc[nifty_idx]
            nifty_old = nifty_close.iloc[nifty_idx - 20] 
            
            nifty_ret = nifty_val / nifty_old
            
            # REJECT if stock is significantly weaker than market
            if stock_ret < nifty_ret: return None
        except:
            pass # Safety pass

        # 3. Setup Detection & Scoring
        setup = None
        score = 5.0 
        reasons = []
        
        # Volume Boost
        vol_multiple = round(curr['Volume'] / avg_vol, 1)
        if vol_multiple > 1.5:
            score += min(vol_multiple, 2.0)
            reasons.append(f"Volume surge ({vol_multiple}x avg)")
        
        # Trend Strength
        if curr['Close'] > curr['EMA_20']:
            score += 1.0
            reasons.append("Holding above 20 EMA")
        
        # --- LOGIC: BREAKOUT ---
        last_10 = df.iloc[-11:-1]
        range_high = last_10['High'].max()
        range_low = last_10['Low'].min()
        width = (range_high - range_low) / range_low
        
        if curr['Close'] > range_high and width < 0.12 and curr['RSI'] < 70:
            setup = "🚀 Breakout"
            score += 2.0
            reasons.append("Breakout from Tight Consolidation")
            
        # --- LOGIC: PULLBACK ---
        elif abs(curr['Close'] - curr['EMA_20']) / curr['Close'] < 0.03 and 45 <= curr['RSI'] <= 60:
            setup = "🧲 Pullback"
            score += 1.5
            reasons.append("RSI Reset + 20 EMA Support")
        
        # Final Score Check
        if not setup or score < MIN_SCORE: return None

        # Targets
        stop_loss = range_low if setup == "🚀 Breakout" else curr['EMA_50']
        
        return {
            "symbol": ticker.replace('.NS', ''),
            "sector": sector,
            "setup": setup,
            "entry": round(curr['Close'], 1),
            "sl": round(stop_loss, 1),
            "t1": round(curr['Close'] * 1.10, 1),
            "t2": round(curr['Close'] * 1.25, 1),
            "score": round(score, 1),
            "reasons": reasons,
            "rsi": round(curr['RSI'], 1)
        }

    except Exception as e:
        return None
    return None

# ================= ORCHESTRATOR =================
def run_scan():
    print("--- 🔍 Starting Pro Swing Scan ---")
    
    # 1. Fetch Nifty Data (For RS Calculation)
    # We still fetch Nifty to compare relative strength, but we REMOVED the "Stop if Market Weak" filter.
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)
        # Handle Nifty MultiIndex
        if isinstance(nifty.columns, pd.MultiIndex):
            nifty_close = nifty.xs('Close', level=0, axis=1)["^NSEI"]
        else:
            nifty_close = nifty['Close']
    except:
        print("Warning: Could not fetch Nifty data. Proceeding without RS check.")
        nifty_close = pd.Series()

    signals = []
    
    # 2. Analyze Stocks
    print(f"Scanning {len(STOCKS)} stocks...")
    for ticker, sector in STOCKS.items():
        if is_duplicate_alert(ticker): 
            continue
        
        data = analyze_stock(ticker, sector, nifty_close)
        if data:
            print(f"✅ Found: {data['symbol']} (Score: {data['score']})")
            signals.append(data)
    
    # 3. Sort by Score
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    # 4. Send Top Alerts
    if not signals:
        print("No setups found today.")
        return

    print(f"--- Sending {min(len(signals), MAX_ALERTS_PER_DAY)} Alerts ---")
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        # Format Reasoning List
        reasoning_text = "\n".join([f"• {r}" for r in s['reasons']])
        
        msg = f"""
🚀 **SWING TRADE ALERT**

📌 **Stock:** {s['symbol']}
🏢 **Sector:** {s['sector']}
🛠 **Setup:** {s['setup']}
🎯 **Target:** 10–25%
⏳ **Timeframe:** 7–18 days
📊 **Confidence Score:** {s['score']} / 10

🧠 **AI Reasoning:**
{reasoning_text}
• Trend is Bullish (Above 200 EMA)

📍 **Entry:** {s['entry']}
🎯 **Targets:** {s['t1']} | {s['t2']}
⛔ **Stop Loss:** {s['sl']}

_Auto-Analysis by SwingBot_
        """
        send_telegram_alert(msg)
        update_history(s['symbol'] + ".NS")

if __name__ == "__main__":
    run_scan()

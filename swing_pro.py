import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import sys

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2

# STOCK UNIVERSE with SECTOR MAPPING (For "Deep Analysis")
# You can add more stocks here.
STOCKS = {
    "RELIANCE.NS": "Energy", "TRENT.NS": "Retail", "HAL.NS": "Defense", 
    "BEL.NS": "Defense", "TATAMOTORS.NS": "Auto", "KPITTECH.NS": "Tech",
    "ZOMATO.NS": "Tech", "VBL.NS": "FMCG", "ABB.NS": "Capital Goods", 
    "SIEMENS.NS": "Capital Goods", "ADANIENT.NS": "Metals/Mining", 
    "COALINDIA.NS": "Energy", "NTPC.NS": "Energy", "SBIN.NS": "Bank", 
    "ITC.NS": "FMCG", "BAJFINANCE.NS": "Finance", "DMART.NS": "Retail", 
    "SUNPHARMA.NS": "Pharma", "HDFCBANK.NS": "Bank", "INFY.NS": "IT"
}

# --- MEMORY SYSTEM ---
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
        if (datetime.date.today() - last_date).days < 5: return True
    return False

def update_history(ticker):
    history = load_history()
    history[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_history(history)

# --- TELEGRAM SENDER ---
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- ANALYSIS ENGINE ---
def analyze_stock(ticker, sector):
    try:
        df = yf.download(ticker, period="1y", progress=False)
        if df.empty or len(df) < 200: return None
        
        # Data Cleanup
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs('Close', level=0, axis=1)[ticker].to_frame(name='Close')
            df['Volume'] = yf.download(ticker, period="1y", progress=False)['Volume']

        # 1. Indicator Calculation
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
        
        # 2. Trend Filter (Must be in Uptrend)
        if curr['Close'] < curr['EMA_200']: return None
        if curr['EMA_50'] < curr['EMA_200']: return None

        # 3. Setup Detection & Scoring
        setup = None
        score = 5.0 # Start with base score
        reasons = [] # "AI Reasoning" points
        
        # Vol Check
        vol_multiple = round(curr['Volume'] / avg_vol, 1)
        if vol_multiple > 1.5:
            score += 1.5
            reasons.append(f"Volume surge ({vol_multiple}x average)")
        
        # Trend Strength
        if curr['Close'] > curr['EMA_20']:
            score += 1.0
            reasons.append("Price sustaining above 20 EMA")
        
        # --- LOGIC: BREAKOUT ---
        last_10 = df.iloc[-11:-1]
        range_high = last_10['High'].max() if 'High' in last_10 else last_10['Close'].max()
        
        if curr['Close'] > range_high and vol_multiple > 1.3:
            setup = "Breakout"
            score += 2.0
            reasons.append("Breakout from consolidation zone")
            entry_type = "Momentum"
            
        # --- LOGIC: PULLBACK ---
        elif abs(curr['Close'] - curr['EMA_20']) / curr['Close'] < 0.03 and 45 <= curr['RSI'] <= 60:
            setup = "Pullback"
            score += 1.5
            reasons.append("RSI Reset (45-60) in Uptrend")
            reasons.append("Respecting 20 EMA Support")
            entry_type = "Trend Continuation"
        
        if not setup: return None

        # 4. Final Data for Alert
        # Targets
        entry_price = curr['Close']
        stop_loss = curr['EMA_20'] * 0.98 # 2% below 20 EMA
        target_1 = entry_price * 1.10 # 10%
        target_2 = entry_price * 1.25 # 25%
        
        # Cap Score
        score = min(round(score, 1), 9.8)
        
        return {
            "symbol": ticker.replace('.NS', ''),
            "sector": sector,
            "setup": setup,
            "entry": round(entry_price, 1),
            "entry_zone": f"{round(entry_price * 0.995, 1)} - {round(entry_price * 1.005, 1)}",
            "sl": round(stop_loss, 1),
            "t1": round(target_1, 1),
            "t2": round(target_2, 1),
            "score": score,
            "reasons": reasons,
            "rsi": round(curr['RSI'], 1)
        }

    except Exception as e:
        return None

# --- ORCHESTRATOR ---
def run_scan():
    print("--- Starting Deep Analysis Scan ---")
    signals = []
    
    # Analyze each stock in the map
    for ticker, sector in STOCKS.items():
        if is_duplicate_alert(ticker): continue
        
        data = analyze_stock(ticker, sector)
        if data:
            signals.append(data)
    
    # Sort by Confidence Score
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    # Send Top 2 Alerts
    for s in signals[:MAX_ALERTS_PER_DAY]:
        # Create Bullet Points for Reasoning
        reasoning_text = "\n".join([f"• {r}" for r in s['reasons']])
        
        msg = f"""
🚀 **SWING TRADE ALERT**

📌 **Stock:** {s['symbol']}
🏢 **Sector:** {s['sector']}
🎯 **Target:** 10–25%
⏳ **Timeframe:** 7–18 days
📊 **Confidence Score:** {s['score']} / 10

🧠 **AI Reasoning:**
{reasoning_text}
• Trend is Bullish (Above 200 EMA)

📍 **Entry Zone:** {s['entry_zone']}
⛔ **Stop Loss:** {s['sl']}

_Auto-Analysis by SwingBot_
        """
        send_telegram_alert(msg)
        update_history(s['symbol'] + ".NS")
        print(f"Sent Alert: {s['symbol']}")

if __name__ == "__main__":
    run_scan()

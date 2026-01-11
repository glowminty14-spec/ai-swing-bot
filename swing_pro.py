import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import sys

# --- CONFIGURATION (Loaded from GitHub Secrets) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Error: Secrets not found.")
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2

# STOCK UNIVERSE (Sample Nifty 500 stocks)
TICKERS = [
    "RELIANCE.NS", "TRENT.NS", "HAL.NS", "BEL.NS", "TATAMOTORS.NS", 
    "KPITTECH.NS", "ZOMATO.NS", "VBL.NS", "ABB.NS", "SIEMENS.NS",
    "ADANIENT.NS", "COALINDIA.NS", "NTPC.NS", "SBIN.NS", "ITC.NS",
    "BAJFINANCE.NS", "DMART.NS", "SUNPHARMA.NS", "HDFCBANK.NS", "INFY.NS",
    "BHEL.NS", "ONGC.NS", "POWERGRID.NS", "TITAN.NS", "ULTRACEMCO.NS"
]

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

# --- TELEGRAM ---
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- ANALYSIS ENGINE ---
def analyze_stock(ticker):
    try:
        df = yf.download(ticker, period="1y", progress=False)
        if df.empty or len(df) < 200: return None
        
        # Clean Data
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs('Close', level=0, axis=1)[ticker].to_frame(name='Close')
            # Fetch Volume separately if needed, but for simplicity using Close here
            # Note: For production, proper multi-index handling is preferred.
            # This is a simplified fallback for yfinance updates.
            df['Volume'] = yf.download(ticker, period="1y", progress=False)['Volume']

        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
        price = df['Close'].iloc[-1]
        
        if price < 50 or avg_vol < 500000: return None

        # Indicators
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        curr = df.iloc[-1]
        
        # Trend Filter
        if curr['Close'] < curr['EMA_200']: return None
        if curr['EMA_50'] < curr['EMA_200']: return None

        # Strategy: Breakout or Pullback
        setup = None
        score = 0
        
        # Breakout Logic
        last_10 = df.iloc[-11:-1]
        range_high = last_10['High'].max() if 'High' in last_10 else last_10['Close'].max()
        
        if curr['Close'] > range_high and curr['Volume'] > (2 * avg_vol):
            setup = "🚀 Breakout"
            score = 10
            
        # Pullback Logic
        dist_ema = abs(curr['Close'] - curr['EMA_20']) / curr['Close']
        if dist_ema < 0.03 and 40 < curr['RSI'] < 60:
            setup = "🧲 Pullback"
            score = 8
            
        if setup:
            return {
                "symbol": ticker, "setup": setup, "price": curr['Close'],
                "score": score, "rsi": round(curr['RSI'],1)
            }
            
    except: return None
    return None

def run_scan():
    print("--- Scan Started ---")
    signals = []
    for ticker in TICKERS:
        if is_duplicate_alert(ticker): continue
        s = analyze_stock(ticker)
        if s: signals.append(s)
    
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        msg = f"🚨 **{s['symbol']}**\nSetup: {s['setup']}\nPrice: {round(s['price'],1)}\nRSI: {s['rsi']}"
        send_telegram_alert(msg)
        update_history(s['symbol'])
        print(f"Sent: {s['symbol']}")

if __name__ == "__main__":
    run_scan()

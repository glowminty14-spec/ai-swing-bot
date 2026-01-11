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
    print("❌ Error: Telegram tokens not found.")
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2
MIN_SCORE = 7.5  # Higher threshold for better quality

# ================= FULL STOCK UNIVERSE =================
STOCKS = {
    "HDFCBANK.NS": "Bank", "ICICIBANK.NS": "Bank", "SBIN.NS": "Bank",
    "AXISBANK.NS": "Bank", "KOTAKBANK.NS": "Bank", "INDUSINDBK.NS": "Bank",
    "BAJFINANCE.NS": "Finance", "BAJAJFINSV.NS": "Finance", "PFC.NS": "Finance",
    "REC.NS": "Finance", "JIOFIN.NS": "Finance", "CHOLAFIN.NS": "Finance",
    "TCS.NS": "IT", "INFY.NS": "IT", "HCLTECH.NS": "IT", "WIPRO.NS": "IT",
    "TECHM.NS": "IT", "LTIM.NS": "IT", "KPITTECH.NS": "Tech",
    "ZOMATO.NS": "Tech", "NAUKRI.NS": "Tech", "PBFINTECH.NS": "Tech",
    "MARUTI.NS": "Auto", "TATAMOTORS.NS": "Auto", "M&M.NS": "Auto",
    "BAJAJ-AUTO.NS": "Auto", "EICHERMOT.NS": "Auto", "TVSMOTOR.NS": "Auto",
    "HEROMOTOCO.NS": "Auto", "TIINDIA.NS": "Auto Ancillary",
    "BHARATFORG.NS": "Auto Ancillary", "RELIANCE.NS": "Energy",
    "ONGC.NS": "Energy", "COALINDIA.NS": "Energy", "NTPC.NS": "Power",
    "POWERGRID.NS": "Power", "TATAPOWER.NS": "Power", "ADANIGREEN.NS": "Power",
    "ADANIPOWER.NS": "Power", "IOC.NS": "Oil", "BPCL.NS": "Oil",
    "HAL.NS": "Defense", "BEL.NS": "Defense", "MAZDOCK.NS": "Defense",
    "COCHINSHIP.NS": "Defense", "BDL.NS": "Defense", "RVNL.NS": "Railways",
    "IRFC.NS": "Railways", "IRCON.NS": "Railways", "LT.NS": "Infra",
    "ABB.NS": "Cap Goods", "SIEMENS.NS": "Cap Goods", "CGPOWER.NS": "Cap Goods",
    "ADANIENT.NS": "Infra", "ITC.NS": "FMCG", "HINDUNILVR.NS": "FMCG",
    "NESTLEIND.NS": "FMCG", "VBL.NS": "FMCG", "TATACONSUM.NS": "FMCG",
    "TITAN.NS": "Retail", "TRENT.NS": "Retail", "DMART.NS": "Retail",
    "SUNPHARMA.NS": "Pharma", "CIPLA.NS": "Pharma", "DRREDDY.NS": "Pharma",
    "DIVISLAB.NS": "Pharma", "APOLLOHOSP.NS": "Healthcare",
    "TATASTEEL.NS": "Metals", "JSWSTEEL.NS": "Metals", "HINDALCO.NS": "Metals",
    "VEDL.NS": "Metals", "DLF.NS": "Realty", "GODREJPROP.NS": "Realty"
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
        if (datetime.date.today() - last_date).days < 5: return True
    return False

def update_history(ticker):
    history = load_history()
    history[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_history(history)

# ================= TELEGRAM =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except: pass

# ================= ADVANCED ANALYSIS =================
def analyze_stock(ticker, sector, nifty_close):
    try:
        # Download 2 Years of data (Required for Weekly Analysis)
        df = yf.download(ticker, period="2y", progress=False)
        
        # --- Safety Fix ---
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                temp = pd.DataFrame()
                temp['Close'] = df.xs('Close', level=0, axis=1)[ticker]
                temp['High'] = df.xs('High', level=0, axis=1)[ticker]
                temp['Low'] = df.xs('Low', level=0, axis=1)[ticker]
                temp['Volume'] = df.xs('Volume', level=0, axis=1)[ticker]
                df = temp
            except: return None
        # ------------------

        if len(df) < 300: return None # Need enough data for weekly resampling

        # 1️⃣ WEEKLY TIMEFRAME CHECK (The Big Picture)
        # Resample Daily data to Weekly
        df_weekly = df.resample('W').agg({'Close': 'last'})
        df_weekly['EMA_50'] = ta.ema(df_weekly['Close'], length=50)
        
        # Rule: Weekly Price MUST be above Weekly 50 EMA
        current_weekly_close = df_weekly['Close'].iloc[-1]
        current_weekly_ema = df_weekly['EMA_50'].iloc[-1]
        
        if pd.isna(current_weekly_ema) or current_weekly_close < current_weekly_ema:
            return None # Reject: Long-term trend is bearish

        # 2️⃣ DAILY INDICATORS
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        # ADX (Trend Strength) - NEW!
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        df = pd.concat([df, adx_df], axis=1) # Join ADX columns
        # Note: pandas_ta names columns like ADX_14, DMP_14, DMN_14
        
        # ATR (Volatility) - NEW!
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)

        df = df.dropna()
        curr = df.iloc[-1]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]

        # 3️⃣ BASIC TREND FILTER
        if curr['Close'] < curr['EMA_200']: return None
        
        # 4️⃣ ADX FILTER (Avoid Choppy Markets)
        # We check ADX_14. If < 20, trend is too weak.
        if curr['ADX_14'] < 20: return None

        # 5️⃣ SCORING & LOGIC
        score = 5.0
        reasons = []
        setup = None

        # Add score for Weekly Alignment
        reasons.append("Weekly Trend is Up (Above 50 EMA)")
        
        # Add score for Strong ADX
        if curr['ADX_14'] > 25:
            score += 1.0
            reasons.append(f"Strong Trend Momentum (ADX: {round(curr['ADX_14'],1)})")

        # Setup A: Breakout
        last_10 = df.iloc[-11:-1]
        range_high = last_10['High'].max()
        range_low = last_10['Low'].min()
        
        if curr['Close'] > range_high and curr['Volume'] > (1.5 * avg_vol):
            setup = "🚀 Breakout"
            score += 2.0
            reasons.append("High Volume Breakout")
        
        # Setup B: Pullback
        elif abs(curr['Close'] - curr['EMA_20']) / curr['Close'] < 0.03 and 45 <= curr['RSI'] <= 60:
            setup = "🧲 Pullback"
            score += 1.5
            reasons.append("Bounce off 20 EMA Support")

        if not setup or score < MIN_SCORE: return None

        # 6️⃣ SMART STOP LOSS (ATR BASED)
        # SL = Price - (2 * ATR)
        atr_value = curr['ATR']
        smart_sl = curr['Close'] - (2 * atr_value)
        
        # Ensure SL isn't too far (max 8% risk)
        max_risk_sl = curr['Close'] * 0.92
        final_sl = max(smart_sl, max_risk_sl)

        return {
            "symbol": ticker.replace('.NS', ''),
            "sector": sector,
            "setup": setup,
            "entry": round(curr['Close'], 1),
            "sl": round(final_sl, 1),
            "t1": round(curr['Close'] + (curr['Close'] - final_sl) * 2, 1), # 1:2 Risk Reward
            "t2": round(curr['Close'] * 1.25, 1),
            "score": round(score, 1),
            "reasons": reasons
        }

    except Exception as e:
        return None

# ================= RUNNER =================
def run_scan():
    print("--- 🔍 Starting Pro Max Scan ---")
    
    # Dummy Nifty Fetch (Kept for compatibility, though RS check is optional now)
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)['Close']
    except:
        nifty = pd.Series()

    signals = []
    
    print(f"Scanning {len(STOCKS)} stocks with Weekly + ADX filters...")
    for ticker, sector in STOCKS.items():
        if is_duplicate_alert(ticker): continue
        
        data = analyze_stock(ticker, sector, nifty)
        if data:
            print(f"✅ Found: {data['symbol']} (Score: {data['score']})")
            signals.append(data)
    
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    if not signals:
        print("No setups passed the strict Weekly/ADX filters.")
        return

    print(f"--- Sending {min(len(signals), MAX_ALERTS_PER_DAY)} Alerts ---")
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        reasoning_text = "\n".join([f"• {r}" for r in s['reasons']])
        risk_per_share = round(s['entry'] - s['sl'], 1)
        
        msg = f"""
💎 **PRO SWING ALERT**

📌 **Stock:** {s['symbol']}
🏢 **Sector:** {s['sector']}
🛠 **Setup:** {s['setup']}
📊 **Score:** {s['score']} / 10

🧠 **AI Analysis:**
{reasoning_text}

📍 **Entry:** {s['entry']}
⛔ **Stop Loss:** {s['sl']} (ATR Based)
🎯 **Target 1:** {s['t1']} (1:2 Risk/Reward)
⚠️ **Risk Per Share:** ₹{risk_per_share}

_Auto-Analysis by SwingBot_
        """
        send_telegram_alert(msg)
        update_history(s['symbol'] + ".NS")

if __name__ == "__main__":
    run_scan()

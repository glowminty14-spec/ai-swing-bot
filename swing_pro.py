import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import sys
import io

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Error: Telegram tokens not found.")
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
MAX_ALERTS_PER_DAY = 2
MIN_SCORE = 7.5

# ================= DYNAMIC STOCK LOADER =================
def fetch_live_nifty_stocks():
    """
    Fetches the latest NIFTY 200 list directly from NSE Archives.
    Returns a dictionary: {'RELIANCE.NS': 'Oil & Gas', ...}
    """
    print("⏳ Downloading latest Nifty 200 list from NSE...")
    
    # URL for Nifty 200 (Best balance of high liquidity and momentum)
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
    
    # Headers to look like a real browser (Prevents blocking)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            # Read CSV data
            df = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
            
            # Create Dictionary: Symbol -> Industry
            live_stocks = {}
            for index, row in df.iterrows():
                symbol = f"{row['Symbol']}.NS"
                industry = row['Industry'] if 'Industry' in row else "Unknown"
                live_stocks[symbol] = industry
            
            print(f"✅ Successfully loaded {len(live_stocks)} stocks from NSE.")
            return live_stocks
        else:
            print(f"⚠️ NSE Download failed (Status {response.status_code}). Using fallback list.")
            return get_fallback_list()

    except Exception as e:
        print(f"⚠️ Error fetching NSE list: {e}. Using fallback list.")
        return get_fallback_list()

def get_fallback_list():
    """Hardcoded backup list in case NSE website is down"""
    return {
        "HDFCBANK.NS": "Bank", "ICICIBANK.NS": "Bank", "SBIN.NS": "Bank",
        "RELIANCE.NS": "Energy", "INFY.NS": "IT", "ITC.NS": "FMCG",
        "L&T.NS": "Infra", "TCS.NS": "IT", "TATAMOTORS.NS": "Auto",
        "SUNPHARMA.NS": "Pharma", "NTPC.NS": "Power", "M&M.NS": "Auto",
        "BHARTIARTL.NS": "Telecom", "COALINDIA.NS": "Metals",
        "BAJFINANCE.NS": "Finance", "ASIANPAINT.NS": "Consumer",
        "MARUTI.NS": "Auto", "TITAN.NS": "Consumer", "HCLTECH.NS": "IT"
    }

# LOAD STOCKS (Runs once when script starts)
STOCKS = fetch_live_nifty_stocks()

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

# ================= TELEGRAM SENDER =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except: pass

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_close):
    try:
        # 1. Download Data (2 Years for Weekly Analysis)
        df = yf.download(ticker, period="2y", progress=False)
        
        # --- Safety Fix for yfinance updates ---
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
        
        if len(df) < 300: return None 
        # ---------------------------------------

        # 2. WEEKLY TREND CHECK
        # Resample to Weekly candles
        df_weekly = df.resample('W').agg({'Close': 'last'})
        df_weekly['EMA_50'] = ta.ema(df_weekly['Close'], length=50)
        
        curr_wk_close = df_weekly['Close'].iloc[-1]
        curr_wk_ema = df_weekly['EMA_50'].iloc[-1]
        
        # Rule: Weekly Trend must be UP
        if pd.isna(curr_wk_ema) or curr_wk_close < curr_wk_ema:
            return None 

        # 3. DAILY INDICATORS
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        
        # ADX (Trend Strength)
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        df = pd.concat([df, adx_df], axis=1)

        df = df.dropna()
        curr = df.iloc[-1]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]

        # 4. FILTERS
        # Daily Trend
        if curr['Close'] < curr['EMA_200']: return None
        
        # Chop Filter (ADX > 20)
        # Handle cases where ADX might be named differently
        adx_col = 'ADX_14' if 'ADX_14' in df.columns else df.columns[df.columns.str.contains('ADX')][0]
        if curr[adx_col] < 20: return None

        # 5. SCORING
        score = 5.0
        reasons = []
        setup = None
        
        reasons.append(f"Weekly Trend Bullish")

        # ADX Bonus
        if curr[adx_col] > 25:
            score += 1.0
            reasons.append(f"Strong Momentum (ADX: {round(curr[adx_col],1)})")

        # Setup A: Breakout
        last_10 = df.iloc[-11:-1]
        range_high = last_10['High'].max()
        
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

        # 6. TARGETS & STOPS
        # ATR Based Stop Loss
        atr_val = curr['ATR']
        stop_loss = curr['Close'] - (2 * atr_val)
        
        # Cap SL at 8% max risk
        max_risk_sl = curr['Close'] * 0.92
        final_sl = max(stop_loss, max_risk_sl)

        return {
            "symbol": ticker.replace('.NS', ''),
            "sector": sector,
            "setup": setup,
            "entry": round(curr['Close'], 1),
            "sl": round(final_sl, 1),
            "t1": round(curr['Close'] + (curr['Close'] - final_sl) * 2, 1),
            "t2": round(curr['Close'] * 1.25, 1),
            "score": round(score, 1),
            "reasons": reasons
        }

    except Exception as e:
        return None

# ================= RUNNER =================
def run_scan():
    print("--- 🔍 Starting Auto-Fetch Scan ---")
    
    # Dummy Nifty fetch
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)['Close']
    except:
        nifty = pd.Series()

    signals = []
    
    print(f"Scanning {len(STOCKS)} stocks from NSE List...")
    
    # Scan Stocks
    for ticker, sector in STOCKS.items():
        if is_duplicate_alert(ticker): continue
        
        data = analyze_stock(ticker, sector, nifty)
        if data:
            print(f"✅ Found: {data['symbol']} (Score: {data['score']})")
            signals.append(data)
    
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    if not signals:
        print("No high-quality setups found today.")
        return

    print(f"--- Sending {min(len(signals), MAX_ALERTS_PER_DAY)} Alerts ---")
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        reasoning_text = "\n".join([f"• {r}" for r in s['reasons']])
        risk = round(s['entry'] - s['sl'], 1)
        
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
🎯 **Target:** {s['t1']} (1:2 Risk)
⚠️ **Risk:** ₹{risk} / share

_Auto-Analysis by SwingBot_
        """
        send_telegram_alert(msg)
        update_history(s['symbol'] + ".NS")

if __name__ == "__main__":
    run_scan()

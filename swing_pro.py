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
TRADES_FILE = "trades.json"  # <--- NEW: File to communicate with Trade Manager
MAX_ALERTS_PER_DAY = 2
MIN_SCORE = 7.5

# ================= DYNAMIC STOCK LOADER =================
def fetch_live_nifty_stocks():
    """Fetches Nifty 200 list from NSE Archives"""
    print("⏳ Downloading Nifty 200 list from NSE...")
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
            # Create Dict: Symbol -> Industry
            live_stocks = {}
            for index, row in df.iterrows():
                symbol = f"{row['Symbol']}.NS"
                industry = row['Industry'] if 'Industry' in row else "Unknown"
                live_stocks[symbol] = industry
            print(f"✅ Successfully loaded {len(live_stocks)} stocks from NSE.")
            return live_stocks
        else:
            return get_fallback_list()
    except:
        return get_fallback_list()

def get_fallback_list():
    return {
        "HDFCBANK.NS": "Bank", "ICICIBANK.NS": "Bank", "SBIN.NS": "Bank",
        "RELIANCE.NS": "Energy", "INFY.NS": "IT", "ITC.NS": "FMCG",
        "LT.NS": "Infra", "TCS.NS": "IT", "TATAMOTORS.NS": "Auto",
        "SUNPHARMA.NS": "Pharma", "NTPC.NS": "Power", "M&M.NS": "Auto"
    }

# LOAD STOCKS
STOCKS = fetch_live_nifty_stocks()

# ================= MEMORY SYSTEM =================
def load_json(filename):
    if not os.path.exists(filename): return {} if "history" in filename else []
    try:
        with open(filename, 'r') as f: return json.load(f)
    except: return {} if "history" in filename else []

def save_json(filename, data):
    with open(filename, 'w') as f: json.dump(data, f, indent=4)

def is_duplicate_alert(ticker):
    history = load_json(HISTORY_FILE)
    if ticker in history:
        last = datetime.datetime.strptime(history[ticker], "%Y-%m-%d").date()
        if (datetime.date.today() - last).days < 5: return True
    return False

def update_history(ticker):
    history = load_json(HISTORY_FILE)
    history[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_json(HISTORY_FILE, history)

# ================= TELEGRAM =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except: pass

# ================= PRO ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_close):
    try:
        # Download 2 Years (Required for Weekly Analysis)
        df = yf.download(ticker, period="2y", progress=False)
        
        # --- SAFETY FIX FOR YFINANCE ---
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

        # 1. WEEKLY TREND CHECK (The Big Picture)
        df_weekly = df.resample('W').agg({'Close': 'last'})
        df_weekly['EMA_50'] = ta.ema(df_weekly['Close'], length=50)
        
        curr_wk_close = df_weekly['Close'].iloc[-1]
        curr_wk_ema = df_weekly['EMA_50'].iloc[-1]
        
        # Rule: Weekly Price must be > Weekly 50 EMA
        if pd.isna(curr_wk_ema) or curr_wk_close < curr_wk_ema:
            return None 

        # 2. DAILY INDICATORS
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

        # 3. FILTERS
        # Daily Trend
        if curr['Close'] < curr['EMA_200']: return None
        
        # Chop Filter (ADX > 20)
        adx_col = df.columns[df.columns.str.contains('ADX_14')][0]
        if curr[adx_col] < 20: return None

        # 4. SCORING
        score = 5.0
        reasons = []
        setup = None
        
        reasons.append("Weekly Trend Bullish (Above 50 EMA)")

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

        # 5. TARGETS & STOPS
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
            "score": round(score, 1),
            "reasons": reasons
        }

    except Exception as e:
        return None

# ================= RUNNER =================
def run_scan():
    print("--- 🔍 Starting Pro Swing Scan ---")
    
    # Dummy Nifty Fetch
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)['Close']
    except:
        nifty = pd.Series()

    signals = []
    
    print(f"Scanning {len(STOCKS)} stocks...")
    for ticker, sector in STOCKS.items():
        if is_duplicate_alert(ticker): continue
        
        data = analyze_stock(ticker, sector, nifty)
        if data:
            print(f"✅ Found: {data['symbol']} (Score: {data['score']})")
            signals.append(data)
    
    # Sort and Send
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    if not signals:
        print("No setups found today.")
        return

    print(f"--- Sending {min(len(signals), MAX_ALERTS_PER_DAY)} Alerts ---")
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        # 1. Send Telegram Alert
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

        # 2. SAVE TO TRADES.JSON (This communicates with trade_manager.py)
        try:
            trade_record = {
                "symbol": s['symbol'],
                "entry": s['entry'],
                "target": s['t1'],
                "sl": s['sl'],
                "date": str(datetime.date.today()),
                "status": "OPEN"
            }
            
            current_trades = []
            if os.path.exists(TRADES_FILE):
                with open(TRADES_FILE, 'r') as f:
                    try: current_trades = json.load(f)
                    except: current_trades = []
            
            current_trades.append(trade_record)
            
            with open(TRADES_FILE, 'w') as f:
                json.dump(current_trades, f, indent=4)
            print(f"📝 Saved {s['symbol']} to ledger.")
            
        except Exception as e:
            print(f"Error saving to ledger: {e}")

if __name__ == "__main__":
    run_scan()

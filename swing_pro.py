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

MAX_ALERTS_PER_DAY = 2
MIN_SCORE = 9.5

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Error: Telegram tokens not found.")
    sys.exit(1)

HISTORY_FILE = "alert_history.json"
TRADES_FILE = "trades.json"

# ================= DYNAMIC STOCK LOADER =================
def fetch_live_nifty_stocks():
    print("⏳ Downloading Nifty 200 list from NSE...")
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
            return {f"{row['Symbol']}.NS": row.get("Industry", "Unknown")
                    for _, row in df.iterrows()}
    except:
        pass
    return {"HDFCBANK.NS": "Bank", "RELIANCE.NS": "Energy", "INFY.NS": "IT"}

STOCKS = fetch_live_nifty_stocks()

# ================= DATA HELPERS =================
def load_json(filename):
    if not os.path.exists(filename): return {} if "history" in filename else []
    try:
        with open(filename, 'r') as f: return json.load(f)
    except: return {} if "history" in filename else []

def save_json(filename, data):
    with open(filename, 'w') as f: json.dump(data, f, indent=4)

def is_duplicate_alert(ticker):
    clean_symbol = ticker.replace('.NS', '')
    history = load_json(HISTORY_FILE)
    if ticker in history:
        try:
            last = datetime.datetime.strptime(history[ticker], "%Y-%m-%d").date()
            if (datetime.date.today() - last).days < 15: return True
        except: pass
    trades = load_json(TRADES_FILE)
    for t in trades:
        if t.get('symbol') == clean_symbol and t.get('status') == 'OPEN': return True
    return False

def update_history(ticker):
    history = load_json(HISTORY_FILE)
    history[ticker] = datetime.date.today().strftime("%Y-%m-%d")
    save_json(HISTORY_FILE, history)

# ================= TELEGRAM =================
def send_telegram_alert(message):
    chat_ids = [x.strip() for x in TELEGRAM_CHAT_ID.split(',')]
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
            requests.post(url, json=payload, timeout=10)
        except: pass

# ================= FUNDAMENTALS =================
def get_fundamentals(ticker):
    try:
        info = yf.Ticker(ticker).info
        roe = info.get("returnOnEquity", 0)
        margin = info.get("profitMargins", 0)
        score = 0
        notes = []
        if roe > 0.15:
            score += 1
            notes.append(f"✅ ROE: {round(roe*100,1)}%")
        if margin > 0.10:
            score += 0.5
            notes.append(f"✅ Margin: {round(margin*100,1)}%")
        return score, "\n".join(notes) if notes else "⚠️ Neutral Fundamentals"
    except: return 0, "⚠️ No Fundamental Data"

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_trend, nifty_ret):
    try:
        # 1. Download & Data Cleaning
        df = yf.download(ticker, period="2y", progress=False)
        if df.empty or len(df) < 260: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

        # 2. Weekly Trend Guard (Institutional Filter)
        weekly = df.resample("W").agg({"Close": "last"})
        weekly["EMA50"] = ta.ema(weekly["Close"], 50)
        if weekly["EMA50"].isna().iloc[-1] or weekly["Close"].iloc[-1] < weekly["EMA50"].iloc[-1]:
            return None

        # 3. Indicators
        df["EMA20"] = ta.ema(df["Close"], 20)
        df["EMA200"] = ta.ema(df["Close"], 200)
        df["RSI"] = ta.rsi(df["Close"], 14)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)
        adx_df = ta.adx(df["High"], df["Low"], df["Close"], 14)
        df = pd.concat([df, adx_df], axis=1)
        adx_col = [c for c in df.columns if "ADX_14" in c][0]
        
        df = df.dropna()
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        avg_vol = df["Volume"].rolling(20).mean().iloc[-1]

        # 4. Strength Filters
        n_ret = nifty_ret.item() if hasattr(nifty_ret, "item") else nifty_ret
        stock_ret = (curr["Close"] / df["Close"].iloc[-60]) - 1
        if (avg_vol * curr["Close"]) < 50000000: return None
        if stock_ret < n_ret: return None
        if curr["Close"] < curr["EMA200"] or curr[adx_col] < 20: return None

        # 5. Scoring & Setup Logic
        score = 5.0
        reasons = ["Weekly Trend Bullish", "🔥 Outperforming Nifty"]
        
        # Rule: 52-Week High (Excluding recent 20-day volatility)
        yearly_high = df["High"].iloc[-252:-20].max()
        if curr["Close"] > yearly_high:
            score += 2.5
            reasons.append("🏆 Long-term Base Breakout")

        # Accumulation
        last_10 = df.tail(10)
        green_vol = last_10[last_10["Close"] > last_10["Open"]]["Volume"].sum()
        red_vol = last_10[last_10["Close"] < last_10["Open"]]["Volume"].sum() or 1
        buy_pressure = green_vol / red_vol
        if buy_pressure > 2.0:
            score += 1.5
            reasons.append(f"🐳 Accumulation ({round(buy_pressure,1)}x)")

        # Specific Setup Detection
        setup = None
        # BREAKOUT: Price > 10d high + 2x Volume Surge
        if curr["Close"] > df.iloc[-11:-1]["High"].max() and curr["Volume"] > (2.0 * avg_vol):
            setup = "🚀 Institutional Breakout"
            score += 2.5
        # PULLBACK: Trend Sandwich (EMA20 > EMA200) + Touch
        elif (curr["Close"] > curr["EMA20"] > curr["EMA200"] and 
              prev["Low"] <= curr["EMA20"] * 1.02 and 45 <= curr["RSI"] <= 60):
            setup = "🧲 Trend Pullback"
            score += 2.0

        if not setup or score < MIN_SCORE: return None

        # 6. Risk Management (The 12% Ceiling)
        sl = round(curr["Close"] - (1.8 * curr["ATR"]), 1)
        risk_pct = (curr["Close"] - sl) / curr["Close"]
        if risk_pct > 0.12: return None # Skip if too volatile
        
        target = round(curr["Close"] + (curr["Close"] - sl) * 2.5, 1)

        return {
            "symbol": ticker.replace(".NS", ""), "sector": sector, "setup": setup,
            "entry": round(curr["Close"], 1), "sl": sl, "t1": target,
            "score": round(score, 1), "reasons": reasons, "ticker_full": ticker
        }
    except Exception as e:
        return None

# ================= RUNNER =================
def run_scan():
    print("--- 🔍 Starting Bulletproof Institutional Scan ---")
    nifty_trend, nifty_ret = "NEUTRAL", 0
    try:
        n_df = yf.download("^NSEI", period="1y", progress=False)
        if isinstance(n_df.columns, pd.MultiIndex): n_df.columns = n_df.columns.get_level_values(0)
        nifty_data = n_df["Close"]
        nifty_trend = "BULLISH" if nifty_data.iloc[-1] > ta.ema(nifty_data, 50).iloc[-1] else "BEARISH"
        nifty_ret = (nifty_data.iloc[-1] / nifty_data.iloc[-60]) - 1
    except: pass

    signals = []
    for ticker, sector in STOCKS.items():
        if not is_duplicate_alert(ticker):
            res = analyze_stock(ticker, sector, nifty_trend, nifty_ret)
            if res: signals.append(res)

    signals.sort(key=lambda x: x["score"], reverse=True)
    
    for s in signals[:MAX_ALERTS_PER_DAY]:
        fs, fn = get_fundamentals(s["ticker_full"])
        s["score"] += fs
        
        reasoning = "\n".join([f"• {r}" for r in s["reasons"]])
        msg = f"💎 **INSTITUTIONAL ALERT**\n\n📌 **Stock:** {s['symbol']}\n📊 **Score:** {s['score']}\n🎯 **Setup:** {s['setup']}\n\n🧠 **Analysis**\n{reasoning}\n\n🏢 **Fundamentals**\n{fn}\n\n📍 **Entry:** {s['entry']}\n⛔ **Stop:** {s['sl']}\n🎯 **Target:** {s['t1']}"
        
        send_telegram_alert(msg)
        update_history(s["symbol"] + ".NS")
        
        # Save Trade
        db = load_json(TRADES_FILE)
        db.append({"symbol": s['symbol'], "entry": s['entry'], "sl": s['sl'], "status": "OPEN", "date": str(datetime.date.today())})
        save_json(TRADES_FILE, db)

if __name__ == "__main__":
    run_scan()

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

    return {
        "HDFCBANK.NS": "Bank", "RELIANCE.NS": "Energy", "INFY.NS": "IT",
        "TATAMOTORS.NS": "Auto", "ITC.NS": "FMCG", "SBIN.NS": "Bank"
    }

STOCKS = fetch_live_nifty_stocks()

# ================= DATA HELPERS =================
def load_json(filename):
    if not os.path.exists(filename):
        return {} if "history" in filename else []
    try:
        with open(filename, 'r') as f: return json.load(f)
    except:
        return {} if "history" in filename else []

def save_json(filename, data):
    with open(filename, 'w') as f: json.dump(data, f, indent=4)

# ================= SMART DUPLICATE CHECKER =================
def is_duplicate_alert(ticker):
    clean_symbol = ticker.replace('.NS', '')
    history = load_json(HISTORY_FILE)
    if ticker in history:
        try:
            last = datetime.datetime.strptime(history[ticker], "%Y-%m-%d").date()
            if (datetime.date.today() - last).days < 15: 
                return True
        except: pass

    trades = load_json(TRADES_FILE)
    for t in trades:
        if t.get('symbol') == clean_symbol and t.get('status') == 'OPEN':
            return True

    for t in reversed(trades):
        if t.get('symbol') == clean_symbol and t.get('status') == 'LOSS':
            try:
                loss_date = datetime.datetime.strptime(t.get('date'), "%Y-%m-%d").date()
                if (datetime.date.today() - loss_date).days < 20: 
                    return True
            except: pass
            break 
            
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
        if not notes:
            notes.append("⚠️ Weak Fundamentals")
        return score, "\n".join(notes)
    except:
        return 0, "⚠️ No Fundamental Data"

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_trend, nifty_ret):
    try:
        df = yf.download(ticker, period="2y", progress=False)
        if df.empty or len(df) < 260: return None # Ensure enough data for 52W check

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(ticker, level=1, axis=1)

        # Weekly trend
        weekly = df.resample("W").agg({"Close": "last"})
        weekly["EMA50"] = ta.ema(weekly["Close"], 50)
        if weekly["Close"].iloc[-1] < weekly["EMA50"].iloc[-1]: return None

        # Indicators
        df["EMA20"] = ta.ema(df["Close"], 20)
        df["EMA200"] = ta.ema(df["Close"], 200)
        df["RSI"] = ta.rsi(df["Close"], 14)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)
        adx = ta.adx(df["High"], df["Low"], df["Close"], 14)
        df = pd.concat([df, adx], axis=1)
        
        df = df.dropna()
        curr = df.iloc[-1]
        avg_vol = df["Volume"].rolling(20).mean().iloc[-1]

        # 🚀 1. TURNOVER FILTER (MIN 5 CRORE)
        if (avg_vol * curr["Close"]) < 50000000:
            return None

        # 🚀 2. RELATIVE STRENGTH FILTER (VS NIFTY 3-MONTHS)
        stock_ret = (df["Close"].iloc[-1] / df["Close"].iloc[-60]) - 1
        if stock_ret < nifty_ret:
            return None

        # Technical Filters
        if curr["Close"] < curr["EMA200"]: return None
        adx_col = [c for c in df.columns if "ADX_14" in c][0]
        if curr[adx_col] < 20: return None

        # Scoring Logic
        score = 5.0
        reasons = ["Weekly Trend Bullish", "🔥 Outperforming Nifty"]
        setup = None

        # 🚀 3. 52-WEEK HIGH BONUS
        yearly_high = df["High"].rolling(252).max().iloc[-2]
        if curr["Close"] > yearly_high:
            score += 2.0
            reasons.append("🏆 52-Week High Breakout")

        if nifty_trend == "BULLISH": score += 0.5

        last_10 = df.tail(10)
        green_vol = last_10[last_10["Close"] > last_10["Open"]]["Volume"].sum()
        red_vol = last_10[last_10["Close"] < last_10["Open"]]["Volume"].sum() or 1
        buy_pressure = green_vol / red_vol

        if buy_pressure > 2:
            score += 1.5
            reasons.append(f"🐳 Strong Accumulation ({round(buy_pressure,1)}x)")
        
        # Setup Detection
        range_high = df.iloc[-11:-1]["High"].max()
        if curr["Close"] > range_high and curr["Volume"] > 1.5 * avg_vol:
            setup = "🚀 Breakout"
            score += 2
        elif abs(curr["Close"] - curr["EMA20"]) / curr["Close"] < 0.03 and 45 <= curr["RSI"] <= 60:
            setup = "🧲 Pullback"
            score += 1.5

        if not setup or score < MIN_SCORE: return None

        sl = max(curr["Close"] - 2 * curr["ATR"], curr["Close"] * 0.92)
        target = curr["Close"] + (curr["Close"] - sl) * 2

        return {
            "symbol": ticker.replace(".NS", ""),
            "sector": sector,
            "setup": setup,
            "entry": round(curr["Close"], 1),
            "sl": round(sl, 1),
            "t1": round(target, 1),
            "score": round(score, 1),
            "reasons": reasons,
            "ticker_full": ticker,
            "buy_pressure": buy_pressure
        }
    except Exception as e:
        print(f"Error on {ticker}: {e}")
        return None

# ================= RUNNER =================
def run_scan():
    print("--- 🔍 Starting Fantastic Scan (With Quality Boosts) ---")
    nifty_trend = "NEUTRAL"
    nifty_ret = 0
    try:
        nifty_data = yf.download("^NSEI", period="1y", progress=False)["Close"]
        nifty_trend = "BULLISH" if nifty_data.iloc[-1] > ta.ema(nifty_data, 50).iloc[-1] else "BEARISH"
        # Calculate Nifty 3-month return (approx 60 trading days)
        nifty_ret = (nifty_data.iloc[-1] / nifty_data.iloc[-60]) - 1
        print(f"📊 Nifty 3M Return: {round(nifty_ret*100, 2)}%")
    except: pass

    market_icon = "🟢" if nifty_trend == "BULLISH" else "🔴"
    signals = []
    
    for ticker, sector in STOCKS.items():
        if not is_duplicate_alert(ticker):
            # Send the nifty_ret into the analysis
            data = analyze_stock(ticker, sector, nifty_trend, nifty_ret)
            if data: signals.append(data)

    signals.sort(key=lambda x: x["score"], reverse=True)

    final_signals = []
    for s in signals[:5]:
        fs, fn = get_fundamentals(s["ticker_full"])
        s["score"] += fs
        s["fund_notes"] = fn
        final_signals.append(s)

    final_signals.sort(key=lambda x: x["score"], reverse=True)

    if not final_signals:
        msg = f"📉 **Daily Scan Complete**\n\nMarket: {market_icon} {nifty_trend}\n✅ Scanned: {len(STOCKS)} stocks\n🚫 Found: 0 high-quality setups"
        send_telegram_alert(msg)
        return

    for s in final_signals[:MAX_ALERTS_PER_DAY]:
        if s["score"] >= 9.5 and nifty_trend == "BULLISH": confidence = "A+"
        elif s["score"] >= 8.5: confidence = "A"
        else: confidence = "B"

        reasoning = "\n".join([f"• {r}" for r in s["reasons"]])
        msg = f"💎 **INSTITUTIONAL SWING ALERT**\n\n📌 **Stock:** {s['symbol']}\n🏢 **Sector:** {s['sector']}\n📊 **Score:** {s['score']} / 14\n🏅 **Confidence:** {confidence}\n\n🧠 **Analysis**\n{reasoning}\n\n🏢 **Fundamentals**\n{s['fund_notes']}\n\n📍 **Entry:** {s['entry']}\n⛔ **Stop:** {s['sl']}\n🎯 **Target:** {s['t1']}\n\n_SwingBot v2.0_"
        
        send_telegram_alert(msg)
        update_history(s["symbol"] + ".NS")
        
        # Record Trade
        try:
            current_trades = load_json(TRADES_FILE)
            current_trades.append({"symbol": s['symbol'], "entry": s['entry'], "target": s['t1'], "sl": s['sl'], "date": str(datetime.date.today()), "status": "OPEN"})
            save_json(TRADES_FILE, current_trades)
        except: pass

if __name__ == "__main__":
    run_scan()
